from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.experiments.gold.admission import (
    configure_consumption_gate_evaluator,
    configure_ingestion_gate_evaluator,
    configure_publication_gate_evaluator,
    configure_retrieval_gate_evaluator,
    evaluate_consumption_gate,
    evaluate_ingestion_gate,
    evaluate_publication_gate,
    evaluate_retrieval_gate,
    validate_consumption_decision,
    validate_ingestion_gate_decision,
    validate_publication_gate_decision,
    validate_retrieval_gate_decision,
)
from synapse.experiments.gold.admission_contracts import (
    CONSUMPTION_GATE_REQUEST_SCHEMA_V1,
    CONSUMPTION_REQUIRED_DIMENSIONS,
    GATE_AUTHORITY_HEADS_SCHEMA_V1,
    GATE_CHECKED_DIMENSION_SCHEMA_V1,
    GATE_CONSUMER_CONTEXT_SCHEMA_V1,
    GATE_EVALUATION_OBSERVATION_SCHEMA_V1,
    INGESTION_GATE_REQUEST_SCHEMA_V1,
    INGESTION_REQUIRED_DIMENSIONS,
    PUBLICATION_GATE_REQUEST_SCHEMA_V1,
    PUBLICATION_REQUIRED_DIMENSIONS,
    RETRIEVAL_GATE_REQUEST_SCHEMA_V1,
    RETRIEVAL_REQUIRED_DIMENSIONS,
    AdmissionFailureCode,
    AdmissionViolation,
    ConsumptionDecision,
    ConsumptionGateReason,
    GateAuthorityHeads,
    GateCheckedDimension,
    GateConsumerContext,
    GateDecisionKind,
    GateDimensionResult,
    GateEvaluationObservation,
    IngestionGateDecision,
    IngestionGateReason,
    PublicationGateDecision,
    PublicationGateReason,
    RetrievalGateDecision,
    RetrievalGateReason,
    consumption_gate_request_payload,
    create_consumption_gate_request,
    create_ingestion_gate_request,
    create_publication_gate_request,
    create_retrieval_gate_request,
    ingestion_gate_request_payload,
    publication_gate_request_payload,
    retrieval_gate_request_payload,
)
from synapse.experiments.gold.admission_store import (
    evaluate_and_commit_ingestion_gate,
    evaluate_and_commit_publication_gate,
    open_admission_history_store,
)
from synapse.experiments.gold.canonicalization import (
    HashBoundRef,
    RefKind,
    STAGE4_CANONICAL_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityRole,
    HistoryDomain,
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    RepositoryRevision,
    RunId,
    AttemptId,
    SchemaVersion,
    compute_record_id,
    create_common_envelope,
    create_history_anchor,
)

from tests.test_stage4_gold_knowledge_snapshot import build_snapshot_harness


ADMISSION_MATRIX_PATH = (
    Path(__file__).parent / "fixtures" / "gold" / "admission_matrix_v1.json"
)


def _admission_matrix_cases() -> tuple[dict[str, object], ...]:
    data = json.loads(ADMISSION_MATRIX_PATH.read_text(encoding="utf-8"))
    assert data["schema"] == "synapse.stage4.gold.admission-matrix-fixture/v1"
    return tuple(data["cases"])


NOW = datetime(2026, 7, 25, 11, 0, tzinfo=timezone.utc)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _ref(name: str) -> HashBoundRef:
    raw = name.encode("utf-8")
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"patch78.{name}",
        schema_id=f"synapse.stage4.gold.test.{name}/v1",
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_length=len(raw),
        media_type="application/octet-stream",
    )


def _heads(harness) -> GateAuthorityHeads:
    return GateAuthorityHeads(
        GATE_AUTHORITY_HEADS_SCHEMA_V1,
        harness.core.lifecycle_root,
        harness.core.provenance_root,
        harness.core.taint_root,
        harness.core.admission_root,
        harness.core.compatibility_evidence_root,
    )


