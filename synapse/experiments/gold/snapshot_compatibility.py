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
from .patch6_adapters import (
    ConfiguredPatch6CompatibilityAdapter,
    require_patch6_compatibility_adapter,
)


def _fail(
    code: CompatibilityFailureCode,
    detail: str,
) -> CompatibilityViolation:
    return CompatibilityViolation(code, detail)


def _canonical(value: object) -> bytes:
    try:
        return canonicalize_stage4_payload(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "snapshot compatibility canonical payload is invalid",
        ) from exc


def _record(
    value: object,
    domain: IdentityDomain | None,
    name: str,
) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} must be an exact RecordId",
        )
    value.to_dict()
    if domain is not None and value.domain is not domain:
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} uses the wrong identity domain",
        )
    return value


def _ref(
    value: object,
    kind: RefKind | None,
    name: str,
) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact HashBoundRef",
        )
    result = HashBoundRef.from_dict(value.to_dict())
    if kind is not None and result.kind is not kind:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} uses the wrong ref kind",
        )
    return result


def _refs(
    value: object,
    kind: RefKind | None,
    name: str,
) -> tuple[HashBoundRef, ...]:
    if type(value) is not tuple:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact tuple",
        )
    result = tuple(_ref(item, kind, f"{name} entry") for item in value)
    keys = tuple((item.kind.value, item.ref_id, item.sha256) for item in result)
    if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} must be normalized and duplicate-free",
        )
    return result


def _timestamp(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} must be timezone-aware UTC",
        )
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").astimezone(timezone.utc).strftime(
        _UTC_FORMAT
    )


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


_SNAPSHOT_COMPATIBILITY_CAPABILITY_SEAL = object()


@dataclass(frozen=True, init=False)
class ConfiguredSnapshotCompatibility:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    patch6_adapter: ConfiguredPatch6CompatibilityAdapter
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> ConfiguredSnapshotCompatibility:
        raise TypeError("ConfiguredSnapshotCompatibility is factory-created")


def configure_snapshot_compatibility(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    patch6_adapter: ConfiguredPatch6CompatibilityAdapter,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredSnapshotCompatibility:
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    evaluator = require_patch6_compatibility_adapter(patch6_adapter)
    if (
        evaluator.authority_handle is not authority_binding.base_authority_handle
        or evaluator.declaration.configuration_id != base.configuration_id
        or evaluator.declaration.declaration_id
        != overlay.compatibility_evaluator_declaration_id
        or evaluator.declaration.evaluator_identity
        != overlay.compatibility_evaluator_identity
        or not callable(trusted_clock)
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "snapshot compatibility dependencies use another authority configuration",
        )
    result = object.__new__(ConfiguredSnapshotCompatibility)
    object.__setattr__(result, "authority_binding", authority_binding)
    object.__setattr__(result, "patch6_adapter", patch6_adapter)
    object.__setattr__(result, "trusted_clock", trusted_clock)
    object.__setattr__(
        result,
        "_configuration_snapshot",
        (authority_binding, patch6_adapter, trusted_clock),
    )
    object.__setattr__(
        result,
        "_trusted_seal",
        _SNAPSHOT_COMPATIBILITY_CAPABILITY_SEAL,
    )
    require_configured_snapshot_compatibility(result)
    return result


def require_configured_snapshot_compatibility(
    value: ConfiguredSnapshotCompatibility,
    *,
    expected: ConfiguredSnapshotCompatibility | None = None,
) -> tuple[
    ConfiguredCompatibilityEvaluator,
    Callable[[], datetime],
]:
    if (
        type(value) is not ConfiguredSnapshotCompatibility
        or getattr(value, "_trusted_seal", None)
        is not _SNAPSHOT_COMPATIBILITY_CAPABILITY_SEAL
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "snapshot compatibility capability is not factory sealed",
        )
    if expected is not None and value is not expected:
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "snapshot compatibility capability instance differs",
        )
    base, overlay = validate_knowledge_admission_authority_binding(
        value.authority_binding
    )
    evaluator = require_patch6_compatibility_adapter(value.patch6_adapter)
    snapshot = getattr(value, "_configuration_snapshot", None)
    if (
        evaluator.authority_handle is not value.authority_binding.base_authority_handle
        or evaluator.declaration.configuration_id != base.configuration_id
        or evaluator.declaration.declaration_id
        != overlay.compatibility_evaluator_declaration_id
        or evaluator.declaration.evaluator_identity
        != overlay.compatibility_evaluator_identity
        or not callable(value.trusted_clock)
        or type(snapshot) is not tuple
        or len(snapshot) != 3
        or snapshot[0] is not value.authority_binding
        or snapshot[1] is not value.patch6_adapter
        or snapshot[2] is not value.trusted_clock
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "snapshot compatibility capability changed",
        )
    return evaluator, value.trusted_clock


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
    _compatibility: ConfiguredSnapshotCompatibility
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
    compatibility: ConfiguredSnapshotCompatibility,
) -> SnapshotBoundCompatibilityContext:
    evaluator, _ = require_configured_snapshot_compatibility(compatibility)
    base, overlay = validate_knowledge_admission_authority_binding(
        compatibility.authority_binding
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
    expected_lineage = (
        LineageParentRef(
            repository_knowledge_snapshot_id,
            LineageEdgeKind.REFERENCES,
        ),
        LineageParentRef(
            atomic_boundary_id,
            LineageEdgeKind.REFERENCES,
        ),
    )
    if (
        envelope.record_id.domain is not IdentityDomain.COMPATIBILITY_CONTEXT_V2
        or envelope.lineage_parent_ids != expected_lineage
        or envelope.producer_component
        != evaluator.declaration.evaluator_component_id
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "v2 compatibility context envelope authority is invalid",
        )
    object.__setattr__(result, "context_id", envelope.record_id)
    object.__setattr__(result, "_compatibility", compatibility)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_context(
        result,
        compatibility=compatibility,
    )
    return result


def validate_snapshot_bound_compatibility_context(
    value: SnapshotBoundCompatibilityContext,
    *,
    compatibility: ConfiguredSnapshotCompatibility | None = None,
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
    bound_compatibility = getattr(value, "_compatibility", None)
    if type(bound_compatibility) is not ConfiguredSnapshotCompatibility:
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility context authority graph is unavailable",
        )
    if compatibility is not None and bound_compatibility is not compatibility:
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "v2 compatibility context uses another configured capability",
        )
    evaluator, _ = require_configured_snapshot_compatibility(
        bound_compatibility
    )
    base, overlay = validate_knowledge_admission_authority_binding(
        bound_compatibility.authority_binding
    )
    payload = _snapshot_context_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.context_id != value.envelope.record_id
        or value.context_id.domain is not IdentityDomain.COMPATIBILITY_CONTEXT_V2
        or value.base_configuration_id != base.configuration_id
        or value.knowledge_admission_configuration_id
        != overlay.configuration_id
        or value.envelope.producer_component
        != evaluator.declaration.evaluator_component_id
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                value.repository_knowledge_snapshot_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                value.atomic_boundary_id,
                LineageEdgeKind.REFERENCES,
            ),
        )
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility context identity changed",
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
    observed_at_utc: datetime
    _compatibility: ConfiguredSnapshotCompatibility
    _context: SnapshotBoundCompatibilityContext
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
        "observed_at_utc": _timestamp_text(value.observed_at_utc),
    }


