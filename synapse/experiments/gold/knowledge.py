"""Stage 4 §21 RepositoryKnowledgeSnapshot and AtomicSnapshotBoundary.

A knowledge snapshot is an immutable, consistent manifest of one selected
knowledge state for one run/attempt. It is not a mutable bag, a vector store
namespace or a list of summaries. Its purpose is to make mix-and-match between
behavior blobs, bindings, attestations, lifecycle, admissions, indexes,
policies and repository revision impossible.

Two properties carry that guarantee:

*Atomic visibility.* A manifest becomes a usable authority object only after an
``AtomicSnapshotBoundary`` commits with a terminal marker. Before the marker the
snapshot does not exist for consumers; after it every declared root and ref is
frozen. Recovery never assembles a snapshot from partial records and never
substitutes a missing root with an older compatible-looking one.

*Authoritative completeness.* ``completeness_status`` is never self-asserted by
the manifest. It is computed by an independent evaluator over the committed root
set and recorded as a separate ``SnapshotCompletenessDecision``; the boundary
binds that decision by reference. Keeping the status outside the identity-bearing
payload is what prevents a producer from minting a snapshot that declares itself
complete. A consumer re-runs validation immediately before replay.

The module owns domain semantics only. Durable staging, the terminal commit
marker and byte-level integrity live in ``persistence.py``; gate decisions and
snapshot boundaries are exchanged with the §22 owner through the hash-bound refs
and resolver protocols declared in ``contracts.py``, so neither owner imports the
other.

Design references (Appendix C): TUF supplies the shape of the anti mix-and-match
and anti-rollback argument — one signed snapshot pins the versions of everything
else, and a version may never decrease. Content-addressed manifest identity over
an ordered ref set follows the Merkle-DAG pattern. The staged-then-marked commit
is the two-phase shape used by table formats whose readers must never observe a
half-written metadata tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    SnapshotCompletenessStatus,
    Stage4AuthorityHandle,
    compute_record_id,
    require_stage4_authority_handle,
    require_snapshot_status_admits_execution,
    validate_record_id,
)
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    SnapshotTransactionMember,
    commit_snapshot_transaction,
    committed_transaction_exists,
    ensure_directory,
    read_committed_snapshot_transaction,
    stage_snapshot_transaction,
)

KNOWLEDGE_CONTEXT_V1 = "synapse.stage4.gold.knowledge-context/v1"
SNAPSHOT_ROOT_SET_V1 = "synapse.stage4.gold.snapshot-root-set/v1"

#: Domain separator for the compatibility evidence root chained below. A digest
#: derived under this prefix cannot collide with one derived anywhere else.
COMPATIBILITY_EVIDENCE_ROOT_GENESIS = b"synapse.stage4.gold.compatibility-evidence-root/v1"

MANIFEST_MEMBER_NAME = "snapshot-manifest.json"
DECISION_MEMBER_NAME = "completeness-decision.json"
BOUNDARY_MEMBER_NAME = "atomic-boundary.json"

_MANIFEST_SEAL = object()
_DECISION_SEAL = object()
_BOUNDARY_SEAL = object()
_EVALUATOR_SEAL = object()
_USABLE_SEAL = object()

_SHA256_RE_LENGTH = 64
_MAX_REFS_PER_COLLECTION = 4096
_IDENTIFIER_MAX = 128

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class KnowledgeFailureCode(str, Enum):
    """Closed, fail-closed vocabulary for §21 knowledge-snapshot failures."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_SHA256 = "MALFORMED_SHA256"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    DUPLICATE_REFERENCE = "DUPLICATE_REFERENCE"
    UNORDERED_REFERENCE = "UNORDERED_REFERENCE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    ROOT_SET_MISMATCH = "ROOT_SET_MISMATCH"
    MANIFEST_IDENTITY_MISMATCH = "MANIFEST_IDENTITY_MISMATCH"
    DECISION_SUBJECT_MISMATCH = "DECISION_SUBJECT_MISMATCH"
    DECISION_NOT_AUTHORITATIVE = "DECISION_NOT_AUTHORITATIVE"
    COMPLETENESS_NOT_ADMITTED = "COMPLETENESS_NOT_ADMITTED"
    COMMIT_MARKER_ABSENT = "COMMIT_MARKER_ABSENT"
    TRANSACTION_ID_REUSED = "TRANSACTION_ID_REUSED"
    SEQUENCE_NOT_MONOTONIC = "SEQUENCE_NOT_MONOTONIC"
    #: The manifest declares a parent that is not the boundary it is being
    #: committed on top of — or declares one while being committed as genesis.
    #: Distinct from SEQUENCE_NOT_MONOTONIC, which is about a *gap* in a chain
    #: whose identities agree, and from PARTIAL_MANIFEST, which is about a
    #: manifest that omitted something. This is a manifest that named something
    #: else, and a fork reported as an omission is a fork not reported.
    LINEAGE_MISMATCH = "LINEAGE_MISMATCH"
    #: The store roots could not be observed as one moment: an authority store
    #: mutated while they were being read.
    #:
    #: This lives in *this owner's* failure vocabulary and deliberately not in
    #: `SnapshotCompletenessStatus`, which is the closed normative §21 table. An
    #: earlier revision of this round added a member there and a tripwire caught
    #: it — amending a normative vocabulary is not an implementation decision.
    #:
    #: It is also the right shape. Every status in that table is a statement about
    #: the *snapshot*: complete, missing a store, mixing generations. A torn
    #: observation is a statement about the *attempt* — the evaluation could not be
    #: performed, so there is nothing to sign about the snapshot, and producing an
    #: authority record would be signing a verdict nobody reached.
    OBSERVATION_TORN = "OBSERVATION_TORN"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"
    MIX_AND_MATCH_DETECTED = "MIX_AND_MATCH_DETECTED"
    PARTIAL_MANIFEST = "PARTIAL_MANIFEST"
    FROZEN_SNAPSHOT_MUTATION = "FROZEN_SNAPSHOT_MUTATION"
    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    COMMITTED_BYTES_CORRUPTED = "COMMITTED_BYTES_CORRUPTED"
    BOUNDARY_MISMATCH = "BOUNDARY_MISMATCH"
    MULTIPLE_ACTIVE_SNAPSHOTS = "MULTIPLE_ACTIVE_SNAPSHOTS"
    ADMISSION_ROOT_UNCONFIRMED = "ADMISSION_ROOT_UNCONFIRMED"
    ADMISSION_HISTORY_CORRUPT = "ADMISSION_HISTORY_CORRUPT"
    ADMISSION_HISTORY_UNCLASSIFIED = "ADMISSION_HISTORY_UNCLASSIFIED"