CONSUMER_CONTEXT = GateConsumerContext(
    GATE_CONSUMER_CONTEXT_SCHEMA_V1,
    ("repository-read",),
    ("read-behavior",),
    ("python",),
    ("trusted-oracle",),
)


def _request_envelope(
    *,
    harness,
    domain: IdentityDomain,
    payload: dict[str, object],
    lineage: tuple[LineageParentRef, ...],
):
    return create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=domain,
        canonical_payload_bytes=_canonical(payload),
        run_id=harness.core.envelope.run_id,
        attempt_id=harness.core.envelope.attempt_id,
        created_at_utc=NOW,
        producer_component="patch78-gate-request-producer",
        repository_revision=harness.core.envelope.repository_revision,
        policy_version=harness.core.envelope.policy_version,
        environment_profile_id=harness.core.envelope.environment_profile_id,
        lineage_parent_ids=lineage,
    )


def build_gate_requests(tmp_path: Path):
    harness = build_snapshot_harness(tmp_path / "snapshot")
    heads = _heads(harness)
    common = {
        "consumer_context": CONSUMER_CONTEXT,
        "authority_heads": heads,
        "scope_ids": CONSUMER_CONTEXT.scope,
        "capability_ids": CONSUMER_CONTEXT.capabilities,
        "tool_ids": CONSUMER_CONTEXT.tools,
        "oracle_ids": CONSUMER_CONTEXT.oracle_identities,
        "observed_at_utc": NOW,
        "valid_until_utc": NOW + timedelta(minutes=30),
        "predecessor_sequence": 0,
    }
    subject_ref = _ref("subject")
    transaction = compute_record_id(
        domain=IdentityDomain.SNAPSHOT_TRANSACTION,
        canonical_bytes=b"patch78-gate-transaction",
    )
    source_identity = ActorIdentity("patch78-source")
    observed_ref = _ref("observed-input")
    ingestion_payload = ingestion_gate_request_payload(
        source_transaction_id=transaction,
        source_identity=source_identity,
        subject_ref=subject_ref,
        observed_input_refs=(observed_ref,),
        predecessor_decision_ref=None,
        **common,
    )
    ingestion = create_ingestion_gate_request(
        envelope=_request_envelope(
            harness=harness,
            domain=IdentityDomain.INGESTION_GATE_REQUEST,
            payload=ingestion_payload,
            lineage=(LineageParentRef(transaction, LineageEdgeKind.REFERENCES),),
        ),
        source_transaction_id=transaction,
        source_identity=source_identity,
        subject_ref=subject_ref,
        observed_input_refs=(observed_ref,),
        predecessor_decision_ref=None,
        **common,
    )
    publication_payload = publication_gate_request_payload(
        publication_transaction_id=transaction,
        ingested_candidate_ref=_ref("ingested-candidate"),
        ingestion_decision_ref=_ref("ingestion-decision"),
        subject_ref=subject_ref,
        attestation_ref=_ref("attestation"),
        binding_refs=(_ref("binding"),),
        target_library_predecessor_root="library-root-0",
        **common,
    )
    publication = create_publication_gate_request(
        envelope=_request_envelope(
            harness=harness,
            domain=IdentityDomain.PUBLICATION_GATE_REQUEST,
            payload=publication_payload,
            lineage=(LineageParentRef(transaction, LineageEdgeKind.REFERENCES),),
        ),
        publication_transaction_id=transaction,
        ingested_candidate_ref=_ref("ingested-candidate"),
        ingestion_decision_ref=_ref("ingestion-decision"),
        subject_ref=subject_ref,
        attestation_ref=_ref("attestation"),
        binding_refs=(_ref("binding"),),
        target_library_predecessor_root="library-root-0",
        **common,
    )
    retrieval_payload = retrieval_gate_request_payload(
        repository_knowledge_snapshot_id=harness.snapshot.snapshot_id,
        atomic_boundary_id=harness.boundary.atomic_boundary_id,
        boundary_commit_sequence=harness.boundary.commit_sequence,
        candidate_ref=_ref("candidate"),
        compatibility_decision_ref=_ref("compatibility-decision"),
        conflict_decision_ref=_ref("conflict-decision"),
        subject_ref=subject_ref,
        **common,
    )
    boundary_lineage = (
        LineageParentRef(
            harness.snapshot.snapshot_id,
            LineageEdgeKind.REFERENCES,
        ),
        LineageParentRef(
            harness.boundary.atomic_boundary_id,
            LineageEdgeKind.REFERENCES,
        ),
    )
    retrieval = create_retrieval_gate_request(
        envelope=_request_envelope(
            harness=harness,
            domain=IdentityDomain.RETRIEVAL_GATE_REQUEST,
            payload=retrieval_payload,
            lineage=boundary_lineage,
        ),
        repository_knowledge_snapshot_id=harness.snapshot.snapshot_id,
        atomic_boundary_id=harness.boundary.atomic_boundary_id,
        boundary_commit_sequence=harness.boundary.commit_sequence,
        candidate_ref=_ref("candidate"),
        compatibility_decision_ref=_ref("compatibility-decision"),
        conflict_decision_ref=_ref("conflict-decision"),
        subject_ref=subject_ref,
        **common,
    )
    consumption_payload = consumption_gate_request_payload(
        repository_knowledge_snapshot_id=harness.snapshot.snapshot_id,
        atomic_boundary_id=harness.boundary.atomic_boundary_id,
        boundary_commit_sequence=harness.boundary.commit_sequence,
        retrieval_decision_ref=_ref("retrieval-decision"),
        load_decision_ref=_ref("load-decision"),
        compatibility_revalidation_ref=_ref("compatibility-revalidation"),
        subject_ref=subject_ref,
        **common,
    )
    consumption = create_consumption_gate_request(
        envelope=_request_envelope(
            harness=harness,
            domain=IdentityDomain.CONSUMPTION_GATE_REQUEST,
            payload=consumption_payload,
            lineage=boundary_lineage,
        ),
        repository_knowledge_snapshot_id=harness.snapshot.snapshot_id,
        atomic_boundary_id=harness.boundary.atomic_boundary_id,
        boundary_commit_sequence=harness.boundary.commit_sequence,
        retrieval_decision_ref=_ref("retrieval-decision"),
        load_decision_ref=_ref("load-decision"),
        compatibility_revalidation_ref=_ref("compatibility-revalidation"),
        subject_ref=subject_ref,
        **common,
    )
    return harness, (ingestion, publication, retrieval, consumption)


