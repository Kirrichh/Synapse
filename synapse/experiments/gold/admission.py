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
    _decision_payload,
    _derive_decision_kind,
    _diagnostics,
    _fail,
    _ordered_reasons,
    _timestamp,
)

class ConfiguredIngestionGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredIngestionGateEvaluator:
        raise TypeError("ConfiguredIngestionGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredPublicationGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredPublicationGateEvaluator:
        raise TypeError("ConfiguredPublicationGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredRetrievalGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredRetrievalGateEvaluator:
        raise TypeError("ConfiguredRetrievalGateEvaluator is factory-created")


@dataclass(frozen=True, init=False)
class ConfiguredConsumptionGateEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    declaration: KnowledgeAdmissionEvaluatorDeclaration
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredConsumptionGateEvaluator:
        raise TypeError("ConfiguredConsumptionGateEvaluator is factory-created")


_EVALUATOR_SPEC = {
    ConfiguredIngestionGateEvaluator: AuthorityRole.INGESTION_GATE_EVALUATOR,
    ConfiguredPublicationGateEvaluator: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
    ConfiguredRetrievalGateEvaluator: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
    ConfiguredConsumptionGateEvaluator: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
}


def _configure_gate_evaluator(
    evaluator_type: type,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
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
    result = object.__new__(evaluator_type)
    object.__setattr__(result, "authority_binding", authority_binding)
    object.__setattr__(result, "declaration", declaration)
    object.__setattr__(result, "_trusted_seal", _EVALUATOR_SEAL)
    _require_configured_gate_evaluator(result, evaluator_type=evaluator_type)
    return result


def configure_ingestion_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> ConfiguredIngestionGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredIngestionGateEvaluator,
        authority_binding=authority_binding,
    )


def configure_publication_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> ConfiguredPublicationGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredPublicationGateEvaluator,
        authority_binding=authority_binding,
    )


def configure_retrieval_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> ConfiguredRetrievalGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredRetrievalGateEvaluator,
        authority_binding=authority_binding,
    )


def configure_consumption_gate_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> ConfiguredConsumptionGateEvaluator:
    return _configure_gate_evaluator(
        ConfiguredConsumptionGateEvaluator,
        authority_binding=authority_binding,
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
    if value.declaration is not expected:
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
    return value.authority_binding, value.declaration


def _evaluate_gate(
    *,
    evaluator: object,
    evaluator_type: type,
    request: object,
    request_type: type,
    decision_type: type,
    reasons: tuple[Enum, ...],
    checked_dimensions: tuple[GateCheckedDimension, ...],
    producer_actor_ids: tuple[ActorIdentity, ...],
    source_actor_ids: tuple[ActorIdentity, ...],
    proposer_identity: ActorIdentity,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
    evaluated_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_decision: object | None,
    decision_sequence: int,
    diagnostics: tuple[tuple[str, str | int | bool | None], ...],
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
    if evaluated_at_utc < request.observed_at_utc or evaluated_at_utc >= request.valid_until_utc:
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "gate evaluation is outside its request validity interval",
        )
    schema, role, reason_type, required = _DECISION_SPEC[decision_type]
    reason_items = _ordered_reasons(reasons, reason_type=reason_type)
    dimensions = _checked_dimensions(checked_dimensions, required=required)
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
        producer_actor_ids=producer_actor_ids,
        source_actor_ids=source_actor_ids,
        proposer_identity=proposer_identity,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
        delegation_chain=(),
    )
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    predecessor_id = (
        None
        if predecessor_decision is None
        else _validated_predecessor_decision(predecessor_decision, decision_type)
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
        "evaluated_at_utc": evaluated_at_utc,
        "valid_until_utc": valid_until_utc,
        "predecessor_decision_id": predecessor_id,
        "decision_sequence": decision_sequence,
        "diagnostics": _diagnostics(diagnostics),
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
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.AUTHORITY_DECISION,
        canonical_payload_bytes=payload_bytes,
        run_id=request.envelope.run_id,
        attempt_id=request.envelope.attempt_id,
        created_at_utc=evaluated_at_utc,
        producer_component=declaration.evaluator_component_identity.value,
        repository_revision=request.envelope.repository_revision,
        policy_version=request.envelope.policy_version,
        environment_profile_id=request.envelope.environment_profile_id,
        lineage_parent_ids=lineage,
    )
    object.__setattr__(candidate, "envelope", envelope)
    object.__setattr__(candidate, "decision_id", decision_id)
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
    **kwargs: object,
) -> IngestionGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredIngestionGateEvaluator,
        request=request,
        request_type=IngestionGateRequest,
        decision_type=IngestionGateDecision,
        **kwargs,
    )


def evaluate_publication_gate(
    *,
    evaluator: ConfiguredPublicationGateEvaluator,
    request: PublicationGateRequest,
    **kwargs: object,
) -> PublicationGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredPublicationGateEvaluator,
        request=request,
        request_type=PublicationGateRequest,
        decision_type=PublicationGateDecision,
        **kwargs,
    )


def evaluate_retrieval_gate(
    *,
    evaluator: ConfiguredRetrievalGateEvaluator,
    request: RetrievalGateRequest,
    **kwargs: object,
) -> RetrievalGateDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredRetrievalGateEvaluator,
        request=request,
        request_type=RetrievalGateRequest,
        decision_type=RetrievalGateDecision,
        **kwargs,
    )


def evaluate_consumption_gate(
    *,
    evaluator: ConfiguredConsumptionGateEvaluator,
    request: ConsumptionGateRequest,
    **kwargs: object,
) -> ConsumptionDecision:
    return _evaluate_gate(
        evaluator=evaluator,
        evaluator_type=ConfiguredConsumptionGateEvaluator,
        request=request,
        request_type=ConsumptionGateRequest,
        decision_type=ConsumptionDecision,
        **kwargs,
    )


def _validate_gate_decision(value: object) -> None:
    spec = _DECISION_SPEC.get(type(value))
    if (
        spec is None
        or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "gate decision is not evaluator sealed",
        )
    schema, role, reason_type, required = spec
    if value.schema_version != schema or type(value.schema_version) is not str:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "gate decision schema is unknown",
        )
    reasons = _ordered_reasons(value.reasons, reason_type=reason_type)
    dimensions = _checked_dimensions(value.checked_dimensions, required=required)
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
    validate_knowledge_admission_evaluator_declaration(
        value.evaluator_declaration,
        expected_role=role,
    )
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
    validate_common_envelope(value.envelope, canonical_payload_bytes=payload_bytes)
    if (
        value.envelope.record_id.domain is not IdentityDomain.AUTHORITY_DECISION
        or value.envelope.created_at_utc != value.evaluated_at_utc
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
    if value.valid_until_utc <= value.evaluated_at_utc:
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "gate decision validity interval changed",
        )
    if (value.predecessor_decision_id is None) != (value.decision_sequence == 1):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate decision sequence changed",
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
