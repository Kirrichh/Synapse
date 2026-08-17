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
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    SnapshotCompletenessStatus,
    Stage4AuthorityHandle,
    compute_record_id,
    compute_envelope_binding_sha256,
    common_envelope_from_dict,
    create_common_envelope,
    envelope_bound_record_bytes,
    require_stage4_authority_handle,
    require_snapshot_status_admits_execution,
    record_id_reference_from_dict,
    validate_record_id,
    validate_envelope_bound_record,
)
from .authority_config import (
    AuthorityConfigViolation,
    SnapshotActorSet,
    SnapshotEvaluatorDeclaration,
    SnapshotIndependenceProof,
    require_snapshot_evaluator_entitlement,
    snapshot_actor_set_digest,
    snapshot_declaration_digest,
    snapshot_proof_digest,
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
    store_transaction,
)

KNOWLEDGE_CONTEXT_V1 = "synapse.stage4.gold.knowledge-context/v1"
SNAPSHOT_ROOT_SET_V1 = "synapse.stage4.gold.snapshot-root-set/v1"
SNAPSHOT_ROOT_SET_V2 = "synapse.stage4.gold.snapshot-root-set/v2"

#: Domain separator for the compatibility evidence root chained below. A digest
#: derived under this prefix cannot collide with one derived anywhere else.
COMPATIBILITY_EVIDENCE_ROOT_GENESIS = b"synapse.stage4.gold.compatibility-evidence-root/v1"

MANIFEST_MEMBER_NAME = "snapshot-manifest.json"
DECISION_MEMBER_NAME = "completeness-decision.json"
BOUNDARY_MEMBER_NAME = "atomic-boundary.json"
ADMISSION_ROOT_MEMBER_NAME = "admission-root-manifest.json"
COMPATIBILITY_ROOT_MEMBER_NAME = "compatibility-evidence-manifest.json"

_MANIFEST_SEAL = object()
_DECISION_SEAL = object()
_BOUNDARY_SEAL = object()
_EVALUATOR_SEAL = object()
_USABLE_SEAL = object()
_COMMITTED_AUDIT_SEAL = object()
_ROOT_TOKEN_SEAL = object()
_EVALUATION_SEAL = object()
_ADMISSION_ROOT_SEAL = object()
_COMPATIBILITY_ROOT_SEAL = object()

#: Domain separator for the root-set digest a token carries. A digest derived
#: under this prefix cannot collide with one derived anywhere else, so a token
#: cannot be made to describe a root set by reusing some other structure's hash.
ROOT_OBSERVATION_DIGEST_GENESIS = b"synapse.stage4.gold.root-observation/v1"

#: Domain separator for the verdict digest a token is bound to. Separate from the
#: root separator so a root-set hash can never be presented as a decision hash.
COMPLETENESS_DECISION_DIGEST_GENESIS = b"synapse.stage4.gold.completeness-decision/v1"

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
    #: A mutation was already open when the root read began. Nothing was read, so
    #: this is not a tear: retrying once the interval closes is correct, and
    #: reporting it as OBSERVATION_TORN would say a change landed inside the
    #: window when none did.
    MUTATION_IN_FLIGHT_AT_ENTRY = "MUTATION_IN_FLIGHT_AT_ENTRY"
    #: A mutation opened while the roots were being gathered. Some of the three
    #: roots may predate it, so the values are discarded rather than reconciled.
    MUTATION_IN_FLIGHT_AT_EXIT = "MUTATION_IN_FLIGHT_AT_EXIT"
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
    COMPATIBILITY_ROOT_UNCONFIRMED = "COMPATIBILITY_ROOT_UNCONFIRMED"
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
# V2 history-root manifests — exact prefixes, not current-history aliases
# ---------------------------------------------------------------------------


def _coordinator_id(value: object) -> str:
    if type(value) is not str or len(value) != 32 or any(
        item not in "0123456789abcdef" for item in value
    ):
        raise _fail(KnowledgeFailureCode.MALFORMED_IDENTIFIER, "coordinator identity is invalid")
    return value


def _configuration_id(value: object) -> RecordId:
    if type(value) is not RecordId or value.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "authority configuration identity is invalid")
    return value


def _history_prefix(value: object, label: str) -> tuple[str, int, str]:
    for name in ("current_anchor", "current_sequence"):
        if not callable(getattr(value, name, None)):
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{label} is missing {name}")
    fence = getattr(value, "mutation_fence", None)
    if fence is None or not callable(getattr(fence, "coordinator_id", None)):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{label} is not coordinator bound")
    anchor = value.current_anchor()
    sequence = value.current_sequence()
    _sha256(anchor, f"{label}_anchor")
    _sequence(sequence, f"{label}_sequence")
    return anchor, sequence, _coordinator_id(fence.coordinator_id())


@dataclass(frozen=True, init=False)
class AdmissionRootManifestV2:
    schema_version: SchemaVersion
    manifest_id: RecordId
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    authority_configuration_id: RecordId
    coordinator_id: str
    admission_history_anchor: str
    admission_history_sequence: int
    retrieval_causal_history_anchor: str
    retrieval_causal_history_sequence: int
    historical_admission_refs: tuple[HashBoundRef, ...]
    historical_retrieval_decision_refs: tuple[HashBoundRef, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AdmissionRootManifestV2:
        raise TypeError("AdmissionRootManifestV2 is created from durable history prefixes")

    def domain_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version.value,
            "authority_configuration_id": self.authority_configuration_id.to_dict(),
            "coordinator_id": self.coordinator_id,
            "admission_history_anchor": self.admission_history_anchor,
            "admission_history_sequence": self.admission_history_sequence,
            "retrieval_causal_history_anchor": self.retrieval_causal_history_anchor,
            "retrieval_causal_history_sequence": self.retrieval_causal_history_sequence,
            "historical_admission_refs": [item.to_dict() for item in self.historical_admission_refs],
            "historical_retrieval_decision_refs": [
                item.to_dict() for item in self.historical_retrieval_decision_refs
            ],
        }

    def to_dict(self) -> dict[str, object]:
        validate_admission_root_manifest(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": self.domain_payload(),
        }

    def canonical_bytes(self) -> bytes:
        validate_admission_root_manifest(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=self.domain_payload(),
        )


@dataclass(frozen=True, init=False)
class CompatibilityEvidenceManifestV2:
    schema_version: SchemaVersion
    manifest_id: RecordId
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    authority_configuration_id: RecordId
    coordinator_id: str
    compatibility_history_anchor: str
    compatibility_history_sequence: int
    compatibility_refs: tuple[HashBoundRef, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityEvidenceManifestV2:
        raise TypeError("CompatibilityEvidenceManifestV2 is created from a durable history prefix")

    def domain_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version.value,
            "authority_configuration_id": self.authority_configuration_id.to_dict(),
            "coordinator_id": self.coordinator_id,
            "compatibility_history_anchor": self.compatibility_history_anchor,
            "compatibility_history_sequence": self.compatibility_history_sequence,
            "compatibility_refs": [item.to_dict() for item in self.compatibility_refs],
        }

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_evidence_manifest(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": self.domain_payload(),
        }

    def canonical_bytes(self) -> bytes:
        validate_compatibility_evidence_manifest(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=self.domain_payload(),
        )


def _validate_root_manifest_envelope(
    *,
    value: object,
    seal: object,
    schema: SchemaVersion,
    identity_domain: IdentityDomain,
    payload: dict[str, object],
) -> None:
    if getattr(value, "_trusted_seal", None) is not seal:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "root manifest is not factory sealed")
    if value.schema_version is not schema:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "root manifest schema is unknown")
    canonical_payload = _canonical(payload)
    try:
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=canonical_payload,
            expected_identity_domain=identity_domain,
        )
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "root manifest envelope is invalid") from exc
    if value.manifest_id != value.envelope.record_id:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "root manifest identity differs from its envelope")


def validate_admission_root_manifest(value: AdmissionRootManifestV2) -> None:
    if type(value) is not AdmissionRootManifestV2:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "admission root manifest type is invalid")
    _configuration_id(value.authority_configuration_id)
    _coordinator_id(value.coordinator_id)
    _sha256(value.admission_history_anchor, "admission_history_anchor")
    _sha256(value.retrieval_causal_history_anchor, "retrieval_causal_history_anchor")
    _sequence(value.admission_history_sequence, "admission_history_sequence")
    _sequence(value.retrieval_causal_history_sequence, "retrieval_causal_history_sequence")
    _refs(value.historical_admission_refs, RefKind.GATE_DECISION, "historical_admission_refs")
    _refs(value.historical_retrieval_decision_refs, None, "historical_retrieval_decision_refs")
    _validate_root_manifest_envelope(
        value=value,
        seal=_ADMISSION_ROOT_SEAL,
        schema=SchemaVersion.ADMISSION_ROOT_MANIFEST_V2,
        identity_domain=IdentityDomain.ADMISSION_ROOT_MANIFEST_V2,
        payload=value.domain_payload(),
    )


def validate_compatibility_evidence_manifest(value: CompatibilityEvidenceManifestV2) -> None:
    if type(value) is not CompatibilityEvidenceManifestV2:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "compatibility root manifest type is invalid")
    _configuration_id(value.authority_configuration_id)
    _coordinator_id(value.coordinator_id)
    _sha256(value.compatibility_history_anchor, "compatibility_history_anchor")
    _sequence(value.compatibility_history_sequence, "compatibility_history_sequence")
    _refs(value.compatibility_refs, None, "compatibility_refs")
    _validate_root_manifest_envelope(
        value=value,
        seal=_COMPATIBILITY_ROOT_SEAL,
        schema=SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2,
        identity_domain=IdentityDomain.COMPATIBILITY_EVIDENCE_MANIFEST_V2,
        payload=value.domain_payload(),
    )


def _root_manifest_envelope(
    *,
    domain_payload: dict[str, object],
    identity_domain: IdentityDomain,
    context: KnowledgeContext,
    run_id: RunId,
    attempt_id: AttemptId,
    created_at_utc: datetime,
    producer_component: str,
) -> tuple[CommonEnvelope, str]:
    canonical_payload = _canonical(domain_payload)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=identity_domain,
        canonical_payload_bytes=canonical_payload,
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=created_at_utc,
        producer_component=producer_component,
        repository_revision=RepositoryRevision.git_commit(context.repository_revision),
        policy_version=context.policy_version,
        environment_profile_id=context.environment_profile_id,
        lineage_parent_ids=(),
    )
    return envelope, compute_envelope_binding_sha256(envelope)


