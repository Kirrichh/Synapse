"""Immutable phase boundaries used to resume one Gold attempt safely.

The controller writes a boundary immediately before an external effect and a
content-bound completion immediately after it.  Recovery trusts neither memory
nor chronology: it validates the gapless predecessor chain and returns the
exact bytes whose owning adapter must decode again.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.stage10.context_codec import (
    decode_base64url,
    encode_base64url,
)

from .models import GoldAttemptContext, GoldRunManifest, canonical_run_bytes
from .records import RecordKind, RunRecordStore
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ATTEMPT_PROGRESS_SCHEMA_V1 = "synapse.stage4.gold.attempt-progress/v1"
ATTEMPT_PREPARATION_SCHEMA_V1 = "synapse.stage4.gold.attempt-preparation/v1"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


class AttemptProgressPhase(str, Enum):
    DELIVERY_REFUSED = "DELIVERY_REFUSED"
    DELIVERY_UNAVAILABLE = "DELIVERY_UNAVAILABLE"
    DELIVERY_STARTED = "DELIVERY_STARTED"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    C1_STARTED = "C1_STARTED"
    C1_COMPLETED = "C1_COMPLETED"


_TERMINAL_DELIVERY_PHASES = frozenset(
    {
        AttemptProgressPhase.DELIVERY_REFUSED,
        AttemptProgressPhase.DELIVERY_UNAVAILABLE,
    }
)
_EXECUTION_PHASES = (
    AttemptProgressPhase.DELIVERY_STARTED,
    AttemptProgressPhase.WORKER_COMPLETED,
    AttemptProgressPhase.C1_STARTED,
    AttemptProgressPhase.C1_COMPLETED,
)
_PAYLOAD_PHASES = frozenset(
    {
        *_TERMINAL_DELIVERY_PHASES,
        AttemptProgressPhase.WORKER_COMPLETED,
        AttemptProgressPhase.C1_COMPLETED,
    }
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, f"{label} is malformed")
    return value


def progress_key(attempt_index: int, phase: AttemptProgressPhase) -> str:
    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index is invalid")
    if type(phase) is not AttemptProgressPhase:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress phase must be exact")
    return f"{attempt_index}.{phase.value.lower()}"


@dataclass(frozen=True)
class AttemptPreparationStarted:
    """Durable intent before snapshot/retrieval/reference execution can mutate.

    Absence of a subsequent context means the outcome is uncertain. It never
    licenses another call to the input supplier, even in a fresh process.
    """

    manifest_sha256: str
    attempt_index: int
    previous_context_sha256: str | None
    started_at_unix_ms: int

    def __post_init__(self) -> None:
        _digest(self.manifest_sha256, "preparation manifest")
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "preparation index is invalid")
        if self.previous_context_sha256 is not None:
            _digest(self.previous_context_sha256, "preparation predecessor")
        if (self.attempt_index == 1) != (self.previous_context_sha256 is None):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "preparation predecessor is missing or unexpected")
        if type(self.started_at_unix_ms) is not int or self.started_at_unix_ms <= 0:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "preparation clock is invalid")

    def payload(self) -> dict[str, object]:
        return {"schema_version": ATTEMPT_PREPARATION_SCHEMA_V1, **self.__dict__}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AttemptPreparationStarted":
        fields = {"manifest_sha256", "attempt_index", "previous_context_sha256", "started_at_unix_ms"}
        if type(payload) is not dict or set(payload) != fields | {"schema_version"}:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "preparation record has an unknown shape")
        if payload["schema_version"] != ATTEMPT_PREPARATION_SCHEMA_V1:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "preparation schema is unknown")
        return cls(**{name: payload[name] for name in fields})


def audit_preparation_starts(store, *, manifest, attempts, decisions) -> None:
    """A start may precede its context, but only after durable CONTINUE."""

    previous_time = 0
    by_index = {item.attempt_index: item for item in attempts}
    by_decision = {item.attempt_index: item for item in decisions}
    for key in sorted(store.iter_keys(kind=RecordKind.PREPARATION_STARTED), key=lambda key: (len(key), key)):
        record = store.get(kind=RecordKind.PREPARATION_STARTED, key=key)
        started = AttemptPreparationStarted.from_payload(record.payload)
        index = started.attempt_index
        if str(index) != key or index > min(len(attempts) + 1, manifest.config.max_attempts):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "preparation start is outside the run prefix")
        if started.manifest_sha256 != manifest.manifest_sha256:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "preparation belongs to another manifest")
        # A refused preparation must retain the actual regressed clock reading.
        # Such a reading can never back an executed attempt context.
        if started.started_at_unix_ms < previous_time and index in by_index:
            raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "preparation clock moved backwards")
        previous_time = started.started_at_unix_ms
        if index > 1:
            previous = by_index.get(index - 1)
            decision = by_decision.get(index - 1)
            if previous is None or decision is None or decision.terminal:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "preparation lacks durable CONTINUE")
            if started.previous_context_sha256 != previous.context.context_sha256:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "preparation names another predecessor")


@dataclass(frozen=True)
class AttemptProgress:
    """One content-addressed transition in an attempt's recovery chain."""

    run_id: RunId
    gold_run_id: str
    manifest_sha256: str
    attempt_index: int
    attempt_id: AttemptId
    context_sha256: str
    phase: AttemptProgressPhase
    predecessor_sha256: str | None
    payload_ref: HashBoundRef | None
    payload_base64url: str | None
    progress_sha256: str

    def __post_init__(self) -> None:
        if type(self.run_id) is not RunId or type(self.attempt_id) is not AttemptId:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress identities must be exact")
        if type(self.gold_run_id) is not str or not self.gold_run_id:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "gold run id is malformed")
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "progress attempt index is invalid")
        if self.attempt_id.value != str(self.attempt_index):
            raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "progress attempt identity differs")
        _digest(self.manifest_sha256, "progress manifest digest")
        _digest(self.context_sha256, "progress context digest")
        _digest(self.progress_sha256, "progress digest")
        if type(self.phase) is not AttemptProgressPhase:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress phase must be exact")
        first = self.phase is AttemptProgressPhase.DELIVERY_STARTED or self.phase in _TERMINAL_DELIVERY_PHASES
        if first != (self.predecessor_sha256 is None):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "progress predecessor is inconsistent")
        if self.predecessor_sha256 is not None:
            _digest(self.predecessor_sha256, "progress predecessor digest")
        has_payload = self.phase in _PAYLOAD_PHASES
        if has_payload != (self.payload_ref is not None and self.payload_base64url is not None):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "progress payload is inconsistent")
        if not has_payload and (self.payload_ref is not None or self.payload_base64url is not None):
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "marker phase carries a payload")
        if has_payload:
            self._validate_payload()

    def _validate_payload(self) -> None:
        if type(self.payload_ref) is not HashBoundRef or self.payload_ref.kind is not RefKind.ARTIFACT:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress payload ref must be an artifact")
        raw = decode_base64url(self.payload_base64url)
        digest = hashlib.sha256(raw).hexdigest()
        if (
            self.payload_ref.ref_id != digest
            or self.payload_ref.sha256 != digest
            or self.payload_ref.byte_length != len(raw)
        ):
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "progress payload differs from its ref")

    def payload_bytes(self) -> bytes | None:
        return None if self.payload_base64url is None else decode_base64url(self.payload_base64url)

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": ATTEMPT_PROGRESS_SCHEMA_V1,
            "run_id": self.run_id.to_dict(),
            "gold_run_id": self.gold_run_id,
            "manifest_sha256": self.manifest_sha256,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id.to_dict(),
            "context_sha256": self.context_sha256,
            "phase": self.phase.value,
            "predecessor_sha256": self.predecessor_sha256,
            "payload_ref": None if self.payload_ref is None else self.payload_ref.to_dict(),
            "payload_base64url": self.payload_base64url,
        }

    def stored_dict(self) -> dict[str, object]:
        return {"record_sha256": self.progress_sha256, "payload": self.payload()}

    @classmethod
    def create(
        cls,
        *,
        manifest: GoldRunManifest,
        context: GoldAttemptContext,
        phase: AttemptProgressPhase,
        predecessor: AttemptProgress | None,
        payload_ref: HashBoundRef | None = None,
        payload_bytes: bytes | None = None,
    ) -> AttemptProgress:
        if type(manifest) is not GoldRunManifest or type(context) is not GoldAttemptContext:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress owners must be exact")
        manifest.validate_identity()
        context.validate_identity()
        if type(phase) is not AttemptProgressPhase:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "progress phase must be exact")
        if context.run_id != manifest.run_id or context.gold_run_id != manifest.gold_run_id:
            raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "progress context belongs to another run")
        expected_predecessor = {
            AttemptProgressPhase.WORKER_COMPLETED: AttemptProgressPhase.DELIVERY_STARTED,
            AttemptProgressPhase.C1_STARTED: AttemptProgressPhase.WORKER_COMPLETED,
            AttemptProgressPhase.C1_COMPLETED: AttemptProgressPhase.C1_STARTED,
        }.get(phase)
        if expected_predecessor is None:
            if predecessor is not None:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "initial progress phase has a predecessor")
        else:
            if type(predecessor) is not AttemptProgress:
                raise _fail(GoldRunFailureCode.PHASE_INVALID, "progress predecessor is absent")
            predecessor.validate_identity()
            if (
                predecessor.phase is not expected_predecessor
                or predecessor.run_id != manifest.run_id
                or predecessor.gold_run_id != manifest.gold_run_id
                or predecessor.manifest_sha256 != manifest.manifest_sha256
                or predecessor.attempt_index != context.attempt_index
                or predecessor.attempt_id != context.attempt_id
                or predecessor.context_sha256 != context.context_sha256
            ):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "progress predecessor differs")
        predecessor_sha256 = None if predecessor is None else predecessor.progress_sha256
        payload_text = None if payload_bytes is None else encode_base64url(payload_bytes)
        fields = {
            "run_id": manifest.run_id,
            "gold_run_id": manifest.gold_run_id,
            "manifest_sha256": manifest.manifest_sha256,
            "attempt_index": context.attempt_index,
            "attempt_id": context.attempt_id,
            "context_sha256": context.context_sha256,
            "phase": phase,
            "predecessor_sha256": predecessor_sha256,
            "payload_ref": payload_ref,
            "payload_base64url": payload_text,
        }
        provisional = cls(progress_sha256="0" * 64, **fields)
        digest = hashlib.sha256(canonical_run_bytes(provisional.payload())).hexdigest()
        return cls(progress_sha256=digest, **fields)

    def validate_identity(self) -> None:
        expected = hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()
        if self.progress_sha256 != expected:
            raise _fail(GoldRunFailureCode.IDENTITY_MISMATCH, "progress identity differs")


