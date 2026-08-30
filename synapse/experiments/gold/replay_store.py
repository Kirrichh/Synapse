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

*Every durable request acquires one explicit post-request outcome.* Normally it
is the result, whatever that result says. If a storage, backend or coordinator
failure prevents the result from becoming durable, the separate attempt
lifecycle records an incomplete/recoverable state instead. That state is not a
fifth replay verdict, and a later result append remains the sole completion
marker.

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

from .canonicalization import HashBoundRef, RefKind
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
from .persistence import (
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    write_staged_bytes,
)
from .replay import (
    MAX_SNAPSHOT_BYTES_V1_E1,
    ReferenceReplayCapture,
    BehaviorReplayRequest,
    BehaviorReplayResult,
    ReplayExecutionManifest,
    reference_capture_from_dict,
    reference_capture_ref,
    replay_manifest_from_dict,
    replay_manifest_ref,
    replay_snapshot_ref,
    replay_request_ref,
    replay_result_from_dict,
    replay_result_ref,
    require_manifest_projects_capture,
    validate_reference_capture,
    validate_replay_manifest,
    validate_replay_request,
    validate_replay_result,
)
from .replay_attempt_lifecycle import (
    REPLAY_EXECUTION_CLAIM_SCHEMA_V1,
    REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1,
    ReplayAttemptLifecycleViolation,
    ReplayExecutionClaim,
    ReplayIncompleteAttempt,
    replay_execution_claim_from_dict,
    replay_execution_claim_ref,
    replay_incomplete_attempt_from_dict,
    replay_incomplete_attempt_ref,
    validate_replay_incomplete_attempt,
)
from .replay_structural_history import (
    MAX_STRUCTURAL_HISTORY_BYTES_V1_E1,
    REPLAY_STRUCTURAL_HISTORY_MEDIA_TYPE,
    REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1,
    StructuralHistoryViolation,
    replay_structural_history_ref,
)

REPLAY_STORE_V1 = "synapse.stage4.gold.replay-store/v1"
REPLAY_JOURNAL_V1 = "replay-records.journal"
SNAPSHOT_DIRECTORY_V1 = "snapshots"
STRUCTURAL_HISTORY_DIRECTORY_V1 = "structural-history"

_MAX_JOURNAL_PAYLOAD = 1024 * 1024

#: A machine snapshot is a whole VM state, so it is allowed to be larger than a
#: journal frame — but not unbounded. A store that would read any size is a store
#: whose reads can be made to cost anything.
_ANCHOR_PREFIX = REPLAY_STORE_V1.encode("utf-8") + b"\x00"


class ReplayRecordKind(str, Enum):
    """The things this journal holds, kept apart by name.

    A store that wrote both under one kind would make "was this run recorded
    before it started" unanswerable, because the request and the result would be
    the same sort of frame arriving in whatever order the writer chose.
    """

    REQUEST = "REQUEST"
    RESULT = "RESULT"
    #: The authority-resolved statement of what a replay is supposed to reach.
    #: Written before a run rather than by it, which is what makes it evidence
    #: instead of a description: expected values arriving as call arguments are
    #: the caller telling the executor what to compare against.
    MANIFEST = "MANIFEST"
    #: What a reference execution actually reached, over exactly which inputs.
    #: A manifest is derived from one of these rather than stated, so the two are
    #: separate kinds: the capture is an observation, the manifest is what an
    #: authority issued from it, and collapsing them would make "who said this
    #: was the expected outcome" unanswerable.
    CAPTURE = "CAPTURE"
    #: One attempt's execution permission, spent exactly once. An in-memory flag
    #: on the receipt object was not enough: the object could be discarded and a
    #: fresh receipt issued for the same durable request, so "spent" meant only
    #: "this Python object was used". Spending is a durable append, and the
    #: journal is what makes a second attempt on one permission fail closed —
    #: including after a restart, which is precisely when an in-memory flag is
    #: gone and the request is still there.
    EXECUTION_SPEND = "EXECUTION_SPEND"
    #: An explicit persistence/lifecycle state, never a replay verdict. It says
    #: this durable request has no terminal result and must be recovered without
    #: treating another execution as a harmless retry.
    INCOMPLETE_ATTEMPT = "INCOMPLETE_ATTEMPT"


class ReplayStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    HISTORY_TORN = "HISTORY_TORN"
    HISTORY_CORRUPT = "HISTORY_CORRUPT"
    HISTORY_FORKED = "HISTORY_FORKED"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    RECORD_DUPLICATE = "RECORD_DUPLICATE"
    RECORD_CONFLICT = "RECORD_CONFLICT"
    RECORD_UNKNOWN = "RECORD_UNKNOWN"
    REQUEST_NOT_RECORDED = "REQUEST_NOT_RECORDED"
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    SNAPSHOT_CORRUPTED = "SNAPSHOT_CORRUPTED"
    STRUCTURAL_HISTORY_UNAVAILABLE = "STRUCTURAL_HISTORY_UNAVAILABLE"
    STRUCTURAL_HISTORY_CORRUPTED = "STRUCTURAL_HISTORY_CORRUPTED"


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
    """Append-only storage for replay requests, lifecycle evidence and results."""

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
        # Created with the store rather than on first write: ``ensure_directory``
        # makes one level, and a fan-out directory whose parent does not exist
        # fails mid-transaction — which, by design, leaves the coordinator's
        # interval open and the whole store refusing.
        self._snapshot_root = root / SNAPSHOT_DIRECTORY_V1
        ensure_directory(self._snapshot_root)
        self._structural_history_root = root / STRUCTURAL_HISTORY_DIRECTORY_V1
        ensure_directory(self._structural_history_root)
        frames = self._frames()
        # Opening a store is recovery, not a promise to validate later. Rebuild
        # every lifecycle index now so malformed or conflicting owner records
        # fail closed before a coordinator can act on an incomplete attempt.
        self._results_by_request(frames)
        self._execution_claims(frames)
        self._incomplete_attempts(frames)

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

    def _results_by_request(
        self, frames: tuple[ReplayRecordFrame, ...] | None = None
    ) -> dict[str, tuple[HashBoundRef, BehaviorReplayResult]]:
        """Rebuild completion markers and reject two results for one request."""

        items = self._frames() if frames is None else frames
        request_sequences = {
            _ref_key(item.record_ref): item.sequence
            for item in items
            if item.kind is ReplayRecordKind.REQUEST
        }
        results: dict[str, tuple[HashBoundRef, BehaviorReplayResult]] = {}
        for item in items:
            if item.kind is not ReplayRecordKind.RESULT:
                continue
            try:
                restored = replay_result_from_dict(item.record)
                expected = replay_result_ref(restored)
            except (TypeError, ValueError) as exc:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable replay result is not a valid owner record",
                ) from exc
            if _ref_key(expected) != _ref_key(item.record_ref):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable result does not reproduce its record reference",
                )
            request_key = _ref_key(restored.request_ref)
            if (
                request_key not in request_sequences
                or request_sequences[request_key] >= item.sequence
            ):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable result does not follow its exact request",
                )
            if request_key in results:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "one durable replay request has more than one result",
                )
            results[request_key] = (item.record_ref, restored)
        return results

    def _execution_claims(
        self, frames: tuple[ReplayRecordFrame, ...] | None = None
    ) -> tuple[ReplayExecutionClaim, ...]:
        """Rebuild exact owner claims; storage never infers their identity."""

        items = self._frames() if frames is None else frames
        request_sequences = {
            _ref_key(item.record_ref): item.sequence
            for item in items
            if item.kind is ReplayRecordKind.REQUEST
        }
        claims: list[ReplayExecutionClaim] = []
        request_keys: set[str] = set()
        identities: set[str] = set()
        for item in items:
            if item.kind is not ReplayRecordKind.EXECUTION_SPEND:
                continue
            try:
                claim = replay_execution_claim_from_dict(item.record)
                expected = replay_execution_claim_ref(claim)
            except ReplayAttemptLifecycleViolation as exc:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable execution claim is not a valid owner record",
                ) from exc
            if _ref_key(expected) != _ref_key(item.record_ref):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable execution claim does not reproduce its reference",
                )
            request_key = _ref_key(claim.request_ref)
            if (
                request_key not in request_sequences
                or request_sequences[request_key] >= item.sequence
            ):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable execution claim does not follow its exact request",
                )
            if request_key in request_keys or claim.execution_identity in identities:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "durable execution claims conflict",
                )
            request_keys.add(request_key)
            identities.add(claim.execution_identity)
            claims.append(claim)
        return tuple(claims)

    def _incomplete_attempts(
        self, frames: tuple[ReplayRecordFrame, ...] | None = None
    ) -> tuple[tuple[HashBoundRef, ReplayIncompleteAttempt], ...]:
        """Rebuild exact non-terminal owner records without classifying them."""

        items = self._frames() if frames is None else frames
        request_sequences = {
            _ref_key(item.record_ref): item.sequence
            for item in items
            if item.kind is ReplayRecordKind.REQUEST
        }
        claims_by_request = {
            _ref_key(claim.request_ref): claim
            for claim in self._execution_claims(items)
        }
        attempts: list[tuple[HashBoundRef, ReplayIncompleteAttempt]] = []
        request_keys: set[str] = set()
        for item in items:
            if item.kind is not ReplayRecordKind.INCOMPLETE_ATTEMPT:
                continue
            try:
                attempt = replay_incomplete_attempt_from_dict(item.record)
                expected = replay_incomplete_attempt_ref(attempt)
            except ReplayAttemptLifecycleViolation as exc:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable incomplete attempt is not a valid owner record",
                ) from exc
            if _ref_key(expected) != _ref_key(item.record_ref):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable incomplete attempt does not reproduce its reference",
                )
            request_key = _ref_key(attempt.request_ref)
            if (
                request_key not in request_sequences
                or request_sequences[request_key] >= item.sequence
            ):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "a durable incomplete attempt does not follow its exact request",
                )
            claim = claims_by_request.get(request_key)
            if (
                claim is not None
                and attempt.execution_identity != claim.execution_identity
            ):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "an incomplete attempt conflicts with its durable claim",
                )
            if request_key in request_keys:
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "one durable request has conflicting incomplete-attempt records",
                )
            request_keys.add(request_key)
            attempts.append((item.record_ref, attempt))
        return tuple(attempts)

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
        frames = self._frames()
        if not any(
            item.kind is ReplayRecordKind.REQUEST
            and _ref_key(item.record_ref) == _ref_key(result.request_ref)
            for item in frames
        ):
            raise _fail(
                ReplayStoreFailureCode.REQUEST_NOT_RECORDED,
                "a result cannot be recorded for a request this store never saw",
            )
        reference = replay_result_ref(result)
        existing = self._results_by_request(frames).get(_ref_key(result.request_ref))
        if existing is not None:
            code = (
                ReplayStoreFailureCode.RECORD_DUPLICATE
                if _ref_key(existing[0]) == _ref_key(reference)
                else ReplayStoreFailureCode.RECORD_CONFLICT
            )
            raise _fail(code, "this durable request already has its sole result")
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

    # --- the blob half: durable machine snapshots ---------------------------

    def _snapshot_path(self, digest: str) -> Path:
        return self._snapshot_root / digest[:2] / digest

    def put_snapshot(self, snapshot: bytes, *, ticket: StoreMutationTicket) -> HashBoundRef:
        """Publish an exact machine snapshot under its own digest.

        Snapshots are content-addressed for the same reason results are: a
        continuation attaches to a *state*, and a state named by anything but its
        own bytes is a state the caller could substitute. Publishing identical
        bytes twice is the same object, and the existing blob is verified to be
        exactly these bytes rather than assumed to be.
        """

        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        if type(snapshot) is not bytes:
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "a snapshot must be exact bytes")
        reference = replay_snapshot_ref(snapshot)
        destination = self._snapshot_path(reference.sha256)
        if destination.exists():
            if self.open_snapshot(reference) != snapshot:
                raise _fail(
                    ReplayStoreFailureCode.SNAPSHOT_CORRUPTED,
                    "a stored snapshot disagrees with the bytes its digest names",
                )
            return reference
        ensure_directory(destination.parent)
        staged = write_staged_bytes(
            destination.parent,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=snapshot,
            maximum_bytes=MAX_SNAPSHOT_BYTES_V1_E1,
            ticket=ticket,
        )
        publish_immutable(staged, destination, ticket=ticket)
        return reference

    def open_snapshot(self, reference: HashBoundRef) -> bytes:
        """Return the exact snapshot bytes this reference names, or refuse.

        Absent, wrong length and wrong digest are three different facts and get
        two different codes: "this store never held it" is not "this store held
        it and someone rewrote it", and a continuation should react differently.
        """

        if type(reference) is not HashBoundRef:
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "an exact snapshot ref is required")
        if reference.schema_id != SchemaVersion.REPLAY_VM_SNAPSHOT_V1_E1.value:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a machine snapshot",
            )
        path = self._snapshot_path(reference.sha256)
        try:
            raw = read_regular_bytes(path, maximum_bytes=MAX_SNAPSHOT_BYTES_V1_E1)
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED:
                raise _fail(
                    ReplayStoreFailureCode.SNAPSHOT_CORRUPTED,
                    "the stored snapshot is larger than any snapshot may be",
                ) from exc
            raise _fail(
                ReplayStoreFailureCode.SNAPSHOT_UNAVAILABLE,
                "the machine snapshot is not retrievable from this store",
            ) from exc
        if len(raw) != reference.byte_length:
            raise _fail(
                ReplayStoreFailureCode.SNAPSHOT_CORRUPTED,
                "the stored snapshot is not the length its reference declares",
            )
        if hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise _fail(
                ReplayStoreFailureCode.SNAPSHOT_CORRUPTED,
                "the stored snapshot does not hash to the digest that names it",
            )
        return raw

    # --- the structural-effect history blob half ---------------------------

    def _structural_history_path(self, digest: str) -> Path:
        return self._structural_history_root / digest[:2] / digest

    def put_structural_history(
        self, raw: bytes, *, ticket: StoreMutationTicket
    ) -> HashBoundRef:
        """Publish one exact structural-effect history by its content digest."""

        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        if type(raw) is not bytes:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "a structural-effect history must be exact bytes",
            )
        try:
            reference = replay_structural_history_ref(raw)
        except StructuralHistoryViolation as exc:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "a structural history must be canonical",
            ) from exc
        destination = self._structural_history_path(reference.sha256)
        if destination.exists():
            if self.open_structural_history(reference) != raw:
                raise _fail(
                    ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                    "stored structural history disagrees with its content address",
                )
            return reference
        ensure_directory(destination.parent)
        staged = write_staged_bytes(
            destination.parent,
            final_name=destination.name,
            operation_id=new_operation_id(),
            value=raw,
            maximum_bytes=MAX_STRUCTURAL_HISTORY_BYTES_V1_E1,
            ticket=ticket,
        )
        publish_immutable(staged, destination, ticket=ticket)
        return reference

    def open_structural_history(self, reference: HashBoundRef) -> bytes:
        """Read the exact immutable structural history named by ``reference``."""

        if type(reference) is not HashBoundRef:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an exact structural-history ref is required",
            )
        if (
            reference.kind is not RefKind.ARTIFACT
            or reference.schema_id != REPLAY_STRUCTURAL_HISTORY_SCHEMA_V1_E1
            or reference.media_type != REPLAY_STRUCTURAL_HISTORY_MEDIA_TYPE
        ):
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a structural-effect history",
            )
        if reference.byte_length > MAX_STRUCTURAL_HISTORY_BYTES_V1_E1:
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                "the structural-history reference declares an impossible size",
            )
        path = self._structural_history_path(reference.sha256)
        try:
            raw = read_regular_bytes(
                path, maximum_bytes=MAX_STRUCTURAL_HISTORY_BYTES_V1_E1
            )
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED:
                raise _fail(
                    ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                    "the stored structural history exceeds its size limit",
                ) from exc
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_UNAVAILABLE,
                "the structural history is not retrievable from this store",
            ) from exc
        try:
            expected = replay_structural_history_ref(raw)
        except StructuralHistoryViolation as exc:
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                "stored structural history is not canonical",
            ) from exc
        if reference.byte_length != len(raw):
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                "stored structural history has a different declared length",
            )
        if reference.sha256 != hashlib.sha256(raw).hexdigest():
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                "stored structural history does not match its digest",
            )
        if reference != expected:
            raise _fail(
                ReplayStoreFailureCode.STRUCTURAL_HISTORY_CORRUPTED,
                "stored structural history does not reproduce its exact reference",
            )
        return raw

    # --- manifests ----------------------------------------------------------

    def append_manifest(
        self, manifest: ReplayExecutionManifest, *, ticket: StoreMutationTicket
    ) -> HashBoundRef:
        """Record what a replay is expected to reach, before it is asked to.

        A manifest is admitted only if the capture it projects is already here
        and still says the same thing. That is the one check this store can make
        which nobody else can: it holds both records, so "these expected values
        came from an observation" stops being a claim in the manifest and becomes
        a comparison against the observation itself.
        """

        validate_replay_manifest(manifest)
        source = self.require_capture(manifest.source_capture_ref)
        require_manifest_projects_capture(manifest, capture=source)
        reference = replay_manifest_ref(manifest)
        if any(
            _ref_key(item) == _ref_key(reference) for item in self.recorded_manifest_refs()
        ):
            # Already durable, and therefore already this exact manifest: the
            # record is content-addressed, so "present" cannot mean "a different
            # manifest under the same name". Two attempts expecting the same
            # outcome of the same behaviours are one statement, not two.
            return reference
        self._append(
            kind=ReplayRecordKind.MANIFEST,
            record_ref=reference,
            record=manifest.to_dict(),
            ticket=ticket,
        )
        return reference

    def require_manifest(self, reference: HashBoundRef) -> ReplayExecutionManifest:
        """The manifest this reference names, rebuilt from its own bytes."""

        if type(reference) is not HashBoundRef:
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "an exact manifest ref is required")
        if reference.schema_id != SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1_E1.value:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a replay manifest",
            )
        for item in self._frames():
            if item.kind is not ReplayRecordKind.MANIFEST:
                continue
            if _ref_key(item.record_ref) != _ref_key(reference):
                continue
            restored = replay_manifest_from_dict(item.record)
            if _ref_key(replay_manifest_ref(restored)) != _ref_key(reference):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "the restored manifest does not reproduce the reference that named it",
                )
            return restored
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable manifest carries this reference",
        )

    def append_capture(
        self, capture: ReferenceReplayCapture, *, ticket: StoreMutationTicket
    ) -> HashBoundRef:
        """Record what the reference execution reached, and name it by its bytes.

        Returns a hash-bound reference rather than the record id. The id says
        which capture this is; the reference says that these exact bytes are it,
        and a manifest issued from a capture has to carry something a later
        reader can check against the store — an id alone is a name, and a name is
        not evidence.

        Idempotent for the same reason a manifest is: the record is
        content-addressed, so a capture that is already present is this exact
        capture. Two reference runs over the same inputs that reached the same
        place are one observation.
        """

        validate_reference_capture(capture)
        reference = reference_capture_ref(capture)
        if any(
            _ref_key(item) == _ref_key(reference) for item in self.recorded_capture_refs()
        ):
            return reference
        self._append(
            kind=ReplayRecordKind.CAPTURE,
            record_ref=reference,
            record=capture.to_dict(),
            ticket=ticket,
        )
        return reference

    def require_capture(self, reference: HashBoundRef) -> ReferenceReplayCapture:
        """The capture these exact bytes are, rebuilt from them."""

        if type(reference) is not HashBoundRef:
            raise _fail(ReplayStoreFailureCode.TYPE_MISMATCH, "an exact capture ref is required")
        if reference.schema_id != SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1_E1.value:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a reference capture",
            )
        for item in self._frames():
            if item.kind is not ReplayRecordKind.CAPTURE:
                continue
            if _ref_key(item.record_ref) != _ref_key(reference):
                continue
            restored = reference_capture_from_dict(item.record)
            if _ref_key(reference_capture_ref(restored)) != _ref_key(reference):
                raise _fail(
                    ReplayStoreFailureCode.HISTORY_CORRUPT,
                    "the restored capture does not reproduce the reference that named it",
                )
            return restored
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable reference capture carries this reference",
        )

    def recorded_capture_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.CAPTURE
        )

    def spend_execution(
        self,
        identity: str,
        *,
        request_ref: HashBoundRef,
        ticket: StoreMutationTicket,
    ) -> None:
        """Claim this attempt's one permission to execute, or refuse.

        The owner computes the identity; this adapter binds it to the exact
        request and performs only the durable compare-and-set. A request, an
        identity, or either one in a different pairing may be claimed once.
        """

        try:
            claim = ReplayExecutionClaim(
                request_ref=request_ref, execution_identity=identity
            )
        except ReplayAttemptLifecycleViolation as exc:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an execution spend requires an exact owner claim",
            ) from exc
        frames = self._frames()
        request_key = _ref_key(claim.request_ref)
        if not any(
            item.kind is ReplayRecordKind.REQUEST
            and _ref_key(item.record_ref) == request_key
            for item in frames
        ):
            raise _fail(
                ReplayStoreFailureCode.REQUEST_NOT_RECORDED,
                "an execution claim requires its exact durable request",
            )
        if request_key in self._results_by_request(frames):
            raise _fail(
                ReplayStoreFailureCode.RECORD_CONFLICT,
                "a completed request cannot acquire an execution claim",
            )
        if any(
            _ref_key(attempt.request_ref) == request_key
            for _, attempt in self._incomplete_attempts(frames)
        ):
            raise _fail(
                ReplayStoreFailureCode.RECORD_CONFLICT,
                "an incomplete attempt cannot acquire another execution claim",
            )
        for existing in self._execution_claims(frames):
            if (
                existing.execution_identity == claim.execution_identity
                or _ref_key(existing.request_ref) == request_key
            ):
                code = (
                    ReplayStoreFailureCode.RECORD_DUPLICATE
                    if existing == claim
                    else ReplayStoreFailureCode.RECORD_CONFLICT
                )
                raise _fail(
                    code,
                    "this request or execution identity was already claimed",
                )
        reference = replay_execution_claim_ref(claim)
        self._append(
            kind=ReplayRecordKind.EXECUTION_SPEND,
            record_ref=reference,
            record=claim.to_dict(),
            ticket=ticket,
        )

    def recorded_execution_claims(self) -> tuple[ReplayExecutionClaim, ...]:
        """Every exact request-bound execution spend in journal order."""

        return self._execution_claims()

    def recorded_execution_claim_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref
            for item in self._frames()
            if item.kind is ReplayRecordKind.EXECUTION_SPEND
        )

    def require_execution_claim(
        self, reference: HashBoundRef
    ) -> ReplayExecutionClaim:
        """Restore the exact request-bound claim named by the reference."""

        if (
            type(reference) is not HashBoundRef
            or reference.kind is not RefKind.ARTIFACT
            or reference.schema_id != REPLAY_EXECUTION_CLAIM_SCHEMA_V1
            or reference.media_type != "application/json"
        ):
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an exact execution-claim ref is required",
            )
        frames = self._frames()
        claims = iter(self._execution_claims(frames))
        for item in frames:
            if item.kind is not ReplayRecordKind.EXECUTION_SPEND:
                continue
            claim = next(claims)
            if _ref_key(item.record_ref) == _ref_key(reference):
                return claim
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable execution claim carries this reference",
        )

    def spent_execution_identities(self) -> frozenset[str]:
        return frozenset(
            item.execution_identity for item in self.recorded_execution_claims()
        )

    def append_incomplete_attempt(
        self,
        attempt: ReplayIncompleteAttempt,
        *,
        ticket: StoreMutationTicket,
    ) -> HashBoundRef:
        """Persist an explicit non-terminal outcome for a durable request.

        Choosing phase and failure domain belongs to the replay owner. This
        adapter verifies only durable lineage and conflicts. An exact repeat is
        idempotent so restart recovery can materialise the same state again
        without creating a second lifecycle fact.
        """

        try:
            validate_replay_incomplete_attempt(attempt)
        except ReplayAttemptLifecycleViolation as exc:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an exact incomplete-attempt owner record is required",
            ) from exc
        require_open_mutation_ticket(ticket)
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        frames = self._frames()
        request_key = _ref_key(attempt.request_ref)
        if not any(
            item.kind is ReplayRecordKind.REQUEST
            and _ref_key(item.record_ref) == request_key
            for item in frames
        ):
            raise _fail(
                ReplayStoreFailureCode.REQUEST_NOT_RECORDED,
                "an incomplete attempt requires its exact durable request",
            )
        reference = replay_incomplete_attempt_ref(attempt)
        existing_attempts = [
            (stored_ref, stored)
            for stored_ref, stored in self._incomplete_attempts(frames)
            if _ref_key(stored.request_ref) == request_key
        ]
        if existing_attempts:
            stored_ref, _stored = existing_attempts[0]
            if _ref_key(stored_ref) == _ref_key(reference):
                return stored_ref
            raise _fail(
                ReplayStoreFailureCode.RECORD_CONFLICT,
                "this request already has a different incomplete-attempt state",
            )
        if request_key in self._results_by_request(frames):
            raise _fail(
                ReplayStoreFailureCode.RECORD_CONFLICT,
                "a completed request cannot be marked incomplete",
            )
        claims = [
            claim
            for claim in self._execution_claims(frames)
            if _ref_key(claim.request_ref) == request_key
        ]
        if claims and attempt.execution_identity != claims[0].execution_identity:
            raise _fail(
                ReplayStoreFailureCode.RECORD_CONFLICT,
                "incomplete-attempt identity does not match its durable claim",
            )
        self._append(
            kind=ReplayRecordKind.INCOMPLETE_ATTEMPT,
            record_ref=reference,
            record=attempt.to_dict(),
            ticket=ticket,
        )
        return reference

    def require_incomplete_attempt(
        self, reference: HashBoundRef
    ) -> ReplayIncompleteAttempt:
        """Restore an exact lifecycle record; it remains non-terminal evidence."""

        if (
            type(reference) is not HashBoundRef
            or reference.kind is not RefKind.ARTIFACT
            or reference.schema_id != REPLAY_INCOMPLETE_ATTEMPT_SCHEMA_V1
            or reference.media_type != "application/json"
        ):
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an exact incomplete-attempt ref is required",
            )
        for stored_ref, attempt in self._incomplete_attempts():
            if _ref_key(stored_ref) == _ref_key(reference):
                return attempt
        raise _fail(
            ReplayStoreFailureCode.RECORD_UNKNOWN,
            "no durable incomplete attempt carries this reference",
        )

    def recorded_incomplete_attempt_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(reference for reference, _ in self._incomplete_attempts())

    def recoverable_attempts(self) -> tuple[ReplayIncompleteAttempt, ...]:
        """Incomplete owner records that still lack the sole completion marker."""

        frames = self._frames()
        completed = set(self._results_by_request(frames))
        return tuple(
            attempt
            for _, attempt in self._incomplete_attempts(frames)
            if _ref_key(attempt.request_ref) not in completed
        )

    def unresolved_request_refs(self) -> tuple[HashBoundRef, ...]:
        """Requests with neither a result nor an explicit recoverable state."""

        frames = self._frames()
        resolved = set(self._results_by_request(frames))
        resolved.update(
            _ref_key(attempt.request_ref)
            for _, attempt in self._incomplete_attempts(frames)
        )
        return tuple(
            item.record_ref
            for item in frames
            if item.kind is ReplayRecordKind.REQUEST
            and _ref_key(item.record_ref) not in resolved
        )

    def unresolved_execution_claims(self) -> tuple[ReplayExecutionClaim, ...]:
        """Claims with neither a result nor an explicit recoverable state."""

        frames = self._frames()
        resolved = set(self._results_by_request(frames))
        resolved.update(
            _ref_key(attempt.request_ref)
            for _, attempt in self._incomplete_attempts(frames)
        )
        return tuple(
            claim
            for claim in self._execution_claims(frames)
            if _ref_key(claim.request_ref) not in resolved
        )

    def result_ref_for_request(
        self, request_ref: HashBoundRef
    ) -> HashBoundRef | None:
        """Return the sole completion marker for a request, if one exists."""

        if (
            type(request_ref) is not HashBoundRef
            or request_ref.kind is not RefKind.ARTIFACT
            or request_ref.schema_id
            != SchemaVersion.BEHAVIOR_REPLAY_REQUEST_V1.value
            or request_ref.media_type != "application/json"
        ):
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an exact request ref is required",
            )
        found = self._results_by_request().get(_ref_key(request_ref))
        return None if found is None else found[0]

    def recorded_manifest_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.MANIFEST
        )

    def recorded_request_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.REQUEST
        )

    def recorded_result_refs(self) -> tuple[HashBoundRef, ...]:
        return tuple(
            item.record_ref for item in self._frames() if item.kind is ReplayRecordKind.RESULT
        )


def require_production_replay_store(value: object) -> FileReplayStore:
    """The exact-type check, asserted by whoever assembles a production binding.

    It lives here rather than in ``replay.py`` because the type lives here, and
    because ``replay.py`` may not import this module — OD-10/V1 makes this file
    an adapter of it. The composition root imports both and is therefore the one
    party that can compare them; an earlier revision tried to close that gap with
    a registration slot inside the owner, which anything could fill first.
    """

    if type(value) is not FileReplayStore:
        raise _fail(
            ReplayStoreFailureCode.TYPE_MISMATCH,
            "a production replay binding requires an exact FileReplayStore",
        )
    return value


__all__ = [
    "REPLAY_JOURNAL_V1",
    "REPLAY_STORE_V1",
    "FileReplayStore",
    "ReplayRecordFrame",
    "ReplayRecordKind",
    "ReplayStoreFailureCode",
    "ReplayStoreViolation",
    "require_production_replay_store",
]