class KnowledgeViolation(ValueError):
    """A typed, fail-closed knowledge-snapshot error with non-payload detail."""

    def __init__(self, failure_code: KnowledgeFailureCode, detail: str) -> None:
        if type(failure_code) is not KnowledgeFailureCode:
            raise TypeError("failure_code must be an exact KnowledgeFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: KnowledgeFailureCode, detail: str) -> KnowledgeViolation:
    return KnowledgeViolation(code, detail)


# ---------------------------------------------------------------------------
# Exact-value helpers
# ---------------------------------------------------------------------------


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(KnowledgeFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is invalid")
    if value.strip() != value:
        raise _fail(KnowledgeFailureCode.MALFORMED_IDENTIFIER, f"{field_name} has padding")
    return value


def _sha256(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != _SHA256_RE_LENGTH:
        raise _fail(KnowledgeFailureCode.MALFORMED_SHA256, f"{field_name} is invalid")
    if any(character not in "0123456789abcdef" for character in value):
        raise _fail(KnowledgeFailureCode.MALFORMED_SHA256, f"{field_name} is not lowercase hex")
    return value


def _sequence(value: object, field_name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        raise _fail(KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC, f"{field_name} is invalid")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(None):
        raise _fail(KnowledgeFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    return value


def _timestamp_text(value: datetime) -> str:
    return value.strftime(UTC_TIMESTAMP_FORMAT)


def _ref(value: object, expected: RefKind | None, field_name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact HashBoundRef")
    if expected is not None and value.kind is not expected:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} has an unexpected ref kind")
    return value


def _refs(value: object, expected: RefKind | None, field_name: str) -> tuple[HashBoundRef, ...]:
    """Return an exact, ordered, duplicate-free ref tuple.

    Ordering is part of the canonical payload: two manifests that select the
    same objects must produce the same identity, and a hidden duplicate must not
    change a root while leaving the visible set unchanged.
    """

    if type(value) is not tuple:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact tuple")
    if len(value) > _MAX_REFS_PER_COLLECTION:
        raise _fail(KnowledgeFailureCode.RESOURCE_LIMIT_EXCEEDED, f"{field_name} exceeds the ref limit")
    items = tuple(_ref(item, expected, field_name) for item in value)
    keys = [f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}" for item in items]
    if len(set(keys)) != len(keys):
        raise _fail(KnowledgeFailureCode.DUPLICATE_REFERENCE, f"{field_name} contains a duplicate reference")
    if keys != sorted(keys):
        raise _fail(KnowledgeFailureCode.UNORDERED_REFERENCE, f"{field_name} is not canonically ordered")
    return items


def _exact_dict(value: object, fields: tuple[str, ...], field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact dict")
    if set(value) != set(fields) or any(type(key) is not str for key in value):
        raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, f"{field_name} field set is incomplete or unknown")
    return value


def _actor(value: object, field_name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact ActorIdentity")
    return value


# ---------------------------------------------------------------------------
# KnowledgeContext — the identity every component must share
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeContext:
    """Exact repository/policy/environment identity bound to a snapshot.

    Mix-and-match detection reduces to one rule: every component admitted into a
    snapshot, and the boundary that commits it, must carry byte-identical
    context. A component that is individually valid but belongs to a different
    repository revision, policy version or environment profile is refused.
    """

    schema_version: str
    repository_revision: str
    policy_version: str
    environment_profile_id: str

    def __post_init__(self) -> None:
        validate_knowledge_context(self)

    def to_dict(self) -> dict[str, object]:
        validate_knowledge_context(self)
        return {
            "schema_version": self.schema_version,
            "repository_revision": self.repository_revision,
            "policy_version": self.policy_version,
            "environment_profile_id": self.environment_profile_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> KnowledgeContext:
        data = _exact_dict(
            value,
            ("schema_version", "repository_revision", "policy_version", "environment_profile_id"),
            "knowledge_context",
        )
        return cls(
            data["schema_version"],
            data["repository_revision"],
            data["policy_version"],
            data["environment_profile_id"],
        )


def validate_knowledge_context(value: KnowledgeContext) -> None:
    if type(value) is not KnowledgeContext:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "knowledge context type is invalid")
    if value.schema_version != KNOWLEDGE_CONTEXT_V1 or type(value.schema_version) is not str:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "knowledge context schema is unknown")
    revision = _identifier(value.repository_revision, "repository_revision")
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise _fail(KnowledgeFailureCode.MALFORMED_IDENTIFIER, "repository_revision must be an exact commit sha")
    _identifier(value.policy_version, "policy_version")
    _identifier(value.environment_profile_id, "environment_profile_id")


def create_knowledge_context(
    *,
    repository_revision: str,
    policy_version: str,
    environment_profile_id: str,
) -> KnowledgeContext:
    return KnowledgeContext(
        KNOWLEDGE_CONTEXT_V1,
        repository_revision,
        policy_version,
        environment_profile_id,
    )


def require_same_context(left: KnowledgeContext, right: KnowledgeContext, *, subject: str) -> None:
    """Fail closed unless two components share byte-identical context."""

    validate_knowledge_context(left)
    validate_knowledge_context(right)
    if left.to_dict() != right.to_dict():
        raise _fail(
            KnowledgeFailureCode.MIX_AND_MATCH_DETECTED,
            f"{subject} belongs to a different repository/policy/environment context",
        )


# ---------------------------------------------------------------------------
# SnapshotRootSet — the coherent store roots pinned by one snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotRootSet:
    """One coherent set of store roots with their monotonic generations.

    Generations exist so a regression is detectable. A root hash alone proves
    content, not recency: an attacker or a faulty recovery can present an older
    but internally valid root. Pinning the generation alongside the hash is the
    same argument TUF makes when its snapshot metadata pins a version number for
    every other role.
    """

    schema_version: str
    library_root_sha256: str
    library_generation: int
    index_root_sha256: str
    index_generation: int
    lifecycle_root_sha256: str
    lifecycle_record_count: int

    def __post_init__(self) -> None:
        validate_snapshot_root_set(self)

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_root_set(self)
        return {
            "schema_version": self.schema_version,
            "library_root_sha256": self.library_root_sha256,
            "library_generation": self.library_generation,
            "index_root_sha256": self.index_root_sha256,
            "index_generation": self.index_generation,
            "lifecycle_root_sha256": self.lifecycle_root_sha256,
            "lifecycle_record_count": self.lifecycle_record_count,
        }

    @classmethod
    def from_dict(cls, value: object) -> SnapshotRootSet:
        data = _exact_dict(
            value,
            (
                "schema_version",
                "library_root_sha256",
                "library_generation",
                "index_root_sha256",
                "index_generation",
                "lifecycle_root_sha256",
                "lifecycle_record_count",
            ),
            "snapshot_root_set",
        )
        return cls(
            data["schema_version"],
            data["library_root_sha256"],
            data["library_generation"],
            data["index_root_sha256"],
            data["index_generation"],
            data["lifecycle_root_sha256"],
            data["lifecycle_record_count"],
        )


def validate_snapshot_root_set(value: SnapshotRootSet) -> None:
    if type(value) is not SnapshotRootSet:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root set type is invalid")
    if value.schema_version != SNAPSHOT_ROOT_SET_V1 or type(value.schema_version) is not str:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "root set schema is unknown")
    _sha256(value.library_root_sha256, "library_root_sha256")
    _sha256(value.index_root_sha256, "index_root_sha256")
    _sha256(value.lifecycle_root_sha256, "lifecycle_root_sha256")
    _sequence(value.library_generation, "library_generation")
    _sequence(value.index_generation, "index_generation")
    _sequence(value.lifecycle_record_count, "lifecycle_record_count")


def create_snapshot_root_set(
    *,
    library_root_sha256: str,
    library_generation: int,
    index_root_sha256: str,
    index_generation: int,
    lifecycle_root_sha256: str,
    lifecycle_record_count: int,
) -> SnapshotRootSet:
    return SnapshotRootSet(
        SNAPSHOT_ROOT_SET_V1,
        library_root_sha256,
        library_generation,
        index_root_sha256,
        index_generation,
        lifecycle_root_sha256,
        lifecycle_record_count,
    )


def detect_root_regression(current: SnapshotRootSet, *, prior: SnapshotRootSet) -> str | None:
    """Return the first regressing root name, or ``None`` when non-regressing.

    A generation that moves backwards is a rollback. A generation that stays
    equal while its root hash changes is a fork: the same version cannot have
    two contents, so it is reported as a regression too.
    """

    validate_snapshot_root_set(current)
    validate_snapshot_root_set(prior)
    checks = (
        ("library", current.library_generation, prior.library_generation, current.library_root_sha256, prior.library_root_sha256),
        ("index", current.index_generation, prior.index_generation, current.index_root_sha256, prior.index_root_sha256),
        ("lifecycle", current.lifecycle_record_count, prior.lifecycle_record_count, current.lifecycle_root_sha256, prior.lifecycle_root_sha256),
    )
    for name, current_generation, prior_generation, current_root, prior_root in checks:
        if current_generation < prior_generation:
            return name
        if current_generation == prior_generation and current_root != prior_root:
            return name
    return None


def detect_mixed_generation(current: SnapshotRootSet, *, prior: SnapshotRootSet) -> str | None:
    """Return a name when one root advanced while another stayed behind.

    A snapshot whose index root is new while its lifecycle root is old is the
    canonical mix-and-match shape: each component verifies on its own, but the
    combination never existed as a consistent state.
    """

    validate_snapshot_root_set(current)
    validate_snapshot_root_set(prior)
    advanced: list[str] = []
    unchanged: list[str] = []
    pairs = (
        ("library", current.library_generation, prior.library_generation),
        ("index", current.index_generation, prior.index_generation),
        ("lifecycle", current.lifecycle_record_count, prior.lifecycle_record_count),
    )
    for name, current_generation, prior_generation in pairs:
        if current_generation > prior_generation:
            advanced.append(name)
        else:
            unchanged.append(name)
    if advanced and unchanged and "lifecycle" in unchanged and "index" in advanced:
        return "index advanced while lifecycle stayed behind"
    return None


# ---------------------------------------------------------------------------
# SnapshotManifest — identity-bearing, without a self-asserted status
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class SnapshotManifest:
    """Immutable content-addressed manifest of one selected knowledge state.

    The payload deliberately carries no completeness status. Identity is derived
    from what was selected, not from a claim about its validity, so a producer
    cannot mint a manifest that declares itself complete.
    """

    schema_version: SchemaVersion
    snapshot_id: RecordId
    context: KnowledgeContext
    roots: SnapshotRootSet
    behavior_refs: tuple[HashBoundRef, ...]
    binding_refs: tuple[HashBoundRef, ...]
    attestation_refs: tuple[HashBoundRef, ...]
    admission_refs: tuple[HashBoundRef, ...]
    retrieval_decision_refs: tuple[HashBoundRef, ...]
    conflict_refs: tuple[HashBoundRef, ...]
    parent_snapshot_digest: str | None
    created_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotManifest:
        raise TypeError("SnapshotManifest is created only by create_snapshot_manifest")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_manifest(self)
        return {
            **_manifest_payload(self),
            "snapshot_id": self.snapshot_id.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        validate_snapshot_manifest(self)
        return _canonical(_manifest_payload(self))

    def payload_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def selected_refs(self) -> tuple[HashBoundRef, ...]:
        """Return every selected object ref in canonical order."""

        validate_snapshot_manifest(self)
        return self.behavior_refs + self.binding_refs


def _manifest_payload(value: SnapshotManifest) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "context": value.context.to_dict(),
        "roots": value.roots.to_dict(),
        "behavior_refs": [item.to_dict() for item in value.behavior_refs],
        "binding_refs": [item.to_dict() for item in value.binding_refs],
        "attestation_refs": [item.to_dict() for item in value.attestation_refs],
        "admission_refs": [item.to_dict() for item in value.admission_refs],
        "retrieval_decision_refs": [item.to_dict() for item in value.retrieval_decision_refs],
        "conflict_refs": [item.to_dict() for item in value.conflict_refs],
        "parent_snapshot_digest": value.parent_snapshot_digest,
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


def validate_snapshot_manifest(value: SnapshotManifest) -> None:
    if type(value) is not SnapshotManifest or getattr(value, "_trusted_seal", None) is not _MANIFEST_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "snapshot manifest is not factory sealed")
    if value.schema_version is not SchemaVersion.KNOWLEDGE_SNAPSHOT_V1:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "snapshot manifest schema is unknown")
    validate_knowledge_context(value.context)
    validate_snapshot_root_set(value.roots)
    _refs(value.behavior_refs, None, "behavior_refs")
    _refs(value.binding_refs, RefKind.BINDING, "binding_refs")
    _refs(value.attestation_refs, None, "attestation_refs")
    _refs(value.admission_refs, RefKind.GATE_DECISION, "admission_refs")
    _refs(value.retrieval_decision_refs, None, "retrieval_decision_refs")
    _refs(value.conflict_refs, None, "conflict_refs")
    _timestamp(value.created_at_utc, "created_at_utc")
    if value.parent_snapshot_digest is not None:
        _sha256(value.parent_snapshot_digest, "parent_snapshot_digest")
        if value.parent_snapshot_digest == value.snapshot_id.digest_sha256:
            raise _fail(KnowledgeFailureCode.ROLLBACK_DETECTED, "snapshot cannot be its own parent")
    if type(value.snapshot_id) is not RecordId or value.snapshot_id.domain is not IdentityDomain.KNOWLEDGE_SNAPSHOT:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "snapshot_id domain is invalid")
    try:
        validate_record_id(value.snapshot_id, canonical_bytes=_canonical(_manifest_payload(value)))
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "snapshot_id does not match its payload") from exc
    if not value.behavior_refs and not value.binding_refs:
        raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "manifest selects no behavior or binding object")


