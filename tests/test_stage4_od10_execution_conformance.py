"""Executable OD-10/V1-E1 conformance over valid real-CVM occurrences.

Effects use governed records; structural commands are captured and exact-replayed.
No live producer is reachable from this acceptance topology.
"""

from __future__ import annotations

import copy, hashlib, json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, FunctionObject, VMState, decode_vm_value, encode_vm_value
from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_store as R_STORE
from synapse.experiments.gold import replay_structural_history as RSH
from synapse.experiments.gold import replay_vm_codec as RVC
from synapse.experiments.gold import replay_vm_adapter as RVM
from synapse.experiments.gold.behavior import BehaviorCore, create_behavior_unit
from synapse.experiments.gold.canonicalization import (
    CanonicalizationFailureCode,
    CanonicalizationViolation,
    HashBoundRef,
    RefKind,
)
from synapse.experiments.gold.contracts import (
    AttemptId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
)
from synapse.experiments.gold.persistence import store_transaction
from tests.gold_store_fence import fence_for
from tests.stage4_gold_replay_support import (
    POLICY,
    channel_for,
    contract_for,
    governed_activity,
)

GAS = 20_000
CONTEXT = R.replay_machine_execution_context(
    run_id=RunId("od10-execution-matrix-run"),
    attempt_id=AttemptId("od10-execution-matrix-attempt"),
    repository_revision=RepositoryRevision.git_commit("b" * 40),
    environment_profile_id="od10-execution-matrix",
    policy_version=POLICY,
)
FACTORY = RVM.CognitiveVMReplayMachineFactory()
_VECTORS = Path(__file__).parent / "fixtures" / "gold" / "behavior_vectors_v1.json"


