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


class AdmissionFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_REASON = "UNKNOWN_REASON"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    MALFORMED_DECISION = "MALFORMED_DECISION"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    DIMENSION_MISSING = "DIMENSION_MISSING"
    DIMENSION_DUPLICATE = "DIMENSION_DUPLICATE"
    REASON_OUTCOME_MISMATCH = "REASON_OUTCOME_MISMATCH"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DECISION_EXPIRED = "DECISION_EXPIRED"
    PREDECESSOR_MISMATCH = "PREDECESSOR_MISMATCH"
    HISTORY_FORK = "HISTORY_FORK"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"
    FAST_FORWARD_DETECTED = "FAST_FORWARD_DETECTED"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    LOCK_BUSY = "LOCK_BUSY"
    DECISION_NOT_DURABLE = "DECISION_NOT_DURABLE"
    BOUNDARY_INVALID = "BOUNDARY_INVALID"
    HANDLE_FORBIDDEN = "HANDLE_FORBIDDEN"
    RESOLVER_MISMATCH = "RESOLVER_MISMATCH"


class AdmissionViolation(RuntimeError):
    def __init__(self, failure_code: AdmissionFailureCode, detail: str) -> None:
        if type(failure_code) is not AdmissionFailureCode:
            raise TypeError("failure_code must be an exact AdmissionFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be safe non-empty text")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: AdmissionFailureCode, detail: str) -> AdmissionViolation:
    return AdmissionViolation(code, detail)


class GateDecisionKind(str, Enum):
    ADMIT = "ADMIT"
    REJECT = "REJECT"
    QUARANTINE = "QUARANTINE"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"


class GateDimensionResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"


class IngestionGateReason(str, Enum):
    SOURCE_ACCEPTED = "SOURCE_ACCEPTED"
    PROVENANCE_COMPLETE = "PROVENANCE_COMPLETE"
    SOURCE_POLICY_REJECTED = "SOURCE_POLICY_REJECTED"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"
    SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"
    SECRET_LIKE_DATA = "SECRET_LIKE_DATA"
    INSTRUCTION_LIKE_TEXT_ISOLATION_REQUIRED = (
        "INSTRUCTION_LIKE_TEXT_ISOLATION_REQUIRED"
    )
    EXECUTABLE_CONTENT_RESTRICTED = "EXECUTABLE_CONTENT_RESTRICTED"
    TAINT_REQUIRES_QUARANTINE = "TAINT_REQUIRES_QUARANTINE"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class PublicationGateReason(str, Enum):
    VERIFIED_OBJECT_ACCEPTED = "VERIFIED_OBJECT_ACCEPTED"
    ATTESTATION_ACCEPTED = "ATTESTATION_ACCEPTED"
    LIFECYCLE_ELIGIBLE = "LIFECYCLE_ELIGIBLE"
    TAINT_ACCEPTED = "TAINT_ACCEPTED"
    BINDINGS_RESOLVED = "BINDINGS_RESOLVED"
    POLICY_ACCEPTED = "POLICY_ACCEPTED"
    OBJECT_UNVERIFIED = "OBJECT_UNVERIFIED"
    ATTESTATION_INVALID = "ATTESTATION_INVALID"
    LIFECYCLE_INELIGIBLE = "LIFECYCLE_INELIGIBLE"
    TAINT_BLOCKED = "TAINT_BLOCKED"
    BINDING_INVALID = "BINDING_INVALID"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    SECRET_LIKE_DATA = "SECRET_LIKE_DATA"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class RetrievalGateReason(str, Enum):
    BOUNDARY_COMMITTED = "BOUNDARY_COMMITTED"
    SUBJECT_IN_EXECUTABLE_VIEW = "SUBJECT_IN_EXECUTABLE_VIEW"
    CONTEXT_BOUND = "CONTEXT_BOUND"
    COMPATIBILITY_ACCEPTED = "COMPATIBILITY_ACCEPTED"
    LIFECYCLE_ELIGIBLE = "LIFECYCLE_ELIGIBLE"
    TAINT_ACCEPTED = "TAINT_ACCEPTED"
    CONFLICTS_RESOLVED = "CONFLICTS_RESOLVED"
    BOUNDARY_INVALID = "BOUNDARY_INVALID"
    SUBJECT_OUTSIDE_SNAPSHOT = "SUBJECT_OUTSIDE_SNAPSHOT"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    COMPATIBILITY_REJECTED = "COMPATIBILITY_REJECTED"
    LIFECYCLE_INELIGIBLE = "LIFECYCLE_INELIGIBLE"
    TAINT_BLOCKED = "TAINT_BLOCKED"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class ConsumptionGateReason(str, Enum):
    BOUNDARY_REVALIDATED = "BOUNDARY_REVALIDATED"
    SUBJECT_IDENTITY_REVALIDATED = "SUBJECT_IDENTITY_REVALIDATED"
    RETRIEVAL_ADMISSION_REVALIDATED = "RETRIEVAL_ADMISSION_REVALIDATED"
    LOADING_REVALIDATION_ACCEPTED = "LOADING_REVALIDATION_ACCEPTED"
    CONSUMPTION_COMPATIBILITY_ACCEPTED = (
        "CONSUMPTION_COMPATIBILITY_ACCEPTED"
    )
    LIFECYCLE_REVALIDATED = "LIFECYCLE_REVALIDATED"
    TAINT_CHAIN_REVALIDATED = "TAINT_CHAIN_REVALIDATED"
    BOUNDARY_STALE = "BOUNDARY_STALE"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    RETRIEVAL_ADMISSION_MISSING = "RETRIEVAL_ADMISSION_MISSING"
    LOADING_REVALIDATION_MISSING = "LOADING_REVALIDATION_MISSING"
    COMPATIBILITY_STALE = "COMPATIBILITY_STALE"
    LIFECYCLE_STALE = "LIFECYCLE_STALE"
    TAINT_CHAIN_INVALID = "TAINT_CHAIN_INVALID"
    SECRET_LIKE_DATA = "SECRET_LIKE_DATA"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


INGESTION_REQUIRED_DIMENSIONS = (
    "initial_taint",
    "provenance",
    "schema",
    "source_classification",
    "source_identity",
)
PUBLICATION_REQUIRED_DIMENSIONS = (
    "attestation",
    "bindings",
    "conflicts",
    "lifecycle",
    "object_identity",
    "policy",
    "provenance",
    "scope",
    "taint",
)
RETRIEVAL_REQUIRED_DIMENSIONS = (
    "boundary",
    "compatibility",
    "conflicts",
    "consumer_context",
    "executable_view",
    "lifecycle",
    "taint",
)
CONSUMPTION_REQUIRED_DIMENSIONS = (
    "boundary",
    "compatibility",
    "lifecycle",
    "loading_revalidation",
    "retrieval_admission",
    "subject_identity",
    "taint_chain",
)

