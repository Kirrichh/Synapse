"""Acceptance of CVM send wire compatibility across retained Gold E1 replay."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from synapse.ast import Literal, Program, SendStmt
from synapse.bytecode import BytecodeProgram, CognitiveCompiler, Instruction
from synapse.cvm import CognitiveVM, VMSnapshotFormatError, VMState, encode_vm_value
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import canonicalization as CANON
from synapse.experiments.gold import replay as REPLAY
from synapse.experiments.gold import replay_vm_codec as CODEC
from synapse.experiments.gold.behavior import BehaviorCore, create_behavior_unit
from tests.stage4_gold_replay_support import (
    ARTIFACTS, channel_for, governed_activity, restore_vm_adapter, vm_adapter,
)


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "gold"


def canonical_bytes(value):
    return json.dumps(encode_vm_value(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def legacy_program(payload):
    return BytecodeProgram(
        instructions=[Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1),
                      Instruction("MSG_SEND", "apply"), Instruction("HALT")],
        constants=["DeploymentController", payload],
    )


def legacy_message(payload):
    return {
        "msg_type": "apply", "method": "apply",
        "sender_id": "default_agent", "sender": "default_agent",
        "target_id": "DeploymentController", "receiver": "DeploymentController",
        "payload": payload, "payload_hash": "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest(),
    }


def recorded_send(program, message):
    return governed_activity(
        kind=ACT.ActivityKind.MESSAGE_SEND,
        inputs=ACT.activity_inputs(
            opcode=b"MSG_SEND",
            operand_a=canonical_bytes({"symbol": "SYS_MSG_SEND", "args": [message]}),
            operand_b=canonical_bytes(None),
        ),
        position=ACT.ActivityPosition(program_hash=program.program_hash,
                                      instruction_pointer=2, frame_depth=0, sequence=1),
        result=CODEC.encode_recorded_result({"accepted": "deployment-42"}),
    )


def artifact_core(program):
    reference = ARTIFACTS.publish(canonical_bytes(program.to_dict()))
    payload = json.loads((FIXTURES / "behavior_vectors_v1.json").read_text())["vectors"][0]["core"]
    payload["canonical_program"] = {"form": "ARTIFACT_REF_V1", "artifact_ref": reference.to_dict()}
    payload["artifact_refs"] = [reference.to_dict()]
    payload["capability_requirements"] = ["capability.message.send"]
    core = BehaviorCore.from_dict(payload)
    return create_behavior_unit(
        behavior_kind=core.behavior_kind, canonical_program=core.canonical_program,
        input_contract=core.input_contract, output_contract=core.output_contract,
        capability_requirements=core.capability_requirements, replay_contract=core.replay_contract,
        verification_contract=core.verification_contract, binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs, artifact_refs=core.artifact_refs,
    ).canonical_core


@pytest.mark.parametrize("payload", [None, [], ["canary", "production"],
    {"schema_version": "synapse.cvm.send-args/v1", "args": ["opaque", "document"]}])
def test_legacy_gold_send_stays_opaque_across_recorded_replay_and_resume(payload):
    program, message = legacy_program(payload), legacy_message(payload)
    record = recorded_send(program, message)
    machine = vm_adapter(program)
    machine.attach_channel(channel_for(record, budget=4))
    machine.step()
    machine.step()
    retained = machine.snapshot_bytes()
    restored = restore_vm_adapter(retained)
    restored_channel = channel_for(record, budget=4)
    restored.attach_channel(restored_channel)
    assert restored.snapshot_bytes() == retained
    machine.step()
    restored.step()
    assert machine.snapshot_bytes() == restored.snapshot_bytes()
    state = json.loads(restored.snapshot_bytes())["machine"]["state"]
    assert state["mailbox_outbound"] == [message]
    assert state["stack"] == [{"accepted": "deployment-42"}]
    assert "sender_id" not in state
    assert "schema_version" not in state["mailbox_outbound"][0]
    assert restored_channel.consumed_identities() == (record.activity_identity,)


def test_retained_gold_snapshot_and_capability_digest_remain_byte_identical():
    raw = (FIXTURES / "golden_replays" / "pure_add_v1.vm_snapshot.json").read_bytes().rstrip(b"\n")
    golden = json.loads((FIXTURES / "golden_replays" / "pure_add_v1.json").read_text())
    restored = restore_vm_adapter(raw)
    assert restored.snapshot_bytes() == raw
    assert REPLAY.capability_profile_digest() == golden["capability_profile_digest"]
    assert restored.snapshot_digest() == golden["expected_terminal_snapshot_digest"]


def test_new_compiler_send_has_explicit_arity_outside_frozen_gold_artifact_profile():
    program = CognitiveCompiler().compile(Program(statements=[
        SendStmt(receiver=Literal(value="DeploymentController"), method="apply", args=[]),
    ]))
    assert "MSG_SEND_ARGS" in [instruction.op for instruction in program.instructions]
    with pytest.raises(CANON.CanonicalizationViolation) as caught:
        CANON.bind_resolved_artifact_program(core=artifact_core(program), program=program,
                                           unit_context_sha256="a" * 64)
    assert caught.value.failure_code is CANON.CanonicalizationFailureCode.FORBIDDEN_OPCODE
    assert "MSG_SEND_ARGS" in str(caught.value)


def test_frozen_gold_replay_refuses_new_opcode_before_any_transition_or_effect():
    program = BytecodeProgram(instructions=[Instruction("MSG_SEND_ARGS", "apply")])
    machine = vm_adapter(program)
    channel = channel_for(budget=4)
    machine.attach_channel(channel)
    before = machine.snapshot_bytes()
    with pytest.raises(REPLAY.ReplayViolation) as caught:
        machine.step()
    assert caught.value.failure_code is REPLAY.ReplayFailureCode.OPCODE_NOT_CLASSIFIED
    assert machine.snapshot_bytes() == before
    assert channel.consumed_identities() == ()


def test_source_sender_extension_does_not_silently_widen_gold_state():
    program = legacy_program(None)
    with pytest.raises(REPLAY.ReplayViolation) as caught:
        vm_adapter(program, state=VMState(sender_id="Planner"))
    assert caught.value.failure_code is REPLAY.ReplayFailureCode.TYPE_MISMATCH
    assert "sender_id" in str(caught.value)


def test_source_sender_snapshot_is_optional_but_binds_new_transition_identity():
    program = BytecodeProgram(instructions=[Instruction("LOAD_NONE"), Instruction("HALT")])
    legacy = VMState()
    named = VMState(sender_id="Planner")
    assert "sender_id" not in legacy.to_dict()
    assert VMState.from_dict(legacy.to_dict()).to_dict() == legacy.to_dict()
    assert VMState.from_dict(named.to_dict()).sender_id == "Planner"
    old_vm, new_vm = CognitiveVM(program, legacy), CognitiveVM(program, named)
    old_vm.step()
    new_vm.step()
    assert old_vm.state.transition_hash != new_vm.state.transition_hash


@pytest.mark.parametrize("sender", ["", 1, [], False])
def test_malformed_source_sender_cannot_enter_a_restored_vm(sender):
    snapshot = VMState().to_dict()
    snapshot["sender_id"] = sender
    with pytest.raises(VMSnapshotFormatError, match="sender_id"):
        VMState.from_dict(snapshot)