def _machine_bytes(value: object) -> bytes:
    return json.dumps(
        encode_vm_value(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _snapshot(machine: object) -> dict[str, object]:
    return json.loads(machine.snapshot_bytes().decode("utf-8"))


def _activity_sequence(machine: object) -> int:
    return _snapshot(machine)["activity_sequence"]


def _build(
    program: BytecodeProgram,
    *,
    expected_structural_history: bytes | None = None,
):
    return FACTORY.build(
        program,
        gas_budget=GAS,
        execution_context=CONTEXT,
        expected_structural_history=expected_structural_history,
    )


def _run_to_stop(machine: object, *, limit: int = 200) -> None:
    for _ in range(limit):
        if machine.is_halted() or machine.next_opcode() is None:
            return
        machine.step()
    raise AssertionError("executable specimen did not reach a stopping state")


def _recorded_activity(
    *,
    kind: ACT.ActivityKind,
    inputs: ACT.ActivityInputs,
    program: BytecodeProgram,
    instruction_pointer: int,
    sequence: int = 1,
    frame_depth: int = 0,
    result: object,
) -> ACT.RecordedActivity:
    return governed_activity(
        kind=kind,
        inputs=inputs,
        position=ACT.ActivityPosition(
            program_hash=program.program_hash,
            instruction_pointer=instruction_pointer,
            frame_depth=frame_depth,
            sequence=sequence,
        ),
        result=RVC.encode_recorded_result(result),
    )


@dataclass(frozen=True)
class PureOccurrence:
    opcode: str
    instructions: tuple[Instruction, ...]
    constants: tuple[object, ...] = ()
    target_ip: int = 0


def _binary(opcode: str, left: object, right: object) -> PureOccurrence:
    return PureOccurrence(
        opcode, (Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction(opcode)),
        (left, right), 2,
    )


PURE_OCCURRENCES = (
    PureOccurrence("LOAD_CONST", (Instruction("LOAD_CONST", 0),), (7,)),
    PureOccurrence("LOAD_NAME", (Instruction("LOAD_NAME", "missing"),)),
    PureOccurrence("LOAD_NONE", (Instruction("LOAD_NONE"),)),
    PureOccurrence("LOAD_TRUE", (Instruction("LOAD_TRUE"),)),
    PureOccurrence("LOAD_FALSE", (Instruction("LOAD_FALSE"),)),
    PureOccurrence(
        "STORE",
        (Instruction("LOAD_CONST", 0), Instruction("STORE", "saved")),
        (7,),
        1,
    ),
    PureOccurrence(
        "POP", (Instruction("LOAD_CONST", 0), Instruction("POP")), (7,), 1
    ),
    PureOccurrence(
        "DUP", (Instruction("LOAD_CONST", 0), Instruction("DUP")), (7,), 1
    ),
    PureOccurrence("SAVE_NAME", (Instruction("SAVE_NAME", "shadowed"),)),
    PureOccurrence(
        "RESTORE_NAME",
        (Instruction("SAVE_NAME", "shadowed"), Instruction("RESTORE_NAME", "shadowed")),
        (),
        1,
    ),
    PureOccurrence("JUMP", (Instruction("JUMP", 1),)),
    PureOccurrence(
        "JUMP_IF_FALSE",
        (Instruction("LOAD_FALSE"), Instruction("JUMP_IF_FALSE", 2)),
        (),
        1,
    ),
    PureOccurrence(
        "JUMP_IF_TRUE",
        (Instruction("LOAD_TRUE"), Instruction("JUMP_IF_TRUE", 2)),
        (),
        1,
    ),
    PureOccurrence("MAKE_FUNCTION", (Instruction("MAKE_FUNCTION", "f", 0, 1),), ([],)),
    PureOccurrence("HALT", (Instruction("HALT"),)),
    _binary("ADD", 8, 2),
    _binary("SUB", 8, 2),
    _binary("MUL", 8, 2),
    _binary("DIV", 8, 2),
    _binary("MOD", 8, 3),
    _binary("EQ", 2, 2),
    _binary("NEQ", 2, 3),
    _binary("LT", 2, 3),
    _binary("GT", 3, 2),
    _binary("LTE", 2, 3),
    _binary("GTE", 3, 2),
    PureOccurrence(
        "AND",
        (Instruction("LOAD_TRUE"), Instruction("LOAD_FALSE"), Instruction("AND")),
        (),
        2,
    ),
    PureOccurrence(
        "OR",
        (Instruction("LOAD_FALSE"), Instruction("LOAD_TRUE"), Instruction("OR")),
        (),
        2,
    ),
    PureOccurrence("NOT", (Instruction("LOAD_FALSE"), Instruction("NOT")), (), 1),
    PureOccurrence(
        "UNARY_NEG", (Instruction("LOAD_CONST", 0), Instruction("UNARY_NEG")), (3,), 1
    ),
    PureOccurrence(
        "BUILD_LIST",
        (Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction("BUILD_LIST", 2)),
        ("a", "b"),
        2,
    ),
    PureOccurrence(
        "BUILD_DICT",
        (Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction("BUILD_DICT", 1)),
        ("answer", 42),
        2,
    ),
    PureOccurrence(
        "INDEX",
        (Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction("INDEX")),
        ([4], 0),
        2,
    ),
    PureOccurrence(
        "MEMBER",
        (Instruction("LOAD_CONST", 0), Instruction("MEMBER", "answer")),
        ({"answer": 42},),
        1,
    ),
    PureOccurrence(
        "PROMPT_BUILD",
        (Instruction("LOAD_CONST", 0), Instruction("PROMPT_BUILD", "template:v1", ["q"])),
        ("what is replay?",),
        1,
    ),
    PureOccurrence("GUARD_ENTER", (Instruction("GUARD_ENTER", "g", "p", "h"),)),
    PureOccurrence(
        "GUARD_EXIT",
        (Instruction("GUARD_ENTER", "g", "p", "h"), Instruction("GUARD_EXIT", "PASS")),
        (),
        1,
    ),
    PureOccurrence(
        "GUARD_CHECK_RESULT",
        (Instruction("LOAD_TRUE"), Instruction("GUARD_CHECK_RESULT")),
        (),
        1,
    ),
    PureOccurrence("GUARD_VIOLATION_ACK", (Instruction("GUARD_VIOLATION_ACK"),)),
    PureOccurrence("RECEIVE_ENTER", (Instruction("RECEIVE_ENTER"),)),
    PureOccurrence("RECEIVE_EXIT", (Instruction("RECEIVE_EXIT"),)),
)


@pytest.mark.parametrize("occurrence", PURE_OCCURRENCES, ids=lambda item: item.opcode)
def test_admissible_occurrence_executes_without_activity_or_host_effect(
    occurrence: PureOccurrence,
) -> None:
    instructions = list(copy.deepcopy(occurrence.instructions))
    if instructions[-1].op != "HALT":
        instructions.append(Instruction("HALT"))
    program = BytecodeProgram(instructions=instructions, constants=list(copy.deepcopy(occurrence.constants)))
    channel = channel_for(budget=4)
    machine = _build(program)
    machine.attach_channel(channel)

    for _ in range(occurrence.target_ip):
        machine.step()
    assert machine.next_opcode() == occurrence.opcode
    machine.step()

    assert channel.consumed_identities() == ()
    assert _activity_sequence(machine) == 0


@dataclass(frozen=True)
class RecordedOccurrence:
    opcode: str
    kind: ACT.ActivityKind


DIRECT_RECORDED_OCCURRENCES = (
    RecordedOccurrence("LLM_EVAL", ACT.ActivityKind.LLM_CALL),
    RecordedOccurrence("DREAM", ACT.ActivityKind.LLM_CALL),
    RecordedOccurrence("IMPRINT", ACT.ActivityKind.MEMORY_WRITE),
    RecordedOccurrence("RECALL", ACT.ActivityKind.MEMORY_READ),
    RecordedOccurrence("AFFECT_EVENT", ACT.ActivityKind.AFFECT_EVENT),
    RecordedOccurrence("AFFECT_STATE", ACT.ActivityKind.AFFECT_READ),
    RecordedOccurrence("METRICS", ACT.ActivityKind.METRICS_EMIT),
    RecordedOccurrence("HOST_EVAL", ACT.ActivityKind.HOST_DISPATCH),
    RecordedOccurrence("FRACTURE_SELF", ACT.ActivityKind.SELF_MODIFICATION),
    RecordedOccurrence("HABIT_SUGGEST", ACT.ActivityKind.HABIT_SUGGESTION),
    RecordedOccurrence("THRESHOLD_CHECK", ACT.ActivityKind.THRESHOLD_EVALUATION),
    RecordedOccurrence("SEND", ACT.ActivityKind.MESSAGE_SEND),
    RecordedOccurrence("RECEIVE", ACT.ActivityKind.MESSAGE_RECEIVE),
)


@pytest.mark.parametrize(
    "occurrence", DIRECT_RECORDED_OCCURRENCES, ids=lambda item: item.opcode
)
def test_recorded_occurrence_injects_exactly_one_governed_result(
    occurrence: RecordedOccurrence,
) -> None:
    operand_a = {"request": occurrence.opcode}
    operand_b = {"mode": "recorded"}
    result = {"source": "durable-record", "opcode": occurrence.opcode}
    program = BytecodeProgram(
        instructions=[Instruction(occurrence.opcode, operand_a, operand_b), Instruction("HALT")]
    )
    activity = _recorded_activity(
        kind=occurrence.kind,
        inputs=ACT.activity_inputs(
            opcode=occurrence.opcode.encode("utf-8"),
            operand_a=_machine_bytes(operand_a),
            operand_b=_machine_bytes(operand_b),
        ),
        program=program,
        instruction_pointer=0,
        result=result,
    )
    channel = channel_for(activity, budget=4)
    machine = _build(program)
    machine.attach_channel(channel)

    machine.step()

    assert channel.consumed_identities() == (activity.activity_identity,)
    assert _activity_sequence(machine) == 1
    assert _snapshot(machine)["machine"]["state"]["stack"][-1] == result


def test_direct_recorded_ledger_miss_does_not_move_machine_or_sequence() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LLM_EVAL", "missing", None), Instruction("HALT")]
    )
    channel = channel_for(budget=4)
    machine = _build(program)
    machine.attach_channel(channel)
    before = machine.snapshot_bytes()

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED
    assert machine.snapshot_bytes() == before
    assert _activity_sequence(machine) == 0
    assert channel.consumed_identities() == ()


@pytest.mark.parametrize(
    "value",
    (
        "\ud800",
        "e\u0301",
        float("nan"),
        float("inf"),
        float("-inf"),
        -0.0,
        2**53,
        -(2**53),
        {"e\u0301": "noncanonical key"},
        {1: "non-string key"},
    ),
    ids=(
        "lone-surrogate",
        "decomposed-nfc",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "negative-zero",
        "positive-unsafe-integer",
        "negative-unsafe-integer",
        "decomposed-mapping-key",
        "non-string-mapping-key",
    ),
)
def test_recorded_result_encoder_refuses_noncanonical_vm_value(value: object) -> None:
    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.encode_recorded_result(value)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE


@pytest.mark.parametrize(
    "value",
    (FunctionObject("not-activity-data", [], 0), ("tuple-is-not-json-data",)),
    ids=("function-object", "tuple"),
)
def test_recorded_result_encoder_refuses_non_data_vm_values(value: object) -> None:
    with pytest.raises(R.ReplayViolation) as excinfo:
        RVC.encode_recorded_result(value)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE


def test_recorded_result_codec_round_trips_exact_nested_activity_data() -> None:
    value = {"answer": [None, True, 7, 0.25, {"text": "caf\u00e9"}]}
    encoded = RVC.encode_recorded_result(value)

    assert RVC.decode_recorded_result(encoded) == value
    assert RVC.encode_recorded_result(RVC.decode_recorded_result(encoded)) == encoded


def test_reserved_vm_tag_record_is_resolved_but_never_injected() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("LLM_EVAL", "reserved-tag", None),
            Instruction("CALL", 0),
            Instruction("HALT"),
        ]
    )
    raw = (
        b'{"__vm_type__":"FunctionObject","body_ip":0,"closure":{},'
        b'"name":"forged","params":[],"program_hash":null}'
    )
    activity = governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_EVAL",
            operand_a=_machine_bytes("reserved-tag"),
            operand_b=_machine_bytes(None),
        ),
        position=ACT.ActivityPosition(program.program_hash, 0, 0, 1),
        result=raw,
    )
    channel = channel_for(activity, budget=4)
    machine = _build(program)
    machine.attach_channel(channel)
    before = _snapshot(machine)["machine"]

    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE
    assert _snapshot(machine)["machine"] == before
    assert channel.consumed_identities() == (activity.activity_identity,)
    restored = FACTORY.restore(
        machine.snapshot_bytes(), gas_budget=GAS, execution_context=CONTEXT
    )
    assert restored.next_opcode() == "LLM_EVAL"


