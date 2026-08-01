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
    CompatibilityDimension,
    CompatibilityReason,
    CompatibilityRevalidationRecord,
    CompatibilitySubjectDescriptor,
    ConfiguredCompatibilityEvaluator,
    ConflictDecisionKind,
    ConflictEvidenceProposal,
    DimensionResult,
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
    CompatibilityCommitEvidence,
    CompatibilityDurabilityBinding,
    CompatibilityHistoryFailureCode,
    CompatibilityHistoryViolation,
    CompatibilityStoredRecordKind,
    create_compatibility_stored_artifact,
    validate_compatibility_durability_binding,
)
from .snapshot_compatibility import (
    CommittedCompatibilityRevalidation,
    CommittedSnapshotBoundCompatibility,
    ConfiguredSnapshotCompatibility,
    SnapshotBoundCompatibilityContext,
    SnapshotBoundCompatibilityDecision,
    SnapshotBoundCompatibilityEvidence,
    SnapshotBoundCompatibilityRevalidation,
    evaluate_snapshot_bound_compatibility,
    evaluate_snapshot_bound_compatibility_revalidation,
    require_snapshot_bound_compatibility_passed,
    snapshot_bound_compatibility_artifact_bytes,
    snapshot_bound_compatibility_artifact_ref,
    snapshot_bound_compatibility_record_identity,
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
    GATE_AUTHORITY_HEADS_SCHEMA_V1,
    GateConsumerContext,
    GateDecisionKind,
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
    AdmissionCausalRecordKind,
    AdmissionCommitEvidence,
    AdmittedKnowledgeHandle,
    RetrievalAdmissionBinding,
    create_admitted_knowledge_handle,
    gate_context_fingerprint,
    require_consumption_admitted,
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
from .snapshot_retrieval import *
from .patch6_adapters import (
    require_patch6_compatibility_adapter,
    require_patch6_retrieval_adapter,
)


def _fail(
    code: RetrievalFailureCode,
    detail: str,
) -> RetrievalViolation:
    return RetrievalViolation(code, detail)


def _canonical(value: object) -> bytes:
    try:
        return canonicalize_stage4_payload(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "retrieval workflow canonical payload is invalid",
        ) from exc


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            f"{name} is invalid",
        )
    return value


def _record(
    value: object,
    domain: IdentityDomain,
    name: str,
) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            f"{name} must be an exact RecordId",
        )
    value.to_dict()
    if value.domain is not domain:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            f"{name} uses the wrong identity domain",
        )
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            f"{name} must be timezone-aware UTC",
        )
    return value


def _evidence_ref_for_bytes(
    *,
    schema_id: str,
    value: bytes,
) -> HashBoundRef:
    digest = hashlib.sha256(value).hexdigest()
    return HashBoundRef(
        kind=RefKind.SOURCE_EVIDENCE,
        ref_id=f"evidence:{digest}",
        schema_id=schema_id,
        sha256=digest,
        byte_length=len(value),
        media_type=RETRIEVAL_MEDIA_TYPE_V1,
    )


def _current_gate_heads(
    *,
    original: GateAuthorityHeads,
    retriever: ConfiguredSnapshotRetrieval,
    compatibility_durability: CompatibilityDurabilityBinding,
) -> GateAuthorityHeads:
    require_configured_snapshot_retrieval(retriever)
    return GateAuthorityHeads(
        GATE_AUTHORITY_HEADS_SCHEMA_V1,
        original.lifecycle,
        original.provenance,
        original.taint,
        retriever.admission_binding.admission_store.current_anchor(),
        compatibility_durability.evidence_store.current_anchor(),
    )


def _retrieval_evaluator(
    retriever: ConfiguredSnapshotRetrieval,
) -> ConfiguredCompatibilityEvaluator:
    require_configured_snapshot_retrieval(retriever)
    return require_patch6_compatibility_adapter(
        retriever.compatibility.patch6_adapter
    )


