"""Durable append-only history for compatibility authority records.

This is a one-way adapter over :mod:`compatibility`.  The evaluator owns the
records and their validation; this module owns only byte-exact persistence,
history ancestry and receipts.  Evaluation never happens here, and
``compatibility.py`` does not import this adapter back.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .compatibility import (
    CompatibilityConflictScan,
    CompatibilityContext,
    CompatibilityDecision,
    CompatibilityEvidence,
    CompatibilityRevalidationRecord,
    validate_compatibility_conflict_scan,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_evidence,
    validate_compatibility_revalidation_record,
)
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
    store_transaction,
)


COMPATIBILITY_HISTORY_SCHEMA_V1 = "synapse.stage4.gold.compatibility-history-frame/v1"
COMPATIBILITY_HISTORY_FILE_V1 = "compatibility.journal"
COMPATIBILITY_HISTORY_GENESIS = b"synapse.stage4.gold.compatibility-history-genesis/v1"


class CompatibilityRecordKind(str, Enum):
    CONTEXT = "COMPATIBILITY_CONTEXT"
    EVIDENCE = "COMPATIBILITY_EVIDENCE"
    DECISION = "COMPATIBILITY_DECISION"
    REVALIDATION = "COMPATIBILITY_REVALIDATION"
    CONFLICT_SCAN = "COMPATIBILITY_CONFLICT_SCAN"


class CompatibilityStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_ROLLED_BACK = "HISTORY_ROLLED_BACK"
    HISTORY_FORKED = "HISTORY_FORKED"
    STALE_PARENT = "STALE_PARENT"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_MISSING = "RECORD_MISSING"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECEIPT_INVALID = "RECEIPT_INVALID"


class CompatibilityStoreViolation(RuntimeError):
    def __init__(self, failure_code: CompatibilityStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not CompatibilityStoreFailureCode:
            raise TypeError("failure_code must be an exact CompatibilityStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: CompatibilityStoreFailureCode, detail: str) -> CompatibilityStoreViolation:
    return CompatibilityStoreViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _anchor_chain(payloads: tuple[bytes, ...]) -> tuple[str, ...]:
    running = hashlib.sha256(COMPATIBILITY_HISTORY_GENESIS).digest()
    result = [running.hex()]
    for payload in payloads:
        running = hashlib.sha256(running + hashlib.sha256(payload).digest()).digest()
        result.append(running.hex())
    return tuple(result)


def _record_kind(value: object) -> CompatibilityRecordKind:
    if type(value) is CompatibilityContext:
        validate_compatibility_context(value, evaluator=value._evaluator)
        return CompatibilityRecordKind.CONTEXT
    if type(value) is CompatibilityEvidence:
        validate_compatibility_evidence(value)
        return CompatibilityRecordKind.EVIDENCE
    if type(value) is CompatibilityDecision:
        validate_compatibility_decision(value, evaluator=value._evaluator)
        return CompatibilityRecordKind.DECISION
    if type(value) is CompatibilityRevalidationRecord:
        validate_compatibility_revalidation_record(value)
        return CompatibilityRecordKind.REVALIDATION
    if type(value) is CompatibilityConflictScan:
        validate_compatibility_conflict_scan(value, evaluator=value._evaluator)
        return CompatibilityRecordKind.CONFLICT_SCAN
    raise _fail(
        CompatibilityStoreFailureCode.TYPE_MISMATCH,
        "compatibility history accepts only the five declared record types",
    )


def compatibility_record_ref(value: object) -> HashBoundRef:
    kind = _record_kind(value)
    payload = _canonical(value.to_dict())
    if kind is CompatibilityRecordKind.CONTEXT:
        identity = value.context_id.digest_sha256
        schema_id = value.schema_version
    elif kind is CompatibilityRecordKind.EVIDENCE:
        identity = value.evidence_id.digest_sha256
        schema_id = value.schema_version
    elif kind is CompatibilityRecordKind.DECISION:
        identity = value.decision_id.record_id.digest_sha256
        schema_id = value.schema_version
    elif kind is CompatibilityRecordKind.REVALIDATION:
        identity = value.revalidation_id.digest_sha256
        schema_id = value.schema_version
    else:
        identity = value.scan_id.digest_sha256
        schema_id = value.schema_version
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=identity,
        schema_id=schema_id,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


@dataclass(frozen=True)
class _HistoryFrame:
    kind: CompatibilityRecordKind
    record_ref: HashBoundRef
    canonical_bytes: bytes
    sequence: int
    parent_anchor: str
    coordinator_id: str
    frame_bytes: bytes


_RECEIPT_SEAL = object()


@dataclass(frozen=True, init=False)
class CompatibilityAppendReceipt:
    record_kind: CompatibilityRecordKind
    record_ref: HashBoundRef
    sequence: int
    parent_anchor: str
    history_anchor: str
    coordinator_id: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object):
        raise TypeError("CompatibilityAppendReceipt is produced only by FileCompatibilityStore")


def _frame_payload(
    *,
    kind: CompatibilityRecordKind,
    record_ref: HashBoundRef,
    canonical_bytes: bytes,
    sequence: int,
    parent_anchor: str,
    coordinator_id: str,
) -> bytes:
    return _canonical(
        {
            "schema_version": COMPATIBILITY_HISTORY_SCHEMA_V1,
            "record_kind": kind.value,
            "record_ref": record_ref.to_dict(),
            "canonical_record": canonical_bytes.decode("utf-8"),
            "sequence": sequence,
            "parent_anchor": parent_anchor,
            "coordinator_id": coordinator_id,
        }
    )


def _decode_frame(raw: bytes) -> _HistoryFrame:
    try:
        data = decode_stage4_canonical_bytes(
            raw,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
        fields = {
            "schema_version", "record_kind", "record_ref", "canonical_record",
            "sequence", "parent_anchor", "coordinator_id",
        }
        if type(data) is not dict or set(data) != fields:
            raise ValueError("frame shape")
        if data["schema_version"] != COMPATIBILITY_HISTORY_SCHEMA_V1:
            raise ValueError("frame schema")
        kind = CompatibilityRecordKind(data["record_kind"])
        ref = HashBoundRef.from_dict(data["record_ref"])
        text = data["canonical_record"]
        sequence = data["sequence"]
        parent = data["parent_anchor"]
        coordinator_id = data["coordinator_id"]
        if type(text) is not str:
            raise ValueError("canonical bytes")
        canonical = text.encode("utf-8")
        if (
            ref.kind is not RefKind.ARTIFACT
            or hashlib.sha256(canonical).hexdigest() != ref.sha256
            or len(canonical) != ref.byte_length
        ):
            raise ValueError("record ref")
        if type(sequence) is not int or type(sequence) is bool or sequence <= 0:
            raise ValueError("sequence")
        if type(parent) is not str or len(parent) != 64:
            raise ValueError("parent")
        if type(coordinator_id) is not str or len(coordinator_id) != 32:
            raise ValueError("coordinator")
        return _HistoryFrame(kind, ref, canonical, sequence, parent, coordinator_id, raw)
    except (CompatibilityStoreViolation,):
        raise
    except Exception as exc:
        raise _fail(
            CompatibilityStoreFailureCode.HISTORY_CORRUPT,
            "compatibility history contains a malformed frame",
        ) from exc


@runtime_checkable
class CompatibilityHistoryPort(Protocol):
    @property
    def mutation_fence(self) -> StoreMutationFencePort: ...

    def append_record(
        self,
        record: object,
        *,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket | None = None,
    ) -> CompatibilityAppendReceipt: ...

    def current_anchor(self) -> str: ...

    def current_sequence(self) -> int: ...

    def contains_ref(self, ref: HashBoundRef) -> bool: ...

    def extends(self, anchor: str) -> bool: ...


class FileCompatibilityStore:
    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "history root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _fail(
                CompatibilityStoreFailureCode.TYPE_MISMATCH,
                "compatibility history requires a mutation fence",
            ) from exc
        self._root = root
        self._mutation_fence = mutation_fence
        ensure_directory(root)
        self._frames()

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def path(self) -> Path:
        return self._root / COMPATIBILITY_HISTORY_FILE_V1

    def _frames(self) -> tuple[_HistoryFrame, ...]:
        try:
            scanned = scan_journal(self.path)
        except PersistenceViolation as exc:
            code = (
                CompatibilityStoreFailureCode.HISTORY_TORN
                if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL
                else CompatibilityStoreFailureCode.HISTORY_CORRUPT
            )
            raise _fail(code, "compatibility history could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _fail(CompatibilityStoreFailureCode.HISTORY_TORN, "compatibility history has a torn tail")
        decoded = tuple(_decode_frame(item.payload) for item in scanned.frames)
        anchors = _anchor_chain(tuple(item.frame_bytes for item in decoded))
        coordinator_id = self._mutation_fence.coordinator_id()
        seen: set[tuple[str, str, str]] = set()
        for index, frame in enumerate(decoded, start=1):
            if frame.sequence != index:
                raise _fail(CompatibilityStoreFailureCode.SEQUENCE_GAP, "compatibility history sequence has a gap")
            if frame.parent_anchor != anchors[index - 1]:
                raise _fail(CompatibilityStoreFailureCode.HISTORY_FORKED, "compatibility history parent is not its exact prefix")
            if frame.coordinator_id != coordinator_id:
                raise _fail(CompatibilityStoreFailureCode.COORDINATOR_MISMATCH, "compatibility history belongs to another coordinator")
            key = (frame.record_ref.ref_id, frame.record_ref.sha256, frame.kind.value)
            if key in seen:
                raise _fail(CompatibilityStoreFailureCode.RECORD_DUPLICATE, "compatibility history repeats one record")
            seen.add(key)
        return decoded

    def current_anchor(self) -> str:
        frames = self._frames()
        return _anchor_chain(tuple(item.frame_bytes for item in frames))[-1]

    def current_sequence(self) -> int:
        return len(self._frames())

    def anchor_at(self, sequence: int) -> str:
        if type(sequence) is not int or sequence < 0:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "history sequence is invalid")
        frames = self._frames()
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        if sequence >= len(anchors):
            raise _fail(CompatibilityStoreFailureCode.RECORD_MISSING, "compatibility prefix is absent")
        return anchors[sequence]

    def extends(self, anchor: str) -> bool:
        if type(anchor) is not str or len(anchor) != 64:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "history anchor must be a sha256")
        frames = self._frames()
        return anchor in _anchor_chain(tuple(item.frame_bytes for item in frames))

    def contains_ref(self, ref: HashBoundRef) -> bool:
        if type(ref) is not HashBoundRef:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "record ref must be exact")
        return any(item.record_ref.to_dict() == ref.to_dict() for item in self._frames())

    def contains_ref_at(self, anchor: str, sequence: int, ref: HashBoundRef) -> bool:
        """Prove the ref was already present in the named frozen prefix."""

        if type(ref) is not HashBoundRef:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "record ref must be exact")
        frames = self._frames()
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        if type(sequence) is not int or sequence < 0 or sequence >= len(anchors):
            return False
        if anchors[sequence] != anchor:
            return False
        return any(
            item.record_ref.to_dict() == ref.to_dict() for item in frames[:sequence]
        )

    def prefix_extends(
        self,
        child_anchor: str,
        child_sequence: int,
        parent_anchor: str,
        parent_sequence: int,
    ) -> bool:
        frames = self._frames()
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        if (
            type(child_sequence) is not int
            or type(parent_sequence) is not int
            or child_sequence < parent_sequence
            or parent_sequence < 0
            or child_sequence >= len(anchors)
        ):
            return False
        return anchors[parent_sequence] == parent_anchor and anchors[child_sequence] == child_anchor

    def resolve_ref(self, ref: HashBoundRef) -> bytes:
        if type(ref) is not HashBoundRef:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "record ref must be exact")
        for item in self._frames():
            if item.record_ref.to_dict() == ref.to_dict():
                return item.canonical_bytes
        raise _fail(CompatibilityStoreFailureCode.RECORD_MISSING, "compatibility record is absent")

    def record_position(self, ref: HashBoundRef) -> int:
        for index, item in enumerate(self._frames()):
            if item.record_ref.to_dict() == ref.to_dict():
                return index
        raise _fail(CompatibilityStoreFailureCode.RECORD_MISSING, "compatibility record is absent")

    def append_record(
        self,
        record: object,
        *,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket | None = None,
    ) -> CompatibilityAppendReceipt:
        kind = _record_kind(record)
        canonical = _canonical(record.to_dict())
        ref = compatibility_record_ref(record)
        if type(expected_parent_anchor) is not str or len(expected_parent_anchor) != 64:
            raise _fail(CompatibilityStoreFailureCode.TYPE_MISMATCH, "expected parent must be a sha256")

        if ticket is None:
            refused: CompatibilityStoreViolation | None = None
            receipt: CompatibilityAppendReceipt | None = None
            with store_transaction(self._mutation_fence) as own:
                try:
                    receipt = self._append(
                        kind=kind,
                        ref=ref,
                        canonical=canonical,
                        expected_parent_anchor=expected_parent_anchor,
                        ticket=own,
                    )
                except CompatibilityStoreViolation as exc:
                    if exc.failure_code not in {
                        CompatibilityStoreFailureCode.STALE_PARENT,
                        CompatibilityStoreFailureCode.RECORD_DUPLICATE,
                    }:
                        raise
                    refused = exc
            if refused is not None:
                raise refused
            if receipt is None:
                raise _fail(CompatibilityStoreFailureCode.STORE_UNAVAILABLE, "compatibility append produced no receipt")
            return receipt
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        return self._append(
            kind=kind,
            ref=ref,
            canonical=canonical,
            expected_parent_anchor=expected_parent_anchor,
            ticket=ticket,
        )

    def _append(
        self,
        *,
        kind: CompatibilityRecordKind,
        ref: HashBoundRef,
        canonical: bytes,
        expected_parent_anchor: str,
        ticket: StoreMutationTicket,
    ) -> CompatibilityAppendReceipt:
        frames = self._frames()
        payloads = tuple(item.frame_bytes for item in frames)
        parent = _anchor_chain(payloads)[-1]
        if parent != expected_parent_anchor:
            raise _fail(CompatibilityStoreFailureCode.STALE_PARENT, "compatibility history moved before append")
        if any(item.record_ref.to_dict() == ref.to_dict() for item in frames):
            raise _fail(CompatibilityStoreFailureCode.RECORD_DUPLICATE, "compatibility record is already committed")
        sequence = len(frames) + 1
        coordinator_id = self._mutation_fence.coordinator_id()
        frame = _frame_payload(
            kind=kind,
            record_ref=ref,
            canonical_bytes=canonical,
            sequence=sequence,
            parent_anchor=parent,
            coordinator_id=coordinator_id,
        )
        try:
            append_journal_payload(self.path, frame, ticket=ticket)
        except PersistenceViolation as exc:
            raise _fail(CompatibilityStoreFailureCode.STORE_UNAVAILABLE, "compatibility record could not be appended") from exc
        committed = self._frames()
        if len(committed) != sequence or committed[-1].record_ref.to_dict() != ref.to_dict():
            raise _fail(CompatibilityStoreFailureCode.HISTORY_FORKED, "compatibility append did not become the exact tail")
        anchor = _anchor_chain(tuple(item.frame_bytes for item in committed))[-1]
        receipt = object.__new__(CompatibilityAppendReceipt)
        object.__setattr__(receipt, "record_kind", kind)
        object.__setattr__(receipt, "record_ref", ref)
        object.__setattr__(receipt, "sequence", sequence)
        object.__setattr__(receipt, "parent_anchor", parent)
        object.__setattr__(receipt, "history_anchor", anchor)
        object.__setattr__(receipt, "coordinator_id", coordinator_id)
        object.__setattr__(receipt, "_trusted_seal", _RECEIPT_SEAL)
        return validate_compatibility_receipt(receipt, store=self)


def validate_compatibility_receipt(
    value: CompatibilityAppendReceipt,
    *,
    store: FileCompatibilityStore | None = None,
) -> CompatibilityAppendReceipt:
    if type(value) is not CompatibilityAppendReceipt or getattr(value, "_trusted_seal", None) is not _RECEIPT_SEAL:
        raise _fail(CompatibilityStoreFailureCode.RECEIPT_INVALID, "compatibility receipt is not store sealed")
    if type(value.record_kind) is not CompatibilityRecordKind or type(value.record_ref) is not HashBoundRef:
        raise _fail(CompatibilityStoreFailureCode.RECEIPT_INVALID, "compatibility receipt fields are malformed")
    if type(value.sequence) is not int or value.sequence <= 0:
        raise _fail(CompatibilityStoreFailureCode.RECEIPT_INVALID, "compatibility receipt sequence is invalid")
    for item in (value.parent_anchor, value.history_anchor):
        if type(item) is not str or len(item) != 64:
            raise _fail(CompatibilityStoreFailureCode.RECEIPT_INVALID, "compatibility receipt anchor is invalid")
    if type(value.coordinator_id) is not str or len(value.coordinator_id) != 32:
        raise _fail(CompatibilityStoreFailureCode.RECEIPT_INVALID, "compatibility receipt coordinator is invalid")
    if store is not None:
        if store.mutation_fence.coordinator_id() != value.coordinator_id:
            raise _fail(CompatibilityStoreFailureCode.COORDINATOR_MISMATCH, "receipt belongs to another coordinator")
        try:
            frames = store._frames()
        except CompatibilityStoreViolation as exc:
            if (
                exc.failure_code is CompatibilityStoreFailureCode.HISTORY_CORRUPT
                and store.path.exists()
                and store.path.stat().st_size == 0
            ):
                raise _fail(
                    CompatibilityStoreFailureCode.HISTORY_ROLLED_BACK,
                    "receipt history was rolled back to genesis",
                ) from exc
            raise
        if len(frames) < value.sequence:
            raise _fail(CompatibilityStoreFailureCode.HISTORY_ROLLED_BACK, "receipt sequence was rolled back")
        frame = frames[value.sequence - 1]
        if frame.record_ref.to_dict() != value.record_ref.to_dict() or frame.kind is not value.record_kind:
            raise _fail(CompatibilityStoreFailureCode.HISTORY_FORKED, "receipt position contains another record")
        if not store.extends(value.history_anchor):
            raise _fail(CompatibilityStoreFailureCode.HISTORY_FORKED, "receipt anchor is not a history prefix")
    return value


__all__ = (
    "COMPATIBILITY_HISTORY_FILE_V1",
    "COMPATIBILITY_HISTORY_SCHEMA_V1",
    "CompatibilityAppendReceipt",
    "CompatibilityHistoryPort",
    "CompatibilityRecordKind",
    "CompatibilityStoreFailureCode",
    "CompatibilityStoreViolation",
    "FileCompatibilityStore",
    "compatibility_record_ref",
    "validate_compatibility_receipt",
)