def test_call_host_injects_one_host_dispatch_result() -> None:
    argument = {"question": "recorded only"}
    result = {"answer": "offline"}
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("CALL_HOST", "external.answer", 1),
            Instruction("HALT"),
        ],
        constants=[argument],
    )
    activity = _recorded_activity(
        kind=ACT.ActivityKind.HOST_DISPATCH,
        inputs=ACT.activity_inputs(
            opcode=b"CALL_HOST",
            operand_a=_machine_bytes(
                {"symbol": "external.answer", "args": [argument]}
            ),
            operand_b=_machine_bytes(None),
        ),
        program=program,
        instruction_pointer=1,
        result=result,
    )
    channel = channel_for(activity, budget=4)
    machine = _build(program)
    machine.attach_channel(channel)

    machine.step()
    machine.step()

    assert channel.consumed_identities() == (activity.activity_identity,)
    assert _activity_sequence(machine) == 1
    assert _snapshot(machine)["machine"]["state"]["stack"] == [result]


def test_llm_request_pauses_then_resumes_from_one_exact_record() -> None:
    prompt = {
        "type": "prompt_envelope",
        "template_hash": "template:od10/v1",
        "variables": {"q": "why replay?"},
        "variables_hash": hashlib.sha256(b"why replay?").hexdigest(),
    }
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("LLM_REQUEST", "schema:answer/v1", {"temperature": 0}, "model_change"),
            Instruction("HALT"),
        ],
        constants=[prompt],
    )
    initial_channel = channel_for(budget=4)
    paused = _build(program)
    paused.attach_channel(initial_channel)
    paused.step()
    paused.step()
    paused_bytes = paused.snapshot_bytes()
    paused_payload = _snapshot(paused)
    pending = paused_payload["machine"]["state"]["pending_host_call"]
    arguments = decode_vm_value(pending["args"])

    assert initial_channel.consumed_identities() == ()
    assert paused_payload["activity_sequence"] == 0
    assert paused.next_opcode() == "LLM_RESUME"

    result = {"answer": "recorded LLM response"}
    activity = _recorded_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_REQUEST",
            symbol=b"llm.request",
            arguments=_machine_bytes(arguments),
            argc=_machine_bytes(4),
            call_id=pending["call_id"].encode("utf-8"),
            created_at_event_id=pending["created_at_event_id"].encode("utf-8"),
        ),
        program=program,
        instruction_pointer=1,
        result=result,
    )
    channel = channel_for(activity, budget=4)
    resumed = FACTORY.restore(paused_bytes, gas_budget=GAS, execution_context=CONTEXT)
    resumed.attach_channel(channel)
    resumed.step()

    state = _snapshot(resumed)["machine"]["state"]
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert _activity_sequence(resumed) == 1
    assert state["pending_host_call"] is None
    assert state["ip"] == 2
    assert state["stack"] == [result]


def test_physical_llm_resume_is_refused_before_machine_mutation() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LLM_RESUME"), Instruction("HALT")]
    )
    channel = channel_for(budget=4)
    machine = _build(program)
    machine.attach_channel(channel)
    before = machine.snapshot_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.INJECTION_PRIMITIVE_MISSING
    assert machine.snapshot_bytes() == before
    assert channel.consumed_identities() == ()


def test_factory_restore_maps_deep_json_to_typed_snapshot_refusal() -> None:
    raw = b"[" * 2_000 + b"0" + b"]" * 2_000
    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(raw, gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


def test_factory_restore_maps_lone_surrogate_snapshot_field_to_typed_refusal() -> None:
    machine = _build(BytecodeProgram(instructions=[Instruction("HALT")]))
    payload = _snapshot(machine)
    payload["schema_version"] = "\ud800"
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")

    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(raw, gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ip", float("inf")),
        ("gas_remaining", float("inf")),
        ("call_stack", [1]),
        ("name_save_stack", [1]),
        ("guard_stack", [1]),
        ("transition_hash", "forged-transition"),
        ("activity_sequence", 2**53),
    ),
    ids=("ip-infinity", "gas-infinity", "call-frame", "name-save", "guard-frame", "transition", "sequence"),
)
def test_factory_restore_refuses_exact_shaped_invalid_state_without_raw_error(
    field: str,
    value: object,
) -> None:
    payload = _snapshot(_build(BytecodeProgram(instructions=[Instruction("HALT")])))
    if field == "activity_sequence":
        payload[field] = value
    else:
        payload["machine"]["state"][field] = value
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(raw, gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("environment_profile_id", "\ud800"),
        ("environment_profile_id", "e\u0301"),
        ("policy_version", "\ud800"),
        ("policy_version", "e\u0301"),
    ),
    ids=("env-surrogate", "env-decomposed", "policy-surrogate", "policy-decomposed"),
)
def test_execution_context_refuses_noncanonical_identity_before_machine_step(
    field: str,
    value: str,
) -> None:
    arguments = {
        "run_id": RunId("unstarted-context-run"),
        "attempt_id": AttemptId("unstarted-context-attempt"),
        "repository_revision": RepositoryRevision.git_commit("d" * 40),
        "environment_profile_id": "canonical-environment",
        "policy_version": "canonical-policy",
    }
    arguments[field] = value

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.replay_machine_execution_context(**arguments)

    assert excinfo.value.failure_code is R.ReplayFailureCode.MALFORMED_IDENTIFIER