def create_admission_root_manifest(
    *,
    context: KnowledgeContext,
    run_id: RunId,
    attempt_id: AttemptId,
    authority_configuration_id: RecordId,
    admission_history: object,
    retrieval_causal_history: object,
    historical_admission_refs: tuple[HashBoundRef, ...],
    historical_retrieval_decision_refs: tuple[HashBoundRef, ...],
    created_at_utc: datetime,
    producer_component: str,
) -> AdmissionRootManifestV2:
    admission_anchor, admission_sequence, admission_coordinator = _history_prefix(
        admission_history, "admission history"
    )
    causal_anchor, causal_sequence, causal_coordinator = _history_prefix(
        retrieval_causal_history, "retrieval causal history"
    )
    if admission_coordinator != causal_coordinator:
        raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "admission histories use different coordinators")
    result = object.__new__(AdmissionRootManifestV2)
    object.__setattr__(result, "schema_version", SchemaVersion.ADMISSION_ROOT_MANIFEST_V2)
    object.__setattr__(result, "authority_configuration_id", _configuration_id(authority_configuration_id))
    object.__setattr__(result, "coordinator_id", admission_coordinator)
    object.__setattr__(result, "admission_history_anchor", admission_anchor)
    object.__setattr__(result, "admission_history_sequence", admission_sequence)
    object.__setattr__(result, "retrieval_causal_history_anchor", causal_anchor)
    object.__setattr__(result, "retrieval_causal_history_sequence", causal_sequence)
    object.__setattr__(result, "historical_admission_refs", historical_admission_refs)
    object.__setattr__(result, "historical_retrieval_decision_refs", historical_retrieval_decision_refs)
    object.__setattr__(result, "_trusted_seal", _ADMISSION_ROOT_SEAL)
    envelope, binding = _root_manifest_envelope(
        domain_payload=result.domain_payload(),
        identity_domain=IdentityDomain.ADMISSION_ROOT_MANIFEST_V2,
        context=context,
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=created_at_utc,
        producer_component=producer_component,
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", binding)
    object.__setattr__(result, "manifest_id", envelope.record_id)
    validate_admission_root_manifest(result)
    return result


def create_compatibility_evidence_manifest(
    *,
    context: KnowledgeContext,
    run_id: RunId,
    attempt_id: AttemptId,
    authority_configuration_id: RecordId,
    compatibility_history: object,
    compatibility_refs: tuple[HashBoundRef, ...],
    created_at_utc: datetime,
    producer_component: str,
) -> CompatibilityEvidenceManifestV2:
    anchor, sequence, coordinator = _history_prefix(compatibility_history, "compatibility history")
    result = object.__new__(CompatibilityEvidenceManifestV2)
    object.__setattr__(result, "schema_version", SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2)
    object.__setattr__(result, "authority_configuration_id", _configuration_id(authority_configuration_id))
    object.__setattr__(result, "coordinator_id", coordinator)
    object.__setattr__(result, "compatibility_history_anchor", anchor)
    object.__setattr__(result, "compatibility_history_sequence", sequence)
    object.__setattr__(result, "compatibility_refs", compatibility_refs)
    object.__setattr__(result, "_trusted_seal", _COMPATIBILITY_ROOT_SEAL)
    envelope, binding = _root_manifest_envelope(
        domain_payload=result.domain_payload(),
        identity_domain=IdentityDomain.COMPATIBILITY_EVIDENCE_MANIFEST_V2,
        context=context,
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=created_at_utc,
        producer_component=producer_component,
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", binding)
    object.__setattr__(result, "manifest_id", envelope.record_id)
    validate_compatibility_evidence_manifest(result)
    return result


def admission_root_manifest_from_dict(value: object) -> AdmissionRootManifestV2:
    """Restore an immutable v2 admission prefix from its canonical record."""

    outer = _exact_dict(
        value,
        ("envelope", "envelope_binding_sha256", "payload"),
        "admission_root_manifest",
    )
    payload = _exact_dict(
        outer["payload"],
        (
            "schema_version",
            "authority_configuration_id",
            "coordinator_id",
            "admission_history_anchor",
            "admission_history_sequence",
            "retrieval_causal_history_anchor",
            "retrieval_causal_history_sequence",
            "historical_admission_refs",
            "historical_retrieval_decision_refs",
        ),
        "admission_root_manifest.payload",
    )
    if payload["schema_version"] != SchemaVersion.ADMISSION_ROOT_MANIFEST_V2.value:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "admission root manifest schema is unknown")
    result = object.__new__(AdmissionRootManifestV2)
    object.__setattr__(result, "schema_version", SchemaVersion.ADMISSION_ROOT_MANIFEST_V2)
    object.__setattr__(result, "authority_configuration_id", record_id_reference_from_dict(payload["authority_configuration_id"]))
    object.__setattr__(result, "coordinator_id", payload["coordinator_id"])
    object.__setattr__(result, "admission_history_anchor", payload["admission_history_anchor"])
    object.__setattr__(result, "admission_history_sequence", payload["admission_history_sequence"])
    object.__setattr__(result, "retrieval_causal_history_anchor", payload["retrieval_causal_history_anchor"])
    object.__setattr__(result, "retrieval_causal_history_sequence", payload["retrieval_causal_history_sequence"])
    object.__setattr__(
        result,
        "historical_admission_refs",
        _ref_tuple(payload["historical_admission_refs"], "historical_admission_refs"),
    )
    object.__setattr__(
        result,
        "historical_retrieval_decision_refs",
        _ref_tuple(
            payload["historical_retrieval_decision_refs"],
            "historical_retrieval_decision_refs",
        ),
    )
    try:
        envelope = common_envelope_from_dict(
            outer["envelope"], canonical_payload_bytes=_canonical(payload)
        )
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "admission root envelope is invalid") from exc
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", outer["envelope_binding_sha256"])
    object.__setattr__(result, "manifest_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _ADMISSION_ROOT_SEAL)
    validate_admission_root_manifest(result)
    if result.canonical_bytes() != _canonical(value):
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "admission root bytes are not canonical")
    return result


def compatibility_evidence_manifest_from_dict(value: object) -> CompatibilityEvidenceManifestV2:
    """Restore an immutable v2 compatibility prefix from canonical bytes."""

    outer = _exact_dict(
        value,
        ("envelope", "envelope_binding_sha256", "payload"),
        "compatibility_evidence_manifest",
    )
    payload = _exact_dict(
        outer["payload"],
        (
            "schema_version",
            "authority_configuration_id",
            "coordinator_id",
            "compatibility_history_anchor",
            "compatibility_history_sequence",
            "compatibility_refs",
        ),
        "compatibility_evidence_manifest.payload",
    )
    if payload["schema_version"] != SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2.value:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "compatibility evidence manifest schema is unknown")
    result = object.__new__(CompatibilityEvidenceManifestV2)
    object.__setattr__(result, "schema_version", SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2)
    object.__setattr__(result, "authority_configuration_id", record_id_reference_from_dict(payload["authority_configuration_id"]))
    object.__setattr__(result, "coordinator_id", payload["coordinator_id"])
    object.__setattr__(result, "compatibility_history_anchor", payload["compatibility_history_anchor"])
    object.__setattr__(result, "compatibility_history_sequence", payload["compatibility_history_sequence"])
    object.__setattr__(
        result,
        "compatibility_refs",
        _ref_tuple(payload["compatibility_refs"], "compatibility_refs"),
    )
    try:
        envelope = common_envelope_from_dict(
            outer["envelope"], canonical_payload_bytes=_canonical(payload)
        )
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "compatibility root envelope is invalid") from exc
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", outer["envelope_binding_sha256"])
    object.__setattr__(result, "manifest_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _COMPATIBILITY_ROOT_SEAL)
    validate_compatibility_evidence_manifest(result)
    if result.canonical_bytes() != _canonical(value):
        raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "compatibility root bytes are not canonical")
    return result


def _root_manifest_ref(value: AdmissionRootManifestV2 | CompatibilityEvidenceManifestV2) -> HashBoundRef:
    if type(value) is AdmissionRootManifestV2:
        validate_admission_root_manifest(value)
        schema = SchemaVersion.ADMISSION_ROOT_MANIFEST_V2
    elif type(value) is CompatibilityEvidenceManifestV2:
        validate_compatibility_evidence_manifest(value)
        schema = SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2
    else:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root manifest is invalid")
    raw = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.manifest_id.digest_sha256,
        schema_id=schema.value,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type="application/json",
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
    admission_root_manifest_ref: HashBoundRef | None = None
    compatibility_evidence_manifest_ref: HashBoundRef | None = None

    def __post_init__(self) -> None:
        validate_snapshot_root_set(self)

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_root_set(self)
        payload = {
            "schema_version": self.schema_version,
            "library_root_sha256": self.library_root_sha256,
            "library_generation": self.library_generation,
            "index_root_sha256": self.index_root_sha256,
            "index_generation": self.index_generation,
            "lifecycle_root_sha256": self.lifecycle_root_sha256,
            "lifecycle_record_count": self.lifecycle_record_count,
        }
        if self.schema_version == SNAPSHOT_ROOT_SET_V2:
            assert self.admission_root_manifest_ref is not None
            assert self.compatibility_evidence_manifest_ref is not None
            payload["admission_root_manifest_ref"] = self.admission_root_manifest_ref.to_dict()
            payload["compatibility_evidence_manifest_ref"] = (
                self.compatibility_evidence_manifest_ref.to_dict()
            )
        return payload

    @classmethod
    def from_dict(cls, value: object) -> SnapshotRootSet:
        if type(value) is not dict:
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "snapshot_root_set must be an exact dict")
        schema = value.get("schema_version")
        base_fields = (
                "schema_version",
                "library_root_sha256",
                "library_generation",
                "index_root_sha256",
                "index_generation",
                "lifecycle_root_sha256",
                "lifecycle_record_count",
        )
        fields = base_fields if schema == SNAPSHOT_ROOT_SET_V1 else base_fields + (
            "admission_root_manifest_ref",
            "compatibility_evidence_manifest_ref",
        )
        data = _exact_dict(
            value,
            fields,
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
            None
            if schema == SNAPSHOT_ROOT_SET_V1
            else HashBoundRef.from_dict(data["admission_root_manifest_ref"]),
            None
            if schema == SNAPSHOT_ROOT_SET_V1
            else HashBoundRef.from_dict(data["compatibility_evidence_manifest_ref"]),
        )


def validate_snapshot_root_set(value: SnapshotRootSet) -> None:
    if type(value) is not SnapshotRootSet:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root set type is invalid")
    if value.schema_version not in {SNAPSHOT_ROOT_SET_V1, SNAPSHOT_ROOT_SET_V2} or type(value.schema_version) is not str:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "root set schema is unknown")
    _sha256(value.library_root_sha256, "library_root_sha256")
    _sha256(value.index_root_sha256, "index_root_sha256")
    _sha256(value.lifecycle_root_sha256, "lifecycle_root_sha256")
    _sequence(value.library_generation, "library_generation")
    _sequence(value.index_generation, "index_generation")
    _sequence(value.lifecycle_record_count, "lifecycle_record_count")
    if value.schema_version == SNAPSHOT_ROOT_SET_V2:
        _ref(value.admission_root_manifest_ref, RefKind.ARTIFACT, "admission_root_manifest_ref")
        _ref(
            value.compatibility_evidence_manifest_ref,
            RefKind.ARTIFACT,
            "compatibility_evidence_manifest_ref",
        )
        if value.admission_root_manifest_ref.schema_id != SchemaVersion.ADMISSION_ROOT_MANIFEST_V2.value:
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "admission root manifest schema is invalid")
        if value.compatibility_evidence_manifest_ref.schema_id != SchemaVersion.COMPATIBILITY_EVIDENCE_MANIFEST_V2.value:
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "compatibility root manifest schema is invalid")
    elif value.admission_root_manifest_ref is not None or value.compatibility_evidence_manifest_ref is not None:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "v1 root set cannot carry v2 root manifests")


