"""Independent Stage 4 knowledge admission gates and durable decisions.

Ingestion, publication, retrieval, and consumption are separate authority
boundaries. Only a durable fresh Consumption ADMIT can issue an admitted
knowledge handle; this module performs no library publication or behavior
execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Protocol

from .canonicalization import (
    HashBoundRef,
    RefKind,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityDecisionId,
    AuthorityRole,
    CommonEnvelope,
    ContractViolation,
    ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    ENVELOPED_ARTIFACT_SCHEMA_V1,
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    IndependenceProof,
    LineageEdgeKind,
    LineageParentRef,
    RecordId,
    SchemaVersion,
    authority_decision_id_from_dict,
    authority_decision_identity_bytes,
    common_envelope_from_dict,
    compute_authority_decision_id,
    compute_proposal_id,
    create_common_envelope,
    create_history_anchor,
    create_independence_proof,
    independence_proof_from_dict,
    record_id_from_dict,
    validate_common_envelope,
    validate_history_anchor,
    validate_history_anchor_extension,
    validate_independence_proof,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    KnowledgeAdmissionEvaluatorDeclaration,
    validate_knowledge_admission_authority_binding,
    validate_knowledge_admission_evaluator_declaration,
)


INGESTION_GATE_REQUEST_SCHEMA_V1 = (
    "synapse.stage4.gold.ingestion-gate-request/v1"
)
PUBLICATION_GATE_REQUEST_SCHEMA_V1 = (
    "synapse.stage4.gold.publication-gate-request/v1"
)
RETRIEVAL_GATE_REQUEST_SCHEMA_V1 = (
    "synapse.stage4.gold.retrieval-gate-request/v1"
)
CONSUMPTION_GATE_REQUEST_SCHEMA_V1 = (
    "synapse.stage4.gold.consumption-gate-request/v1"
)
INGESTION_GATE_DECISION_SCHEMA_V1 = (
    "synapse.stage4.gold.ingestion-gate-decision/v1"
)
PUBLICATION_GATE_DECISION_SCHEMA_V1 = (
    "synapse.stage4.gold.publication-gate-decision/v1"
)
RETRIEVAL_GATE_DECISION_SCHEMA_V1 = (
    "synapse.stage4.gold.retrieval-gate-decision/v1"
)
CONSUMPTION_GATE_DECISION_SCHEMA_V1 = (
    "synapse.stage4.gold.consumption-gate-decision/v1"
)
ADMISSION_HISTORY_FRAME_SCHEMA_V1 = (
    "synapse.stage4.gold.admission-history-frame/v1"
)
ADMITTED_KNOWLEDGE_HANDLE_SCHEMA_V1 = (
    "synapse.stage4.gold.admitted-knowledge-handle/v1"
)
GATE_CONSUMER_CONTEXT_SCHEMA_V1 = (
    "synapse.stage4.gold.gate-consumer-context/v1"
)
GATE_AUTHORITY_HEADS_SCHEMA_V1 = (
    "synapse.stage4.gold.gate-authority-heads/v1"
)
GATE_CHECKED_DIMENSION_SCHEMA_V1 = (
    "synapse.stage4.gold.gate-checked-dimension/v1"
)
ADMISSION_COMMIT_EVIDENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.admission-commit-evidence/v1"
)
ADMISSION_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.admission+json"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/|-]{0,511}\Z")
_REQUEST_SEAL = object()
_DECISION_SEAL = object()
_EVALUATOR_SEAL = object()
_STORE_SEAL = object()
_BINDING_SEAL = object()
_RESOLVER_SEAL = object()
_COMMIT_SEAL = object()
_HANDLE_SEAL = object()


from .admission_contracts import *
from .admission_contracts import (
    _DECISION_SPEC,
    _GATE_REQUIRED_DIMENSIONS,
    _REQUEST_SPEC,
    _artifact_ref,
    _canonical,
    _checked_dimensions,
    _decision_payload,
    _derive_decision_kind,
    _diagnostics,
    _fail,
    _ordered_reasons,
    _timestamp,
    _validate_gate_evaluation_observation,
    _validate_gate_request,
)


@dataclass(frozen=True, init=False)
class ConfiguredIngestionGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    evaluation_provider: GateEvaluationProvider
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredIngestionGateEvaluator:
        raise TypeError("ConfiguredIngestionGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredPublicationGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    evaluation_provider: GateEvaluationProvider
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredPublicationGateEvaluator:
        raise TypeError("ConfiguredPublicationGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredRetrievalGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    evaluation_provider: GateEvaluationProvider
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredRetrievalGateEvaluator:
        raise TypeError("ConfiguredRetrievalGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredConsumptionGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    evaluation_provider: GateEvaluationProvider
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredConsumptionGateEvaluator:
        raise TypeError("ConfiguredConsumptionGateEvaluator is factory-created")


_EVALUATOR_SPEC = {
    ConfiguredIngestionGateEvaluator: AuthorityRole.INGESTION_GATE_EVALUATOR,
    ConfiguredPublicationGateEvaluator: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
    ConfiguredRetrievalGateEvaluator: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
    ConfiguredConsumptionGateEvaluator: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
}

_DECISION_GRAPH_SPEC = {
    IngestionGateDecision: (
        IngestionGateRequest,
        ConfiguredIngestionGateEvaluator,
    ),
    PublicationGateDecision: (
        PublicationGateRequest,
        ConfiguredPublicationGateEvaluator,
    ),
    RetrievalGateDecision: (
        RetrievalGateRequest,
        ConfiguredRetrievalGateEvaluator,
    ),
    ConsumptionDecision: (
        ConsumptionGateRequest,
        ConfiguredConsumptionGateEvaluator,
    ),
}


def _validate_gate_evaluation_provider(
    value: object,
    *,
    declaration: KnowledgeAdmissionEvaluatorDeclaration,
) -> None:
    validate_knowledge_admission_evaluator_declaration(declaration)
    profile_id = getattr(value, "profile_id", None)
    component_identity = getattr(value, "component_identity", None)
    observe_gate = getattr(value, "observe_gate", None)
    if (
        type(profile_id) is not str
        or profile_id not in declaration.resolver_profile_ids
        or type(component_identity) is not ActorIdentity
        or component_identity != declaration.evaluator_component_identity
        or not callable(observe_gate)
    ):
        raise _fail(
            AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
            "gate evaluation provider does not match its declaration",
        )
    component_identity.to_dict()


def _configure_gate_evaluator(
    evaluator_type: type,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evaluation_provider: GateEvaluationProvider,
    trusted_clock: Callable[[], datetime],
) -> object:
    _, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    role = _EVALUATOR_SPEC[evaluator_type]
    declaration_by_role = {
        AuthorityRole.INGESTION_GATE_EVALUATOR: overlay.ingestion_gate_evaluator,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR: overlay.publication_gate_evaluator,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR: overlay.retrieval_gate_evaluator,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR: overlay.consumption_gate_evaluator,
    }
    declaration = declaration_by_role[role]
    validate_knowledge_admission_evaluator_declaration(
        declaration,
        expected_base_authority_handle=authority_binding.base_authority_handle,
        expected_role=role,
    )
    if declaration.required_checked_dimensions != _GATE_REQUIRED_DIMENSIONS[role]:
        raise _fail(
            AdmissionFailureCode.DIMENSION_MISSING,
            "gate declaration does not match its closed dimension registry",
        )
    _validate_gate_evaluation_provider(
        evaluation_provider,
        declaration=declaration,
    )
    if not callable(trusted_clock):
        raise _fail(
            AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
            "gate evaluator trusted clock is unavailable",
        )
    result = object.__new__(evaluator_type)
    object.__setattr__(result, "authority_binding", authority_binding)
    object.__setattr__(result, "declaration", declaration)
    object.__setattr__(result, "evaluation_provider", evaluation_provider)
    object.__setattr__(result, "trusted_clock", trusted_clock)
    object.__setattr__(
        result,
        "_configuration_snapshot",
        (
            authority_binding,
            declaration,
            evaluation_provider,
            trusted_clock,
        ),
    )
    object.__setattr__(result, "_trusted_seal", _EVALUATOR_SEAL)
    _require_configured_gate_evaluator(result, evaluator_type=evaluator_type)
    return result


def configure_ingestion_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evaluation_provider: GateEvaluationProvider,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredIngestionGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredIngestionGateEvaluator,
        authority_binding=authority_binding,
        evaluation_provider=evaluation_provider,
        trusted_clock=trusted_clock,
    )


def configure_publication_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evaluation_provider: GateEvaluationProvider,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredPublicationGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredPublicationGateEvaluator,
        authority_binding=authority_binding,
        evaluation_provider=evaluation_provider,
        trusted_clock=trusted_clock,
    )


def configure_retrieval_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evaluation_provider: GateEvaluationProvider,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredRetrievalGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredRetrievalGateEvaluator,
        authority_binding=authority_binding,
        evaluation_provider=evaluation_provider,
        trusted_clock=trusted_clock,
    )


def configure_consumption_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evaluation_provider: GateEvaluationProvider,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredConsumptionGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredConsumptionGateEvaluator,
        authority_binding=authority_binding,
        evaluation_provider=evaluation_provider,
        trusted_clock=trusted_clock,
    )


def _require_configured_gate_evaluator(
    value: object,
    *,
    evaluator_type: type,
) -> tuple[KnowledgeAdmissionAuthorityBinding, KnowledgeAdmissionEvaluatorDeclaration]:
    if (
        type(value) is not evaluator_type
        or getattr(value, "_trusted_seal", None) is not _EVALUATOR_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "gate evaluator is not factory sealed",
        )
    base, overlay = validate_knowledge_admission_authority_binding(
        value.authority_binding
    )
    role = _EVALUATOR_SPEC[evaluator_type]
    expected = {
        AuthorityRole.INGESTION_GATE_EVALUATOR: overlay.ingestion_gate_evaluator,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR: overlay.publication_gate_evaluator,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR: overlay.retrieval_gate_evaluator,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR: overlay.consumption_gate_evaluator,
    }[role]
    snapshot = getattr(value, "_configuration_snapshot", None)
    if (
        value.declaration is not expected
        or type(snapshot) is not tuple
        or len(snapshot) != 4
        or snapshot[0] is not value.authority_binding
        or snapshot[1] is not value.declaration
        or snapshot[2] is not value.evaluation_provider
        or snapshot[3] is not value.trusted_clock
    ):
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "gate evaluator declaration changed",
        )
    validate_knowledge_admission_evaluator_declaration(
        value.declaration,
        expected_base_authority_handle=value.authority_binding.base_authority_handle,
        expected_role=role,
    )
    if value.declaration.required_checked_dimensions != _GATE_REQUIRED_DIMENSIONS[role]:
        raise _fail(
            AdmissionFailureCode.DIMENSION_MISSING,
            "gate evaluator dimensions changed",
        )
    _validate_gate_evaluation_provider(
        value.evaluation_provider,
        declaration=value.declaration,
    )
    if not callable(value.trusted_clock):
        raise _fail(
            AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
            "gate evaluator trusted clock changed",
        )
    return value.authority_binding, value.declaration


def _dependency_failure_observation(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    declaration: KnowledgeAdmissionEvaluatorDeclaration,
    request: object,
    reason_type: type[Enum],
    required_dimensions: tuple[str, ...],
) -> GateEvaluationObservation:
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    request_payload = _REQUEST_SPEC[type(request)][2](request)
    request_ref = _artifact_ref(request.envelope, request_payload)
    if type(request) is IngestionGateRequest:
        source_actor = request.source_identity
    elif type(request) is PublicationGateRequest:
        source_actor = overlay.compatibility_resolver_component_identity
    elif type(request) is RetrievalGateRequest:
        source_actor = overlay.retrieval_authority_resolver_component_identity
    else:
        source_actor = overlay.knowledge_boundary_resolver_component_identity
    return GateEvaluationObservation(
        schema_version=GATE_EVALUATION_OBSERVATION_SCHEMA_V1,
        authority_role=declaration.authority_role,
        request_id=request.request_id,
        reasons=(reason_type.DEPENDENCY_UNAVAILABLE,),
        checked_dimensions=tuple(
            GateCheckedDimension(
                GATE_CHECKED_DIMENSION_SCHEMA_V1,
                dimension,
                GateDimensionResult.UNAVAILABLE,
                (request_ref,),
            )
            for dimension in required_dimensions
        ),
        producer_actor_ids=(declaration.evaluator_component_identity,),
        source_actor_ids=(source_actor,),
        proposer_identity=declaration.evaluator_component_identity,
        executor_identity=None,
        subject_derived_actor_ids=(),
        diagnostics=(("failure", "evaluation_dependency_unavailable"),),
    )


def _request_evidence_refs(request: object) -> tuple[HashBoundRef, ...]:
    request_ref = _artifact_ref(
        request.envelope,
        _REQUEST_SPEC[type(request)][2](request),
    )
    if type(request) is IngestionGateRequest:
        refs = (
            request_ref,
            request.subject_ref,
            *request.observed_input_refs,
            *(
                ()
                if request.predecessor_decision_ref is None
                else (request.predecessor_decision_ref,)
            ),
        )
    elif type(request) is PublicationGateRequest:
        refs = (
            request_ref,
            request.subject_ref,
            request.ingested_candidate_ref,
            request.ingestion_decision_ref,
            request.attestation_ref,
            *request.binding_refs,
        )
    elif type(request) is RetrievalGateRequest:
        refs = (
            request_ref,
            request.subject_ref,
            request.candidate_ref,
            request.compatibility_decision_ref,
            request.conflict_decision_ref,
        )
    elif type(request) is ConsumptionGateRequest:
        refs = (
            request_ref,
            request.subject_ref,
            request.retrieval_decision_ref,
            request.load_decision_ref,
            request.compatibility_revalidation_ref,
        )
    else:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate request has no evidence-ref contract",
        )
    return tuple(HashBoundRef.from_dict(item.to_dict()) for item in refs)


def _evaluate_gate(
    *,
    evaluator: object,
    evaluator_type: type,
    request: object,
    request_type: type,
    decision_type: type,
    predecessor_decision: object | None,
) -> object:
    authority_binding, declaration = _require_configured_gate_evaluator(
        evaluator,
        evaluator_type=evaluator_type,
    )
    if type(request) is not request_type:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate request belongs to another gate",
        )
    _validate_gate_request(request)
    validate_gate_authority_heads(
        request.authority_heads,
        authority_binding=authority_binding,
    )
    try:
        evaluated = _timestamp(
            evaluator.trusted_clock(),
            "evaluated_at_utc",
        )
    except Exception as exc:
        raise _fail(
            AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
            "gate evaluator trusted clock failed closed",
        ) from exc
    expiry = request.valid_until_utc
    if evaluated < request.observed_at_utc or evaluated >= request.valid_until_utc:
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "gate evaluation is outside its request validity interval",
        )
    schema, role, reason_type, required = _DECISION_SPEC[decision_type]
    try:
        observation = evaluator.evaluation_provider.observe_gate(
            request=request,
        )
        _validate_gate_evaluation_observation(
            observation,
            expected_role=role,
            expected_request_id=request.request_id,
        )
    except Exception:
        observation = _dependency_failure_observation(
            authority_binding=authority_binding,
            declaration=declaration,
            request=request,
            reason_type=reason_type,
            required_dimensions=required,
        )
    reason_items = _ordered_reasons(
        observation.reasons,
        reason_type=reason_type,
    )
    dimensions = _checked_dimensions(
        observation.checked_dimensions,
        required=required,
    )
    allowed_evidence_refs = set(_request_evidence_refs(request))
    if any(
        evidence_ref not in allowed_evidence_refs
        for dimension in dimensions
        for evidence_ref in dimension.evidence_refs
    ):
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "gate observation cites evidence outside the exact request",
        )
    decision_kind = _derive_decision_kind(
        reason_type=reason_type,
        reasons=reason_items,
        checked_dimensions=dimensions,
    )
    request_payload = _REQUEST_SPEC[request_type][2](request)
    request_ref = _artifact_ref(request.envelope, request_payload)
    proposal_id = compute_proposal_id(canonical_bytes=_canonical(request_payload))
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal_id,
        authority_identity=declaration.evaluator_identity,
        authority_role=role,
        reason_code=declaration.independence_reason,
        producer_actor_ids=observation.producer_actor_ids,
        source_actor_ids=observation.source_actor_ids,
        proposer_identity=observation.proposer_identity,
        executor_identity=observation.executor_identity,
        subject_derived_actor_ids=observation.subject_derived_actor_ids,
        delegation_chain=(),
    )
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if predecessor_decision is None:
        predecessor_id = None
        decision_sequence = 1
    else:
        predecessor_id = _validated_predecessor_decision(
            predecessor_decision,
            decision_type,
        )
        decision_sequence = predecessor_decision.decision_sequence + 1
        if (
            predecessor_decision._evaluator is not evaluator
            or predecessor_decision.envelope.run_id != request.envelope.run_id
            or predecessor_decision.envelope.attempt_id
            != request.envelope.attempt_id
            or predecessor_decision.envelope.repository_revision
            != request.envelope.repository_revision
            or predecessor_decision.envelope.policy_version
            != request.envelope.policy_version
            or predecessor_decision.envelope.environment_profile_id
            != request.envelope.environment_profile_id
        ):
            raise _fail(
                AdmissionFailureCode.PREDECESSOR_MISMATCH,
                "gate predecessor is not the exact contiguous authority state",
            )
    if type(request) is IngestionGateRequest:
        expected_predecessor_ref = (
            None
            if predecessor_decision is None
            else _artifact_ref(
                predecessor_decision.envelope,
                _decision_payload(predecessor_decision),
            )
        )
        if request.predecessor_decision_ref != expected_predecessor_ref:
            raise _fail(
                AdmissionFailureCode.PREDECESSOR_MISMATCH,
                "ingestion request does not bind its exact predecessor decision",
            )
    fields = {
        "schema_version": schema,
        "request_ref": request_ref,
        "decision_kind": decision_kind,
        "reasons": reason_items,
        "checked_dimensions": dimensions,
        "evaluator_declaration": declaration,
        "base_configuration_id": base.configuration_id,
        "knowledge_admission_configuration_id": overlay.configuration_id,
        "independence_proof": proof,
        "evaluated_at_utc": evaluated,
        "valid_until_utc": expiry,
        "predecessor_decision_id": predecessor_id,
        "decision_sequence": decision_sequence,
        "diagnostics": _diagnostics(observation.diagnostics),
    }
    candidate = object.__new__(decision_type)
    for name, item in fields.items():
        object.__setattr__(candidate, name, item)
    payload = _decision_payload(candidate)
    payload_bytes = _canonical(payload)
    decision_id = compute_authority_decision_id(
        canonical_bytes=payload_bytes,
        independence_proof=proof,
    )
    lineage = (
        LineageParentRef(
            parent_record_id=request.request_id,
            edge_kind=LineageEdgeKind.DERIVED_FROM,
        ),
        *(
            ()
            if predecessor_id is None
            else (
                LineageParentRef(
                    parent_record_id=predecessor_id.record_id,
                    edge_kind=LineageEdgeKind.SUPERSEDES,
                ),
            )
        ),
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.AUTHORITY_DECISION,
        canonical_payload_bytes=payload_bytes,
        run_id=request.envelope.run_id,
        attempt_id=request.envelope.attempt_id,
        created_at_utc=evaluated,
        producer_component=declaration.evaluator_component_identity.value,
        repository_revision=request.envelope.repository_revision,
        policy_version=request.envelope.policy_version,
        environment_profile_id=request.envelope.environment_profile_id,
        lineage_parent_ids=lineage,
    )
    object.__setattr__(candidate, "envelope", envelope)
    object.__setattr__(candidate, "decision_id", decision_id)
    object.__setattr__(candidate, "_request", request)
    object.__setattr__(candidate, "_evaluator", evaluator)
    object.__setattr__(
        candidate,
        "_predecessor_decision",
        predecessor_decision,
    )
    object.__setattr__(candidate, "_consumer_validator", _validate_gate_decision)
    object.__setattr__(candidate, "_trusted_seal", _DECISION_SEAL)
    _validate_gate_decision(candidate)
    return candidate


def _validated_predecessor_decision(value: object, decision_type: type) -> AuthorityDecisionId:
    if type(value) is not decision_type:
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate predecessor belongs to another gate",
        )
    _validate_gate_decision(value)
    return value.decision_id


def evaluate_ingestion_gate(
    *,
    evaluator: ConfiguredIngestionGateEvaluator,
    request: IngestionGateRequest,
    predecessor_decision: IngestionGateDecision | None = None,
) -> IngestionGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredIngestionGateEvaluator,
        request=request,
        request_type=IngestionGateRequest,
        decision_type=IngestionGateDecision,
        predecessor_decision=predecessor_decision,
    )


def evaluate_publication_gate(
    *,
    evaluator: ConfiguredPublicationGateEvaluator,
    request: PublicationGateRequest,
    predecessor_decision: PublicationGateDecision | None = None,
) -> PublicationGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredPublicationGateEvaluator,
        request=request,
        request_type=PublicationGateRequest,
        decision_type=PublicationGateDecision,
        predecessor_decision=predecessor_decision,
    )


def evaluate_retrieval_gate(
    *,
    evaluator: ConfiguredRetrievalGateEvaluator,
    request: RetrievalGateRequest,
    predecessor_decision: RetrievalGateDecision | None = None,
) -> RetrievalGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredRetrievalGateEvaluator,
        request=request,
        request_type=RetrievalGateRequest,
        decision_type=RetrievalGateDecision,
        predecessor_decision=predecessor_decision,
    )


def evaluate_consumption_gate(
    *,
    evaluator: ConfiguredConsumptionGateEvaluator,
    request: ConsumptionGateRequest,
    predecessor_decision: ConsumptionDecision | None = None,
) -> ConsumptionDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredConsumptionGateEvaluator,
        request=request,
        request_type=ConsumptionGateRequest,
        decision_type=ConsumptionDecision,
        predecessor_decision=predecessor_decision,
    )


def _validate_gate_decision(
    value: object,
    *,
    _seen: set[int] | None = None,
) -> None:
    seen = set() if _seen is None else _seen
    if id(value) in seen:
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate decision predecessor graph is circular",
        )
    seen.add(id(value))
    spec = _DECISION_SPEC.get(type(value))
    graph_spec = _DECISION_GRAPH_SPEC.get(type(value))
    if (
        spec is None
        or graph_spec is None
        or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL
        or getattr(value, "_consumer_validator", None) is not _validate_gate_decision
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "gate decision is not evaluator sealed",
        )
    schema, role, reason_type, required = spec
    request_type, evaluator_type = graph_spec
    if (
        type(getattr(value, "_request", None)) is not request_type
        or type(getattr(value, "_evaluator", None)) is not evaluator_type
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "gate decision authority graph changed",
        )
    _validate_gate_request(value._request)
    authority_binding, declaration = _require_configured_gate_evaluator(
        value._evaluator,
        evaluator_type=evaluator_type,
    )
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if value.schema_version != schema or type(value.schema_version) is not str:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "gate decision schema is unknown",
        )
    reasons = _ordered_reasons(value.reasons, reason_type=reason_type)
    dimensions = _checked_dimensions(value.checked_dimensions, required=required)
    allowed_evidence_refs = set(_request_evidence_refs(value._request))
    if any(
        evidence_ref not in allowed_evidence_refs
        for dimension in dimensions
        for evidence_ref in dimension.evidence_refs
    ):
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "gate decision cites evidence outside its exact request",
        )
    expected_kind = _derive_decision_kind(
        reason_type=reason_type,
        reasons=reasons,
        checked_dimensions=dimensions,
    )
    if value.decision_kind is not expected_kind:
        raise _fail(
            AdmissionFailureCode.REASON_OUTCOME_MISMATCH,
            "gate decision kind differs from precedence",
        )
    if (
        value.evaluator_declaration is not declaration
        or value.base_configuration_id != base.configuration_id
        or value.knowledge_admission_configuration_id
        != overlay.configuration_id
    ):
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "gate decision configuration changed",
        )
    validate_knowledge_admission_evaluator_declaration(
        value.evaluator_declaration,
        expected_base_authority_handle=authority_binding.base_authority_handle,
        expected_role=role,
    )
    validate_independence_proof(value.independence_proof)
    if value.independence_proof.authority_identity != value.evaluator_declaration.evaluator_identity:
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "gate decision authority differs from its evaluator",
        )
    if (
        value.independence_proof.authority_role is not role
        or value.independence_proof.reason_code
        is not value.evaluator_declaration.independence_reason
    ):
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "gate decision proof role or reason changed",
        )
    payload = _decision_payload(value)
    payload_bytes = _canonical(payload)
    request_payload = _REQUEST_SPEC[request_type][2](value._request)
    expected_request_ref = _artifact_ref(
        value._request.envelope,
        request_payload,
    )
    expected_proposal_id = compute_proposal_id(
        canonical_bytes=_canonical(request_payload)
    )
    if (
        value.request_ref != expected_request_ref
        or value.independence_proof.subject_proposal_id
        != expected_proposal_id
    ):
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "gate decision request binding changed",
        )
    validate_common_envelope(value.envelope, canonical_payload_bytes=payload_bytes)
    if (
        value.envelope.record_id.domain is not IdentityDomain.AUTHORITY_DECISION
        or value.envelope.created_at_utc != value.evaluated_at_utc
        or value.envelope.run_id != value._request.envelope.run_id
        or value.envelope.attempt_id != value._request.envelope.attempt_id
        or value.envelope.repository_revision
        != value._request.envelope.repository_revision
        or value.envelope.policy_version
        != value._request.envelope.policy_version
        or value.envelope.environment_profile_id
        != value._request.envelope.environment_profile_id
        or value.envelope.producer_component
        != declaration.evaluator_component_identity.value
    ):
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "gate decision envelope context changed",
        )
    expected_id = compute_authority_decision_id(
        canonical_bytes=payload_bytes,
        independence_proof=value.independence_proof,
    )
    if value.decision_id != expected_id:
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "gate authority decision identity changed",
        )
    if (
        _timestamp(value.evaluated_at_utc, "evaluated_at_utc")
        < value._request.observed_at_utc
        or value.evaluated_at_utc >= value._request.valid_until_utc
        or _timestamp(value.valid_until_utc, "valid_until_utc")
        <= value.evaluated_at_utc
        or value.valid_until_utc > value._request.valid_until_utc
    ):
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "gate decision validity interval changed",
        )
    predecessor = getattr(value, "_predecessor_decision", None)
    if predecessor is value:
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate decision predecessor is circular",
        )
    if predecessor is None:
        valid_predecessor = (
            value.predecessor_decision_id is None
            and value.decision_sequence == 1
        )
    else:
        valid_predecessor = (
            type(predecessor) is type(value)
            and getattr(predecessor, "_evaluator", None) is value._evaluator
        )
        if valid_predecessor:
            _validate_gate_decision(predecessor, _seen=seen)
            valid_predecessor = (
                value.predecessor_decision_id == predecessor.decision_id
                and value.decision_sequence == predecessor.decision_sequence + 1
                and predecessor.envelope.run_id == value.envelope.run_id
                and predecessor.envelope.attempt_id == value.envelope.attempt_id
                and predecessor.envelope.repository_revision
                == value.envelope.repository_revision
                and predecessor.envelope.policy_version
                == value.envelope.policy_version
                and predecessor.envelope.environment_profile_id
                == value.envelope.environment_profile_id
            )
    expected_lineage = (
        LineageParentRef(
            parent_record_id=value._request.request_id,
            edge_kind=LineageEdgeKind.DERIVED_FROM,
        ),
        *(
            ()
            if predecessor is None
            else (
                LineageParentRef(
                    parent_record_id=predecessor.decision_id.record_id,
                    edge_kind=LineageEdgeKind.SUPERSEDES,
                ),
            )
        ),
    )
    if (
        not valid_predecessor
        or value.envelope.lineage_parent_ids != expected_lineage
    ):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate decision predecessor or lineage changed",
        )


def validate_ingestion_gate_decision(value: IngestionGateDecision) -> None:
    if type(value) is not IngestionGateDecision:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "ingestion decision type differs")
    _validate_gate_decision(value)


def validate_publication_gate_decision(value: PublicationGateDecision) -> None:
    if type(value) is not PublicationGateDecision:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "publication decision type differs")
    _validate_gate_decision(value)


def validate_retrieval_gate_decision(value: RetrievalGateDecision) -> None:
    if type(value) is not RetrievalGateDecision:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "retrieval decision type differs")
    _validate_gate_decision(value)


def validate_consumption_decision(value: ConsumptionDecision) -> None:
    if type(value) is not ConsumptionDecision:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "consumption decision type differs")
    _validate_gate_decision(value)
