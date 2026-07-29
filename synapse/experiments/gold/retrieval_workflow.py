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
from .snapshot_retrieval import *

def _evidence_ref_for_bytes(
    *,
    schema_id: str,
    value: bytes,
) -> HashBoundRef:
    digest = hashlib.sha256(value).hexdigest()
    return HashBoundRef(
        kind=RefKind.EVIDENCE,
        ref_id=f"evidence:{digest}",
        schema_id=schema_id,
        sha256=digest,
        byte_length=len(value),
        media_type=RETRIEVAL_MEDIA_TYPE_V1,
    )


def _gate_dimensions(
    names: tuple[str, ...],
    *,
    passed: bool,
    evidence_ref: HashBoundRef,
) -> tuple[GateCheckedDimension, ...]:
    return tuple(
        GateCheckedDimension(
            GATE_CHECKED_DIMENSION_SCHEMA_V1,
            name,
            GateDimensionResult.PASS if passed else GateDimensionResult.FAIL,
            (evidence_ref,),
        )
        for name in names
    )


def _current_gate_heads(
    *,
    original: GateAuthorityHeads,
    retriever: ConfiguredRetriever,
) -> GateAuthorityHeads:
    return GateAuthorityHeads(
        GATE_AUTHORITY_HEADS_SCHEMA_V1,
        original.lifecycle,
        original.provenance,
        original.taint,
        retriever._admission_binding.admission_store.current_anchor(),
        retriever._evaluator.durability_binding.evidence_store.current_anchor(),
    )


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
        producer_component=query._retriever._evaluator.retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(query.query_id, LineageEdgeKind.REFERENCES),
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
    selected = tuple(
        item.content_identity
        for item in sorted(
            (item for item in candidate_audits if item.selected),
            key=lambda item: (
                -item.ranking_observation.semantic_score_micros,
                item.content_identity,
            ),
        )
    )
    result = object.__new__(SnapshotBoundRetrievalDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_DECISION_V2)
    object.__setattr__(result, "query_id", query.query_id)
    object.__setattr__(
        result,
        "repository_knowledge_snapshot_id",
        query.repository_knowledge_snapshot_id,
    )
    object.__setattr__(result, "atomic_boundary_id", query.atomic_boundary_id)
    object.__setattr__(
        result,
        "boundary_commit_sequence",
        query.boundary_commit_sequence,
    )
    object.__setattr__(result, "considered_candidates", candidate_audits)
    object.__setattr__(result, "selected_content_identities", selected)
    object.__setattr__(result, "conflict_scan_ref", conflict_ref)
    object.__setattr__(
        result,
        "created_at_utc",
        _timestamp(created_at_utc, "snapshot retrieval decision timestamp"),
    )
    payload = result.payload_dict()
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_DECISION_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=result.created_at_utc,
        producer_component=query._retriever._evaluator.retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(query.query_id, LineageEdgeKind.DERIVED_FROM),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "decision_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_retrieval_decision(result)
    return result


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
    result = object.__new__(SnapshotBoundRetrievalLoadDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_LOAD_DECISION_V2)
    object.__setattr__(
        result,
        "retrieval_decision_ref",
        snapshot_bound_retrieval_decision_ref(retrieval_decision),
    )
    object.__setattr__(
        result,
        "selected_content_identity",
        candidate.content_key.value,
    )
    object.__setattr__(
        result,
        "selected_manifest_identity",
        candidate.manifest_id.value,
    )
    object.__setattr__(result, "loaded_subject_ref", loaded_subject_ref)
    object.__setattr__(
        result,
        "before_loading_revalidation_ref",
        snapshot_bound_compatibility_artifact_ref(before_loading.record),
    )
    object.__setattr__(result, "pre_load_library_root", pre_load_root)
    object.__setattr__(result, "post_load_library_root", post_load_root)
    object.__setattr__(
        result,
        "created_at_utc",
        _timestamp(created_at_utc, "snapshot load timestamp"),
    )
    payload = result.payload_dict()
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_LOAD_DECISION_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=result.created_at_utc,
        producer_component=query._retriever._evaluator.retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                retrieval_decision.decision_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "load_decision_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_retrieval_load_decision(result)
    return result