def create_snapshot_root_set(
    *,
    library_root_sha256: str,
    library_generation: int,
    index_root_sha256: str,
    index_generation: int,
    lifecycle_root_sha256: str,
    lifecycle_record_count: int,
    admission_root_manifest: AdmissionRootManifestV2,
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2,
) -> SnapshotRootSet:
    validate_admission_root_manifest(admission_root_manifest)
    validate_compatibility_evidence_manifest(compatibility_evidence_manifest)
    if admission_root_manifest.coordinator_id != compatibility_evidence_manifest.coordinator_id:
        raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "root manifests use different coordinators")
    if admission_root_manifest.authority_configuration_id != compatibility_evidence_manifest.authority_configuration_id:
        raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "root manifests use different authority configurations")
    return SnapshotRootSet(
        SNAPSHOT_ROOT_SET_V2,
        library_root_sha256,
        library_generation,
        index_root_sha256,
        index_generation,
        lifecycle_root_sha256,
        lifecycle_record_count,
        _root_manifest_ref(admission_root_manifest),
        _root_manifest_ref(compatibility_evidence_manifest),
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
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
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
        if self.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V1:
            return {**_manifest_payload(self), "snapshot_id": self.snapshot_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _manifest_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_snapshot_manifest(self)
        if self.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V1:
            return _canonical(_manifest_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_manifest_payload(self),
        )

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
    if value.schema_version not in {
        SchemaVersion.KNOWLEDGE_SNAPSHOT_V1,
        SchemaVersion.KNOWLEDGE_SNAPSHOT_V2,
    }:
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
    expected_domain = (
        IdentityDomain.KNOWLEDGE_SNAPSHOT
        if value.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V1
        else IdentityDomain.KNOWLEDGE_SNAPSHOT_V2
    )
    if type(value.snapshot_id) is not RecordId or value.snapshot_id.domain is not expected_domain:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "snapshot_id domain is invalid")
    try:
        if value.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V1:
            validate_record_id(value.snapshot_id, canonical_bytes=_canonical(_manifest_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "v2 manifest envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_manifest_payload(value)),
                expected_identity_domain=IdentityDomain.KNOWLEDGE_SNAPSHOT_V2,
            )
            if value.snapshot_id != value.envelope.record_id:
                raise _fail(KnowledgeFailureCode.MANIFEST_IDENTITY_MISMATCH, "snapshot identity differs from its envelope")
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
    run_id: RunId,
    attempt_id: AttemptId,
    producer_component: str,
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
        if parent_snapshot_id.domain not in {
            IdentityDomain.KNOWLEDGE_SNAPSHOT,
            IdentityDomain.KNOWLEDGE_SNAPSHOT_V2,
        }:
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
        run_id=run_id,
        attempt_id=attempt_id,
        producer_component=producer_component,
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
    run_id: RunId | None = None,
    attempt_id: AttemptId | None = None,
    producer_component: str | None = None,
    restored_envelope: CommonEnvelope | None = None,
    restored_envelope_binding: str | None = None,
) -> SnapshotManifest:
    """Assemble a manifest from an already-validated lineage digest."""

    result = object.__new__(SnapshotManifest)
    current = run_id is not None or restored_envelope is not None
    object.__setattr__(
        result,
        "schema_version",
        SchemaVersion.KNOWLEDGE_SNAPSHOT_V2 if current else SchemaVersion.KNOWLEDGE_SNAPSHOT_V1,
    )
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
    if current:
        if restored_envelope is None:
            if type(run_id) is not RunId or type(attempt_id) is not AttemptId or type(producer_component) is not str:
                raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "v2 snapshot execution identity is incomplete")
            restored_envelope = create_common_envelope(
                schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
                identity_domain=IdentityDomain.KNOWLEDGE_SNAPSHOT_V2,
                canonical_payload_bytes=_canonical(_manifest_payload(result)),
                run_id=run_id,
                attempt_id=attempt_id,
                created_at_utc=created_at_utc,
                producer_component=producer_component,
                repository_revision=RepositoryRevision.git_commit(context.repository_revision),
                policy_version=context.policy_version,
                environment_profile_id=context.environment_profile_id,
                lineage_parent_ids=(),
            )
            restored_envelope_binding = compute_envelope_binding_sha256(restored_envelope)
        object.__setattr__(result, "envelope", restored_envelope)
        object.__setattr__(result, "envelope_binding_sha256", restored_envelope_binding)
        object.__setattr__(result, "snapshot_id", restored_envelope.record_id)
    else:
        object.__setattr__(result, "envelope", None)
        object.__setattr__(result, "envelope_binding_sha256", None)
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

    if type(value) is not dict:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "snapshot manifest must be an exact dict")
    current = set(value) == {"envelope", "envelope_binding_sha256", "payload"}
    data = _exact_dict(
        value["payload"] if current else value,
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
    expected_schema = (
        SchemaVersion.KNOWLEDGE_SNAPSHOT_V2.value
        if current
        else SchemaVersion.KNOWLEDGE_SNAPSHOT_V1.value
    )
    if data["schema_version"] != expected_schema:
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
    restored_envelope = None
    restored_binding = None
    if current:
        restored_envelope = common_envelope_from_dict(
            value["envelope"],
            canonical_payload_bytes=_canonical(data),
        )
        restored_binding = value["envelope_binding_sha256"]
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
        restored_envelope=restored_envelope,
        restored_envelope_binding=restored_binding,
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

    def current_sequence(self) -> int: ...

    def extends(self, anchor: str) -> bool: ...

    def contains_record(self, digest: str) -> bool: ...

    def contains_record_at(self, anchor: str, sequence: int, digest: str) -> bool: ...

    def prefix_extends(
        self,
        child_anchor: str,
        child_sequence: int,
        parent_anchor: str,
        parent_sequence: int,
    ) -> bool: ...


@runtime_checkable
class CompatibilityHistoryRootPort(Protocol):
    """Structural view of the durable compatibility prefix fixed by a boundary."""

    def current_anchor(self) -> str: ...

    def extends(self, anchor: str) -> bool: ...

    def contains_ref(self, ref: HashBoundRef) -> bool: ...

    def current_sequence(self) -> int: ...

    def contains_ref_at(self, anchor: str, sequence: int, ref: HashBoundRef) -> bool: ...

    def prefix_extends(
        self,
        child_anchor: str,
        child_sequence: int,
        parent_anchor: str,
        parent_sequence: int,
    ) -> bool: ...


@runtime_checkable
class AdmissionCausalHistoryPort(Protocol):
    """Structural view of opaque retrieval-decision artifacts."""

    def contains_ref(self, ref: HashBoundRef) -> bool: ...

    def current_anchor(self) -> str: ...

    def current_sequence(self) -> int: ...

    def contains_ref_at(self, anchor: str, sequence: int, ref: HashBoundRef) -> bool: ...

    def prefix_extends(
        self,
        child_anchor: str,
        child_sequence: int,
        parent_anchor: str,
        parent_sequence: int,
    ) -> bool: ...


@runtime_checkable
class AuthoritativeKnowledgeStorePort(Protocol):
    """Dependency-direction-safe view of the ``knowledge_store`` adapter."""

    @property
    def mutation_fence(self) -> object: ...

    def current_boundary_id(self) -> str | None: ...

    def attempt_boundary_id(self, attempt_id: AttemptId) -> str | None: ...

    def current_boundary(self) -> AtomicSnapshotBoundary | None: ...

    def open_current(self) -> UsableKnowledgeSnapshot: ...

    def commit_boundary(
        self,
        *,
        boundary: AtomicSnapshotBoundary,
        transaction_id: str,
        run_id: RunId,
        attempt_id: AttemptId,
        expected_parent_boundary_id: str | None,
        committed_members: tuple[SnapshotTransactionMember, ...],
        ticket: object,
    ) -> object: ...


@runtime_checkable
class RootObservationFencePort(Protocol):
    """What a fence must offer for a root observation to be one moment.

    Structural for the same reason `AdmissionHistoryRootPort` is: the concrete
    fence is an adapter of `persistence`, and this owner may not import it.
    `FileSnapshotFence` already answers all four methods.

    **Why §21 needs a fence at all.** A root set is three roots and three
    generations taken from three stores. Reading them through one callable makes
    one call, and one call is not one instant — the phrase is `coordination`'s
    own, written about the §22 authority heads, and the same defect stood on this
    side untouched. A library root from before a publish beside a lifecycle root
    from after it describes no world that ever existed, and every value in it
    validates, which is exactly why validating the result cannot detect it.

    The epoch is compared across the read, and its parity is checked at both
    ends. If it moved, some store mutated while the roots were being gathered and
    the observation is refused rather than repaired: there is no way to tell
    which of the three values came from before the change. If it is odd, a
    mutation is in flight and no coherent moment exists to observe.

    ``mutating`` is on this port even though observing roots does not mutate
    anything, because the boundary commit *does* — it is the §21 store's own
    write, and it went entirely unfenced until this round. The port that reads
    the roots and the one that commits the boundary must be the same coordinator,
    or the commit would land invisibly to the reader that is about to be told the
    snapshot is complete.

    This detects tearing; it does not prevent it. A fence backed by a real lock
    would make tearing impossible, one backed by a counter makes it visible, and
    which is in use is a property of the injected port rather than of this module
    — so this module claims only the weaker of the two.
    """

    def coordinator_id(self) -> str: ...

    def current_epoch(self) -> int: ...

    def mutating(self): ...

    def exclusive(self): ...


def require_root_observation_fence(value: object) -> RootObservationFencePort:
    if not isinstance(value, RootObservationFencePort):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root fence does not implement the observation port")
    for name in ("coordinator_id", "current_epoch", "mutating", "exclusive"):
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

    for name in ("current_anchor", "extends", "contains_record"):
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


def _exact_port_bool(value: object, *, detail: str) -> bool:
    if type(value) is not bool:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, detail)
    return value


def _confirm_durable_history_bindings(
    *,
    manifest: SnapshotManifest,
    admission_root_manifest: AdmissionRootManifestV2,
    admission_journal: AdmissionHistoryRootPort,
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2,
    compatibility_history: CompatibilityHistoryRootPort,
    admission_causal_history: AdmissionCausalHistoryPort,
) -> None:
    """Confirm roots and exact historical refs while the shared lock is held."""

    validate_admission_root_manifest(admission_root_manifest)
    validate_compatibility_evidence_manifest(compatibility_evidence_manifest)
    if manifest.roots.admission_root_manifest_ref.to_dict() != _root_manifest_ref(admission_root_manifest).to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "manifest names another admission root manifest")
    if manifest.roots.compatibility_evidence_manifest_ref.to_dict() != _root_manifest_ref(compatibility_evidence_manifest).to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "manifest names another compatibility root manifest")
    if admission_root_manifest.coordinator_id != compatibility_evidence_manifest.coordinator_id:
        raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "root manifests use different coordinators")
    if admission_root_manifest.authority_configuration_id != compatibility_evidence_manifest.authority_configuration_id:
        raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "root manifests use different authority configurations")
    if admission_root_manifest.historical_admission_refs != manifest.admission_refs:
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "admission root manifest names another admission set")
    if admission_root_manifest.historical_retrieval_decision_refs != manifest.retrieval_decision_refs:
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "admission root manifest names another retrieval history")
    expected_compatibility_refs = manifest.conflict_refs
    if compatibility_evidence_manifest.compatibility_refs != expected_compatibility_refs:
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "compatibility root manifest names another evidence set")
    _confirm_admission_root(
        admission_journal,
        admission_root_sha256=admission_root_manifest.admission_history_anchor,
    )
    for port, methods, label in (
        (
            compatibility_history,
            ("current_anchor", "current_sequence", "extends", "contains_ref_at"),
            "compatibility history",
        ),
        (
            admission_causal_history,
            ("current_anchor", "current_sequence", "contains_ref_at"),
            "admission causal history",
        ),
    ):
        for name in methods:
            if not callable(getattr(port, name, None)):
                raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{label} is missing {name}")
    try:
        compatible_prefix = compatibility_history.extends(
            compatibility_evidence_manifest.compatibility_history_anchor
        )
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED,
            "the compatibility history failed while confirming its prefix",
        ) from exc
    if not _exact_port_bool(
        compatible_prefix,
        detail="compatibility history did not answer extends with an exact bool",
    ):
        raise _fail(
            KnowledgeFailureCode.COMPATIBILITY_ROOT_UNCONFIRMED,
            "the compatibility root is not an ancestor of durable compatibility history",
        )

    # A v2 root manifest is a statement about the authoritative *current*
    # prefix observed before evaluation, not permission to choose an arbitrary
    # old ancestor that merely remains reachable.  Exact sequence plus anchor
    # closes that substitution while the shared coordinator lock prevents a
    # legitimate writer from moving the prefix during these reads.
    current_prefixes = (
        (
            admission_journal,
            admission_root_manifest.admission_history_anchor,
            admission_root_manifest.admission_history_sequence,
            "admission history",
        ),
        (
            admission_causal_history,
            admission_root_manifest.retrieval_causal_history_anchor,
            admission_root_manifest.retrieval_causal_history_sequence,
            "retrieval causal history",
        ),
        (
            compatibility_history,
            compatibility_evidence_manifest.compatibility_history_anchor,
            compatibility_evidence_manifest.compatibility_history_sequence,
            "compatibility history",
        ),
    )
    try:
        for port, expected_anchor, expected_sequence, label in current_prefixes:
            if port.current_sequence() != expected_sequence or port.current_anchor() != expected_anchor:
                raise _fail(
                    KnowledgeFailureCode.ROOT_SET_MISMATCH,
                    f"{label} moved away from the manifest's confirmed prefix",
                )
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED,
            "a durable history failed while confirming its current prefix",
        ) from exc

    try:
        for ref in manifest.admission_refs:
            if not _exact_port_bool(
                admission_journal.contains_record_at(
                    admission_root_manifest.admission_history_anchor,
                    admission_root_manifest.admission_history_sequence,
                    ref.sha256,
                ),
                detail="admission history did not answer membership with an exact bool",
            ):
                raise _fail(
                    KnowledgeFailureCode.UNRESOLVED_REFERENCE,
                    "a historical admission ref is absent from admission history",
                )
        for ref in manifest.retrieval_decision_refs:
            if not _exact_port_bool(
                admission_causal_history.contains_ref_at(
                    admission_root_manifest.retrieval_causal_history_anchor,
                    admission_root_manifest.retrieval_causal_history_sequence,
                    ref,
                ),
                detail="admission causal history did not answer membership with an exact bool",
            ):
                raise _fail(
                    KnowledgeFailureCode.UNRESOLVED_REFERENCE,
                    "a historical retrieval decision ref is absent from admission causal history",
                )
        for ref in manifest.conflict_refs:
            if not _exact_port_bool(
                compatibility_history.contains_ref_at(
                    compatibility_evidence_manifest.compatibility_history_anchor,
                    compatibility_evidence_manifest.compatibility_history_sequence,
                    ref,
                ),
                detail="compatibility history did not answer membership with an exact bool",
            ):
                raise _fail(
                    KnowledgeFailureCode.UNRESOLVED_REFERENCE,
                    "a historical conflict ref is absent from compatibility history",
                )
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED,
            "a durable history failed while resolving a historical ref",
        ) from exc


