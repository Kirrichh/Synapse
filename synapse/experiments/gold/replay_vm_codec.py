"""Canonical value, state, result, and snapshot codecs for the CVM replay adapter.

This is an internal cohesion component of ``replay_vm_adapter.py``.  It owns no
replay policy, activity routing, machine orchestration, or protected-core
integration; it only validates and serializes the adapter's frozen wire values.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import unicodedata

from synapse.bytecode import BytecodeProgram
from synapse.cvm import (
    CallFrame,
    FunctionObject,
    GuardFrame,
    PendingHostCall,
    VMState,
    VMSnapshotFormatError,
    encode_vm_value,
)

from .activities import ACTIVITY_RESULT_CODEC_V1_E1
from .replay import (
    MAX_MACHINE_SNAPSHOT_BYTES_V1_E1,
    MAX_SNAPSHOT_BYTES_V1_E1,
    REPLAY_MACHINE_ADAPTER_ID_V1_E1,
    ReplayFailureCode,
    _fail,
)


_SNAPSHOT_DIGEST_PROFILE = b"synapse.stage4.gold.replay-machine-port-e1/v1\x00"
_ADAPTER_SNAPSHOT_SCHEMA_V1_E1 = "synapse.stage4.gold.replay-vm-adapter-snapshot-e1/v1"
MAX_VM_VALUE_DEPTH = 64
MAX_VM_VALUE_NODES = 8192
MAX_SAFE_INTEGER = 2**53 - 1
CANONICAL_VM_SCALARS = (type(None), bool, int, float, str)
_FRAME_FIELDS = {
    FunctionObject: tuple(field.name for field in dataclasses.fields(FunctionObject)),
    CallFrame: tuple(field.name for field in dataclasses.fields(CallFrame)),
    GuardFrame: tuple(field.name for field in dataclasses.fields(GuardFrame)),
}
VM_STATE_FIELDS = frozenset(field.name for field in dataclasses.fields(VMState))


def _has_function_origin(
    program: BytecodeProgram,
    name: str,
    body_ip: int,
    params: list[str] | None = None,
) -> bool:
    for instruction in program.instructions:
        if (instruction.op, instruction.a, instruction.c) != (
            "MAKE_FUNCTION",
            name,
            body_ip,
        ):
            continue
        if params is None:
            return True
        index = instruction.b
        if index is None:
            declared = []
        elif type(index) is int and 0 <= index < len(program.constants):
            declared = program.constants[index]
        else:
            continue
        if type(declared) is list and declared == params:
            return True
    return False


def is_transition_id(value: object) -> bool:
    return type(value) is str and (
        value == "sha256:genesis"
        or (
            len(value) == 71
            and value.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in value[7:])
        )
    )


def require_canonical_vm_value(
    value: object,
    *,
    field: str = "value",
    program: BytecodeProgram | None = None,
    data_only: bool = False,
) -> None:
    budget = [MAX_VM_VALUE_NODES]

    def walk(node: object, depth: int) -> None:
        if depth > MAX_VM_VALUE_DEPTH:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} nests too deeply",
            )
        budget[0] -= 1
        if budget[0] < 0:
            raise _fail(
                ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
                f"{field} has too many values",
            )
        kind = type(node)
        if kind is int and not -MAX_SAFE_INTEGER <= node <= MAX_SAFE_INTEGER:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} integer is outside the safe range",
            )
        if kind is float and (
            not math.isfinite(node)
            or (node == 0.0 and math.copysign(1.0, node) < 0)
        ):
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} float is not canonical",
            )
        if kind is str and (
            any(0xD800 <= ord(char) <= 0xDFFF for char in node)
            or unicodedata.normalize("NFC", node) != node
        ):
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} text is not canonical Unicode",
            )
        if kind in CANONICAL_VM_SCALARS:
            return
        if kind is dict or (not data_only and kind is PendingHostCall):
            if "__vm_type__" in node:
                raise _fail(
                    ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                    f"{field} contains a reserved VM tag",
                )
            for key, item in node.items():
                if type(key) is not str:
                    raise _fail(
                        ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                        f"{field} has a non-text mapping key",
                    )
                walk(key, depth + 1)
                walk(item, depth + 1)
            return
        if kind is list:
            for item in node:
                walk(item, depth + 1)
            return
        if data_only:
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                f"{field} is not data-only",
            )
        if kind is tuple:
            for item in node:
                walk(item, depth + 1)
            return
        if kind is FunctionObject:
            if (
                set(vars(node)) != set(_FRAME_FIELDS[FunctionObject])
                or type(node.params) is not list
                or type(node.closure) is not dict
            ):
                raise _fail(
                    ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                    f"{field} function schema is not exact",
                )
            if any(type(item) is not str for item in node.params):
                raise _fail(
                    ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                    f"{field} function parameters are not exact",
                )
            if (
                type(program) is not BytecodeProgram
                or node.program_hash != program.program_hash
                or type(node.name) is not str
                or type(node.body_ip) is not int
                or not _has_function_origin(program, node.name, node.body_ip, node.params)
            ):
                raise _fail(
                    ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                    f"{field} function has no admitted origin",
                )
            for name in _FRAME_FIELDS[FunctionObject]:
                walk(getattr(node, name), depth + 1)
            return
        if kind is CallFrame or kind is GuardFrame:
            if set(vars(node)) != set(_FRAME_FIELDS[kind]):
                raise _fail(
                    ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                    f"{field} frame schema is not exact",
                )
            for frame_field in _FRAME_FIELDS[kind]:
                walk(getattr(node, frame_field), depth + 1)
            return
        raise _fail(
            ReplayFailureCode.NON_CANONICAL_VM_VALUE,
            f"{field} is not a canonical machine value",
        )

    walk(value, 0)


def require_canonical_vm_state(
    state: VMState,
    *,
    program: BytecodeProgram | None = None,
) -> VMState:
    if type(state) is not VMState or set(vars(state)) != VM_STATE_FIELDS:
        raise _fail(ReplayFailureCode.TYPE_MISMATCH, "VMState schema is not exact")
    for name in VM_STATE_FIELDS:
        require_canonical_vm_value(
            getattr(state, name),
            field=f"state.{name}",
            program=program,
        )

    def natural(name: str, optional: bool = False) -> None:
        item = getattr(state, name)
        if optional and item is None:
            return
        if type(item) is not int or not 0 <= item <= MAX_SAFE_INTEGER:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                f"state.{name} is not a safe natural",
            )

    natural("ip")
    natural("gas_remaining")
    natural("cognitive_budget_remaining", True)
    exact = {
        "stack": list,
        "locals": dict,
        "call_stack": list,
        "context_stack": list,
        "actor_stack": list,
        "policy_stack": list,
        "name_save_stack": list,
        "mailbox_inbound": list,
        "mailbox_outbound": list,
        "guard_stack": list,
    }
    if any(type(getattr(state, name)) is not kind for name, kind in exact.items()):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "VMState container schema is not exact",
        )
    if type(state.guard_violation_active) is not bool:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "guard violation flag is not exact",
        )
    for name in ("current_context", "agent_id"):
        if getattr(state, name) is not None and type(getattr(state, name)) is not str:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                f"state.{name} is not optional text",
            )
    if not is_transition_id(state.transition_hash):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "transition hash is not exact",
        )
    if any(
        type(item) is not str
        for name in ("context_stack", "actor_stack", "policy_stack")
        for item in getattr(state, name)
    ):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "structural stack item is not exact text",
        )
    if any(
        type(item) is not dict
        for name in ("mailbox_inbound", "mailbox_outbound")
        for item in getattr(state, name)
    ):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "mailbox item is not an exact mapping",
        )
    if any(
        item is not None and type(item) not in (dict, PendingHostCall)
        for item in (
            state.pending_host_call,
            state.pending_message_receive,
            state.error,
        )
    ):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "pending/error envelope is not exact",
        )
    for item in state.name_save_stack:
        if (
            type(item) is not tuple
            or len(item) != 3
            or type(item[0]) is not str
            or type(item[1]) is not bool
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "name-save entry is not exact",
            )
    for frame in state.call_stack:
        if type(frame) is not CallFrame:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "call stack item is not a CallFrame",
            )
        if any(
            type(getattr(frame, name)) is not list
            or any(type(item) is not str for item in getattr(frame, name))
            for name in (
                "context_stack_snapshot",
                "actor_stack_snapshot",
                "policy_stack_snapshot",
            )
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "call frame structural snapshots are not exact",
            )
        if type(frame.locals_snapshot) is not dict or type(frame.fn_name) is not str:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "call frame identity/schema is not exact",
            )
        if any(
            type(item) is not int or not 0 <= item <= MAX_SAFE_INTEGER
            for item in (frame.return_ip, frame.stack_base)
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "call frame position is not a safe natural",
            )
        if type(program) is BytecodeProgram and (
            frame.program_hash != program.program_hash
            or type(frame.body_ip) is not int
            or not _has_function_origin(program, frame.fn_name, frame.body_ip)
        ):
            raise _fail(
                ReplayFailureCode.NON_CANONICAL_VM_VALUE,
                "call frame has no admitted origin",
            )
    for frame in state.guard_stack:
        if type(frame) is not GuardFrame or frame.verdict not in {
            "PENDING",
            "PASS",
            "FAIL",
        }:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "guard stack item is not exact",
            )
        if any(
            type(getattr(frame, name)) is not str
            for name in (
                "guard_id",
                "policy_hash",
                "guard_hash",
                "verdict",
                "entered_at_history_hash",
            )
        ) or not is_transition_id(frame.entered_at_history_hash):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "guard frame identity is not exact",
            )
        if (
            type(frame.entered_at_ip) is not int
            or not 0 <= frame.entered_at_ip <= MAX_SAFE_INTEGER
        ):
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "guard frame position is not a safe natural",
            )
        if frame.parent_guard_id is not None and type(frame.parent_guard_id) is not str:
            raise _fail(
                ReplayFailureCode.TYPE_MISMATCH,
                "guard parent identity is not optional text",
            )
    if type(program) is BytecodeProgram and state.ip > len(program.instructions):
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "instruction pointer is outside the admitted program",
        )
    return state


def encode_recorded_result(value: object) -> bytes:
    require_canonical_vm_value(value, field="recorded result", data_only=True)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def decode_recorded_result(raw: bytes) -> object:
    if type(raw) is not bytes:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a recorded result must be exact bytes",
        )
    try:
        value = json.loads(raw.decode("utf-8"))
        require_canonical_vm_value(value, field="recorded result", data_only=True)
        canonical = encode_recorded_result(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ) as exc:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            "the recorded result is not canonical under the activity result codec",
        ) from exc
    if canonical != raw:
        raise _fail(
            ReplayFailureCode.RESULT_NOT_DECODABLE,
            f"the recorded result is not canonical under {ACTIVITY_RESULT_CODEC_V1_E1}",
        )
    return value


def machine_value_bytes(value: object) -> bytes:
    require_canonical_vm_value(value, field="host call operand")
    return json.dumps(
        encode_vm_value(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_snapshot_bytes(snapshot: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "snapshot is not canonical JSON",
        ) from exc


def encode_adapter_snapshot(
    *,
    activity_sequence: int,
    machine: dict[str, object],
    resolved_structural_history: bytes,
) -> bytes:
    if type(machine) is not dict or len(_canonical_snapshot_bytes(machine)) > (
        MAX_MACHINE_SNAPSHOT_BYTES_V1_E1
    ):
        raise _fail(
            ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "the embedded CVM snapshot exceeds the E1 machine limit",
        )
    if type(resolved_structural_history) is not bytes or not resolved_structural_history:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "resolved structural history must be exact non-empty bytes",
        )
    try:
        structural_history = json.loads(resolved_structural_history.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "resolved structural history must be canonical JSON",
        ) from exc
    if type(structural_history) is not dict or _canonical_snapshot_bytes(
        structural_history
    ) != resolved_structural_history:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "resolved structural history must be an exact canonical object",
        )
    return _canonical_snapshot_bytes(
        {
            "schema_version": _ADAPTER_SNAPSHOT_SCHEMA_V1_E1,
            "adapter_id": REPLAY_MACHINE_ADAPTER_ID_V1_E1,
            "activity_sequence": activity_sequence,
            "resolved_structural_history": structural_history,
            "machine": machine,
        }
    )


def digest_adapter_snapshot(snapshot_bytes: bytes) -> str:
    return hashlib.sha256(_SNAPSHOT_DIGEST_PROFILE + snapshot_bytes).hexdigest()


def decode_adapter_snapshot(
    snapshot_bytes: bytes,
) -> tuple[BytecodeProgram, VMState, bool, int, bytes]:
    if type(snapshot_bytes) is not bytes or not snapshot_bytes:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a snapshot must be exact non-empty bytes",
        )
    if len(snapshot_bytes) > MAX_SNAPSHOT_BYTES_V1_E1:
        raise _fail(
            ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "the adapter snapshot exceeds the E1 combined limit",
        )
    try:
        snapshot = json.loads(snapshot_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a durable snapshot is not JSON",
        ) from exc
    if type(snapshot) is not dict or set(snapshot) != {
        "schema_version",
        "adapter_id",
        "activity_sequence",
        "resolved_structural_history",
        "machine",
    }:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a durable adapter snapshot is not exact",
        )
    if _canonical_snapshot_bytes(snapshot) != snapshot_bytes:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "a durable snapshot is not canonical",
        )
    if snapshot["schema_version"] != _ADAPTER_SNAPSHOT_SCHEMA_V1_E1:
        raise _fail(
            ReplayFailureCode.UNKNOWN_SCHEMA_VERSION,
            "adapter snapshot schema is unknown",
        )
    if snapshot["adapter_id"] != REPLAY_MACHINE_ADAPTER_ID_V1_E1:
        raise _fail(
            ReplayFailureCode.IDENTITY_MISMATCH,
            "adapter snapshot names another machine",
        )
    sequence = snapshot["activity_sequence"]
    if type(sequence) is not int or not 0 <= sequence <= MAX_SAFE_INTEGER:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "activity_sequence is not a safe natural",
        )
    structural_history = snapshot["resolved_structural_history"]
    if type(structural_history) is not dict:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "resolved structural history is not an exact object",
        )
    machine_snapshot = snapshot["machine"]
    if type(machine_snapshot) is not dict or set(machine_snapshot) != {
        "program",
        "state",
        "halted",
    }:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "embedded CVM snapshot is not exact",
        )
    if len(_canonical_snapshot_bytes(machine_snapshot)) > MAX_MACHINE_SNAPSHOT_BYTES_V1_E1:
        raise _fail(
            ReplayFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "the embedded CVM snapshot exceeds the E1 machine limit",
        )
    halted = machine_snapshot["halted"]
    if type(halted) is not bool:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "snapshot halted flag must be exact",
        )
    try:
        program = BytecodeProgram.from_dict(machine_snapshot["program"])
        state = VMState.from_dict(machine_snapshot["state"])
    except (
        AttributeError,
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
        VMSnapshotFormatError,
    ) as exc:
        raise _fail(
            ReplayFailureCode.TYPE_MISMATCH,
            "snapshot payload is malformed",
        ) from exc
    return program, state, halted, sequence, _canonical_snapshot_bytes(structural_history)