_REASON_RULES = {
    IngestionGateReason: {
        GateDecisionKind.ADMIT: {
            IngestionGateReason.SOURCE_ACCEPTED,
            IngestionGateReason.PROVENANCE_COMPLETE,
        },
        GateDecisionKind.QUARANTINE: {
            IngestionGateReason.SECRET_LIKE_DATA,
            IngestionGateReason.TAINT_REQUIRES_QUARANTINE,
        },
        GateDecisionKind.REJECT: {
            IngestionGateReason.SOURCE_POLICY_REJECTED,
            IngestionGateReason.SOURCE_UNAVAILABLE,
            IngestionGateReason.PROVENANCE_INCOMPLETE,
            IngestionGateReason.SCHEMA_UNSUPPORTED,
            IngestionGateReason.EXECUTABLE_CONTENT_RESTRICTED,
            IngestionGateReason.DEPENDENCY_UNAVAILABLE,
        },
        GateDecisionKind.REQUIRE_REVIEW: {
            IngestionGateReason.INSTRUCTION_LIKE_TEXT_ISOLATION_REQUIRED,
            IngestionGateReason.HUMAN_REVIEW_REQUIRED,
        },
    },
    PublicationGateReason: {
        GateDecisionKind.ADMIT: {
            PublicationGateReason.VERIFIED_OBJECT_ACCEPTED,
            PublicationGateReason.ATTESTATION_ACCEPTED,
            PublicationGateReason.LIFECYCLE_ELIGIBLE,
            PublicationGateReason.TAINT_ACCEPTED,
            PublicationGateReason.BINDINGS_RESOLVED,
            PublicationGateReason.POLICY_ACCEPTED,
        },
        GateDecisionKind.QUARANTINE: {
            PublicationGateReason.TAINT_BLOCKED,
            PublicationGateReason.SECRET_LIKE_DATA,
        },
        GateDecisionKind.REJECT: {
            PublicationGateReason.OBJECT_UNVERIFIED,
            PublicationGateReason.ATTESTATION_INVALID,
            PublicationGateReason.LIFECYCLE_INELIGIBLE,
            PublicationGateReason.BINDING_INVALID,
            PublicationGateReason.POLICY_MISMATCH,
            PublicationGateReason.SCOPE_EXPANSION,
            PublicationGateReason.CONFLICT_UNRESOLVED,
            PublicationGateReason.DEPENDENCY_UNAVAILABLE,
        },
        GateDecisionKind.REQUIRE_REVIEW: {
            PublicationGateReason.HUMAN_REVIEW_REQUIRED,
        },
    },
    RetrievalGateReason: {
        GateDecisionKind.ADMIT: {
            RetrievalGateReason.BOUNDARY_COMMITTED,
            RetrievalGateReason.SUBJECT_IN_EXECUTABLE_VIEW,
            RetrievalGateReason.CONTEXT_BOUND,
            RetrievalGateReason.COMPATIBILITY_ACCEPTED,
            RetrievalGateReason.LIFECYCLE_ELIGIBLE,
            RetrievalGateReason.TAINT_ACCEPTED,
            RetrievalGateReason.CONFLICTS_RESOLVED,
        },
        GateDecisionKind.QUARANTINE: {
            RetrievalGateReason.TAINT_BLOCKED,
        },
        GateDecisionKind.REJECT: {
            RetrievalGateReason.BOUNDARY_INVALID,
            RetrievalGateReason.SUBJECT_OUTSIDE_SNAPSHOT,
            RetrievalGateReason.CONTEXT_MISMATCH,
            RetrievalGateReason.COMPATIBILITY_REJECTED,
            RetrievalGateReason.LIFECYCLE_INELIGIBLE,
            RetrievalGateReason.CONFLICT_UNRESOLVED,
            RetrievalGateReason.DEPENDENCY_UNAVAILABLE,
        },
        GateDecisionKind.REQUIRE_REVIEW: {
            RetrievalGateReason.HUMAN_REVIEW_REQUIRED,
        },
    },
    ConsumptionGateReason: {
        GateDecisionKind.ADMIT: {
            ConsumptionGateReason.BOUNDARY_REVALIDATED,
            ConsumptionGateReason.SUBJECT_IDENTITY_REVALIDATED,
            ConsumptionGateReason.RETRIEVAL_ADMISSION_REVALIDATED,
            ConsumptionGateReason.LOADING_REVALIDATION_ACCEPTED,
            ConsumptionGateReason.CONSUMPTION_COMPATIBILITY_ACCEPTED,
            ConsumptionGateReason.LIFECYCLE_REVALIDATED,
            ConsumptionGateReason.TAINT_CHAIN_REVALIDATED,
        },
        GateDecisionKind.QUARANTINE: {
            ConsumptionGateReason.TAINT_CHAIN_INVALID,
            ConsumptionGateReason.SECRET_LIKE_DATA,
        },
        GateDecisionKind.REJECT: {
            ConsumptionGateReason.BOUNDARY_STALE,
            ConsumptionGateReason.SUBJECT_MISMATCH,
            ConsumptionGateReason.RETRIEVAL_ADMISSION_MISSING,
            ConsumptionGateReason.LOADING_REVALIDATION_MISSING,
            ConsumptionGateReason.COMPATIBILITY_STALE,
            ConsumptionGateReason.LIFECYCLE_STALE,
            ConsumptionGateReason.DEPENDENCY_UNAVAILABLE,
        },
        GateDecisionKind.REQUIRE_REVIEW: {
            ConsumptionGateReason.HUMAN_REVIEW_REQUIRED,
        },
    },
}


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _safe_text(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_TEXT_RE.fullmatch(value) is None:
        raise _fail(AdmissionFailureCode.MALFORMED_REQUEST, f"{name} is invalid")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            f"{name} must be exact timezone.utc",
        )
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _record(value: object, domain: IdentityDomain, name: str) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact RecordId",
        )
    value.to_dict()
    if value.domain is not domain:
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            f"{name} uses the wrong identity domain",
        )
    return value


def _ref(value: object, name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef or value.kind is not RefKind.ARTIFACT:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact artifact reference",
        )
    return HashBoundRef.from_dict(value.to_dict())


def _refs(value: object, name: str, *, non_empty: bool) -> tuple[HashBoundRef, ...]:
    if type(value) is not tuple:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact tuple",
        )
    result = tuple(_ref(item, name) for item in value)
    if non_empty and not result:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            f"{name} is required",
        )
    if (
        result != tuple(sorted(result, key=lambda item: _canonical(item.to_dict())))
        or len({item.sha256 for item in result}) != len(result)
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            f"{name} must be distinct and ordered",
        )
    return result


def _ordered_texts(
    value: object,
    name: str,
    *,
    non_empty: bool,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact tuple",
        )
    result = tuple(_safe_text(item, name) for item in value)
    if (
        (non_empty and not result)
        or result != tuple(sorted(result))
        or len(set(result)) != len(result)
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            f"{name} must be distinct and ordered",
        )
    return result