def _create_consumption_request(
    *,
    query: SnapshotBoundRetrievalQuery,
    candidate: ResolvedSnapshotCandidate,
    retrieval_decision: SnapshotBoundRetrievalDecision,
    load_decision: SnapshotBoundRetrievalLoadDecision,
    before_consumption: CommittedCompatibilityRevalidation,
    heads: GateAuthorityHeads,
    observed_at_utc: datetime,
) -> ConsumptionGateRequest:
    retrieval_ref = snapshot_bound_retrieval_decision_ref(retrieval_decision)
    load_ref = snapshot_bound_retrieval_load_decision_ref(load_decision)
    revalidation_ref = snapshot_bound_compatibility_artifact_ref(
        before_consumption.record
    )
    valid_until = before_consumption.record.evaluated_at_utc.replace(
        year=before_consumption.record.evaluated_at_utc.year + 1
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
        producer_component=query._retriever._evaluator.consumer_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                retrieval_decision.decision_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                load_decision.load_decision_id,
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


def retrieve_and_load(
    *,
    retriever: ConfiguredRetriever,
    query: SnapshotBoundRetrievalQuery,
    manifest_core: SnapshotManifestCore,
    snapshot: RepositoryKnowledgeSnapshot,
    committed_boundary: CommittedAtomicSnapshotBoundary,
    snapshot_evaluator: ConfiguredSnapshotCompletenessEvaluator,
    knowledge_store: KnowledgeSnapshotStore,
    resolved_candidates: tuple[ResolvedSnapshotCandidate, ...],
    retrieval_gate_evaluator: ConfiguredRetrievalGateEvaluator,
    consumption_gate_evaluator: ConfiguredConsumptionGateEvaluator,
    fresh_evidence_provider: FreshCompatibilityEvidenceProvider,
) -> SnapshotRetrievalResult:
    require_configured_retriever(retriever)
    validate_snapshot_bound_retrieval_query(query)
    validate_snapshot_manifest_core(manifest_core)
    validate_repository_knowledge_snapshot(snapshot, evaluator=snapshot_evaluator)
    boundary = require_committed_atomic_snapshot_boundary(
        committed_boundary,
        evaluator=snapshot_evaluator,
        expected_store=knowledge_store,
        trusted_clock=retriever._trusted_clock,
    )
    if (
        snapshot._core is not manifest_core
        or boundary.snapshot_id != snapshot.snapshot_id
        or query.repository_knowledge_snapshot_id != snapshot.snapshot_id
        or query.atomic_boundary_id != boundary.atomic_boundary_id
        or query.boundary_commit_sequence != boundary.commit_sequence
        or query.envelope.run_id != boundary.envelope.run_id
        or query.envelope.attempt_id != boundary.envelope.attempt_id
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval query, snapshot, and committed boundary differ",
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
            snapshot_bound_compatibility_artifact_ref(
                item.compatibility_context
            )
            != query.compatibility_context_ref
        ):
            raise _fail(
                RetrievalFailureCode.CONTEXT_SUBSTITUTION,
                "candidate compatibility context differs from query",
            )
    compatibility_results = tuple(
        evaluate_and_commit_snapshot_bound_compatibility(
            evaluator=retriever._evaluator,
            context=item.compatibility_context,
            historical_context=item.historical_compatibility_context,
            descriptor=item.descriptor,
            index_entry=item.index_entry,
            subject_ref=item.subject_ref,
            source_evidence_refs=item.source_evidence_refs,
            valid_until_utc=boundary._core.valid_until_utc,
        )
        for item in resolved_candidates
    )
    if compatibility_results:
        contexts = {item.historical_context.context_id.value for item in compatibility_results}
        if len(contexts) != 1:
            raise _fail(
                RetrievalFailureCode.CONTEXT_SUBSTITUTION,
                "candidate compatibility histories mix contexts",
            )
        historical_context = compatibility_results[0].historical_context
        historical_decisions = tuple(
            item.historical_decision for item in compatibility_results
        )
        descriptors = tuple(item.descriptor for item in resolved_candidates)
        entries = tuple(item.index_entry for item in resolved_candidates)
        proposals = retriever._conflict_proposal_resolver(
            historical_context,
            historical_decisions,
            descriptors,
        )
        conflict_scan = evaluate_conflicts(
            evaluator=retriever._evaluator,
            context=historical_context,
            decisions=historical_decisions,
            descriptors=descriptors,
            considered_index_entries=entries,
            proposals=proposals,
        )
        conflict_bytes = _canonical(conflict_scan.to_dict())
        conflict_ref = _evidence_ref_for_bytes(
            schema_id=conflict_scan.schema_version,
            value=conflict_bytes,
        )
        conflicts_resolved = (
            conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
        )
        retriever._evaluator.durability_binding.evidence_store.append(
            conflict_scan
        )
    else:
        conflict_bytes = _canonical(
            {
                "schema_version": "synapse.stage4.gold.empty-conflict-scan/v1",
                "snapshot_id": snapshot.snapshot_id.to_dict(),
            }
        )
        conflict_ref = _evidence_ref_for_bytes(
            schema_id="synapse.stage4.gold.empty-conflict-scan/v1",
            value=conflict_bytes,
        )
        conflicts_resolved = True
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
            retriever._trusted_clock(),
            "retrieval gate timestamp",
        )
        heads = _current_gate_heads(original=query.authority_heads, retriever=retriever)
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
        reasons = (
            (
                RetrievalGateReason.BOUNDARY_COMMITTED,
                RetrievalGateReason.SUBJECT_IN_EXECUTABLE_VIEW,
                RetrievalGateReason.CONTEXT_BOUND,
                RetrievalGateReason.COMPATIBILITY_ACCEPTED,
                RetrievalGateReason.LIFECYCLE_ELIGIBLE,
                RetrievalGateReason.TAINT_ACCEPTED,
                RetrievalGateReason.CONFLICTS_RESOLVED,
            )
            if admitted
            else (
                (RetrievalGateReason.COMPATIBILITY_REJECTED,)
                if not compatibility_ok
                else (RetrievalGateReason.CONFLICT_UNRESOLVED,)
            )
        )
        evidence_ref = snapshot_bound_compatibility_artifact_ref(
            compatibility.decision
        )
        decision = evaluate_retrieval_gate(
            evaluator=retrieval_gate_evaluator,
            request=request,
            reasons=reasons,
            checked_dimensions=_gate_dimensions(
                retrieval_gate_evaluator.declaration.required_checked_dimensions,
                passed=admitted,
                evidence_ref=evidence_ref,
            ),
            producer_actor_ids=(retriever._evaluator.retriever_actor,),
            source_actor_ids=(retriever._evaluator.consumer_actor,),
            proposer_identity=retriever._evaluator.retriever_actor,
            executor_identity=None,
            subject_derived_actor_ids=(),
            evaluated_at_utc=observed_at,
            valid_until_utc=compatibility.decision.valid_until_utc,
            predecessor_decision=None,
            decision_sequence=1,
            diagnostics=(),
        )
        commit = retriever._admission_binding.admission_store.append_decision(
            decision
        )
        gate_results.append((candidate, compatibility, decision, commit))
    ranking: dict[str, SnapshotRankingObservation] = {}
    for candidate, _, decision, _ in gate_results:
        if decision.decision_kind is GateDecisionKind.ADMIT:
            ranking[candidate.content_key.value] = _observe_snapshot_ranking(
                retriever._ranking_provider,
                query=query,
                context=candidate.compatibility_context,
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
                    _retrieval_artifact_ref(
                        decision.envelope,
                        decision.to_dict()["payload"],
                    ),
                    decision.decision_kind,
                    (
                        None
                        if decision.decision_kind is GateDecisionKind.ADMIT
                        else decision.reasons[0].value
                    ),
                    ranking.get(candidate.content_key.value),
                    candidate.content_key.value in selected_order,
                )
                for candidate, compatibility, decision, _ in gate_results
            ),
            key=lambda item: item.content_identity,
        )
    )
    retrieval_decision = _create_snapshot_retrieval_decision(
        query=query,
        candidate_audits=audits,
        conflict_ref=conflict_ref,
        created_at_utc=retriever._trusted_clock(),
    )
    retrieval_ref = snapshot_bound_retrieval_decision_ref(retrieval_decision)
    retrieval_commit = retriever._admission_binding.admission_store.append_causal_artifact(
        record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
        record_identity=retrieval_decision.decision_id,
        artifact_ref=retrieval_ref,
        artifact=_retrieval_artifact_bytes(
            retrieval_decision.envelope,
            retrieval_decision.payload_dict(),
        ),
    )
    object.__setattr__(
        retriever,
        "_v2_decisions",
        (*retriever._v2_decisions, retrieval_decision),
    )
    load_decisions: list[SnapshotBoundRetrievalLoadDecision] = []
    handles: list[AdmittedKnowledgeHandle] = []
    by_content = {
        item.content_key.value: (item, compatibility)
        for item, compatibility, _, _ in gate_results
    }
    for selected_identity in retrieval_decision.selected_content_identities:
        candidate, compatibility = by_content[selected_identity]
        pre_verification = retriever._library.current_snapshot()
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
        loaded = retriever._library.get_verified_behavior(
            candidate.content_key,
            candidate.manifest_id,
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
        fresh_loading = fresh_evidence_provider(
            candidate,
            RevalidationStage.BEFORE_LOADING,
        )
        before_loading = revalidate_and_commit_snapshot_bound_compatibility(
            evaluator=retriever._evaluator,
            committed=compatibility,
            fresh_evidence=fresh_loading,
            stage=RevalidationStage.BEFORE_LOADING,
            observed_head_refs=candidate.source_evidence_refs,
            prior=None,
        )
        if before_loading.record.outcome is not RevalidationOutcome.PASSED:
            raise _fail(
                RetrievalFailureCode.LOADING_FORBIDDEN,
                "before-loading compatibility revalidation failed",
            )
        post_verification = retriever._library.current_snapshot(
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
            created_at_utc=retriever._trusted_clock(),
        )
        load_ref = snapshot_bound_retrieval_load_decision_ref(load_decision)
        retriever._admission_binding.admission_store.append_causal_artifact(
            record_kind=AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION,
            record_identity=load_decision.load_decision_id,
            artifact_ref=load_ref,
            artifact=_retrieval_artifact_bytes(
                load_decision.envelope,
                load_decision.payload_dict(),
            ),
        )
        fresh_consumption = fresh_evidence_provider(
            candidate,
            RevalidationStage.BEFORE_CONSUMPTION,
        )
        before_consumption = revalidate_and_commit_snapshot_bound_compatibility(
            evaluator=retriever._evaluator,
            committed=compatibility,
            fresh_evidence=fresh_consumption,
            stage=RevalidationStage.BEFORE_CONSUMPTION,
            observed_head_refs=candidate.source_evidence_refs,
            prior=before_loading,
        )
        if before_consumption.record.outcome is not RevalidationOutcome.PASSED:
            raise _fail(
                RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED,
                "before-consumption compatibility revalidation failed",
            )
        revalidation_ref = snapshot_bound_compatibility_artifact_ref(
            before_consumption.record
        )
        retriever._admission_binding.admission_store.append_causal_artifact(
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
            retriever._trusted_clock(),
            "consumption gate timestamp",
        )
        heads = _current_gate_heads(original=query.authority_heads, retriever=retriever)
        consumption_request = _create_consumption_request(
            query=query,
            candidate=candidate,
            retrieval_decision=retrieval_decision,
            load_decision=load_decision,
            before_consumption=before_consumption,
            heads=heads,
            observed_at_utc=observed,
        )
        consumption_decision = evaluate_consumption_gate(
            evaluator=consumption_gate_evaluator,
            request=consumption_request,
            reasons=(
                ConsumptionGateReason.BOUNDARY_REVALIDATED,
                ConsumptionGateReason.SUBJECT_IDENTITY_REVALIDATED,
                ConsumptionGateReason.RETRIEVAL_ADMISSION_REVALIDATED,
                ConsumptionGateReason.LOADING_REVALIDATION_ACCEPTED,
                ConsumptionGateReason.CONSUMPTION_COMPATIBILITY_ACCEPTED,
                ConsumptionGateReason.LIFECYCLE_REVALIDATED,
                ConsumptionGateReason.TAINT_CHAIN_REVALIDATED,
            ),
            checked_dimensions=_gate_dimensions(
                consumption_gate_evaluator.declaration.required_checked_dimensions,
                passed=True,
                evidence_ref=revalidation_ref,
            ),
            producer_actor_ids=(retriever._evaluator.retriever_actor,),
            source_actor_ids=(retriever._evaluator.consumer_actor,),
            proposer_identity=retriever._evaluator.consumer_actor,
            executor_identity=None,
            subject_derived_actor_ids=(),
            evaluated_at_utc=observed,
            valid_until_utc=consumption_request.valid_until_utc,
            predecessor_decision=None,
            decision_sequence=1,
            diagnostics=(),
        )
        consumption_commit = (
            retriever._admission_binding.admission_store.append_decision(
                consumption_decision
            )
        )
        resolution = retriever._admission_binding.knowledge_boundary_resolver.resolve_boundary(
            repository_knowledge_snapshot_id=query.repository_knowledge_snapshot_id,
            atomic_boundary_id=query.atomic_boundary_id,
            commit_sequence=query.boundary_commit_sequence,
        )
        handle = create_admitted_knowledge_handle(
            consumption_request=consumption_request,
            consumption_decision=consumption_decision,
            admission_store=retriever._admission_binding.admission_store,
            admission_commit_evidence=consumption_commit,
            loaded_subject_ref=candidate.subject_ref,
            boundary_resolution=resolution,
        )
        load_decisions.append(load_decision)
        handles.append(handle)
    result = SnapshotRetrievalResult(
        retrieval_decision,
        retrieval_commit,
        tuple(load_decisions),
        tuple(handles),
    )
    result.__post_init__()
    return result


__all__ = (
    "RETRIEVAL_QUERY_V1", "RETRIEVAL_BINDING_TARGET_V1", "RETRIEVAL_CANDIDATE_V1",
    "RANKING_FEATURE_OBSERVATION_V1",
    "RETRIEVAL_CONFLICT_RECORD_V1", "RETRIEVAL_DECISION_V1", "RETRIEVAL_LOAD_DECISION_V1",
    "RETRIEVAL_POLICY_V1", "RANKING_PROFILE_V1", "RETRIEVAL_MEDIA_TYPE_V1",
    "RetrievalFailureCode", "RetrievalViolation", "CandidateDisposition", "RetrievalOutcome",
    "LoadOutcome", "RetrievalBindingTarget", "RetrievalQuery", "RankingFeatureObservation",
    "ConfiguredRankingFeatureProvider", "ConfiguredRetriever", "RetrievalCandidateAudit",
    "RetrievalConflictRecord",
    "RetrievalDecision", "RetrievalLoadDecision", "RetrievalResult",
    "configure_ranking_feature_provider", "require_configured_ranking_feature_provider",
    "validate_ranking_feature_observation", "configure_retriever", "require_configured_retriever",
    "binding_to_retrieval_target", "validate_retrieval_binding_target",
    "create_retrieval_query", "validate_retrieval_query", "retrieval_query_from_dict",
    "validate_retrieval_candidate_audit", "validate_retrieval_conflict_record",
    "validate_retrieval_decision",
    "retrieve_and_load", "validate_retrieval_load_decision",
    "revalidate_loaded_before_consumption",
)