@dataclass
class ObservationProvider:
    profile_id: str
    component_identity: ActorIdentity
    role: AuthorityRole
    reasons: tuple[object, ...]
    required_dimensions: tuple[str, ...]
    raise_error: bool = False

    def observe_gate(self, *, request: object) -> GateEvaluationObservation:
        if self.raise_error:
            raise RuntimeError("provider unavailable")
        evidence_ref = request.subject_ref
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
                for dimension in self.required_dimensions
            ),
            (ActorIdentity(request.envelope.producer_component),),
            (ActorIdentity("patch78-observed-source"),),
            ActorIdentity("patch78-gate-proposer"),
            ActorIdentity("patch78-gate-executor"),
            (),
            (("observation", "accepted"),),
        )


_GATE_SPECS = (
    (
        "ingestion",
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        IngestionGateReason,
        INGESTION_REQUIRED_DIMENSIONS,
        (IngestionGateReason.SOURCE_ACCEPTED, IngestionGateReason.PROVENANCE_COMPLETE),
        IngestionGateReason.HUMAN_REVIEW_REQUIRED,
        IngestionGateReason.SOURCE_POLICY_REJECTED,
        IngestionGateReason.SECRET_LIKE_DATA,
        configure_ingestion_gate_evaluator,
        evaluate_ingestion_gate,
        validate_ingestion_gate_decision,
    ),
    (
        "publication",
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        PublicationGateReason,
        PUBLICATION_REQUIRED_DIMENSIONS,
        (
            PublicationGateReason.VERIFIED_OBJECT_ACCEPTED,
            PublicationGateReason.ATTESTATION_ACCEPTED,
            PublicationGateReason.LIFECYCLE_ELIGIBLE,
            PublicationGateReason.TAINT_ACCEPTED,
            PublicationGateReason.BINDINGS_RESOLVED,
            PublicationGateReason.POLICY_ACCEPTED,
        ),
        PublicationGateReason.HUMAN_REVIEW_REQUIRED,
        PublicationGateReason.OBJECT_UNVERIFIED,
        PublicationGateReason.SECRET_LIKE_DATA,
        configure_publication_gate_evaluator,
        evaluate_publication_gate,
        validate_publication_gate_decision,
    ),
    (
        "retrieval",
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        RetrievalGateReason,
        RETRIEVAL_REQUIRED_DIMENSIONS,
        (
            RetrievalGateReason.BOUNDARY_COMMITTED,
            RetrievalGateReason.SUBJECT_IN_EXECUTABLE_VIEW,
            RetrievalGateReason.CONTEXT_BOUND,
            RetrievalGateReason.COMPATIBILITY_ACCEPTED,
            RetrievalGateReason.LIFECYCLE_ELIGIBLE,
            RetrievalGateReason.TAINT_ACCEPTED,
            RetrievalGateReason.CONFLICTS_RESOLVED,
        ),
        RetrievalGateReason.HUMAN_REVIEW_REQUIRED,
        RetrievalGateReason.BOUNDARY_INVALID,
        RetrievalGateReason.TAINT_BLOCKED,
        configure_retrieval_gate_evaluator,
        evaluate_retrieval_gate,
        validate_retrieval_gate_decision,
    ),
    (
        "consumption",
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        ConsumptionGateReason,
        CONSUMPTION_REQUIRED_DIMENSIONS,
        (
            ConsumptionGateReason.BOUNDARY_REVALIDATED,
            ConsumptionGateReason.SUBJECT_IDENTITY_REVALIDATED,
            ConsumptionGateReason.RETRIEVAL_ADMISSION_REVALIDATED,
            ConsumptionGateReason.LOADING_REVALIDATION_ACCEPTED,
            ConsumptionGateReason.CONSUMPTION_COMPATIBILITY_ACCEPTED,
            ConsumptionGateReason.LIFECYCLE_REVALIDATED,
            ConsumptionGateReason.TAINT_CHAIN_REVALIDATED,
        ),
        ConsumptionGateReason.HUMAN_REVIEW_REQUIRED,
        ConsumptionGateReason.BOUNDARY_STALE,
        ConsumptionGateReason.SECRET_LIKE_DATA,
        configure_consumption_gate_evaluator,
        evaluate_consumption_gate,
        validate_consumption_decision,
    ),
)


