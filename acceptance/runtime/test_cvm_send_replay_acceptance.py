"""Compiled sends use the interpreter's delivery, replay and ownership boundary.

These scenarios use a real interpreter, compiler, CVM and actor runtime. A
remote route ends at the runtime's outbound queue; no network transport is
needed to distinguish forwarding from accidental local delivery.
"""
import copy
import json

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM, VMHostError, VMState
from synapse.interpreter import ReplayIntegrityError, RuntimeMode
from synapse.memory_points import DURABLE_COGNITIVE_PROFILE


RUN_VM = "compile vm { source source bind code }\nrun vm { source code gas 10000 bind result }\n"
DESTINATION = "WarehouseReceiver"
REMOTE_NODE = "node://warehouse-secondary"


def messages(interpreter):
    return [event for event in interpreter.execution_history
            if event["type"] in {"message_sent", "message_forwarded"}]


def program(batch):
    return (f'let destination = "{DESTINATION}"\n'
            f'let payload = [[{batch}]]\n'
            'send destination.process(payload)\n')


def run_vm(interpreter, source):
    interpreter.global_env.define("source", source)
    interpreter.interpret(compile_to_ast(RUN_VM))


def delivery(interpreter, remote):
    if remote:
        assert interpreter.mailboxes.get(DESTINATION, []) == []
        packets = interpreter.outbound_packets
        assert all(packet["target_node"] == REMOTE_NODE and packet["target_actor"] == DESTINATION
                   for packet in packets)
        return [packet["message"] for packet in packets]
    assert interpreter.outbound_packets == []
    return interpreter.mailboxes.get(DESTINATION, [])


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("verified", [False, True], ids=["ordinary", "verified"])
def test_a_compiled_send_replays_without_delivery_and_continues_with_one_new_effect(remote, verified):
    live = Interpreter()
    live.source_code = RUN_VM
    if remote:
        live.register_route(DESTINATION, REMOTE_NODE)
    run_vm(live, program(1))
    assert [item["payload"] for item in delivery(live, remote)] == [[[1]]]
    assert [event["type"] for event in messages(live)] == ["message_forwarded" if remote else "message_sent"]

    saved = json.loads(json.dumps(live.snapshot()))
    recorded = copy.deepcopy(live.execution_history)
    replay = Interpreter()
    replay.run_id = live.run_id
    if verified:
        replay.durable_profile = DURABLE_COGNITIVE_PROFILE
    replay.load_snapshot(saved)
    run_vm(replay, program(1))

    assert replay.runtime_mode is RuntimeMode.LIVE
    assert replay.execution_history == recorded
    assert replay.global_env.get("result") == live.global_env.get("result")
    assert delivery(replay, remote) == delivery(live, remote)
    assert replay.actor_log == live.actor_log

    run_vm(replay, program(2))
    assert [item["payload"] for item in delivery(replay, remote)] == [[[1]], [[2]]]
    assert [event["message"]["payload"] for event in messages(replay)] == [[[1]], [[2]]]
    assert replay.execution_history[:len(recorded)] == recorded
    assert saved["execution_history"] == recorded

    # Saving the continuation and replaying both invocations still consumes
    # the same two sends; it must not turn the new work into a third delivery.
    twice = Interpreter()
    twice.run_id = replay.run_id
    if verified:
        twice.durable_profile = DURABLE_COGNITIVE_PROFILE
    twice.load_snapshot(json.loads(json.dumps(replay.snapshot())))
    run_vm(twice, program(1))
    run_vm(twice, program(2))
    assert twice.runtime_mode is RuntimeMode.LIVE
    assert twice.execution_history == replay.execution_history
    assert twice.global_env.get("result") == replay.global_env.get("result")
    assert delivery(twice, remote) == delivery(replay, remote)


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
def test_a_compiled_send_missing_from_the_replay_record_cannot_deliver(remote):
    live = Interpreter()
    live.source_code = RUN_VM
    if remote:
        live.register_route(DESTINATION, REMOTE_NODE)
    source = program(1) + 'let recorded_after_send = uuid()\n'
    run_vm(live, source)
    replay = Interpreter()
    replay.load_snapshot(json.loads(json.dumps(live.snapshot())))
    before = copy.deepcopy((replay.mailboxes, replay.outbound_packets, replay.actor_log))
    different = source.replace('destination.process(payload)', 'destination.archive(payload)')

    with pytest.raises((ReplayIntegrityError, VMHostError), match="REPLAY_INTEGRITY_ERROR"):
        run_vm(replay, different)

    assert (replay.mailboxes, replay.outbound_packets, replay.actor_log) == before
    assert messages(replay) == messages(live)


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("verified", [False, True], ids=["ordinary", "verified"])
def test_a_recorded_policy_verdict_survives_policy_change_until_new_compiled_work(remote, verified):
    live = Interpreter()
    live.source_code = RUN_VM
    policy = (f'policy Gate {{ target "{DESTINATION}.process" guard(args) {{\n'
              'if sender != "global" { reject "wrong sender" }\n} }\n')
    live.interpret(compile_to_ast(policy))
    if remote:
        live.register_route(DESTINATION, REMOTE_NODE)
    run_vm(live, program(1))
    recorded = copy.deepcopy(live.execution_history)
    verdicts = [event for event in recorded if event["type"] == "policy_evaluated"]
    assert verdicts == [{"type": "policy_evaluated", "policy": "Gate",
                        "target": f"{DESTINATION}.process", "sender": "global",
                        "receiver": DESTINATION, "method": "process",
                        "args": [[[1]]], "result": "pass"}]

    replay = Interpreter()
    replay.run_id = live.run_id
    if verified:
        replay.durable_profile = DURABLE_COGNITIVE_PROFILE
    replay.load_snapshot(copy.deepcopy(live.snapshot()))
    revoked = (f'policy Gate {{ target "{DESTINATION}.process" guard(args) {{\n'
               'reject "permission revoked after checkpoint"\n} }\n')
    replay.interpret(compile_to_ast(revoked))
    run_vm(replay, program(1))
    assert replay.runtime_mode is RuntimeMode.LIVE
    assert replay.execution_history == recorded
    assert replay.global_env.get("result") == live.global_env.get("result")
    assert delivery(replay, remote) == delivery(live, remote)
    assert replay.actor_log == live.actor_log

    with pytest.raises(VMHostError, match="permission revoked after checkpoint"):
        run_vm(replay, program(1))
    assert replay.execution_history[:len(recorded)] == recorded
    new_events = replay.execution_history[len(recorded):]
    assert [event["type"] for event in new_events] == ["vm_bytecode_compiled", "policy_violation"]
    assert new_events[-1]["policy"] == "Gate"
    assert new_events[-1]["args"] == [[[1]]]
    assert messages(replay) == messages(live)
    assert delivery(replay, remote) == delivery(live, remote)