def _snapshot_compatibility_store_artifact(value: object) -> object:
    kind_by_type = {
        SnapshotBoundCompatibilityContext: (
            CompatibilityStoredRecordKind.CONTEXT_V2
        ),
        SnapshotBoundCompatibilityEvidence: (
            CompatibilityStoredRecordKind.EVIDENCE_V2
        ),
        SnapshotBoundCompatibilityDecision: (
            CompatibilityStoredRecordKind.DECISION_V2
        ),
        SnapshotBoundCompatibilityRevalidation: (
            CompatibilityStoredRecordKind.REVALIDATION_V2
        ),
    }
    kind = kind_by_type.get(type(value))
    if kind is None:
        raise _fail(
            RetrievalFailureCode.COMPATIBILITY_MISSING,
            "snapshot compatibility store artifact kind is invalid",
        )
    return create_compatibility_stored_artifact(
        record_kind=kind,
        record_identity=snapshot_bound_compatibility_record_identity(value),
        artifact_bytes=snapshot_bound_compatibility_artifact_bytes(value),
        artifact_ref=snapshot_bound_compatibility_artifact_ref(value),
    )


def _commit_snapshot_compatibility_artifact(
    *,
    durability: CompatibilityDurabilityBinding,
    value: object,
) -> CompatibilityCommitEvidence:
    artifact = _snapshot_compatibility_store_artifact(value)
    try:
        return durability.evidence_store.require_inclusion(artifact)
    except CompatibilityHistoryViolation as exc:
        if (
            exc.failure_code
            is not CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE
        ):
            raise
    return durability.evidence_store.append(artifact)


def _create_retrieval_gate_request_for_candidate(
    *,
    query: SnapshotBoundRetrievalQuery,
    candidate: ResolvedSnapshotCandidate,
    compatibility: CommittedSnapshotBoundCompatibility,
    conflict_ref: HashBoundRef,
    heads: GateAuthorityHeads,
    observed_at_utc: datetime,
) -> RetrievalGateRequest:
    compatibility_ref = snapshot_bound_compatibility_artifact_ref(
        compatibility.decision
    )
    payload = retrieval_gate_request_payload(
        repository_knowledge_snapshot_id=query.repository_knowledge_snapshot_id,
        atomic_boundary_id=query.atomic_boundary_id,
        boundary_commit_sequence=query.boundary_commit_sequence,
        candidate_ref=candidate.subject_ref,
        compatibility_decision_ref=compatibility_ref,
        conflict_decision_ref=conflict_ref,
        subject_ref=candidate.subject_ref,
        consumer_context=query.consumer_context,
        authority_heads=heads,
        scope_ids=query.consumer_context.scope,
        capability_ids=query.consumer_context.capabilities,
        tool_ids=query.consumer_context.tools,
        oracle_ids=query.consumer_context.oracle_identities,
        observed_at_utc=observed_at_utc,
        valid_until_utc=compatibility.decision.valid_until_utc,
        predecessor_sequence=heads.admission.entry_count,
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_GATE_REQUEST,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=observed_at_utc,
        producer_component=_retrieval_evaluator(
            query._retriever
        ).retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                query.repository_knowledge_snapshot_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                query.atomic_boundary_id,
                LineageEdgeKind.REFERENCES,
            ),
        ),
    )
    return create_retrieval_gate_request(
        envelope=envelope,
        repository_knowledge_snapshot_id=query.repository_knowledge_snapshot_id,
        atomic_boundary_id=query.atomic_boundary_id,
        boundary_commit_sequence=query.boundary_commit_sequence,
        candidate_ref=candidate.subject_ref,
        compatibility_decision_ref=compatibility_ref,
        conflict_decision_ref=conflict_ref,
        subject_ref=candidate.subject_ref,
        consumer_context=query.consumer_context,
        authority_heads=heads,
        scope_ids=query.consumer_context.scope,
        capability_ids=query.consumer_context.capabilities,
        tool_ids=query.consumer_context.tools,
        oracle_ids=query.consumer_context.oracle_identities,
        observed_at_utc=observed_at_utc,
        valid_until_utc=compatibility.decision.valid_until_utc,
        predecessor_sequence=heads.admission.entry_count,
    )