def create_snapshot_bound_compatibility_evidence(
    *,
    compatibility: ConfiguredSnapshotCompatibility,
    context: SnapshotBoundCompatibilityContext,
    subject_ref: HashBoundRef,
    dimensions: tuple[CompatibilityDimensionRecord, ...],
    source_evidence_refs: tuple[HashBoundRef, ...],
    observed_at_utc: datetime,
) -> SnapshotBoundCompatibilityEvidence:
    evaluator, _ = require_configured_snapshot_compatibility(compatibility)
    validate_snapshot_bound_compatibility_context(
        context,
        compatibility=compatibility,
    )
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
    observed = _timestamp(observed_at_utc, "v2 compatibility observation")
    object.__setattr__(result, "observed_at_utc", observed)
    payload = _snapshot_evidence_v2_payload(result)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.COMPATIBILITY_EVIDENCE_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=context.envelope.run_id,
        attempt_id=context.envelope.attempt_id,
        created_at_utc=observed,
        producer_component=evaluator.declaration.evaluator_component_id,
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
    object.__setattr__(result, "_compatibility", compatibility)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_evidence(result)
    return result


def validate_snapshot_bound_compatibility_evidence(
    value: SnapshotBoundCompatibilityEvidence,
    *,
    compatibility: ConfiguredSnapshotCompatibility | None = None,
    context: SnapshotBoundCompatibilityContext | None = None,
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
    bound_compatibility = getattr(value, "_compatibility", None)
    bound_context = getattr(value, "_context", None)
    if (
        type(bound_compatibility) is not ConfiguredSnapshotCompatibility
        or type(bound_context) is not SnapshotBoundCompatibilityContext
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility evidence authority graph is unavailable",
        )
    if (
        compatibility is not None
        and bound_compatibility is not compatibility
    ) or (context is not None and bound_context is not context):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "v2 compatibility evidence uses another authority graph",
        )
    evaluator, _ = require_configured_snapshot_compatibility(
        bound_compatibility
    )
    validate_snapshot_bound_compatibility_context(
        bound_context,
        compatibility=bound_compatibility,
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
    observed = _timestamp(
        value.observed_at_utc,
        "v2 compatibility observation",
    )
    _ref(value.context_ref, RefKind.ARTIFACT, "context ref")
    _ref(value.subject_ref, None, "subject ref")
    _refs(value.source_evidence_refs, None, "source evidence refs")
    payload = _snapshot_evidence_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    expected_context_ref = _enveloped_compatibility_ref(
        bound_context.envelope,
        _snapshot_context_v2_payload(bound_context),
    )
    if (
        value.evidence_id != value.envelope.record_id
        or value.evidence_id.domain is not IdentityDomain.COMPATIBILITY_EVIDENCE_V2
        or value.context_ref != expected_context_ref
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                bound_context.context_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        or value.envelope.producer_component
        != evaluator.declaration.evaluator_component_id
        or value.envelope.run_id != bound_context.envelope.run_id
        or value.envelope.attempt_id != bound_context.envelope.attempt_id
        or value.envelope.repository_revision
        != bound_context.envelope.repository_revision
        or value.envelope.policy_version
        != bound_context.envelope.policy_version
        or value.envelope.environment_profile_id
        != bound_context.envelope.environment_profile_id
        or value.envelope.created_at_utc != observed
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
    _compatibility: ConfiguredSnapshotCompatibility
    _context: SnapshotBoundCompatibilityContext
    _evidence: SnapshotBoundCompatibilityEvidence
    _historical_decision: CompatibilityDecision
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
    compatibility: ConfiguredSnapshotCompatibility,
    context: SnapshotBoundCompatibilityContext,
    evidence: SnapshotBoundCompatibilityEvidence,
    historical_decision: CompatibilityDecision,
    producer_actor_ids: tuple[ActorIdentity, ...],
    source_actor_ids: tuple[ActorIdentity, ...],
    proposer_identity: ActorIdentity,
    executor_identity: ActorIdentity | None,
    evaluated_at_utc: datetime,
    valid_until_utc: datetime,
) -> SnapshotBoundCompatibilityDecision:
    evaluator, _ = require_configured_snapshot_compatibility(compatibility)
    declaration = evaluator.declaration
    validate_snapshot_bound_compatibility_context(
        context,
        compatibility=compatibility,
    )
    validate_snapshot_bound_compatibility_evidence(
        evidence,
        compatibility=compatibility,
        context=context,
    )
    if type(historical_decision) is not CompatibilityDecision:
        raise _fail(
            CompatibilityFailureCode.DECISION_MISMATCH,
            "v2 compatibility decision requires exact Patch 6 authority",
        )
    validate_compatibility_decision(
        historical_decision,
        evaluator=evaluator,
    )
    if historical_decision.evidence.dimensions != evidence.dimensions:
        raise _fail(
            CompatibilityFailureCode.DECISION_MISMATCH,
            "v2 compatibility evidence differs from Patch 6 authority",
        )
    validate_compatibility_evaluator_declaration(declaration)
    base, overlay = validate_knowledge_admission_authority_binding(
        compatibility.authority_binding
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
    kind = historical_decision.decision_kind
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
    object.__setattr__(result, "_compatibility", compatibility)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_evidence", evidence)
    object.__setattr__(
        result,
        "_historical_decision",
        historical_decision,
    )
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_decision(result)
    return result


def validate_snapshot_bound_compatibility_decision(
    value: SnapshotBoundCompatibilityDecision,
    *,
    compatibility: ConfiguredSnapshotCompatibility | None = None,
    context: SnapshotBoundCompatibilityContext | None = None,
    evidence: SnapshotBoundCompatibilityEvidence | None = None,
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
    bound_compatibility = getattr(value, "_compatibility", None)
    bound_context = getattr(value, "_context", None)
    bound_evidence = getattr(value, "_evidence", None)
    bound_historical_decision = getattr(
        value,
        "_historical_decision",
        None,
    )
    if (
        type(bound_compatibility) is not ConfiguredSnapshotCompatibility
        or type(bound_context) is not SnapshotBoundCompatibilityContext
        or type(bound_evidence) is not SnapshotBoundCompatibilityEvidence
        or type(bound_historical_decision) is not CompatibilityDecision
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility decision authority graph is unavailable",
        )
    if (
        (compatibility is not None and bound_compatibility is not compatibility)
        or (context is not None and bound_context is not context)
        or (evidence is not None and bound_evidence is not evidence)
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "v2 compatibility decision uses another authority graph",
        )
    evaluator, _ = require_configured_snapshot_compatibility(
        bound_compatibility
    )
    validate_snapshot_bound_compatibility_context(
        bound_context,
        compatibility=bound_compatibility,
    )
    validate_snapshot_bound_compatibility_evidence(
        bound_evidence,
        compatibility=bound_compatibility,
        context=bound_context,
    )
    validate_compatibility_decision(
        bound_historical_decision,
        evaluator=evaluator,
    )
    if (
        bound_historical_decision.evidence.dimensions
        != bound_evidence.dimensions
    ):
        raise _fail(
            CompatibilityFailureCode.DECISION_MISMATCH,
            "v2 compatibility decision Patch 6 evidence changed",
        )
    declaration = evaluator.declaration
    base, overlay = validate_knowledge_admission_authority_binding(
        bound_compatibility.authority_binding
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
        value.evaluator_declaration_id != declaration.declaration_id
        or value.base_configuration_id != base.configuration_id
        or value.knowledge_admission_configuration_id
        != overlay.configuration_id
        or value.independence_proof.authority_identity
        != declaration.evaluator_identity
        or value.independence_proof.authority_role
        is not AuthorityRole.COMPATIBILITY_EVALUATOR
        or value.independence_proof.reason_code
        is not ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "v2 compatibility decision proof changed",
        )
    expected_context_ref = _enveloped_compatibility_ref(
        bound_context.envelope,
        _snapshot_context_v2_payload(bound_context),
    )
    expected_evidence_ref = _enveloped_compatibility_ref(
        bound_evidence.envelope,
        _snapshot_evidence_v2_payload(bound_evidence),
    )
    expected_kind = bound_historical_decision.decision_kind
    decision_basis = _canonical(
        {
            "schema_version": COMPATIBILITY_DECISION_V2,
            "context_ref": expected_context_ref.to_dict(),
            "evidence_ref": expected_evidence_ref.to_dict(),
            "decision_kind": expected_kind.value,
            "evaluator_declaration_id": declaration.declaration_id.to_dict(),
            "base_configuration_id": base.configuration_id.to_dict(),
            "knowledge_admission_configuration_id": (
                overlay.configuration_id.to_dict()
            ),
        }
    )
    if (
        value.context_ref != expected_context_ref
        or value.evidence_ref != expected_evidence_ref
        or value.decision_kind is not expected_kind
        or value.independence_proof.subject_proposal_id
        != compute_proposal_id(canonical_bytes=decision_basis)
    ):
        raise _fail(
            CompatibilityFailureCode.DECISION_MISMATCH,
            "v2 compatibility decision evidence graph changed",
        )
    evaluated_at = _timestamp(
        value.evaluated_at_utc,
        "v2 compatibility decision timestamp",
    )
    valid_until = _timestamp(
        value.valid_until_utc,
        "v2 compatibility decision expiry",
    )
    if valid_until <= evaluated_at:
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
        or value.envelope.created_at_utc != value.evaluated_at_utc
        or value.envelope.producer_component
        != declaration.evaluator_component_id
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                bound_context.context_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                bound_evidence.evidence_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        or value.envelope.run_id != bound_context.envelope.run_id
        or value.envelope.attempt_id != bound_context.envelope.attempt_id
        or value.envelope.repository_revision
        != bound_context.envelope.repository_revision
        or value.envelope.policy_version
        != bound_context.envelope.policy_version
        or value.envelope.environment_profile_id
        != bound_context.envelope.environment_profile_id
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
    evidence_ref: HashBoundRef
    stage: RevalidationStage
    outcome: RevalidationOutcome
    checked_dimension_results: tuple[DimensionResult, ...]
    observed_head_refs: tuple[HashBoundRef, ...]
    evaluated_at_utc: datetime
    predecessor_revalidation_id: RecordId | None
    predecessor_revalidation_ref: HashBoundRef | None
    _compatibility: ConfiguredSnapshotCompatibility
    _context: SnapshotBoundCompatibilityContext
    _decision: SnapshotBoundCompatibilityDecision
    _evidence: SnapshotBoundCompatibilityEvidence
    _predecessor: SnapshotBoundCompatibilityRevalidation | None
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
        "evidence_ref": value.evidence_ref.to_dict(),
        "stage": value.stage.value,
        "outcome": value.outcome.value,
        "checked_dimension_results": [
            item.value for item in value.checked_dimension_results
        ],
        "observed_head_refs": [item.to_dict() for item in value.observed_head_refs],
        "evaluated_at_utc": _timestamp_text(value.evaluated_at_utc),
        "predecessor_revalidation_id": (
            None
            if value.predecessor_revalidation_id is None
            else value.predecessor_revalidation_id.to_dict()
        ),
        "predecessor_revalidation_ref": (
            None
            if value.predecessor_revalidation_ref is None
            else value.predecessor_revalidation_ref.to_dict()
        ),
    }


def create_snapshot_bound_compatibility_revalidation(
    *,
    compatibility: ConfiguredSnapshotCompatibility,
    context: SnapshotBoundCompatibilityContext,
    decision: SnapshotBoundCompatibilityDecision,
    evidence: SnapshotBoundCompatibilityEvidence,
    stage: RevalidationStage,
    observed_head_refs: tuple[HashBoundRef, ...],
    evaluated_at_utc: datetime,
    predecessor: SnapshotBoundCompatibilityRevalidation | None,
) -> SnapshotBoundCompatibilityRevalidation:
    require_configured_snapshot_compatibility(compatibility)
    validate_snapshot_bound_compatibility_context(
        context,
        compatibility=compatibility,
    )
    validate_snapshot_bound_compatibility_decision(
        decision,
        compatibility=compatibility,
        context=context,
    )
    validate_snapshot_bound_compatibility_evidence(
        evidence,
        compatibility=compatibility,
        context=context,
    )
    context_ref = _enveloped_compatibility_ref(
        context.envelope,
        _snapshot_context_v2_payload(context),
    )
    decision_ref = _enveloped_compatibility_ref(
        decision.envelope,
        _snapshot_decision_v2_payload(decision),
    )
    evidence_ref = _enveloped_compatibility_ref(
        evidence.envelope,
        _snapshot_evidence_v2_payload(evidence),
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
    if stage is RevalidationStage.BEFORE_LOADING:
        if predecessor is not None:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "loading revalidation cannot have a predecessor",
            )
    elif stage is RevalidationStage.BEFORE_CONSUMPTION:
        if type(predecessor) is not SnapshotBoundCompatibilityRevalidation:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption revalidation requires an exact loading predecessor",
            )
        validate_snapshot_bound_compatibility_revalidation(
            predecessor,
            compatibility=compatibility,
            context=context,
            decision=decision,
        )
        if (
            predecessor.stage is not RevalidationStage.BEFORE_LOADING
            or predecessor.outcome is not RevalidationOutcome.PASSED
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption predecessor did not pass before loading",
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
    object.__setattr__(result, "evidence_ref", evidence_ref)
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
    object.__setattr__(
        result,
        "predecessor_revalidation_id",
        None if predecessor is None else predecessor.revalidation_id,
    )
    object.__setattr__(
        result,
        "predecessor_revalidation_ref",
        None
        if predecessor is None
        else snapshot_bound_compatibility_artifact_ref(predecessor),
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
            LineageParentRef(
                evidence.evidence_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        + (
            ()
            if predecessor is None
            else (
                LineageParentRef(
                    predecessor.revalidation_id,
                    LineageEdgeKind.DERIVED_FROM,
                ),
            )
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "revalidation_id", envelope.record_id)
    object.__setattr__(result, "_compatibility", compatibility)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_decision", decision)
    object.__setattr__(result, "_evidence", evidence)
    object.__setattr__(result, "_predecessor", predecessor)
    object.__setattr__(result, "_trusted_seal", _V2_SEAL)
    validate_snapshot_bound_compatibility_revalidation(result)
    return result


def validate_snapshot_bound_compatibility_revalidation(
    value: SnapshotBoundCompatibilityRevalidation,
    *,
    compatibility: ConfiguredSnapshotCompatibility | None = None,
    context: SnapshotBoundCompatibilityContext | None = None,
    decision: SnapshotBoundCompatibilityDecision | None = None,
    evidence: SnapshotBoundCompatibilityEvidence | None = None,
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
    bound_compatibility = getattr(value, "_compatibility", None)
    bound_context = getattr(value, "_context", None)
    bound_decision = getattr(value, "_decision", None)
    bound_evidence = getattr(value, "_evidence", None)
    bound_predecessor = getattr(value, "_predecessor", None)
    if (
        type(bound_compatibility) is not ConfiguredSnapshotCompatibility
        or type(bound_context) is not SnapshotBoundCompatibilityContext
        or type(bound_decision) is not SnapshotBoundCompatibilityDecision
        or type(bound_evidence) is not SnapshotBoundCompatibilityEvidence
    ):
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "v2 compatibility revalidation authority graph is unavailable",
        )
    if (
        (compatibility is not None and bound_compatibility is not compatibility)
        or (context is not None and bound_context is not context)
        or (decision is not None and bound_decision is not decision)
        or (evidence is not None and bound_evidence is not evidence)
    ):
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "v2 compatibility revalidation uses another authority graph",
        )
    evaluator, _ = require_configured_snapshot_compatibility(
        bound_compatibility
    )
    validate_snapshot_bound_compatibility_context(
        bound_context,
        compatibility=bound_compatibility,
    )
    validate_snapshot_bound_compatibility_decision(
        bound_decision,
        compatibility=bound_compatibility,
        context=bound_context,
    )
    validate_snapshot_bound_compatibility_evidence(
        bound_evidence,
        compatibility=bound_compatibility,
        context=bound_context,
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
    _ref(value.context_ref, RefKind.ARTIFACT, "context ref")
    _ref(value.decision_ref, RefKind.ARTIFACT, "decision ref")
    _ref(value.evidence_ref, RefKind.ARTIFACT, "evidence ref")
    if value.stage is RevalidationStage.BEFORE_LOADING:
        if (
            bound_predecessor is not None
            or value.predecessor_revalidation_id is not None
            or value.predecessor_revalidation_ref is not None
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "loading revalidation predecessor must be absent",
            )
    elif value.stage is RevalidationStage.BEFORE_CONSUMPTION:
        if type(bound_predecessor) is not SnapshotBoundCompatibilityRevalidation:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption revalidation predecessor is unavailable",
            )
        validate_snapshot_bound_compatibility_revalidation(
            bound_predecessor,
            compatibility=bound_compatibility,
            context=bound_context,
            decision=bound_decision,
        )
        if (
            bound_predecessor.stage is not RevalidationStage.BEFORE_LOADING
            or bound_predecessor.outcome is not RevalidationOutcome.PASSED
            or value.predecessor_revalidation_id
            != bound_predecessor.revalidation_id
            or value.predecessor_revalidation_ref
            != snapshot_bound_compatibility_artifact_ref(bound_predecessor)
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "consumption revalidation predecessor changed",
            )
    expected = (
        RevalidationOutcome.PASSED
        if bound_decision.decision_kind
        is CompatibilityDecisionKind.COMPATIBLE
        and all(
            item is DimensionResult.PASS
            for item in value.checked_dimension_results
        )
        else RevalidationOutcome.FAILED
    )
    expected_results = tuple(
        item.result for item in bound_evidence.dimensions
    )
    expected_context_ref = _enveloped_compatibility_ref(
        bound_context.envelope,
        _snapshot_context_v2_payload(bound_context),
    )
    expected_decision_ref = _enveloped_compatibility_ref(
        bound_decision.envelope,
        _snapshot_decision_v2_payload(bound_decision),
    )
    expected_evidence_ref = _enveloped_compatibility_ref(
        bound_evidence.envelope,
        _snapshot_evidence_v2_payload(bound_evidence),
    )
    if (
        value.outcome is not expected
        or value.checked_dimension_results != expected_results
        or value.context_ref != expected_context_ref
        or value.decision_ref != expected_decision_ref
        or value.evidence_ref != expected_evidence_ref
    ):
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "v2 compatibility revalidation outcome changed",
        )
    _refs(value.observed_head_refs, None, "observed head refs")
    evaluated_at = _timestamp(
        value.evaluated_at_utc,
        "compatibility revalidation timestamp",
    )
    payload = _snapshot_revalidation_v2_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.revalidation_id != value.envelope.record_id
        or value.revalidation_id.domain
        is not IdentityDomain.COMPATIBILITY_REVALIDATION_V2
        or value.envelope.created_at_utc != value.evaluated_at_utc
        or value.envelope.producer_component
        != evaluator.declaration.evaluator_component_id
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                bound_context.context_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                bound_decision.envelope.record_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
            LineageParentRef(
                bound_evidence.evidence_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        + (
            ()
            if bound_predecessor is None
            else (
                LineageParentRef(
                    bound_predecessor.revalidation_id,
                    LineageEdgeKind.DERIVED_FROM,
                ),
            )
        )
        or value.envelope.run_id != bound_context.envelope.run_id
        or value.envelope.attempt_id != bound_context.envelope.attempt_id
        or value.envelope.repository_revision
        != bound_context.envelope.repository_revision
        or value.envelope.policy_version
        != bound_context.envelope.policy_version
        or value.envelope.environment_profile_id
        != bound_context.envelope.environment_profile_id
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility revalidation identity changed",
        )
def _snapshot_bound_compatibility_artifact_parts(
    value: object,
) -> tuple[str, bytes, HashBoundRef]:
    if type(value) is SnapshotBoundCompatibilityContext:
        validate_snapshot_bound_compatibility_context(value)
        identity = value.context_id.value
        payload = _snapshot_context_v2_payload(value)
    elif type(value) is SnapshotBoundCompatibilityEvidence:
        validate_snapshot_bound_compatibility_evidence(value)
        identity = value.evidence_id.value
        payload = _snapshot_evidence_v2_payload(value)
    elif type(value) is SnapshotBoundCompatibilityDecision:
        validate_snapshot_bound_compatibility_decision(value)
        identity = value.decision_id.record_id.value
        payload = _snapshot_decision_v2_payload(value)
    elif type(value) is SnapshotBoundCompatibilityRevalidation:
        validate_snapshot_bound_compatibility_revalidation(value)
        identity = value.revalidation_id.value
        payload = _snapshot_revalidation_v2_payload(value)
    else:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "current compatibility authority requires a snapshot-bound record",
        )
    raw = _enveloped_compatibility_bytes(value.envelope, payload)
    ref = _enveloped_compatibility_ref(value.envelope, payload)
    return identity, raw, ref


def snapshot_bound_compatibility_record_identity(value: object) -> str:
    identity, _, _ = _snapshot_bound_compatibility_artifact_parts(value)
    return identity


def snapshot_bound_compatibility_artifact_bytes(value: object) -> bytes:
    _, raw, _ = _snapshot_bound_compatibility_artifact_parts(value)
    return raw


def snapshot_bound_compatibility_artifact_ref(value: object) -> HashBoundRef:
    _, _, ref = _snapshot_bound_compatibility_artifact_parts(value)
    return ref


def historical_compatibility_context_ref(
    value: CompatibilityContext,
    *,
    compatibility: ConfiguredSnapshotCompatibility,
) -> HashBoundRef:
    evaluator, _ = require_configured_snapshot_compatibility(compatibility)
    validate_compatibility_context(value, evaluator=evaluator)
    raw = _canonical(value.to_dict())
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
    compatibility: ConfiguredSnapshotCompatibility
    historical_context: CompatibilityContext
    historical_decision: CompatibilityDecision
    context: SnapshotBoundCompatibilityContext
    context_commit: object
    evidence: SnapshotBoundCompatibilityEvidence
    evidence_commit: object
    decision: SnapshotBoundCompatibilityDecision
    decision_commit: object

    def __post_init__(self) -> None:
        evaluator, _ = require_configured_snapshot_compatibility(
            self.compatibility
        )
        validate_compatibility_context(
            self.historical_context,
            evaluator=evaluator,
        )
        validate_compatibility_decision(
            self.historical_decision,
            evaluator=evaluator,
            context=self.historical_context,
        )
        validate_snapshot_bound_compatibility_context(
            self.context,
            compatibility=self.compatibility,
        )
        validate_snapshot_bound_compatibility_evidence(
            self.evidence,
            compatibility=self.compatibility,
            context=self.context,
        )
        validate_snapshot_bound_compatibility_decision(
            self.decision,
            compatibility=self.compatibility,
            context=self.context,
            evidence=self.evidence,
        )
        for item in (
            self.context_commit,
            self.evidence_commit,
            self.decision_commit,
        ):
            if not callable(getattr(item, "to_dict", None)):
                raise _fail(
                    CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
                    "compatibility commit receipt is unavailable",
                )
            item.to_dict()
        if (
            getattr(self.context_commit.record_kind, "value", None)
            != "CONTEXT_V2"
            or getattr(self.evidence_commit.record_kind, "value", None)
            != "EVIDENCE_V2"
            or getattr(self.decision_commit.record_kind, "value", None)
            != "DECISION_V2"
            or
            self.context_commit.record_identity != self.context.context_id.value
            or self.evidence_commit.record_identity != self.evidence.evidence_id.value
            or self.decision_commit.record_identity
            != self.decision.decision_id.record_id.value
        ):
            raise _fail(
                CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
                "committed compatibility graph differs from its records",
            )


@dataclass(frozen=True)
class SnapshotCompatibilityEvaluation:
    compatibility: ConfiguredSnapshotCompatibility
    historical_context: CompatibilityContext
    historical_decision: CompatibilityDecision
    context: SnapshotBoundCompatibilityContext
    evidence: SnapshotBoundCompatibilityEvidence
    decision: SnapshotBoundCompatibilityDecision

    def __post_init__(self) -> None:
        evaluator, _ = require_configured_snapshot_compatibility(
            self.compatibility
        )
        validate_compatibility_context(
            self.historical_context,
            evaluator=evaluator,
        )
        validate_compatibility_decision(
            self.historical_decision,
            evaluator=evaluator,
            context=self.historical_context,
        )
        validate_snapshot_bound_compatibility_context(
            self.context,
            compatibility=self.compatibility,
        )
        validate_snapshot_bound_compatibility_evidence(
            self.evidence,
            compatibility=self.compatibility,
            context=self.context,
        )
        validate_snapshot_bound_compatibility_decision(
            self.decision,
            compatibility=self.compatibility,
            context=self.context,
            evidence=self.evidence,
        )
        if (
            self.evidence.context_ref
            != snapshot_bound_compatibility_artifact_ref(self.context)
            or self.decision.context_ref
            != snapshot_bound_compatibility_artifact_ref(self.context)
            or self.decision.evidence_ref
            != snapshot_bound_compatibility_artifact_ref(self.evidence)
        ):
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "snapshot compatibility evaluation graph changed",
            )