@dataclass(frozen=True)
class AttemptProgressState:
    records: tuple[AttemptProgress, ...]

    @property
    def latest(self) -> AttemptProgress | None:
        return None if not self.records else self.records[-1]

    def get(self, phase: AttemptProgressPhase) -> AttemptProgress | None:
        for record in self.records:
            if record.phase is phase:
                return record
        return None


def _restore_progress(stored: dict[str, object]) -> AttemptProgress:
    if type(stored) is not dict or set(stored) != {"record_sha256", "payload"}:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "progress envelope has unknown shape")
    raw = stored["payload"]
    fields = {
        "schema_version", "run_id", "gold_run_id", "manifest_sha256",
        "attempt_index", "attempt_id", "context_sha256", "phase",
        "predecessor_sha256", "payload_ref", "payload_base64url",
    }
    if type(raw) is not dict or set(raw) != fields or raw["schema_version"] != ATTEMPT_PROGRESS_SCHEMA_V1:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "progress payload has unknown shape")
    try:
        result = AttemptProgress(
            run_id=RunId.from_dict(raw["run_id"]),
            gold_run_id=raw["gold_run_id"],
            manifest_sha256=raw["manifest_sha256"],
            attempt_index=raw["attempt_index"],
            attempt_id=AttemptId.from_dict(raw["attempt_id"]),
            context_sha256=raw["context_sha256"],
            phase=AttemptProgressPhase(raw["phase"]),
            predecessor_sha256=raw["predecessor_sha256"],
            payload_ref=None if raw["payload_ref"] is None else HashBoundRef.from_dict(raw["payload_ref"]),
            payload_base64url=raw["payload_base64url"],
            progress_sha256=stored["record_sha256"],
        )
    except GoldRunViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "progress payload is malformed") from exc
    result.validate_identity()
    return result


