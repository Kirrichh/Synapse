from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from synapse.experiments.gold.admission import (
    configure_consumption_gate_evaluator,
    configure_retrieval_gate_evaluator,
)
from synapse.experiments.gold.admission_contracts import (
    CONSUMPTION_REQUIRED_DIMENSIONS,
    GATE_AUTHORITY_HEADS_SCHEMA_V1,
    GATE_CHECKED_DIMENSION_SCHEMA_V1,
    GATE_EVALUATION_OBSERVATION_SCHEMA_V1,
    RETRIEVAL_REQUIRED_DIMENSIONS,
    ConsumptionGateReason,
    GateAuthorityHeads,
    GateCheckedDimension,
    GateDecisionKind,
    GateDimensionResult,
    GateEvaluationObservation,
    RetrievalGateReason,
)
from synapse.experiments.gold.admission_store import (
    create_retrieval_admission_binding,
    open_admission_history_store,
)
from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    RefKind,
    STAGE4_CANONICAL_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.compatibility_store import (
    create_compatibility_durability_binding,
    open_compatibility_evidence_store,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityRole,
    HistoryDomain,
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    SchemaVersion,
    create_common_envelope,
    create_history_anchor,
)
from synapse.experiments.gold.knowledge import create_knowledge_boundary_resolver
from synapse.experiments.gold.knowledge_contracts import (
    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
    SnapshotObjectStatus,
    SnapshotStoreSequence,
    SnapshotViewEntry,
)
from synapse.experiments.gold.patch6_adapters import (
    configure_patch6_compatibility_adapter,
    configure_patch6_retrieval_adapter,
)
from synapse.experiments.gold.retrieval import RetrievalFailureCode, RetrievalViolation
from synapse.experiments.gold.retrieval_workflow import retrieve_snapshot_knowledge
from synapse.experiments.gold.snapshot_compatibility import (
    configure_snapshot_compatibility,
    create_snapshot_bound_compatibility_context,
    historical_compatibility_context_ref,
    snapshot_bound_compatibility_artifact_ref,
    snapshot_bound_compatibility_context_payload,
)
from synapse.experiments.gold.snapshot_retrieval import (
    RETRIEVAL_DECISION_V2,
    RETRIEVAL_LOAD_DECISION_V2,
    RETRIEVAL_QUERY_V2,
    ResolvedSnapshotCandidate,
    configure_snapshot_retrieval,
    create_retrieval_query,
    snapshot_bound_retrieval_query_payload,
)

from tests.test_stage4_gold_admission import CONSUMER_CONTEXT
from tests.test_stage4_gold_authority_overlay import build_authority_bundle
from tests.test_stage4_gold_compatibility import _make_harness
from tests.test_stage4_gold_knowledge_snapshot import (
    NOW as SNAPSHOT_NOW,
    build_snapshot_harness,
    commit_snapshot,
)
from tests.test_stage4_gold_retrieval import _configured_retriever


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _artifact_ref(name: str, value: object) -> HashBoundRef:
    raw = _canonical(value)
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"patch78.{name}:{hashlib.sha256(raw).hexdigest()}",
        schema_id=f"synapse.stage4.gold.test.{name}/v1",
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type="application/json",
    )


class TickingClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        self.current += timedelta(seconds=1)
        return self.current