def evaluate_snapshot_bound_compatibility(
    *,
    compatibility: ConfiguredSnapshotCompatibility,
    context: SnapshotBoundCompatibilityContext,
    historical_context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    index_entry: IndexEntry,
    subject_ref: HashBoundRef,
    source_evidence_refs: tuple[HashBoundRef, ...],
    valid_until_utc: datetime,
) -> SnapshotCompatibilityEvaluation:
    evaluator, trusted_clock = require_configured_snapshot_compatibility(
        compatibility
    )
    validate_snapshot_bound_compatibility_context(
        context,
        compatibility=compatibility,
    )
    validate_compatibility_context(historical_context, evaluator=evaluator)
    if context.historical_context_ref != historical_compatibility_context_ref(
        historical_context,
        compatibility=compatibility,
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "snapshot-bound context does not bind the historical evidence context",
        )
    _, historical_decision = compatibility.patch6_adapter.evaluate(
        context=historical_context,
        descriptor=descriptor,
        index_entry=index_entry,
        subject_ref=subject_ref,
        source_evidence_refs=source_evidence_refs,
    )
    validate_compatibility_decision(
        historical_decision,
        evaluator=evaluator,
        context=historical_context,
        descriptor=descriptor,
    )
    observed_at = _timestamp(
        trusted_clock(),
        "snapshot-bound compatibility timestamp",
    )
    evidence = create_snapshot_bound_compatibility_evidence(
        compatibility=compatibility,
        context=context,
        subject_ref=subject_ref,
        dimensions=historical_decision.evidence.dimensions,
        source_evidence_refs=source_evidence_refs,
        observed_at_utc=observed_at,
    )
    decision = create_snapshot_bound_compatibility_decision(
        compatibility=compatibility,
        context=context,
        evidence=evidence,
        historical_decision=historical_decision,
        producer_actor_ids=historical_decision.independence_proof.producer_actor_ids,
        source_actor_ids=historical_decision.independence_proof.source_actor_ids,
        proposer_identity=evaluator.retriever_actor,
        executor_identity=None,
        evaluated_at_utc=observed_at,
        valid_until_utc=valid_until_utc,
    )
    result = SnapshotCompatibilityEvaluation(
        compatibility,
        historical_context,
        historical_decision,
        context,
        evidence,
        decision,
    )
    result.__post_init__()
    return result


