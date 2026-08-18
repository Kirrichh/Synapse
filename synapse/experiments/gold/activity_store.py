"""Stage 4 §23 — durable storage for exact activity results and their records.

This is the adapter that makes "replay injects the recorded result" a fact about
bytes rather than a claim about metadata.

Before it existed, a recorded activity carried a digest and an optional
reference, and the replay adapter answered an effect with a dictionary it built
during the run — the opcode, a status string, the identity, the digest. Every
field in that dictionary was true, and the whole was a substitution: the machine
received a description of a result instead of the result. Nothing that consumed
it could tell the difference, because there was nothing else to compare against;
the bytes the live call had produced were not kept anywhere.

So they are kept here, and two separate concerns are kept apart while doing it,
the way a content-addressable store and its action metadata are kept apart in
build systems that had to learn the same lesson about poisoned caches:

*The blob half* is immutable and content-addressed. Bytes are staged and
published under their own digest, so a second recording of identical bytes is
the same object rather than a second one, and no path rewrites a published blob.
A reference is only "hash-bound" if something checks it, so ``open_result``
re-reads the bytes and re-derives the digest before returning them, and refuses
on a length or digest disagreement rather than trusting the name it was given.

*The record half* is an append-only journal of the recorded activities and the
activity-policy decisions that govern them. It carries the same fail-closed
posture the other Stage 4 histories carry: a torn tail, a sequence gap, a forked
parent anchor or a frame from another coordinator is a typed refusal, never a
best-effort recovery. A partially written record does not become a record by
being read optimistically.

The two halves are written in one interval and read independently. A record
whose blob is missing is not a weaker record — it is unusable, and says so.

The module holds no policy and takes no decision. It stores what it is given,
returns exactly what it stored, and refuses when it cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import json

from .activities import (
    ACTIVITY_RESULT_BLOB_V1,
    ACTIVITY_RESULT_MEDIA_TYPE,
    RecordedActivity,
    activity_ref,
    activity_record_from_dict,
    validate_recorded_activity,
)
from .canonicalization import HashBoundRef, RefKind
from .contracts import SchemaVersion
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    require_open_mutation_ticket,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
    write_staged_bytes,
)

ACTIVITY_RESULT_STORE_V1 = "synapse.stage4.gold.activity-result-store/v1"
ACTIVITY_RECORD_JOURNAL_V1 = "activity-records.journal"
RESULT_DIRECTORY_V1 = "results"

#: A recorded external result is a bounded object. The ceiling is here rather
#: than at the call site because a store that would accept any size is a store
#: whose recovery cost is unbounded, and §23 keeps large payloads out of records.
MAX_RESULT_BYTES_V1 = 4 * 1024 * 1024
_MAX_JOURNAL_PAYLOAD = 256 * 1024

_ANCHOR_PREFIX = ACTIVITY_RESULT_STORE_V1.encode("utf-8") + b"\x00"


class ActivityStoreFailureCode(str, Enum):
    """Typed refusals. Each names a different fact about the world."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    RESULT_CORRUPTED = "RESULT_CORRUPTED"
    RESULT_REF_MISMATCH = "RESULT_REF_MISMATCH"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_FORKED = "HISTORY_FORKED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECORD_UNKNOWN = "RECORD_UNKNOWN"