@dataclass
class PositiveGateProvider:
    profile_id: str
    component_identity: ActorIdentity
    role: AuthorityRole
    reasons: tuple[object, ...]
    dimensions: tuple[str, ...]
    events: list[str]

    def observe_gate(self, *, request: object) -> GateEvaluationObservation:
        stage = "retrieval_gate" if self.role is AuthorityRole.RETRIEVAL_GATE_EVALUATOR else "consumption_gate"
        self.events.append(stage)
        evidence_ref = (
            request.compatibility_decision_ref
            if self.role is AuthorityRole.RETRIEVAL_GATE_EVALUATOR
            else request.compatibility_revalidation_ref
        )
        return GateEvaluationObservation(
            GATE_EVALUATION_OBSERVATION_SCHEMA_V1,
            self.role,
            request.request_id,
            self.reasons,
            tuple(
                GateCheckedDimension(
                    GATE_CHECKED_DIMENSION_SCHEMA_V1,
                    dimension,
                    GateDimensionResult.PASS,
                    (evidence_ref,),
                )
                for dimension in self.dimensions
            ),
            (ActorIdentity(request.envelope.producer_component),),
            (ActorIdentity("patch78-candidate-source"),),
            ActorIdentity(request.envelope.producer_component),
            ActorIdentity("patch78-consumer-executor"),
            (),
            (("stage", stage),),
        )


def _history_anchor(domain, configuration_id):
    return create_history_anchor(
        history_domain=domain,
        configuration_id=configuration_id,
        entry_sha256s=(),
        domain_heads=(),
    )