@dataclass(frozen=True)
class CommittedCompatibilityRevalidation:
    compatibility: ConfiguredSnapshotCompatibility
    evidence: SnapshotBoundCompatibilityEvidence
    evidence_commit: object
    record: SnapshotBoundCompatibilityRevalidation
    commit_evidence: object

    def __post_init__(self) -> None:
        require_configured_snapshot_compatibility(self.compatibility)
        validate_snapshot_bound_compatibility_evidence(
            self.evidence,
            compatibility=self.compatibility,
        )
        validate_snapshot_bound_compatibility_revalidation(
            self.record,
            compatibility=self.compatibility,
            evidence=self.evidence,
        )
        for item in (self.evidence_commit, self.commit_evidence):
            if not callable(getattr(item, "to_dict", None)):
                raise _fail(
                    CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
                    "compatibility revalidation commit receipt is unavailable",
                )
            item.to_dict()
        if (
            self.record.evidence_ref
            != snapshot_bound_compatibility_artifact_ref(self.evidence)
            or self.evidence_commit.record_identity != self.evidence.evidence_id.value
            or getattr(self.evidence_commit.record_kind, "value", None)
            != "EVIDENCE_V2"
            or
            self.commit_evidence.record_identity != self.record.revalidation_id.value
            or getattr(self.commit_evidence.record_kind, "value", None)
            != "REVALIDATION_V2"
        ):
            raise _fail(
                CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
                "compatibility revalidation is not durably bound",
            )