@dataclass(frozen=True)
class GateConsumerContext:
    schema_version: str
    scope: tuple[str, ...]
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    oracle_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != GATE_CONSUMER_CONTEXT_SCHEMA_V1:
            raise _fail(
                AdmissionFailureCode.UNKNOWN_SCHEMA,
                "gate consumer context schema is unknown",
            )
        _ordered_texts(self.scope, "scope", non_empty=True)
        _ordered_texts(self.capabilities, "capabilities", non_empty=False)
        _ordered_texts(self.tools, "tools", non_empty=False)
        _ordered_texts(
            self.oracle_identities,
            "oracle_identities",
            non_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "scope": list(self.scope),
            "capabilities": list(self.capabilities),
            "tools": list(self.tools),
            "oracle_identities": list(self.oracle_identities),
        }


@dataclass(frozen=True)
class GateAuthorityHeads:
    schema_version: str
    lifecycle: HistoryAnchor
    provenance: HistoryAnchor
    taint: HistoryAnchor
    admission: HistoryAnchor
    compatibility: HistoryAnchor

    def to_dict(self) -> dict[str, object]:
        if self.schema_version != GATE_AUTHORITY_HEADS_SCHEMA_V1:
            raise _fail(
                AdmissionFailureCode.UNKNOWN_SCHEMA,
                "gate authority-head schema is unknown",
            )
        for anchor in (
            self.lifecycle,
            self.provenance,
            self.taint,
            self.admission,
            self.compatibility,
        ):
            validate_history_anchor(anchor)
        return {
            "schema_version": self.schema_version,
            "lifecycle": self.lifecycle.to_dict(),
            "provenance": self.provenance.to_dict(),
            "taint": self.taint.to_dict(),
            "admission": self.admission.to_dict(),
            "compatibility": self.compatibility.to_dict(),
        }


def validate_gate_authority_heads(
    value: GateAuthorityHeads,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding | None = None,
) -> None:
    if type(value) is not GateAuthorityHeads:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate authority heads must be exact",
        )
    value.to_dict()
    if authority_binding is None:
        return
    base, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    expected = (
        (value.lifecycle, HistoryDomain.LIFECYCLE, base.configuration_id),
        (value.provenance, HistoryDomain.PROVENANCE, base.configuration_id),
        (value.taint, HistoryDomain.TAINT, base.configuration_id),
        (value.admission, HistoryDomain.ADMISSION, overlay.configuration_id),
        (
            value.compatibility,
            HistoryDomain.COMPATIBILITY,
            overlay.configuration_id,
        ),
    )
    if any(
        anchor.history_domain is not domain
        or anchor.configuration_id != configuration_id
        for anchor, domain, configuration_id in expected
    ):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "gate authority heads mix configuration domains",
        )


@dataclass(frozen=True)
class GateCheckedDimension:
    schema_version: str
    dimension: str
    result: GateDimensionResult
    evidence_refs: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        if self.schema_version != GATE_CHECKED_DIMENSION_SCHEMA_V1:
            raise _fail(
                AdmissionFailureCode.UNKNOWN_SCHEMA,
                "gate checked-dimension schema is unknown",
            )
        _safe_text(self.dimension, "dimension")
        if type(self.result) is not GateDimensionResult:
            raise _fail(
                AdmissionFailureCode.MALFORMED_DECISION,
                "gate dimension result is unknown",
            )
        _refs(
            self.evidence_refs,
            "dimension.evidence_refs",
            non_empty=True,
        )

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "dimension": self.dimension,
            "result": self.result.value,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
        }


def _checked_dimensions(
    value: object,
    *,
    required: tuple[str, ...],
) -> tuple[GateCheckedDimension, ...]:
    if type(value) is not tuple or any(type(item) is not GateCheckedDimension for item in value):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "checked dimensions must contain exact GateCheckedDimension values",
        )
    for item in value:
        item.__post_init__()
    names = tuple(item.dimension for item in value)
    if len(set(names)) != len(names):
        raise _fail(
            AdmissionFailureCode.DIMENSION_DUPLICATE,
            "checked dimensions contain duplicates",
        )
    if names != required:
        raise _fail(
            AdmissionFailureCode.DIMENSION_MISSING,
            "checked dimensions differ from the declared gate registry",
        )
    return value


def _artifact_bytes(envelope: CommonEnvelope, payload: dict[str, object]) -> bytes:
    canonical_payload = _canonical(payload)
    validate_common_envelope(
        envelope,
        canonical_payload_bytes=canonical_payload,
    )
    return _canonical({"envelope": envelope.to_dict(), "payload": payload})


def _artifact_ref(envelope: CommonEnvelope, payload: dict[str, object]) -> HashBoundRef:
    raw = _artifact_bytes(envelope, payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    )


def _request_common_payload(
    *,
    schema_version: str,
    subject_ref: HashBoundRef,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> dict[str, object]:
    _ref(subject_ref, "subject_ref")
    consumer_context.__post_init__()
    validate_gate_authority_heads(authority_heads)
    scopes = _ordered_texts(scope_ids, "scope_ids", non_empty=True)
    capabilities = _ordered_texts(
        capability_ids,
        "capability_ids",
        non_empty=True,
    )
    tools = _ordered_texts(tool_ids, "tool_ids", non_empty=True)
    oracles = _ordered_texts(oracle_ids, "oracle_ids", non_empty=True)
    observed = _timestamp(observed_at_utc, "observed_at_utc")
    expiry = _timestamp(valid_until_utc, "valid_until_utc")
    if expiry <= observed:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "gate request expiry must follow observation",
        )
    if type(predecessor_sequence) is not int or predecessor_sequence < 0:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "gate predecessor sequence is invalid",
        )
    return {
        "schema_version": schema_version,
        "subject_ref": subject_ref.to_dict(),
        "consumer_context": consumer_context.to_dict(),
        "authority_heads": authority_heads.to_dict(),
        "scope_ids": list(scopes),
        "capability_ids": list(capabilities),
        "tool_ids": list(tools),
        "oracle_ids": list(oracles),
        "observed_at_utc": _timestamp_text(observed),
        "valid_until_utc": _timestamp_text(expiry),
        "predecessor_sequence": predecessor_sequence,
    }


def ingestion_gate_request_payload(
    *,
    source_transaction_id: RecordId,
    source_identity: ActorIdentity,
    subject_ref: HashBoundRef,
    observed_input_refs: tuple[HashBoundRef, ...],
    predecessor_decision_ref: HashBoundRef | None,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> dict[str, object]:
    transaction = _record(
        source_transaction_id,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        "source_transaction_id",
    )
    if type(source_identity) is not ActorIdentity:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "source_identity must be an exact ActorIdentity",
        )
    source_identity.to_dict()
    inputs = _refs(observed_input_refs, "observed_input_refs", non_empty=True)
    predecessor = (
        None
        if predecessor_decision_ref is None
        else _ref(predecessor_decision_ref, "predecessor_decision_ref")
    )
    payload = _request_common_payload(
        schema_version=INGESTION_GATE_REQUEST_SCHEMA_V1,
        subject_ref=subject_ref,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
        scope_ids=scope_ids,
        capability_ids=capability_ids,
        tool_ids=tool_ids,
        oracle_ids=oracle_ids,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until_utc,
        predecessor_sequence=predecessor_sequence,
    )
    payload.update(
        {
            "source_transaction_id": transaction.to_dict(),
            "source_identity": source_identity.to_dict(),
            "observed_input_refs": [item.to_dict() for item in inputs],
            "predecessor_decision_ref": (
                None if predecessor is None else predecessor.to_dict()
            ),
        }
    )
    return payload