def load_attempt_progress(
    store: RunRecordStore,
    *,
    manifest: GoldRunManifest,
    context: GoldAttemptContext,
) -> AttemptProgressState:
    """Load the exact gapless checkpoint prefix for one attempt.

    The local sets and prefix accumulator describe one bounded namespace scan;
    they remain together so terminal and execution paths cannot be validated
    against different snapshots of the store.
    """

    prefix = f"{context.attempt_index}."
    present_keys = {
        key for key in store.iter_keys(kind=RecordKind.ATTEMPT_PROGRESS) if key.startswith(prefix)
    }
    terminal_keys = {
        progress_key(context.attempt_index, phase) for phase in _TERMINAL_DELIVERY_PHASES
    } & present_keys
    execution_keys = {
        progress_key(context.attempt_index, phase) for phase in _EXECUTION_PHASES
    } & present_keys
    if terminal_keys:
        if len(terminal_keys) != 1 or execution_keys or len(present_keys) != 1:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "delivery terminal path is mixed")
        key = next(iter(terminal_keys))
        stored = store.get(kind=RecordKind.ATTEMPT_PROGRESS, key=key)
        if stored is None:
            raise _fail(GoldRunFailureCode.RECORD_MISSING, "delivery terminal progress disappeared")
        progress = _restore_progress(stored.payload)
        phase = next(
            item
            for item in _TERMINAL_DELIVERY_PHASES
            if progress_key(context.attempt_index, item) == key
        )
        _require_progress_binding(progress, manifest=manifest, context=context, phase=phase, predecessor=None)
        return AttemptProgressState((progress,))

    records: list[AttemptProgress] = []
    missing_seen = False
    for phase in _EXECUTION_PHASES:
        stored = store.get(kind=RecordKind.ATTEMPT_PROGRESS, key=progress_key(context.attempt_index, phase))
        if stored is None:
            missing_seen = True
            continue
        if missing_seen:
            raise _fail(GoldRunFailureCode.PHASE_INVALID, "attempt progress contains a phase gap")
        progress = _restore_progress(stored.payload)
        predecessor = None if not records else records[-1]
        _require_progress_binding(
            progress,
            manifest=manifest,
            context=context,
            phase=phase,
            predecessor=predecessor,
        )
        records.append(progress)
    known = {progress_key(context.attempt_index, phase) for phase in _EXECUTION_PHASES}
    extras = present_keys - known
    if extras:
        raise _fail(GoldRunFailureCode.RECORD_CONFLICT, "attempt progress contains an unknown phase")
    return AttemptProgressState(tuple(records))