def create_snapshot_manifest(
    *,
    context: KnowledgeContext,
    roots: SnapshotRootSet,
    behavior_refs: tuple[HashBoundRef, ...],
    binding_refs: tuple[HashBoundRef, ...],
    attestation_refs: tuple[HashBoundRef, ...],
    admission_refs: tuple[HashBoundRef, ...],
    retrieval_decision_refs: tuple[HashBoundRef, ...],
    conflict_refs: tuple[HashBoundRef, ...],
    created_at_utc: datetime,
    parent_snapshot_id: RecordId | None = None,
) -> SnapshotManifest:
    """Build a frozen manifest and derive its content identity.

    ``parent_snapshot_id`` is accepted as a ``RecordId`` and stored as its
    digest. A ``RecordId`` is constructible only from the exact bytes that
    produced it, and a parent's bytes belong to a different committed
    transaction, so lineage travels as a digest the way TUF and table formats
    carry a parent pointer.
    """

    parent_digest: str | None = None
    if parent_snapshot_id is not None:
        if type(parent_snapshot_id) is not RecordId:
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "parent_snapshot_id must be an exact RecordId")
        if parent_snapshot_id.domain is not IdentityDomain.KNOWLEDGE_SNAPSHOT:
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "parent_snapshot_id domain is invalid")
        parent_digest = parent_snapshot_id.digest_sha256
    return _rebuild_manifest(
        context=context,
        roots=roots,
        behavior_refs=behavior_refs,
        binding_refs=binding_refs,
        attestation_refs=attestation_refs,
        admission_refs=admission_refs,
        retrieval_decision_refs=retrieval_decision_refs,
        conflict_refs=conflict_refs,
        created_at_utc=created_at_utc,
        parent_snapshot_digest=parent_digest,
    )


def _rebuild_manifest(
    *,
    context: KnowledgeContext,
    roots: SnapshotRootSet,
    behavior_refs: tuple[HashBoundRef, ...],
    binding_refs: tuple[HashBoundRef, ...],
    attestation_refs: tuple[HashBoundRef, ...],
    admission_refs: tuple[HashBoundRef, ...],
    retrieval_decision_refs: tuple[HashBoundRef, ...],
    conflict_refs: tuple[HashBoundRef, ...],
    created_at_utc: datetime,
    parent_snapshot_digest: str | None,
) -> SnapshotManifest:
    """Assemble a manifest from an already-validated lineage digest."""

    result = object.__new__(SnapshotManifest)
    object.__setattr__(result, "schema_version", SchemaVersion.KNOWLEDGE_SNAPSHOT_V1)
    object.__setattr__(result, "context", context)
    object.__setattr__(result, "roots", roots)
    object.__setattr__(result, "behavior_refs", behavior_refs)
    object.__setattr__(result, "binding_refs", binding_refs)
    object.__setattr__(result, "attestation_refs", attestation_refs)
    object.__setattr__(result, "admission_refs", admission_refs)
    object.__setattr__(result, "retrieval_decision_refs", retrieval_decision_refs)
    object.__setattr__(result, "conflict_refs", conflict_refs)
    object.__setattr__(result, "parent_snapshot_digest", parent_snapshot_digest)
    object.__setattr__(result, "created_at_utc", created_at_utc)
    object.__setattr__(result, "_trusted_seal", _MANIFEST_SEAL)
    object.__setattr__(
        result,
        "snapshot_id",
        compute_record_id(
            domain=IdentityDomain.KNOWLEDGE_SNAPSHOT,
            canonical_bytes=_canonical(_manifest_payload(result)),
        ),
    )
    validate_snapshot_manifest(result)
    return result