def build_retrieval_harness(tmp_path: Path, *, include_candidate: bool = True):
    patch6 = _make_harness(tmp_path / "patch6")
    authority = build_authority_bundle(
        base_handle=patch6.handle,
        compatibility_declaration_id=patch6.declaration.declaration_id,
        compatibility_evaluator_identity=patch6.declaration.evaluator_identity,
    )
    overlay = authority.binding.knowledge_admission_authority_handle.configuration
    admission_genesis = _history_anchor(
        HistoryDomain.ADMISSION,
        overlay.configuration_id,
    )
    compatibility_genesis = _history_anchor(
        HistoryDomain.COMPATIBILITY,
        overlay.configuration_id,
    )
    lifecycle_root = patch6.lifecycle_store.current_anchor()
    provenance_root = patch6.attestation_store.current_anchor()
    taint_root = patch6.taint_store.current_anchor()
    library_snapshot = patch6.context.library_snapshot
    attestation_ref = _artifact_ref(
        "behavior-attestation",
        patch6.attestation.to_dict(),
    )
    lifecycle_ref = _artifact_ref(
        "lifecycle-record",
        patch6.lifecycle_record.to_dict(),
    )
    behavior_ref = _artifact_ref(
        "behavior-manifest",
        patch6.manifest.to_dict(unit=patch6.unit, blob=patch6.blob),
    )
    view = SnapshotViewEntry(
        "synapse.stage4.gold.snapshot-view-entry/v1",
        patch6.unit.content_key.value,
        patch6.manifest.manifest_id.value,
        patch6.unit.core.behavior_kind.value,
        patch6.descriptor.binding_refs,
        (attestation_ref,),
        lifecycle_ref,
        (),
        SnapshotObjectStatus.EXECUTABLE,
        (),
    )
    sequences = tuple(
        sorted(
            (
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "admission",
                    admission_genesis.entry_count,
                    admission_genesis.anchor_id.value,
                ),
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "compatibility",
                    compatibility_genesis.entry_count,
                    compatibility_genesis.anchor_id.value,
                ),
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "library",
                    library_snapshot.committed_journal_sequence,
                    library_snapshot.integrity_manifest_sha256,
                ),
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "lifecycle",
                    lifecycle_root.entry_count,
                    lifecycle_root.anchor_id.value,
                ),
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "provenance",
                    provenance_root.entry_count,
                    provenance_root.anchor_id.value,
                ),
                SnapshotStoreSequence(
                    SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1,
                    "taint",
                    taint_root.entry_count,
                    taint_root.anchor_id.value,
                ),
            ),
            key=lambda item: item.store_name,
        )
    )
    views = (view,) if include_candidate else ()
    snapshot = build_snapshot_harness(
        tmp_path / "snapshot",
        authority=authority,
        repository_revision=patch6.context.repository_revision,
        policy_version=patch6.context.compatibility_policy,
        library_root_sha256=library_snapshot.integrity_manifest_sha256,
        index_root_sha256=library_snapshot.index_sha256,
        lifecycle_root=lifecycle_root,
        provenance_root=provenance_root,
        taint_root=taint_root,
        behavior_refs=((behavior_ref,) if include_candidate else ()),
        binding_refs=((patch6.descriptor.binding_refs) if include_candidate else ()),
        attestation_refs=((attestation_ref,) if include_candidate else ()),
        executable_view=views,
        audit_view=views,
        store_sequences=sequences,
    )
    committed_boundary = commit_snapshot(snapshot)
    compatibility_store = open_compatibility_evidence_store(
        (tmp_path / "compatibility-store").resolve(),
        authority_binding=authority.binding,
        coordination_context=snapshot.store.coordination_context,
    )
    admission_store = open_admission_history_store(
        (tmp_path / "admission-store").resolve(),
        authority_binding=authority.binding,
        coordination_context=snapshot.store.coordination_context,
    )
    assert compatibility_store.current_anchor() == snapshot.core.compatibility_evidence_root
    assert admission_store.current_anchor() == snapshot.core.admission_root
    compatibility_adapter = configure_patch6_compatibility_adapter(patch6.evaluator)
    clock = TickingClock(SNAPSHOT_NOW + timedelta(minutes=1))
    compatibility = configure_snapshot_compatibility(
        authority_binding=authority.binding,
        patch6_adapter=compatibility_adapter,
        trusted_clock=clock,
    )
    historical_context_ref = historical_compatibility_context_ref(
        patch6.context,
        compatibility=compatibility,
    )
    context_payload = snapshot_bound_compatibility_context_payload(
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        historical_context_ref=historical_context_ref,
        declared_input_refs=(historical_context_ref,),
        base_configuration_id=authority.base_handle.configuration.configuration_id,
        knowledge_admission_configuration_id=overlay.configuration_id,
    )
    compatibility_context_envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMPATIBILITY_CONTEXT_V2,
        canonical_payload_bytes=_canonical(context_payload),
        run_id=snapshot.core.envelope.run_id,
        attempt_id=snapshot.core.envelope.attempt_id,
        created_at_utc=clock(),
        producer_component=patch6.declaration.evaluator_component_id,
        repository_revision=snapshot.core.envelope.repository_revision,
        policy_version=snapshot.core.envelope.policy_version,
        environment_profile_id=snapshot.core.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(snapshot.snapshot.snapshot_id, LineageEdgeKind.REFERENCES),
            LineageParentRef(snapshot.boundary.atomic_boundary_id, LineageEdgeKind.REFERENCES),
        ),
    )
    compatibility_context = create_snapshot_bound_compatibility_context(
        envelope=compatibility_context_envelope,
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        historical_context_ref=historical_context_ref,
        declared_input_refs=(historical_context_ref,),
        compatibility=compatibility,
    )
    events: list[str] = []
    patch6_retriever, _, historical_query = _configured_retriever(
        patch6,
        scorer=lambda query_id, descriptor_id, score_input: events.append("ranking") or 700_000,
    )
    patch6_retrieval = configure_patch6_retrieval_adapter(
        retriever=patch6_retriever,
        compatibility_adapter=compatibility_adapter,
    )
    boundary_resolver = create_knowledge_boundary_resolver(
        authority_binding=authority.binding,
        store=snapshot.store,
    )
    admission_binding = create_retrieval_admission_binding(
        authority_binding=authority.binding,
        admission_store=admission_store,
        coordination_context=snapshot.store.coordination_context,
        knowledge_boundary_resolver=boundary_resolver,
    )
    retriever = configure_snapshot_retrieval(
        authority_binding=authority.binding,
        patch6_adapter=patch6_retrieval,
        compatibility=compatibility,
        admission_binding=admission_binding,
        trusted_clock=clock,
    )
    heads = GateAuthorityHeads(
        GATE_AUTHORITY_HEADS_SCHEMA_V1,
        snapshot.core.lifecycle_root,
        snapshot.core.provenance_root,
        snapshot.core.taint_root,
        admission_store.current_anchor(),
        compatibility_store.current_anchor(),
    )
    compatibility_context_ref = snapshot_bound_compatibility_artifact_ref(
        compatibility_context
    )
    query_payload = snapshot_bound_retrieval_query_payload(
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        compatibility_context_ref=compatibility_context_ref,
        requested_behavior_kinds=(patch6.unit.core.behavior_kind,),
        required_binding_targets=(),
        selected_set_limit=1,
        consumer_context=CONSUMER_CONTEXT,
        authority_heads=heads,
    )
    query_envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_QUERY_V2,
        canonical_payload_bytes=_canonical(query_payload),
        run_id=snapshot.core.envelope.run_id,
        attempt_id=snapshot.core.envelope.attempt_id,
        created_at_utc=clock(),
        producer_component=patch6.evaluator.retriever_actor.value,
        repository_revision=snapshot.core.envelope.repository_revision,
        policy_version=snapshot.core.envelope.policy_version,
        environment_profile_id=snapshot.core.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(snapshot.snapshot.snapshot_id, LineageEdgeKind.REFERENCES),
            LineageParentRef(snapshot.boundary.atomic_boundary_id, LineageEdgeKind.REFERENCES),
        ),
    )
    query = create_retrieval_query(
        retriever=retriever,
        envelope=query_envelope,
        repository_knowledge_snapshot_id=snapshot.snapshot.snapshot_id,
        atomic_boundary_id=snapshot.boundary.atomic_boundary_id,
        boundary_commit_sequence=snapshot.boundary.commit_sequence,
        compatibility_context_ref=compatibility_context_ref,
        requested_behavior_kinds=(patch6.unit.core.behavior_kind,),
        required_binding_targets=(),
        selected_set_limit=1,
        consumer_context=CONSUMER_CONTEXT,
        authority_heads=heads,
    )
    candidates = (
        (
            ResolvedSnapshotCandidate(
                view,
                patch6.entry,
                patch6.descriptor,
                patch6.unit.content_key,
                patch6.manifest.manifest_id,
                attestation_ref,
                compatibility_context,
                patch6.context,
                patch6.context.verification_refs,
            ),
        )
        if include_candidate
        else ()
    )
    retrieval_declaration = overlay.retrieval_gate_evaluator
    retrieval_provider = PositiveGateProvider(
        retrieval_declaration.resolver_profile_ids[0],
        retrieval_declaration.evaluator_component_identity,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        (
            RetrievalGateReason.BOUNDARY_COMMITTED,
            RetrievalGateReason.SUBJECT_IN_EXECUTABLE_VIEW,
            RetrievalGateReason.CONTEXT_BOUND,
            RetrievalGateReason.COMPATIBILITY_ACCEPTED,
            RetrievalGateReason.LIFECYCLE_ELIGIBLE,
            RetrievalGateReason.TAINT_ACCEPTED,
            RetrievalGateReason.CONFLICTS_RESOLVED,
        ),
        RETRIEVAL_REQUIRED_DIMENSIONS,
        events,
    )
    consumption_declaration = overlay.consumption_gate_evaluator
    consumption_provider = PositiveGateProvider(
        consumption_declaration.resolver_profile_ids[0],
        consumption_declaration.evaluator_component_identity,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        (
            ConsumptionGateReason.BOUNDARY_REVALIDATED,
            ConsumptionGateReason.SUBJECT_IDENTITY_REVALIDATED,
            ConsumptionGateReason.RETRIEVAL_ADMISSION_REVALIDATED,
            ConsumptionGateReason.LOADING_REVALIDATION_ACCEPTED,
            ConsumptionGateReason.CONSUMPTION_COMPATIBILITY_ACCEPTED,
            ConsumptionGateReason.LIFECYCLE_REVALIDATED,
            ConsumptionGateReason.TAINT_CHAIN_REVALIDATED,
        ),
        CONSUMPTION_REQUIRED_DIMENSIONS,
        events,
    )
    retrieval_gate = configure_retrieval_gate_evaluator(
        authority_binding=authority.binding,
        evaluation_provider=retrieval_provider,
        trusted_clock=clock,
    )
    consumption_gate = configure_consumption_gate_evaluator(
        authority_binding=authority.binding,
        evaluation_provider=consumption_provider,
        trusted_clock=clock,
    )
    durability = create_compatibility_durability_binding(
        authority_binding=authority.binding,
        evidence_store=compatibility_store,
        coordination_context=snapshot.store.coordination_context,
    )
    return {
        "patch6": patch6,
        "snapshot": snapshot,
        "committed_boundary": committed_boundary,
        "retriever": retriever,
        "durability": durability,
        "query": query,
        "historical_query": historical_query,
        "candidates": candidates,
        "retrieval_gate": retrieval_gate,
        "consumption_gate": consumption_gate,
        "events": events,
        "admission_store": admission_store,
        "compatibility_store": compatibility_store,
    }