def _require_history_fence(value: object, *, fence: RootObservationFencePort, label: str) -> None:
    history_fence = getattr(value, "mutation_fence", None)
    if history_fence is not fence:
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            f"{label} is not bound to the boundary coordinator instance",
        )


def _require_prefix_extension(
    port: object,
    *,
    child_anchor: str,
    child_sequence: int,
    parent_anchor: str,
    parent_sequence: int,
    label: str,
) -> None:
    method = getattr(port, "prefix_extends", None)
    if not callable(method):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{label} is missing prefix_extends")
    try:
        extends = method(child_anchor, child_sequence, parent_anchor, parent_sequence)
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED,
            f"{label} failed while checking parent prefix",
        ) from exc
    if not _exact_port_bool(extends, detail=f"{label} did not answer prefix_extends with an exact bool"):
        raise _fail(
            KnowledgeFailureCode.ROLLBACK_DETECTED,
            f"{label} does not extend the authoritative parent prefix",
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
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    snapshot_id: RecordId
    manifest_sha256: str
    context: KnowledgeContext
    roots: SnapshotRootSet
    status: SnapshotCompletenessStatus
    authority_identity: AuthorityIdentity
    authority_role: AuthorityRole
    authority_configuration_sha256: str | None
    evaluator_declaration_sha256: str | None
    evaluator_actor_set_sha256: str | None
    evaluator_independence_proof_sha256: str | None
    evaluated_at_utc: datetime
    detail: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotCompletenessDecision:
        raise TypeError("SnapshotCompletenessDecision is produced only by a configured evaluator")

    def to_dict(self) -> dict[str, object]:
        validate_completeness_decision(self)
        if self.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1:
            return {**_decision_payload(self), "decision_id": self.decision_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _decision_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_completeness_decision(self)
        if self.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1:
            return _canonical(_decision_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_decision_payload(self),
        )


def _decision_payload(value: SnapshotCompletenessDecision) -> dict[str, object]:
    payload = {
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
    if value.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2:
        payload.update(
            {
                "authority_configuration_sha256": value.authority_configuration_sha256,
                "evaluator_declaration_sha256": value.evaluator_declaration_sha256,
                "evaluator_actor_set_sha256": value.evaluator_actor_set_sha256,
                "evaluator_independence_proof_sha256": value.evaluator_independence_proof_sha256,
            }
        )
    return payload


def validate_completeness_decision(value: SnapshotCompletenessDecision) -> None:
    if type(value) is not SnapshotCompletenessDecision or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "completeness decision is not evaluator sealed")
    if value.schema_version not in {
        SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1,
        SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2,
    }:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision schema is unknown")
    if type(value.status) is not SnapshotCompletenessStatus:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "completeness status is not typed")
    if type(value.authority_identity) is not AuthorityIdentity or type(value.authority_role) is not AuthorityRole:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision authority is invalid")
    if value.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2:
        for name in (
            "authority_configuration_sha256",
            "evaluator_declaration_sha256",
            "evaluator_actor_set_sha256",
            "evaluator_independence_proof_sha256",
        ):
            _sha256(getattr(value, name), name)
        if value.authority_role is not AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR:
            raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "v2 decision has the wrong evaluator role")
    expected_snapshot_domain = (
        IdentityDomain.KNOWLEDGE_SNAPSHOT
        if value.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1
        else IdentityDomain.KNOWLEDGE_SNAPSHOT_V2
    )
    if type(value.snapshot_id) is not RecordId or value.snapshot_id.domain is not expected_snapshot_domain:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision subject domain is invalid")
    _sha256(value.manifest_sha256, "manifest_sha256")
    validate_knowledge_context(value.context)
    validate_snapshot_root_set(value.roots)
    _timestamp(value.evaluated_at_utc, "evaluated_at_utc")
    if type(value.detail) is not str or not value.detail or len(value.detail) > 256:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision detail is invalid")
    try:
        if value.schema_version is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1:
            validate_record_id(value.decision_id, canonical_bytes=_canonical(_decision_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "v2 decision envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_decision_payload(value)),
                expected_identity_domain=IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION_V2,
                expected_run_id=value.envelope.run_id,
                expected_attempt_id=value.envelope.attempt_id,
            )
            if value.decision_id != value.envelope.record_id:
                raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "decision identity differs from its envelope")
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "decision_id does not match its payload") from exc


def require_current_snapshot_evaluator_entitlement(
    decision: SnapshotCompletenessDecision,
    *,
    declaration: SnapshotEvaluatorDeclaration,
    actor_set: SnapshotActorSet,
    independence_proof: SnapshotIndependenceProof,
) -> None:
    """Re-check a v2 completeness decision against consumer-owned config records."""

    validate_completeness_decision(decision)
    if decision.schema_version is not SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "v1 completeness evidence is audit-only")
    try:
        require_snapshot_evaluator_entitlement(
            independence_proof,
            declaration=declaration,
            actor_set=actor_set,
        )
    except AuthorityConfigViolation as exc:
        raise _fail(KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT, "snapshot entitlement re-check failed") from exc
    if decision.authority_identity != declaration.evaluator_identity or decision.authority_role != declaration.authority_role:
        raise _fail(KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT, "decision names another evaluator entitlement")
    if decision.context.policy_version != declaration.policy_version:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "snapshot declaration uses another policy")
    expected = {
        "authority_configuration_sha256": declaration.configuration_id.digest_sha256,
        "evaluator_declaration_sha256": snapshot_declaration_digest(declaration),
        "evaluator_actor_set_sha256": snapshot_actor_set_digest(actor_set),
        "evaluator_independence_proof_sha256": snapshot_proof_digest(independence_proof),
    }
    for name, digest in expected.items():
        if getattr(decision, name) != digest:
            raise _fail(KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT, f"decision {name} differs from the entitlement copy")


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
            self._declaration,
            self._actor_set,
            self._independence_proof,
            self._trusted_clock,
            self._observed_roots_provider,
            self._root_fence,
            self._ref_resolver,
            self._consumability_probe,
        ) = args
        self._configuration_frozen = True

    @property
    def authority_identity(self) -> AuthorityIdentity:
        return self._declaration.evaluator_identity

    @property
    def authority_role(self) -> AuthorityRole:
        return self._declaration.authority_role

    @property
    def producer_actor(self) -> ActorIdentity:
        return self._actor_set.producer_actor

    @property
    def declaration(self) -> SnapshotEvaluatorDeclaration:
        return self._declaration

    @property
    def actor_set(self) -> SnapshotActorSet:
        return self._actor_set

    @property
    def independence_proof(self) -> SnapshotIndependenceProof:
        return self._independence_proof


def configure_snapshot_evaluator(
    *,
    authority_handle: Stage4AuthorityHandle,
    declaration: SnapshotEvaluatorDeclaration,
    actor_set: SnapshotActorSet,
    independence_proof: SnapshotIndependenceProof,
    trusted_clock: Callable[[], datetime],
    observed_roots_provider: Callable[[], SnapshotRootSet],
    root_fence: RootObservationFencePort,
    ref_resolver: Callable[[HashBoundRef], bool],
    consumability_probe: Callable[[HashBoundRef], bool],
) -> ConfiguredSnapshotEvaluator:
    configuration = require_stage4_authority_handle(authority_handle)
    try:
        require_snapshot_evaluator_entitlement(
            independence_proof,
            declaration=declaration,
            actor_set=actor_set,
        )
    except AuthorityConfigViolation as exc:
        raise _fail(KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT, "snapshot evaluator entitlement is invalid") from exc
    if declaration._authority_handle is not authority_handle or actor_set._authority_handle is not authority_handle:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "snapshot entitlement uses another authority handle")
    if declaration.configuration_id != configuration.configuration_id:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "snapshot declaration uses another configuration")
    if not callable(trusted_clock) or not callable(observed_roots_provider):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator providers must be callable")
    # Required, with no default. An evaluator that could be configured without a
    # fence would read three roots at three moments and report the result as one
    # observation, which is the condition this round exists to make detectable —
    # and an optional barrier is the NR-09 bypass in the shape of a default.
    require_root_observation_fence(root_fence)
    if not callable(ref_resolver) or not callable(consumability_probe):
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator resolvers must be callable")
    result = ConfiguredSnapshotEvaluator(
        authority_handle,
        declaration,
        actor_set,
        independence_proof,
        trusted_clock,
        observed_roots_provider,
        root_fence,
        ref_resolver,
        consumability_probe,
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
    object.__setattr__(result, "schema_version", SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2)
    object.__setattr__(result, "snapshot_id", manifest.snapshot_id)
    object.__setattr__(result, "manifest_sha256", manifest.payload_sha256())
    object.__setattr__(result, "context", manifest.context)
    object.__setattr__(result, "roots", manifest.roots)
    object.__setattr__(result, "status", status)
    object.__setattr__(result, "authority_identity", evaluator.authority_identity)
    object.__setattr__(result, "authority_role", evaluator.authority_role)
    object.__setattr__(
        result,
        "authority_configuration_sha256",
        evaluator.declaration.configuration_id.digest_sha256,
    )
    object.__setattr__(result, "evaluator_declaration_sha256", snapshot_declaration_digest(evaluator.declaration))
    object.__setattr__(result, "evaluator_actor_set_sha256", snapshot_actor_set_digest(evaluator.actor_set))
    object.__setattr__(
        result,
        "evaluator_independence_proof_sha256",
        snapshot_proof_digest(evaluator.independence_proof),
    )
    object.__setattr__(result, "evaluated_at_utc", _timestamp(evaluator._trusted_clock(), "evaluated_at_utc"))
    object.__setattr__(result, "detail", detail)
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    if manifest.envelope is None:
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "current decision requires a v2 manifest")
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION_V2,
        canonical_payload_bytes=_canonical(_decision_payload(result)),
        run_id=manifest.envelope.run_id,
        attempt_id=manifest.envelope.attempt_id,
        created_at_utc=result.evaluated_at_utc,
        producer_component="snapshot-evaluator",
        repository_revision=manifest.envelope.repository_revision,
        policy_version=manifest.envelope.policy_version,
        environment_profile_id=manifest.envelope.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(result, "decision_id", envelope.record_id)
    validate_completeness_decision(result)
    return result


def _decision_digest(decision: SnapshotCompletenessDecision) -> str:
    """The canonical digest of one verdict, domain separated.

    Over the decision's canonical bytes rather than its ``decision_id``: the id is
    derived from the payload already, but hashing the bytes here means a token
    cannot be satisfied by a record that merely claims the same identity.
    """

    return hashlib.sha256(
        COMPLETENESS_DECISION_DIGEST_GENESIS + decision.canonical_bytes()
    ).hexdigest()


def _root_set_digest(roots: SnapshotRootSet) -> str:
    """The canonical digest of one root set, domain separated."""

    return hashlib.sha256(
        ROOT_OBSERVATION_DIGEST_GENESIS + _canonical(roots.to_dict())
    ).hexdigest()


@dataclass(frozen=True, init=False)
class RootObservationToken:
    """Evidence that a specific coherent moment was observed, by whom, and where.

    A completeness verdict is computed against store roots read at some instant.
    Between that instant and the commit that makes the snapshot visible, the
    stores can move — and until this existed, nothing carried the instant
    forward. The commit compared nothing, or at best compared an epoch it had
    read after deciding, which is a different number from the one the decision
    rested on.

    So the observation is recorded as a capability rather than left implicit.
    Every field is one thing the commit has to be able to re-check:

    * ``coordinator_id`` — *which* world was observed. An epoch without it is a
      number that some other coordinator may also be sitting on.
    * ``epoch`` — the exact even count the read was bracketed by. Even, because
      an odd one means a write was in flight and no coherent moment existed.
    * ``roots_digest`` — what was actually seen, so a re-read under the lock can
      be compared against it rather than against the manifest alone.
    * ``context`` and ``snapshot_id`` — which evaluation this belongs to, so a
      token from another snapshot cannot stand in for this one.
    * ``evaluator_identity`` — who was standing there. §21 requires an
      independent evaluator, and a token that did not name one would let any
      party's observation carry any party's verdict.
    * ``decision_digest`` — *which verdict* this observation was reached for.
      Everything above describes the world; without this, nothing describes the
      conclusion. Two evaluations of one snapshot at one epoch over identical
      roots by one evaluator produced interchangeable tokens, so a COMPLETE
      verdict could be committed carrying the token of a second evaluation that
      had itself refused. That is not a hypothetical: it was reproduced, and it
      wrote a terminal marker.
    """

    coordinator_id: str
    epoch: int
    roots_digest: str
    context: KnowledgeContext
    snapshot_id: RecordId
    evaluator_identity: AuthorityIdentity
    decision_digest: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RootObservationToken:
        raise TypeError("RootObservationToken is produced only by evaluate_snapshot_completeness")


