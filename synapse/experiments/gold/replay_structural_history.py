"""Canonical OD-10/V1-E1 structural commands and their ordered history.

Structural commands are deterministic execution evidence, not activities.  This
module records and exact-matches them; it performs no VM execution and no I/O.
The replay owner supplies the frozen profile identity, the CVM adapter supplies
commands, and the existing replay store persists the resulting canonical bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import unicodedata

from .canonicalization import (
    HashBoundRef,
    RefKind,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)

REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1 = (
    "synapse.stage4.gold.replay-structural-history-e1/v1"
)
REPLAY_STRUCTURAL_HISTORY_MEDIA_TYPE = (
    "application/vnd.synapse.replay-structural-history+json"
)
MAX_STRUCTURAL_HISTORY_BYTES_V1_E1 = 8 * 1024 * 1024
MAX_STRUCTURAL_EVENTS_V1 = 1_000_000
_EVENT_PREFIX = b"synapse.stage4.gold.replay-structural-event/v1-e1\x00"


class StructuralHistoryFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    PROFILE_MISMATCH = "PROFILE_MISMATCH"
    HISTORY_MISMATCH = "HISTORY_MISMATCH"


class StructuralHistoryViolation(ValueError):
    def __init__(self, failure_code: StructuralHistoryFailureCode, detail: str) -> None:
        if type(failure_code) is not StructuralHistoryFailureCode:
            raise TypeError("failure_code must be exact")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: StructuralHistoryFailureCode, detail: str
) -> StructuralHistoryViolation:
    return StructuralHistoryViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identifier(value: object, field: str, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value.strip() != value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, f"{field} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, f"{field} is invalid") from exc
    return value


def _sha256(value: object, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, f"{field} is invalid")
    return value


def _transition_id(value: object, field: str) -> str:
    if not (
        type(value) is str
        and (
            value == "sha256:genesis"
            or (
                len(value) == 71
                and value.startswith("sha256:")
                and all(character in "0123456789abcdef" for character in value[7:])
            )
        )
    ):
        raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, f"{field} is invalid")
    return value


def _natural(value: object, field: str) -> int:
    if type(value) is not int or isinstance(value, bool) or not 0 <= value < 2**53:
        raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, f"{field} is invalid")
    return value


_STRUCTURAL_OPCODE_ABI = {
    "CONTEXT_ENTER": ("SYS_CONTEXT_ENTER", "context", "enter"),
    "CONTEXT_EXIT": ("SYS_CONTEXT_EXIT", "context", "exit"),
    "ACTOR_ENTER": ("SYS_ACTOR_ENTER", "actor", "enter"),
    "ACTOR_EXIT": ("SYS_ACTOR_EXIT", "actor", "exit"),
    "POLICY_ENTER": ("SYS_POLICY_ENTER", "policy", "enter"),
    "POLICY_EXIT": ("SYS_POLICY_EXIT", "policy", "exit"),
    "POLICY_RULE_ENTER": ("SYS_POLICY_RULE_ENTER", "policy_rule", "enter"),
    "POLICY_RULE_EXIT": ("SYS_POLICY_RULE_EXIT", "policy_rule", "exit"),
}
_RETURN_SYMBOL = {
    "context": "SYS_CONTEXT_EXIT",
    "actor": "SYS_ACTOR_EXIT",
    "policy": "SYS_POLICY_EXIT",
    "policy_rule": "SYS_POLICY_RULE_EXIT",
}


@dataclass(frozen=True)
class ReplayStructuralCommand:
    """One command before its structural sequence is assigned."""

    profile_id: str
    profile_digest: str
    program_hash: str
    instruction_pointer: int
    frame_depth: int
    pre_transition_hash: str
    occurrence_index: int
    occurrence_size: int
    opcode: str
    sys_symbol: str
    scope_kind: str
    label: str
    metadata_digest: str
    direction: str
    unwind_reason: str | None
    host_abi_version: str

    def __post_init__(self) -> None:
        _identifier(self.profile_id, "profile_id")
        _sha256(self.profile_digest, "profile_digest")
        _sha256(self.program_hash, "program_hash")
        _natural(self.instruction_pointer, "instruction_pointer")
        _natural(self.frame_depth, "frame_depth")
        _transition_id(self.pre_transition_hash, "pre_transition_hash")
        _natural(self.occurrence_index, "occurrence_index")
        _natural(self.occurrence_size, "occurrence_size")
        if not 1 <= self.occurrence_size <= MAX_STRUCTURAL_EVENTS_V1 or self.occurrence_index >= self.occurrence_size:
            raise _fail(
                StructuralHistoryFailureCode.TYPE_MISMATCH,
                "structural occurrence boundary is invalid",
            )
        _identifier(self.opcode, "opcode")
        _identifier(self.sys_symbol, "sys_symbol")
        _identifier(self.scope_kind, "scope_kind")
        _identifier(self.direction, "direction")
        if self.unwind_reason is not None:
            _identifier(self.unwind_reason, "unwind_reason")
        _identifier(self.host_abi_version, "host_abi_version")
        _identifier(self.label, "label", maximum=1024)
        _sha256(self.metadata_digest, "metadata_digest")
        if self.scope_kind in ("policy", "policy_rule") and (
            (":rule:" in self.label) != (self.scope_kind == "policy_rule")
        ):
            raise _fail(
                StructuralHistoryFailureCode.TYPE_MISMATCH,
                "policy scope and label disagree",
            )
        if self.opcode == "RETURN":
            if (
                _RETURN_SYMBOL.get(self.scope_kind) != self.sys_symbol
                or self.direction != "exit"
                or self.unwind_reason != "function_return"
            ):
                raise _fail(
                    StructuralHistoryFailureCode.TYPE_MISMATCH,
                    "RETURN command does not match the unwind ABI",
                )
        elif (
            _STRUCTURAL_OPCODE_ABI.get(self.opcode)
            != (self.sys_symbol, self.scope_kind, self.direction)
            or self.unwind_reason is not None
        ):
            raise _fail(
                StructuralHistoryFailureCode.TYPE_MISMATCH,
                "command does not match its structural opcode ABI",
            )

    def to_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def occurrence_identity(self) -> tuple[object, ...]:
        return (
            self.program_hash,
            self.instruction_pointer,
            self.frame_depth,
            self.pre_transition_hash,
            self.opcode,
            self.host_abi_version,
        )


def _validate_occurrences(commands: tuple[ReplayStructuralCommand, ...]) -> None:
    cursor = 0
    while cursor < len(commands):
        first = commands[cursor]
        size = first.occurrence_size
        batch = commands[cursor : cursor + size]
        if (
            first.occurrence_index != 0
            or len(batch) != size
            or any(
                command.occurrence_identity() != first.occurrence_identity()
                or command.occurrence_size != size
                or command.occurrence_index != index
                for index, command in enumerate(batch)
            )
        ):
            raise _fail(
                StructuralHistoryFailureCode.HISTORY_MISMATCH,
                "structural history has a broken occurrence boundary",
            )
        cursor += size


def _record(command: ReplayStructuralCommand, sequence: int) -> dict[str, object]:
    if type(command) is not ReplayStructuralCommand:
        raise _fail(
            StructuralHistoryFailureCode.TYPE_MISMATCH,
            "structural commands must be exact",
        )
    command.__post_init__()
    if not 1 <= sequence <= MAX_STRUCTURAL_EVENTS_V1:
        raise _fail(
            StructuralHistoryFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "structural sequence exceeds its ceiling",
        )
    body = {"structural_sequence": sequence, **command.to_dict()}
    return {
        **body,
        "event_sha256": hashlib.sha256(_EVENT_PREFIX + _canonical(body)).hexdigest(),
    }


def encode_replay_structural_history(
    commands: tuple[ReplayStructuralCommand, ...],
    *,
    profile_id: str,
    profile_digest: str,
) -> bytes:
    _identifier(profile_id, "profile_id")
    _sha256(profile_digest, "profile_digest")
    if type(commands) is not tuple or len(commands) > MAX_STRUCTURAL_EVENTS_V1:
        raise _fail(
            StructuralHistoryFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "structural history exceeds its event ceiling",
        )
    for command in commands:
        if (
            type(command) is not ReplayStructuralCommand
            or command.profile_id != profile_id
            or command.profile_digest != profile_digest
        ):
            raise _fail(
                StructuralHistoryFailureCode.PROFILE_MISMATCH,
                "structural command belongs to another profile",
            )
    _validate_occurrences(commands)
    raw = _canonical(
        {
            "schema_version": REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1,
            "profile_id": profile_id,
            "profile_digest": profile_digest,
            "records": [
                _record(command, sequence)
                for sequence, command in enumerate(commands, start=1)
            ],
        }
    )
    if len(raw) > MAX_STRUCTURAL_HISTORY_BYTES_V1_E1:
        raise _fail(
            StructuralHistoryFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "structural history exceeds its byte ceiling",
        )
    return raw


def decode_replay_structural_history(
    raw: bytes,
    *,
    profile_id: str | None = None,
    profile_digest: str | None = None,
) -> tuple[ReplayStructuralCommand, ...]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_STRUCTURAL_HISTORY_BYTES_V1_E1:
        raise _fail(
            StructuralHistoryFailureCode.TYPE_MISMATCH,
            "structural history must be exact bounded bytes",
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _fail(
            StructuralHistoryFailureCode.HISTORY_MISMATCH,
            "structural history is not JSON",
        ) from exc
    if (
        type(payload) is not dict
        or set(payload) != {"schema_version", "profile_id", "profile_digest", "records"}
        or payload["schema_version"] != REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1
        or type(payload["records"]) is not list
        or len(payload["records"]) > MAX_STRUCTURAL_EVENTS_V1
    ):
        raise _fail(
            StructuralHistoryFailureCode.HISTORY_MISMATCH,
            "structural history shape or schema differs",
        )
    stored_profile = _identifier(payload["profile_id"], "profile_id")
    stored_digest = _sha256(payload["profile_digest"], "profile_digest")
    if (
        (profile_id is not None and stored_profile != profile_id)
        or (profile_digest is not None and stored_digest != profile_digest)
    ):
        raise _fail(
            StructuralHistoryFailureCode.PROFILE_MISMATCH,
            "structural history belongs to another replay profile",
        )
    fields = set(ReplayStructuralCommand.__dataclass_fields__)
    commands: list[ReplayStructuralCommand] = []
    for sequence, record in enumerate(payload["records"], start=1):
        if (
            type(record) is not dict
            or set(record) != fields | {"structural_sequence", "event_sha256"}
            or record["structural_sequence"] != sequence
        ):
            raise _fail(
                StructuralHistoryFailureCode.HISTORY_MISMATCH,
                "structural record shape or sequence differs",
            )
        command = ReplayStructuralCommand(**{name: record[name] for name in fields})
        if _record(command, sequence) != record:
            raise _fail(
                StructuralHistoryFailureCode.HISTORY_MISMATCH,
                "structural event digest differs",
            )
        commands.append(command)
    result = tuple(commands)
    if encode_replay_structural_history(
        result,
        profile_id=stored_profile,
        profile_digest=stored_digest,
    ) != raw:
        raise _fail(
            StructuralHistoryFailureCode.HISTORY_MISMATCH,
            "structural history transport is not canonical",
        )
    return result


def replay_structural_history_ref(raw: bytes) -> HashBoundRef:
    decode_replay_structural_history(raw)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1,
        sha256=digest,
        byte_length=len(raw),
        media_type=REPLAY_STRUCTURAL_HISTORY_MEDIA_TYPE,
    )


class ReplayStructuralHistory:
    """Attempt-local capture or exact-replay cursor with atomic batch resolve."""

    __slots__ = ("_profile_id", "_profile_digest", "_expected", "_resolved")

    def __init__(
        self,
        *,
        profile_id: str,
        profile_digest: str,
        expected_bytes: bytes | None,
        resolved_bytes: bytes | None = None,
    ) -> None:
        self._profile_id = _identifier(profile_id, "profile_id")
        self._profile_digest = _sha256(profile_digest, "profile_digest")
        self._expected = (
            None
            if expected_bytes is None
            else decode_replay_structural_history(
                expected_bytes,
                profile_id=self._profile_id,
                profile_digest=self._profile_digest,
            )
        )
        resolved = (
            ()
            if resolved_bytes is None
            else decode_replay_structural_history(
                resolved_bytes,
                profile_id=self._profile_id,
                profile_digest=self._profile_digest,
            )
        )
        if self._expected is not None and self._expected[: len(resolved)] != resolved:
            raise _fail(
                StructuralHistoryFailureCode.HISTORY_MISMATCH,
                "resolved structural history is not a prefix of the expected history",
            )
        self._resolved = list(resolved)

    def resolve_batch(
        self,
        commands: tuple[ReplayStructuralCommand, ...],
        *,
        program_hash: str,
        instruction_pointer: int,
        frame_depth: int,
        pre_transition_hash: str,
        opcode: str,
        host_abi_version: str,
    ) -> None:
        if type(commands) is not tuple:
            raise _fail(
                StructuralHistoryFailureCode.TYPE_MISMATCH,
                "structural batch must be an exact tuple",
            )
        identity = (
            _sha256(program_hash, "program_hash"),
            _natural(instruction_pointer, "instruction_pointer"),
            _natural(frame_depth, "frame_depth"),
            _transition_id(pre_transition_hash, "pre_transition_hash"),
            _identifier(opcode, "opcode"),
            _identifier(host_abi_version, "host_abi_version"),
        )
        if any(
            command.occurrence_identity() != identity
            or command.occurrence_size != len(commands)
            or command.occurrence_index != index
            for index, command in enumerate(commands)
        ):
            raise _fail(
                StructuralHistoryFailureCode.HISTORY_MISMATCH,
                "structural commands do not form one exact occurrence",
            )
        proposed = tuple(self._resolved) + commands
        encode_replay_structural_history(
            proposed,
            profile_id=self._profile_id,
            profile_digest=self._profile_digest,
        )
        if self._expected is not None:
            start = len(self._resolved)
            expected = self._expected[start : start + len(commands)]
            next_expected = self._expected[start] if start < len(self._expected) else None
            if (
                expected != commands
                or (
                    not commands
                    and next_expected is not None
                    and next_expected.occurrence_identity() == identity
                )
            ):
                raise _fail(
                    StructuralHistoryFailureCode.HISTORY_MISMATCH,
                    "structural command batch differs from recorded history",
                )
        self._resolved.extend(commands)

    def checkpoint(self) -> int:
        return len(self._resolved)

    def rollback(self, checkpoint: int) -> None:
        if type(checkpoint) is not int or not 0 <= checkpoint <= len(self._resolved):
            raise _fail(StructuralHistoryFailureCode.TYPE_MISMATCH, "structural checkpoint is invalid")
        del self._resolved[checkpoint:]

    def is_complete(self) -> bool:
        return self._expected is None or len(self._resolved) == len(self._expected)

    def canonical_bytes(self) -> bytes:
        return encode_replay_structural_history(
            tuple(self._resolved),
            profile_id=self._profile_id,
            profile_digest=self._profile_digest,
        )


__all__ = [
    "MAX_STRUCTURAL_HISTORY_BYTES_V1_E1",
    "REPLAY_STRUCTURAL_HISTORY_MEDIA_TYPE",
    "REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1",
    "ReplayStructuralCommand",
    "ReplayStructuralHistory",
    "StructuralHistoryFailureCode",
    "StructuralHistoryViolation",
    "decode_replay_structural_history",
    "encode_replay_structural_history",
    "replay_structural_history_ref",
]