def test_msg_send_resolves_message_send_before_outbound_commit() -> None:
    target = "actor:receiver"
    payload = {"body": "hello"}
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("LOAD_CONST", 1),
            Instruction("MSG_SEND", "ping"),
            Instruction("HALT"),
        ],
        constants=[target, payload],
    )
    payload_hash = "sha256:" + hashlib.sha256(_machine_bytes(payload)).hexdigest()
    message = {
        "msg_type": "ping",
        "method": "ping",
        "sender_id": "default_agent",
        "sender": "default_agent",
        "target_id": target,
        "receiver": target,
        "payload": payload,
        "payload_hash": payload_hash,
    }
    acknowledgment = {"status": "recorded-send"}
    activity = _recorded_activity(
        kind=ACT.ActivityKind.MESSAGE_SEND,
        inputs=ACT.activity_inputs(
            opcode=b"MSG_SEND",
            operand_a=_machine_bytes(
                {"symbol": "SYS_MSG_SEND", "args": [message]}
            ),
            operand_b=_machine_bytes(None),
        ),
        program=program,
        instruction_pointer=2,
        result=acknowledgment,
    )
    channel = channel_for(activity, budget=4)
    machine = _build(program)
    machine.attach_channel(channel)
    machine.step()
    machine.step()
    machine.step()

    state = _snapshot(machine)["machine"]["state"]
    assert activity.kind is ACT.ActivityKind.MESSAGE_SEND
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert state["mailbox_outbound"] == [message]
    assert state["stack"] == [acknowledgment]


def test_empty_mailbox_receive_pauses_then_resumes_from_message_record() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("MSG_RECEIVE", "sender", "message"), Instruction("HALT")]
    )
    initial_channel = channel_for(budget=4)
    paused = _build(program)
    paused.attach_channel(initial_channel)
    paused.step()
    paused_bytes = paused.snapshot_bytes()
    paused_payload = _snapshot(paused)
    pending = paused_payload["machine"]["state"]["pending_message_receive"]

    assert initial_channel.consumed_identities() == ()
    assert paused_payload["activity_sequence"] == 0
    assert paused.next_opcode() == "MSG_RECEIVE"

    message = {
        "msg_type": "ping",
        "sender_id": "actor:sender",
        "payload": {"body": "delivered from history"},
    }
    activity = _recorded_activity(
        kind=ACT.ActivityKind.MESSAGE_RECEIVE,
        inputs=ACT.activity_inputs(
            opcode=b"MSG_RECEIVE",
            receiver_id=pending["receiver_id"].encode("utf-8"),
            sender_var=pending["sender_var"].encode("utf-8"),
            target_var=pending["target_var"].encode("utf-8"),
            message_receive_id=pending["message_receive_id"].encode("utf-8"),
            created_at_event_id=pending["created_at_event_id"].encode("utf-8"),
        ),
        program=program,
        instruction_pointer=0,
        result=message,
    )
    channel = channel_for(activity, budget=4)
    resumed = FACTORY.restore(paused_bytes, gas_budget=GAS, execution_context=CONTEXT)
    resumed.attach_channel(channel)
    resumed.step()

    state = _snapshot(resumed)["machine"]["state"]
    assert channel.consumed_identities() == (activity.activity_identity,)
    assert _activity_sequence(resumed) == 1
    assert state["pending_message_receive"] is None
    assert state["locals"]["sender"] == "actor:sender"
    assert state["locals"]["message"] == message
    assert state["stack"] == [message]


def _attach_empty(program: BytecodeProgram, *, state: VMState | None = None):
    machine = RVM.CognitiveVMReplayAdapter(
        program,
        gas_budget=GAS,
        execution_context=CONTEXT,
        _state=state,
    )
    channel = channel_for(budget=4)
    machine.attach_channel(channel)
    return machine, channel


def test_call_internal_function_consumes_no_activity() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "inner", 0, 3),
            Instruction("CALL", 0),
            Instruction("HALT"),
            Instruction("LOAD_CONST", 1),
            Instruction("RETURN"),
        ],
        constants=[[], "done"],
    )
    machine, channel = _attach_empty(program)
    machine.step()
    machine.step()

    assert machine.next_opcode() == "LOAD_CONST"
    assert len(_snapshot(machine)["machine"]["state"]["call_stack"]) == 1
    assert channel.consumed_identities() == ()
    assert _activity_sequence(machine) == 0


def test_make_function_snapshot_restores_to_internal_call_without_activity() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "inner", 0, 3),
            Instruction("CALL", 0),
            Instruction("HALT"),
            Instruction("LOAD_CONST", 1),
            Instruction("RETURN"),
        ],
        constants=[[], "done"],
    )
    captured = _build(program)
    captured.step()
    restored = FACTORY.restore(
        captured.snapshot_bytes(), gas_budget=GAS, execution_context=CONTEXT
    )
    channel = channel_for(budget=4)
    restored.attach_channel(channel)
    restored.step()

    assert restored.next_opcode() == "LOAD_CONST"
    assert len(_snapshot(restored)["machine"]["state"]["call_stack"]) == 1
    assert channel.consumed_identities() == ()
    assert _activity_sequence(restored) == 0


