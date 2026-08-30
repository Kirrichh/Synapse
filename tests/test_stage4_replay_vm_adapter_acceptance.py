"""Product acceptance for pending CVM replay lifecycles and adapter identity.

The cases cross the exact machine factory and a real sealed
``RecordedActivityChannel`` backed by the Stage 4 ledger/store. There is no live
producer: only the canonical blob named by a recorded activity.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import (
    CognitiveVM, VMState, VMStatus, compute_message_consumed_id, decode_vm_value,
    encode_vm_value,
)
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_structural_history as RSH
from synapse.experiments.gold import replay_vm_codec as RVC
from synapse.experiments.gold import replay_vm_adapter as RVM
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from tests.stage4_gold_replay_support import POLICY, channel_for, contract_for, governed_activity


GAS = 10_000
PROMPT_TEXT = "explain the pending replay"
PROMPT = {
    "type": "prompt_envelope",
    "template_hash": "template:pending-replay/v1",
    "variables": {"question": PROMPT_TEXT},
    "variables_hash": hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest(),
}
SCHEMA_HASH = "schema:answer/v1"
ENGINE_PARAMS = {"temperature": 0, "top_p": 1}
CACHE_POLICY = "model_change"
EXPECTED_ARGUMENTS = [PROMPT, SCHEMA_HASH, ENGINE_PARAMS, CACHE_POLICY]
RESULT = {"answer": "recorded", "model": "offline-fixture"}
RESULT_BYTES = RVC.encode_recorded_result(RESULT)
CONTEXT = R.replay_machine_execution_context(
    run_id=RunId("point-of-use-run"),
    attempt_id=AttemptId("point-of-use-attempt"),
    repository_revision=RepositoryRevision.git_commit("a" * 40),
    environment_profile_id="production-point-of-use",
    policy_version=POLICY,
)
FACTORY = RVM.CognitiveVMReplayMachineFactory()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _machine_bytes(value: object) -> bytes:
    return _canonical_bytes(encode_vm_value(value))


def _program() -> BytecodeProgram:
    return BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", a=0),
            Instruction(
                "LLM_REQUEST", a=SCHEMA_HASH,
                b=copy.deepcopy(ENGINE_PARAMS), c=CACHE_POLICY,
            ),
            Instruction("POP"),
            Instruction("HALT"),
        ],
        constants=[copy.deepcopy(PROMPT)],
    )


def _snapshot(machine) -> dict[str, object]:
    return json.loads(machine.snapshot_bytes().decode("utf-8"))


def _pending(snapshot: dict[str, object]) -> dict[str, object]:
    pending = snapshot["machine"]["state"]["pending_host_call"]
    assert type(pending) is dict
    return pending


def _pause_with_real_channel(context=CONTEXT):
    channel = channel_for(budget=4)
    machine = FACTORY.build(_program(), gas_budget=GAS, execution_context=context)
    machine.attach_channel(channel)
    machine.step()
    assert machine.next_opcode() == "LLM_REQUEST"
    machine.step()
    return machine, channel


def _record_for_pending(
    snapshot: dict[str, object], result_bytes: bytes = RESULT_BYTES
) -> ACT.RecordedActivity:
    pending = _pending(snapshot)
    decoded_arguments = decode_vm_value(copy.deepcopy(pending["args"]))
    assert decoded_arguments == EXPECTED_ARGUMENTS
    return governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_REQUEST",
            symbol=b"llm.request",
            arguments=RVC.encode_recorded_result(EXPECTED_ARGUMENTS),
            argc=RVC.encode_recorded_result(4),
            call_id=pending["call_id"].encode("utf-8"),
            created_at_event_id=pending["created_at_event_id"].encode("utf-8"),
        ),
        position=ACT.ActivityPosition(
            program_hash=snapshot["machine"]["program"]["program_hash"],
            instruction_pointer=pending["ip_after_call"] - 1,
            frame_depth=pending["frame_depth_at_call"],
            sequence=1,
        ),
        result=result_bytes,
    )


def test_host_status_is_local_and_a_ledger_miss_does_not_advance_sequence() -> None:
    machine, channel = _pause_with_real_channel()
    pending_bytes = machine.snapshot_bytes()
    snapshot = _snapshot(machine)
    pending = _pending(snapshot)

    assert channel.consumed_identities() == ()
    assert snapshot["activity_sequence"] == 0
    assert machine.next_opcode() == "LLM_RESUME"
    assert machine.next_step_gas_cost() == 0
    assert snapshot["machine"]["state"]["ip"] == pending["ip_after_call"] == 2
    assert pending["ip_after_call"] - 1 == 1
    assert pending["call_id"] == pending["deterministic_call_id"]

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert machine.snapshot_bytes() == pending_bytes
    assert channel.consumed_identities() == ()


def test_host_status_event_identity_is_bound_to_the_sealed_execution_context() -> None:
    other_context = R.replay_machine_execution_context(
        run_id=RunId("other-point-of-use-run"),
        attempt_id=AttemptId("other-point-of-use-attempt"),
        repository_revision=RepositoryRevision.git_commit("a" * 40),
        environment_profile_id="production-point-of-use",
        policy_version=POLICY,
    )
    first, first_channel = _pause_with_real_channel()
    second, second_channel = _pause_with_real_channel(other_context)
    first_pending, second_pending = _pending(_snapshot(first)), _pending(_snapshot(second))

    assert first_pending["created_at_event_id"] != second_pending["created_at_event_id"]
    assert first_pending["call_id"] != second_pending["call_id"]
    assert first_channel.consumed_identities() == second_channel.consumed_identities() == ()
    assert _snapshot(first)["activity_sequence"] == _snapshot(second)["activity_sequence"] == 0
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(first.snapshot_bytes(), gas_budget=GAS, execution_context=other_context)
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH


def test_function_closure_snapshot_schema_is_exact() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("MAKE_FUNCTION", "inner", 0, 2), Instruction("HALT"), Instruction("RETURN")],
        constants=[[]],
    )
    machine = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    machine.step()
    snapshot = _snapshot(machine)
    snapshot["machine"]["state"]["stack"][0]["closure"] = [["forged", 1]]
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(_canonical_bytes(snapshot), gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE


def test_rejected_structural_transition_rolls_back_state_and_history(monkeypatch) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", "context"), Instruction("HALT")]
    )
    channel = channel_for(budget=4)
    machine = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    machine.attach_channel(channel)
    before_snapshot, before_history = machine.snapshot_bytes(), machine.structural_history_bytes()
    original = CognitiveVM._call_host

    def suppress_context_callback(vm, opcode, a, b):
        if opcode == "CALL_HOST" and type(a) is dict and a.get("symbol") == "SYS_CONTEXT_ENTER":
            return None
        return original(vm, opcode, a, b)

    monkeypatch.setattr(CognitiveVM, "_call_host", suppress_context_callback)
    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH
    assert machine.snapshot_bytes() == before_snapshot
    assert machine.structural_history_bytes() == before_history
    assert channel.consumed_identities() == ()


def test_pending_snapshot_resolves_once_and_post_resume_restore_cannot_reinject() -> None:
    paused, status_channel = _pause_with_real_channel()
    pending_bytes = paused.snapshot_bytes()
    pending_snapshot = _snapshot(paused)
    pending = _pending(pending_snapshot)
    activity = _record_for_pending(pending_snapshot)
    channel = channel_for(activity, budget=4)

    restored = FACTORY.restore(
        pending_bytes, gas_budget=GAS, execution_context=CONTEXT
    )
    restored.attach_channel(channel)
    restored.step()

    resumed = _snapshot(restored)
    assert status_channel.consumed_identities() == ()
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert activity.position.instruction_pointer == pending["ip_after_call"] - 1
    assert activity.position.sequence == 1
    assert resumed["activity_sequence"] == 1
    assert resumed["machine"]["state"]["pending_host_call"] is None
    assert resumed["machine"]["state"]["ip"] == pending["ip_after_call"]
    assert resumed["machine"]["state"]["stack"] == [RESULT]
    assert restored.next_opcode() == "POP"

    no_second_result = channel_for(budget=4)
    continued = FACTORY.restore(
        restored.snapshot_bytes(), gas_budget=GAS, execution_context=CONTEXT
    )
    continued.attach_channel(no_second_result)
    continued.step()
    continued.step()

    terminal = _snapshot(continued)
    assert continued.is_halted()
    assert terminal["activity_sequence"] == 1
    assert terminal["machine"]["state"]["pending_host_call"] is None
    assert no_second_result.consumed_identities() == ()


def test_tampered_pending_identity_and_noncanonical_result_are_refused() -> None:
    paused, _status_channel = _pause_with_real_channel()
    pending_bytes = paused.snapshot_bytes()
    pending_snapshot = _snapshot(paused)

    changed_args = copy.deepcopy(pending_snapshot)
    _pending(changed_args)["args"][1] = 7
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(
            _canonical_bytes(changed_args), gas_budget=GAS, execution_context=CONTEXT
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

    changed_call = copy.deepcopy(pending_snapshot)
    changed_call_pending = _pending(changed_call)
    changed_call_pending["call_id"] = "forged-call-id"
    changed_call_pending["deterministic_call_id"] = "forged-call-id"
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(
            _canonical_bytes(changed_call), gas_budget=GAS, execution_context=CONTEXT
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH

    changed_argc = copy.deepcopy(pending_snapshot)
    _pending(changed_argc)["argc"] = 4.0
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(_canonical_bytes(changed_argc), gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

    changed_transition = copy.deepcopy(pending_snapshot)
    _pending(changed_transition)["transition_hash_at_call"] = "forged-transition"
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(
            _canonical_bytes(changed_transition), gas_budget=GAS, execution_context=CONTEXT
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH

    sloppy = b'{ "model": "offline-fixture", "answer": "recorded" }'
    activity = _record_for_pending(pending_snapshot, sloppy)
    channel = channel_for(activity, budget=4)
    restored = FACTORY.restore(
        pending_bytes, gas_budget=GAS, execution_context=CONTEXT
    )
    restored.attach_channel(channel)
    with pytest.raises(R.ReplayViolation) as excinfo:
        restored.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE
    failed = _snapshot(restored)
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert failed["activity_sequence"] == 1
    assert failed["machine"]["state"]["pending_host_call"] == _pending(pending_snapshot)


def test_message_send_ledger_miss_refuses_before_the_cvm_transition() -> None:
    target, payload = "actor:receiver", {"body": "not-recorded"}
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1),
            Instruction("MSG_SEND", "ping"), Instruction("HALT"),
        ],
        constants=[target, payload],
    )
    machine = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    channel = channel_for(budget=4)
    machine.attach_channel(channel)
    machine.step()
    machine.step()
    before = machine.snapshot_bytes()

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert machine.snapshot_bytes() == before
    assert _snapshot(machine)["machine"]["state"]["mailbox_outbound"] == []
    assert channel.consumed_identities() == ()


def test_queued_message_receive_is_one_atomic_recorded_occurrence(monkeypatch) -> None:
    message = {
        "msg_type": "ping", "sender_id": "actor:sender",
        "payload": {"body": "queued"},
    }
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "message"), Instruction("HALT")]
    )
    initial_state = VMState(gas_remaining=GAS, mailbox_inbound=[copy.deepcopy(message)])
    miss = RVM.CognitiveVMReplayAdapter(
        program, gas_budget=GAS, execution_context=CONTEXT,
        _state=copy.deepcopy(initial_state),
    )
    miss_channel = channel_for(budget=4)
    miss.attach_channel(miss_channel)
    before = miss.snapshot_bytes()
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        miss.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert miss.snapshot_bytes() == before
    assert miss_channel.consumed_identities() == ()

    transition = initial_state.transition_hash
    event_id = RVM._deterministic_event_id(
        CONTEXT, program_hash=program.program_hash, instruction_pointer=0,
        transition_hash=transition, frame_depth=0,
    )
    payload_hash = "sha256:" + hashlib.sha256(_machine_bytes(message["payload"])).hexdigest()
    event = {
        "type": "message_consumed",
        "message_consumed_id": compute_message_consumed_id(
            "default_agent", "ping", "actor:sender", transition, event_id, payload_hash,
        ),
        "receiver_id": "default_agent", "msg_type": "ping",
        "sender_id": "actor:sender", "payload_hash": payload_hash,
        "message": encode_vm_value(message),
    }
    activity = governed_activity(
        kind=ACT.ActivityKind.MESSAGE_RECEIVE,
        inputs=ACT.activity_inputs(
            opcode=b"MSG_RECEIVE",
            operand_a=_machine_bytes({"symbol": "SYS_MSG_CONSUME", "args": [event]}),
            operand_b=_machine_bytes(None),
        ),
        position=ACT.ActivityPosition(
            program_hash=program.program_hash, instruction_pointer=0,
            frame_depth=0, sequence=1,
        ),
        result=RVC.encode_recorded_result({"status": "recorded-consume"}),
    )
    channel = channel_for(activity, budget=4)
    machine = RVM.CognitiveVMReplayAdapter(
        program, gas_budget=GAS, execution_context=CONTEXT,
        _state=copy.deepcopy(initial_state),
    )
    machine.attach_channel(channel)
    machine.step()
    state = _snapshot(machine)["machine"]["state"]
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert state["mailbox_inbound"] == []
    assert state["pending_message_receive"] is None
    assert state["locals"] == {"sender": "actor:sender", "message": message}
    assert state["stack"] == [message]

    original_step = CognitiveVM.step

    def leave_forged_pending(vm):
        result = original_step(vm)
        if vm.program.instructions[0].op == "MSG_RECEIVE":
            vm.state.pending_message_receive = {"status": VMStatus.PAUSED_MESSAGING}
        return result

    monkeypatch.setattr(CognitiveVM, "step", leave_forged_pending)
    forged_channel = channel_for(activity, budget=4)
    forged = RVM.CognitiveVMReplayAdapter(
        program, gas_budget=GAS, execution_context=CONTEXT,
        _state=copy.deepcopy(initial_state),
    )
    forged.attach_channel(forged_channel)
    before_forgery = _snapshot(forged)
    with pytest.raises(R.ReplayViolation) as excinfo:
        forged.step()
    failed = _snapshot(forged)
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
    assert failed["machine"] == before_forgery["machine"]
    assert failed["activity_sequence"] == 1
    assert forged_channel.consumed_identities() == (activity.activity_identity,)


def test_pending_message_receive_miss_is_atomic_and_resume_cannot_reinject(monkeypatch) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "message"), Instruction("HALT")]
    )
    paused = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    miss_channel = channel_for(budget=4)
    paused.attach_channel(miss_channel)
    paused.step()
    pending_bytes, pending_snapshot = paused.snapshot_bytes(), _snapshot(paused)
    pending = pending_snapshot["machine"]["state"]["pending_message_receive"]
    assert type(pending) is dict

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        paused.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert paused.snapshot_bytes() == pending_bytes
    assert miss_channel.consumed_identities() == ()

    message = {
        "msg_type": "ping", "sender_id": "actor:sender",
        "payload": {"body": "from durable history"},
    }
    activity = governed_activity(
        kind=ACT.ActivityKind.MESSAGE_RECEIVE,
        inputs=ACT.activity_inputs(
            opcode=b"MSG_RECEIVE", receiver_id=pending["receiver_id"].encode("utf-8"),
            sender_var=pending["sender_var"].encode("utf-8"),
            target_var=pending["target_var"].encode("utf-8"),
            message_receive_id=pending["message_receive_id"].encode("utf-8"),
            created_at_event_id=pending["created_at_event_id"].encode("utf-8"),
        ),
        position=ACT.ActivityPosition(
            program_hash=program.program_hash, instruction_pointer=0,
            frame_depth=0, sequence=1,
        ),
        result=RVC.encode_recorded_result(message),
    )
    original_resume = CognitiveVM.resume_message_receive

    def fail_after_message_injection(vm, delivered):
        original_resume(vm, delivered)
        raise RuntimeError("partial message resume")

    monkeypatch.setattr(CognitiveVM, "resume_message_receive", fail_after_message_injection)
    failed_channel = channel_for(activity, budget=4)
    failed = FACTORY.restore(pending_bytes, gas_budget=GAS, execution_context=CONTEXT)
    failed.attach_channel(failed_channel)
    with pytest.raises(R.ReplayViolation) as excinfo:
        failed.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.MACHINE_EXECUTION_FAULT
    failed_snapshot = _snapshot(failed)
    assert failed_snapshot["machine"] == pending_snapshot["machine"]
    assert failed_snapshot["activity_sequence"] == 1
    assert failed_channel.consumed_identities() == (activity.activity_identity,)

    monkeypatch.setattr(CognitiveVM, "resume_message_receive", original_resume)
    channel = channel_for(activity, budget=4)
    resumed = FACTORY.restore(pending_bytes, gas_budget=GAS, execution_context=CONTEXT)
    resumed.attach_channel(channel)
    resumed.step()
    resumed_bytes, state = resumed.snapshot_bytes(), _snapshot(resumed)["machine"]["state"]
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert state["pending_message_receive"] is None
    assert state["locals"] == {"sender": "actor:sender", "message": message}
    assert state["stack"] == [message]

    no_second_record = channel_for(budget=4)
    continued = FACTORY.restore(resumed_bytes, gas_budget=GAS, execution_context=CONTEXT)
    continued.attach_channel(no_second_record)
    continued.step()
    terminal = _snapshot(continued)
    assert continued.is_halted()
    assert terminal["activity_sequence"] == 1
    terminal_state = terminal["machine"]["state"]
    assert terminal_state["pending_message_receive"] is None
    assert terminal_state["locals"] == state["locals"]
    assert terminal_state["stack"] == state["stack"]
    assert no_second_record.consumed_identities() == ()


def test_pending_creation_and_llm_resume_are_validated_inside_the_transaction(
    monkeypatch,
) -> None:
    machine = FACTORY.build(_program(), gas_budget=GAS, execution_context=CONTEXT)
    machine.attach_channel(channel_for(budget=4))
    machine.step()
    before_creation = machine.snapshot_bytes()
    original_step = CognitiveVM.step

    def create_tampered_pending(vm):
        result = original_step(vm)
        if vm.state.pending_host_call is not None:
            vm.state.pending_host_call["argc"] = 5
        return result

    monkeypatch.setattr(CognitiveVM, "step", create_tampered_pending)
    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH
    assert machine.snapshot_bytes() == before_creation

    monkeypatch.setattr(CognitiveVM, "step", original_step)
    paused, _status_channel = _pause_with_real_channel()
    pending_bytes, pending_snapshot = paused.snapshot_bytes(), _snapshot(paused)
    activity = _record_for_pending(pending_snapshot)
    channel = channel_for(activity, budget=4)
    resumed = FACTORY.restore(pending_bytes, gas_budget=GAS, execution_context=CONTEXT)
    resumed.attach_channel(channel)
    original_resume = CognitiveVM.resume_host_call

    def forge_resume_successor(vm, call_id, result):
        original_resume(vm, call_id, result)
        vm.state.transition_hash = "forged-transition"

    monkeypatch.setattr(CognitiveVM, "resume_host_call", forge_resume_successor)
    with pytest.raises(R.ReplayViolation) as excinfo:
        resumed.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
    failed = _snapshot(resumed)
    assert failed["machine"] == pending_snapshot["machine"]
    assert failed["activity_sequence"] == 1
    assert channel.consumed_identities() == (activity.activity_identity,)


def test_return_refuses_an_expected_superbatch_before_the_vm_transition() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "scoped", 0, 3), Instruction("CALL", 0),
            Instruction("HALT"), Instruction("CONTEXT_ENTER", "context:inner"),
            Instruction("LOAD_CONST", 1), Instruction("RETURN"),
        ],
        constants=[[], "done"],
    )
    captured = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    captured.attach_channel(channel_for(budget=4))
    while not captured.is_halted():
        captured.step()
    commands = list(RSH.decode_replay_structural_history(captured.structural_history_bytes()))
    return_index = next(index for index, command in enumerate(commands) if command.opcode == "RETURN")
    first = dataclasses.replace(commands[return_index], occurrence_size=2)
    commands[return_index:return_index + 1] = [
        first, dataclasses.replace(first, occurrence_index=1)
    ]
    superbatch = RSH.encode_replay_structural_history(
        tuple(commands), profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        profile_digest=R.capability_profile_digest(),
    )
    replayed = FACTORY.build(
        program, gas_budget=GAS, execution_context=CONTEXT,
        expected_structural_history=superbatch,
    )
    channel = channel_for(budget=4)
    replayed.attach_channel(channel)
    while replayed.next_opcode() != "RETURN":
        replayed.step()
    before_state, before_history = replayed.snapshot_bytes(), replayed.structural_history_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        replayed.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH
    assert replayed.snapshot_bytes() == before_state
    assert replayed.structural_history_bytes() == before_history
    assert channel.consumed_identities() == ()

def test_return_refuses_expected_unwind_when_actual_batch_is_empty_before_step() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "scoped", 0, 3), Instruction("CALL", 0),
            Instruction("HALT"), Instruction("CONTEXT_ENTER", "context:inner"),
            Instruction("LOAD_CONST", 1), Instruction("RETURN"),
        ],
        constants=[[], "done"],
    )
    captured = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    captured.attach_channel(channel_for(budget=4))
    while captured.next_opcode() != "RETURN":
        captured.step()
    before_return_history = captured.structural_history_bytes()
    before_return = _snapshot(captured)
    captured.step()
    return_commands = tuple(
        command
        for command in RSH.decode_replay_structural_history(
            captured.structural_history_bytes()
        )
        if command.opcode == "RETURN"
    )
    assert return_commands
    expected = captured.structural_history_bytes()

    empty_unwind = copy.deepcopy(before_return)
    empty_unwind["machine"]["state"]["context_stack"] = []
    replayed = FACTORY.restore(
        _canonical_bytes(empty_unwind), gas_budget=GAS, execution_context=CONTEXT,
        expected_structural_history=expected,
    )
    channel = channel_for(budget=4)
    replayed.attach_channel(channel)
    before_state = replayed.snapshot_bytes()
    before_history = replayed.structural_history_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        replayed.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH
    assert replayed.snapshot_bytes() == before_state
    assert replayed.structural_history_bytes() == before_history
    assert channel.consumed_identities() == ()

    later_commands = tuple(
        dataclasses.replace(command, pre_transition_hash="sha256:" + "f" * 64)
        for command in return_commands
    )
    before_return_commands = RSH.decode_replay_structural_history(before_return_history)
    later_expected = RSH.encode_replay_structural_history(
        before_return_commands + later_commands,
        profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        profile_digest=R.capability_profile_digest(),
    )
    later = FACTORY.restore(
        _canonical_bytes(empty_unwind), gas_budget=GAS, execution_context=CONTEXT,
        expected_structural_history=later_expected,
    )
    later_channel = channel_for(budget=4)
    later.attach_channel(later_channel)
    later.step()

    assert later.next_opcode() == "HALT"
    assert RSH.decode_replay_structural_history(
        later.structural_history_bytes()
    ) == before_return_commands
    assert later_channel.consumed_identities() == ()


def test_snapshot_restore_preserves_structural_progress_and_binds_expected_tail() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("CONTEXT_ENTER", "context:one"),
            Instruction("CONTEXT_EXIT", "context:one"),
            Instruction("HALT"),
        ],
        constants=[],
    )
    captured = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    captured.attach_channel(channel_for(budget=4))
    captured.step()
    prefix = captured.structural_history_bytes()
    snapshot = captured.snapshot_bytes()
    while not captured.is_halted():
        captured.step()
    expected = captured.structural_history_bytes()

    replayed = FACTORY.restore(
        snapshot, gas_budget=GAS, execution_context=CONTEXT,
        expected_structural_history=expected,
    )
    replayed.attach_channel(channel_for(budget=4))
    assert replayed.snapshot_bytes() == snapshot
    assert replayed.structural_history_bytes() == prefix
    while not replayed.is_halted():
        replayed.step()
    assert replayed.structural_history_complete()
    assert replayed.structural_history_bytes() == expected

    capture_continuation = FACTORY.restore(
        snapshot, gas_budget=GAS, execution_context=CONTEXT,
    )
    capture_continuation.attach_channel(channel_for(budget=4))
    while not capture_continuation.is_halted():
        capture_continuation.step()
    assert capture_continuation.structural_history_bytes() == expected

    wrong_tail = RSH.encode_replay_structural_history(
        (), profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        profile_digest=R.capability_profile_digest(),
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(
            snapshot, gas_budget=GAS, execution_context=CONTEXT,
            expected_structural_history=wrong_tail,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH


@pytest.mark.parametrize("refusal_point", ("next_opcode", "structural_history_complete"))
def test_driver_seals_late_typed_machine_port_refusals(
    monkeypatch, refusal_point: str,
) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LOAD_CONST", 0), Instruction("HALT")], constants=[1]
    )
    machine = FACTORY.build(program, gas_budget=GAS, execution_context=CONTEXT)
    channel = channel_for(budget=4); machine.attach_channel(channel)
    original = getattr(RVM.CognitiveVMReplayAdapter, refusal_point)

    def refuse(machine_self):
        state_ip = _snapshot(machine_self)["machine"]["state"]["ip"]
        if refusal_point == "structural_history_complete" or state_ip > 0:
            raise R.ReplayViolation(
                R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH,
                "falsification: late structural port refusal",
            )
        return original(machine_self)

    monkeypatch.setattr(RVM.CognitiveVMReplayAdapter, refusal_point, refuse)
    run = R._drive_one_behavior(
        binding=R.ReplayProgramBinding(
            "late-port-refusal", program.program_hash, program.host_abi_version,
            "od10-artifact-resolver", program.version, contract_for(()),
        ),
        machine=machine, channel=channel, gas_budget=GAS, step_limit=20,
    )

    assert run.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert run.steps_executed >= 1
    assert R.status_for_reason(run.failure_reason) is R.ReplayStatus.REPLAY_FAILED
