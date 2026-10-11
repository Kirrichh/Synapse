"""Compiled sends use the interpreter's policy and delivery owners.

The regressions run actual DSL, Interpreter, compiler, CVM and VMBridge. Only
the explicit missing-owner case removes an entry point to model misconfiguration.
No alternate actor or governance implementation supplies the expected result.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM, VMHostError, VMState
from synapse.interpreter import PolicyViolationException


WORKER = 'agent Worker { model "mock" }\n'
SEND = 'let worker = "Worker"\nsend worker.process({arguments})\n'
DENIALS = {
    "static": 'policy Gov { target "Worker.process" forbid "blocked" }\n',
    "guard": ('policy Gov { target "Worker.process" guard(args) {\n'
              'if args[0] == "blocked" { reject "blocked by guard" }\n}\n}\n'),
}
ARGUMENTS = [
    pytest.param("", [], id="zero"),
    pytest.param("null", [None], id="one-none"),
    pytest.param('"permit", 7', ["permit", 7], id="two"),
    pytest.param('["permit", 7]', [["permit", 7]], id="one-list"),
]


def interpret(interp, source):
    interp.interpret(compile_to_ast(source))


def compiled_command(source):
    return (f"compile vm {{ source {json.dumps(source)} bind code }}\n"
            "run vm { source code bind result }\n")


def application_vm(interp, source):
    interp.global_env.define("source_text", source)
    interpret(interp, "compile vm { source source_text bind code }\n"
              "run vm { source code bind result }\n")
    result = interp.global_env.get("result")
    assert result["halted"] is True
    assert "error" not in result
    return result


def attached_vm(interp, source, *, actor_stack=()):
    program = CognitiveCompiler().compile(compile_to_ast(source))
    vm = CognitiveVM(program, VMState(gas_remaining=1_000, actor_stack=list(actor_stack)))
    vm.host = interp.runtime.vm.get_cvm_callback_adapter(vm)
    return vm


def send_records(interp):
    kinds = {"policy_evaluated", "policy_violation", "message_sent", "message_forwarded"}
    return [event for event in interp.execution_history if event.get("type") in kinds]


@pytest.mark.parametrize("execution", ["interpreted", "compiled"])
@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("policy", list(DENIALS))
def test_policy_denial_delivers_nothing_and_commits_no_vm_outbound(execution, remote, policy):
    interp = Interpreter()
    interpret(interp, WORKER + DENIALS[policy])
    if remote:
        interp.register_route("Worker", "node-remote")
    source = SEND.format(arguments='"blocked"')
    vm = attached_vm(interp, source) if execution == "compiled" else None
    with pytest.raises((PolicyViolationException, VMHostError), match="blocked"):
        if vm is None:
            interpret(interp, source)
        else:
            interp.runtime.vm.run_cvm_with_context_safety(vm)
    assert interp.mailboxes.get("Worker", []) == []
    assert interp.outbound_packets == []
    records = send_records(interp)
    assert [event["type"] for event in records] == ["policy_violation"]
    assert records[0]["args"] == ["blocked"]
    if vm is not None:
        assert vm.state.mailbox_outbound == []


@pytest.mark.parametrize("arguments,expected", ARGUMENTS)
def test_compiled_argument_vectors_and_policy_records_match_interpreted_send(arguments, expected):
    expected_dsl = json.dumps(expected)
    policy = ('policy Gov { target "Worker.process" guard(args) {\n'
              f'if args != {expected_dsl} {{ reject "argument vector changed" }}\n'
              '} }\n')
    source = SEND.format(arguments=arguments)
    tree, compiled = Interpreter(), Interpreter()
    for interp in (tree, compiled):
        interpret(interp, WORKER + policy)
    interpret(tree, source)
    application_vm(compiled, source)
    assert compiled.mailboxes["Worker"] == tree.mailboxes["Worker"]
    message = compiled.mailboxes["Worker"][0]
    assert message["args"] == expected
    assert message["payload"] == (expected[0] if len(expected) == 1 else expected)
    assert send_records(compiled) == send_records(tree)
    assert [event["type"] for event in send_records(compiled)] == ["policy_evaluated", "message_sent"]
    assert send_records(compiled)[0]["args"] == expected


@pytest.mark.parametrize("actor", [None, "Planner"], ids=["global", "self"])
def test_application_vm_preserves_the_interpreters_sender_for_policy_and_delivery(actor):
    sender = actor or "global"
    setup = WORKER
    if actor is not None:
        setup += f'agent {actor} {{ model "mock" }}\nlet self = {actor}\n'
    setup += ('policy Gov { target "Worker.process" guard(args) {\n'
              f'if sender != "{sender}" {{ reject "wrong sender" }}\n'
              '} }\n')
    tree, compiled = Interpreter(), Interpreter()
    for interp in (tree, compiled):
        interpret(interp, setup)
    source = SEND.format(arguments='"permit"')
    interpret(tree, source)
    application_vm(compiled, source)
    assert send_records(compiled) == send_records(tree)
    assert compiled.mailboxes["Worker"][0]["sender"] == sender
    assert send_records(compiled)[0]["sender"] == sender


def test_current_vm_actor_is_the_policy_sender_and_the_delivered_sender():
    interp = Interpreter()
    interpret(interp, WORKER + 'policy Gov { target "Worker.process" guard(args) {\n'
              'if sender != "Planner" { reject "wrong actor" }\n} }\n')
    vm = attached_vm(interp, SEND.format(arguments='"permit"'), actor_stack=("Outer", "Planner"))
    result = interp.runtime.vm.run_cvm_with_context_safety(vm)
    assert result["halted"] is True
    assert [event["type"] for event in send_records(interp)] == ["policy_evaluated", "message_sent"]
    assert send_records(interp)[0]["sender"] == "Planner"
    assert interp.mailboxes["Worker"][0]["sender"] == "Planner"
    assert len(vm.state.mailbox_outbound) == 1
    assert vm.state.mailbox_outbound[0]["sender"] == "Planner"


def test_allowed_remote_send_uses_the_same_route_and_verdict_once():
    tree, compiled = Interpreter(), Interpreter()
    for interp in (tree, compiled):
        interpret(interp, WORKER + DENIALS["static"])
        interp.register_route("Worker", "node-remote")
    source = SEND.format(arguments='"permit", 7')
    interpret(tree, source)
    application_vm(compiled, source)
    assert compiled.mailboxes.get("Worker", []) == []
    assert compiled.outbound_packets == tree.outbound_packets
    assert len(compiled.outbound_packets) == 1
    assert send_records(compiled) == send_records(tree)
    assert [event["type"] for event in send_records(compiled)] == ["policy_evaluated", "message_forwarded"]


def test_missing_actor_delivery_owner_fails_closed(monkeypatch):
    interp = Interpreter()
    interpret(interp, WORKER)
    vm = attached_vm(interp, SEND.format(arguments='"permit"'))
    # The bridge must not write a mailbox itself when its canonical delivery
    # owner is unavailable. Governance and interpreter remain real and intact.
    monkeypatch.setattr(interp.runtime, "actor", None)
    with pytest.raises(VMHostError, match="ActorRuntime"):
        interp.runtime.vm.run_cvm_with_context_safety(vm)
    assert interp.mailboxes.get("Worker", []) == []
    assert interp.outbound_packets == []
    assert vm.state.mailbox_outbound == []
    assert send_records(interp) == []


@pytest.mark.parametrize("fields,omitted", [
    pytest.param({}, "argc", id="missing-argc"),
    pytest.param({"argc": True}, None, id="true-is-not-argc"),
    pytest.param({"argc": False}, None, id="false-is-not-argc"),
    pytest.param({"argc": -1}, None, id="negative-argc"),
    pytest.param({"argc": 1.0}, None, id="float-is-not-argc"),
    pytest.param({"argc": 2, "payload": ["permit"]}, None, id="count-mismatch"),
    pytest.param({"argc": 2}, None, id="multiple-args-need-list"),
    pytest.param({"argc": 0, "payload": ["permit"]}, None, id="zero-with-content"),
    pytest.param({"args": ["blocked"]}, None, id="second-argument-source"),
    pytest.param({}, "payload", id="missing-payload"),
])
def test_malformed_versioned_send_is_refused_before_policy_or_delivery(fields, omitted):
    interp = Interpreter()
    interpret(interp, WORKER + DENIALS["static"])
    vm = attached_vm(interp, SEND.format(arguments='"permit"'))
    message = {
        "schema_version": "synapse.cvm.send-args/v1", "argc": 1,
        "sender_id": "global", "sender": "global",
        "target_id": "Worker", "receiver": "Worker",
        "msg_type": "process", "method": "process", "payload": "permit",
        **fields,
    }
    if omitted is not None:
        del message[omitted]
    with pytest.raises(VMHostError) as error:
        vm.host("CALL_HOST", {"symbol": "SYS_MSG_SEND", "args": [message]}, None)
    assert error.value.code == "MESSAGE_FORMAT_ERROR"
    assert interp.mailboxes.get("Worker", []) == []
    assert interp.outbound_packets == []
    assert vm.state.mailbox_outbound == []
    assert send_records(interp) == []


@pytest.mark.parametrize("argument,allowed", [(7, True), (99, False)])
def test_canonical_cli_respects_the_same_send_policy(argument, allowed):
    root = Path(__file__).resolve().parents[2]
    # The DSL has no string escapes, so this source uses an actor's numeric
    # address and numeric payload: it embeds unchanged in the compile command.
    source = f"let worker = 23 send worker.process({argument})"
    command = ('policy Gov { target "23.process" forbid 99 }\n'
               + compiled_command(source) + 'print("after-send")\n')
    env = {**os.environ, "PYTHONPATH": str(root)}
    observed = subprocess.run(
        [sys.executable, "-B", "-m", "synapse", "run", "-c", command],
        cwd=root, env=env, text=True, capture_output=True, timeout=30,
    )
    if allowed:
        assert observed.returncode == 0, observed.stderr
        assert observed.stdout.strip() == "after-send"
    else:
        assert observed.returncode != 0
        assert "99" in observed.stdout + observed.stderr
        assert "after-send" not in observed.stdout
