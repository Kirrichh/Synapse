"""Authoritative append-only current-boundary adapter for :mod:`knowledge`.

This is the one new production module expressly authorized by OD-P78-R06.
Immutable snapshot members remain audit-readable through ``knowledge.py`` and
``persistence.py``.  Current authority exists only when this adapter has one
valid terminal frame; there is deliberately no mutable head file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Iterable

from . import knowledge as K
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    canonicalize_stage4_payload,
)
from .contracts import (
    AttemptId,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RunId,
    SchemaVersion,
    common_envelope_from_dict,
    compute_envelope_binding_sha256,
    create_common_envelope,
    envelope_bound_record_bytes,
    validate_envelope_bound_record,
)
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    SnapshotTransactionMember,
    StoreMutationFencePort,
    StoreMutationTicket,
    append_journal_payload,
    ensure_directory,
    read_committed_snapshot_transaction,
    require_store_mutation_fence,
    require_ticket_of_coordinator,
    scan_journal,
)


AUTHORITY_JOURNAL_NAME_V2 = "boundary-authority-v2.journal"
_FRAME_SEAL = object()
_BINDING_SEAL = object()


class KnowledgeStoreFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STORE_CORRUPT = "STORE_CORRUPT"
    STORE_TORN = "STORE_TORN"
    STALE_PARENT = "STALE_PARENT"
    SECOND_GENESIS = "SECOND_GENESIS"
    SIBLING_FORK = "SIBLING_FORK"
    TRANSACTION_REUSED = "TRANSACTION_REUSED"
    ATTEMPT_REBOUND = "ATTEMPT_REBOUND"
    BOUNDARY_MISSING = "BOUNDARY_MISSING"
    MEMBER_MISMATCH = "MEMBER_MISMATCH"
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"


class KnowledgeStoreViolation(RuntimeError):
    def __init__(self, failure_code: KnowledgeStoreFailureCode, detail: str) -> None:
        if type(failure_code) is not KnowledgeStoreFailureCode:
            raise TypeError("failure_code must be an exact KnowledgeStoreFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be bounded non-empty text")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: KnowledgeStoreFailureCode, detail: str) -> KnowledgeStoreViolation:
    return KnowledgeStoreViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _member_payload(
    members: Iterable[SnapshotTransactionMember],
) -> list[dict[str, object]]:
    ordered = tuple(sorted(members, key=lambda item: item.member_name))
    if not ordered or any(type(item) is not SnapshotTransactionMember for item in ordered):
        raise _fail(KnowledgeStoreFailureCode.TYPE_MISMATCH, "committed members are invalid")
    names = tuple(item.member_name for item in ordered)
    if len(set(names)) != len(names):
        raise _fail(KnowledgeStoreFailureCode.MEMBER_MISMATCH, "committed member names repeat")
    return [item.to_payload() for item in ordered]


@dataclass(frozen=True, init=False)
class AttemptBoundaryBindingV2:
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    run_id: RunId
    attempt_id: AttemptId
    atomic_boundary_ref: HashBoundRef
    transaction_id: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptBoundaryBindingV2:
        raise TypeError("AttemptBoundaryBindingV2 is store-created")

    def domain_payload(self) -> dict[str, object]:
        return {
            "schema_version": SchemaVersion.ATTEMPT_BOUNDARY_BINDING_V2.value,
            "run_id": self.run_id.to_dict(),
            "attempt_id": self.attempt_id.to_dict(),
            "atomic_boundary_ref": self.atomic_boundary_ref.to_dict(),
            "transaction_id": self.transaction_id,
        }

    def to_dict(self) -> dict[str, object]:
        validate_attempt_binding(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": self.domain_payload(),
        }

    def canonical_bytes(self) -> bytes:
        validate_attempt_binding(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=self.domain_payload(),
        )


def validate_attempt_binding(value: AttemptBoundaryBindingV2) -> None:
    if (
        type(value) is not AttemptBoundaryBindingV2
        or getattr(value, "_trusted_seal", None) is not _BINDING_SEAL
    ):
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding is not store sealed")
    if type(value.run_id) is not RunId or type(value.attempt_id) is not AttemptId:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding identity is invalid")
    if type(value.atomic_boundary_ref) is not HashBoundRef:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding boundary ref is invalid")
    try:
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=_canonical(value.domain_payload()),
            expected_identity_domain=IdentityDomain.ATTEMPT_BOUNDARY_BINDING_V2,
            expected_run_id=value.run_id,
            expected_attempt_id=value.attempt_id,
        )
    except ContractViolation as exc:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding envelope is invalid") from exc


@dataclass(frozen=True, init=False)
class AuthoritativeBoundaryCommitFrame:
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    store_sequence: int
    transaction_id: str
    expected_parent_boundary_id: str | None
    authoritative_boundary_ref: HashBoundRef
    attempt_binding: AttemptBoundaryBindingV2
    committed_members: tuple[SnapshotTransactionMember, ...]
    coordinator_id: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AuthoritativeBoundaryCommitFrame:
        raise TypeError("AuthoritativeBoundaryCommitFrame is store-created")

    def domain_payload(self) -> dict[str, object]:
        return {
            "schema_version": SchemaVersion.AUTHORITATIVE_BOUNDARY_COMMIT_V2.value,
            "store_sequence": self.store_sequence,
            "transaction_id": self.transaction_id,
            "expected_parent_boundary_id": self.expected_parent_boundary_id,
            "authoritative_boundary_ref": self.authoritative_boundary_ref.to_dict(),
            "attempt_binding": self.attempt_binding.to_dict(),
            "committed_members": _member_payload(self.committed_members),
            "coordinator_id": self.coordinator_id,
        }

    def to_dict(self) -> dict[str, object]:
        validate_commit_frame(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": self.domain_payload(),
        }

    def canonical_bytes(self) -> bytes:
        validate_commit_frame(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=self.domain_payload(),
        )


def validate_commit_frame(value: AuthoritativeBoundaryCommitFrame) -> None:
    if (
        type(value) is not AuthoritativeBoundaryCommitFrame
        or getattr(value, "_trusted_seal", None) is not _FRAME_SEAL
    ):
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame is not store sealed")
    if type(value.store_sequence) is not int or value.store_sequence <= 0:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority sequence is invalid")
    if type(value.authoritative_boundary_ref) is not HashBoundRef:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority boundary ref is invalid")
    validate_attempt_binding(value.attempt_binding)
    if value.attempt_binding.atomic_boundary_ref.to_dict() != value.authoritative_boundary_ref.to_dict():
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding names another boundary")
    _member_payload(value.committed_members)
    if type(value.coordinator_id) is not str or len(value.coordinator_id) != 32:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority coordinator is invalid")
    try:
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=_canonical(value.domain_payload()),
            expected_identity_domain=IdentityDomain.AUTHORITATIVE_BOUNDARY_COMMIT_V2,
            expected_run_id=value.attempt_binding.run_id,
            expected_attempt_id=value.attempt_binding.attempt_id,
        )
    except ContractViolation as exc:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame envelope is invalid") from exc


def _restore_binding(value: object) -> AttemptBoundaryBindingV2:
    if type(value) is not dict or set(value) != {
        "envelope",
        "envelope_binding_sha256",
        "payload",
    }:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding fields are invalid")
    payload = value["payload"]
    required = {
        "schema_version",
        "run_id",
        "attempt_id",
        "atomic_boundary_ref",
        "transaction_id",
    }
    if type(payload) is not dict or set(payload) != required:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding payload is invalid")
    if payload["schema_version"] != SchemaVersion.ATTEMPT_BOUNDARY_BINDING_V2.value:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "attempt binding schema is unknown")
    result = object.__new__(AttemptBoundaryBindingV2)
    object.__setattr__(result, "run_id", RunId.from_dict(payload["run_id"]))
    object.__setattr__(result, "attempt_id", AttemptId.from_dict(payload["attempt_id"]))
    object.__setattr__(
        result,
        "atomic_boundary_ref",
        HashBoundRef.from_dict(payload["atomic_boundary_ref"]),
    )
    object.__setattr__(result, "transaction_id", payload["transaction_id"])
    object.__setattr__(
        result,
        "envelope",
        common_envelope_from_dict(
            value["envelope"], canonical_payload_bytes=_canonical(payload)
        ),
    )
    object.__setattr__(
        result, "envelope_binding_sha256", value["envelope_binding_sha256"]
    )
    object.__setattr__(result, "_trusted_seal", _BINDING_SEAL)
    validate_attempt_binding(result)
    return result


def _restore_frame(raw: bytes) -> AuthoritativeBoundaryCommitFrame:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame is undecodable") from exc
    if type(value) is not dict or set(value) != {
        "envelope",
        "envelope_binding_sha256",
        "payload",
    }:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame fields are invalid")
    payload = value["payload"]
    required = {
        "schema_version",
        "store_sequence",
        "transaction_id",
        "expected_parent_boundary_id",
        "authoritative_boundary_ref",
        "attempt_binding",
        "committed_members",
        "coordinator_id",
    }
    if type(payload) is not dict or set(payload) != required:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame payload is invalid")
    if payload["schema_version"] != SchemaVersion.AUTHORITATIVE_BOUNDARY_COMMIT_V2.value:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame schema is unknown")
    raw_members = payload["committed_members"]
    if type(raw_members) is not list:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority members are invalid")
    result = object.__new__(AuthoritativeBoundaryCommitFrame)
    object.__setattr__(result, "store_sequence", payload["store_sequence"])
    object.__setattr__(result, "transaction_id", payload["transaction_id"])
    object.__setattr__(
        result,
        "expected_parent_boundary_id",
        payload["expected_parent_boundary_id"],
    )
    object.__setattr__(
        result,
        "authoritative_boundary_ref",
        HashBoundRef.from_dict(payload["authoritative_boundary_ref"]),
    )
    object.__setattr__(result, "attempt_binding", _restore_binding(payload["attempt_binding"]))
    object.__setattr__(
        result,
        "committed_members",
        tuple(SnapshotTransactionMember.from_payload(item) for item in raw_members),
    )
    object.__setattr__(result, "coordinator_id", payload["coordinator_id"])
    object.__setattr__(
        result,
        "envelope",
        common_envelope_from_dict(
            value["envelope"], canonical_payload_bytes=_canonical(payload)
        ),
    )
    object.__setattr__(
        result, "envelope_binding_sha256", value["envelope_binding_sha256"]
    )
    object.__setattr__(result, "_trusted_seal", _FRAME_SEAL)
    validate_commit_frame(result)
    if result.canonical_bytes() != raw:
        raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority frame bytes are not canonical")
    return result


class AuthoritativeKnowledgeStore:
    """Append-only CAS head with an atomic attempt binding per terminal frame."""

    def __init__(self, root: Path, *, mutation_fence: StoreMutationFencePort) -> None:
        if not isinstance(root, Path):
            raise _fail(KnowledgeStoreFailureCode.TYPE_MISMATCH, "knowledge root must be a Path")
        try:
            require_store_mutation_fence(mutation_fence)
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeStoreFailureCode.TYPE_MISMATCH,
                "knowledge store requires a mutation fence",
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
        return self._root / AUTHORITY_JOURNAL_NAME_V2

    def _frames(self) -> tuple[AuthoritativeBoundaryCommitFrame, ...]:
        try:
            scanned = scan_journal(self.path)
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeStoreFailureCode.STORE_CORRUPT,
                "authority journal could not be scanned",
            ) from exc
        if scanned.torn_tail:
            raise _fail(
                KnowledgeStoreFailureCode.STORE_TORN,
                "authority journal has a torn terminal frame",
            )
        frames = tuple(_restore_frame(item.payload) for item in scanned.frames)
        coordinator = self._mutation_fence.coordinator_id()
        seen_transactions: set[str] = set()
        seen_attempts: dict[str, str] = {}
        previous: str | None = None
        for sequence, frame in enumerate(frames, start=1):
            if frame.store_sequence != sequence:
                raise _fail(KnowledgeStoreFailureCode.STORE_CORRUPT, "authority sequence has a gap")
            if frame.coordinator_id != coordinator:
                raise _fail(
                    KnowledgeStoreFailureCode.COORDINATOR_MISMATCH,
                    "authority journal belongs to another coordinator",
                )
            if frame.expected_parent_boundary_id != previous:
                raise _fail(
                    KnowledgeStoreFailureCode.SIBLING_FORK,
                    "authority frame does not extend the exact head",
                )
            if frame.transaction_id in seen_transactions:
                raise _fail(
                    KnowledgeStoreFailureCode.TRANSACTION_REUSED,
                    "authority journal repeats a transaction",
                )
            attempt = frame.attempt_binding.attempt_id.value
            boundary = frame.authoritative_boundary_ref.ref_id
            if attempt in seen_attempts and seen_attempts[attempt] != boundary:
                raise _fail(
                    KnowledgeStoreFailureCode.ATTEMPT_REBOUND,
                    "attempt is bound to two boundaries",
                )
            seen_transactions.add(frame.transaction_id)
            seen_attempts[attempt] = boundary
            previous = boundary
        for frame in frames:
            self._require_resolved_frame(frame)
        return frames

    def _require_resolved_frame(self, frame: AuthoritativeBoundaryCommitFrame) -> None:
        """Reject a head/binding whose immutable transaction no longer resolves."""

        try:
            # Let the knowledge owner classify the immutable transaction first.
            # In particular, an absent terminal marker is non-existence, while
            # a marker whose members no longer verify is corruption.  Reading
            # the raw marker first collapses both into persistence's broader
            # INTEGRITY_MANIFEST_MALFORMED code and loses that distinction.
            snapshot = K.open_usable_snapshot(
                self._root, transaction_id=frame.transaction_id
            )
            marker, _ = read_committed_snapshot_transaction(
                self._root, transaction_id=frame.transaction_id
            )
        except PersistenceViolation as exc:
            code = (
                KnowledgeStoreFailureCode.BOUNDARY_MISSING
                if exc.failure_code is PersistenceFailureCode.FILESYSTEM_IO_FAILED
                else KnowledgeStoreFailureCode.MEMBER_MISMATCH
            )
            raise _fail(
                code,
                "authority frame does not resolve to exact committed members",
            ) from exc
        except K.KnowledgeViolation as exc:
            if exc.failure_code is K.KnowledgeFailureCode.COMMIT_MARKER_ABSENT:
                code = KnowledgeStoreFailureCode.BOUNDARY_MISSING
            elif exc.failure_code is K.KnowledgeFailureCode.STORE_UNAVAILABLE:
                # A vanished transaction directory is an unavailable store. If
                # its terminal marker is still present, however, the store is
                # reachable and one of the immutable members it names vanished;
                # that is corruption and must not be presented as retryable I/O.
                marker_path = self._root / frame.transaction_id / "commit-marker.json"
                code = (
                    KnowledgeStoreFailureCode.MEMBER_MISMATCH
                    if marker_path.is_file()
                    else KnowledgeStoreFailureCode.STORE_UNAVAILABLE
                )
            else:
                code = KnowledgeStoreFailureCode.MEMBER_MISMATCH
            raise _fail(
                code,
                "authority frame contains invalid snapshot members",
            ) from exc
        if snapshot.boundary.schema_version is not SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2:
            raise _fail(
                KnowledgeStoreFailureCode.STORE_CORRUPT,
                "v1 audit boundary cannot be current authority",
            )
        recorded_members = tuple(
            SnapshotTransactionMember.from_payload(item)
            for item in marker["members"]
        )
        if _member_payload(recorded_members) != _member_payload(frame.committed_members):
            raise _fail(
                KnowledgeStoreFailureCode.MEMBER_MISMATCH,
                "authority frame member manifest differs from commit marker",
            )
        if K.atomic_boundary_ref(snapshot.boundary).to_dict() != frame.authoritative_boundary_ref.to_dict():
            raise _fail(
                KnowledgeStoreFailureCode.BOUNDARY_MISSING,
                "authority frame names another boundary",
            )

    def current_sequence(self) -> int:
        return len(self._frames())

    def current_boundary_id(self) -> str | None:
        frames = self._frames()
        return None if not frames else frames[-1].authoritative_boundary_ref.ref_id

    def current_anchor(self) -> str:
        running = hashlib.sha256(
            b"synapse.stage4.gold.authoritative-boundary-genesis/v2"
        ).digest()
        for frame in self._frames():
            running = hashlib.sha256(
                running + hashlib.sha256(frame.canonical_bytes()).digest()
            ).digest()
        return running.hex()

    def attempt_boundary_id(self, attempt_id: AttemptId) -> str | None:
        if type(attempt_id) is not AttemptId:
            raise _fail(KnowledgeStoreFailureCode.TYPE_MISMATCH, "attempt id must be exact")
        for frame in self._frames():
            if frame.attempt_binding.attempt_id == attempt_id:
                return frame.authoritative_boundary_ref.ref_id
        return None

    def current_boundary(self) -> K.AtomicSnapshotBoundary | None:
        frames = self._frames()
        if not frames:
            return None
        frame = frames[-1]
        snapshot = K.open_usable_snapshot(self._root, transaction_id=frame.transaction_id)
        if K.atomic_boundary_ref(snapshot.boundary).to_dict() != frame.authoritative_boundary_ref.to_dict():
            raise _fail(
                KnowledgeStoreFailureCode.BOUNDARY_MISSING,
                "authoritative head member is absent or changed",
            )
        return snapshot.boundary

    def open_current(self) -> K.UsableKnowledgeSnapshot:
        frames = self._frames()
        if not frames:
            raise _fail(
                KnowledgeStoreFailureCode.BOUNDARY_MISSING,
                "authoritative boundary head is absent",
            )
        snapshot = K.open_usable_snapshot(self._root, transaction_id=frames[-1].transaction_id)
        if K.atomic_boundary_ref(snapshot.boundary).to_dict() != frames[-1].authoritative_boundary_ref.to_dict():
            raise _fail(KnowledgeStoreFailureCode.BOUNDARY_MISSING, "authoritative head does not resolve")
        return snapshot

    def open_for_attempt(self, attempt_id: AttemptId) -> K.UsableKnowledgeSnapshot:
        if type(attempt_id) is not AttemptId:
            raise _fail(KnowledgeStoreFailureCode.TYPE_MISMATCH, "attempt id must be exact")
        frame = next(
            (
                item
                for item in self._frames()
                if item.attempt_binding.attempt_id == attempt_id
            ),
            None,
        )
        if frame is None:
            raise _fail(
                KnowledgeStoreFailureCode.BOUNDARY_MISSING,
                "attempt has no authoritative boundary",
            )
        snapshot = K.open_usable_snapshot(self._root, transaction_id=frame.transaction_id)
        if K.atomic_boundary_ref(snapshot.boundary).to_dict() != frame.authoritative_boundary_ref.to_dict():
            raise _fail(KnowledgeStoreFailureCode.BOUNDARY_MISSING, "attempt binding does not resolve")
        return snapshot

    def commit_boundary(
        self,
        *,
        boundary: K.AtomicSnapshotBoundary,
        transaction_id: str,
        run_id: RunId,
        attempt_id: AttemptId,
        expected_parent_boundary_id: str | None,
        committed_members: tuple[SnapshotTransactionMember, ...],
        ticket: StoreMutationTicket,
    ) -> AuthoritativeBoundaryCommitFrame:
        """CAS the exact head by appending the one visibility frame."""

        K.validate_atomic_boundary(boundary)
        if boundary.schema_version is not SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2 or boundary.envelope is None:
            raise _fail(
                KnowledgeStoreFailureCode.STORE_CORRUPT,
                "only an envelope-bound v2 boundary may become current",
            )
        if boundary.transaction_id != transaction_id:
            raise _fail(
                KnowledgeStoreFailureCode.TRANSACTION_REUSED,
                "boundary and store transaction identities differ",
            )
        if boundary.envelope.run_id != run_id or boundary.envelope.attempt_id != attempt_id:
            raise _fail(
                KnowledgeStoreFailureCode.ATTEMPT_REBOUND,
                "boundary belongs to another run or attempt",
            )
        require_ticket_of_coordinator(
            ticket, coordinator_id=self._mutation_fence.coordinator_id()
        )
        frames = self._frames()
        actual_parent = (
            None if not frames else frames[-1].authoritative_boundary_ref.ref_id
        )
        if not frames and expected_parent_boundary_id is not None:
            raise _fail(
                KnowledgeStoreFailureCode.STALE_PARENT,
                "genesis expectation names a parent",
            )
        if frames and expected_parent_boundary_id is None:
            raise _fail(
                KnowledgeStoreFailureCode.SECOND_GENESIS,
                "a second genesis is forbidden",
            )
        if expected_parent_boundary_id != actual_parent:
            raise _fail(
                KnowledgeStoreFailureCode.STALE_PARENT,
                "caller expectation is not the authoritative head",
            )
        boundary_ref = K.atomic_boundary_ref(boundary)
        if any(item.transaction_id == transaction_id for item in frames):
            raise _fail(
                KnowledgeStoreFailureCode.TRANSACTION_REUSED,
                "transaction identity is already authoritative",
            )
        if any(item.attempt_binding.attempt_id == attempt_id for item in frames):
            raise _fail(
                KnowledgeStoreFailureCode.ATTEMPT_REBOUND,
                "attempt is already durably bound",
            )

        binding = object.__new__(AttemptBoundaryBindingV2)
        object.__setattr__(binding, "run_id", run_id)
        object.__setattr__(binding, "attempt_id", attempt_id)
        object.__setattr__(binding, "atomic_boundary_ref", boundary_ref)
        object.__setattr__(binding, "transaction_id", transaction_id)
        object.__setattr__(binding, "_trusted_seal", _BINDING_SEAL)
        binding_payload = binding.domain_payload()
        binding_envelope = create_common_envelope(
            schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
            identity_domain=IdentityDomain.ATTEMPT_BOUNDARY_BINDING_V2,
            canonical_payload_bytes=_canonical(binding_payload),
            run_id=run_id,
            attempt_id=attempt_id,
            created_at_utc=boundary.envelope.created_at_utc,
            producer_component="knowledge-store",
            repository_revision=boundary.envelope.repository_revision,
            policy_version=boundary.envelope.policy_version,
            environment_profile_id=boundary.envelope.environment_profile_id,
            lineage_parent_ids=(),
        )
        object.__setattr__(binding, "envelope", binding_envelope)
        object.__setattr__(
            binding,
            "envelope_binding_sha256",
            compute_envelope_binding_sha256(binding_envelope),
        )
        validate_attempt_binding(binding)

        result = object.__new__(AuthoritativeBoundaryCommitFrame)
        object.__setattr__(result, "store_sequence", len(frames) + 1)
        object.__setattr__(result, "transaction_id", transaction_id)
        object.__setattr__(
            result,
            "expected_parent_boundary_id",
            expected_parent_boundary_id,
        )
        object.__setattr__(result, "authoritative_boundary_ref", boundary_ref)
        object.__setattr__(result, "attempt_binding", binding)
        object.__setattr__(result, "committed_members", committed_members)
        object.__setattr__(
            result, "coordinator_id", self._mutation_fence.coordinator_id()
        )
        object.__setattr__(result, "_trusted_seal", _FRAME_SEAL)
        frame_payload = result.domain_payload()
        frame_envelope = create_common_envelope(
            schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
            identity_domain=IdentityDomain.AUTHORITATIVE_BOUNDARY_COMMIT_V2,
            canonical_payload_bytes=_canonical(frame_payload),
            run_id=run_id,
            attempt_id=attempt_id,
            created_at_utc=boundary.envelope.created_at_utc,
            producer_component="knowledge-store",
            repository_revision=boundary.envelope.repository_revision,
            policy_version=boundary.envelope.policy_version,
            environment_profile_id=boundary.envelope.environment_profile_id,
            lineage_parent_ids=(),
        )
        object.__setattr__(result, "envelope", frame_envelope)
        object.__setattr__(
            result,
            "envelope_binding_sha256",
            compute_envelope_binding_sha256(frame_envelope),
        )
        validate_commit_frame(result)
        try:
            append_journal_payload(self.path, result.canonical_bytes(), ticket=ticket)
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeStoreFailureCode.STORE_UNAVAILABLE,
                "authority terminal frame could not be appended",
            ) from exc
        committed = self._frames()
        if (
            len(committed) != result.store_sequence
            or committed[-1].canonical_bytes() != result.canonical_bytes()
        ):
            raise _fail(
                KnowledgeStoreFailureCode.STORE_CORRUPT,
                "terminal frame did not become the exact head",
            )
        return committed[-1]


__all__ = (
    "AUTHORITY_JOURNAL_NAME_V2",
    "AttemptBoundaryBindingV2",
    "AuthoritativeBoundaryCommitFrame",
    "AuthoritativeKnowledgeStore",
    "KnowledgeStoreFailureCode",
    "KnowledgeStoreViolation",
    "validate_attempt_binding",
    "validate_commit_frame",
)