def snapshot_manifest_from_dict(value: object) -> SnapshotManifest:
    """Rebuild a manifest from its canonical payload and re-derive its identity.

    The committed payload carries no ``snapshot_id`` field at all. Identity is
    recomputed from the bytes, so an edited manifest cannot be restored under
    its original name and a stored identity can never contradict its content.
    """

    data = _exact_dict(
        value,
        (
            "schema_version",
            "context",
            "roots",
            "behavior_refs",
            "binding_refs",
            "attestation_refs",
            "admission_refs",
            "retrieval_decision_refs",
            "conflict_refs",
            "parent_snapshot_digest",
            "created_at_utc",
        ),
        "snapshot_manifest",
    )
    if data["schema_version"] != SchemaVersion.KNOWLEDGE_SNAPSHOT_V1.value:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "snapshot manifest schema is unknown")
    raw_parent = data["parent_snapshot_digest"]
    if raw_parent is not None:
        _sha256(raw_parent, "parent_snapshot_digest")
    created_raw = data["created_at_utc"]
    if type(created_raw) is not str:
        raise _fail(KnowledgeFailureCode.MALFORMED_TIMESTAMP, "created_at_utc is invalid")
    try:
        created = datetime.strptime(created_raw, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(KnowledgeFailureCode.MALFORMED_TIMESTAMP, "created_at_utc is unparseable") from exc
    result = _rebuild_manifest(
        context=KnowledgeContext.from_dict(data["context"]),
        roots=SnapshotRootSet.from_dict(data["roots"]),
        behavior_refs=_ref_tuple(data["behavior_refs"], "behavior_refs"),
        binding_refs=_ref_tuple(data["binding_refs"], "binding_refs"),
        attestation_refs=_ref_tuple(data["attestation_refs"], "attestation_refs"),
        admission_refs=_ref_tuple(data["admission_refs"], "admission_refs"),
        retrieval_decision_refs=_ref_tuple(data["retrieval_decision_refs"], "retrieval_decision_refs"),
        conflict_refs=_ref_tuple(data["conflict_refs"], "conflict_refs"),
        created_at_utc=created,
        parent_snapshot_digest=raw_parent,
    )
    return result


def compatibility_evidence_root(manifest: SnapshotManifest) -> str:
    """The root over the compatibility evidence this snapshot actually carries.

    Derived, not supplied. An earlier revision took this root as an argument to
    ``commit_atomic_snapshot_boundary``, checked that it looked like a sha256,
    and wrote it into the boundary — so the boundary attested a fact nobody had
    established, and a caller could name any digest at all.

    The manifest already holds the evidence: ``retrieval_decision_refs`` are the
    decisions that authorised the selection and ``conflict_refs`` are the records
    that qualified it. Chaining those in canonical order produces a value that
    *cannot* disagree with the manifest, which is a stronger property than a
    supplied value that is checked — there is no second source to diverge.

    The chain is domain-separated and order-sensitive on purpose: two snapshots
    holding the same evidence in a different order are different states, and a
    root that collapsed them would let one be presented as the other.
    """

    validate_snapshot_manifest(manifest)
    running = hashlib.sha256(COMPATIBILITY_EVIDENCE_ROOT_GENESIS).digest()
    for ref in manifest.retrieval_decision_refs + manifest.conflict_refs:
        running = hashlib.sha256(running + hashlib.sha256(_canonical(ref.to_dict())).digest()).digest()
    return running.hex()


class AdmissionHistoryRootPort(Protocol):
    """What a store must offer for this owner to confirm an admission root.

    Structural on purpose. §21 and §22 must not import each other — Patch 6.5
    exists to keep that cycle out — so this is declared here by shape and
    satisfied elsewhere. ``FileAdmissionJournal`` already answers both methods,
    and neither module names the other.

    ``extends`` rather than equality: the journal legitimately grows between the
    moment a snapshot's admission evidence was gathered and the moment its
    boundary commits. What must hold is that the root being recorded is still an
    ancestor of the committed history, which is what makes a rollback detectable
    while an ordinary append stays valid.

    **How an implementation reports a failure.** This owner cannot classify one
    for it: it may not import the admission package, so it cannot name that
    package's exception types, and an owner that cannot name a condition must not
    claim to recognise it. So the contract is stated here and the implementation
    must speak this owner's vocabulary:

    * the store is unreachable → ``KnowledgeViolation(STORE_UNAVAILABLE)``;
    * the store is present and is not this journal → ``ADMISSION_HISTORY_CORRUPT``;
    * the anchor is simply not an ancestor → return ``False``.

    Anything else that escapes is reported as ``ADMISSION_HISTORY_UNCLASSIFIED``.
    An earlier revision reported it as an outage instead, which turned a corrupt
    store into a retryable one — the substitution NR-10 exists to forbid, and one
    this module's own docstrings were arguing against at the time.
    """

    def current_anchor(self) -> str: ...

    def extends(self, anchor: str) -> bool: ...


@runtime_checkable
class RootObservationFencePort(Protocol):
    """What a fence must offer for a root observation to be one moment.

    Structural for the same reason `AdmissionHistoryRootPort` is: the concrete
    fence is an adapter of `persistence`, and this owner may not import it.
    `FileSnapshotFence` already answers all three methods.

    **Why §21 needs a fence at all.** A root set is three roots and three
    generations taken from three stores. Reading them through one callable makes
    one call, and one call is not one instant — the phrase is `coordination`'s
    own, written about the §22 authority heads, and the same defect stood on this
    side untouched. A library root from before a publish beside a lifecycle root
    from after it describes no world that ever existed, and every value in it
    validates, which is exactly why validating the result cannot detect it.

    The epoch is compared across the read. If it moved, some store mutated while
    the roots were being gathered and the observation is refused rather than
    repaired: there is no way to tell which of the three values came from before
    the change.

    This detects tearing; it does not prevent it. A fence backed by a real lock
    would make tearing impossible, one backed by a counter makes it visible, and
    which is in use is a property of the injected port rather than of this module
    — so this module claims only the weaker of the two.
    """

    def acquire_lease(self) -> str: ...

    def current_epoch(self) -> int: ...

    def release_lease(self, lease_id: str) -> None: ...


def require_root_observation_fence(value: object) -> RootObservationFencePort:
    if not isinstance(value, RootObservationFencePort):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root fence does not implement the observation port")
    for name in ("acquire_lease", "current_epoch", "release_lease"):
        if not callable(getattr(value, name, None)):
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"root fence is missing {name}")
    return value


def _confirm_admission_root(admission_journal: object, *, admission_root_sha256: str) -> None:
    """Refuse an admission root the journal does not confirm.

    Four outcomes, kept apart because NR-10 forbids merging them, and one of them
    exists because this owner is honest about what it cannot tell:

    * a port that reports in this owner's vocabulary keeps its own classification
      — ``KnowledgeViolation`` passes through untouched;
    * ``False`` is a definite refusal, and the boundary would otherwise attest an
      admission history that never existed;
    * a non-``bool`` answer is a broken adapter;
    * anything else that escapes is **unclassified**, not an outage. This owner
      cannot import the admission package and therefore cannot recognise its
      exception types, so calling an unrecognised failure "the store was
      unreachable" is a claim it is in no position to make. It was one, until
      this round.
    """

    for name in ("current_anchor", "extends"):
        if not callable(getattr(admission_journal, name, None)):
            raise _fail(
                KnowledgeFailureCode.TYPE_MISMATCH,
                f"admission journal is missing {name}",
            )
    try:
        extends = admission_journal.extends(admission_root_sha256)
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED,
            "the admission journal failed in a way this owner cannot classify",
        ) from exc
    if type(extends) is not bool:
        raise _fail(
            KnowledgeFailureCode.TYPE_MISMATCH,
            "admission journal did not answer extends with an exact bool",
        )
    if not extends:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_ROOT_UNCONFIRMED,
            "the admission root is not an ancestor of the committed admission history",
        )


def _ref_tuple(value: object, field_name: str) -> tuple[HashBoundRef, ...]:
    if type(value) is not list:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact list")
    return tuple(HashBoundRef.from_dict(item) for item in value)


# ---------------------------------------------------------------------------
# SnapshotCompletenessDecision — authoritative, never self-asserted
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class SnapshotCompletenessDecision:
    """An independent evaluator's exact verdict over one committed root set."""

    schema_version: SchemaVersion
    decision_id: RecordId
    snapshot_id: RecordId
    manifest_sha256: str
    context: KnowledgeContext
    roots: SnapshotRootSet
    status: SnapshotCompletenessStatus
    authority_identity: AuthorityIdentity
    authority_role: AuthorityRole
    evaluated_at_utc: datetime
    detail: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotCompletenessDecision:
        raise TypeError("SnapshotCompletenessDecision is produced only by a configured evaluator")

    def to_dict(self) -> dict[str, object]:
        validate_completeness_decision(self)
        return {**_decision_payload(self), "decision_id": self.decision_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_completeness_decision(self)
        return _canonical(_decision_payload(self))


def _decision_payload(value: SnapshotCompletenessDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "snapshot_digest": value.snapshot_id.digest_sha256,
        "manifest_sha256": value.manifest_sha256,
        "context": value.context.to_dict(),
        "roots": value.roots.to_dict(),
        "status": value.status.value,
        "authority_identity": value.authority_identity.to_dict(),
        "authority_role": value.authority_role.value,
        "evaluated_at_utc": _timestamp_text(value.evaluated_at_utc),
        "detail": value.detail,
    }


def validate_completeness_decision(value: SnapshotCompletenessDecision) -> None:
    if type(value) is not SnapshotCompletenessDecision or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "completeness decision is not evaluator sealed")
    if value.schema_version is not SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision schema is unknown")
    if type(value.status) is not SnapshotCompletenessStatus:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "completeness status is not typed")
    if type(value.authority_identity) is not AuthorityIdentity or type(value.authority_role) is not AuthorityRole:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision authority is invalid")
    if type(value.snapshot_id) is not RecordId or value.snapshot_id.domain is not IdentityDomain.KNOWLEDGE_SNAPSHOT:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision subject domain is invalid")
    _sha256(value.manifest_sha256, "manifest_sha256")
    validate_knowledge_context(value.context)
    validate_snapshot_root_set(value.roots)
    _timestamp(value.evaluated_at_utc, "evaluated_at_utc")
    if type(value.detail) is not str or not value.detail or len(value.detail) > 256:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision detail is invalid")
    try:
        validate_record_id(value.decision_id, canonical_bytes=_canonical(_decision_payload(value)))
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "decision_id does not match its payload") from exc


class ConfiguredSnapshotEvaluator:
    """Write-once capability object holding the completeness evaluator's ports.

    The evaluator is independent by construction: its authority identity must
    differ from the snapshot producer and from the consumer that will use the
    result. Store access arrives as injected callables, so this owner needs no
    import of the library, lifecycle or admission owners.
    """

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_configuration_frozen", False):
            raise AttributeError("configured snapshot evaluator is write-once")
        object.__setattr__(self, name, value)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _EVALUATOR_SEAL or kwargs or len(args) != 9:
            raise TypeError("ConfiguredSnapshotEvaluator is factory-created")
        (
            self._authority_handle,
            self._authority_identity,
            self._authority_role,
            self._trusted_clock,
            self._observed_roots_provider,
            self._root_fence,
            self._ref_resolver,
            self._consumability_probe,
            self._producer_actor,
        ) = args
        self._configuration_frozen = True

    @property
    def authority_identity(self) -> AuthorityIdentity:
        return self._authority_identity

    @property
    def authority_role(self) -> AuthorityRole:
        return self._authority_role

    @property
    def producer_actor(self) -> ActorIdentity:
        return self._producer_actor