def _create_snapshot_retrieval_decision(
    *,
    query: SnapshotBoundRetrievalQuery,
    candidate_audits: tuple[SnapshotCandidateAudit, ...],
    conflict_ref: HashBoundRef,
    created_at_utc: datetime,
) -> SnapshotBoundRetrievalDecision:
    return create_snapshot_bound_retrieval_decision(
        query=query,
        candidate_audits=candidate_audits,
        conflict_ref=conflict_ref,
        created_at_utc=created_at_utc,
    )


def _create_snapshot_load_decision(
    *,
    query: SnapshotBoundRetrievalQuery,
    retrieval_decision: SnapshotBoundRetrievalDecision,
    candidate: ResolvedSnapshotCandidate,
    loaded_subject_ref: HashBoundRef,
    before_loading: CommittedCompatibilityRevalidation,
    pre_load_root: str,
    post_load_root: str,
    created_at_utc: datetime,
) -> SnapshotBoundRetrievalLoadDecision:
    return create_snapshot_bound_retrieval_load_decision(
        query=query,
        retrieval_decision=retrieval_decision,
        candidate=candidate,
        loaded_subject_ref=loaded_subject_ref,
        before_loading=before_loading.record,
        pre_load_library_root=pre_load_root,
        post_load_library_root=post_load_root,
        created_at_utc=created_at_utc,
    )


def _create_consumption_request(
    *,
    query: SnapshotBoundRetrievalQuery,
    candidate: ResolvedSnapshotCandidate,
    retrieval_decision: SnapshotBoundRetrievalDecision,
    load_decision: SnapshotBoundRetrievalLoadDecision,
    before_consumption: CommittedCompatibilityRevalidation,
    heads: GateAuthorityHeads,
    observed_at_utc: datetime,
    valid_until_utc: datetime,
) -> ConsumptionGateRequest:
    retrieval_ref = snapshot_bound_retrieval_decision_ref(retrieval_decision)
    load_ref = snapshot_bound_retrieval_load_decision_ref(load_decision)
    revalidation_ref = snapshot_bound_compatibility_artifact_ref(
        before_consumption.record
    )
    valid_until = _timestamp(
        valid_until_utc,
        "consumption gate validity",
    )
    if valid_until <= observed_at_utc:
        raise _fail(
            RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED,
            "consumption gate validity has expired",
        )
    payload = consumption_gate_request_payload(
        repository_knowledge_snapshot_id=query.repository_knowledge_snapshot_id,
        atomic_boundary_id=query.atomic_boundary_id,
        boundary_commit_sequence=query.boundary_commit_sequence,
        retrieval_decision_ref=retrieval_ref,
        load_decision_ref=load_ref,
        compatibility_revalidation_ref=revalidation_ref,
        subject_ref=candidate.subject_ref,
        consumer_context=query.consumer_context,
        authority_heads=heads,
        scope_ids=query.consumer_context.scope,
        capability_ids=query.consumer_context.capabilities,
        tool_ids=query.consumer_context.tools,
        oracle_ids=query.consumer_context.oracle_identities,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until,
        predecessor_sequence=heads.admission.entry_count,
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.CONSUMPTION_GATE_REQUEST,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=observed_at_utc,
        producer_component=_retrieval_evaluator(
            query._retriever
        ).consumer_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                query.repository_knowledge_snapshot_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                query.atomic_boundary_id,
                LineageEdgeKind.REFERENCES,
            ),
        ),
    )
    return create_consumption_gate_request(
        envelope=envelope,
        repository_knowledge_snapshot_id=query.repository_knowledge_snapshot_id,
        atomic_boundary_id=query.atomic_boundary_id,
        boundary_commit_sequence=query.boundary_commit_sequence,
        retrieval_decision_ref=retrieval_ref,
        load_decision_ref=load_ref,
        compatibility_revalidation_ref=revalidation_ref,
        subject_ref=candidate.subject_ref,
        consumer_context=query.consumer_context,
        authority_heads=heads,
        scope_ids=query.consumer_context.scope,
        capability_ids=query.consumer_context.capabilities,
        tool_ids=query.consumer_context.tools,
        oracle_ids=query.consumer_context.oracle_identities,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until,
        predecessor_sequence=heads.admission.entry_count,
    )