def test_factory_restore_refuses_function_not_produced_by_admitted_program() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("MAKE_FUNCTION", "inner", 0, 2), Instruction("HALT"), Instruction("RETURN")],
        constants=[[]],
    )
    machine = _build(program)
    machine.step()
    payload = _snapshot(machine)
    payload["machine"]["state"]["stack"][0]["name"] = "forged"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(R.ReplayViolation) as excinfo:
        FACTORY.restore(raw, gas_budget=GAS, execution_context=CONTEXT)
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE


def test_call_method_internal_dict_member_consumes_no_activity() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("CALL_METHOD", "answer", 0),
            Instruction("HALT"),
        ],
        constants=[{"answer": 42}],
    )
    machine, channel = _attach_empty(program)
    machine.step()
    machine.step()

    assert _snapshot(machine)["machine"]["state"]["stack"] == [42]
    assert channel.consumed_identities() == ()
    assert _activity_sequence(machine) == 0


@pytest.mark.parametrize("opcode", ("CALL", "CALL_METHOD"))
def test_guarded_host_fallback_resolves_one_host_dispatch(opcode: str) -> None:
    subject = {"known": "value"} if opcode == "CALL_METHOD" else "remote-callable"
    instruction = (
        Instruction("CALL_METHOD", "missing", 0)
        if opcode == "CALL_METHOD"
        else Instruction("CALL", 0)
    )
    program = BytecodeProgram(
        instructions=[Instruction("LOAD_CONST", 0), instruction, Instruction("HALT")],
        constants=[subject],
    )
    operand_a = f"{subject}.missing" if opcode == "CALL_METHOD" else subject
    result = {"route": opcode, "source": "record"}
    activity = _recorded_activity(
        kind=ACT.ActivityKind.HOST_DISPATCH,
        inputs=ACT.activity_inputs(
            opcode=b"HOST_EVAL",
            operand_a=_machine_bytes(operand_a),
            operand_b=_machine_bytes([]),
        ),
        program=program,
        instruction_pointer=1,
        result=result,
    )
    channel = channel_for(activity, budget=4)
    machine = _build(program)
    machine.attach_channel(channel)
    machine.step()
    machine.step()

    assert channel.consumed_identities() == (activity.activity_identity,)
    assert _activity_sequence(machine) == 1
    assert _snapshot(machine)["machine"]["state"]["stack"] == [result]


@pytest.mark.parametrize("member", ("upper", "__class__"))
def test_guarded_python_descriptor_is_refused_before_dispatch(member: str) -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("CALL_METHOD", member, 0),
            Instruction("HALT"),
        ],
        constants=["do not execute me"],
    )
    machine, channel = _attach_empty(program)
    machine.step()
    before = machine.snapshot_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert machine.snapshot_bytes() == before
    assert channel.consumed_identities() == ()


def test_member_bound_method_is_refused_before_machine_or_channel_mutation() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LOAD_CONST", 0), Instruction("MEMBER", "upper"), Instruction("HALT")],
        constants=["hello"],
    )
    machine, channel = _attach_empty(program)
    machine.step()
    before = machine.snapshot_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()

    assert excinfo.value.failure_code is R.ReplayFailureCode.UNGOVERNED_DISPATCH
    assert machine.snapshot_bytes() == before
    assert channel.consumed_identities() == ()


def _driver_run(
    program: BytecodeProgram,
    *,
    structural_history: bytes | None = None,
    activities: tuple[ACT.RecordedActivity, ...] = (),
):
    machine = _build(program, expected_structural_history=structural_history)
    channel = channel_for(*activities, budget=4)
    machine.attach_channel(channel)
    return R._drive_one_behavior(
        binding=R.ReplayProgramBinding(
            "od10-driver-subject",
            program.program_hash,
            program.host_abi_version,
            "od10-artifact-resolver",
            program.version,
            contract_for(()),
        ),
        machine=machine,
        channel=channel,
        gas_budget=GAS,
        step_limit=20,
    )


@pytest.mark.parametrize(
    ("program", "steps"),
    (
        (BytecodeProgram(instructions=[Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction("ADD"), Instruction("HALT")], constants=[1e308, 1e308]), 2),
        (BytecodeProgram(instructions=[Instruction("LOAD_CONST", 0), Instruction("UNARY_NEG"), Instruction("HALT")], constants=[0.0]), 1),
        (BytecodeProgram(instructions=[Instruction("LOAD_CONST", 0), Instruction("LOAD_CONST", 1), Instruction("BUILD_DICT", 1), Instruction("HALT")], constants=[1, "value"]), 2),
    ),
    ids=("add-overflow", "negative-zero", "non-string-dict-key"),
)
def test_noncanonical_successor_rolls_back_and_is_a_durable_transition_failure(
    program: BytecodeProgram,
    steps: int,
) -> None:
    machine, channel = _attach_empty(program)
    for _ in range(steps):
        machine.step()
    before = machine.snapshot_bytes()

    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.NON_CANONICAL_VM_VALUE
    assert machine.snapshot_bytes() == before
    assert channel.consumed_identities() == ()

    run = _driver_run(program)
    assert run.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert R.status_for_reason(run.failure_reason) is R.ReplayStatus.REPLAY_FAILED


def test_driver_maps_determinism_refusals_to_replay_failures() -> None:
    recorded = BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", "recorded"), Instruction("HALT")]
    )
    captured, _channel = _attach_empty(recorded)
    _run_to_stop(captured)
    divergent = BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", "divergent"), Instruction("HALT")]
    )
    structural = _driver_run(
        divergent, structural_history=captured.structural_history_bytes()
    )
    physical_resume = _driver_run(
        BytecodeProgram(instructions=[Instruction("LLM_RESUME"), Instruction("HALT")])
    )
    malformed_structural = _driver_run(BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", " padded "), Instruction("HALT")]))

    assert structural.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert physical_resume.failure_reason is R.ReplayFailureReason.FORBIDDEN_HOST_CALL
    assert malformed_structural.failure_reason is R.ReplayFailureReason.TRANSITION_MISMATCH
    assert R.status_for_reason(structural.failure_reason) is R.ReplayStatus.REPLAY_FAILED
    assert R.status_for_reason(physical_resume.failure_reason) is R.ReplayStatus.REPLAY_FAILED


def test_structural_command_mismatch_is_refused_before_the_vm_transition() -> None:
    program = BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", "recorded"), Instruction("HALT")]
    )
    captured, _ = _attach_empty(program)
    captured.step()
    command, = RSH.decode_replay_structural_history(captured.structural_history_bytes())
    mismatched = RSH.encode_replay_structural_history(
        (replace(command, label="different"),),
        profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        profile_digest=R.capability_profile_digest(),
    )
    machine = _build(program, expected_structural_history=mismatched)
    channel = channel_for(budget=4); machine.attach_channel(channel)
    before = machine.snapshot_bytes()
    with pytest.raises(R.ReplayViolation) as excinfo:
        machine.step()
    assert excinfo.value.failure_code is R.ReplayFailureCode.STRUCTURAL_HISTORY_MISMATCH
    assert machine.snapshot_bytes() == before and channel.consumed_identities() == ()