def validate_root_observation_token(value: object) -> RootObservationToken:
    """Refuse anything that is not a sealed, self-consistent observation token."""

    if (
        type(value) is not RootObservationToken
        or getattr(value, "_trusted_seal", None) is not _ROOT_TOKEN_SEAL
    ):
        raise _fail(
            KnowledgeFailureCode.TRUSTED_OBJECT_FORGED,
            "root observation token is not evaluator produced",
        )
    if type(value.coordinator_id) is not str or not value.coordinator_id:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "a token names its coordinator")
    if type(value.epoch) is not int or value.epoch < 0 or value.epoch % 2:
        raise _fail(
            KnowledgeFailureCode.TYPE_MISMATCH,
            "a token belongs to an exact settled coordinator epoch",
        )
    _sha256(value.roots_digest, "roots_digest")
    validate_knowledge_context(value.context)
    if type(value.snapshot_id) is not RecordId:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "a token names its snapshot")
    if type(value.evaluator_identity) is not AuthorityIdentity:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "a token names its evaluator")
    _sha256(value.decision_digest, "decision_digest")
    return value


@dataclass(frozen=True, init=False)
class CompletenessEvaluation:
    """A completeness verdict together with the observation it was reached on.

    Returned as one object rather than as two calls the caller sequences itself.
    A separate ``observe_roots`` step would let a caller observe, evaluate,
    observe *again*, and hand the fresher token to the commit while the decision
    rested on the older one — which is exactly the substitution the token exists
    to prevent. Coming out of one call, the token cannot describe a moment the
    decision did not see.
    """

    decision: SnapshotCompletenessDecision
    token: RootObservationToken | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompletenessEvaluation:
        raise TypeError("CompletenessEvaluation is produced only by evaluate_snapshot_completeness")


def validate_completeness_evaluation(value: object) -> CompletenessEvaluation:
    if (
        type(value) is not CompletenessEvaluation
        or getattr(value, "_trusted_seal", None) is not _EVALUATION_SEAL
    ):
        raise _fail(
            KnowledgeFailureCode.TRUSTED_OBJECT_FORGED,
            "completeness evaluation is not evaluator produced",
        )
    validate_completeness_decision(value.decision)
    if value.token is None:
        # The one combination that must never exist: a snapshot declared complete
        # with no record of the moment it was complete *at*. Everything the commit
        # re-checks comes from the token, so a COMPLETE without one is a verdict
        # nothing downstream could ever confirm — and it would be committed on the
        # strength of the word alone.
        if value.decision.status is SnapshotCompletenessStatus.COMPLETE:
            raise _fail(
                KnowledgeFailureCode.TRUSTED_OBJECT_FORGED,
                "a complete verdict must carry the observation it was reached on",
            )
        return value
    validate_root_observation_token(value.token)
    if value.token.snapshot_id.value != value.decision.snapshot_id.value:
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the observation token belongs to another snapshot",
        )
    require_same_context(value.token.context, value.decision.context, subject="observation token")
    # The exact pairing, checked at the seal rather than at the commit. A pair
    # that does not belong together cannot be sealed at all, so there is no
    # sealed object downstream for anyone to take apart and recombine.
    if value.token.decision_digest != _decision_digest(value.decision):
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the observation token was taken for a different verdict",
        )
    return value


def _fenced_root_observation(evaluator: ConfiguredSnapshotEvaluator) -> tuple[object, int]:
    """Observe the roots inside one seqlock window, and report the epoch, or refuse.

    The window replaces the advisory lease this used to take. The lease named a
    read attempt and excluded nobody, which it said plainly — but naming an
    attempt is not a property the algorithm uses, and keeping a file to record it
    invited the reading that the barrier was doing more than comparing two
    numbers. What makes the answer trustworthy is the comparison and the parity,
    so those are all that remain.

    Parity is the part the lease version was missing entirely. An even count at
    both ends says no interval was open when the read started or when it
    finished; equality says none opened and closed in between. Neither alone is
    enough, and the old code checked only the second.
    """

    fence = evaluator._root_fence
    before = fence.current_epoch()
    if type(before) is not int:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root fence reported a non-integer epoch")
    if before % 2:
        raise _fail(
            KnowledgeFailureCode.MUTATION_IN_FLIGHT_AT_ENTRY,
            "a mutation was already in flight, so the roots were never read",
        )
    observed = evaluator._observed_roots_provider()
    after = fence.current_epoch()
    if type(after) is not int:
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
    if after % 2:
        raise _fail(
            KnowledgeFailureCode.MUTATION_IN_FLIGHT_AT_EXIT,
            "a mutation opened while the roots were being observed",
        )
    if after != before:
        raise _fail(
            KnowledgeFailureCode.OBSERVATION_TORN,
            "an authority store mutated while the roots were being observed",
        )
    # The epoch travels out with the roots. Discarding it was what left the
    # commit with nothing to re-check: it could compare an epoch it read *after*
    # deciding, which is a different number from the one this read was bracketed
    # by, and no comparison at all against the moment the verdict rests on.
    return observed, after


def evaluate_snapshot_completeness(
    evaluator: ConfiguredSnapshotEvaluator,
    *,
    manifest: SnapshotManifest,
    prior_roots: SnapshotRootSet | None = None,
) -> CompletenessEvaluation:
    """Compute the authoritative completeness verdict over a manifest.

    Checks run in fail-closed order: an unavailable store is never downgraded to
    an optimistic pass, and the first definite failure wins. ``COMPLETE`` is
    reachable only when every required reference resolves, no root regressed,
    no generation mix is present and no selected object is blocked by lifecycle.

    The verdict comes back bound to the observation it was reached on. A decision
    on its own says a snapshot *was* complete; it cannot say when, against which
    coordinator, or against which roots — and the commit needs all three to know
    whether the world has moved since. Returning them together is what makes the
    commit's re-check possible at all.
    """

    if type(evaluator) is not ConfiguredSnapshotEvaluator:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator is not a configured snapshot evaluator")
    validate_snapshot_manifest(manifest)

    try:
        observed, observed_epoch = _fenced_root_observation(evaluator)
    except KnowledgeViolation:
        # A torn observation and a fence that went backwards both arrive here and
        # both propagate. Neither is a completeness verdict: the evaluation did not
        # happen, so no decision is produced and nothing downstream can commit one.
        raise
    except Exception:
        # No coherent moment was observed, so there is no token to mint. The
        # verdict is still a verdict — §21 names "required store unavailable" as
        # one — but it is one that can never be committed, which is correct.
        return _evaluation(
            _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE,
                "required store roots are unavailable",
            ),
            None,
        )
    if type(observed) is not SnapshotRootSet:
        return _evaluation(
            _make_decision(
                evaluator,
                manifest,
                SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE,
                "observed store roots are not an exact root set",
            ),
            None,
        )
    validate_snapshot_root_set(observed)

    # The verdict first, then the token, and that order is the binding. A token
    # minted before the verdict exists can only describe the world; one minted
    # after can name the conclusion the world was read for, which is what stops
    # two evaluations of one snapshot from swapping observations.
    decision = _completeness_verdict(
        evaluator, manifest=manifest, prior_roots=prior_roots, observed=observed
    )
    token = object.__new__(RootObservationToken)
    object.__setattr__(token, "coordinator_id", evaluator._root_fence.coordinator_id())
    object.__setattr__(token, "epoch", observed_epoch)
    object.__setattr__(token, "roots_digest", _root_set_digest(observed))
    object.__setattr__(token, "context", manifest.context)
    object.__setattr__(token, "snapshot_id", manifest.snapshot_id)
    object.__setattr__(token, "evaluator_identity", evaluator.authority_identity)
    object.__setattr__(token, "decision_digest", _decision_digest(decision))
    object.__setattr__(token, "_trusted_seal", _ROOT_TOKEN_SEAL)
    validate_root_observation_token(token)

    return _evaluation(decision, token)


def _evaluation(
    decision: SnapshotCompletenessDecision, token: RootObservationToken | None
) -> CompletenessEvaluation:
    """Seal a verdict together with the observation it rests on, or without one.

    ``token`` is ``None`` exactly when no coherent observation existed, which is
    a different fact from "the observation was fine and the snapshot was not"
    (NR-10). ``COMPLETE`` can never be one of those cases, and
    ``validate_completeness_evaluation`` refuses the combination outright rather
    than trusting this factory to be the only producer.
    """

    result = object.__new__(CompletenessEvaluation)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "token", token)
    object.__setattr__(result, "_trusted_seal", _EVALUATION_SEAL)
    return validate_completeness_evaluation(result)


def _completeness_verdict(
    evaluator: ConfiguredSnapshotEvaluator,
    *,
    manifest: SnapshotManifest,
    prior_roots: SnapshotRootSet | None,
    observed: SnapshotRootSet,
) -> SnapshotCompletenessDecision:
    """Everything the verdict depends on beyond the observation itself."""

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
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    transaction_id: str
    context: KnowledgeContext
    roots: SnapshotRootSet
    admission_root_sha256: str
    compatibility_evidence_root_sha256: str
    admission_root_manifest_ref: HashBoundRef | None
    compatibility_evidence_manifest_ref: HashBoundRef | None
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
        if self.schema_version is SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1:
            return {**_boundary_payload(self), "atomic_boundary_id": self.atomic_boundary_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _boundary_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_atomic_boundary(self)
        if self.schema_version is SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1:
            return _canonical(_boundary_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_boundary_payload(self),
        )


def _boundary_payload(value: AtomicSnapshotBoundary) -> dict[str, object]:
    payload = {
        "schema_version": value.schema_version.value,
        "transaction_id": value.transaction_id,
        "context": value.context.to_dict(),
        "roots": value.roots.to_dict(),
        "manifest_ref": value.manifest_ref.to_dict(),
        "manifest_sha256": value.manifest_sha256,
        "completeness_decision_ref": value.completeness_decision_ref.to_dict(),
        "start_sequence": value.start_sequence,
        "commit_sequence": value.commit_sequence,
        "parent_boundary_digest": value.parent_boundary_digest,
        "commit_marker": value.commit_marker,
    }
    if value.schema_version is SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1:
        payload["admission_root_sha256"] = value.admission_root_sha256
        payload["compatibility_evidence_root_sha256"] = value.compatibility_evidence_root_sha256
    else:
        assert value.admission_root_manifest_ref is not None
        assert value.compatibility_evidence_manifest_ref is not None
        payload["admission_root_manifest_ref"] = value.admission_root_manifest_ref.to_dict()
        payload["compatibility_evidence_manifest_ref"] = (
            value.compatibility_evidence_manifest_ref.to_dict()
        )
    return payload


def validate_atomic_boundary(value: AtomicSnapshotBoundary) -> None:
    if type(value) is not AtomicSnapshotBoundary or getattr(value, "_trusted_seal", None) is not _BOUNDARY_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "atomic boundary is not commit sealed")
    if value.schema_version not in {
        SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1,
        SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2,
    }:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "atomic boundary schema is unknown")
    _identifier(value.transaction_id, "transaction_id")
    validate_knowledge_context(value.context)
    validate_snapshot_root_set(value.roots)
    _sha256(value.admission_root_sha256, "admission_root_sha256")
    _sha256(value.compatibility_evidence_root_sha256, "compatibility_evidence_root_sha256")
    if value.schema_version is SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2:
        _ref(value.admission_root_manifest_ref, RefKind.ARTIFACT, "admission_root_manifest_ref")
        _ref(
            value.compatibility_evidence_manifest_ref,
            RefKind.ARTIFACT,
            "compatibility_evidence_manifest_ref",
        )
        if value.admission_root_sha256 != value.admission_root_manifest_ref.sha256:
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "admission root alias differs from typed ref")
        if value.compatibility_evidence_root_sha256 != value.compatibility_evidence_manifest_ref.sha256:
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "compatibility root alias differs from typed ref")
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
        if value.schema_version is SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1:
            validate_record_id(value.atomic_boundary_id, canonical_bytes=_canonical(_boundary_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "v2 boundary envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_boundary_payload(value)),
                expected_identity_domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY_V2,
            )
            if value.atomic_boundary_id != value.envelope.record_id:
                raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "boundary identity differs from its envelope")
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "atomic_boundary_id does not match its payload") from exc


