"""Product acceptance for the CVM ``LLM_REQUEST`` replay lifecycle.

The cases cross the exact machine factory and a real sealed
``RecordedActivityChannel`` backed by the Stage 4 ledger/store. There is no live
producer: only the canonical blob named by a recorded activity.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import decode_vm_value
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_vm_adapter as RVM
from synapse.experiments.gold.contracts import AttemptId, RepositoryRevision, RunId
from tests.test_stage4_gold_replay import POLICY, channel_for, governed_activity


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
RESULT_BYTES = RVM.encode_recorded_result(RESULT)
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


def _pause_with_real_channel():
    channel = channel_for(budget=4)
    machine = FACTORY.build(_program(), gas_budget=GAS, execution_context=CONTEXT)
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
            arguments=RVM.encode_recorded_result(EXPECTED_ARGUMENTS),
            argc=RVM.encode_recorded_result(4),
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
    assert machine.instruction_pointer() == pending["ip_after_call"] - 1 == 1
    assert pending["call_id"] == pending["deterministic_call_id"]

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert machine.snapshot_bytes() == pending_bytes
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