def test_second_real_cvm_host_route_is_cardinality_failure_and_replay_failure(
    monkeypatch,
) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LLM_EVAL", "twice", None), Instruction("HALT")]
    )
    activity = _recorded_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_EVAL",
            operand_a=_machine_bytes("twice"),
            operand_b=_machine_bytes(None),
        ),
        program=program,
        instruction_pointer=0,
        result="recorded once",
    )
    original_step = CognitiveVM.step

    def twice(vm):
        instruction = vm.program.instructions[vm.state.ip]
        result = original_step(vm)
        if instruction.op == "LLM_EVAL":
            vm.host(instruction.op, instruction.a, instruction.b)
        return result

    monkeypatch.setattr(CognitiveVM, "step", twice)
    direct = _build(program)
    direct.attach_channel(channel_for(activity, budget=4))
    with pytest.raises(R.ReplayViolation) as excinfo:
        direct.step()
    assert (
        excinfo.value.failure_code
        is R.ReplayFailureCode.ACTIVITY_CARDINALITY_MISMATCH
    )

    run = _driver_run(program, activities=(activity,))
    assert run.failure_reason is R.ReplayFailureReason.ACTIVITY_HISTORY_MISMATCH
    assert R.status_for_reason(run.failure_reason) is R.ReplayStatus.REPLAY_FAILED


@pytest.mark.parametrize(
    "raw",
    (
        b'{ "answer": "noncanonical transport" }',
        b"[" * 2_000 + b"0" + b"]" * 2_000,
        b'"\\ud800"',
        b'"e\xcc\x81"',
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"-0.0",
        b"9007199254740992",
        b"-9007199254740992",
        b'{"e\\u0301":"noncanonical key"}',
        (
            b'{"__vm_type__":"FunctionObject","body_ip":0,"closure":{},'
            b'"name":"forged","params":[],"program_hash":null}'
        ),
    ),
    ids=(
        "noncanonical-transport",
        "deep-json",
        "lone-surrogate",
        "decomposed-nfc",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "negative-zero",
        "positive-unsafe-integer",
        "negative-unsafe-integer",
        "decomposed-mapping-key",
        "reserved-vm-tag",
    ),
)
def test_driver_maps_noncanonical_recorded_result_to_activity_substitution(
    raw: bytes,
) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("LLM_EVAL", "decode", None), Instruction("HALT")]
    )
    activity = governed_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(
            opcode=b"LLM_EVAL",
            operand_a=_machine_bytes("decode"),
            operand_b=_machine_bytes(None),
        ),
        position=ACT.ActivityPosition(program.program_hash, 0, 0, 1),
        result=raw,
    )

    run = _driver_run(program, activities=(activity,))

    assert run.failure_reason is R.ReplayFailureReason.ACTIVITY_SUBSTITUTED
    assert R.status_for_reason(run.failure_reason) is R.ReplayStatus.REPLAY_FAILED


def _capture_and_replay_structural(program: BytecodeProgram):
    capture_channel = channel_for(budget=4)
    captured = _build(program)
    captured.attach_channel(capture_channel)
    _run_to_stop(captured)
    history = captured.structural_history_bytes()

    replay_channel = channel_for(budget=4)
    replayed = _build(program, expected_structural_history=history)
    replayed.attach_channel(replay_channel)
    _run_to_stop(replayed)

    assert capture_channel.consumed_identities() == ()
    assert replay_channel.consumed_identities() == ()
    assert _activity_sequence(captured) == 0
    assert _activity_sequence(replayed) == 0
    assert replayed.structural_history_complete()
    assert replayed.structural_history_bytes() == history
    return RSH.decode_replay_structural_history(history)


def test_eight_structural_opcodes_capture_and_exact_replay_without_activities() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("CONTEXT_ENTER", "context:one"),
            Instruction("CONTEXT_EXIT", "context:one"),
            Instruction("ACTOR_ENTER", "actor:one", {"role": "worker"}),
            Instruction("ACTOR_EXIT", "actor:one"),
            Instruction("POLICY_ENTER", "policy:one", {"version": "1"}),
            Instruction("POLICY_RULE_ENTER", "policy:one:rule:allow", {"effect": "allow"}),
            Instruction("POLICY_RULE_EXIT", "policy:one:rule:allow"),
            Instruction("POLICY_EXIT", "policy:one"),
            Instruction("HALT"),
        ]
    )

    commands = _capture_and_replay_structural(program)

    assert tuple(command.opcode for command in commands) == (
        "CONTEXT_ENTER",
        "CONTEXT_EXIT",
        "ACTOR_ENTER",
        "ACTOR_EXIT",
        "POLICY_ENTER",
        "POLICY_RULE_ENTER",
        "POLICY_RULE_EXIT",
        "POLICY_EXIT",
    )


@pytest.mark.parametrize("inside_frame", (False, True), ids=("top-level", "frame"))
def test_return_without_dangling_scope_records_no_structural_command(
    inside_frame: bool,
) -> None:
    if inside_frame:
        program = BytecodeProgram(
            instructions=[
                Instruction("MAKE_FUNCTION", "plain", 0, 3),
                Instruction("CALL", 0),
                Instruction("HALT"),
                Instruction("LOAD_CONST", 1),
                Instruction("RETURN"),
            ],
            constants=[[], "done"],
        )
    else:
        program = BytecodeProgram(
            instructions=[Instruction("LOAD_CONST", 0), Instruction("RETURN")],
            constants=["done"],
        )

    commands = _capture_and_replay_structural(program)

    assert commands == ()