def _make_boundary(
    *,
    transaction_id: str,
    context: KnowledgeContext,
    roots: SnapshotRootSet,
    admission_root_sha256: str,
    compatibility_evidence_root_sha256: str,
    admission_root_manifest_ref: HashBoundRef | None = None,
    compatibility_evidence_manifest_ref: HashBoundRef | None = None,
    manifest_ref: HashBoundRef,
    manifest_sha256: str,
    completeness_decision_ref: HashBoundRef,
    start_sequence: int,
    commit_sequence: int,
    parent_boundary_digest: str | None,
    commit_marker: str,
    run_id: RunId | None = None,
    attempt_id: AttemptId | None = None,
    created_at_utc: datetime | None = None,
    producer_component: str = "knowledge-boundary",
    restored_envelope: CommonEnvelope | None = None,
    restored_envelope_binding: str | None = None,
) -> AtomicSnapshotBoundary:
    result = object.__new__(AtomicSnapshotBoundary)
    current = run_id is not None or restored_envelope is not None
    object.__setattr__(
        result,
        "schema_version",
        SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2 if current else SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1,
    )
    object.__setattr__(result, "transaction_id", transaction_id)
    object.__setattr__(result, "context", context)
    object.__setattr__(result, "roots", roots)
    object.__setattr__(result, "admission_root_sha256", admission_root_sha256)
    object.__setattr__(result, "compatibility_evidence_root_sha256", compatibility_evidence_root_sha256)
    object.__setattr__(result, "admission_root_manifest_ref", admission_root_manifest_ref)
    object.__setattr__(result, "compatibility_evidence_manifest_ref", compatibility_evidence_manifest_ref)
    object.__setattr__(result, "manifest_ref", manifest_ref)
    object.__setattr__(result, "manifest_sha256", manifest_sha256)
    object.__setattr__(result, "completeness_decision_ref", completeness_decision_ref)
    object.__setattr__(result, "start_sequence", start_sequence)
    object.__setattr__(result, "commit_sequence", commit_sequence)
    object.__setattr__(result, "parent_boundary_digest", parent_boundary_digest)
    object.__setattr__(result, "commit_marker", commit_marker)
    object.__setattr__(result, "_trusted_seal", _BOUNDARY_SEAL)
    if current:
        if restored_envelope is None:
            if type(run_id) is not RunId or type(attempt_id) is not AttemptId or type(created_at_utc) is not datetime:
                raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "v2 boundary execution identity is incomplete")
            restored_envelope = create_common_envelope(
                schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
                identity_domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY_V2,
                canonical_payload_bytes=_canonical(_boundary_payload(result)),
                run_id=run_id,
                attempt_id=attempt_id,
                created_at_utc=created_at_utc,
                producer_component=producer_component,
                repository_revision=RepositoryRevision.git_commit(context.repository_revision),
                policy_version=context.policy_version,
                environment_profile_id=context.environment_profile_id,
                lineage_parent_ids=(),
            )
            restored_envelope_binding = compute_envelope_binding_sha256(restored_envelope)
        object.__setattr__(result, "envelope", restored_envelope)
        object.__setattr__(result, "envelope_binding_sha256", restored_envelope_binding)
        object.__setattr__(result, "atomic_boundary_id", restored_envelope.record_id)
    else:
        object.__setattr__(result, "envelope", None)
        object.__setattr__(result, "envelope_binding_sha256", None)
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
        schema_id=manifest.schema_version.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def snapshot_manifest_ref(manifest: SnapshotManifest) -> HashBoundRef:
    """Return the exact full-record reference for a restored snapshot manifest."""

    return _manifest_ref(manifest)


def _decision_ref(decision: SnapshotCompletenessDecision) -> HashBoundRef:
    payload = decision.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ATOMIC_BOUNDARY,
        ref_id=decision.decision_id.digest_sha256,
        schema_id=decision.schema_version.value,
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
        schema_id=boundary.schema_version.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def commit_atomic_snapshot_boundary(
    root: Path,
    *,
    transaction_id: str,
    manifest: SnapshotManifest,
    evaluation: CompletenessEvaluation,
    admission_root_manifest: AdmissionRootManifestV2,
    admission_journal: AdmissionHistoryRootPort,
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2,
    compatibility_history: CompatibilityHistoryRootPort,
    admission_causal_history: AdmissionCausalHistoryPort,
    knowledge_store: AuthoritativeKnowledgeStorePort,
    run_id: RunId,
    attempt_id: AttemptId,
    expected_parent_boundary_id: str | None,
    start_sequence: int,
    commit_sequence: int,
    evaluator: ConfiguredSnapshotEvaluator,
) -> AtomicSnapshotBoundary:
    """Commit audit bytes, then publish exactly one authoritative terminal frame.

    The caller supplies no portable root strings and no caller-constructed
    parent object.  Typed history-prefix manifests are checked against the exact
    durable stores, while parent lineage is resolved from the CAS head owned by
    ``knowledge_store``.  The legacy commit marker remains an immutable audit
    artifact; only the final append-only authority frame makes the boundary
    current.
    """

    validate_snapshot_manifest(manifest)
    validate_completeness_evaluation(evaluation)
    decision = evaluation.decision
    token = evaluation.token
    if type(evaluator) is not ConfiguredSnapshotEvaluator:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "evaluator is not a configured snapshot evaluator")
    require_current_snapshot_evaluator_entitlement(
        evaluation.decision,
        declaration=evaluator.declaration,
        actor_set=evaluator.actor_set,
        independence_proof=evaluator.independence_proof,
    )
    fence = evaluator._root_fence
    require_root_observation_fence(fence)
    _identifier(transaction_id, "transaction_id")
    validate_admission_root_manifest(admission_root_manifest)
    validate_compatibility_evidence_manifest(compatibility_evidence_manifest)
    if type(run_id) is not RunId or type(attempt_id) is not AttemptId:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "boundary run and attempt identity are invalid")
    if expected_parent_boundary_id is not None:
        _sha256(expected_parent_boundary_id, "expected_parent_boundary_id")
    if manifest.schema_version is not SchemaVersion.KNOWLEDGE_SNAPSHOT_V2 or manifest.envelope is None:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "current authority requires a v2 snapshot")
    if manifest.envelope.run_id != run_id or manifest.envelope.attempt_id != attempt_id:
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "manifest belongs to another execution")
    for root_manifest, label in (
        (admission_root_manifest, "admission root manifest"),
        (compatibility_evidence_manifest, "compatibility root manifest"),
    ):
        if root_manifest.envelope.run_id != run_id or root_manifest.envelope.attempt_id != attempt_id:
            raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, f"{label} belongs to another execution")
    expected_configuration_id = evaluator._authority_handle.configuration_id
    if (
        admission_root_manifest.authority_configuration_id != expected_configuration_id
        or compatibility_evidence_manifest.authority_configuration_id != expected_configuration_id
    ):
        raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "root manifests use another authority configuration")
    coordinator_id = fence.coordinator_id()
    if (
        admission_root_manifest.coordinator_id != coordinator_id
        or compatibility_evidence_manifest.coordinator_id != coordinator_id
    ):
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "root manifests use another coordinator")
    for history, label in (
        (admission_journal, "admission history"),
        (compatibility_history, "compatibility history"),
        (admission_causal_history, "retrieval causal history"),
    ):
        required = (
            ("current_anchor", "extends", "contains_record")
            if history is admission_journal
            else (
                ("current_anchor", "current_sequence", "extends", "contains_ref_at")
                if history is compatibility_history
                else ("current_anchor", "current_sequence", "contains_ref_at")
            )
        )
        for name in required:
            if not callable(getattr(history, name, None)):
                raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"{label} is missing {name}")
        _require_history_fence(history, fence=fence, label=label)
    if getattr(knowledge_store, "mutation_fence", None) is not fence:
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "knowledge store uses another coordinator instance")
    for name in (
        "current_boundary_id",
        "attempt_boundary_id",
        "current_boundary",
        "open_current",
        "commit_boundary",
    ):
        if not callable(getattr(knowledge_store, name, None)):
            raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, f"knowledge store is missing {name}")

    # A transaction identity is globally consumed even if a stale caller also
    # supplied the wrong parent expectation.  Classify the replay before the
    # CAS comparison so retrying a permanently consumed id is never presented
    # as a recoverable head race.
    ensure_directory(root)
    if committed_transaction_exists(root, transaction_id=transaction_id) or (root / transaction_id).exists():
        raise _fail(KnowledgeFailureCode.TRANSACTION_ID_REUSED, "transaction id is already consumed")
    if knowledge_store.attempt_boundary_id(attempt_id) is not None:
        raise _fail(
            KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS,
            "attempt is already durably bound to an authoritative boundary",
        )

    if decision.snapshot_id.value != manifest.snapshot_id.value:
        raise _fail(KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH, "decision does not describe this manifest")
    if decision.manifest_sha256 != manifest.payload_sha256():
        raise _fail(KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH, "decision was made over different manifest bytes")
    require_same_context(decision.context, manifest.context, subject="completeness decision")
    if decision.roots.to_dict() != manifest.roots.to_dict():
        raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "decision root set differs from the manifest root set")

    # Only now the token, and the order is the point. "This decision is not about
    # this manifest" is a more precise diagnosis than "this token is not about
    # this decision", and it is the one an operator can act on — so the decision
    # is tied to the manifest first, and the observation to the decision second.
    if token.snapshot_id.value != decision.snapshot_id.value:
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the observation token belongs to another snapshot",
        )
    if token.evaluator_identity.value != decision.authority_identity.value:
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the observation token was taken by another evaluator",
        )
    require_same_context(token.context, decision.context, subject="observation token")
    if token.roots_digest != _root_set_digest(decision.roots):
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the observation token describes roots the decision did not rest on",
        )
    try:
        require_snapshot_status_admits_execution(decision.status)
    except ContractViolation as exc:
        raise _fail(
            KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED,
            f"completeness status {decision.status.value} does not admit a commit",
        ) from exc

    actual_parent_id = knowledge_store.current_boundary_id()
    if actual_parent_id != expected_parent_boundary_id:
        raise _fail(KnowledgeFailureCode.LINEAGE_MISMATCH, "expected parent is not the authoritative head")
    parent_snapshot: UsableKnowledgeSnapshot | None = None
    parent_boundary: AtomicSnapshotBoundary | None = None
    if actual_parent_id is not None:
        parent_snapshot = knowledge_store.open_current()
        parent_boundary = parent_snapshot.boundary
        validate_atomic_boundary(parent_boundary)
        if parent_boundary.atomic_boundary_id.digest_sha256 != actual_parent_id:
            raise _fail(KnowledgeFailureCode.LINEAGE_MISMATCH, "authoritative head resolves to another boundary")
        require_same_context(parent_boundary.context, manifest.context, subject="parent boundary")
        if start_sequence != parent_boundary.commit_sequence:
            raise _fail(
                KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC,
                "child start_sequence must equal the authoritative parent commit_sequence",
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
        if (
            parent_snapshot.admission_root_manifest is None
            or parent_snapshot.compatibility_evidence_manifest is None
        ):
            raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "current parent lacks v2 root manifests")
        parent_admission = parent_snapshot.admission_root_manifest
        parent_compatibility = parent_snapshot.compatibility_evidence_manifest
        _require_prefix_extension(
            admission_journal,
            child_anchor=admission_root_manifest.admission_history_anchor,
            child_sequence=admission_root_manifest.admission_history_sequence,
            parent_anchor=parent_admission.admission_history_anchor,
            parent_sequence=parent_admission.admission_history_sequence,
            label="admission history",
        )
        _require_prefix_extension(
            admission_causal_history,
            child_anchor=admission_root_manifest.retrieval_causal_history_anchor,
            child_sequence=admission_root_manifest.retrieval_causal_history_sequence,
            parent_anchor=parent_admission.retrieval_causal_history_anchor,
            parent_sequence=parent_admission.retrieval_causal_history_sequence,
            label="retrieval causal history",
        )
        _require_prefix_extension(
            compatibility_history,
            child_anchor=compatibility_evidence_manifest.compatibility_history_anchor,
            child_sequence=compatibility_evidence_manifest.compatibility_history_sequence,
            parent_anchor=parent_compatibility.compatibility_history_anchor,
            parent_sequence=parent_compatibility.compatibility_history_sequence,
            label="compatibility history",
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

    # A child may cover more than one underlying sequence step.  Only its start
    # is chained to the parent; the boundary validator requires commit > start.
    _sequence(start_sequence, "start_sequence")
    _sequence(commit_sequence, "commit_sequence")
    if commit_sequence <= start_sequence:
        raise _fail(KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC, "commit_sequence must exceed start_sequence")

    manifest_bytes = manifest.canonical_bytes()
    decision_bytes = decision.canonical_bytes()
    admission_root_bytes = admission_root_manifest.canonical_bytes()
    compatibility_root_bytes = compatibility_evidence_manifest.canonical_bytes()
    admission_root_ref = _root_manifest_ref(admission_root_manifest)
    compatibility_root_ref = _root_manifest_ref(compatibility_evidence_manifest)
    boundary = _make_boundary(
        transaction_id=transaction_id,
        context=manifest.context,
        roots=manifest.roots,
        admission_root_sha256=admission_root_ref.sha256,
        compatibility_evidence_root_sha256=compatibility_root_ref.sha256,
        admission_root_manifest_ref=admission_root_ref,
        compatibility_evidence_manifest_ref=compatibility_root_ref,
        manifest_ref=_manifest_ref(manifest),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        completeness_decision_ref=_decision_ref(decision),
        start_sequence=start_sequence,
        commit_sequence=commit_sequence,
        parent_boundary_digest=None if parent_boundary is None else parent_boundary.atomic_boundary_id.digest_sha256,
        commit_marker=hashlib.sha256(manifest_bytes + decision_bytes).hexdigest(),
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=decision.evaluated_at_utc,
    )
    boundary_bytes = boundary.canonical_bytes()

    with fence.exclusive() as coordinator_guard:
        if knowledge_store.current_boundary_id() != expected_parent_boundary_id:
            raise _fail(KnowledgeFailureCode.LINEAGE_MISMATCH, "authoritative head moved before commit")
        _require_world_unmoved_under_lock(evaluator, token=token, manifest=manifest)
        _confirm_durable_history_bindings(
            manifest=manifest,
            admission_root_manifest=admission_root_manifest,
            admission_journal=admission_journal,
            compatibility_evidence_manifest=compatibility_evidence_manifest,
            compatibility_history=compatibility_history,
            admission_causal_history=admission_causal_history,
        )
        try:
            # Staging and the marker are one interval, not two. A transaction that
            # closed its interval after staging would advertise a settled store in
            # the window where the members exist and the marker does not — which is
            # exactly the crash state this function is built to make unusable.
            with store_transaction(fence, guard=coordinator_guard) as ticket:
                staged = stage_snapshot_transaction(
                    root,
                    transaction_id=transaction_id,
                    members={
                        MANIFEST_MEMBER_NAME: manifest_bytes,
                        DECISION_MEMBER_NAME: decision_bytes,
                        ADMISSION_ROOT_MEMBER_NAME: admission_root_bytes,
                        COMPATIBILITY_ROOT_MEMBER_NAME: compatibility_root_bytes,
                        BOUNDARY_MEMBER_NAME: boundary_bytes,
                    },
                    ticket=ticket,
                )
                # Checked *before* the marker, and that ordering is the whole
                # point. The marker is what makes the snapshot exist, so drift
                # discovered after it has already published the thing it is
                # refusing — the first version of this check ran after the
                # commit and left a terminal marker behind on every negative
                # case. Inside an open interval the count is the token's plus
                # one; anything else means somebody completed a transaction
                # between the re-read and here.
                drifted = fence.current_epoch() != token.epoch + 1
                if not drifted:
                    commit_snapshot_transaction(
                        root,
                        transaction_id=transaction_id,
                        members=staged,
                        boundary_id=boundary.atomic_boundary_id.value,
                        marker_sha256=boundary.commit_marker,
                        ticket=ticket,
                    )
                    knowledge_store.commit_boundary(
                        boundary=boundary,
                        transaction_id=transaction_id,
                        run_id=run_id,
                        attempt_id=attempt_id,
                        expected_parent_boundary_id=expected_parent_boundary_id,
                        committed_members=staged,
                        ticket=ticket,
                    )
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeFailureCode.STORE_UNAVAILABLE,
                "boundary commit could not be durably recorded",
            ) from exc
        # Raised out here rather than inside, so the interval closes normally.
        # The store is not in an unknown state: the members are staged and no
        # marker was written, which is precisely the crash state this design
        # already treats as "not a snapshot". Abandoning the interval would hold
        # the whole coordinator closed over a condition that is fully known.
        if drifted:
            raise _fail(
                KnowledgeFailureCode.OBSERVATION_TORN,
                "a store moved during the commit that this transaction did not write",
            )
    return boundary