@pytest.mark.parametrize("remote", [False, True], ids=["local", "remote"])
@pytest.mark.parametrize("edited", ["arguments", "returned", "vm_outbox", "delivered", "history"])
def test_each_compiled_send_owner_keeps_its_own_nested_data(remote, edited):
    interpreter = Interpreter()
    if remote:
        interpreter.register_route(DESTINATION, REMOTE_NODE)
    argument = {"batch": [{"number": 1}]}
    code = CognitiveCompiler().compile(compile_to_ast('send destination.process(payload)\n'))
    vm = CognitiveVM(code, state=VMState(locals={"destination": DESTINATION, "payload": argument}))
    vm.host = interpreter.runtime.vm.get_cvm_callback_adapter(vm)

    # Stop immediately after the real delivery, while the VM still holds the
    # host's returned value. The following statement cleanup is immaterial.
    for _ in range(100):
        vm.step()
        if messages(interpreter):
            break
    assert len(messages(interpreter)) == 1
    assert len(delivery(interpreter, remote)) == 1
    assert len(vm.state.mailbox_outbound) == 1
    views = {
        "arguments": argument,
        "returned": vm.state.stack[-1]["payload"],
        "vm_outbox": vm.state.mailbox_outbound[0]["payload"],
        "delivered": delivery(interpreter, remote)[0]["payload"],
        "history": messages(interpreter)[0]["message"]["payload"],
    }
    assert all(value == {"batch": [{"number": 1}]} for value in views.values())
    views[edited]["batch"][0]["number"] = 9
    assert {name: value["batch"][0]["number"] for name, value in views.items()} == {
        name: 9 if name == edited else 1 for name in views}