def test_return_records_one_atomic_inner_to_outer_unwind_batch() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("MAKE_FUNCTION", "scoped", 0, 3),
            Instruction("CALL", 0),
            Instruction("HALT"),
            Instruction("CONTEXT_ENTER", "context:outer"),
            Instruction("CONTEXT_ENTER", "context:inner"),
            Instruction("ACTOR_ENTER", "actor:outer", {}),
            Instruction("ACTOR_ENTER", "actor:inner", {}),
            Instruction("POLICY_ENTER", "policy:one", {}),
            Instruction("POLICY_RULE_ENTER", "policy:one:rule:outer", {}),
            Instruction("POLICY_RULE_ENTER", "policy:one:rule:inner", {}),
            Instruction("LOAD_CONST", 1),
            Instruction("RETURN"),
        ],
        constants=[[], "returned"],
    )

    commands = _capture_and_replay_structural(program)
    unwind = tuple(command for command in commands if command.opcode == "RETURN")

    assert tuple((command.scope_kind, command.label) for command in unwind) == (
        ("context", "context:inner"),
        ("context", "context:outer"),
        ("actor", "actor:inner"),
        ("actor", "actor:outer"),
        ("policy_rule", "policy:one:rule:inner"),
        ("policy_rule", "policy:one:rule:outer"),
        ("policy", "policy:one"),
    )
    assert all(command.frame_depth == 1 for command in unwind)
    assert all(command.unwind_reason == "function_return" for command in unwind)
    assert tuple(command.occurrence_index for command in unwind) == tuple(range(len(unwind)))
    assert {command.occurrence_size for command in unwind} == {len(unwind)}


def test_structural_history_crosses_replay_store_cas_and_restart_fail_closed(
    tmp_path: Path,
) -> None:
    program = BytecodeProgram(
        instructions=[Instruction("CONTEXT_ENTER", "durable"), Instruction("HALT")]
    )
    machine, _channel = _attach_empty(program)
    _run_to_stop(machine)
    raw = machine.structural_history_bytes()
    root = tmp_path / "replay-store"
    fence = fence_for(tmp_path)
    store = R_STORE.FileReplayStore(root, mutation_fence=fence)
    with store_transaction(fence) as ticket:
        with pytest.raises(R_STORE.ReplayStoreViolation) as excinfo:
            store.put_structural_history(b"{}", ticket=ticket)
        assert excinfo.value.failure_code is R_STORE.ReplayStoreFailureCode.TYPE_MISMATCH
    with store_transaction(fence) as ticket:
        reference = store.put_structural_history(raw, ticket=ticket)

    reopened = R_STORE.FileReplayStore(root, mutation_fence=fence)
    assert reopened.open_structural_history(reference) == raw

    path = (
        root / R_STORE.STRUCTURAL_HISTORY_DIRECTORY_V1
        / reference.sha256[:2] / reference.sha256
    )
    changed = bytearray(raw)
    changed[-2] = changed[-2] ^ 1
    path.write_bytes(bytes(changed))
    with pytest.raises(R_STORE.ReplayStoreViolation) as excinfo:
        reopened.open_structural_history(reference)
    assert (
        excinfo.value.failure_code
        is R_STORE.ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED
    )


def _structural_command(**changes: object) -> RSH.ReplayStructuralCommand:
    fields: dict[str, object] = {
        "profile_id": R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        "profile_digest": R.capability_profile_digest(),
        "program_hash": "c" * 64,
        "instruction_pointer": 0,
        "frame_depth": 0,
        "pre_transition_hash": "sha256:genesis",
        "occurrence_index": 0,
        "occurrence_size": 1,
        "opcode": "CONTEXT_ENTER",
        "sys_symbol": "SYS_CONTEXT_ENTER",
        "scope_kind": "context",
        "label": "context",
        "metadata_digest": hashlib.sha256(b"{}").hexdigest(),
        "direction": "enter",
        "unwind_reason": None,
        "host_abi_version": "2.2",
    }
    fields.update(changes)
    return RSH.ReplayStructuralCommand(**fields)


@pytest.mark.parametrize(
    "changes",
    (
        {"opcode": []},
        {"label": "\ud800"},
    ),
    ids=("unhashable-opcode", "lone-surrogate"),
)
def test_structural_decode_is_typed_for_malformed_valid_json(changes: dict) -> None:
    valid = RSH.encode_replay_structural_history(
        (_structural_command(),),
        profile_id=R.REPLAY_CAPABILITY_PROFILE_V1_E1,
        profile_digest=R.capability_profile_digest(),
    )
    payload = json.loads(valid.decode("utf-8"))
    payload["records"][0].update(changes)
    malformed = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")

    with pytest.raises(RSH.StructuralHistoryViolation):
        RSH.decode_replay_structural_history(malformed)


def test_structural_command_refuses_non_nfc_label_before_canonicalization() -> None:
    with pytest.raises(RSH.StructuralHistoryViolation) as excinfo:
        _structural_command(label="e\u0301")
    assert excinfo.value.failure_code is RSH.StructuralHistoryFailureCode.TYPE_MISMATCH


def test_policy_rule_marker_contradiction_is_refused_with_an_exact_rule_abi() -> None:
    with pytest.raises(RSH.StructuralHistoryViolation) as excinfo:
        _structural_command(
            opcode="POLICY_RULE_ENTER",
            sys_symbol="SYS_POLICY_RULE_ENTER",
            scope_kind="policy_rule",
            direction="enter",
            label="allow",
        )

    assert excinfo.value.failure_code is RSH.StructuralHistoryFailureCode.TYPE_MISMATCH
    assert excinfo.value.detail == "policy scope and label disagree"


def test_structural_decode_maps_deep_json_to_history_mismatch() -> None:
    raw = b"[" * 10_000 + b"0" + b"]" * 10_000
    with pytest.raises(RSH.StructuralHistoryViolation) as excinfo:
        RSH.decode_replay_structural_history(raw)
    assert (
        excinfo.value.failure_code
        is RSH.StructuralHistoryFailureCode.HISTORY_MISMATCH
    )


@pytest.mark.parametrize("field", ("instruction_pointer", "frame_depth"))
def test_structural_naturals_exclude_the_non_safe_integer_boundary(field: str) -> None:
    with pytest.raises(RSH.StructuralHistoryViolation) as excinfo:
        _structural_command(**{field: 2**53})
    assert excinfo.value.failure_code is RSH.StructuralHistoryFailureCode.TYPE_MISMATCH