def publication_gate_request_payload(
    *,
    publication_transaction_id: RecordId,
    ingested_candidate_ref: HashBoundRef,
    ingestion_decision_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    attestation_ref: HashBoundRef,
    binding_refs: tuple[HashBoundRef, ...],
    target_library_predecessor_root: str,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> dict[str, object]:
    transaction = _record(
        publication_transaction_id,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        "publication_transaction_id",
    )
    payload = _request_common_payload(
        schema_version=PUBLICATION_GATE_REQUEST_SCHEMA_V1,
        subject_ref=subject_ref,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
        scope_ids=scope_ids,
        capability_ids=capability_ids,
        tool_ids=tool_ids,
        oracle_ids=oracle_ids,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until_utc,
        predecessor_sequence=predecessor_sequence,
    )
    payload.update(
        {
            "publication_transaction_id": transaction.to_dict(),
            "ingested_candidate_ref": _ref(
                ingested_candidate_ref,
                "ingested_candidate_ref",
            ).to_dict(),
            "ingestion_decision_ref": _ref(
                ingestion_decision_ref,
                "ingestion_decision_ref",
            ).to_dict(),
            "attestation_ref": _ref(
                attestation_ref,
                "attestation_ref",
            ).to_dict(),
            "binding_refs": [
                item.to_dict()
                for item in _refs(binding_refs, "binding_refs", non_empty=True)
            ],
            "target_library_predecessor_root": _safe_text(
                target_library_predecessor_root,
                "target_library_predecessor_root",
            ),
        }
    )
    return payload


def retrieval_gate_request_payload(
    *,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    candidate_ref: HashBoundRef,
    compatibility_decision_ref: HashBoundRef,
    conflict_decision_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> dict[str, object]:
    snapshot_id = _record(
        repository_knowledge_snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "repository_knowledge_snapshot_id",
    )
    boundary_id = _record(
        atomic_boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "atomic_boundary_id",
    )
    if (
        type(boundary_commit_sequence) is not int
        or boundary_commit_sequence < 1
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "boundary commit sequence is invalid",
        )
    payload = _request_common_payload(
        schema_version=RETRIEVAL_GATE_REQUEST_SCHEMA_V1,
        subject_ref=subject_ref,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
        scope_ids=scope_ids,
        capability_ids=capability_ids,
        tool_ids=tool_ids,
        oracle_ids=oracle_ids,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until_utc,
        predecessor_sequence=predecessor_sequence,
    )
    payload.update(
        {
            "repository_knowledge_snapshot_id": snapshot_id.to_dict(),
            "atomic_boundary_id": boundary_id.to_dict(),
            "boundary_commit_sequence": boundary_commit_sequence,
            "candidate_ref": _ref(candidate_ref, "candidate_ref").to_dict(),
            "compatibility_decision_ref": _ref(
                compatibility_decision_ref,
                "compatibility_decision_ref",
            ).to_dict(),
            "conflict_decision_ref": _ref(
                conflict_decision_ref,
                "conflict_decision_ref",
            ).to_dict(),
        }
    )
    return payload


def consumption_gate_request_payload(
    *,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    retrieval_decision_ref: HashBoundRef,
    load_decision_ref: HashBoundRef,
    compatibility_revalidation_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> dict[str, object]:
    payload = retrieval_gate_request_payload(
        repository_knowledge_snapshot_id=repository_knowledge_snapshot_id,
        atomic_boundary_id=atomic_boundary_id,
        boundary_commit_sequence=boundary_commit_sequence,
        candidate_ref=subject_ref,
        compatibility_decision_ref=compatibility_revalidation_ref,
        conflict_decision_ref=retrieval_decision_ref,
        subject_ref=subject_ref,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
        scope_ids=scope_ids,
        capability_ids=capability_ids,
        tool_ids=tool_ids,
        oracle_ids=oracle_ids,
        observed_at_utc=observed_at_utc,
        valid_until_utc=valid_until_utc,
        predecessor_sequence=predecessor_sequence,
    )
    payload["schema_version"] = CONSUMPTION_GATE_REQUEST_SCHEMA_V1
    del payload["candidate_ref"]
    del payload["compatibility_decision_ref"]
    del payload["conflict_decision_ref"]
    payload.update(
        {
            "retrieval_decision_ref": _ref(
                retrieval_decision_ref,
                "retrieval_decision_ref",
            ).to_dict(),
            "load_decision_ref": _ref(
                load_decision_ref,
                "load_decision_ref",
            ).to_dict(),
            "compatibility_revalidation_ref": _ref(
                compatibility_revalidation_ref,
                "compatibility_revalidation_ref",
            ).to_dict(),
        }
    )
    return payload


@dataclass(frozen=True, init=False)
class IngestionGateRequest:
    schema_version: str
    envelope: CommonEnvelope
    request_id: RecordId
    source_transaction_id: RecordId
    source_identity: ActorIdentity
    subject_ref: HashBoundRef
    observed_input_refs: tuple[HashBoundRef, ...]
    predecessor_decision_ref: HashBoundRef | None
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    scope_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    oracle_ids: tuple[str, ...]
    observed_at_utc: datetime
    valid_until_utc: datetime
    predecessor_sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> IngestionGateRequest:
        raise TypeError("IngestionGateRequest is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_ingestion_gate_request(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _ingestion_request_payload(self),
        }


@dataclass(frozen=True, init=False)
class PublicationGateRequest:
    schema_version: str
    envelope: CommonEnvelope
    request_id: RecordId
    publication_transaction_id: RecordId
    ingested_candidate_ref: HashBoundRef
    ingestion_decision_ref: HashBoundRef
    subject_ref: HashBoundRef
    attestation_ref: HashBoundRef
    binding_refs: tuple[HashBoundRef, ...]
    target_library_predecessor_root: str
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    scope_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    oracle_ids: tuple[str, ...]
    observed_at_utc: datetime
    valid_until_utc: datetime
    predecessor_sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PublicationGateRequest:
        raise TypeError("PublicationGateRequest is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_publication_gate_request(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _publication_request_payload(self),
        }


@dataclass(frozen=True, init=False)
class RetrievalGateRequest:
    schema_version: str
    envelope: CommonEnvelope
    request_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    candidate_ref: HashBoundRef
    compatibility_decision_ref: HashBoundRef
    conflict_decision_ref: HashBoundRef
    subject_ref: HashBoundRef
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    scope_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    oracle_ids: tuple[str, ...]
    observed_at_utc: datetime
    valid_until_utc: datetime
    predecessor_sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalGateRequest:
        raise TypeError("RetrievalGateRequest is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_gate_request(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _retrieval_request_payload(self),
        }


@dataclass(frozen=True, init=False)
class ConsumptionGateRequest:
    schema_version: str
    envelope: CommonEnvelope
    request_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    retrieval_decision_ref: HashBoundRef
    load_decision_ref: HashBoundRef
    compatibility_revalidation_ref: HashBoundRef
    subject_ref: HashBoundRef
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    scope_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    tool_ids: tuple[str, ...]
    oracle_ids: tuple[str, ...]
    observed_at_utc: datetime
    valid_until_utc: datetime
    predecessor_sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConsumptionGateRequest:
        raise TypeError("ConsumptionGateRequest is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_consumption_gate_request(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _consumption_request_payload(self),
        }


def _ingestion_request_payload(value: IngestionGateRequest) -> dict[str, object]:
    return ingestion_gate_request_payload(
        source_transaction_id=value.source_transaction_id,
        source_identity=value.source_identity,
        subject_ref=value.subject_ref,
        observed_input_refs=value.observed_input_refs,
        predecessor_decision_ref=value.predecessor_decision_ref,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
        scope_ids=value.scope_ids,
        capability_ids=value.capability_ids,
        tool_ids=value.tool_ids,
        oracle_ids=value.oracle_ids,
        observed_at_utc=value.observed_at_utc,
        valid_until_utc=value.valid_until_utc,
        predecessor_sequence=value.predecessor_sequence,
    )


def _publication_request_payload(
    value: PublicationGateRequest,
) -> dict[str, object]:
    return publication_gate_request_payload(
        publication_transaction_id=value.publication_transaction_id,
        ingested_candidate_ref=value.ingested_candidate_ref,
        ingestion_decision_ref=value.ingestion_decision_ref,
        subject_ref=value.subject_ref,
        attestation_ref=value.attestation_ref,
        binding_refs=value.binding_refs,
        target_library_predecessor_root=value.target_library_predecessor_root,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
        scope_ids=value.scope_ids,
        capability_ids=value.capability_ids,
        tool_ids=value.tool_ids,
        oracle_ids=value.oracle_ids,
        observed_at_utc=value.observed_at_utc,
        valid_until_utc=value.valid_until_utc,
        predecessor_sequence=value.predecessor_sequence,
    )


def _retrieval_request_payload(value: RetrievalGateRequest) -> dict[str, object]:
    return retrieval_gate_request_payload(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        boundary_commit_sequence=value.boundary_commit_sequence,
        candidate_ref=value.candidate_ref,
        compatibility_decision_ref=value.compatibility_decision_ref,
        conflict_decision_ref=value.conflict_decision_ref,
        subject_ref=value.subject_ref,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
        scope_ids=value.scope_ids,
        capability_ids=value.capability_ids,
        tool_ids=value.tool_ids,
        oracle_ids=value.oracle_ids,
        observed_at_utc=value.observed_at_utc,
        valid_until_utc=value.valid_until_utc,
        predecessor_sequence=value.predecessor_sequence,
    )


def _consumption_request_payload(
    value: ConsumptionGateRequest,
) -> dict[str, object]:
    return consumption_gate_request_payload(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        boundary_commit_sequence=value.boundary_commit_sequence,
        retrieval_decision_ref=value.retrieval_decision_ref,
        load_decision_ref=value.load_decision_ref,
        compatibility_revalidation_ref=value.compatibility_revalidation_ref,
        subject_ref=value.subject_ref,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
        scope_ids=value.scope_ids,
        capability_ids=value.capability_ids,
        tool_ids=value.tool_ids,
        oracle_ids=value.oracle_ids,
        observed_at_utc=value.observed_at_utc,
        valid_until_utc=value.valid_until_utc,
        predecessor_sequence=value.predecessor_sequence,
    )


_REQUEST_SPEC = {
    IngestionGateRequest: (
        INGESTION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.INGESTION_GATE_REQUEST,
        _ingestion_request_payload,
    ),
    PublicationGateRequest: (
        PUBLICATION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.PUBLICATION_GATE_REQUEST,
        _publication_request_payload,
    ),
    RetrievalGateRequest: (
        RETRIEVAL_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.RETRIEVAL_GATE_REQUEST,
        _retrieval_request_payload,
    ),
    ConsumptionGateRequest: (
        CONSUMPTION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.CONSUMPTION_GATE_REQUEST,
        _consumption_request_payload,
    ),
}


def _seal_request(
    request_type: type,
    *,
    envelope: CommonEnvelope,
    fields: dict[str, object],
) -> object:
    if request_type not in _REQUEST_SPEC:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate request type is unsupported",
        )
    schema, domain, payload_factory = _REQUEST_SPEC[request_type]
    result = object.__new__(request_type)
    object.__setattr__(result, "schema_version", schema)
    object.__setattr__(result, "envelope", envelope)
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    payload = payload_factory(result)
    payload_bytes = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=payload_bytes)
    if envelope.record_id.domain is not domain:
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "gate request envelope domain is invalid",
        )
    object.__setattr__(result, "request_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _REQUEST_SEAL)
    _validate_gate_request(result)
    return result


def create_ingestion_gate_request(
    *,
    envelope: CommonEnvelope,
    source_transaction_id: RecordId,
    source_identity: ActorIdentity,
    subject_ref: HashBoundRef,
    observed_input_refs: tuple[HashBoundRef, ...],
    predecessor_decision_ref: HashBoundRef | None,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> IngestionGateRequest:
    fields = dict(locals())
    fields.pop("envelope")
    return _seal_request(
        IngestionGateRequest,
        envelope=envelope,
        fields=fields,
    )


def create_publication_gate_request(
    *,
    envelope: CommonEnvelope,
    publication_transaction_id: RecordId,
    ingested_candidate_ref: HashBoundRef,
    ingestion_decision_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    attestation_ref: HashBoundRef,
    binding_refs: tuple[HashBoundRef, ...],
    target_library_predecessor_root: str,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> PublicationGateRequest:
    fields = dict(locals())
    fields.pop("envelope")
    return _seal_request(PublicationGateRequest, envelope=envelope, fields=fields)


def create_retrieval_gate_request(
    *,
    envelope: CommonEnvelope,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    candidate_ref: HashBoundRef,
    compatibility_decision_ref: HashBoundRef,
    conflict_decision_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> RetrievalGateRequest:
    fields = dict(locals())
    fields.pop("envelope")
    return _seal_request(RetrievalGateRequest, envelope=envelope, fields=fields)


def create_consumption_gate_request(
    *,
    envelope: CommonEnvelope,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    retrieval_decision_ref: HashBoundRef,
    load_decision_ref: HashBoundRef,
    compatibility_revalidation_ref: HashBoundRef,
    subject_ref: HashBoundRef,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
    scope_ids: tuple[str, ...],
    capability_ids: tuple[str, ...],
    tool_ids: tuple[str, ...],
    oracle_ids: tuple[str, ...],
    observed_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_sequence: int,
) -> ConsumptionGateRequest:
    fields = dict(locals())
    fields.pop("envelope")
    return _seal_request(ConsumptionGateRequest, envelope=envelope, fields=fields)


def _validate_gate_request(value: object) -> None:
    spec = _REQUEST_SPEC.get(type(value))
    if (
        spec is None
        or getattr(value, "_trusted_seal", None) is not _REQUEST_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "gate request is not factory sealed",
        )
    schema, domain, payload_factory = spec
    if value.schema_version != schema or type(value.schema_version) is not str:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "gate request schema is unknown",
        )
    payload = payload_factory(value)
    payload_bytes = _canonical(payload)
    validate_common_envelope(value.envelope, canonical_payload_bytes=payload_bytes)
    if (
        value.envelope.record_id.domain is not domain
        or value.request_id != value.envelope.record_id
    ):
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "gate request identity changed",
        )
    if value.envelope.created_at_utc != value.observed_at_utc:
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "gate request observation differs from its envelope timestamp",
        )


def validate_ingestion_gate_request(value: IngestionGateRequest) -> None:
    if type(value) is not IngestionGateRequest:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "ingestion request type differs")
    _validate_gate_request(value)


