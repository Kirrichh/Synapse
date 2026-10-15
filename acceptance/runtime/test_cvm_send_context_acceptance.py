"""Compiled sends preserve the caller's execution restrictions."""
import json

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.bytecode import BytecodeProgram, CognitiveCompiler, Instruction
from synapse.cvm import CognitiveVM, VMHostError, VMResumeSyncError, VMState
from synapse.interpreter import (
    ConsensusVoteSideEffectError,
    DreamIsolationViolation,
    IntegrateIsolationViolation,
    OrphanedIdentityException,
    PolicyCompilationError,
)


SEND = 'let destination = str(7) send destination.push(7)'
RUN_VM = 'run vm { source code gas 1000 bind vm_result }\n'
COMPILE_VM = f'compile vm {{ source {json.dumps(SEND)} bind code }}\n'
CONTEXTS = [
    ({"dream_depth": 1}, DreamIsolationViolation, "dream cannot send"),
    ({"integrate_depth": 1}, IntegrateIsolationViolation, "send is forbidden inside integrate"),
    ({"policy_guard_depth": 1}, PolicyCompilationError, "Policy guard cannot send"),
    ({"_consensus_vote_query_depth": 1}, ConsensusVoteSideEffectError, "consensus vote query"),
    ({"fracture_depth": 1, "_subagent_stack": ["Worker"]},
     OrphanedIdentityException, "send is forbidden inside sub-agent"),
]


def assert_no_delivery(interpreter):
    assert interpreter.mailboxes.get("7", []) == []
    assert interpreter.outbound_packets == []
    assert not [event for event in interpreter.execution_history
                if event["type"] in {"message_sent", "message_forwarded"}]


def test_dream_cannot_deliver_a_compiled_send_to_the_outer_mailbox():
    interpreter = Interpreter()
    source = 'let result = dream {\n' + COMPILE_VM + RUN_VM + 'return 1\n}\n'

    with pytest.raises(DreamIsolationViolation, match="dream cannot send"):
        interpreter.interpret(compile_to_ast(source))

    assert_no_delivery(interpreter)
    assert interpreter.dream_depth == 0
    interpreter.interpret(compile_to_ast(COMPILE_VM + RUN_VM))
    assert [message["payload"] for message in interpreter.mailboxes["7"]] == [7]


@pytest.mark.parametrize("overlay", [False, True])
def test_integrate_rejects_compiled_send_before_delivery_then_rolls_back(overlay):
    interpreter = Interpreter()
    interpreter.integrate_i2_skeleton_enabled = overlay
    interpreter.interpret(compile_to_ast(COMPILE_VM))
    source = 'let x = 1\nintegrate x {\nx = 2\n' + RUN_VM + '} on fail rollback\n'

    with pytest.raises(IntegrateIsolationViolation, match="send is forbidden inside integrate"):
        interpreter.interpret(compile_to_ast(source))

    assert_no_delivery(interpreter)
    assert interpreter.global_env.get("x") == 1
    assert interpreter.integrate_depth == 0
    interpreter.interpret(compile_to_ast(RUN_VM))
    assert [message["payload"] for message in interpreter.mailboxes["7"]] == [7]


def test_compiled_send_kills_only_the_sending_subagent():
    interpreter = Interpreter()
    source = ('agent Guide { model "mock" }\nlet self = Guide\n'
              'let result = fracture self into {\nSender {\n' + COMPILE_VM + RUN_VM
              + 'return "forbidden"\n}\nAnalyst { return "safe" }\n} consensus weighted\n'
              'print("base-alive")\n')
    interpreter.interpret(compile_to_ast(source))

    assert_no_delivery(interpreter)
    deaths = {event["subagent"]: event for event in interpreter.execution_history
              if event["type"] == "subagent_terminated"}
    assert deaths["Sender"]["death_type"] == "KILLED_NETWORK"
    assert "send is forbidden inside sub-agent" in deaths["Sender"]["reason"]
    assert deaths["Analyst"]["position"] == "safe"
    assert deaths["Analyst"]["death_type"] == "NATURAL"
    assert interpreter.get_output() == "base-alive"