def configure_snapshot_evaluator(
    *,
    authority_handle: Stage4AuthorityHandle,
    authority_identity: AuthorityIdentity,
    authority_role: AuthorityRole,
    trusted_clock: Callable[[], datetime],
    observed_roots_provider: Callable[[], SnapshotRootSet],
    root_fence: RootObservationFencePort,
    ref_resolver: Callable[[HashBoundRef], bool],
    consumability_probe: Callable[[HashBoundRef], bool],
    producer_actor: ActorIdentity,
) -> ConfiguredSnapshotEvaluator:
    require_stage4_authority_handle(authority_handle)
    if type(authority_identity) is not AuthorityIdentity or type(authority_role) is not AuthorityRole:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator authority is invalid")
    if not callable(trusted_clock) or not callable(observed_roots_provider):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator providers must be callable")
    # Required, with no default. An evaluator that could be configured without a
    # fence would read three roots at three moments and report the result as one
    # observation, which is the condition this round exists to make detectable —
    # and an optional barrier is the NR-09 bypass in the shape of a default.
    require_root_observation_fence(root_fence)
    if not callable(ref_resolver) or not callable(consumability_probe):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator resolvers must be callable")
    producer = _actor(producer_actor, "producer_actor")
    if authority_identity.value == producer.value:
        raise _fail(
            KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "completeness evaluator cannot be the snapshot producer",
        )
    result = ConfiguredSnapshotEvaluator(
        authority_handle,
        authority_identity,
        authority_role,
        trusted_clock,
        observed_roots_provider,
        root_fence,
        ref_resolver,
        consumability_probe,
        producer,
        _seal=_EVALUATOR_SEAL,
    )
    return result


def _make_decision(
    evaluator: ConfiguredSnapshotEvaluator,
    manifest: SnapshotManifest,
    status: SnapshotCompletenessStatus,
    detail: str,
) -> SnapshotCompletenessDecision:
    result = object.__new__(SnapshotCompletenessDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1)
    object.__setattr__(result, "snapshot_id", manifest.snapshot_id)
    object.__setattr__(result, "manifest_sha256", manifest.payload_sha256())
    object.__setattr__(result, "context", manifest.context)
    object.__setattr__(result, "roots", manifest.roots)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "authority_identity", evaluator.authority_identity)
    object.__setattr__(result, "authority_role", evaluator.authority_role)
    object.__setattr__(result, "evaluated_at_utc", _timestamp(evaluator._trusted_clock(), "evaluated_at_utc"))
    object.__setattr__(result, "detail", detail)
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    object.__setattr__(
        result,
        "decision_id",
        compute_record_id(
            domain=IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION,
            canonical_bytes=_canonical(_decision_payload(result)),
        ),
    )
    validate_completeness_decision(result)
    return result


def _fenced_root_observation(evaluator: ConfiguredSnapshotEvaluator) -> object:
    """Observe the roots inside one lease and report whether the epoch moved.

    The lease is advisory and is not asked to be more: it names one read window
    so that the two epoch readings provably bracket the same observation. What
    makes the answer trustworthy is the comparison, not exclusion — a fence that
    excluded a concurrent writer would add a way to deadlock a root read without
    adding a property this algorithm relies on.

    The release runs in a ``finally`` because a lease left held by a failing read
    would make the *next* read refuse for a reason that has nothing to do with it,
    and a barrier that misattributes its own faults teaches an operator to
    distrust it.
    """

    fence = evaluator._root_fence
    lease = fence.acquire_lease()
    try:
        before = fence.current_epoch()
        observed = evaluator._observed_roots_provider()
        after = fence.current_epoch()
    finally:
        fence.release_lease(lease)
    if type(before) is not int or type(after) is not int:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root fence reported a non-integer epoch")
    if after < before:
        # A monotonic counter that went backwards is not a torn read: it means the
        # fence itself was replaced or rolled back, and treating that as ordinary
        # interference would invite a retry against a fence that can no longer
        # answer the question.
        raise _fail(
            KnowledgeFailureCode.STORE_UNAVAILABLE,
            "the root fence epoch went backwards and can no longer bracket a read",
        )
    if after != before:
        raise _fail(
            KnowledgeFailureCode.OBSERVATION_TORN,
            "an authority store mutated while the roots were being observed",
        )
    return observed


def evaluate_snapshot_completeness(
    evaluator: ConfiguredSnapshotEvaluator,
    *,
    manifest: SnapshotManifest,
    prior_roots: SnapshotRootSet | None = None,
) -> SnapshotCompletenessDecision:
    """Compute the authoritative completeness verdict over a manifest.

    Checks run in fail-closed order: an unavailable store is never downgraded to
    an optimistic pass, and the first definite failure wins. ``COMPLETE`` is
    reachable only when every required reference resolves, no root regressed,
    no generation mix is present and no selected object is blocked by lifecycle.
    """

    if type(evaluator) is not ConfiguredSnapshotEvaluator:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator is not a configured snapshot evaluator")
    validate_snapshot_manifest(manifest)

    try:
        observed = _fenced_root_observation(evaluator)
    except KnowledgeViolation:
        # A torn observation and a fence that went backwards both arrive here and
        # both propagate. Neither is a completeness verdict: the evaluation did not
        # happen, so no decision is produced and nothing downstream can commit one.
        raise
    except Exception:
        return _make_decision(
            evaluator,
            manifest,
            SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE,
            "required store roots are unavailable",
        )
    if type(observed) is not SnapshotRootSet:
        return _make_decision(
            evaluator,
            manifest,
            SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE,
            "observed store roots are not an exact root set",
        )
    validate_snapshot_root_set(observed)

    if observed.to_dict() != manifest.roots.to_dict():
        regression = detect_root_regression(manifest.roots, prior=observed)
        if regression is not None:
            return _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.ROLLBACK_DETECTED,
                f"manifest {regression} root is older than the observed store root",
            )
        return _make_decision(
            evaluator,
            manifest,
            SnapshotCompletenessStatus.INCONSISTENT_REFERENCES,
            "manifest roots differ from the observed store roots",
        )

    if prior_roots is not None:
        regression = detect_root_regression(manifest.roots, prior=prior_roots)
        if regression is not None:
            return _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.ROLLBACK_DETECTED,
                f"{regression} root regressed against the prior committed snapshot",
            )
        mixed = detect_mixed_generation(manifest.roots, prior=prior_roots)
        if mixed is not None:
            return _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED,
                mixed,
            )

    # Every ref collection the manifest carries, not a subset of them. An
    # earlier revision resolved four of the six: ``retrieval_decision_refs`` and
    # ``conflict_refs`` were never looked up, so a snapshot reached COMPLETE —
    # the one status that admits execution — while the decisions that authorised
    # its selection and the conflict records that qualified it dangled.
    #
    # The two additions share ``INCOMPLETE_COMPATIBILITY_DATA`` with the
    # admission refs rather than getting members of their own. §21 fixes this
    # vocabulary as closed, and amending a normative enum is not a repair's
    # business; the status names the class of missing evidence while the
    # decision's ``detail`` names the exact collection, which is what an
    # incident needs to route.
    required = (
        ("behavior", manifest.behavior_refs, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE),
        ("binding", manifest.binding_refs, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_BINDING),
        ("attestation", manifest.attestation_refs, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION),
        ("admission", manifest.admission_refs, SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA),
        ("retrieval decision", manifest.retrieval_decision_refs, SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA),
        ("conflict", manifest.conflict_refs, SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA),
    )
    for name, refs, missing_status in required:
        for ref in refs:
            try:
                resolved = evaluator._ref_resolver(ref)
            except KnowledgeViolation:
                raise
            except Exception:
                return _make_decision(
                    evaluator,
                    manifest,
                    missing_status,
                    f"{name} reference could not be resolved",
                )
            if resolved is not True:
                return _make_decision(
                    evaluator,
                    manifest,
                    missing_status,
                    f"{name} reference is unresolved",
                )

    for ref in manifest.selected_refs():
        try:
            consumable = evaluator._consumability_probe(ref)
        except KnowledgeViolation:
            raise
        except Exception:
            return _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE,
                "lifecycle state for a selected object is unresolvable",
            )
        if consumable is not True:
            return _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE,
                "a selected object is not consumable in its lifecycle state",
            )

    return _make_decision(
        evaluator,
        manifest,
        SnapshotCompletenessStatus.COMPLETE,
        "all required references resolved over a coherent root set",
    )