def _artifact_subject(
    program: BytecodeProgram,
    declared_capabilities: tuple[str, ...],
):
    raw = json.dumps(
        program.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    reference = HashBoundRef(
        kind=RefKind.PROGRAM_ARTIFACT,
        ref_id=digest,
        schema_id=SchemaVersion.REPLAY_ARTIFACT_PROGRAM_V1.value,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )
    payload = copy.deepcopy(json.loads(_VECTORS.read_text(encoding="utf-8"))["vectors"][0]["core"])
    payload["canonical_program"] = {
        "form": "ARTIFACT_REF_V1",
        "artifact_ref": reference.to_dict(),
    }
    payload["artifact_refs"] = [reference.to_dict()]
    payload["capability_requirements"] = list(declared_capabilities)
    core = BehaviorCore.from_dict(payload)
    unit = create_behavior_unit(
        behavior_kind=core.behavior_kind,
        canonical_program=core.canonical_program,
        input_contract=core.input_contract,
        output_contract=core.output_contract,
        capability_requirements=core.capability_requirements,
        replay_contract=core.replay_contract,
        verification_contract=core.verification_contract,
        binding_refs=core.binding_refs,
        source_evidence_refs=core.source_evidence_refs,
        artifact_refs=core.artifact_refs,
    )

    class ExactArtifactResolver:
        def open_artifact(self, requested: HashBoundRef) -> bytes:
            assert requested == reference
            return raw

    return unit, ExactArtifactResolver()


def _capability_instruction(opcode: str) -> Instruction:
    if opcode == "MSG_SEND":
        return Instruction(opcode, "ping")
    if opcode == "MSG_RECEIVE":
        return Instruction(opcode, "sender", "message")
    return Instruction(opcode)


@pytest.mark.parametrize(
    "instruction",
    (
        Instruction("POLICY_RULE_ENTER", "policy:rule:label", {}),
        Instruction("POLICY_RULE_EXIT", "policy:rule:label"),
        Instruction("CONTEXT_ENTER", " padded "),
    ),
    ids=("POLICY_RULE_ENTER", "POLICY_RULE_EXIT", "CONTEXT_ENTER"),
)
def test_artifact_decoder_refuses_noncanonical_structural_label(
    instruction: Instruction,
) -> None:
    program = BytecodeProgram(instructions=[instruction, Instruction("HALT")])
    unit, resolver = _artifact_subject(program, ())

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resolve_artifact_program(unit, resolver=resolver)
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH
    cause = excinfo.value.__cause__
    assert type(cause) is CanonicalizationViolation
    assert cause.failure_code is CanonicalizationFailureCode.COMPILER_OUTPUT_MISMATCH


def test_reserved_vm_tag_is_refused_at_artifact_admission() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("LOAD_CONST", 0),
            Instruction("CALL", 0),
            Instruction("HALT"),
        ],
        constants=[{"__vm_type__": "FunctionObject", "name": "forged"}],
    )
    unit, resolver = _artifact_subject(program, ("capability.host",))

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resolve_artifact_program(unit, resolver=resolver)

    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH


@pytest.mark.parametrize(
    ("opcode", "capability"),
    (
        ("FRACTURE_SELF", "capability.self.modify"),
        ("HABIT_SUGGEST", "capability.habit.suggest"),
        ("THRESHOLD_CHECK", "capability.affect.threshold.evaluate"),
        ("MSG_SEND", "capability.message.send"),
        ("MSG_RECEIVE", "capability.message.receive"),
    ),
)
def test_program_artifact_resolves_only_with_approved_activity_capability(
    opcode: str,
    capability: str,
) -> None:
    program = BytecodeProgram(
        instructions=[_capability_instruction(opcode), Instruction("HALT")]
    )
    unit, resolver = _artifact_subject(program, (capability,))

    resolved, _binding = R.resolve_artifact_program(unit, resolver=resolver)

    assert resolved.program_hash == program.program_hash
    assert R.capabilities_required_by(resolved) == (capability,)


def test_program_artifact_rejects_both_missing_and_extra_capability_declaration() -> None:
    program = BytecodeProgram(
        instructions=[
            Instruction("FRACTURE_SELF"),
            Instruction("HABIT_SUGGEST"),
            Instruction("THRESHOLD_CHECK"),
            _capability_instruction("MSG_SEND"),
            _capability_instruction("MSG_RECEIVE"),
            Instruction("HALT"),
        ]
    )
    exact = R.capabilities_required_by(program)
    declarations = (exact[1:], exact + ("capability.unrelated",))

    for declared in declarations:
        unit, resolver = _artifact_subject(program, declared)
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.resolve_artifact_program(unit, resolver=resolver)
        assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED


@pytest.mark.parametrize(
    "instruction",
    (Instruction("CALL", 0), Instruction("CALL_METHOD", "member", 0)),
    ids=("CALL", "CALL_METHOD"),
)
def test_dispatch_guarded_artifact_requires_host_capability(
    instruction: Instruction,
) -> None:
    program = BytecodeProgram(instructions=[instruction, Instruction("HALT")])
    exact_unit, exact_resolver = _artifact_subject(program, ("capability.host",))
    missing_unit, missing_resolver = _artifact_subject(program, ())

    resolved, _binding = R.resolve_artifact_program(exact_unit, resolver=exact_resolver)
    assert R.capabilities_required_by(resolved) == ("capability.host",)

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.resolve_artifact_program(missing_unit, resolver=missing_resolver)
    assert excinfo.value.failure_code is R.ReplayFailureCode.ACTIVITY_NOT_GOVERNED


def test_missing_activity_capability_is_a_typed_profile_violation(monkeypatch) -> None:
    monkeypatch.delitem(R._CAPABILITY_BY_ACTIVITY_KIND, ACT.ActivityKind.SELF_MODIFICATION)
    program = BytecodeProgram(instructions=[Instruction("FRACTURE_SELF"), Instruction("HALT")])

    with pytest.raises(R.ReplayViolation) as excinfo:
        R.capabilities_required_by(program)

    assert excinfo.value.failure_code is R.ReplayFailureCode.CAPABILITY_NOT_CLASSIFIED


def test_capability_mapping_is_part_of_profile_digest(monkeypatch) -> None:
    before = R.capability_profile_digest()
    monkeypatch.setitem(R._CAPABILITY_BY_ACTIVITY_KIND, ACT.ActivityKind.MESSAGE_SEND,
                        "capability.message.send.changed")

    assert R.capability_profile_digest() != before