def _configured_evaluator(harness, spec, reasons, *, raise_error=False):
    name, role, _, dimensions, _, _, _, _, configure, _, _ = spec
    declaration = getattr(
        harness.authority.binding.knowledge_admission_authority_handle.configuration,
        f"{name}_gate_evaluator",
    )
    provider = ObservationProvider(
        declaration.resolver_profile_ids[0],
        declaration.evaluator_component_identity,
        role,
        reasons,
        dimensions,
        raise_error,
    )
    return configure(
        authority_binding=harness.authority.binding,
        evaluation_provider=provider,
        trusted_clock=lambda: NOW + timedelta(seconds=1),
    )


@pytest.mark.parametrize("gate_index", range(4))
def test_s4_acc_gate_positive_matrix_uses_four_exact_decision_types(
    tmp_path: Path,
    gate_index: int,
) -> None:
    harness, requests = build_gate_requests(tmp_path)
    spec = _GATE_SPECS[gate_index]
    evaluator = _configured_evaluator(harness, spec, spec[4])
    decision = spec[9](evaluator=evaluator, request=requests[gate_index])

    spec[10](decision)
    assert decision.decision_kind is GateDecisionKind.ADMIT
    assert type(decision) is (
        IngestionGateDecision,
        PublicationGateDecision,
        RetrievalGateDecision,
        ConsumptionDecision,
    )[gate_index]
    assert tuple(item.dimension for item in decision.checked_dimensions) == spec[3]