# ---------------------------------------------------------------------------
# AtomicSnapshotBoundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class AtomicSnapshotBoundary:
    """The committed transaction that makes one snapshot exist for consumers."""

    schema_version: SchemaVersion
    atomic_boundary_id: RecordId
    transaction_id: str
    context: KnowledgeContext
    roots: SnapshotRootSet
    admission_root_sha256: str
    compatibility_evidence_root_sha256: str
    manifest_ref: HashBoundRef
    manifest_sha256: str
    completeness_decision_ref: HashBoundRef
    start_sequence: int
    commit_sequence: int
    parent_boundary_digest: str | None
    commit_marker: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AtomicSnapshotBoundary:
        raise TypeError("AtomicSnapshotBoundary is created only by commit_atomic_snapshot_boundary")

    def to_dict(self) -> dict[str, object]:
        validate_atomic_boundary(self)
        return {**_boundary_payload(self), "atomic_boundary_id": self.atomic_boundary_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_atomic_boundary(self)
        return _canonical(_boundary_payload(self))


def _boundary_payload(value: AtomicSnapshotBoundary) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "transaction_id": value.transaction_id,
        "context": value.context.to_dict(),
        "roots": value.roots.to_dict(),
        "admission_root_sha256": value.admission_root_sha256,
        "compatibility_evidence_root_sha256": value.compatibility_evidence_root_sha256,
        "manifest_ref": value.manifest_ref.to_dict(),
        "manifest_sha256": value.manifest_sha256,
        "completeness_decision_ref": value.completeness_decision_ref.to_dict(),
        "start_sequence": value.start_sequence,
        "commit_sequence": value.commit_sequence,
        "parent_boundary_digest": value.parent_boundary_digest,
        "commit_marker": value.commit_marker,
    }


def validate_atomic_boundary(value: AtomicSnapshotBoundary) -> None:
    if type(value) is not AtomicSnapshotBoundary or getattr(value, "_trusted_seal", None) is not _BOUNDARY_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "atomic boundary is not commit sealed")
    if value.schema_version is not SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "atomic boundary schema is unknown")
    _identifier(value.transaction_id, "transaction_id")
    validate_knowledge_context(value.context)
    validate_snapshot_root_set(value.roots)
    _sha256(value.admission_root_sha256, "admission_root_sha256")
    _sha256(value.compatibility_evidence_root_sha256, "compatibility_evidence_root_sha256")
    _ref(value.manifest_ref, RefKind.KNOWLEDGE_SNAPSHOT, "manifest_ref")
    _ref(value.completeness_decision_ref, RefKind.ATOMIC_BOUNDARY, "completeness_decision_ref")
    _sha256(value.manifest_sha256, "manifest_sha256")
    _sha256(value.commit_marker, "commit_marker")
    start = _sequence(value.start_sequence, "start_sequence")
    commit = _sequence(value.commit_sequence, "commit_sequence")
    # A boundary's own window must advance. Continuity *across* boundaries is
    # enforced at commit, where the parent is in hand: §21 specifies a monotonic
    # transaction range in which gaps are detected, so a child begins exactly
    # where its parent ended.
    if commit <= start:
        raise _fail(KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC, "commit_sequence must exceed start_sequence")
    if value.manifest_ref.sha256 != value.manifest_sha256:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "manifest_ref hash differs from manifest_sha256")
    if value.parent_boundary_digest is not None:
        _sha256(value.parent_boundary_digest, "parent_boundary_digest")
    try:
        validate_record_id(value.atomic_boundary_id, canonical_bytes=_canonical(_boundary_payload(value)))
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "atomic_boundary_id does not match its payload") from exc


def _make_boundary(
    *,
    transaction_id: str,
    context: KnowledgeContext,
    roots: SnapshotRootSet,
    admission_root_sha256: str,
    compatibility_evidence_root_sha256: str,
    manifest_ref: HashBoundRef,
    manifest_sha256: str,
    completeness_decision_ref: HashBoundRef,
    start_sequence: int,
    commit_sequence: int,
    parent_boundary_digest: str | None,
    commit_marker: str,
) -> AtomicSnapshotBoundary:
    result = object.__new__(AtomicSnapshotBoundary)
    object.__setattr__(result, "schema_version", SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1)
    object.__setattr__(result, "transaction_id", transaction_id)
    object.__setattr__(result, "context", context)
    object.__setattr__(result, "roots", roots)
    object.__setattr__(result, "admission_root_sha256", admission_root_sha256)
    object.__setattr__(result, "compatibility_evidence_root_sha256", compatibility_evidence_root_sha256)
    object.__setattr__(result, "manifest_ref", manifest_ref)
    object.__setattr__(result, "manifest_sha256", manifest_sha256)
    object.__setattr__(result, "completeness_decision_ref", completeness_decision_ref)
    object.__setattr__(result, "start_sequence", start_sequence)
    object.__setattr__(result, "commit_sequence", commit_sequence)
    object.__setattr__(result, "parent_boundary_digest", parent_boundary_digest)
    object.__setattr__(result, "commit_marker", commit_marker)
    object.__setattr__(result, "_trusted_seal", _BOUNDARY_SEAL)
    object.__setattr__(
        result,
        "atomic_boundary_id",
        compute_record_id(
            domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            canonical_bytes=_canonical(_boundary_payload(result)),
        ),
    )
    validate_atomic_boundary(result)
    return result


def _manifest_ref(manifest: SnapshotManifest) -> HashBoundRef:
    # ``ref_id`` carries the record identity digest rather than the full
    # ``RecordId.value``: the identity domain is already pinned by ``kind`` and
    # ``schema_id``, and the textual record id contains a separator that the
    # canonical ref-id grammar does not admit.
    payload = manifest.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.KNOWLEDGE_SNAPSHOT,
        ref_id=manifest.snapshot_id.digest_sha256,
        schema_id=SchemaVersion.KNOWLEDGE_SNAPSHOT_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def _decision_ref(decision: SnapshotCompletenessDecision) -> HashBoundRef:
    payload = decision.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ATOMIC_BOUNDARY,
        ref_id=decision.decision_id.digest_sha256,
        schema_id=SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def atomic_boundary_ref(boundary: AtomicSnapshotBoundary) -> HashBoundRef:
    """The hash-bound reference by which a committed boundary is named elsewhere.

    §22's consumption gate decides against a ``boundary_ref`` and asks a probe
    whether that boundary is committed. Until this existed there was no
    projection from a real boundary to that reference, so every caller built one
    by hand — and a reference assembled by the party asking the question is not a
    reference to anything in particular.

    The name lives here because the boundary is this owner's object. Deriving it
    anywhere else would put the naming of a §21 record in a module that cannot
    see what the record is, which is how a write side and a read side come to
    call the same thing by two names.
    """

    validate_atomic_boundary(boundary)
    payload = boundary.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ATOMIC_BOUNDARY,
        ref_id=boundary.atomic_boundary_id.digest_sha256,
        schema_id=SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def commit_atomic_snapshot_boundary(
    root: Path,
    *,
    transaction_id: str,
    manifest: SnapshotManifest,
    decision: SnapshotCompletenessDecision,
    admission_root_sha256: str,
    admission_journal: AdmissionHistoryRootPort,
    start_sequence: int,
    commit_sequence: int,
    parent_boundary: AtomicSnapshotBoundary | None = None,
) -> AtomicSnapshotBoundary:
    """Durably commit one boundary, making its snapshot visible exactly once.

    Every consistency check runs before anything is staged, and the terminal
    commit marker is written last. A crash before the marker leaves a staged
    transaction that no consumer can open.

    **The two roots are no longer taken on trust.** Both used to arrive as bare
    strings whose only check was sha256 *shape*, so the boundary attested two
    facts that nothing had established. The compatibility evidence root is now
    derived from the manifest's own evidence rather than supplied, and the
    admission root must be confirmed by the journal that owns it.

    **On sequence numbers.** ``start_sequence`` must equal the parent's
    ``commit_sequence`` exactly. §21 specifies these as a *monotonic transaction
    range* with "gaps/forks/rollback detected", and a gap that is accepted is a
    gap that is not detected.

    An earlier revision of this module argued the opposite: that lineage travels
    in ``parent_boundary_digest``, so the numbers need only order. That is wrong
    in the part that matters — the digest proves *which* parent, not that nothing
    went unrecorded between parent and child. Contiguity is what makes a missing
    number mean a missing boundary.

    TUF, which §21 names as the source of its anti-rollback model, draws exactly
    this line by role: a hash-chained trust root must advance by precisely one
    and the client must walk every intermediate version, while an unchained role
    like timestamp needs only to increase. An ``AtomicSnapshotBoundary`` carries
    a parent digest, so it is the chained kind.
    """

    validate_snapshot_manifest(manifest)
    validate_completeness_decision(decision)
    _identifier(transaction_id, "transaction_id")
    _sha256(admission_root_sha256, "admission_root_sha256")
    _confirm_admission_root(admission_journal, admission_root_sha256=admission_root_sha256)
    compatibility_evidence_root_sha256 = compatibility_evidence_root(manifest)

    if decision.snapshot_id.value != manifest.snapshot_id.value:
        raise _fail(KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH, "decision does not describe this manifest")
    if decision.manifest_sha256 != manifest.payload_sha256():
        raise _fail(KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH, "decision was made over different manifest bytes")
    require_same_context(decision.context, manifest.context, subject="completeness decision")
    if decision.roots.to_dict() != manifest.roots.to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "decision root set differs from the manifest root set")
    try:
        require_snapshot_status_admits_execution(decision.status)
    except ContractViolation as exc:
        raise _fail(
            KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED,
            f"completeness status {decision.status.value} does not admit a commit",
        ) from exc

    if parent_boundary is not None:
        validate_atomic_boundary(parent_boundary)
        require_same_context(parent_boundary.context, manifest.context, subject="parent boundary")
        if start_sequence != parent_boundary.commit_sequence:
            raise _fail(
                KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC,
                "start_sequence must continue the parent range exactly, without a gap",
            )
        regression = detect_root_regression(manifest.roots, prior=parent_boundary.roots)
        if regression is not None:
            raise _fail(
                KnowledgeFailureCode.ROLLBACK_DETECTED,
                f"{regression} root regressed against the parent boundary",
            )
        if manifest.parent_snapshot_digest is None:
            raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "derived snapshot must declare its parent")
        # Declaring *a* parent is not descending from *this* one. Without this
        # comparison a child could name one lineage while being chained onto
        # another, and every other check would pass: the contexts match, the
        # roots do not regress, and round 17's contiguity rule lines the sequence
        # numbers up exactly. That is the fork §21 claims to detect, and the
        # sequence rule made it harder to see rather than closing it.
        #
        # ``manifest_ref.ref_id`` is the parent snapshot's identity digest —
        # ``_manifest_ref`` puts it there deliberately — so the two values are
        # the same fact and comparing them needs nothing new to be recorded.
        if manifest.parent_snapshot_digest != parent_boundary.manifest_ref.ref_id:
            raise _fail(
                KnowledgeFailureCode.LINEAGE_MISMATCH,
                "manifest declares a parent other than the boundary it extends",
            )
    elif manifest.parent_snapshot_digest is not None:
        # The other half of the same rule. A manifest claiming descent while
        # being committed as a genesis boundary would put an unverifiable parent
        # into the permanent record: nothing here can confirm that snapshot ever
        # existed, and a lineage that cannot be walked is not a lineage.
        raise _fail(
            KnowledgeFailureCode.LINEAGE_MISMATCH,
            "manifest declares a parent but is being committed without one",
        )

    ensure_directory(root)
    if committed_transaction_exists(root, transaction_id=transaction_id):
        raise _fail(KnowledgeFailureCode.TRANSACTION_ID_REUSED, "transaction id is already committed")

    manifest_bytes = manifest.canonical_bytes()
    decision_bytes = decision.canonical_bytes()
    boundary = _make_boundary(
        transaction_id=transaction_id,
        context=manifest.context,
        roots=manifest.roots,
        admission_root_sha256=admission_root_sha256,
        compatibility_evidence_root_sha256=compatibility_evidence_root_sha256,
        manifest_ref=_manifest_ref(manifest),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        completeness_decision_ref=_decision_ref(decision),
        start_sequence=start_sequence,
        commit_sequence=commit_sequence,
        parent_boundary_digest=None if parent_boundary is None else parent_boundary.atomic_boundary_id.digest_sha256,
        commit_marker=hashlib.sha256(manifest_bytes + decision_bytes).hexdigest(),
    )
    boundary_bytes = boundary.canonical_bytes()

    try:
        staged = stage_snapshot_transaction(
            root,
            transaction_id=transaction_id,
            members={
                MANIFEST_MEMBER_NAME: manifest_bytes,
                DECISION_MEMBER_NAME: decision_bytes,
                BOUNDARY_MEMBER_NAME: boundary_bytes,
            },
        )
        commit_snapshot_transaction(
            root,
            transaction_id=transaction_id,
            members=staged,
            boundary_id=boundary.atomic_boundary_id.value,
            marker_sha256=boundary.commit_marker,
        )
    except PersistenceViolation as exc:
        raise _fail(KnowledgeFailureCode.STORE_UNAVAILABLE, "boundary commit could not be durably recorded") from exc
    return boundary


