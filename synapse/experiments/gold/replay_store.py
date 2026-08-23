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
from .contracts import RecordId, SchemaVersion
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
    MAX_SNAPSHOT_BYTES_V1,
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

REPLAY_STORE_V1 = "synapse.stage4.gold.replay-store/v1"
REPLAY_JOURNAL_V1 = "replay-records.journal"
SNAPSHOT_DIRECTORY_V1 = "snapshots"

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
    SNAPSHOT_UNAVAILABLE = "SNAPSHOT_UNAVAILABLE"
    SNAPSHOT_CORRUPTED = "SNAPSHOT_CORRUPTED"


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
        # Created with the store rather than on first write: ``ensure_directory``
        # makes one level, and a fan-out directory whose parent does not exist
        # fails mid-transaction — which, by design, leaves the coordinator's
        # interval open and the whole store refusing.
        self._snapshot_root = root / SNAPSHOT_DIRECTORY_V1
        ensure_directory(self._snapshot_root)
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
            maximum_bytes=MAX_SNAPSHOT_BYTES_V1,
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
        if reference.schema_id != SchemaVersion.REPLAY_VM_SNAPSHOT_V1.value:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "this reference does not name a machine snapshot",
            )
        path = self._snapshot_path(reference.sha256)
        try:
            raw = read_regular_bytes(path, maximum_bytes=MAX_SNAPSHOT_BYTES_V1)
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
        if reference.schema_id != SchemaVersion.REPLAY_EXECUTION_MANIFEST_V1.value:
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
        if reference.schema_id != SchemaVersion.REFERENCE_REPLAY_CAPTURE_V1.value:
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

    def spend_execution(self, identity: str, *, ticket: StoreMutationTicket) -> None:
        """Claim this attempt's one permission to execute, or refuse.

        A compare-and-set against the durable history: the identity is appended
        only if it is not already there, and a second claim raises. The identity
        is computed by the owner from everything that makes this attempt *this*
        attempt — its request, the manifest and capture it descends from, the
        policy decisions it pinned, the exact execution configuration and the
        provenance — so two different attempts never collide and one attempt
        cannot be executed twice under two receipts.
        """

        if type(identity) is not str or len(identity) != 64:
            raise _fail(
                ReplayStoreFailureCode.TYPE_MISMATCH,
                "an execution spend identity is an exact sha256 digest",
            )
        int(identity, 16)
        if identity in self.spent_execution_identities():
            raise _fail(
                ReplayStoreFailureCode.RECORD_DUPLICATE,
                "this attempt's execution permission was already spent",
            )
        self._append(
            kind=ReplayRecordKind.EXECUTION_SPEND,
            record_ref=HashBoundRef(
                kind=RefKind.ARTIFACT,
                ref_id=identity,
                schema_id=REPLAY_JOURNAL_V1,
                sha256=identity,
                byte_length=len(identity),
                media_type="application/json",
            ),
            record={"execution_identity": identity},
            ticket=ticket,
        )

    def spent_execution_identities(self) -> frozenset[str]:
        return frozenset(
            item.record["execution_identity"]
            for item in self._frames()
            if item.kind is ReplayRecordKind.EXECUTION_SPEND
        )

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