def validate_publication_gate_request(value: PublicationGateRequest) -> None:
    if type(value) is not PublicationGateRequest:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "publication request type differs")
    _validate_gate_request(value)


def validate_retrieval_gate_request(value: RetrievalGateRequest) -> None:
    if type(value) is not RetrievalGateRequest:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "retrieval request type differs")
    _validate_gate_request(value)


def validate_consumption_gate_request(value: ConsumptionGateRequest) -> None:
    if type(value) is not ConsumptionGateRequest:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "consumption request type differs")
    _validate_gate_request(value)


_GATE_REASON_TYPES = {
    AuthorityRole.INGESTION_GATE_EVALUATOR: IngestionGateReason,
    AuthorityRole.PUBLICATION_GATE_EVALUATOR: PublicationGateReason,
    AuthorityRole.RETRIEVAL_GATE_EVALUATOR: RetrievalGateReason,
    AuthorityRole.CONSUMPTION_GATE_EVALUATOR: ConsumptionGateReason,
}
_GATE_REQUIRED_DIMENSIONS = {
    AuthorityRole.INGESTION_GATE_EVALUATOR: INGESTION_REQUIRED_DIMENSIONS,
    AuthorityRole.PUBLICATION_GATE_EVALUATOR: PUBLICATION_REQUIRED_DIMENSIONS,
    AuthorityRole.RETRIEVAL_GATE_EVALUATOR: RETRIEVAL_REQUIRED_DIMENSIONS,
    AuthorityRole.CONSUMPTION_GATE_EVALUATOR: CONSUMPTION_REQUIRED_DIMENSIONS,
}