@pytest.mark.parametrize("flags,error,reason", CONTEXTS)
@pytest.mark.parametrize("entrance", ["actor", "cvm"])
def test_the_delivery_owner_enforces_restricted_host_contexts(flags, error, reason, entrance):
    interpreter = Interpreter()
    code = CognitiveCompiler().compile(compile_to_ast(SEND))
    vm = CognitiveVM(code)
    vm.host = interpreter.runtime.vm.get_cvm_callback_adapter(vm)
    for field, value in flags.items():
        setattr(interpreter, field, value)

    with pytest.raises(error, match=reason):
        if entrance == "actor":
            interpreter.runtime.actor.send_message("global", "7", "push", [7])
        else:
            interpreter.runtime.vm.run_cvm_with_context_safety(vm)

    assert_no_delivery(interpreter)
    assert vm.state.mailbox_outbound == []


@pytest.mark.parametrize("flags,error,reason", CONTEXTS)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_forbidden_tree_send_rejects_before_evaluating_its_arguments(flags, error, reason, asynchronous):
    interpreter = Interpreter()
    interpreter.interpret(compile_to_ast(
        'let seen = {"calls": 0}\nfn argument() { seen.calls = 1 return 7 }\nlet destination = str(7)\n'))
    source = compile_to_ast('send destination.push(argument())\n')
    for field, value in flags.items():
        setattr(interpreter, field, value)

    with pytest.raises(error, match=reason):
        if asynchronous:
            list(interpreter.interpret_async(source))
        else:
            interpreter.interpret(source)

    assert interpreter.global_env.get("seen") == {"calls": 0}
    assert_no_delivery(interpreter)


@pytest.mark.parametrize("forged", ["sender", "sender_id", "both"])
def test_direct_host_message_cannot_claim_another_sender(forged):
    interpreter = Interpreter()
    code = CognitiveCompiler().compile(compile_to_ast(SEND))
    vm = CognitiveVM(code, state=VMState(sender_id="global"))
    callback = interpreter.runtime.vm.get_cvm_callback_adapter(vm)
    message = {"schema_version": "synapse.cvm.send-args/v1", "argc": 1, "payload": 7,
               "sender": "global", "sender_id": "global", "receiver": "7", "target_id": "7",
               "method": "push", "msg_type": "push"}
    for field in ("sender", "sender_id") if forged == "both" else (forged,):
        message[field] = "PrivilegedSender"

    with pytest.raises(VMHostError, match="sender|identity|principal"):
        callback("CALL_HOST", {"symbol": "SYS_MSG_SEND", "args": [message]}, None)

    assert_no_delivery(interpreter)
    assert vm.state.mailbox_outbound == []


@pytest.mark.parametrize("saved_sender,claimed_sender,allowed", [
    (None, "default_agent", False),
    (None, "PrivilegedSender", False),
    ("PrivilegedSender", "PrivilegedSender", False),
    (None, "global", True),
])
@pytest.mark.parametrize("entrance", ["callback", "dispatcher"])
def test_direct_host_send_uses_its_invoking_identity(saved_sender, claimed_sender, allowed, entrance):
    interpreter = Interpreter()
    code = CognitiveCompiler().compile(compile_to_ast('let x = 1'))
    vm = CognitiveVM(code, state=VMState(sender_id=saved_sender))
    message = {"schema_version": "synapse.cvm.send-args/v1", "argc": 1, "payload": 7,
               "sender": claimed_sender, "sender_id": claimed_sender,
               "receiver": "7", "target_id": "7", "method": "push", "msg_type": "push"}

    def deliver():
        if entrance == "dispatcher":
            return interpreter.runtime.vm.dispatch_host_call(vm, "SYS_MSG_SEND", [message])
        callback = interpreter.runtime.vm.get_cvm_callback_adapter(vm)
        return callback("CALL_HOST", {"symbol": "SYS_MSG_SEND", "args": [message]}, None)

    if allowed:
        result = deliver()
        assert result["sender"] == "global"
        assert [item["sender"] for item in interpreter.mailboxes["7"]] == ["global"]
    else:
        with pytest.raises(VMHostError, match="sender|identity|principal"):
            deliver()
        assert_no_delivery(interpreter)
    assert vm.state.mailbox_outbound == []


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("trusted_snapshot", [False, True])
def test_host_send_respects_the_snapshot_agent_trust_gate(legacy, trusted_snapshot):
    interpreter = Interpreter()
    interpreter.trust_snapshot_agent_id = trusted_snapshot
    code = CognitiveCompiler().compile(compile_to_ast('let x = 1'))
    vm = CognitiveVM(code, state=VMState(agent_id="PrivilegedSender"))
    sender = "PrivilegedSender" if legacy else "global"
    message = {"payload": 7, "sender_id": sender, "target_id": "7", "msg_type": "push"}
    if not legacy:
        message.update(schema_version="synapse.cvm.send-args/v1", argc=1)

    if trusted_snapshot:
        interpreter.runtime.vm.dispatch_host_call(vm, "SYS_MSG_SEND", [message])
        assert [item["sender"] for item in interpreter.mailboxes["7"]] == [sender]
    else:
        with pytest.raises(VMResumeSyncError, match="snapshot agent_id"):
            interpreter.runtime.vm.dispatch_host_call(vm, "SYS_MSG_SEND", [message])
        assert_no_delivery(interpreter)