def _run_workflow(harness):
    snapshot = harness["snapshot"]
    return retrieve_snapshot_knowledge(
        retriever=harness["retriever"],
        compatibility_durability=harness["durability"],
        query=harness["query"],
        historical_context=harness["patch6"].context,
        historical_ranking_query=harness["historical_query"],
        manifest_core=snapshot.core,
        snapshot=snapshot.snapshot,
        committed_boundary=harness["committed_boundary"],
        snapshot_evaluator=snapshot.evaluator,
        knowledge_store=snapshot.store,
        resolved_candidates=harness["candidates"],
        retrieval_gate_evaluator=harness["retrieval_gate"],
        consumption_gate_evaluator=harness["consumption_gate"],
    )


def test_s4_acc_current_workflow_ranks_only_after_retrieval_gate_and_returns_handle(
    tmp_path: Path,
) -> None:
    harness = build_retrieval_harness(tmp_path)

    result = _run_workflow(harness)

    assert len(result.admitted_handles) == 1
    assert result.decision.schema_version == RETRIEVAL_DECISION_V2
    assert result.load_decisions[0].schema_version == RETRIEVAL_LOAD_DECISION_V2
    assert harness["events"].index("retrieval_gate") < harness["events"].index("ranking")
    assert harness["events"].index("ranking") < harness["events"].index("consumption_gate")
    assert harness["admission_store"].recover().diagnostic is None
    assert harness["compatibility_store"].recover().diagnostic is None