def _ordered_reasons(
    value: object,
    *,
    reason_type: type[Enum],
) -> tuple[Enum, ...]:
    if type(value) is not tuple or any(type(item) is not reason_type for item in value):
        raise _fail(
            AdmissionFailureCode.UNKNOWN_REASON,
            "gate reasons must use the exact closed vocabulary",
        )
    order = {item: index for index, item in enumerate(reason_type)}
    if len(set(value)) != len(value) or tuple(sorted(value, key=order.get)) != value:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_REASON,
            "gate reasons must be distinct and canonical",
        )
    return value


def _derive_decision_kind(
    *,
    reason_type: type[Enum],
    reasons: tuple[Enum, ...],
    checked_dimensions: tuple[GateCheckedDimension, ...],
) -> GateDecisionKind:
    rules = _REASON_RULES.get(reason_type)
    if rules is None:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_REASON,
            "gate reason registry is incomplete",
        )
    reason_set = set(reasons)
    if reason_set & rules[GateDecisionKind.QUARANTINE]:
        return GateDecisionKind.QUARANTINE
    if reason_set & rules[GateDecisionKind.REJECT]:
        return GateDecisionKind.REJECT
    if reason_set & rules[GateDecisionKind.REQUIRE_REVIEW]:
        return GateDecisionKind.REQUIRE_REVIEW
    if (
        rules[GateDecisionKind.ADMIT].issubset(reason_set)
        and all(
            item.result is GateDimensionResult.PASS
            for item in checked_dimensions
        )
    ):
        return GateDecisionKind.ADMIT
    raise _fail(
        AdmissionFailureCode.REASON_OUTCOME_MISMATCH,
        "gate evidence does not establish a closed outcome",
    )


def _diagnostics(
    value: object,
) -> tuple[tuple[str, str | int | bool | None], ...]:
    if type(value) is not tuple:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "diagnostics must be an exact immutable tuple",
        )
    result: list[tuple[str, str | int | bool | None]] = []
    for item in value:
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or type(item[1]) not in (str, int, bool, type(None))
        ):
            raise _fail(
                AdmissionFailureCode.MALFORMED_DECISION,
                "diagnostics must contain JSON scalar pairs",
            )
        key = _safe_text(item[0], "diagnostic key")
        scalar = item[1]
        if type(scalar) is str:
            scalar = _safe_text(scalar, "diagnostic value")
        if type(scalar) is int and not -(2**53) < scalar < 2**53:
            raise _fail(
                AdmissionFailureCode.MALFORMED_DECISION,
                "diagnostic integer is outside the exact JSON range",
            )
        result.append((key, scalar))
    normalized = tuple(result)
    if normalized != tuple(sorted(normalized, key=lambda item: item[0])) or len(
        {item[0] for item in normalized}
    ) != len(normalized):
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "diagnostics must have distinct canonical keys",
        )
    return normalized


def _decision_payload_fields(
    *,
    schema_version: str,
    request_ref: HashBoundRef,
    decision_kind: GateDecisionKind,
    reasons: tuple[Enum, ...],
    checked_dimensions: tuple[GateCheckedDimension, ...],
    evaluator_declaration: KnowledgeAdmissionEvaluatorDeclaration,
    base_configuration_id: RecordId,
    knowledge_admission_configuration_id: RecordId,
    independence_proof: IndependenceProof,
    evaluated_at_utc: datetime,
    valid_until_utc: datetime,
    predecessor_decision_id: AuthorityDecisionId | None,
    decision_sequence: int,
    diagnostics: tuple[tuple[str, str | int | bool | None], ...],
) -> dict[str, object]:
    _ref(request_ref, "request_ref")
    if type(decision_kind) is not GateDecisionKind:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "gate decision kind is invalid",
        )
    validate_knowledge_admission_evaluator_declaration(evaluator_declaration)
    if (
        type(base_configuration_id) is not RecordId
        or base_configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION
    ):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "base authority configuration identity is invalid",
        )
    base_configuration_id.to_dict()
    if (
        type(knowledge_admission_configuration_id) is not RecordId
        or knowledge_admission_configuration_id.domain
        is not IdentityDomain.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION
    ):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "knowledge/admission authority configuration identity is invalid",
        )
    knowledge_admission_configuration_id.to_dict()
    validate_independence_proof(independence_proof)
    evaluated = _timestamp(evaluated_at_utc, "evaluated_at_utc")
    expiry = _timestamp(valid_until_utc, "valid_until_utc")
    if expiry <= evaluated:
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "gate decision expiry must follow evaluation",
        )
    if type(decision_sequence) is not int or decision_sequence < 1:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "gate decision sequence is invalid",
        )
    if predecessor_decision_id is not None:
        predecessor_decision_id.to_dict()
    if (predecessor_decision_id is None) != (decision_sequence == 1):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "gate decision predecessor and sequence disagree",
        )
    diagnostic_items = _diagnostics(diagnostics)
    return {
        "schema_version": schema_version,
        "request_ref": request_ref.to_dict(),
        "decision_kind": decision_kind.value,
        "reasons": [item.value for item in reasons],
        "checked_dimensions": [
            item.to_dict() for item in checked_dimensions
        ],
        "evaluator_declaration_id": evaluator_declaration.declaration_id.to_dict(),
        "base_configuration_id": base_configuration_id.to_dict(),
        "knowledge_admission_configuration_id": (
            knowledge_admission_configuration_id.to_dict()
        ),
        "independence_proof": independence_proof.to_dict(),
        "evaluated_at_utc": _timestamp_text(evaluated),
        "valid_until_utc": _timestamp_text(expiry),
        "predecessor_decision_id": (
            None
            if predecessor_decision_id is None
            else predecessor_decision_id.to_dict()
        ),
        "decision_sequence": decision_sequence,
        "diagnostics": [
            {"key": key, "value": item} for key, item in diagnostic_items
        ],
    }