def retrieve_snapshot_knowledge(
    *,
    retriever: ConfiguredSnapshotRetrieval,
    compatibility_durability: CompatibilityDurabilityBinding,
    query: SnapshotBoundRetrievalQuery,
    historical_context: CompatibilityContext,
    historical_ranking_query: RetrievalQuery,
    manifest_core: SnapshotManifestCore,
    snapshot: RepositoryKnowledgeSnapshot,
    committed_boundary: CommittedAtomicSnapshotBoundary,
    snapshot_evaluator: ConfiguredSnapshotCompletenessEvaluator,
    knowledge_store: KnowledgeSnapshotStore,
    resolved_candidates: tuple[ResolvedSnapshotCandidate, ...],
    retrieval_gate_evaluator: ConfiguredRetrievalGateEvaluator,
    consumption_gate_evaluator: ConfiguredConsumptionGateEvaluator,
) -> SnapshotRetrievalResult:
    base_retriever = require_configured_snapshot_retrieval(retriever)
    validate_compatibility_durability_binding(
        compatibility_durability,
        authority_binding=retriever.authority_binding,
    )
    if type(knowledge_store) is not KnowledgeSnapshotStore:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "current knowledge store is invalid",
        )
    shared_context = retriever.admission_binding.coordination_context
    if (
        compatibility_durability.coordination_context is not shared_context
        or knowledge_store.coordination_context is not shared_context
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "current stores do not share the exact snapshot coordination boundary",
        )
    evaluator = _retrieval_evaluator(retriever)
    validate_snapshot_bound_retrieval_query(query)
    if query._retriever is not retriever:
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot query belongs to another configured retriever",
        )
    validate_compatibility_context(historical_context, evaluator=evaluator)
    validate_retrieval_query(
        historical_ranking_query,
        retriever=base_retriever,
        context=historical_context,
    )
    validate_snapshot_manifest_core(manifest_core)
    validate_repository_knowledge_snapshot(snapshot, evaluator=snapshot_evaluator)
    boundary = require_committed_atomic_snapshot_boundary(
        committed_boundary,
        evaluator=snapshot_evaluator,
        expected_store=knowledge_store,
        trusted_clock=retriever.trusted_clock,
    )
    if (
        snapshot._core is not manifest_core
        or boundary._core is not manifest_core
        or boundary.snapshot_id != snapshot.snapshot_id
        or query.repository_knowledge_snapshot_id != snapshot.snapshot_id
        or query.atomic_boundary_id != boundary.atomic_boundary_id
        or query.boundary_commit_sequence != boundary.commit_sequence
        or query.envelope.run_id != boundary.envelope.run_id
        or query.envelope.attempt_id != boundary.envelope.attempt_id
        or query.envelope.repository_revision
        != boundary.envelope.repository_revision
        or query.envelope.policy_version != boundary.envelope.policy_version
        or query.envelope.environment_profile_id
        != boundary.envelope.environment_profile_id
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval query, snapshot, and committed boundary differ",
        )
    admission_store = retriever.admission_binding.admission_store
    compatibility_store = compatibility_durability.evidence_store
    admission_store.require_anchor_ancestry(
        manifest_core.admission_root,
        expected_current=query.authority_heads.admission,
    )
    compatibility_store.require_anchor_ancestry(
        manifest_core.compatibility_evidence_root,
        expected_current=query.authority_heads.compatibility,
    )

    expected_entries = tuple(
        item
        for item in manifest_core.executable_view
        if item.behavior_kind
        in {kind.value for kind in query.requested_behavior_kinds}
    )
    if (
        type(resolved_candidates) is not tuple
        or tuple(item.view_entry for item in resolved_candidates)
        != expected_entries
    ):
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "resolved candidates differ from the frozen executable view",
        )
    for item in resolved_candidates:
        item.__post_init__()
        if (
            item.historical_compatibility_context is not historical_context
            or snapshot_bound_compatibility_artifact_ref(
                item.compatibility_context
            )
            != query.compatibility_context_ref
        ):
            raise _fail(
                RetrievalFailureCode.CONTEXT_SUBSTITUTION,
                "candidate compatibility context differs from query",
            )

    compatibility_results: list[CommittedSnapshotBoundCompatibility] = []
    for candidate in resolved_candidates:
        evaluation = evaluate_snapshot_bound_compatibility(
            compatibility=retriever.compatibility,
            context=candidate.compatibility_context,
            historical_context=historical_context,
            descriptor=candidate.descriptor,
            index_entry=candidate.index_entry,
            subject_ref=candidate.subject_ref,
            source_evidence_refs=candidate.source_evidence_refs,
            valid_until_utc=manifest_core.valid_until_utc,
        )
        context_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=evaluation.context,
        )
        evidence_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=evaluation.evidence,
        )
        decision_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=evaluation.decision,
        )
        committed_compatibility = CommittedSnapshotBoundCompatibility(
            retriever.compatibility,
            evaluation.historical_context,
            evaluation.historical_decision,
            evaluation.context,
            context_commit,
            evaluation.evidence,
            evidence_commit,
            evaluation.decision,
            decision_commit,
        )
        committed_compatibility.__post_init__()
        compatibility_store.require_inclusion(
            _snapshot_compatibility_store_artifact(evaluation.context),
            expected_evidence=context_commit,
        )
        compatibility_store.require_inclusion(
            _snapshot_compatibility_store_artifact(evaluation.evidence),
            expected_evidence=evidence_commit,
        )
        compatibility_store.require_inclusion(
            _snapshot_compatibility_store_artifact(evaluation.decision),
            expected_evidence=decision_commit,
        )
        compatibility_results.append(committed_compatibility)

    historical_decisions = tuple(
        item.historical_decision for item in compatibility_results
    )
    descriptors = tuple(item.descriptor for item in resolved_candidates)
    entries = tuple(item.index_entry for item in resolved_candidates)
    proposals = retriever.patch6_adapter.resolve_conflict_proposals(
        context=historical_context,
        decisions=historical_decisions,
        descriptors=descriptors,
    )
    conflict_scan = retriever.patch6_adapter.evaluate_conflicts(
        context=historical_context,
        decisions=historical_decisions,
        descriptors=descriptors,
        considered_index_entries=entries,
        proposals=proposals,
    )
    conflict_commit = (
        compatibility_durability.evidence_store.append(conflict_scan)
    )
    conflict_ref = conflict_commit.artifact_ref
    conflicts_resolved = (
        conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    )

    gate_results: list[
        tuple[
            ResolvedSnapshotCandidate,
            CommittedSnapshotBoundCompatibility,
            RetrievalGateDecision,
            AdmissionCommitEvidence,
        ]
    ] = []
    for candidate, compatibility in zip(
        resolved_candidates,
        compatibility_results,
    ):
        observed_at = _timestamp(
            retriever.trusted_clock(),
            "retrieval gate timestamp",
        )
        heads = _current_gate_heads(
            original=query.authority_heads,
            retriever=retriever,
            compatibility_durability=compatibility_durability,
        )
        request = _create_retrieval_gate_request_for_candidate(
            query=query,
            candidate=candidate,
            compatibility=compatibility,
            conflict_ref=conflict_ref,
            heads=heads,
            observed_at_utc=observed_at,
        )
        compatibility_ok = (
            compatibility.decision.decision_kind
            is CompatibilityDecisionKind.COMPATIBLE
            and all(
                item.result is DimensionResult.PASS
                for item in compatibility.evidence.dimensions
            )
        )
        admitted = compatibility_ok and conflicts_resolved
        evidence_ref = snapshot_bound_compatibility_artifact_ref(
            compatibility.decision
        )
        decision = evaluate_retrieval_gate(
            evaluator=retrieval_gate_evaluator,
            request=request,
        )
        if (
            (
                not compatibility_ok
                and RetrievalGateReason.COMPATIBILITY_REJECTED
                not in decision.reasons
            )
            or (
                not conflicts_resolved
                and RetrievalGateReason.CONFLICT_UNRESOLVED
                not in decision.reasons
            )
            or (
                decision.decision_kind is GateDecisionKind.ADMIT
                and (
                    not admitted
                    or any(
                        evidence_ref not in item.evidence_refs
                        for item in decision.checked_dimensions
                    )
                )
            )
        ):
            raise _fail(
                RetrievalFailureCode.CONTEXT_SUBSTITUTION,
                "retrieval gate observation contradicts committed evidence",
            )
        commit = retriever.admission_binding.admission_store.append_decision(
            decision
        )
        gate_results.append((candidate, compatibility, decision, commit))

    ranking: dict[str, SnapshotRankingObservation] = {}
    for candidate, _, decision, _ in gate_results:
        if decision.decision_kind is GateDecisionKind.ADMIT:
            ranking[candidate.content_key.value] = observe_snapshot_ranking(
                retriever.patch6_adapter,
                query=query,
                context=candidate.compatibility_context,
                historical_query=historical_ranking_query,
                historical_context=historical_context,
                descriptor=candidate.descriptor,
            )
    selected_order = tuple(
        item[0].content_key.value
        for item in sorted(
            (
                item
                for item in gate_results
                if item[2].decision_kind is GateDecisionKind.ADMIT
            ),
            key=lambda item: (
                -ranking[item[0].content_key.value].semantic_score_micros,
                item[0].content_key.value,
            ),
        )[: query.selected_set_limit]
    )
    audits = tuple(
        sorted(
            (
                SnapshotCandidateAudit(
                    "synapse.stage4.gold.snapshot-candidate-audit/v1",
                    candidate.content_key.value,
                    candidate.manifest_id.value,
                    snapshot_bound_compatibility_artifact_ref(
                        compatibility.decision
                    ),
                    commit.artifact_ref,
                    decision.decision_kind,
                    (
                        None
                        if decision.decision_kind is GateDecisionKind.ADMIT
                        else decision.reasons[0].value
                    ),
                    ranking.get(candidate.content_key.value),
                    candidate.content_key.value in selected_order,
                )
                for candidate, compatibility, decision, commit in gate_results
            ),
            key=lambda item: item.content_identity,
        )
    )
    retrieval_decision = _create_snapshot_retrieval_decision(
        query=query,
        candidate_audits=audits,
        conflict_ref=conflict_ref,
        created_at_utc=retriever.trusted_clock(),
    )
    retrieval_ref = snapshot_bound_retrieval_decision_ref(retrieval_decision)
    retrieval_commit = (
        retriever.admission_binding.admission_store.append_causal_artifact(
            record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
            record_identity=retrieval_decision.decision_id,
            artifact_ref=retrieval_ref,
            artifact=snapshot_bound_retrieval_artifact_bytes(
                envelope=retrieval_decision.envelope,
                payload=retrieval_decision.payload_dict(),
            ),
        )
    )
    resolved_retrieval_id, resolved_retrieval_ref = (
        retriever.retrieval_authority_resolver.resolve_retrieval_authority(
            retrieval_decision_ref=retrieval_ref,
            expected_envelope=retrieval_decision.envelope,
        )
    )
    if (
        resolved_retrieval_id != retrieval_decision.decision_id
        or resolved_retrieval_ref != retrieval_ref
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval authority resolver substituted the committed decision",
        )

    load_decisions: list[SnapshotBoundRetrievalLoadDecision] = []
    consumption_refs: list[HashBoundRef] = []
    handles: list[AdmittedKnowledgeHandle] = []
    by_content = {
        item.content_key.value: (item, compatibility)
        for item, compatibility, _, _ in gate_results
    }
    for selected_identity in retrieval_decision.selected_content_identities:
        candidate, compatibility = by_content[selected_identity]
        pre_verification = retriever.patch6_adapter.current_library_snapshot()
        validate_snapshot_verification(pre_verification)
        pre_snapshot = pre_verification.snapshot
        if (
            pre_snapshot.integrity_manifest_sha256
            != manifest_core.library_root_sha256
            or pre_snapshot.index_sha256 != manifest_core.index_root_sha256
        ):
            raise _fail(
                RetrievalFailureCode.SNAPSHOT_DRIFT,
                "live library roots differ before verified load",
            )
        loaded = retriever.patch6_adapter.load_verified_behavior(
            content_key=candidate.content_key,
            manifest_id=candidate.manifest_id,
        )
        validate_verified_behavior_record(loaded)
        if (
            loaded.unit.content_key != candidate.content_key
            or loaded.manifest.manifest_id != candidate.manifest_id
        ):
            raise _fail(
                RetrievalFailureCode.LOADED_IDENTITY_MISMATCH,
                "verified load returned another content identity",
            )

        loading_evaluation = (
            evaluate_snapshot_bound_compatibility_revalidation(
                compatibility=retriever.compatibility,
                committed=compatibility,
                descriptor=candidate.descriptor,
                index_entry=candidate.index_entry,
                subject_ref=candidate.subject_ref,
                source_evidence_refs=candidate.source_evidence_refs,
                stage=RevalidationStage.BEFORE_LOADING,
                observed_head_refs=candidate.source_evidence_refs,
                prior=None,
            )
        )
        loading_evidence_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=loading_evaluation.evidence,
        )
        loading_record_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=loading_evaluation.record,
        )
        before_loading = CommittedCompatibilityRevalidation(
            retriever.compatibility,
            loading_evaluation.evidence,
            loading_evidence_commit,
            loading_evaluation.record,
            loading_record_commit,
        )
        before_loading.__post_init__()

        post_verification = retriever.patch6_adapter.current_library_snapshot(
            trusted_prior=pre_snapshot
        )
        validate_snapshot_verification(post_verification)
        post_snapshot = post_verification.snapshot
        load_decision = _create_snapshot_load_decision(
            query=query,
            retrieval_decision=retrieval_decision,
            candidate=candidate,
            loaded_subject_ref=candidate.subject_ref,
            before_loading=before_loading,
            pre_load_root=pre_snapshot.integrity_manifest_sha256,
            post_load_root=post_snapshot.integrity_manifest_sha256,
            created_at_utc=retriever.trusted_clock(),
        )
        load_ref = snapshot_bound_retrieval_load_decision_ref(load_decision)
        retriever.admission_binding.admission_store.append_causal_artifact(
            record_kind=AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION,
            record_identity=load_decision.load_decision_id,
            artifact_ref=load_ref,
            artifact=snapshot_bound_retrieval_artifact_bytes(
                envelope=load_decision.envelope,
                payload=load_decision.payload_dict(),
            ),
        )

        consumption_evaluation = (
            evaluate_snapshot_bound_compatibility_revalidation(
                compatibility=retriever.compatibility,
                committed=compatibility,
                descriptor=candidate.descriptor,
                index_entry=candidate.index_entry,
                subject_ref=candidate.subject_ref,
                source_evidence_refs=candidate.source_evidence_refs,
                stage=RevalidationStage.BEFORE_CONSUMPTION,
                observed_head_refs=candidate.source_evidence_refs,
                prior=before_loading,
            )
        )
        consumption_evidence_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=consumption_evaluation.evidence,
        )
        consumption_record_commit = _commit_snapshot_compatibility_artifact(
            durability=compatibility_durability,
            value=consumption_evaluation.record,
        )
        before_consumption = CommittedCompatibilityRevalidation(
            retriever.compatibility,
            consumption_evaluation.evidence,
            consumption_evidence_commit,
            consumption_evaluation.record,
            consumption_record_commit,
        )
        before_consumption.__post_init__()
        revalidation_ref = snapshot_bound_compatibility_artifact_ref(
            before_consumption.record
        )
        retriever.admission_binding.admission_store.append_causal_artifact(
            record_kind=(
                AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION
            ),
            record_identity=before_consumption.record.revalidation_id,
            artifact_ref=revalidation_ref,
            artifact=snapshot_bound_compatibility_artifact_bytes(
                before_consumption.record
            ),
        )

        observed = _timestamp(
            retriever.trusted_clock(),
            "consumption gate timestamp",
        )
        heads = _current_gate_heads(
            original=query.authority_heads,
            retriever=retriever,
            compatibility_durability=compatibility_durability,
        )
        consumption_request = _create_consumption_request(
            query=query,
            candidate=candidate,
            retrieval_decision=retrieval_decision,
            load_decision=load_decision,
            before_consumption=before_consumption,
            heads=heads,
            observed_at_utc=observed,
            valid_until_utc=manifest_core.valid_until_utc,
        )
        loading_passed = (
            before_loading.record.outcome is RevalidationOutcome.PASSED
        )
        consumption_passed = (
            before_consumption.record.outcome is RevalidationOutcome.PASSED
        )
        failed_dimensions = {
            item.dimension
            for item in before_consumption.evidence.dimensions
            if item.result is not DimensionResult.PASS
        }
        taint_failed = any(
            item.reason is CompatibilityReason.TAINT_INVALID
            for item in before_consumption.evidence.dimensions
        )
        gate_passed = loading_passed and consumption_passed
        consumption_decision = evaluate_consumption_gate(
            evaluator=consumption_gate_evaluator,
            request=consumption_request,
        )
        expected_restrictive_reason = (
            ConsumptionGateReason.TAINT_CHAIN_INVALID
            if taint_failed
            else (
                ConsumptionGateReason.DEPENDENCY_UNAVAILABLE
                if CompatibilityDimension.EVIDENCE_COMPLETENESS
                in failed_dimensions
                else (
                    ConsumptionGateReason.LIFECYCLE_STALE
                    if CompatibilityDimension.LIFECYCLE in failed_dimensions
                    else (
                        ConsumptionGateReason.LOADING_REVALIDATION_MISSING
                        if not loading_passed
                        else ConsumptionGateReason.COMPATIBILITY_STALE
                    )
                )
            )
        )
        if (
            (
                not gate_passed
                and expected_restrictive_reason
                not in consumption_decision.reasons
            )
            or (
                consumption_decision.decision_kind is GateDecisionKind.ADMIT
                and (
                    not gate_passed
                    or any(
                        revalidation_ref not in item.evidence_refs
                        for item in consumption_decision.checked_dimensions
                    )
                )
            )
        ):
            raise _fail(
                RetrievalFailureCode.CONTEXT_SUBSTITUTION,
                "consumption gate observation contradicts fresh evidence",
            )
        consumption_commit = (
            retriever.admission_binding.admission_store.append_decision(
                consumption_decision
            )
        )
        load_decisions.append(load_decision)
        consumption_refs.append(consumption_commit.artifact_ref)
        if consumption_decision.decision_kind is GateDecisionKind.ADMIT:
            handle = create_admitted_knowledge_handle(
                consumption_request=consumption_request,
                consumption_decision=consumption_decision,
                admission_store=admission_store,
                compatibility_store=compatibility_store,
                admission_commit_evidence=consumption_commit,
                loaded_subject_ref=candidate.subject_ref,
                retrieval_admission_binding=retriever.admission_binding,
            )
            require_consumption_admitted(
                handle,
                consumption_request=consumption_request,
                consumption_decision=consumption_decision,
                admission_store=admission_store,
                compatibility_store=compatibility_store,
                retrieval_admission_binding=retriever.admission_binding,
                current_heads=_current_gate_heads(
                    original=query.authority_heads,
                    retriever=retriever,
                    compatibility_durability=compatibility_durability,
                ),
                at_utc=observed,
            )
            handles.append(handle)

    result = SnapshotRetrievalResult(
        retrieval_decision,
        retrieval_commit,
        tuple(load_decisions),
        tuple(consumption_refs),
        tuple(handles),
    )
    result.__post_init__()
    return result


__all__ = ("retrieve_snapshot_knowledge",)