def require_progress_payload(progress: AttemptProgress) -> tuple[bytes, HashBoundRef]:
    """Return the exact content-bound payload required by a payload phase."""

    if type(progress) is not AttemptProgress:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt progress must be exact")
    payload_ref = progress.payload_ref
    payload_bytes = progress.payload_bytes()
    if payload_ref is None or payload_bytes is None:
        raise _fail(
            GoldRunFailureCode.PHASE_INVALID,
            "progress payload is required for this phase",
        )
    return payload_bytes, payload_ref


def _require_progress_binding(
    progress: AttemptProgress,
    *,
    manifest: GoldRunManifest,
    context: GoldAttemptContext,
    phase: AttemptProgressPhase,
    predecessor: AttemptProgress | None,
) -> None:
    if (
        progress.run_id != manifest.run_id
        or progress.gold_run_id != manifest.gold_run_id
        or progress.manifest_sha256 != manifest.manifest_sha256
        or progress.attempt_index != context.attempt_index
        or progress.attempt_id != context.attempt_id
        or progress.context_sha256 != context.context_sha256
        or progress.phase is not phase
        or progress.predecessor_sha256
        != (None if predecessor is None else predecessor.progress_sha256)
    ):
        raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "attempt progress chain differs")


__all__ = [
    "ATTEMPT_PROGRESS_SCHEMA_V1",
    "AttemptProgress",
    "AttemptProgressPhase",
    "AttemptProgressState",
    "load_attempt_progress",
    "progress_key",
    "require_progress_payload",
]