@dataclass(frozen=True, init=False)
class IngestionGateDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: AuthorityDecisionId
    request_ref: HashBoundRef
    decision_kind: GateDecisionKind
    reasons: tuple[IngestionGateReason, ...]
    checked_dimensions: tuple[GateCheckedDimension, ...]
    evaluator_declaration: KnowledgeAdmissionEvaluatorDeclaration
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    evaluated_at_utc: datetime
    valid_until_utc: datetime
    predecessor_decision_id: AuthorityDecisionId | None
    decision_sequence: int
    diagnostics: tuple[tuple[str, str | int | bool | None], ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> IngestionGateDecision:
        raise TypeError("IngestionGateDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_ingestion_gate_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _decision_payload(self),
            "decision_id": self.decision_id.to_dict(),
        }


@dataclass(frozen=True, init=False)
class PublicationGateDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: AuthorityDecisionId
    request_ref: HashBoundRef
    decision_kind: GateDecisionKind
    reasons: tuple[PublicationGateReason, ...]
    checked_dimensions: tuple[GateCheckedDimension, ...]
    evaluator_declaration: KnowledgeAdmissionEvaluatorDeclaration
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    evaluated_at_utc: datetime
    valid_until_utc: datetime
    predecessor_decision_id: AuthorityDecisionId | None
    decision_sequence: int
    diagnostics: tuple[tuple[str, str | int | bool | None], ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PublicationGateDecision:
        raise TypeError("PublicationGateDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_publication_gate_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _decision_payload(self),
            "decision_id": self.decision_id.to_dict(),
        }


@dataclass(frozen=True, init=False)
class RetrievalGateDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: AuthorityDecisionId
    request_ref: HashBoundRef
    decision_kind: GateDecisionKind
    reasons: tuple[RetrievalGateReason, ...]
    checked_dimensions: tuple[GateCheckedDimension, ...]
    evaluator_declaration: KnowledgeAdmissionEvaluatorDeclaration
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    evaluated_at_utc: datetime
    valid_until_utc: datetime
    predecessor_decision_id: AuthorityDecisionId | None
    decision_sequence: int
    diagnostics: tuple[tuple[str, str | int | bool | None], ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalGateDecision:
        raise TypeError("RetrievalGateDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_gate_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _decision_payload(self),
            "decision_id": self.decision_id.to_dict(),
        }


@dataclass(frozen=True, init=False)
class ConsumptionDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: AuthorityDecisionId
    request_ref: HashBoundRef
    decision_kind: GateDecisionKind
    reasons: tuple[ConsumptionGateReason, ...]
    checked_dimensions: tuple[GateCheckedDimension, ...]
    evaluator_declaration: KnowledgeAdmissionEvaluatorDeclaration
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    evaluated_at_utc: datetime
    valid_until_utc: datetime
    predecessor_decision_id: AuthorityDecisionId | None
    decision_sequence: int
    diagnostics: tuple[tuple[str, str | int | bool | None], ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConsumptionDecision:
        raise TypeError("ConsumptionDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_consumption_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _decision_payload(self),
            "decision_id": self.decision_id.to_dict(),
        }


_DECISION_SPEC = {
    IngestionGateDecision: (
        INGESTION_GATE_DECISION_SCHEMA_V1,
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        IngestionGateReason,
        INGESTION_REQUIRED_DIMENSIONS,
    ),
    PublicationGateDecision: (
        PUBLICATION_GATE_DECISION_SCHEMA_V1,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        PublicationGateReason,
        PUBLICATION_REQUIRED_DIMENSIONS,
    ),
    RetrievalGateDecision: (
        RETRIEVAL_GATE_DECISION_SCHEMA_V1,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        RetrievalGateReason,
        RETRIEVAL_REQUIRED_DIMENSIONS,
    ),
    ConsumptionDecision: (
        CONSUMPTION_GATE_DECISION_SCHEMA_V1,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        ConsumptionGateReason,
        CONSUMPTION_REQUIRED_DIMENSIONS,
    ),
}


def _decision_payload(value: object) -> dict[str, object]:
    spec = _DECISION_SPEC.get(type(value))
    if spec is None:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate decision type is unsupported",
        )
    schema, _, _, _ = spec
    return _decision_payload_fields(
        schema_version=schema,
        request_ref=value.request_ref,
        decision_kind=value.decision_kind,
        reasons=value.reasons,
        checked_dimensions=value.checked_dimensions,
        evaluator_declaration=value.evaluator_declaration,
        base_configuration_id=value.base_configuration_id,
        knowledge_admission_configuration_id=(
            value.knowledge_admission_configuration_id
        ),
        independence_proof=value.independence_proof,
        evaluated_at_utc=value.evaluated_at_utc,
        valid_until_utc=value.valid_until_utc,
        predecessor_decision_id=value.predecessor_decision_id,
        decision_sequence=value.decision_sequence,
        diagnostics=value.diagnostics,
    )


class KnowledgeBoundaryResolution:
    schema_version: str
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    commit_sequence: int
    context_fingerprint_sha256: str
    admission_anchor: HistoryAnchor
    compatibility_anchor: HistoryAnchor
    valid_until_utc: datetime

    def __post_init__(self) -> None:
        if self.schema_version != "synapse.stage4.gold.knowledge-boundary-resolution/v1":
            raise _fail(
                AdmissionFailureCode.UNKNOWN_SCHEMA,
                "knowledge boundary resolution schema is unknown",
            )
        _record(
            self.repository_knowledge_snapshot_id,
            IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
            "repository_knowledge_snapshot_id",
        )
        _record(
            self.atomic_boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "atomic_boundary_id",
        )
        if type(self.commit_sequence) is not int or self.commit_sequence < 1:
            raise _fail(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary commit sequence is invalid",
            )
        if type(self.context_fingerprint_sha256) is not str or _SHA256_RE.fullmatch(
            self.context_fingerprint_sha256
        ) is None:
            raise _fail(
                AdmissionFailureCode.CONTEXT_MISMATCH,
                "knowledge boundary context fingerprint is invalid",
            )
        validate_history_anchor(self.admission_anchor)
        validate_history_anchor(self.compatibility_anchor)
        if (
            self.admission_anchor.history_domain is not HistoryDomain.ADMISSION
            or self.compatibility_anchor.history_domain
            is not HistoryDomain.COMPATIBILITY
        ):
            raise _fail(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary history domains are invalid",
            )
        _timestamp(self.valid_until_utc, "boundary.valid_until_utc")


class KnowledgeBoundaryResolver(Protocol):
    def resolve_boundary(
        self,
        *,
        repository_knowledge_snapshot_id: RecordId,
        atomic_boundary_id: RecordId,
        commit_sequence: int,
    ) -> KnowledgeBoundaryResolution:
        ...


@dataclass(frozen=True, init=False)
class SealedKnowledgeBoundaryResolver:
    profile_id: str
    component_identity: ActorIdentity
    _implementation: KnowledgeBoundaryResolver
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SealedKnowledgeBoundaryResolver:
        raise TypeError("SealedKnowledgeBoundaryResolver is factory-created")

    def resolve_boundary(
        self,
        *,
        repository_knowledge_snapshot_id: RecordId,
        atomic_boundary_id: RecordId,
        commit_sequence: int,
    ) -> KnowledgeBoundaryResolution:
        validate_sealed_knowledge_boundary_resolver(self)
        try:
            result = self._implementation.resolve_boundary(
                repository_knowledge_snapshot_id=repository_knowledge_snapshot_id,
                atomic_boundary_id=atomic_boundary_id,
                commit_sequence=commit_sequence,
            )
        except AdmissionViolation:
            raise
        except Exception as exc:
            raise _fail(
                AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
                "knowledge boundary resolver failed closed",
            ) from exc
        if type(result) is not KnowledgeBoundaryResolution:
            raise _fail(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary resolver returned an invalid result",
            )
        result.__post_init__()
        if (
            result.repository_knowledge_snapshot_id
            != repository_knowledge_snapshot_id
            or result.atomic_boundary_id != atomic_boundary_id
            or result.commit_sequence != commit_sequence
        ):
            raise _fail(
                AdmissionFailureCode.BOUNDARY_INVALID,
                "knowledge boundary resolver substituted context",
            )
        return result


def seal_knowledge_boundary_resolver(
    *,
    implementation: KnowledgeBoundaryResolver,
    profile_id: str,
    component_identity: ActorIdentity,
) -> SealedKnowledgeBoundaryResolver:
    if not callable(getattr(implementation, "resolve_boundary", None)):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge boundary resolver implementation is missing",
        )
    profile = _safe_text(profile_id, "knowledge resolver profile")
    if type(component_identity) is not ActorIdentity:
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge resolver component identity is invalid",
        )
    component_identity.to_dict()
    result = object.__new__(SealedKnowledgeBoundaryResolver)
    object.__setattr__(result, "profile_id", profile)
    object.__setattr__(result, "component_identity", component_identity)
    object.__setattr__(result, "_implementation", implementation)
    object.__setattr__(result, "_trusted_seal", _RESOLVER_SEAL)
    validate_sealed_knowledge_boundary_resolver(result)
    return result


def validate_sealed_knowledge_boundary_resolver(
    value: SealedKnowledgeBoundaryResolver,
) -> None:
    if (
        type(value) is not SealedKnowledgeBoundaryResolver
        or getattr(value, "_trusted_seal", None) is not _RESOLVER_SEAL
        or not callable(getattr(value._implementation, "resolve_boundary", None))
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge boundary resolver is not factory sealed",
        )
    _safe_text(value.profile_id, "knowledge resolver profile")
    if type(value.component_identity) is not ActorIdentity:
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge resolver component identity changed",
        )
    value.component_identity.to_dict()