def test_s4_acc_live_index_is_not_candidate_authority_after_freeze(tmp_path: Path) -> None:
    harness = build_retrieval_harness(tmp_path, include_candidate=False)
    assert harness["patch6"].library.search_index()

    result = _run_workflow(harness)

    assert result.decision.considered_candidates == ()
    assert result.decision.selected_content_identities == ()
    assert result.admitted_handles == ()
    assert "ranking" not in harness["events"]


def test_s4_acc_resolved_candidates_must_equal_frozen_executable_view(
    tmp_path: Path,
) -> None:
    harness = build_retrieval_harness(tmp_path)
    harness["candidates"] = (*harness["candidates"], *harness["candidates"])

    with pytest.raises(RetrievalViolation) as raised:
        _run_workflow(harness)

    assert raised.value.failure_code is RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE
    assert "ranking" not in harness["events"]


def test_snapshot_retrieval_schema_constants_are_exact() -> None:
    assert RETRIEVAL_QUERY_V2 == "synapse.stage4.gold.retrieval-query/v2"
    assert RETRIEVAL_DECISION_V2 == "synapse.stage4.gold.retrieval-decision/v2"
    assert RETRIEVAL_LOAD_DECISION_V2 == "synapse.stage4.gold.retrieval-load-decision/v2"