@pytest.mark.parametrize("gate_index", range(4))
def test_s4_acc_gate_precedence_is_quarantine_reject_review_admit(
    tmp_path: Path,
    gate_index: int,
) -> None:
    harness, requests = build_gate_requests(tmp_path)
    spec = _GATE_SPECS[gate_index]
    reasons = tuple(
        item
        for item in spec[2]
        if item in {*spec[4], spec[5], spec[6], spec[7]}
    )
    evaluator = _configured_evaluator(harness, spec, reasons)

    decision = spec[9](evaluator=evaluator, request=requests[gate_index])

    assert decision.decision_kind is GateDecisionKind.QUARANTINE
    assert decision.reasons == reasons


@pytest.mark.parametrize("gate_index", range(4))
def test_s4_acc_gate_provider_exception_fails_closed(
    tmp_path: Path,
    gate_index: int,
) -> None:
    harness, requests = build_gate_requests(tmp_path)
    spec = _GATE_SPECS[gate_index]
    evaluator = _configured_evaluator(
        harness,
        spec,
        spec[4],
        raise_error=True,
    )

    decision = spec[9](evaluator=evaluator, request=requests[gate_index])

    assert decision.decision_kind is GateDecisionKind.REJECT
    assert decision.reasons[-1].value == "DEPENDENCY_UNAVAILABLE"


def test_s4_acc_gate_types_are_not_interchangeable(tmp_path: Path) -> None:
    harness, requests = build_gate_requests(tmp_path)
    ingestion = _configured_evaluator(harness, _GATE_SPECS[0], _GATE_SPECS[0][4])

    with pytest.raises(AdmissionViolation) as raised:
        evaluate_ingestion_gate(evaluator=ingestion, request=requests[2])

    assert raised.value.failure_code is AdmissionFailureCode.TYPE_MISMATCH


def test_s4_acc_gate_decision_rejects_replace_and_low_level_tampering(
    tmp_path: Path,
) -> None:
    harness, requests = build_gate_requests(tmp_path)
    spec = _GATE_SPECS[0]
    evaluator = _configured_evaluator(harness, spec, spec[4])
    decision = evaluate_ingestion_gate(evaluator=evaluator, request=requests[0])

    with pytest.raises((TypeError, AdmissionViolation)):
        replace(decision, decision_kind=GateDecisionKind.REJECT)
    object.__setattr__(decision, "decision_kind", GateDecisionKind.REJECT)
    with pytest.raises(AdmissionViolation) as raised:
        validate_ingestion_gate_decision(decision)
    assert raised.value.failure_code is AdmissionFailureCode.REASON_OUTCOME_MISMATCH