def _require_world_unmoved_under_lock(
    evaluator: ConfiguredSnapshotEvaluator,
    *,
    token: RootObservationToken,
    manifest: SnapshotManifest,
) -> None:
    """Re-establish, while holding the lock, that the observed world is still here.

    Three questions in the order that makes their answers mean something.

    *Whose world*: the coordinator now must be the one the token names. A second
    coordinator parked on the same even number would satisfy every check below
    while counting mutations these readers never see.

    *Which moment*: the epoch now must be the token's exactly. Not "at least" —
    a later even value means a complete mutation landed between the observation
    and this line, and the verdict was reached before it.

    *What is actually there*: the roots are read **again**, here, under the lock.
    The epoch is a count of transactions, not a description of content: a store
    restored from a backup, or repaired in place, can present different roots at
    a count the coordinator never saw move. Comparing the re-read against the
    token is what turns "nothing has been recorded as changing" into "the same
    roots are still here", and comparing it against the manifest as well is what
    stops a manifest that never matched from riding through on a token that did.
    """

    fence = evaluator._root_fence
    if fence.coordinator_id() != token.coordinator_id:
        raise _fail(
            KnowledgeFailureCode.BOUNDARY_MISMATCH,
            "the boundary is being committed under a different coordinator",
        )
    now = fence.current_epoch()
    if type(now) is not int:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "root fence reported a non-integer epoch")
    if now % 2:
        raise _fail(
            KnowledgeFailureCode.MUTATION_IN_FLIGHT_AT_ENTRY,
            "a mutation is in flight, so the commit cannot confirm the observed world",
        )
    if now != token.epoch:
        raise _fail(
            KnowledgeFailureCode.OBSERVATION_TORN,
            "an authority store moved between the completeness verdict and the commit",
        )
    try:
        current = evaluator._observed_roots_provider()
    except KnowledgeViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeFailureCode.STORE_UNAVAILABLE,
            "the store roots could not be re-read before the commit",
        ) from exc
    if type(current) is not SnapshotRootSet:
        raise _fail(
            KnowledgeFailureCode.STORE_UNAVAILABLE,
            "the re-read store roots are not an exact root set",
        )
    validate_snapshot_root_set(current)
    if _root_set_digest(current) != token.roots_digest:
        raise _fail(
            KnowledgeFailureCode.OBSERVATION_TORN,
            "the store roots differ from the ones the completeness verdict saw",
        )
    # The manifest is deliberately *not* compared here as well. It cannot differ
    # by the time this runs: the caller has already refused a decision whose
    # roots differ from the manifest's, and refused a token whose digest differs
    # from the decision's, so `current == token == decision == manifest` follows.
    # A comparison at this line would look like a further safeguard and would be
    # a condition no input can reach — which the mutation campaign showed by
    # deleting it and killing nothing.


# ---------------------------------------------------------------------------
# Restore and consumer-side revalidation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class CommittedKnowledgeSnapshotAudit:
    """Fully validated immutable snapshot transaction for audit only.

    A terminal marker proves that the transaction and its exact members were
    committed. It does not prove that an ``AuthoritativeKnowledgeStore`` made
    the boundary current or bound it to an attempt. Consequently this record
    deliberately exposes no executable references and confers no present-time
    authority. It is a runtime recovery view, not a new persisted record.
    """

    boundary: AtomicSnapshotBoundary
    manifest: SnapshotManifest
    decision: SnapshotCompletenessDecision
    admission_root_manifest: AdmissionRootManifestV2 | None
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2 | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CommittedKnowledgeSnapshotAudit:
        raise TypeError(
            "CommittedKnowledgeSnapshotAudit is produced only by "
            "open_committed_snapshot_for_audit"
        )

    @property
    def snapshot_id(self) -> RecordId:
        return self.manifest.snapshot_id

    @property
    def atomic_boundary_id(self) -> RecordId:
        return self.boundary.atomic_boundary_id

    @property
    def completeness_status(self) -> SnapshotCompletenessStatus:
        return self.decision.status


@dataclass(frozen=True, init=False)
class UsableKnowledgeSnapshot:
    """The authoritative-store-produced shape a consumer may execute against.

    The immutable transaction has been fully audited and its exact boundary,
    member manifest, transaction identity and run/attempt binding have also
    resolved through an authoritative store frame. ``completeness_status`` is
    exposed from the authoritative decision rather than inferred from the
    manifest.
    """

    boundary: AtomicSnapshotBoundary
    manifest: SnapshotManifest
    decision: SnapshotCompletenessDecision
    admission_root_manifest: AdmissionRootManifestV2 | None
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2 | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> UsableKnowledgeSnapshot:
        raise TypeError(
            "UsableKnowledgeSnapshot is produced only by AuthoritativeKnowledgeStore"
        )

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


def _make_committed_audit(
    boundary: AtomicSnapshotBoundary,
    manifest: SnapshotManifest,
    decision: SnapshotCompletenessDecision,
    admission_root_manifest: AdmissionRootManifestV2 | None = None,
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2 | None = None,
) -> CommittedKnowledgeSnapshotAudit:
    result = object.__new__(CommittedKnowledgeSnapshotAudit)
    object.__setattr__(result, "boundary", boundary)
    object.__setattr__(result, "manifest", manifest)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "admission_root_manifest", admission_root_manifest)
    object.__setattr__(
        result,
        "compatibility_evidence_manifest",
        compatibility_evidence_manifest,
    )
    object.__setattr__(result, "_trusted_seal", _COMMITTED_AUDIT_SEAL)
    return result


def _authoritative_usable_snapshot(
    value: CommittedKnowledgeSnapshotAudit,
) -> UsableKnowledgeSnapshot:
    """Private adapter seam used only after an authoritative frame is verified.

    ``knowledge_store.py`` owns the frame, current-head and attempt-binding
    checks. This owner keeps construction closed and accepts only its own sealed
    audit result, so the adapter can expose the authoritative runtime shape
    without introducing another durable grant or persisted schema.
    """

    if (
        type(value) is not CommittedKnowledgeSnapshotAudit
        or getattr(value, "_trusted_seal", None) is not _COMMITTED_AUDIT_SEAL
    ):
        raise _fail(
            KnowledgeFailureCode.TRUSTED_OBJECT_FORGED,
            "authoritative store supplied an unsealed committed snapshot audit",
        )
    result = object.__new__(UsableKnowledgeSnapshot)
    object.__setattr__(result, "boundary", value.boundary)
    object.__setattr__(result, "manifest", value.manifest)
    object.__setattr__(result, "decision", value.decision)
    object.__setattr__(result, "admission_root_manifest", value.admission_root_manifest)
    object.__setattr__(
        result,
        "compatibility_evidence_manifest",
        value.compatibility_evidence_manifest,
    )
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