# ---------------------------------------------------------------------------
# Restore and consumer-side revalidation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class UsableKnowledgeSnapshot:
    """The only shape a consumer may execute against.

    It exists only when a committed boundary, its exact manifest bytes and a
    ``COMPLETE`` authoritative decision agree. ``completeness_status`` is exposed
    here, read from the authoritative decision rather than from the manifest.
    """

    boundary: AtomicSnapshotBoundary
    manifest: SnapshotManifest
    decision: SnapshotCompletenessDecision
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> UsableKnowledgeSnapshot:
        raise TypeError("UsableKnowledgeSnapshot is produced only by open_usable_snapshot")

    @property
    def snapshot_id(self) -> RecordId:
        return self.manifest.snapshot_id

    @property
    def atomic_boundary_id(self) -> RecordId:
        return self.boundary.atomic_boundary_id

    @property
    def completeness_status(self) -> SnapshotCompletenessStatus:
        return self.decision.status

    def executable_refs(self, *, consumability_probe: Callable[[HashBoundRef], bool]) -> tuple[HashBoundRef, ...]:
        """Return selected refs still consumable at time of use.

        The manifest keeps every selected ref as audit history; an object that
        became revoked or quarantined after the commit is excluded here rather
        than erased there.
        """

        if not callable(consumability_probe):
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "consumability probe must be callable")
        usable: list[HashBoundRef] = []
        for ref in self.manifest.selected_refs():
            try:
                allowed = consumability_probe(ref)
            except KnowledgeViolation:
                raise
            except Exception as exc:
                raise _fail(
                    KnowledgeFailureCode.UNRESOLVED_REFERENCE,
                    "lifecycle state for a selected object is unresolvable",
                ) from exc
            if allowed is True:
                usable.append(ref)
        return tuple(usable)


def _make_usable(
    boundary: AtomicSnapshotBoundary,
    manifest: SnapshotManifest,
    decision: SnapshotCompletenessDecision,
) -> UsableKnowledgeSnapshot:
    result = object.__new__(UsableKnowledgeSnapshot)
    object.__setattr__(result, "boundary", boundary)
    object.__setattr__(result, "manifest", manifest)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "_trusted_seal", _USABLE_SEAL)
    return result


def _decode_member(value: bytes, field_name: str) -> object:
    try:
        return decode_stage4_canonical_bytes(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except Exception as exc:
        raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, f"{field_name} bytes are undecodable") from exc


def open_usable_snapshot(
    root: Path,
    *,
    transaction_id: str,
    expected_boundary_id: RecordId | None = None,
) -> UsableKnowledgeSnapshot:
    """Restore a committed snapshot and re-verify it end to end.

    Recovery recomputes every identity from committed bytes. A staged but
    unmarked transaction, a mutated member, a decision that is not ``COMPLETE``
    or a boundary whose roots disagree with its manifest all fail closed; none
    of them is repaired by substituting an older root.
    """

    try:
        marker, members = read_committed_snapshot_transaction(root, transaction_id=transaction_id)
    except PersistenceViolation as exc:
        if "commit marker is absent" in exc.detail:
            raise _fail(
                KnowledgeFailureCode.COMMIT_MARKER_ABSENT,
                "snapshot has no terminal commit marker and does not exist",
            ) from exc
        if exc.failure_code is PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED:
            # Committed bytes that no longer match their recorded digest are
            # corruption, not absence. Recovery reports it rather than falling
            # back to an older root that still verifies.
            raise _fail(
                KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED,
                "committed snapshot bytes do not match their recorded digests",
            ) from exc
        raise _fail(KnowledgeFailureCode.STORE_UNAVAILABLE, "committed snapshot could not be read") from exc

    if set(members) != {MANIFEST_MEMBER_NAME, DECISION_MEMBER_NAME, BOUNDARY_MEMBER_NAME}:
        raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "committed transaction member set is incomplete")

    manifest = snapshot_manifest_from_dict(_decode_member(members[MANIFEST_MEMBER_NAME], "manifest"))
    decision = _restore_decision(_decode_member(members[DECISION_MEMBER_NAME], "decision"), manifest=manifest)
    boundary = _restore_boundary(_decode_member(members[BOUNDARY_MEMBER_NAME], "boundary"))

    if marker["boundary_id"] != boundary.atomic_boundary_id.value:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "commit marker names a different boundary")
    if marker["marker_sha256"] != boundary.commit_marker:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "commit marker hash differs from the boundary")
    if boundary.transaction_id != transaction_id:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "boundary transaction id differs")
    # There is deliberately no separate ``manifest_sha256`` comparison against
    # the member bytes. The marker below binds the concatenation of manifest and
    # decision, so manifest bytes that changed necessarily change it too — the
    # narrower check could never fire on its own, and it survived its own removal
    # in the campaign for exactly that reason. What it appeared to add was a more
    # precise failure code, and a code that cannot be reached is not precision.
    # The manifest is still bound to this boundary: ``validate_atomic_boundary``
    # ties ``manifest_ref.sha256`` to ``manifest_sha256``, and the identity check
    # further down ties ``manifest_ref.ref_id`` to the restored manifest.
    if boundary.commit_marker != hashlib.sha256(
        members[MANIFEST_MEMBER_NAME] + members[DECISION_MEMBER_NAME]
    ).hexdigest():
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "committed manifest or decision bytes are not the ones this boundary marked",
        )
    if expected_boundary_id is not None and expected_boundary_id.value != boundary.atomic_boundary_id.value:
        raise _fail(
            KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS,
            "restored boundary differs from the boundary bound to this attempt",
        )

    require_same_context(boundary.context, manifest.context, subject="restored boundary")
    require_same_context(decision.context, manifest.context, subject="restored decision")
    if boundary.roots.to_dict() != manifest.roots.to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "restored boundary roots differ from the manifest")
    if decision.roots.to_dict() != manifest.roots.to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "restored decision roots differ from the manifest")
    # The decision's subject is checked where the decision is decoded, against
    # the digest the committed bytes carry, and the restored record then takes
    # the manifest's own ``snapshot_id``. A comparison here would therefore hold
    # a value against the value it was just assigned from — the shape that
    # checks nothing — which is why removing it changed no test.
    if boundary.completeness_decision_ref.sha256 != hashlib.sha256(members[DECISION_MEMBER_NAME]).hexdigest():
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "boundary decision ref hash differs")
    if boundary.completeness_decision_ref.ref_id != decision.decision_id.digest_sha256:
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "boundary decision ref names another decision")
    if boundary.manifest_ref.ref_id != manifest.snapshot_id.digest_sha256:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "boundary manifest ref names another snapshot")
    try:
        require_snapshot_status_admits_execution(decision.status)
    except ContractViolation as exc:
        raise _fail(
            KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED,
            f"restored completeness status {decision.status.value} does not admit execution",
        ) from exc

    return _make_usable(boundary, manifest, decision)