def test_s4_acc_ingestion_and_publication_admit_are_durable_before_return(
    tmp_path: Path,
) -> None:
    harness, requests = build_gate_requests(tmp_path)
    store = open_admission_history_store(
        (tmp_path / "admission-store").resolve(),
        authority_binding=harness.authority.binding,
        coordination_context=harness.store.coordination_context,
    )
    ingestion_evaluator = _configured_evaluator(
        harness,
        _GATE_SPECS[0],
        _GATE_SPECS[0][4],
    )
    publication_evaluator = _configured_evaluator(
        harness,
        _GATE_SPECS[1],
        _GATE_SPECS[1][4],
    )

    ingestion = evaluate_and_commit_ingestion_gate(
        evaluator=ingestion_evaluator,
        admission_store=store,
        request=requests[0],
    )
    previous_publication = requests[1]
    publication_payload = publication_gate_request_payload(
        publication_transaction_id=previous_publication.publication_transaction_id,
        ingested_candidate_ref=previous_publication.ingested_candidate_ref,
        ingestion_decision_ref=ingestion.commit_evidence.artifact_ref,
        subject_ref=previous_publication.subject_ref,
        attestation_ref=previous_publication.attestation_ref,
        binding_refs=previous_publication.binding_refs,
        target_library_predecessor_root=(
            previous_publication.target_library_predecessor_root
        ),
        consumer_context=previous_publication.consumer_context,
        authority_heads=previous_publication.authority_heads,
        scope_ids=previous_publication.scope_ids,
        capability_ids=previous_publication.capability_ids,
        tool_ids=previous_publication.tool_ids,
        oracle_ids=previous_publication.oracle_ids,
        observed_at_utc=previous_publication.observed_at_utc,
        valid_until_utc=previous_publication.valid_until_utc,
        predecessor_sequence=1,
    )
    publication_request = create_publication_gate_request(
        envelope=_request_envelope(
            harness=harness,
            domain=IdentityDomain.PUBLICATION_GATE_REQUEST,
            payload=publication_payload,
            lineage=(
                LineageParentRef(
                    previous_publication.publication_transaction_id,
                    LineageEdgeKind.REFERENCES,
                ),
            ),
        ),
        publication_transaction_id=previous_publication.publication_transaction_id,
        ingested_candidate_ref=previous_publication.ingested_candidate_ref,
        ingestion_decision_ref=ingestion.commit_evidence.artifact_ref,
        subject_ref=previous_publication.subject_ref,
        attestation_ref=previous_publication.attestation_ref,
        binding_refs=previous_publication.binding_refs,
        target_library_predecessor_root=(
            previous_publication.target_library_predecessor_root
        ),
        consumer_context=previous_publication.consumer_context,
        authority_heads=previous_publication.authority_heads,
        scope_ids=previous_publication.scope_ids,
        capability_ids=previous_publication.capability_ids,
        tool_ids=previous_publication.tool_ids,
        oracle_ids=previous_publication.oracle_ids,
        observed_at_utc=previous_publication.observed_at_utc,
        valid_until_utc=previous_publication.valid_until_utc,
        predecessor_sequence=1,
    )
    publication = evaluate_and_commit_publication_gate(
        evaluator=publication_evaluator,
        admission_store=store,
        request=publication_request,
    )

    recovery = store.recover()
    assert recovery.diagnostic is None
    assert recovery.head == publication.commit_evidence
    assert ingestion.decision.decision_kind is GateDecisionKind.ADMIT
    assert publication.decision.decision_kind is GateDecisionKind.ADMIT
    assert store.require_inclusion(
        record_identity=publication.decision.decision_id.record_id.value,
        artifact_ref=publication.commit_evidence.artifact_ref,
        expected_evidence=publication.commit_evidence,
    ) == publication.commit_evidence


def test_gate_request_schema_constants_are_exact() -> None:
    assert INGESTION_GATE_REQUEST_SCHEMA_V1 == "synapse.stage4.gold.ingestion-gate-request/v1"
    assert PUBLICATION_GATE_REQUEST_SCHEMA_V1 == "synapse.stage4.gold.publication-gate-request/v1"
    assert RETRIEVAL_GATE_REQUEST_SCHEMA_V1 == "synapse.stage4.gold.retrieval-gate-request/v1"
    assert CONSUMPTION_GATE_REQUEST_SCHEMA_V1 == "synapse.stage4.gold.consumption-gate-request/v1"


@pytest.mark.parametrize(
    "case",
    _admission_matrix_cases(),
    ids=lambda case: case["id"],
)
def test_s4_acc_admission_matrix_vectors_execute_exact_gate_contracts(
    tmp_path: Path,
    case: dict[str, object],
) -> None:
    gate_index = ("ingestion", "publication", "retrieval", "consumption").index(
        case["gate"]
    )
    harness, requests = build_gate_requests(tmp_path)
    spec = _GATE_SPECS[gate_index]
    reason_enum = spec[2]
    reasons = tuple(reason_enum[name] for name in case["reasons"])
    evaluator = _configured_evaluator(harness, spec, reasons)

    decision = spec[9](evaluator=evaluator, request=requests[gate_index])

    spec[10](decision)
    assert decision.decision_kind is GateDecisionKind[case["expected"]]
    assert decision.reasons == reasons