class RetrievalAuthorityResolver(Protocol):
    def resolve_retrieval_authority(
        self,
        *,
        retrieval_decision_ref: HashBoundRef,
        expected_envelope: CommonEnvelope,
    ) -> tuple[RecordId, HashBoundRef]:
        ...


@dataclass(frozen=True, init=False)
class SealedRetrievalAuthorityResolver:
    profile_id: str
    component_identity: ActorIdentity
    _implementation: RetrievalAuthorityResolver
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SealedRetrievalAuthorityResolver:
        raise TypeError("SealedRetrievalAuthorityResolver is factory-created")

    def resolve_retrieval_authority(
        self,
        *,
        retrieval_decision_ref: HashBoundRef,
        expected_envelope: CommonEnvelope,
    ) -> tuple[RecordId, HashBoundRef]:
        validate_sealed_retrieval_authority_resolver(self)
        _ref(retrieval_decision_ref, "retrieval_decision_ref")
        try:
            result = self._implementation.resolve_retrieval_authority(
                retrieval_decision_ref=retrieval_decision_ref,
                expected_envelope=expected_envelope,
            )
        except AdmissionViolation:
            raise
        except Exception as exc:
            raise _fail(
                AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
                "retrieval authority resolver failed closed",
            ) from exc
        if (
            type(result) is not tuple
            or len(result) != 2
            or type(result[0]) is not RecordId
            or result[0].domain is not IdentityDomain.RETRIEVAL_DECISION_V2
            or type(result[1]) is not HashBoundRef
            or result[1] != retrieval_decision_ref
        ):
            raise _fail(
                AdmissionFailureCode.RESOLVER_MISMATCH,
                "retrieval authority resolver substituted authority",
            )
        return result


def seal_retrieval_authority_resolver(
    *,
    implementation: RetrievalAuthorityResolver,
    profile_id: str,
    component_identity: ActorIdentity,
) -> SealedRetrievalAuthorityResolver:
    if not callable(
        getattr(implementation, "resolve_retrieval_authority", None)
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval authority resolver implementation is missing",
        )
    result = object.__new__(SealedRetrievalAuthorityResolver)
    object.__setattr__(
        result,
        "profile_id",
        _safe_text(profile_id, "retrieval resolver profile"),
    )
    if type(component_identity) is not ActorIdentity:
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval resolver component identity is invalid",
        )
    component_identity.to_dict()
    object.__setattr__(result, "component_identity", component_identity)
    object.__setattr__(result, "_implementation", implementation)
    object.__setattr__(result, "_trusted_seal", _RESOLVER_SEAL)
    validate_sealed_retrieval_authority_resolver(result)
    return result


def validate_sealed_retrieval_authority_resolver(
    value: SealedRetrievalAuthorityResolver,
) -> None:
    if (
        type(value) is not SealedRetrievalAuthorityResolver
        or getattr(value, "_trusted_seal", None) is not _RESOLVER_SEAL
        or not callable(
            getattr(value._implementation, "resolve_retrieval_authority", None)
        )
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval authority resolver is not factory sealed",
        )
    _safe_text(value.profile_id, "retrieval resolver profile")
    if type(value.component_identity) is not ActorIdentity:
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval resolver component identity changed",
        )
    value.component_identity.to_dict()