@dataclass(frozen=True)
class SnapshotCompatibilityRevalidationEvaluation:
    compatibility: ConfiguredSnapshotCompatibility
    evidence: SnapshotBoundCompatibilityEvidence
    record: SnapshotBoundCompatibilityRevalidation

    def __post_init__(self) -> None:
        require_configured_snapshot_compatibility(self.compatibility)
        validate_snapshot_bound_compatibility_evidence(
            self.evidence,
            compatibility=self.compatibility,
        )
        validate_snapshot_bound_compatibility_revalidation(
            self.record,
            compatibility=self.compatibility,
            evidence=self.evidence,
        )
        if (
            self.record.evidence_ref
            != snapshot_bound_compatibility_artifact_ref(self.evidence)
        ):
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "compatibility revalidation evidence graph changed",
            )


def evaluate_snapshot_bound_compatibility_revalidation(
    *,
    compatibility: ConfiguredSnapshotCompatibility,
    committed: CommittedSnapshotBoundCompatibility,
    descriptor: CompatibilitySubjectDescriptor,
    index_entry: IndexEntry,
    subject_ref: HashBoundRef,
    source_evidence_refs: tuple[HashBoundRef, ...],
    stage: RevalidationStage,
    observed_head_refs: tuple[HashBoundRef, ...],
    prior: CommittedCompatibilityRevalidation | None,
) -> SnapshotCompatibilityRevalidationEvaluation:
    _, trusted_clock = require_configured_snapshot_compatibility(
        compatibility
    )
    committed.__post_init__()
    if committed.compatibility is not compatibility:
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH,
            "compatibility revalidation uses another configured capability",
        )
    _, fresh_historical_decision = compatibility.patch6_adapter.evaluate(
        context=committed.historical_context,
        descriptor=descriptor,
        index_entry=index_entry,
        subject_ref=subject_ref,
        source_evidence_refs=source_evidence_refs,
    )
    evaluated_at = _timestamp(
        trusted_clock(),
        "snapshot-bound compatibility revalidation timestamp",
    )
    fresh_evidence = create_snapshot_bound_compatibility_evidence(
        compatibility=compatibility,
        context=committed.context,
        subject_ref=subject_ref,
        dimensions=fresh_historical_decision.evidence.dimensions,
        source_evidence_refs=source_evidence_refs,
        observed_at_utc=evaluated_at,
    )
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
        compatibility=compatibility,
        context=committed.context,
        decision=committed.decision,
        evidence=fresh_evidence,
        stage=stage,
        observed_head_refs=observed_head_refs,
        evaluated_at_utc=evaluated_at,
        predecessor=None if prior is None else prior.record,
    )
    result = SnapshotCompatibilityRevalidationEvaluation(
        compatibility,
        fresh_evidence,
        record,
    )
    result.__post_init__()
    return result


def require_snapshot_bound_compatibility_passed(
    value: CommittedSnapshotBoundCompatibility,
    *,
    at_utc: datetime,
) -> None:
    if type(value) is not CommittedSnapshotBoundCompatibility:
        raise _fail(
            CompatibilityFailureCode.TRUSTED_OBJECT_FORGED,
            "current compatibility authority must be an exact committed v2 graph",
        )
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