def open_committed_snapshot_for_audit(
    root: Path,
    *,
    transaction_id: str,
) -> CommittedKnowledgeSnapshotAudit:
    """Restore and fully validate immutable snapshot bytes for audit.

    Recovery recomputes every identity from committed bytes. A staged but
    unmarked transaction, a mutated member, a decision that is not ``COMPLETE``
    or a boundary whose roots disagree with its manifest all fail closed; none
    of them is repaired by substituting an older root. A successful result says
    nothing about an authoritative frame, a current head or an attempt binding,
    and therefore carries no executable capability.
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
        # Everything reaching here used to become STORE_UNAVAILABLE, which tells
        # an operator to retry. Most of these are not retryable, and the audit
        # found the case I had already reported and carried forward: a committed
        # member that *grew* trips the byte limit before any digest is compared,
        # so an edited file was announced as a store that could not be reached.
        # NR-10 forbids the substitution, and a wrong instruction is worse than a
        # vague one — a retry against corruption is work that cannot succeed.
        if exc.failure_code is PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED:
            raise _fail(
                KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED,
                "a committed snapshot member no longer has the size it was written with",
            ) from exc
        if exc.failure_code in {
            PersistenceFailureCode.JOURNAL_MAGIC_MISMATCH,
            PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH,
            PersistenceFailureCode.JOURNAL_TORN_TAIL,
        }:
            raise _fail(
                KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED,
                "the committed snapshot store is damaged rather than unreachable",
            ) from exc
        if exc.failure_code in {
            PersistenceFailureCode.NON_REGULAR_ENTRY,
            PersistenceFailureCode.LINK_OR_REPARSE_POINT,
            PersistenceFailureCode.INVALID_PATH,
            PersistenceFailureCode.TYPE_MISMATCH,
        }:
            raise _fail(
                KnowledgeFailureCode.PARTIAL_MANIFEST,
                "the committed snapshot path does not hold a readable transaction",
            ) from exc
        raise _fail(KnowledgeFailureCode.STORE_UNAVAILABLE, "committed snapshot could not be read") from exc

    v1_members = {MANIFEST_MEMBER_NAME, DECISION_MEMBER_NAME, BOUNDARY_MEMBER_NAME}
    v2_members = v1_members | {ADMISSION_ROOT_MEMBER_NAME, COMPATIBILITY_ROOT_MEMBER_NAME}
    member_names = frozenset(members)
    if member_names not in {frozenset(v1_members), frozenset(v2_members)}:
        raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "committed transaction member set is incomplete")

    manifest = snapshot_manifest_from_dict(_decode_member(members[MANIFEST_MEMBER_NAME], "manifest"))
    decision = _restore_decision(_decode_member(members[DECISION_MEMBER_NAME], "decision"), manifest=manifest)
    boundary = _restore_boundary(_decode_member(members[BOUNDARY_MEMBER_NAME], "boundary"))
    admission_root_manifest: AdmissionRootManifestV2 | None = None
    compatibility_evidence_manifest: CompatibilityEvidenceManifestV2 | None = None
    if manifest.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V2:
        if set(members) != v2_members:
            raise _fail(KnowledgeFailureCode.PARTIAL_MANIFEST, "v2 snapshot root manifests are absent")
        admission_root_manifest = admission_root_manifest_from_dict(
            _decode_member(members[ADMISSION_ROOT_MEMBER_NAME], "admission root manifest")
        )
        compatibility_evidence_manifest = compatibility_evidence_manifest_from_dict(
            _decode_member(members[COMPATIBILITY_ROOT_MEMBER_NAME], "compatibility root manifest")
        )
    elif set(members) != v1_members:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "v1 audit snapshot cannot carry v2 root manifests")

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
    if manifest.schema_version is SchemaVersion.KNOWLEDGE_SNAPSHOT_V2:
        assert admission_root_manifest is not None and compatibility_evidence_manifest is not None
        admission_ref = _root_manifest_ref(admission_root_manifest)
        compatibility_ref = _root_manifest_ref(compatibility_evidence_manifest)
        if manifest.roots.admission_root_manifest_ref.to_dict() != admission_ref.to_dict():
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "manifest admission root ref differs from committed bytes")
        if manifest.roots.compatibility_evidence_manifest_ref.to_dict() != compatibility_ref.to_dict():
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "manifest compatibility root ref differs from committed bytes")
        if boundary.admission_root_manifest_ref.to_dict() != admission_ref.to_dict():
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "boundary admission root ref differs from committed bytes")
        if boundary.compatibility_evidence_manifest_ref.to_dict() != compatibility_ref.to_dict():
            raise _fail(KnowledgeFailureCode.ROOT_SET_MISMATCH, "boundary compatibility root ref differs from committed bytes")
        execution_envelopes = (
            decision.envelope,
            boundary.envelope,
            admission_root_manifest.envelope,
            compatibility_evidence_manifest.envelope,
        )
        assert manifest.envelope is not None
        for envelope in execution_envelopes:
            if envelope is None or envelope.run_id != manifest.envelope.run_id or envelope.attempt_id != manifest.envelope.attempt_id:
                raise _fail(KnowledgeFailureCode.CONTEXT_MISMATCH, "committed v2 records belong to different executions")
        if admission_root_manifest.coordinator_id != compatibility_evidence_manifest.coordinator_id:
            raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "committed root manifests use different coordinators")
        if admission_root_manifest.authority_configuration_id != compatibility_evidence_manifest.authority_configuration_id:
            raise _fail(KnowledgeFailureCode.MIX_AND_MATCH_DETECTED, "committed root manifests use different authority configurations")
    try:
        require_snapshot_status_admits_execution(decision.status)
    except ContractViolation as exc:
        raise _fail(
            KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED,
            f"restored completeness status {decision.status.value} does not admit execution",
        ) from exc

    return _make_committed_audit(
        boundary,
        manifest,
        decision,
        admission_root_manifest,
        compatibility_evidence_manifest,
    )


def _restore_decision(value: object, *, manifest: SnapshotManifest) -> SnapshotCompletenessDecision:
    wrapped = type(value) is dict and set(value) == {
        "envelope",
        "envelope_binding_sha256",
        "payload",
    }
    payload = value["payload"] if wrapped else value
    if type(payload) is not dict:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "completeness decision payload must be an exact dict")
    try:
        schema = SchemaVersion(payload.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision schema is unknown") from exc
    fields = (
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
    )
    if schema is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2:
        fields += (
            "authority_configuration_sha256",
            "evaluator_declaration_sha256",
            "evaluator_actor_set_sha256",
            "evaluator_independence_proof_sha256",
        )
    data = _exact_dict(
        payload,
        fields,
        "completeness_decision",
    )
    if schema not in {
        SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V1,
        SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2,
    }:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision schema is unknown")
    if wrapped != (schema is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2):
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "completeness decision wrapper does not match schema")
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
    object.__setattr__(result, "schema_version", schema)
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
    for name in (
        "authority_configuration_sha256",
        "evaluator_declaration_sha256",
        "evaluator_actor_set_sha256",
        "evaluator_independence_proof_sha256",
    ):
        object.__setattr__(result, name, data[name] if schema is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2 else None)
    object.__setattr__(result, "evaluated_at_utc", evaluated)
    object.__setattr__(result, "detail", data["detail"])
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    if schema is SchemaVersion.SNAPSHOT_COMPLETENESS_DECISION_V2:
        try:
            envelope = common_envelope_from_dict(
                value["envelope"], canonical_payload_bytes=_canonical(data)
            )
        except ContractViolation as exc:
            raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "decision envelope is invalid") from exc
        object.__setattr__(result, "envelope", envelope)
        object.__setattr__(result, "envelope_binding_sha256", value["envelope_binding_sha256"])
        object.__setattr__(result, "decision_id", envelope.record_id)
    else:
        object.__setattr__(result, "envelope", None)
        object.__setattr__(result, "envelope_binding_sha256", None)
        object.__setattr__(
            result,
            "decision_id",
            compute_record_id(
                domain=IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION,
                canonical_bytes=_canonical(_decision_payload(result)),
            ),
        )
    validate_completeness_decision(result)
    if result.canonical_bytes() != _canonical(value):
        raise _fail(KnowledgeFailureCode.DECISION_NOT_AUTHORITATIVE, "decision bytes are not canonical")
    return result


def _authority_from_dict(value: object) -> AuthorityIdentity:
    try:
        return AuthorityIdentity.from_dict(value)
    except ContractViolation as exc:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "decision authority identity is invalid") from exc


def _restore_boundary(value: object) -> AtomicSnapshotBoundary:
    wrapped = type(value) is dict and set(value) == {
        "envelope",
        "envelope_binding_sha256",
        "payload",
    }
    payload = value["payload"] if wrapped else value
    if type(payload) is not dict:
        raise _fail(KnowledgeFailureCode.TYPE_MISMATCH, "atomic boundary payload is invalid")
    raw_schema = payload.get("schema_version")
    if raw_schema == SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V1.value:
        fields = (
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
        )
    elif raw_schema == SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2.value:
        fields = (
            "schema_version",
            "transaction_id",
            "context",
            "roots",
            "admission_root_manifest_ref",
            "compatibility_evidence_manifest_ref",
            "manifest_ref",
            "manifest_sha256",
            "completeness_decision_ref",
            "start_sequence",
            "commit_sequence",
            "parent_boundary_digest",
            "commit_marker",
        )
    else:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "atomic boundary schema is unknown")
    data = _exact_dict(
        payload,
        fields,
        "atomic_boundary",
    )
    current = raw_schema == SchemaVersion.ATOMIC_SNAPSHOT_BOUNDARY_V2.value
    if wrapped != current:
        raise _fail(KnowledgeFailureCode.UNKNOWN_SCHEMA_VERSION, "atomic boundary wrapper does not match schema")
    raw_parent = data["parent_boundary_digest"]
    admission_ref = HashBoundRef.from_dict(data["admission_root_manifest_ref"]) if current else None
    compatibility_ref = HashBoundRef.from_dict(data["compatibility_evidence_manifest_ref"]) if current else None
    restored_envelope: CommonEnvelope | None = None
    restored_binding: str | None = None
    if current:
        try:
            restored_envelope = common_envelope_from_dict(
                value["envelope"], canonical_payload_bytes=_canonical(data)
            )
        except ContractViolation as exc:
            raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "boundary envelope is invalid") from exc
        restored_binding = value["envelope_binding_sha256"]
    result = _make_boundary(
        transaction_id=data["transaction_id"],
        context=KnowledgeContext.from_dict(data["context"]),
        roots=SnapshotRootSet.from_dict(data["roots"]),
        admission_root_sha256=data["admission_root_sha256"] if not current else admission_ref.sha256,
        compatibility_evidence_root_sha256=(
            data["compatibility_evidence_root_sha256"] if not current else compatibility_ref.sha256
        ),
        admission_root_manifest_ref=admission_ref,
        compatibility_evidence_manifest_ref=compatibility_ref,
        manifest_ref=HashBoundRef.from_dict(data["manifest_ref"]),
        manifest_sha256=data["manifest_sha256"],
        completeness_decision_ref=HashBoundRef.from_dict(data["completeness_decision_ref"]),
        start_sequence=data["start_sequence"],
        commit_sequence=data["commit_sequence"],
        parent_boundary_digest=raw_parent,
        commit_marker=data["commit_marker"],
        restored_envelope=restored_envelope,
        restored_envelope_binding=restored_binding,
    )
    if result.canonical_bytes() != _canonical(value):
        raise _fail(KnowledgeFailureCode.BOUNDARY_MISMATCH, "boundary bytes are not canonical")
    return result


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
    question and is answered only by resolving the exact authoritative frame —
    see ``AuthoritativeKnowledgeStore.open_current`` and
    ``AuthoritativeKnowledgeStore.open_for_attempt``.
    """

    if type(value) is not UsableKnowledgeSnapshot or getattr(value, "_trusted_seal", None) is not _USABLE_SEAL:
        raise _fail(KnowledgeFailureCode.TRUSTED_OBJECT_FORGED, "usable snapshot is not restore sealed")
    validate_atomic_boundary(value.boundary)
    validate_snapshot_manifest(value.manifest)
    validate_completeness_decision(value.decision)
    if type(attempt_boundary_id) is not RecordId or attempt_boundary_id.domain not in {
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY_V2,
    }:
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
    "ADMISSION_ROOT_MEMBER_NAME",
    "COMPATIBILITY_ROOT_MEMBER_NAME",
    "DECISION_MEMBER_NAME",
    "KNOWLEDGE_CONTEXT_V1",
    "MANIFEST_MEMBER_NAME",
    "SNAPSHOT_ROOT_SET_V1",
    "SNAPSHOT_ROOT_SET_V2",
    "AdmissionRootManifestV2",
    "CompatibilityEvidenceManifestV2",
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
    "CommittedKnowledgeSnapshotAudit",
    "UsableKnowledgeSnapshot",
    "atomic_boundary_ref",
    "snapshot_manifest_ref",
    "commit_atomic_snapshot_boundary",
    "compatibility_evidence_root",
    "configure_snapshot_evaluator",
    "create_knowledge_context",
    "create_admission_root_manifest",
    "create_compatibility_evidence_manifest",
    "create_snapshot_manifest",
    "create_snapshot_root_set",
    "detect_mixed_generation",
    "detect_root_regression",
    "CompletenessEvaluation",
    "RootObservationToken",
    "evaluate_snapshot_completeness",
    "validate_completeness_evaluation",
    "validate_root_observation_token",
    "open_committed_snapshot_for_audit",
    "require_current_snapshot_evaluator_entitlement",
    "require_same_context",
    "require_snapshot_bound_to_attempt",
    "snapshot_manifest_from_dict",
    "admission_root_manifest_from_dict",
    "compatibility_evidence_manifest_from_dict",
    "validate_atomic_boundary",
    "validate_completeness_decision",
    "validate_knowledge_context",
    "validate_snapshot_manifest",
    "validate_snapshot_root_set",
]