def test_unbound_legacy_sender_survives_a_named_host_principal():
    interpreter = Interpreter()
    interpreter.current_agent_id = "Operator"
    interpreter.interpret(compile_to_ast(
        'policy Legacy { target "7.push" guard(args) {\n'
        'if sender != "default_agent" { reject "legacy sender changed" }\n'
        'if args[0] != 7 { reject "legacy payload changed" }\n}\n}\n'))
    code = BytecodeProgram(constants=["7", 7], instructions=[
        Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1),
        Instruction("MSG_SEND", "push"), Instruction("HALT")])
    vm = CognitiveVM(code)
    vm.host = interpreter.runtime.vm.get_cvm_callback_adapter(vm)

    interpreter.runtime.vm.run_cvm_with_context_safety(vm)

    assert [(item["sender"], item["args"]) for item in interpreter.mailboxes["7"]] == [
        ("default_agent", [7])]
    verdicts = [item for item in interpreter.execution_history if item["type"] == "policy_evaluated"]
    assert [(item["sender"], item["args"]) for item in verdicts] == [("default_agent", [7])]
    assert vm.state.agent_id is None


def test_resuming_a_compiled_send_under_another_caller_cannot_deliver():
    interpreter = Interpreter()
    setup = ('agent Origin { model "mock" }\nagent Replacement { model "mock" }\n'
             'let self = Origin\n' + COMPILE_VM
             + 'run vm { source code gas 1000 checkpoint "origin" at_ip 0 bind original }\n')
    interpreter.interpret(compile_to_ast(setup))
    assert [message["sender"] for message in interpreter.mailboxes["7"]] == ["Origin"]
    interpreter.interpret(compile_to_ast('self = Replacement\n'))

    with pytest.raises(VMHostError, match="sender|identity|principal|caller"):
        interpreter.interpret(compile_to_ast(
            'run vm { resume_from "origin" gas 1000 bind resumed }\n'))

    assert [message["sender"] for message in interpreter.mailboxes["7"]] == ["Origin"]
    assert sum(event["type"] == "message_sent" for event in interpreter.execution_history) == 1


@pytest.mark.parametrize("missing", ["absent", "null"])
def test_checkpoint_cannot_discard_its_bound_sender(missing):
    interpreter = Interpreter()
    setup = ('agent Origin { model "mock" }\nagent Replacement { model "mock" }\n'
             'let self = Origin\n' + COMPILE_VM
             + 'run vm { source code gas 1000 checkpoint "origin" at_ip 0 bind original }\n')
    interpreter.interpret(compile_to_ast(setup))
    state = interpreter.vm_checkpoints["origin"]["vm_state"]
    if missing == "absent":
        del state["sender_id"]
    else:
        state["sender_id"] = None
    interpreter.interpret(compile_to_ast('self = Replacement\n'))

    with pytest.raises(VMHostError, match="sender|identity|principal|caller"):
        interpreter.interpret(compile_to_ast(
            'run vm { resume_from "origin" gas 1000 bind resumed }\n'))

    assert [message["sender"] for message in interpreter.mailboxes["7"]] == ["Origin"]
    assert sum(event["type"] == "message_sent" for event in interpreter.execution_history) == 1


def test_checkpoint_keeps_its_sender_when_resuming_the_same_caller():
    interpreter = Interpreter()
    setup = ('agent Origin { model "mock" }\nlet self = Origin\n' + COMPILE_VM
             + 'run vm { source code gas 1000 checkpoint "origin" before_op HALT bind original }\n')
    interpreter.interpret(compile_to_ast(setup))
    assert interpreter.vm_checkpoints["origin"]["vm_state"]["sender_id"] == "Origin"

    interpreter.interpret(compile_to_ast(
        'run vm { resume_from "origin" gas 1000 bind resumed }\n'))

    assert interpreter.global_env.get("resumed")["halted"] is True
    assert [message["sender"] for message in interpreter.mailboxes["7"]] == ["Origin"]
