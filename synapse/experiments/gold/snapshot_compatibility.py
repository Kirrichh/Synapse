"""Stage 4 compatibility evidence and revalidation boundaries.

This module evaluates metadata and existing trusted records only. It does not
load or execute behavior payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Callable

from synapse.version import LANGUAGE_VERSION

from .behavior import (
    BehaviorBlob,
    BehaviorKind,
    BehaviorManifest,
    SynapseBehaviorUnit,
    validate_behavior_blob,
    validate_behavior_unit,
    validate_compiler_binding_for_unit,
)
from .bindings import (
    BindingKind,
    BindingViolation,
    DocumentBinding,
    PythonBinding,
    RequirementBinding,
    binding_to_ref,
    consume_document_binding,
    consume_python_binding,
    consume_requirement_binding,
)
from .canonicalization import (
    COMPILER_ADAPTER_PROFILE_V1,
    CVM_HOST_ABI_VERSION,
    CompilerBinding,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    ContentKey,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    AuthorityDecisionId,
    AuthorityIdentity,
    AuthorityRole,
    ActorIdentity,
    CommonEnvelope,
    ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    ENVELOPED_ARTIFACT_SCHEMA_V1,
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    IndependenceProof,
    LineageEdgeKind,
    LineageParentRef,
    ProposalId,
    ReasonCode,
    RecordId,
    RepositoryRevision,
    RepositoryRevisionKind,
    SchemaVersion,
    Stage4AuthorityHandle,
    compute_authority_decision_id,
    compute_proposal_id,
    compute_record_id,
    create_common_envelope,
    create_history_anchor,
    create_independence_proof,
    require_stage4_authority_handle,
    validate_history_anchor,
    validate_history_anchor_extension,
    validate_independence_proof,
    validate_common_envelope,
    validate_record_id,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    validate_knowledge_admission_authority_binding,
)
from .coordination import CoordinatedFenceLease
from .library import (
    MAX_INDEX_ENTRIES_V1,
    BehaviorLibrary,
    IndexEntry,
    LibraryViolation,
    LibraryObjectRef,
    LibrarySnapshot,
    SnapshotVerificationStatus,
    validate_snapshot_verification,
)
from .lifecycle import (
    LifecycleContext,
    LifecycleRecord,
    LifecycleSnapshot,
    LifecycleState,
    LifecycleStore,
    LifecycleViolation,
    validate_lifecycle_snapshot,
    validate_lifecycle_record,
)
from .provenance import (
    BehaviorAttestation,
    BehaviorAttestationStore,
    ExternalInputKind,
    ObservedExternalInput,
    OracleObservation,
    PlatformObservedProvenance,
    behavior_attestation_to_ref,
    require_behavior_attestation_consumable,
    validate_behavior_attestation,
    validate_platform_observed_provenance,
)
from .taint import (
    SourceTaintProfile,
    TaintAuthorityDecision,
    TaintDerivationRecord,
    TaintHistoryStore,
    require_taint_consumable,
    validate_source_taint_profile,
    validate_taint_derivation,
)


COMPATIBILITY_EVALUATOR_DECLARATION_V1 = (
    "synapse.stage4.gold.compatibility-evaluator-declaration/v1"
)
COMPATIBILITY_CONTEXT_V1 = "synapse.stage4.gold.compatibility-context/v1"
COMPATIBILITY_SUBJECT_DESCRIPTOR_V1 = (
    "synapse.stage4.gold.compatibility-subject-descriptor/v1"
)
COMPATIBILITY_DIMENSION_RECORD_V1 = (
    "synapse.stage4.gold.compatibility-dimension-record/v1"
)
COMPATIBILITY_EVIDENCE_V1 = "synapse.stage4.gold.compatibility-evidence/v1"
COMPATIBILITY_DECISION_V1 = "synapse.stage4.gold.compatibility-decision/v1"
COMPATIBILITY_REVALIDATION_V1 = "synapse.stage4.gold.compatibility-revalidation/v1"
CONFLICT_EVIDENCE_PROPOSAL_V1 = "synapse.stage4.gold.conflict-evidence-proposal/v1"
CONFLICT_EVALUATION_REQUEST_V1 = "synapse.stage4.gold.conflict-evaluation-request/v1"
COMPATIBILITY_CONFLICT_SCAN_V1 = "synapse.stage4.gold.compatibility-conflict-scan/v1"
CONFLICT_PAIR_ASSESSMENT_V1 = "synapse.stage4.gold.conflict-pair-assessment/v1"
COMPATIBILITY_POLICY_V1 = "synapse.stage4.gold.compatibility-policy/v1"
COMPATIBILITY_COMPARATOR_PROFILE_V1 = (
    "synapse.stage4.gold.compatibility-comparator-profile/v1"
)
COMPATIBILITY_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.compatibility+json"
COMPATIBILITY_CONTEXT_V2 = "synapse.stage4.gold.compatibility-context/v2"
COMPATIBILITY_EVIDENCE_V2 = "synapse.stage4.gold.compatibility-evidence/v2"
COMPATIBILITY_DECISION_V2 = "synapse.stage4.gold.compatibility-decision/v2"
COMPATIBILITY_REVALIDATION_V2 = (
    "synapse.stage4.gold.compatibility-revalidation/v2"
)
COMPATIBILITY_HISTORY_FRAME_SCHEMA_V1 = (
    "synapse.stage4.gold.compatibility-history-frame/v1"
)
COMPATIBILITY_COMMIT_EVIDENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.compatibility-commit-evidence/v1"
)

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SEAL = object()
_DECLARATION_SEAL = object()
_CAPABILITY_SEAL = object()
_V2_SEAL = object()
_STORE_SEAL = object()
_DURABILITY_BINDING_SEAL = object()
_COMMIT_SEAL = object()
_HOST_ABI_OBSERVATION_V1 = "synapse.stage4.host-abi/v1"
_HOST_ABI_BY_OBSERVATION_VERSION = MappingProxyType({
    _HOST_ABI_OBSERVATION_V1: CVM_HOST_ABI_VERSION,
})


from .compatibility import *
from .compatibility import _canonical, _record, _ref, _refs, _timestamp, _timestamp_text

def _enveloped_compatibility_bytes(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> bytes:
    payload_bytes = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=payload_bytes)
    return _canonical({"envelope": envelope.to_dict(), "payload": payload})


def _enveloped_compatibility_ref(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> HashBoundRef:
    value = _enveloped_compatibility_bytes(envelope, payload)
    digest = hashlib.sha256(value).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(value),
        media_type=ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    )


@dataclass(frozen=True, init=False)
class SnapshotBoundCompatibilityContext:
    schema_version: str
    envelope: CommonEnvelope
    context_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    historical_context_ref: HashBoundRef
    declared_input_refs: tuple[HashBoundRef, ...]
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundCompatibilityContext:
        raise TypeError("SnapshotBoundCompatibilityContext is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_compatibility_context(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _snapshot_context_v2_payload(self),
        }


def snapshot_bound_compatibility_context_payload(
    *,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    historical_context_ref: HashBoundRef,
    declared_input_refs: tuple[HashBoundRef, ...],
    base_configuration_id: RecordId,
    knowledge_admission_configuration_id: RecordId,
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
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "boundary commit sequence is invalid",
        )
    _ref(historical_context_ref, RefKind.ARTIFACT, "historical context ref")
    refs = _refs(declared_input_refs, None, "declared input refs")
    if not refs:
        raise _fail(
            CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
            "declared compatibility inputs are empty",
        )
    _record(
        base_configuration_id,
        IdentityDomain.AUTHORITY_CONFIGURATION,
        "base configuration id",
    )
    _record(
        knowledge_admission_configuration_id,
        IdentityDomain.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION,
        "knowledge admission configuration id",
    )
    return {
        "schema_version": COMPATIBILITY_CONTEXT_V2,
        "repository_knowledge_snapshot_id": (
            repository_knowledge_snapshot_id.to_dict()
        ),
        "atomic_boundary_id": atomic_boundary_id.to_dict(),
        "boundary_commit_sequence": boundary_commit_sequence,
        "historical_context_ref": historical_context_ref.to_dict(),
        "declared_input_refs": [item.to_dict() for item in refs],
        "base_configuration_id": base_configuration_id.to_dict(),
        "knowledge_admission_configuration_id": (
            knowledge_admission_configuration_id.to_dict()
        ),
    }


def _snapshot_context_v2_payload(
    value: SnapshotBoundCompatibilityContext,
) -> dict[str, object]:
    return snapshot_bound_compatibility_context_payload(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        boundary_commit_sequence=value.boundary_commit_sequence,
        historical_context_ref=value.historical_context_ref,
        declared_input_refs=value.declared_input_refs,
        base_configuration_id=value.base_configuration_id,
        knowledge_admission_configuration_id=(
            value.knowledge_admission_configuration_id
        ),
    )


def create_snapshot_bound_compatibility_context(
    *,
    envelope: CommonEnvelope,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    historical_context_ref: HashBoundRef,
    declared_input_refs: tuple[HashBoundRef, ...],
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> SnapshotBoundCompatibilityContext:
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    result = object.__new__(SnapshotBoundCompatibilityContext)
    object.__setattr__(result, "schema_version", COMPATIBILITY_CONTEXT_V2)
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
    object.__setattr__(result, "historical_context_ref", historical_context_ref)
    object.__setattr__(result, "declared_input_refs", declared_input_refs)
    object.__setattr__(result, "base_configuration_id", base.configuration_id)
    object.__setattr__(
        result,
        "knowledge_admission_configuration_id",
        overlay.configuration_id,
    )
    payload = _snapshot_context_v2_payload(result)
    validate_common_envelope(envelope, canonical_payload_bytes=_canonical(payload))
    if envelope.record_id.domain is not IdentityDomain.COMPATIBILITY_CONTEXT_V2:
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "v2 compatibility context envelope domain is invalid",
        )
    object.__setattr__(result, "context_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_context(
        result,
        authority_binding=authority_binding,
    )
    return result


def validate_snapshot_bound_compatibility_context(
    value: SnapshotBoundCompatibilityContext,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding | None = None,
) -> None:
    if (
        type(value) is not SnapshotBoundCompatibilityContext
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != COMPATIBILITY_CONTEXT_V2
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility context is not factory sealed",
        )
    payload = _snapshot_context_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.context_id != value.envelope.record_id
        or value.context_id.domain is not IdentityDomain.COMPATIBILITY_CONTEXT_V2
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility context identity changed",
        )
    if authority_binding is not None:
        base, overlay = validate_knowledge_admission_authority_binding(
            authority_binding
        )
        if (
            value.base_configuration_id != base.configuration_id
            or value.knowledge_admission_configuration_id
            != overlay.configuration_id
        ):
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility context authority changed",
            )


@dataclass(frozen=True, init=False)
class SnapshotBoundCompatibilityEvidence:
    schema_version: str
    envelope: CommonEnvelope
    evidence_id: RecordId
    context_ref: HashBoundRef
    subject_ref: HashBoundRef
    dimensions: tuple[CompatibilityDimensionRecord, ...]
    source_evidence_refs: tuple[HashBoundRef, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundCompatibilityEvidence:
        raise TypeError("SnapshotBoundCompatibilityEvidence is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_compatibility_evidence(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _snapshot_evidence_v2_payload(self),
        }


def _snapshot_evidence_v2_payload(
    value: SnapshotBoundCompatibilityEvidence,
) -> dict[str, object]:
    return {
        "schema_version": COMPATIBILITY_EVIDENCE_V2,
        "context_ref": value.context_ref.to_dict(),
        "subject_ref": value.subject_ref.to_dict(),
        "dimensions": [item.to_dict() for item in value.dimensions],
        "source_evidence_refs": [
            item.to_dict() for item in value.source_evidence_refs
        ],
    }


def create_snapshot_bound_compatibility_evidence(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: SnapshotBoundCompatibilityContext,
    subject_ref: HashBoundRef,
    dimensions: tuple[CompatibilityDimensionRecord, ...],
    source_evidence_refs: tuple[HashBoundRef, ...],
    observed_at_utc: datetime,
) -> SnapshotBoundCompatibilityEvidence:
    require_configured_compatibility_evaluator(evaluator)
    validate_snapshot_bound_compatibility_context(context)
    if (
        type(dimensions) is not tuple
        or tuple(item.dimension for item in dimensions)
        != REQUIRED_COMPATIBILITY_DIMENSIONS
    ):
        raise _fail(
            CompatibilityFailureCode.DIMENSION_MISSING,
            "v2 evidence requires all twelve ordered compatibility dimensions",
        )
    for item in dimensions:
        validate_compatibility_dimension_record(item)
    result = object.__new__(SnapshotBoundCompatibilityEvidence)
    object.__setattr__(result, "schema_version", COMPATIBILITY_EVIDENCE_V2)
    object.__setattr__(
        result,
        "context_ref",
        _enveloped_compatibility_ref(
            context.envelope,
            _snapshot_context_v2_payload(context),
        ),
    )
    object.__setattr__(result, "subject_ref", _ref(subject_ref, None, "subject ref"))
    object.__setattr__(result, "dimensions", dimensions)
    object.__setattr__(
        result,
        "source_evidence_refs",
        _refs(source_evidence_refs, None, "source evidence refs"),
    )
    payload = _snapshot_evidence_v2_payload(result)
    observed = _timestamp(observed_at_utc, "v2 compatibility observation")
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMPATIBILITY_EVIDENCE_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=context.envelope.run_id,
        attempt_id=context.envelope.attempt_id,
        created_at_utc=observed,
        producer_component=evaluator._component_id,
        repository_revision=context.envelope.repository_revision,
        policy_version=context.envelope.policy_version,
        environment_profile_id=context.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                context.context_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "evidence_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_evidence(result)
    return result


def validate_snapshot_bound_compatibility_evidence(
    value: SnapshotBoundCompatibilityEvidence,
) -> None:
    if (
        type(value) is not SnapshotBoundCompatibilityEvidence
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != COMPATIBILITY_EVIDENCE_V2
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility evidence is not evaluator sealed",
        )
    if (
        tuple(item.dimension for item in value.dimensions)
        != REQUIRED_COMPATIBILITY_DIMENSIONS
        or len(set(item.dimension for item in value.dimensions))
        != len(REQUIRED_COMPATIBILITY_DIMENSIONS)
    ):
        raise _fail(
            CompatibilityFailureCode.DIMENSION_MISSING,
            "v2 compatibility dimension registry changed",
        )
    for item in value.dimensions:
        validate_compatibility_dimension_record(item)
    _ref(value.context_ref, RefKind.ARTIFACT, "context ref")
    _ref(value.subject_ref, None, "subject ref")
    _refs(value.source_evidence_refs, None, "source evidence refs")
    payload = _snapshot_evidence_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.evidence_id != value.envelope.record_id
        or value.evidence_id.domain is not IdentityDomain.COMPATIBILITY_EVIDENCE_V2
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility evidence identity changed",
        )


@dataclass(frozen=True, init=False)
class SnapshotBoundCompatibilityDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: AuthorityDecisionId
    context_ref: HashBoundRef
    evidence_ref: HashBoundRef
    decision_kind: CompatibilityDecisionKind
    evaluator_declaration_id: RecordId
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    evaluated_at_utc: datetime
    valid_until_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundCompatibilityDecision:
        raise TypeError("SnapshotBoundCompatibilityDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_compatibility_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _snapshot_decision_v2_payload(self),
            "decision_id": self.decision_id.to_dict(),
        }


def _snapshot_decision_v2_payload(
    value: SnapshotBoundCompatibilityDecision,
) -> dict[str, object]:
    return {
        "schema_version": COMPATIBILITY_DECISION_V2,
        "context_ref": value.context_ref.to_dict(),
        "evidence_ref": value.evidence_ref.to_dict(),
        "decision_kind": value.decision_kind.value,
        "evaluator_declaration_id": value.evaluator_declaration_id.to_dict(),
        "base_configuration_id": value.base_configuration_id.to_dict(),
        "knowledge_admission_configuration_id": (
            value.knowledge_admission_configuration_id.to_dict()
        ),
        "independence_proof": value.independence_proof.to_dict(),
        "evaluated_at_utc": _timestamp_text(value.evaluated_at_utc),
        "valid_until_utc": _timestamp_text(value.valid_until_utc),
    }


def create_snapshot_bound_compatibility_decision(
    *,
    context: SnapshotBoundCompatibilityContext,
    evidence: SnapshotBoundCompatibilityEvidence,
    declaration: CompatibilityEvaluatorDeclaration,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    producer_actor_ids: tuple[ActorIdentity, ...],
    source_actor_ids: tuple[ActorIdentity, ...],
    proposer_identity: ActorIdentity,
    executor_identity: ActorIdentity | None,
    evaluated_at_utc: datetime,
    valid_until_utc: datetime,
) -> SnapshotBoundCompatibilityDecision:
    validate_snapshot_bound_compatibility_context(
        context,
        authority_binding=authority_binding,
    )
    validate_snapshot_bound_compatibility_evidence(evidence)
    validate_compatibility_evaluator_declaration(declaration)
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if declaration.configuration_id != base.configuration_id:
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_DECLARATION_MISMATCH,
            "compatibility declaration does not belong to the base configuration",
        )
    context_ref = _enveloped_compatibility_ref(
        context.envelope,
        _snapshot_context_v2_payload(context),
    )
    evidence_ref = _enveloped_compatibility_ref(
        evidence.envelope,
        _snapshot_evidence_v2_payload(evidence),
    )
    if evidence.context_ref != context_ref:
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "v2 compatibility evidence belongs to another context",
        )
    kind = _expected_decision_kind(evidence.dimensions)
    decision_basis = _canonical(
        {
            "schema_version": COMPATIBILITY_DECISION_V2,
            "context_ref": context_ref.to_dict(),
            "evidence_ref": evidence_ref.to_dict(),
            "decision_kind": kind.value,
            "evaluator_declaration_id": declaration.declaration_id.to_dict(),
            "base_configuration_id": base.configuration_id.to_dict(),
            "knowledge_admission_configuration_id": overlay.configuration_id.to_dict(),
        }
    )
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=compute_proposal_id(canonical_bytes=decision_basis),
        authority_identity=declaration.evaluator_identity,
        authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
        reason_code=ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT,
        producer_actor_ids=producer_actor_ids,
        source_actor_ids=source_actor_ids,
        proposer_identity=proposer_identity,
        executor_identity=executor_identity,
        subject_derived_actor_ids=(),
        delegation_chain=(),
    )
    evaluated = _timestamp(evaluated_at_utc, "v2 compatibility evaluation")
    expiry = _timestamp(valid_until_utc, "v2 compatibility expiry")
    if expiry <= evaluated:
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "v2 compatibility decision expiry is invalid",
        )
    result = object.__new__(SnapshotBoundCompatibilityDecision)
    object.__setattr__(result, "schema_version", COMPATIBILITY_DECISION_V2)
    object.__setattr__(result, "context_ref", context_ref)
    object.__setattr__(result, "evidence_ref", evidence_ref)
    object.__setattr__(result, "decision_kind", kind)
    object.__setattr__(
        result,
        "evaluator_declaration_id",
        declaration.declaration_id,
    )
    object.__setattr__(result, "base_configuration_id", base.configuration_id)
    object.__setattr__(
        result,
        "knowledge_admission_configuration_id",
        overlay.configuration_id,
    )
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "evaluated_at_utc", evaluated)
    object.__setattr__(result, "valid_until_utc", expiry)
    payload = _snapshot_decision_v2_payload(result)
    payload_bytes = _canonical(payload)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.AUTHORITY_DECISION,
        canonical_payload_bytes=payload_bytes,
        run_id=context.envelope.run_id,
        attempt_id=context.envelope.attempt_id,
        created_at_utc=evaluated,
        producer_component=declaration.evaluator_component_id,
        repository_revision=context.envelope.repository_revision,
        policy_version=context.envelope.policy_version,
        environment_profile_id=context.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                context.context_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                evidence.evidence_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(
        result,
        "decision_id",
        compute_authority_decision_id(
            canonical_bytes=payload_bytes,
            independence_proof=proof,
        ),
    )
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_decision(result)
    return result


def validate_snapshot_bound_compatibility_decision(
    value: SnapshotBoundCompatibilityDecision,
) -> None:
    if (
        type(value) is not SnapshotBoundCompatibilityDecision
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != COMPATIBILITY_DECISION_V2
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility decision is not evaluator sealed",
        )
    _ref(value.context_ref, RefKind.ARTIFACT, "context ref")
    _ref(value.evidence_ref, RefKind.ARTIFACT, "evidence ref")
    if type(value.decision_kind) is not CompatibilityDecisionKind:
        raise _fail(
            CompatibilityFailureCode.DECISION_MISMATCH,
            "v2 compatibility decision kind is invalid",
        )
    _record(
        value.evaluator_declaration_id,
        IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION,
        "evaluator declaration id",
    )
    _record(
        value.base_configuration_id,
        IdentityDomain.AUTHORITY_CONFIGURATION,
        "base configuration id",
    )
    _record(
        value.knowledge_admission_configuration_id,
        IdentityDomain.KNOWLEDGE_ADMISSION_AUTHORITY_CONFIGURATION,
        "knowledge admission configuration id",
    )
    validate_independence_proof(value.independence_proof)
    if (
        value.independence_proof.authority_role
        is not AuthorityRole.COMPATIBILITY_EVALUATOR
        or value.independence_proof.reason_code
        is not ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "v2 compatibility decision proof changed",
        )
    if value.valid_until_utc <= value.evaluated_at_utc:
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "v2 compatibility decision expired before evaluation",
        )
    payload = _snapshot_decision_v2_payload(value)
    payload_bytes = _canonical(payload)
    validate_common_envelope(value.envelope, canonical_payload_bytes=payload_bytes)
    expected = compute_authority_decision_id(
        canonical_bytes=payload_bytes,
        independence_proof=value.independence_proof,
    )
    if (
        value.decision_id != expected
        or value.envelope.record_id.domain is not IdentityDomain.AUTHORITY_DECISION
    ):
        raise _fail(
            CompatibilityFailureCode.AUTHORITY_DECISION_INVALID,
            "v2 compatibility authority identity changed",
        )


@dataclass(frozen=True, init=False)
class SnapshotBoundCompatibilityRevalidation:
    schema_version: str
    envelope: CommonEnvelope
    revalidation_id: RecordId
    context_ref: HashBoundRef
    decision_ref: HashBoundRef
    stage: RevalidationStage
    outcome: RevalidationOutcome
    checked_dimension_results: tuple[DimensionResult, ...]
    observed_head_refs: tuple[HashBoundRef, ...]
    evaluated_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundCompatibilityRevalidation:
        raise TypeError("SnapshotBoundCompatibilityRevalidation is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_compatibility_revalidation(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _snapshot_revalidation_v2_payload(self),
        }


def _snapshot_revalidation_v2_payload(
    value: SnapshotBoundCompatibilityRevalidation,
) -> dict[str, object]:
    return {
        "schema_version": COMPATIBILITY_REVALIDATION_V2,
        "context_ref": value.context_ref.to_dict(),
        "decision_ref": value.decision_ref.to_dict(),
        "stage": value.stage.value,
        "outcome": value.outcome.value,
        "checked_dimension_results": [
            item.value for item in value.checked_dimension_results
        ],
        "observed_head_refs": [item.to_dict() for item in value.observed_head_refs],
        "evaluated_at_utc": _timestamp_text(value.evaluated_at_utc),
    }


def create_snapshot_bound_compatibility_revalidation(
    *,
    context: SnapshotBoundCompatibilityContext,
    decision: SnapshotBoundCompatibilityDecision,
    evidence: SnapshotBoundCompatibilityEvidence,
    stage: RevalidationStage,
    observed_head_refs: tuple[HashBoundRef, ...],
    evaluated_at_utc: datetime,
) -> SnapshotBoundCompatibilityRevalidation:
    validate_snapshot_bound_compatibility_context(context)
    validate_snapshot_bound_compatibility_decision(decision)
    validate_snapshot_bound_compatibility_evidence(evidence)
    context_ref = _enveloped_compatibility_ref(
        context.envelope,
        _snapshot_context_v2_payload(context),
    )
    decision_ref = _enveloped_compatibility_ref(
        decision.envelope,
        _snapshot_decision_v2_payload(decision),
    )
    if decision.context_ref != context_ref or evidence.context_ref != context_ref:
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "compatibility revalidation graph mixes contexts",
        )
    if type(stage) is not RevalidationStage:
        raise _fail(
            CompatibilityFailureCode.UNKNOWN_SCHEMA,
            "compatibility revalidation stage is invalid",
        )
    results = tuple(item.result for item in evidence.dimensions)
    outcome = (
        RevalidationOutcome.PASSED
        if decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE
        and all(item is DimensionResult.PASS for item in results)
        else RevalidationOutcome.FAILED
    )
    result = object.__new__(SnapshotBoundCompatibilityRevalidation)
    object.__setattr__(result, "schema_version", COMPATIBILITY_REVALIDATION_V2)
    object.__setattr__(result, "context_ref", context_ref)
    object.__setattr__(result, "decision_ref", decision_ref)
    object.__setattr__(result, "stage", stage)
    object.__setattr__(result, "outcome", outcome)
    object.__setattr__(result, "checked_dimension_results", results)
    object.__setattr__(
        result,
        "observed_head_refs",
        _refs(observed_head_refs, None, "observed head refs"),
    )
    object.__setattr__(
        result,
        "evaluated_at_utc",
        _timestamp(evaluated_at_utc, "compatibility revalidation timestamp"),
    )
    payload = _snapshot_revalidation_v2_payload(result)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMPATIBILITY_REVALIDATION_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=context.envelope.run_id,
        attempt_id=context.envelope.attempt_id,
        created_at_utc=result.evaluated_at_utc,
        producer_component=decision.envelope.producer_component,
        repository_revision=context.envelope.repository_revision,
        policy_version=context.envelope.policy_version,
        environment_profile_id=context.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                context.context_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                decision.envelope.record_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "revalidation_id", envelope.record_id)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_revalidation(result)
    return result


def validate_snapshot_bound_compatibility_revalidation(
    value: SnapshotBoundCompatibilityRevalidation,
) -> None:
    if (
        type(value) is not SnapshotBoundCompatibilityRevalidation
        or getattr(value, "_trusted_seal", None) is not _V2_SEAL
        or value.schema_version != COMPATIBILITY_REVALIDATION_V2
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility revalidation is not evaluator sealed",
        )
    if (
        type(value.stage) is not RevalidationStage
        or type(value.outcome) is not RevalidationOutcome
        or type(value.checked_dimension_results) is not tuple
        or len(value.checked_dimension_results)
        != len(REQUIRED_COMPATIBILITY_DIMENSIONS)
        or any(type(item) is not DimensionResult for item in value.checked_dimension_results)
    ):
        raise _fail(
            CompatibilityFailureCode.DIMENSION_MISSING,
            "v2 compatibility revalidation dimensions changed",
        )
    expected = (
        RevalidationOutcome.PASSED
        if all(item is DimensionResult.PASS for item in value.checked_dimension_results)
        else RevalidationOutcome.FAILED
    )
    if value.outcome is not expected:
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "v2 compatibility revalidation outcome changed",
        )
    _refs(value.observed_head_refs, None, "observed head refs")
    payload = _snapshot_revalidation_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.revalidation_id != value.envelope.record_id
        or value.revalidation_id.domain
        is not IdentityDomain.COMPATIBILITY_REVALIDATION_V2
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility revalidation identity changed",
        )
def historical_compatibility_context_ref(
    value: CompatibilityContext,
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
) -> HashBoundRef:
    validate_compatibility_context(value, evaluator=evaluator)
    raw = _canonical(_context_payload(value))
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=COMPATIBILITY_CONTEXT_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=COMPATIBILITY_MEDIA_TYPE_V1,
    )


@dataclass(frozen=True)
class CommittedSnapshotBoundCompatibility:
    historical_context: CompatibilityContext
    historical_decision: CompatibilityDecision
    context: SnapshotBoundCompatibilityContext
    context_commit: CompatibilityCommitEvidence
    evidence: SnapshotBoundCompatibilityEvidence
    evidence_commit: CompatibilityCommitEvidence
    decision: SnapshotBoundCompatibilityDecision
    decision_commit: CompatibilityCommitEvidence

    def __post_init__(self) -> None:
        validate_compatibility_context(
            self.historical_context,
            evaluator=self.historical_context._evaluator,
        )
        validate_compatibility_decision(
            self.historical_decision,
            evaluator=self.historical_context._evaluator,
            context=self.historical_context,
        )
        validate_snapshot_bound_compatibility_context(self.context)
        validate_snapshot_bound_compatibility_evidence(self.evidence)
        validate_snapshot_bound_compatibility_decision(self.decision)
        for item in (
            self.context_commit,
            self.evidence_commit,
            self.decision_commit,
        ):
            validate_compatibility_commit_evidence(item)
        if (
            self.context_commit.record_identity != self.context.context_id.value
            or self.evidence_commit.record_identity != self.evidence.evidence_id.value
            or self.decision_commit.record_identity
            != self.decision.decision_id.record_id.value
        ):
            raise _fail(
                CompatibilityFailureCode.RECORD_NOT_DURABLE,
                "committed compatibility graph differs from its records",
            )


def evaluate_and_commit_snapshot_bound_compatibility(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: SnapshotBoundCompatibilityContext,
    historical_context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    index_entry: IndexEntry,
    subject_ref: HashBoundRef,
    source_evidence_refs: tuple[HashBoundRef, ...],
    valid_until_utc: datetime,
    fence_lease: CoordinatedFenceLease | None = None,
) -> CommittedSnapshotBoundCompatibility:
    require_configured_compatibility_evaluator(evaluator)
    validate_snapshot_bound_compatibility_context(
        context,
        authority_binding=evaluator._authority_binding,
    )
    validate_compatibility_context(historical_context, evaluator=evaluator)
    if context.historical_context_ref != historical_compatibility_context_ref(
        historical_context,
        evaluator=evaluator,
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "snapshot-bound context does not bind the historical evidence context",
        )
    historical_decision = evaluate_compatibility(
        evaluator=evaluator,
        context=historical_context,
        descriptor=descriptor,
        index_entry=index_entry,
    )
    validate_compatibility_decision(
        historical_decision,
        evaluator=evaluator,
        context=historical_context,
        descriptor=descriptor,
    )
    observed_at = _timestamp(
        evaluator._trusted_clock(),
        "snapshot-bound compatibility timestamp",
    )
    evidence = create_snapshot_bound_compatibility_evidence(
        evaluator=evaluator,
        context=context,
        subject_ref=subject_ref,
        dimensions=historical_decision.evidence.dimensions,
        source_evidence_refs=source_evidence_refs,
        observed_at_utc=observed_at,
    )
    decision = create_snapshot_bound_compatibility_decision(
        context=context,
        evidence=evidence,
        declaration=evaluator.declaration,
        authority_binding=evaluator._authority_binding,
        producer_actor_ids=historical_decision.independence_proof.producer_actor_ids,
        source_actor_ids=historical_decision.independence_proof.source_actor_ids,
        proposer_identity=evaluator.retriever_actor,
        executor_identity=None,
        evaluated_at_utc=observed_at,
        valid_until_utc=valid_until_utc,
    )
    store = evaluator._durability_binding.evidence_store
    context_commit = store.append(context, fence_lease=fence_lease)
    evidence_commit = store.append(evidence, fence_lease=fence_lease)
    decision_commit = store.append(decision, fence_lease=fence_lease)
    result = CommittedSnapshotBoundCompatibility(
        historical_context,
        historical_decision,
        context,
        context_commit,
        evidence,
        evidence_commit,
        decision,
        decision_commit,
    )
    result.__post_init__()
    return result


@dataclass(frozen=True)
class CommittedCompatibilityRevalidation:
    record: SnapshotBoundCompatibilityRevalidation
    commit_evidence: CompatibilityCommitEvidence

    def __post_init__(self) -> None:
        validate_snapshot_bound_compatibility_revalidation(self.record)
        validate_compatibility_commit_evidence(self.commit_evidence)
        if (
            self.commit_evidence.record_identity != self.record.revalidation_id.value
            or self.commit_evidence.record_kind
            is not CompatibilityStoredRecordKind.REVALIDATION_V2
        ):
            raise _fail(
                CompatibilityFailureCode.RECORD_NOT_DURABLE,
                "compatibility revalidation is not durably bound",
            )


def revalidate_and_commit_snapshot_bound_compatibility(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    committed: CommittedSnapshotBoundCompatibility,
    fresh_evidence: SnapshotBoundCompatibilityEvidence,
    stage: RevalidationStage,
    observed_head_refs: tuple[HashBoundRef, ...],
    prior: CommittedCompatibilityRevalidation | None,
    fence_lease: CoordinatedFenceLease | None = None,
) -> CommittedCompatibilityRevalidation:
    require_configured_compatibility_evaluator(evaluator)
    committed.__post_init__()
    validate_snapshot_bound_compatibility_evidence(fresh_evidence)
    if fresh_evidence.context_ref != committed.evidence.context_ref:
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "fresh compatibility evidence belongs to another context",
        )
    if stage is RevalidationStage.BEFORE_LOADING:
        if prior is not None:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "loading revalidation cannot have a predecessor",
            )
    elif stage is RevalidationStage.BEFORE_CONSUMPTION:
        if prior is None:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption requires exact loading revalidation",
            )
        prior.__post_init__()
        if (
            prior.record.stage is not RevalidationStage.BEFORE_LOADING
            or prior.record.outcome is not RevalidationOutcome.PASSED
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption predecessor did not pass before loading",
            )
    else:
        raise _fail(
            CompatibilityFailureCode.UNKNOWN_SCHEMA,
            "snapshot-bound revalidation stage is unknown",
        )
    record = create_snapshot_bound_compatibility_revalidation(
        context=committed.context,
        decision=committed.decision,
        evidence=fresh_evidence,
        stage=stage,
        observed_head_refs=observed_head_refs,
        evaluated_at_utc=evaluator._trusted_clock(),
    )
    evidence = evaluator._durability_binding.evidence_store.append(
        record,
        fence_lease=fence_lease,
    )
    result = CommittedCompatibilityRevalidation(record, evidence)
    result.__post_init__()
    return result


def require_snapshot_bound_compatibility_passed(
    value: CommittedSnapshotBoundCompatibility,
    *,
    evidence_store: CompatibilityEvidenceStore,
    at_utc: datetime,
) -> None:
    value.__post_init__()
    now = _timestamp(at_utc, "compatibility consumption timestamp")
    if (
        value.decision.decision_kind is not CompatibilityDecisionKind.COMPATIBLE
        or now >= value.decision.valid_until_utc
        or any(
            item.result is not DimensionResult.PASS
            for item in value.evidence.dimensions
        )
    ):
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "snapshot-bound compatibility is not current and fully passing",
        )
    evidence_store.require_inclusion(
        value.context,
        expected_evidence=value.context_commit,
    )
    evidence_store.require_inclusion(
        value.evidence,
        expected_evidence=value.evidence_commit,
    )
    evidence_store.require_inclusion(
        value.decision,
        expected_evidence=value.decision_commit,
    )