class ActivityStoreViolation(ValueError):
    """A typed, fail-closed activity-store error carrying no payload."""

    def __init__(self, failure_code: ActivityStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not ActivityStoreFailureCode:
            raise TypeError("failure_code must be an exact ActivityStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ActivityStoreFailureCode, detail: str) -> ActivityStoreViolation:
    return ActivityStoreViolation(code, detail)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def activity_result_ref(result: bytes) -> HashBoundRef:
    """The reference these exact bytes are stored and named under.

    Derived from the bytes rather than supplied alongside them. A caller that
    could choose the reference could name one blob and store another, which is
    the whole class of defect a content address exists to remove.
    """

    if type(result) is not bytes:
        raise _fail(ActivityStoreFailureCode.TYPE_MISMATCH, "an activity result must be exact bytes")
    if len(result) > MAX_RESULT_BYTES_V1:
        raise _fail(
            ActivityStoreFailureCode.RESOURCE_LIMIT_EXCEEDED,
            "activity result exceeds the recorded-result ceiling",
        )
    digest = hashlib.sha256(result).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=ACTIVITY_RESULT_BLOB_V1,
        sha256=digest,
        byte_length=len(result),
        media_type=ACTIVITY_RESULT_MEDIA_TYPE,
    )


def _require_result_ref(value: object) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(ActivityStoreFailureCode.TYPE_MISMATCH, "an activity result ref must be exact")
    if (
        value.kind is not RefKind.ARTIFACT
        or value.schema_id != ACTIVITY_RESULT_BLOB_V1
        or value.media_type != ACTIVITY_RESULT_MEDIA_TYPE
        or value.ref_id != value.sha256
    ):
        raise _fail(
            ActivityStoreFailureCode.RESULT_REF_MISMATCH,
            "this is not a reference to an activity result blob",
        )
    return value


@dataclass(frozen=True)
class ActivityRecordFrame:
    """One journal frame: a recorded activity and the anchor it extended."""

    sequence: int
    coordinator_id: str
    parent_anchor: str
    record: RecordedActivity
    frame_bytes: bytes


def _frame_payload(
    *, sequence: int, coordinator_id: str, parent_anchor: str, record: RecordedActivity
) -> bytes:
    return _canonical(
        {
            "schema_version": ACTIVITY_RESULT_STORE_V1,
            "sequence": sequence,
            "coordinator_id": coordinator_id,
            "parent_anchor": parent_anchor,
            "record": record.to_dict(),
        }
    )


def _anchor_chain(frames: tuple[bytes, ...]) -> tuple[str, ...]:
    anchors = [hashlib.sha256(_ANCHOR_PREFIX).hexdigest()]
    for payload in frames:
        anchors.append(
            hashlib.sha256(
                _ANCHOR_PREFIX + bytes.fromhex(anchors[-1]) + hashlib.sha256(payload).digest()
            ).hexdigest()
        )
    return tuple(anchors)


class FileActivityStore:
    """Exact result bytes and the append-only record of the activities using them."""

    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(ActivityStoreFailureCode.TYPE_MISMATCH, "activity store root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _fail(
                ActivityStoreFailureCode.TYPE_MISMATCH,
                "the activity store requires a mutation fence",
            ) from exc
        self._root = root
        self._mutation_fence = mutation_fence
        ensure_directory(root)
        ensure_directory(root / RESULT_DIRECTORY_V1)
        self._frames()

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def journal_path(self) -> Path:
        return self._root / ACTIVITY_RECORD_JOURNAL_V1

    def _blob_path(self, digest: str) -> Path:
        return self._root / RESULT_DIRECTORY_V1 / f"{digest}.blob"

    # --- the blob half ------------------------------------------------------

    def put_result(self, result: bytes, *, ticket: StoreMutationTicket) -> HashBoundRef:
        """Publish exact bytes under their own digest and return their reference.

        Publishing identical bytes twice is the same object, not a second one —
        the destination already exists and the existing blob is verified to be
        exactly these bytes rather than assumed to be. That check is the point:
        "the file is already there" and "the file already there is this file"
        are different statements, and only the second permits a silent return.
        """

        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        reference = activity_result_ref(result)
        destination = self._blob_path(reference.sha256)
        if destination.exists():
            existing = self.open_result(reference)
            if existing != result:
                raise _fail(
                    ActivityStoreFailureCode.RESULT_CORRUPTED,
                    "a stored blob disagrees with the bytes its digest names",
                )
            return reference
        staged = write_staged_bytes(
            destination.parent,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=result,
            maximum_bytes=MAX_RESULT_BYTES_V1,
            ticket=ticket,
        )
        publish_immutable(staged, destination, ticket=ticket)
        return reference

    def open_result(self, reference: HashBoundRef) -> bytes:
        """Return the exact bytes this reference names, or refuse.

        Absence, a length disagreement and a digest disagreement are three
        different facts and get three different answers. Collapsing them would
        make "the result was never recorded" indistinguishable from "the result
        was recorded and someone rewrote it", and those call for different
        reactions from whoever is reading.
        """

        reference = _require_result_ref(reference)
        path = self._blob_path(reference.sha256)
        try:
            raw = read_regular_bytes(path, maximum_bytes=MAX_RESULT_BYTES_V1)
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED:
                raise _fail(
                    ActivityStoreFailureCode.RESULT_CORRUPTED,
                    "the stored result is larger than any result may be",
                ) from exc
            raise _fail(
                ActivityStoreFailureCode.RESULT_UNAVAILABLE,
                "the recorded result is not retrievable from this store",
            ) from exc
        if len(raw) != reference.byte_length:
            raise _fail(
                ActivityStoreFailureCode.RESULT_CORRUPTED,
                "the stored result is not the length its reference declares",
            )
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise _fail(
                ActivityStoreFailureCode.RESULT_CORRUPTED,
                "the stored result does not hash to the digest that names it",
            )
        return raw

    # --- the record half ----------------------------------------------------

    def _frames(self) -> tuple[ActivityRecordFrame, ...]:
        try:
            scanned = scan_journal(self.journal_path)
        except PersistenceViolation as exc:
            code = (
                ActivityStoreFailureCode.HISTORY_TORN
                if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL
                else ActivityStoreFailureCode.HISTORY_CORRUPT
            )
            raise _fail(code, "the activity record journal could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _fail(
                ActivityStoreFailureCode.HISTORY_TORN,
                "the activity record journal has a torn tail",
            )
        decoded = tuple(self._decode(item.payload) for item in scanned.frames)
        anchors = _anchor_chain(tuple(item.frame_bytes for item in decoded))
        coordinator_id = self._mutation_fence.coordinator_id()
        seen: set[str] = set()
        for index, frame in enumerate(decoded, start=1):
            if frame.sequence != index:
                raise _fail(
                    ActivityStoreFailureCode.SEQUENCE_GAP,
                    "the activity record journal sequence has a gap",
                )
            if frame.parent_anchor != anchors[index - 1]:
                raise _fail(
                    ActivityStoreFailureCode.HISTORY_FORKED,
                    "an activity record frame does not extend its exact prefix",
                )
            if frame.coordinator_id != coordinator_id:
                raise _fail(
                    ActivityStoreFailureCode.COORDINATOR_MISMATCH,
                    "the activity record journal belongs to another coordinator",
                )
            if frame.record.activity_identity in seen:
                raise _fail(
                    ActivityStoreFailureCode.RECORD_DUPLICATE,
                    "the activity record journal repeats one activity identity",
                )
            seen.add(frame.record.activity_identity)
        return decoded

    def _decode(self, payload: bytes) -> ActivityRecordFrame:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                ActivityStoreFailureCode.HISTORY_CORRUPT,
                "an activity record frame is not canonical JSON",
            ) from exc
        if type(data) is not dict or set(data) != {
            "schema_version", "sequence", "coordinator_id", "parent_anchor", "record"
        }:
            raise _fail(
                ActivityStoreFailureCode.HISTORY_CORRUPT,
                "an activity record frame has an unexpected shape",
            )
        if data["schema_version"] != ACTIVITY_RESULT_STORE_V1:
            raise _fail(
                ActivityStoreFailureCode.HISTORY_CORRUPT,
                "an activity record frame declares an unknown store schema",
            )
        record = activity_record_from_dict(data["record"])
        frame_bytes = _frame_payload(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            record=record,
        )
        if frame_bytes != payload:
            raise _fail(
                ActivityStoreFailureCode.HISTORY_CORRUPT,
                "an activity record frame is not the canonical bytes of its content",
            )
        return ActivityRecordFrame(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            record=record,
            frame_bytes=frame_bytes,
        )

    def current_anchor(self) -> str:
        return _anchor_chain(tuple(item.frame_bytes for item in self._frames()))[-1]

    def current_sequence(self) -> int:
        return len(self._frames())

    def append_record(
        self, record: RecordedActivity, *, ticket: StoreMutationTicket
    ) -> str:
        """Append one recorded activity, refusing unless its result is retrievable.

        The order matters and is not an implementation detail. A record is a
        promise that exact bytes can be produced later; appending one whose blob
        is absent would durably record a promise the store cannot keep, and every
        reader afterwards would have to treat "recorded" as "probably recorded".
        """

        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        validate_recorded_activity(record)
        stored = self.open_result(record.result_ref)
        if hashlib.sha256(stored).hexdigest() != record.result_sha256:
            raise _fail(
                ActivityStoreFailureCode.RESULT_CORRUPTED,
                "the stored result does not hash to what this record recorded",
            )
        frames = self._frames()
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        if any(item.record.activity_identity == record.activity_identity for item in frames):
            raise _fail(
                ActivityStoreFailureCode.RECORD_DUPLICATE,
                "this activity identity is already recorded",
            )
        payload = _frame_payload(
            sequence=len(frames) + 1,
            coordinator_id=self._mutation_fence.coordinator_id(),
            parent_anchor=anchors[-1],
            record=record,
        )
        if len(payload) > _MAX_JOURNAL_PAYLOAD:
            raise _fail(
                ActivityStoreFailureCode.RESOURCE_LIMIT_EXCEEDED,
                "an activity record frame exceeds the journal payload ceiling",
            )
        append_journal_payload(self.journal_path, payload, ticket=ticket)
        return self.current_anchor()

    def recorded_activities(self) -> tuple[RecordedActivity, ...]:
        """Every recorded activity, in append order, after a full re-read.

        Restart recovery is this call and nothing else: the journal is scanned,
        every frame is re-derived from its content and checked against its own
        bytes, and the anchor chain is recomputed. Nothing is cached across the
        boundary, so a store reopened in a new process cannot inherit a belief
        the bytes on disk no longer support.
        """

        return tuple(item.record for item in self._frames())

    def require_recorded(self, activity_identity: str) -> RecordedActivity:
        """The durable record for this identity, or a typed refusal."""

        for item in self._frames():
            if item.record.activity_identity == activity_identity:
                return item.record
        raise _fail(
            ActivityStoreFailureCode.RECORD_UNKNOWN,
            "no durable record carries this activity identity",
        )

    def require_record(self, reference: HashBoundRef) -> RecordedActivity:
        """Resolve one exact hash-bound activity record after a full history scan."""

        if type(reference) is not HashBoundRef:
            raise _fail(
                ActivityStoreFailureCode.TYPE_MISMATCH,
                "an exact activity record reference is required",
            )
        if reference.schema_id != SchemaVersion.RECORDED_ACTIVITY_V1.value:
            raise _fail(
                ActivityStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a recorded activity",
            )
        for frame in self._frames():
            restored = frame.record
            if activity_ref(restored).to_dict() != reference.to_dict():
                continue
            self.open_result(restored.result_ref)
            return restored
        raise _fail(
            ActivityStoreFailureCode.RECORD_UNKNOWN,
            "no durable activity record carries this reference",
        )


__all__ = [
    "ACTIVITY_RECORD_JOURNAL_V1",
    "ACTIVITY_RESULT_STORE_V1",
    "MAX_RESULT_BYTES_V1",
    "RESULT_DIRECTORY_V1",
    "ActivityRecordFrame",
    "ActivityStoreFailureCode",
    "ActivityStoreViolation",
    "FileActivityStore",
    "activity_result_ref",
]
