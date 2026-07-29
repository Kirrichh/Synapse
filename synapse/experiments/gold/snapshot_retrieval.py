"""Stage 4 candidate discovery, deterministic ranking, and verified loading.

Index and semantic-score data remain non-authoritative. This module stops
before replay, worker dispatch, or host execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Callable, Protocol

from .behavior import BehaviorKind
from .bindings import (
    BindingKind,
    DocumentBinding,
    PythonBinding,
    RequirementBinding,
    binding_to_ref,
)
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    ContentKey,
    RefKind,
    canonicalize_stage4_payload,
)
from .compatibility import (
    COMPATIBILITY_POLICY_V1,
    CompatibilityConflictScan,
    CompatibilityContext,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityFailureCode,
    CompatibilityRevalidationRecord,
    CompatibilitySubjectDescriptor,
    CompatibilityViolation,
    ConfiguredCompatibilityEvaluator,
    ConflictDecisionKind,
    ConflictEvidenceProposal,
    RevalidationOutcome,
    RevalidationStage,
    evaluate_compatibility,
    evaluate_conflicts,
    reconcile_index_entry,
    require_configured_compatibility_evaluator,
    require_revalidation_passed,
    revalidate_before_consumption,
    revalidate_before_loading,
    validate_compatibility_conflict_scan,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_revalidation_record,
    validate_compatibility_subject_descriptor,
    validate_loaded_compatibility_subject,
)
from .compatibility_store import (
    CompatibilityDurabilityBinding,
    snapshot_bound_compatibility_artifact_bytes,
    snapshot_bound_compatibility_artifact_ref,
)
from .snapshot_compatibility import (
    CommittedCompatibilityRevalidation,
    CommittedSnapshotBoundCompatibility,
    SnapshotBoundCompatibilityContext,
    SnapshotBoundCompatibilityEvidence,
    evaluate_and_commit_snapshot_bound_compatibility,
    revalidate_and_commit_snapshot_bound_compatibility,
    require_snapshot_bound_compatibility_passed,
)
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    CommonEnvelope,
    ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    ENVELOPED_ARTIFACT_SCHEMA_V1,
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    ProposalId,
    RecordId,
    SchemaVersion,
    Stage4AuthorityHandle,
    create_common_envelope,
    compute_record_id,
    require_stage4_authority_handle,
    validate_common_envelope,
    validate_record_id,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    validate_knowledge_admission_authority_binding,
)
from .admission import (
    ConfiguredConsumptionGateEvaluator,
    ConfiguredRetrievalGateEvaluator,
    evaluate_consumption_gate,
    evaluate_retrieval_gate,
)
from .admission_contracts import (
    ConsumptionDecision,
    ConsumptionGateReason,
    ConsumptionGateRequest,
    GateAuthorityHeads,
    GateCheckedDimension,
    GATE_AUTHORITY_HEADS_SCHEMA_V1,
    GATE_CHECKED_DIMENSION_SCHEMA_V1,
    GateConsumerContext,
    GateDecisionKind,
    GateDimensionResult,
    KnowledgeBoundaryResolution,
    RetrievalGateDecision,
    RetrievalGateReason,
    RetrievalGateRequest,
    SealedRetrievalAuthorityResolver,
    consumption_gate_request_payload,
    create_consumption_gate_request,
    create_retrieval_gate_request,
    seal_knowledge_boundary_resolver,
    seal_retrieval_authority_resolver,
    retrieval_gate_request_payload,
)
from .admission_store import (
    AdmissionCommitEvidence,
    AdmittedKnowledgeHandle,
    RetrievalAdmissionBinding,
    create_admitted_knowledge_handle,
    gate_context_fingerprint,
    validate_retrieval_admission_binding,
)
from .knowledge_contracts import (
    AtomicSnapshotBoundary,
    ConfiguredSnapshotCompletenessEvaluator,
    RepositoryKnowledgeSnapshot,
    SnapshotManifestCore,
    SnapshotObjectStatus,
    SnapshotViewEntry,
    validate_repository_knowledge_snapshot,
    validate_snapshot_manifest_core,
)
from .knowledge_store import (
    CommittedAtomicSnapshotBoundary,
    KnowledgeSnapshotStore,
    require_committed_atomic_snapshot_boundary,
)
from .library import (
    MAX_INDEX_ENTRIES_V1,
    BehaviorLibrary,
    IndexEntry,
    LibrarySnapshot,
    LibraryViolation,
    SnapshotVerificationStatus,
    VerifiedBehaviorRecord,
    validate_snapshot_verification,
    validate_verified_behavior_record,
)


RETRIEVAL_QUERY_V1 = "synapse.stage4.gold.retrieval-query/v1"
RETRIEVAL_BINDING_TARGET_V1 = "synapse.stage4.gold.retrieval-binding-target/v1"
RETRIEVAL_CANDIDATE_V1 = "synapse.stage4.gold.retrieval-candidate/v1"
RANKING_FEATURE_OBSERVATION_V1 = "synapse.stage4.gold.ranking-feature-observation/v1"
RETRIEVAL_CONFLICT_RECORD_V1 = "synapse.stage4.gold.retrieval-conflict-record/v1"
RETRIEVAL_DECISION_V1 = "synapse.stage4.gold.retrieval-decision/v1"
RETRIEVAL_LOAD_DECISION_V1 = "synapse.stage4.gold.retrieval-load-decision/v1"
RETRIEVAL_POLICY_V1 = "synapse.stage4.gold.retrieval-policy/v1"
RANKING_PROFILE_V1 = "synapse.stage4.gold.ranking-profile/v1"
RETRIEVAL_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.retrieval+json"
RETRIEVAL_QUERY_V2 = "synapse.stage4.gold.retrieval-query/v2"
RETRIEVAL_DECISION_V2 = "synapse.stage4.gold.retrieval-decision/v2"
RETRIEVAL_LOAD_DECISION_V2 = (
    "synapse.stage4.gold.retrieval-load-decision/v2"
)

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
from .retrieval import *
from .retrieval import _canonical, _fail, _record, _safe_id, _timestamp

def _retrieval_artifact_bytes(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> bytes:
    payload_bytes = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=payload_bytes)
    return _canonical({"envelope": envelope.to_dict(), "payload": payload})


def _retrieval_artifact_ref(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> HashBoundRef:
    raw = _retrieval_artifact_bytes(envelope, payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    )


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalQuery:
    schema_version: str
    envelope: CommonEnvelope
    query_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    compatibility_context_ref: HashBoundRef
    requested_behavior_kinds: tuple[BehaviorKind, ...]
    required_binding_targets: tuple[RetrievalBindingTarget, ...]
    selected_set_limit: int
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    _retriever: ConfiguredRetriever
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalQuery:
        raise TypeError("SnapshotBoundRetrievalQuery is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_query(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": snapshot_bound_retrieval_query_payload(
                repository_knowledge_snapshot_id=(
                    self.repository_knowledge_snapshot_id
                ),
                atomic_boundary_id=self.atomic_boundary_id,
                boundary_commit_sequence=self.boundary_commit_sequence,
                compatibility_context_ref=self.compatibility_context_ref,
                requested_behavior_kinds=self.requested_behavior_kinds,
                required_binding_targets=self.required_binding_targets,
                selected_set_limit=self.selected_set_limit,
                consumer_context=self.consumer_context,
                authority_heads=self.authority_heads,
            ),
        }


def snapshot_bound_retrieval_query_payload(
    *,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    compatibility_context_ref: HashBoundRef,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[RetrievalBindingTarget, ...],
    selected_set_limit: int,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
) -> dict[str, object]:
    _record(
        repository_knowledge_snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "repository knowledge snapshot id",
    )
    _record(
        atomic_boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "atomic boundary id",
    )
    if type(boundary_commit_sequence) is not int or boundary_commit_sequence < 1:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "boundary commit sequence is invalid",
        )
    if (
        type(compatibility_context_ref) is not HashBoundRef
        or compatibility_context_ref.kind is not RefKind.ARTIFACT
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "compatibility context ref is invalid",
        )
    if (
        type(requested_behavior_kinds) is not tuple
        or not requested_behavior_kinds
        or any(type(item) is not BehaviorKind for item in requested_behavior_kinds)
        or requested_behavior_kinds
        != tuple(sorted(set(requested_behavior_kinds), key=lambda item: item.value))
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query behavior kinds are invalid",
        )
    if type(required_binding_targets) is not tuple:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query binding targets are mutable",
        )
    for item in required_binding_targets:
        validate_retrieval_binding_target(item)
    if required_binding_targets != tuple(
        sorted(required_binding_targets, key=lambda item: _canonical(item.to_dict()))
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query binding targets are not canonical",
        )
    if type(selected_set_limit) is not int or not 1 <= selected_set_limit <= MAX_INDEX_ENTRIES_V1:
        raise _fail(
            RetrievalFailureCode.SELECTION_LIMIT_INVALID,
            "snapshot query selected-set limit is invalid",
        )
    consumer_context.__post_init__()
    authority_heads.to_dict()
    return {
        "schema_version": RETRIEVAL_QUERY_V2,
        "repository_knowledge_snapshot_id": (
            repository_knowledge_snapshot_id.to_dict()
        ),
        "atomic_boundary_id": atomic_boundary_id.to_dict(),
        "boundary_commit_sequence": boundary_commit_sequence,
        "compatibility_context_ref": compatibility_context_ref.to_dict(),
        "requested_behavior_kinds": [
            item.value for item in requested_behavior_kinds
        ],
        "required_binding_targets": [
            item.to_dict() for item in required_binding_targets
        ],
        "selected_set_limit": selected_set_limit,
        "consumer_context": consumer_context.to_dict(),
        "authority_heads": authority_heads.to_dict(),
    }


def create_retrieval_query(
    *,
    retriever: ConfiguredRetriever,
    envelope: CommonEnvelope,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    compatibility_context: SnapshotBoundCompatibilityContext,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[RetrievalBindingTarget, ...],
    selected_set_limit: int,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
) -> SnapshotBoundRetrievalQuery:
    require_configured_retriever(retriever)
    compatibility_ref = snapshot_bound_compatibility_artifact_ref(
        compatibility_context
    )
    result = object.__new__(SnapshotBoundRetrievalQuery)
    object.__setattr__(result, "schema_version", RETRIEVAL_QUERY_V2)
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(
        result,
        "repository_knowledge_snapshot_id",
        repository_knowledge_snapshot_id,
    )
    object.__setattr__(result, "atomic_boundary_id", atomic_boundary_id)
    object.__setattr__(
        result,
        "boundary_commit_sequence",
        boundary_commit_sequence,
    )
    object.__setattr__(result, "compatibility_context_ref", compatibility_ref)
    object.__setattr__(
        result,
        "requested_behavior_kinds",
        requested_behavior_kinds,
    )
    object.__setattr__(
        result,
        "required_binding_targets",
        required_binding_targets,
    )
    object.__setattr__(result, "selected_set_limit", selected_set_limit)
    object.__setattr__(result, "consumer_context", consumer_context)
    object.__setattr__(result, "authority_heads", authority_heads)
    object.__setattr__(result, "_retriever", retriever)
    payload = snapshot_bound_retrieval_query_payload(
        repository_knowledge_snapshot_id=repository_knowledge_snapshot_id,
        atomic_boundary_id=atomic_boundary_id,
        boundary_commit_sequence=boundary_commit_sequence,
        compatibility_context_ref=compatibility_ref,
        requested_behavior_kinds=requested_behavior_kinds,
        required_binding_targets=required_binding_targets,
        selected_set_limit=selected_set_limit,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
    )
    validate_common_envelope(envelope, canonical_payload_bytes=_canonical(payload))
    if envelope.record_id.domain is not IdentityDomain.RETRIEVAL_QUERY_V2:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "snapshot retrieval query envelope domain is invalid",
        )
    object.__setattr__(result, "query_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_retrieval_query(result)
    return result


def validate_snapshot_bound_retrieval_query(
    value: SnapshotBoundRetrievalQuery,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalQuery
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != RETRIEVAL_QUERY_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval query is not factory sealed",
        )
    require_configured_retriever(value._retriever)
    payload = snapshot_bound_retrieval_query_payload(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        boundary_commit_sequence=value.boundary_commit_sequence,
        compatibility_context_ref=value.compatibility_context_ref,
        requested_behavior_kinds=value.requested_behavior_kinds,
        required_binding_targets=value.required_binding_targets,
        selected_set_limit=value.selected_set_limit,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
    )
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.query_id != value.envelope.record_id
        or value.query_id.domain is not IdentityDomain.RETRIEVAL_QUERY_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval query identity changed",
        )


@dataclass(frozen=True)
class ResolvedSnapshotCandidate:
    view_entry: SnapshotViewEntry
    index_entry: IndexEntry
    descriptor: CompatibilitySubjectDescriptor
    content_key: ContentKey
    manifest_id: RecordId
    subject_ref: HashBoundRef
    compatibility_context: SnapshotBoundCompatibilityContext
    historical_compatibility_context: CompatibilityContext
    source_evidence_refs: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        self.view_entry.__post_init__()
        if self.view_entry.status is not SnapshotObjectStatus.EXECUTABLE:
            raise _fail(
                RetrievalFailureCode.LOADING_FORBIDDEN,
                "resolved candidate is not in the executable view",
            )
        IndexEntry.from_dict(self.index_entry.to_dict())
        validate_compatibility_subject_descriptor(self.descriptor)
        self.content_key.to_dict()
        _record(
            self.manifest_id,
            IdentityDomain.BEHAVIOR_MANIFEST,
            "candidate manifest id",
        )
        if (
            self.view_entry.content_identity != self.content_key.value
            or self.view_entry.manifest_identity != self.manifest_id.value
            or self.index_entry.content_key != self.content_key.value
            or self.index_entry.manifest_id != self.manifest_id.value
            or self.index_entry.behavior_kind != self.view_entry.behavior_kind
        ):
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH,
                "frozen view, index entry, and trusted identities differ",
            )
        if type(self.subject_ref) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_MISSING,
                "resolved candidate subject ref is invalid",
            )
        if (
            type(self.source_evidence_refs) is not tuple
            or not self.source_evidence_refs
            or any(type(item) is not HashBoundRef for item in self.source_evidence_refs)
        ):
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_MISSING,
                "resolved candidate evidence refs are incomplete",
            )


@dataclass(frozen=True)
class SnapshotRankingObservation:
    schema_version: str
    query_id: RecordId
    compatibility_context_id: RecordId
    descriptor_id: RecordId
    score_input_ref: HashBoundRef
    semantic_score_micros: int
    scorer_component_id: str
    scorer_component_version: str
    scoring_profile: str
    observation_id: RecordId

    def __post_init__(self) -> None:
        if self.schema_version != RANKING_FEATURE_OBSERVATION_V1:
            raise _fail(
                RetrievalFailureCode.UNKNOWN_SCHEMA,
                "snapshot ranking observation schema is unknown",
            )
        _record(self.query_id, IdentityDomain.RETRIEVAL_QUERY_V2, "ranking query")
        _record(
            self.compatibility_context_id,
            IdentityDomain.COMPATIBILITY_CONTEXT_V2,
            "ranking compatibility context",
        )
        _record(
            self.descriptor_id,
            IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR,
            "ranking descriptor",
        )
        _score(self.semantic_score_micros)
        payload = {
            "schema_version": self.schema_version,
            "query_id": self.query_id.to_dict(),
            "compatibility_context_id": self.compatibility_context_id.to_dict(),
            "descriptor_id": self.descriptor_id.to_dict(),
            "score_input_ref": self.score_input_ref.to_dict(),
            "semantic_score_micros": self.semantic_score_micros,
            "scorer_component_id": self.scorer_component_id,
            "scorer_component_version": self.scorer_component_version,
            "scoring_profile": self.scoring_profile,
        }
        validate_record_id(self.observation_id, canonical_bytes=_canonical(payload))

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "query_id": self.query_id.to_dict(),
            "compatibility_context_id": self.compatibility_context_id.to_dict(),
            "descriptor_id": self.descriptor_id.to_dict(),
            "score_input_ref": self.score_input_ref.to_dict(),
            "semantic_score_micros": self.semantic_score_micros,
            "scorer_component_id": self.scorer_component_id,
            "scorer_component_version": self.scorer_component_version,
            "scoring_profile": self.scoring_profile,
            "observation_id": self.observation_id.to_dict(),
        }


def _observe_snapshot_ranking(
    provider: ConfiguredRankingFeatureProvider,
    *,
    query: SnapshotBoundRetrievalQuery,
    context: SnapshotBoundCompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
) -> SnapshotRankingObservation:
    require_configured_ranking_feature_provider(provider)
    validate_snapshot_bound_retrieval_query(query)
    validate_compatibility_subject_descriptor(descriptor)
    score_input = provider._input_resolver(query.query_id, descriptor.descriptor_id)
    if type(score_input) is not HashBoundRef:
        raise _fail(
            RetrievalFailureCode.RANKING_INPUT_MISSING,
            "snapshot score input resolver returned no exact ref",
        )
    score_ref = HashBoundRef.from_dict(score_input.to_dict())
    score = _score(provider._scorer(query.query_id, descriptor.descriptor_id, score_ref))
    payload = {
        "schema_version": RANKING_FEATURE_OBSERVATION_V1,
        "query_id": query.query_id.to_dict(),
        "compatibility_context_id": context.context_id.to_dict(),
        "descriptor_id": descriptor.descriptor_id.to_dict(),
        "score_input_ref": score_ref.to_dict(),
        "semantic_score_micros": score,
        "scorer_component_id": provider._component_id,
        "scorer_component_version": provider._component_version,
        "scoring_profile": provider._scoring_profile,
    }
    result = SnapshotRankingObservation(
        RANKING_FEATURE_OBSERVATION_V1,
        query.query_id,
        context.context_id,
        descriptor.descriptor_id,
        score_ref,
        score,
        provider._component_id,
        provider._component_version,
        provider._scoring_profile,
        compute_record_id(
            domain=IdentityDomain.RETRIEVAL_RANKING_FEATURE,
            canonical_bytes=_canonical(payload),
        ),
    )
    result.__post_init__()
    return result


@dataclass(frozen=True)
class SnapshotCandidateAudit:
    schema_version: str
    content_identity: str
    manifest_identity: str
    compatibility_decision_ref: HashBoundRef
    retrieval_gate_decision_ref: HashBoundRef
    gate_outcome: GateDecisionKind
    failure_reason: str | None
    ranking_observation: SnapshotRankingObservation | None
    selected: bool

    def __post_init__(self) -> None:
        if self.schema_version != "synapse.stage4.gold.snapshot-candidate-audit/v1":
            raise _fail(
                RetrievalFailureCode.UNKNOWN_SCHEMA,
                "snapshot candidate audit schema is unknown",
            )
        _safe_id(self.content_identity, "candidate content identity")
        _safe_id(self.manifest_identity, "candidate manifest identity")
        if type(self.compatibility_decision_ref) is not HashBoundRef or type(
            self.retrieval_gate_decision_ref
        ) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate authority refs are invalid",
            )
        if type(self.gate_outcome) is not GateDecisionKind:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate gate outcome is invalid",
            )
        if self.failure_reason is not None:
            _safe_id(self.failure_reason, "candidate failure reason")
        if self.ranking_observation is not None:
            self.ranking_observation.__post_init__()
        if type(self.selected) is not bool:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate selected state is invalid",
            )
        if (
            self.gate_outcome is not GateDecisionKind.ADMIT
            and (self.ranking_observation is not None or self.selected)
        ):
            raise _fail(
                RetrievalFailureCode.LOADING_FORBIDDEN,
                "non-admitted candidate acquired ranking or selection",
            )

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "content_identity": self.content_identity,
            "manifest_identity": self.manifest_identity,
            "compatibility_decision_ref": self.compatibility_decision_ref.to_dict(),
            "retrieval_gate_decision_ref": self.retrieval_gate_decision_ref.to_dict(),
            "gate_outcome": self.gate_outcome.value,
            "failure_reason": self.failure_reason,
            "ranking_observation": (
                None
                if self.ranking_observation is None
                else self.ranking_observation.to_dict()
            ),
            "selected": self.selected,
        }


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: RecordId
    query_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    considered_candidates: tuple[SnapshotCandidateAudit, ...]
    selected_content_identities: tuple[str, ...]
    conflict_scan_ref: HashBoundRef
    created_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalDecision:
        raise TypeError("SnapshotBoundRetrievalDecision is retriever-created")

    def payload_dict(self) -> dict[str, object]:
        return {
            "schema_version": RETRIEVAL_DECISION_V2,
            "query_id": self.query_id.to_dict(),
            "repository_knowledge_snapshot_id": (
                self.repository_knowledge_snapshot_id.to_dict()
            ),
            "atomic_boundary_id": self.atomic_boundary_id.to_dict(),
            "boundary_commit_sequence": self.boundary_commit_sequence,
            "considered_candidates": [
                item.to_dict() for item in self.considered_candidates
            ],
            "selected_content_identities": list(
                self.selected_content_identities
            ),
            "conflict_scan_ref": self.conflict_scan_ref.to_dict(),
            "created_at_utc": _timestamp_text(self.created_at_utc),
        }

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def validate_snapshot_bound_retrieval_decision(
    value: SnapshotBoundRetrievalDecision,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalDecision
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != RETRIEVAL_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval decision is not retriever sealed",
        )
    _record(value.query_id, IdentityDomain.RETRIEVAL_QUERY_V2, "decision query")
    _record(
        value.repository_knowledge_snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "decision snapshot",
    )
    _record(
        value.atomic_boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "decision boundary",
    )
    if type(value.considered_candidates) is not tuple:
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "snapshot candidate audit is mutable",
        )
    for item in value.considered_candidates:
        item.__post_init__()
    identities = tuple(item.content_identity for item in value.considered_candidates)
    if identities != tuple(sorted(identities)) or len(set(identities)) != len(identities):
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "snapshot candidate audit is incomplete or unordered",
        )
    selected = tuple(
        item.content_identity
        for item in sorted(
            (
                item
                for item in value.considered_candidates
                if item.selected
            ),
            key=lambda item: (
                -item.ranking_observation.semantic_score_micros,
                item.content_identity,
            ),
        )
    )
    if selected != value.selected_content_identities:
        raise _fail(
            RetrievalFailureCode.RANKING_NONDETERMINISTIC,
            "snapshot selected set differs from ranked admitted candidates",
        )
    payload = value.payload_dict()
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.decision_id != value.envelope.record_id
        or value.decision_id.domain is not IdentityDomain.RETRIEVAL_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval decision identity changed",
        )


def snapshot_bound_retrieval_decision_ref(
    value: SnapshotBoundRetrievalDecision,
) -> HashBoundRef:
    validate_snapshot_bound_retrieval_decision(value)
    return _retrieval_artifact_ref(value.envelope, value.payload_dict())


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalLoadDecision:
    schema_version: str
    envelope: CommonEnvelope
    load_decision_id: RecordId
    retrieval_decision_ref: HashBoundRef
    selected_content_identity: str
    selected_manifest_identity: str
    loaded_subject_ref: HashBoundRef
    before_loading_revalidation_ref: HashBoundRef
    pre_load_library_root: str
    post_load_library_root: str
    created_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalLoadDecision:
        raise TypeError("SnapshotBoundRetrievalLoadDecision is retriever-created")

    def payload_dict(self) -> dict[str, object]:
        return {
            "schema_version": RETRIEVAL_LOAD_DECISION_V2,
            "retrieval_decision_ref": self.retrieval_decision_ref.to_dict(),
            "selected_content_identity": self.selected_content_identity,
            "selected_manifest_identity": self.selected_manifest_identity,
            "loaded_subject_ref": self.loaded_subject_ref.to_dict(),
            "before_loading_revalidation_ref": (
                self.before_loading_revalidation_ref.to_dict()
            ),
            "pre_load_library_root": self.pre_load_library_root,
            "post_load_library_root": self.post_load_library_root,
            "created_at_utc": _timestamp_text(self.created_at_utc),
        }

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_load_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def validate_snapshot_bound_retrieval_load_decision(
    value: SnapshotBoundRetrievalLoadDecision,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalLoadDecision
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != RETRIEVAL_LOAD_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot load decision is not retriever sealed",
        )
    if (
        value.pre_load_library_root != value.post_load_library_root
        or _SHA256_RE.fullmatch(value.pre_load_library_root) is None
    ):
        raise _fail(
            RetrievalFailureCode.SNAPSHOT_DRIFT,
            "library root drifted during verified load",
        )
    payload = value.payload_dict()
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.load_decision_id != value.envelope.record_id
        or value.load_decision_id.domain
        is not IdentityDomain.RETRIEVAL_LOAD_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot load decision identity changed",
        )


def snapshot_bound_retrieval_load_decision_ref(
    value: SnapshotBoundRetrievalLoadDecision,
) -> HashBoundRef:
    validate_snapshot_bound_retrieval_load_decision(value)
    return _retrieval_artifact_ref(value.envelope, value.payload_dict())


class FreshCompatibilityEvidenceProvider(Protocol):
    def __call__(
        self,
        candidate: ResolvedSnapshotCandidate,
        stage: RevalidationStage,
    ) -> SnapshotBoundCompatibilityEvidence:
        ...


@dataclass(frozen=True)
class SnapshotRetrievalResult:
    decision: SnapshotBoundRetrievalDecision
    decision_commit: AdmissionCommitEvidence
    load_decisions: tuple[SnapshotBoundRetrievalLoadDecision, ...]
    admitted_handles: tuple[AdmittedKnowledgeHandle, ...]

    def __post_init__(self) -> None:
        validate_snapshot_bound_retrieval_decision(self.decision)
        if (
            type(self.load_decisions) is not tuple
            or type(self.admitted_handles) is not tuple
        ):
            raise _fail(
                RetrievalFailureCode.TRUSTED_RECORD_FORGED,
                "snapshot retrieval result collections are mutable",
            )
        for item in self.load_decisions:
            validate_snapshot_bound_retrieval_load_decision(item)
        if len(self.load_decisions) != len(self.admitted_handles):
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "snapshot loads and admitted handles differ",
            )