def _restore_decision(value: object, *, manifest: SnapshotManifest) -> SnapshotCompletenessDecision:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "snapshot_digest",
            "manifest_sha256",
            "context",
            "roots",
            "status",
            "authority_identity",
            "authority_role",
            "evaluated_at_utc",
            "detail",
        ),
        "completeness_decision",
    )
    if data["schema_version"] != SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1.value:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision schema is unknown")
    try:
        status = SnapshotCompletenessStatus(data["status"])
    except (TypeError, ValueError) as exc:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "completeness status is unknown") from exc
    try:
        role = AuthorityRole(data["authority_role"])
    except (TypeError, ValueError) as exc:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision authority role is unknown") from exc
    raw_time = data["evaluated_at_utc"]
    if type(raw_time) is not str:
        raise _fail(KnowledgeFailureCode.MALFORMED_TIMESTAMP, "evaluated_at_utc is invalid")
    try:
        evaluated = datetime.strptime(raw_time, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(KnowledgeFailureCode.MALFORMED_TIMESTAMP, "evaluated_at_utc is unparseable") from exc
    result = object.__new__(SnapshotCompletenessDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1)
    _sha256(data["snapshot_digest"], "snapshot_digest")
    if data["snapshot_digest"] != manifest.snapshot_id.digest_sha256:
        raise _fail(KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH, "restored decision names another snapshot")
    object.__setattr__(result, "snapshot_id", manifest.snapshot_id)
    object.__setattr__(result, "manifest_sha256", data["manifest_sha256"])
    object.__setattr__(result, "context", KnowledgeContext.from_dict(data["context"]))
    object.__setattr__(result, "roots", SnapshotRootSet.from_dict(data["roots"]))
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "authority_identity", _authority_from_dict(data["authority_identity"]))
    object.__setattr__(result, "authority_role", role)
    object.__setattr__(result, "evaluated_at_utc", evaluated)
    object.__setattr__(result, "detail", data["detail"])
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    object.__setattr__(
        result,
        "decision_id",
        compute_record_id(
            domain=IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION,
            canonical_bytes=_canonical(_decision_payload(result)),
        ),
    )
    validate_completeness_decision(result)
    return result


def _authority_from_dict(value: object) -> AuthorityIdentity:
    try:
        return AuthorityIdentity.from_dict(value)
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision authority identity is invalid") from exc


def _restore_boundary(value: object) -> AtomicSnapshotBoundary:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "transaction_id",
            "context",
            "roots",
            "admission_root_sha256",
            "compatibility_evidence_root_sha256",
            "manifest_ref",
            "manifest_sha256",
            "completeness_decision_ref",
            "start_sequence",
            "commit_sequence",
            "parent_boundary_digest",
            "commit_marker",
        ),
        "atomic_boundary",
    )
    if data["schema_version"] != SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1.value:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "atomic boundary schema is unknown")
    raw_parent = data["parent_boundary_digest"]
    return _make_boundary(
        transaction_id=data["transaction_id"],
        context=KnowledgeContext.from_dict(data["context"]),
        roots=SnapshotRootSet.from_dict(data["roots"]),
        admission_root_sha256=data["admission_root_sha256"],
        compatibility_evidence_root_sha256=data["compatibility_evidence_root_sha256"],
        manifest_ref=HashBoundRef.from_dict(data["manifest_ref"]),
        manifest_sha256=data["manifest_sha256"],
        completeness_decision_ref=HashBoundRef.from_dict(data["completeness_decision_ref"]),
        start_sequence=data["start_sequence"],
        commit_sequence=data["commit_sequence"],
        parent_boundary_digest=raw_parent,
        commit_marker=data["commit_marker"],
    )


def require_snapshot_bound_to_attempt(
    value: UsableKnowledgeSnapshot,
    *,
    attempt_boundary_id: RecordId,
    expected_context: KnowledgeContext,
) -> UsableKnowledgeSnapshot:
    """Confirm an already-open snapshot is bound to *this* attempt and context.

    **This reads no disk, and the name now says so.** It was called
    ``require_usable_snapshot`` and described as re-verifying a snapshot
    "immediately before replay or worker consumption", which reads as a freshness
    guarantee it cannot give: the argument is an object someone already opened,
    and nothing here can tell whether the transaction behind it still exists.

    That name was not a cosmetic problem. Round 18 called it from the frozen-set
    mint and wrote a docstring claiming durability had been re-verified; deleting
    the terminal commit marker after opening left the mint happily producing a
    capability. A name promising freshness it cannot deliver is a trap, and the
    reader it caught was the author.

    What it does check is binding, and that is worth having: one attempt consumes
    exactly one boundary, so a different boundary id means a new attempt or an
    explicit restart record, never a silent substitution. Durability is a separate
    question and is answered by re-opening the transaction — see
    ``open_usable_snapshot``, which every caller that needs freshness must call
    first.
    """

    if type(value) is not UsableKnowledgeSnapshot or getattr(value, "_trusted_seal", None) is not _USABLE_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "usable snapshot is not restore sealed")
    validate_atomic_boundary(value.boundary)
    validate_snapshot_manifest(value.manifest)
    validate_completeness_decision(value.decision)
    if type(attempt_boundary_id) is not RecordId or attempt_boundary_id.domain is not IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "attempt boundary id is invalid")
    if attempt_boundary_id.value != value.boundary.atomic_boundary_id.value:
        raise _fail(
            KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS,
            "attempt is bound to a different atomic boundary",
        )
    require_same_context(value.manifest.context, expected_context, subject="consumer context")
    try:
        require_snapshot_status_admits_execution(value.decision.status)
    except ContractViolation as exc:
        raise _fail(
            KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED,
            f"completeness status {value.decision.status.value} does not admit execution",
        ) from exc
    return value


__all__ = [
    "BOUNDARY_MEMBER_NAME",
    "DECISION_MEMBER_NAME",
    "KNOWLEDGE_CONTEXT_V1",
    "MANIFEST_MEMBER_NAME",
    "SNAPSHOT_ROOT_SET_V1",
    "AdmissionHistoryRootPort",
    "RootObservationFencePort",
    "require_root_observation_fence",
    "AtomicSnapshotBoundary",
    "COMPATIBILITY_EVIDENCE_ROOT_GENESIS",
    "ConfiguredSnapshotEvaluator",
    "KnowledgeContext",
    "KnowledgeFailureCode",
    "KnowledgeViolation",
    "SnapshotCompletenessDecision",
    "SnapshotManifest",
    "SnapshotRootSet",
    "UsableKnowledgeSnapshot",
    "atomic_boundary_ref",
    "commit_atomic_snapshot_boundary",
    "compatibility_evidence_root",
    "configure_snapshot_evaluator",
    "create_knowledge_context",
    "create_snapshot_manifest",
    "create_snapshot_root_set",
    "detect_mixed_generation",
    "detect_root_regression",
    "evaluate_snapshot_completeness",
    "open_usable_snapshot",
    "require_same_context",
    "require_snapshot_bound_to_attempt",
    "snapshot_manifest_from_dict",
    "validate_atomic_boundary",
    "validate_completeness_decision",
    "validate_knowledge_context",
    "validate_snapshot_manifest",
    "validate_snapshot_root_set",
]
