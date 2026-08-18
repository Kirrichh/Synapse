"""Stage 4 §23 — the durable record of replay requests and their results.

§23 says the replay request and result are persisted with their transition and
activity references. Before this module they were not persisted at all: the
request existed as a sealed object for the length of one call, the result was
returned to the caller, and nothing on disk could later say that a particular
replay had been asked for, or what it found.

That absence has a specific consequence, which is why it is a §23 clause rather
than an operational nicety. NR-13 requires every attempt to be preserved — not
the successful ones, every one — and an attempt nobody can find afterwards is
indistinguishable from an attempt that was never made. A run that diverged, a
run refused as incompatible, a run the machine faulted in: each of those is
evidence, and each of them was being discarded.

So both records are appended here, and the order they are appended in carries
meaning:

*The request is appended before the first transition.* Not after, and not
alongside the result. A machine that starts stepping under a request nobody
recorded is a run that can be denied afterwards, and the point of a durable
request is that the decision to run is on record independently of how the run
turned out.

*The result is appended for every replay that began.* Identical, incompatible,
failed, infra error — all four. A store that kept only successes would make the
history of a behavior a history of its good days.

Restoration recomputes rather than trusts. A stored result is rebuilt field by
field, its payload re-canonicalised, its envelope re-derived from those exact
bytes and its record id recomputed; a frame that does not reproduce its own
canonical bytes is refused, as is a torn tail, a sequence gap, a forked anchor
or a frame from another coordinator. Nothing here recovers a partial record into
a whole one.

One asymmetry is deliberate and load-bearing. A **result** restores completely,
because it carries no authority — it says what happened. A **request** carries
the sealed ledger and the minted admission, which are present-tense capability,
and those are not in its payload and cannot be restored from it. What comes back
from the store for a request is the record, for audit and lineage; what it is not
is a request one could execute. Resuming therefore fetches the predecessor
*result* by its exact reference, from here, rather than accepting whatever object
a caller offers as the thing it continues.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import hashlib
import json

from .canonicalization import HashBoundRef
from .contracts import SchemaVersion
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    require_open_mutation_ticket,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
)
from .replay import (
    BehaviorReplayRequest,
    BehaviorReplayResult,
    replay_request_ref,
    replay_result_from_dict,
    replay_result_ref,
    validate_replay_request,
    validate_replay_result,
)

REPLAY_STORE_V1 = "synapse.stage4.gold.replay-store/v1"
REPLAY_JOURNAL_V1 = "replay-records.journal"

_MAX_JOURNAL_PAYLOAD = 1024 * 1024
_ANCHOR_PREFIX = REPLAY_STORE_V1.encode("utf-8") + b"\x00"


class ReplayRecordKind(str, Enum):
    """The two things this journal holds, kept apart by name.

    A store that wrote both under one kind would make "was this run recorded
    before it started" unanswerable, because the request and the result would be
    the same sort of frame arriving in whatever order the writer chose.
    """

    REQUEST = "REQUEST"
    RESULT = "RESULT"


class ReplayStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_FORKED = "HISTORY_FORKED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECORD_UNKNOWN = "RECORD_UNKNOWN"
    REQUEST_NOT_RECORDED = "REQUEST_NOT_RECORDED"


class ReplayStoreViolation(ValueError):
    """A typed, fail-closed replay-store error carrying no payload."""

    def __init__(self, failure_code: ReplayStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not ReplayStoreFailureCode:
            raise TypeError("failure_code must be an exact ReplayStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ReplayStoreFailureCode, detail: str) -> ReplayStoreViolation:
    return ReplayStoreViolation(code, detail)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _ref_key(value: HashBoundRef) -> str:
    if type(value) is not HashBoundRef:
        raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "an exact HashBoundRef is required")
    return _canonical(value.to_dict()).decode("utf-8")


@dataclass(frozen=True)
class ReplayRecordFrame:
    """One journal frame: a request or a result, and the anchor it extended."""

    sequence: int
    coordinator_id: str
    parent_anchor: str
    kind: ReplayRecordKind
    record_ref: HashBoundRef
    record: dict
    frame_bytes: bytes


def _frame_payload(
    *,
    sequence: int,
    coordinator_id: str,
    parent_anchor: str,
    kind: ReplayRecordKind,
    record_ref: HashBoundRef,
    record: dict,
) -> bytes:
    return _canonical(
        {
            "schema_version": REPLAY_STORE_V1,
            "sequence": sequence,
            "coordinator_id": coordinator_id,
            "parent_anchor": parent_anchor,
            "kind": kind.value,
            "record_ref": record_ref.to_dict(),
            "record": record,
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


class FileReplayStore:
    """Append-only durable storage for replay requests and replay results."""

    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "replay store root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "the replay store requires a mutation fence",
            ) from exc
        self._root = root
        self._mutation_fence = mutation_fence
        ensure_directory(root)
        self._frames()

    @property
    def mutation_fence(self) -> StoreMutationFencePort:
        return self._mutation_fence

    @property
    def journal_path(self) -> Path:
        return self._root / REPLAY_JOURNAL_V1

    def _frames(self) -> tuple[ReplayRecordFrame, ...]:
        try:
            scanned = scan_journal(self.journal_path)
        except PersistenceViolation as exc:
            code = (
                ReplayStoreFailureCode.HISTORY_TORN
                if exc.failure_code is PersistenceFailureCode.JOURNAL_TORN_TAIL
                else ReplayStoreFailureCode.HISTORY_CORRUPT
            )
            raise _fail(code, "the replay journal could not be reconstructed") from exc
        if scanned.torn_tail:
            raise _fail(ReplayStoreFailureCode.HISTORY_TORN, "the replay journal has a torn tail")
        decoded = tuple(self._decode(item.payload) for item in scanned.frames)
        anchors = _anchor_chain(tuple(item.frame_bytes for item in decoded))
        coordinator_id = self._mutation_fence.coordinator_id()
        seen: set[tuple[str, str]] = set()
        for index, frame in enumerate(decoded, start=1):
            if frame.sequence != index:
                raise _fail(ReplayStoreFailureCode.SEQUENCE_GAP, "the replay journal sequence has a gap")
            if frame.parent_anchor != anchors[index - 1]:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_FORKED,
                    "a replay frame does not extend its exact prefix",
                )
            if frame.coordinator_id != coordinator_id:
                raise _fail(
                    ReplayStoreFailureCode.COORDINATOR_MISMATCH,
                    "the replay journal belongs to another coordinator",
                )
            key = (frame.kind.value, _ref_key(frame.record_ref))
            if key in seen:
                raise _fail(
                    ReplayStoreFailureCode.RECORD_DUPLICATE,
                    "the replay journal repeats one record",
                )
            seen.add(key)
        return decoded

    def _decode(self, payload: bytes) -> ReplayRecordFrame:
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                ReplayStoreFailureCode.HISTORY_CORRUPT,
                "a replay frame is not canonical JSON",
            ) from exc
        if type(data) is not dict or set(data) != {
            "schema_version", "sequence", "coordinator_id", "parent_anchor",
            "kind", "record_ref", "record",
        }:
            raise _fail(
                ReplayStoreFailureCode.HISTORY_CORRUPT,
                "a replay frame has an unexpected shape",
            )
        if data["schema_version"] != REPLAY_STORE_V1:
            raise _fail(
                ReplayStoreFailureCode.HISTORY_CORRUPT,
                "a replay frame declares an unknown store schema",
            )
        kind = next((item for item in ReplayRecordKind if item.value == data["kind"]), None)
        if kind is None:
            raise _fail(ReplayStoreFailureCode.HISTORY_CORRUPT, "a replay frame has an unknown kind")
        record_ref = HashBoundRef.from_dict(data["record_ref"])
        frame_bytes = _frame_payload(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            kind=kind,
            record_ref=record_ref,
            record=data["record"],
        )
        if frame_bytes != payload:
            raise _fail(
                ReplayStoreFailureCode.HISTORY_CORRUPT,
                "a replay frame is not the canonical bytes of its content",
            )
        stored = _canonical(data["record"])
        if hashlib.sha256(stored).hexdigest() != record_ref.sha256 or len(stored) != record_ref.byte_length:
            raise _fail(
                ReplayStoreFailureCode.HISTORY_CORRUPT,
                "a replay frame's record does not match the reference that names it",
            )
        return ReplayRecordFrame(
            sequence=data["sequence"],
            coordinator_id=data["coordinator_id"],
            parent_anchor=data["parent_anchor"],
            kind=kind,
            record_ref=record_ref,
            record=data["record"],
            frame_bytes=frame_bytes,
        )

    def current_anchor(self) -> str:
        return _anchor_chain(tuple(item.frame_bytes for item in self._frames()))[-1]

    def current_sequence(self) -> int:
        return len(self._frames())

    def _append(
        self,
        *,
        kind: ReplayRecordKind,
        record_ref: HashBoundRef,
        record: dict,
        ticket: StoreMutationTicket,
    ) -> str:
        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        frames = self._frames()
        anchors = _anchor_chain(tuple(item.frame_bytes for item in frames))
        key = (kind.value, _ref_key(record_ref))
        if any((item.kind.value, _ref_key(item.record_ref)) == key for item in frames):
            raise _fail(
                ReplayStoreFailureCode.RECORD_DUPLICATE,
                "this replay record is already durable",
            )
        payload = _frame_payload(
            sequence=len(frames) + 1,
            coordinator_id=self._mutation_fence.coordinator_id(),
            parent_anchor=anchors[-1],
            kind=kind,
            record_ref=record_ref,
            record=record,
        )
        if len(payload) > _MAX_JOURNAL_PAYLOAD:
            raise _fail(
                ReplayStoreFailureCode.RESOURCE_LIMIT_EXCEEDED,
                "a replay frame exceeds the journal payload ceiling",
            )
        append_journal_payload(self.journal_path, payload, ticket=ticket)
        return self.current_anchor()

    def append_request(
        self, request: BehaviorReplayRequest, *, ticket: StoreMutationTicket
    ) -> HashBoundRef:
        """Record the request. Called before the first transition, never after."""

        validate_replay_request(request)
        reference = replay_request_ref(request)
        self._append(
            kind=ReplayRecordKind.REQUEST,
            record_ref=reference,
            record=request.to_dict(),
            ticket=ticket,
        )
        return reference

    def append_result(
        self, result: BehaviorReplayResult, *, ticket: StoreMutationTicket
    ) -> HashBoundRef:
        """Record the result, whatever it says, and only for a recorded request.

        The precondition is the half that makes the ordering real. Appending a
        result whose request was never recorded would produce a history in which
        a run appeared out of nowhere with an outcome already attached — exactly
        the shape a run that was allowed to start unrecorded would leave.
        """

        validate_replay_result(result)
        if not any(
            item.kind is ReplayRecordKind.REQUEST
            and _ref_key(item.record_ref) == _ref_key(result.request_ref)
            for item in self._frames()
        ):
            raise _fail(
                ReplayStoreFailureCode.REQUEST_NOT_RECORDED,
                "a result cannot be recorded for a request this store never saw",
            )
        reference = replay_result_ref(result)
        self._append(
            kind=ReplayRecordKind.RESULT,
            record_ref=reference,
            record=result.to_dict(),
            ticket=ticket,
        )
        return reference

    def request_record(self, reference: HashBoundRef) -> dict:
        """The durable request record, for audit and lineage.

        Deliberately a record and not a ``BehaviorReplayRequest``. The sealed
        ledger and the minted admission are present-tense capability and are not
        in the payload, so no amount of reading gives them back — which is the
        correct outcome and not a limitation: restoring an executable request
        from bytes would be restoring an authority from bytes.
        """

        for item in self._frames():
            if item.kind is ReplayRecordKind.REQUEST and _ref_key(item.record_ref) == _ref_key(reference):
                return item.record
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable request carries this reference",
        )

    def require_result(self, reference: HashBoundRef) -> BehaviorReplayResult:
        """The authoritative predecessor a continuation resumes from.

        Resume reads through here rather than accepting a caller's object,
        because "the result this continues" is a claim about the record of the
        world, and the only party that can settle it is the store that holds the
        record. The restored result is rebuilt from its own bytes and re-verified
        against the reference that named it.
        """

        if type(reference) is not HashBoundRef:
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "an exact result ref is required")
        if reference.schema_id != SchemaVersion.BEHAVIOR_REPLAY_RESULT_V1.value:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a replay result",
            )
        for item in self._frames():
            if item.kind is not ReplayRecordKind.RESULT:
                continue
            if _ref_key(item.record_ref) != _ref_key(reference):
                continue
            restored = replay_result_from_dict(item.record)
            if _ref_key(replay_result_ref(restored)) != _ref_key(reference):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "the restored result does not reproduce the reference that named it",
                )
            return restored
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable result carries this reference",
        )

    def recorded_request_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.REQUEST
        )

    def recorded_result_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.RESULT
        )


__all__ = [
    "REPLAY_JOURNAL_V1",
    "REPLAY_STORE_V1",
    "FileReplayStore",
    "ReplayRecordFrame",
    "ReplayRecordKind",
    "ReplayStoreFailureCode",
    "ReplayStoreViolation",
]
