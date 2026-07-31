"""Stage 4 compatibility evidence and revalidation boundaries.

This module evaluates metadata and existing trusted records only. It does not
load or execute behavior payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
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
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    IndependenceProof,
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
    create_independence_proof,
    require_stage4_authority_handle,
    validate_history_anchor,
    validate_independence_proof,
    validate_record_id,
)
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

_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SEAL = object()
_DECLARATION_SEAL = object()
_CAPABILITY_SEAL = object()
_HOST_ABI_OBSERVATION_V1 = "synapse.stage4.host-abi/v1"
_HOST_ABI_BY_OBSERVATION_VERSION = MappingProxyType({
    _HOST_ABI_OBSERVATION_V1: CVM_HOST_ABI_VERSION,
})


class CompatibilityFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    UNKNOWN_POLICY = "UNKNOWN_POLICY"
    UNKNOWN_COMPARATOR = "UNKNOWN_COMPARATOR"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    EVALUATOR_DECLARATION_MISMATCH = "EVALUATOR_DECLARATION_MISMATCH"
    EVALUATOR_CAPABILITY_MISMATCH = "EVALUATOR_CAPABILITY_MISMATCH"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    OBSERVATION_AUTHORITY_MISMATCH = "OBSERVATION_AUTHORITY_MISMATCH"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    SUBJECT_DESCRIPTOR_MISMATCH = "SUBJECT_DESCRIPTOR_MISMATCH"
    INDEX_DESCRIPTOR_MISMATCH = "INDEX_DESCRIPTOR_MISMATCH"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    DIMENSION_MISSING = "DIMENSION_MISSING"
    DIMENSION_DUPLICATE = "DIMENSION_DUPLICATE"
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    SNAPSHOT_UNANCHORED = "SNAPSHOT_UNANCHORED"
    SNAPSHOT_DRIFT = "SNAPSHOT_DRIFT"
    LIFECYCLE_UNAVAILABLE = "LIFECYCLE_UNAVAILABLE"
    LIFECYCLE_NOT_CONSUMABLE = "LIFECYCLE_NOT_CONSUMABLE"
    BINDING_UNAVAILABLE = "BINDING_UNAVAILABLE"
    BINDING_INCOMPATIBLE = "BINDING_INCOMPATIBLE"
    ATTESTATION_UNAVAILABLE = "ATTESTATION_UNAVAILABLE"
    ATTESTATION_INVALID = "ATTESTATION_INVALID"
    TAINT_HISTORY_UNAVAILABLE = "TAINT_HISTORY_UNAVAILABLE"
    TAINT_NOT_CONSUMABLE = "TAINT_NOT_CONSUMABLE"
    MIGRATION_RELATION_UNAVAILABLE = "MIGRATION_RELATION_UNAVAILABLE"
    CONFLICT_SCAN_INCOMPLETE = "CONFLICT_SCAN_INCOMPLETE"
    CONFLICT_EVIDENCE_INVALID = "CONFLICT_EVIDENCE_INVALID"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    AUTHORITY_DECISION_INVALID = "AUTHORITY_DECISION_INVALID"
    TOCTOU_REVALIDATION_FAILED = "TOCTOU_REVALIDATION_FAILED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"


class CompatibilityViolation(ValueError):
    def __init__(self, failure_code: CompatibilityFailureCode, detail: str) -> None:
        if type(failure_code) is not CompatibilityFailureCode:
            raise TypeError("failure_code must be CompatibilityFailureCode")
        if type(detail) is not str or not detail or len(detail) > 512:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: CompatibilityFailureCode, detail: str) -> CompatibilityViolation:
    return CompatibilityViolation(code, detail)


class CompatibilityDimension(str, Enum):
    REPOSITORY_REVISION = "repository_revision"
    TASK_CONTRACT_IDENTITY = "task_contract_identity"
    BEHAVIOR_SCHEMA_AND_LANGUAGE = "behavior_schema_and_language"
    CANONICALIZATION_AND_COMPILER = "canonicalization_and_compiler"
    HOST_ABI_AND_CAPABILITIES = "host_abi_and_capabilities"
    POLICY = "policy"
    ENVIRONMENT_AND_TOOLCHAIN = "environment_and_toolchain"
    ORACLE = "oracle"
    BINDINGS = "bindings"
    ALLOWED_SCOPE = "allowed_scope"
    LIFECYCLE = "lifecycle"
    EVIDENCE_COMPLETENESS = "evidence_completeness"


REQUIRED_COMPATIBILITY_DIMENSIONS = tuple(CompatibilityDimension)


class DimensionResult(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class CompatibilityReason(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    VALUE_MISMATCH = "VALUE_MISMATCH"
    REQUIRED_EVIDENCE_MISSING = "REQUIRED_EVIDENCE_MISSING"
    VALUE_UNAVAILABLE = "VALUE_UNAVAILABLE"
    VALUE_UNKNOWN = "VALUE_UNKNOWN"
    VALUE_NOT_APPLICABLE = "VALUE_NOT_APPLICABLE"
    VALUE_EMPTY = "VALUE_EMPTY"
    VALUE_NULL = "VALUE_NULL"
    VALUE_FALSE = "VALUE_FALSE"
    VALUE_ZERO = "VALUE_ZERO"
    VALUE_REDACTED = "VALUE_REDACTED"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    TOOLCHAIN_MISMATCH = "TOOLCHAIN_MISMATCH"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    BINDING_INVALID = "BINDING_INVALID"
    ATTESTATION_INVALID = "ATTESTATION_INVALID"
    TAINT_INVALID = "TAINT_INVALID"
    LIFECYCLE_STALE = "LIFECYCLE_STALE"
    LIFECYCLE_REVOKED = "LIFECYCLE_REVOKED"
    LIFECYCLE_SUPERSEDED = "LIFECYCLE_SUPERSEDED"
    LIFECYCLE_QUARANTINED = "LIFECYCLE_QUARANTINED"
    LIFECYCLE_NOT_CONSUMABLE = "LIFECYCLE_NOT_CONSUMABLE"
    COMPLETE = "COMPLETE"


class CompatibilityValueState(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    EMPTY = "EMPTY"
    NULL = "NULL"
    FALSE = "FALSE"
    ZERO = "ZERO"
    REDACTED = "REDACTED"


class EvidenceCompleteness(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class CompatibilityDecisionKind(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE_REVISION = "INCOMPATIBLE_REVISION"
    INCOMPATIBLE_TASK_CONTRACT = "INCOMPATIBLE_TASK_CONTRACT"
    INCOMPATIBLE_BEHAVIOR_VERSION = "INCOMPATIBLE_BEHAVIOR_VERSION"
    INCOMPATIBLE_PROGRAM = "INCOMPATIBLE_PROGRAM"
    INCOMPATIBLE_HOST_ABI = "INCOMPATIBLE_HOST_ABI"
    FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
    INCOMPATIBLE_POLICY = "INCOMPATIBLE_POLICY"
    INCOMPATIBLE_ENVIRONMENT = "INCOMPATIBLE_ENVIRONMENT"
    INCOMPATIBLE_TOOLCHAIN = "INCOMPATIBLE_TOOLCHAIN"
    INCOMPATIBLE_ORACLE = "INCOMPATIBLE_ORACLE"
    INCOMPATIBLE_BINDING = "INCOMPATIBLE_BINDING"
    INCOMPATIBLE_SCOPE = "INCOMPATIBLE_SCOPE"
    STALE = "STALE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"
    QUARANTINED = "QUARANTINED"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    INSUFFICIENT_COMPATIBILITY_EVIDENCE = "INSUFFICIENT_COMPATIBILITY_EVIDENCE"


COMPATIBILITY_DECISION_PRECEDENCE = (
    CompatibilityDecisionKind.REVOKED,
    CompatibilityDecisionKind.QUARANTINED,
    CompatibilityDecisionKind.SUPERSEDED,
    CompatibilityDecisionKind.STALE,
    CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE,
    CompatibilityDecisionKind.INCOMPATIBLE_REVISION,
    CompatibilityDecisionKind.INCOMPATIBLE_TASK_CONTRACT,
    CompatibilityDecisionKind.INCOMPATIBLE_BEHAVIOR_VERSION,
    CompatibilityDecisionKind.INCOMPATIBLE_PROGRAM,
    CompatibilityDecisionKind.INCOMPATIBLE_HOST_ABI,
    CompatibilityDecisionKind.FORBIDDEN_CAPABILITY,
    CompatibilityDecisionKind.INCOMPATIBLE_POLICY,
    CompatibilityDecisionKind.INCOMPATIBLE_ENVIRONMENT,
    CompatibilityDecisionKind.INCOMPATIBLE_TOOLCHAIN,
    CompatibilityDecisionKind.INCOMPATIBLE_ORACLE,
    CompatibilityDecisionKind.INCOMPATIBLE_BINDING,
    CompatibilityDecisionKind.INCOMPATIBLE_SCOPE,
    CompatibilityDecisionKind.COMPATIBLE,
)


class RevalidationStage(str, Enum):
    BEFORE_LOADING = "BEFORE_LOADING"
    BEFORE_CONSUMPTION = "BEFORE_CONSUMPTION"


class RevalidationOutcome(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class ConflictKind(str, Enum):
    FAILED_HYPOTHESIS = "FAILED_HYPOTHESIS"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    ACTIVE_CANDIDATE_AMBIGUITY = "ACTIVE_CANDIDATE_AMBIGUITY"
    SUPERSESSION_AMBIGUITY = "SUPERSESSION_AMBIGUITY"


class ConflictDecisionKind(str, Enum):
    NO_CONFLICT_FOUND = "NO_CONFLICT_FOUND"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    SCAN_INCOMPLETE = "SCAN_INCOMPLETE"


class ConflictProposalDisposition(str, Enum):
    RECORDED_UNTRUSTED_EVIDENCE = "RECORDED_UNTRUSTED_EVIDENCE"


def _canonical(value: object) -> bytes:
    try:
        return canonicalize_stage4_payload(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "canonical payload is invalid") from exc


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} is invalid")
    return value


def _version(value: object, name: str) -> str:
    text = _safe_id(value, name)
    if "/v" not in text and not re.search(r"\d", text):
        raise _fail(CompatibilityFailureCode.UNKNOWN_PROFILE, f"{name} is not versioned")
    return text


def _sha256(value: object, name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, f"{name} is not lowercase SHA-256")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be timezone-aware UTC")
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").astimezone(timezone.utc).strftime(_UTC_FORMAT)


def _actor(value: object, name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be exact ActorIdentity")
    return ActorIdentity.from_dict(value.to_dict())


def _authority(value: object) -> AuthorityIdentity:
    if type(value) is not AuthorityIdentity:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "evaluator_identity must be exact AuthorityIdentity",
        )
    return AuthorityIdentity.from_dict(value.to_dict())


def _record(value: object, domain: IdentityDomain | None, name: str) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, f"{name} must be exact RecordId")
    value.to_dict()
    if domain is not None and value.domain is not domain:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, f"{name} uses the wrong domain")
    return value


def _ref(value: object, kind: RefKind | None, name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be exact HashBoundRef")
    result = HashBoundRef.from_dict(value.to_dict())
    if kind is not None and result.kind is not kind:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} uses the wrong ref kind")
    return result


def _refs(value: object, kind: RefKind | None, name: str) -> tuple[HashBoundRef, ...]:
    if type(value) is not tuple:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be an exact tuple")
    result = tuple(_ref(item, kind, f"{name} entry") for item in value)
    keys = tuple((item.kind.value, item.ref_id, item.sha256) for item in result)
    if len(set(keys)) != len(keys):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} contains duplicates")
    if keys != tuple(sorted(keys)):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be normalized")
    return result


def _strings(value: object, name: str, *, nonempty: bool) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be an exact tuple")
    result = tuple(_safe_id(item, f"{name} entry") for item in value)
    if nonempty and not result:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must not be empty")
    if len(set(result)) != len(result) or result != tuple(sorted(result)):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be sorted and duplicate-free")
    return result


def _scopes(value: object, name: str, *, nonempty: bool) -> tuple[str, ...]:
    result = _strings(value, name, nonempty=nonempty)
    for path in result:
        normalized = path.replace("\\", "/")
        segments = normalized.split("/")
        if (
            normalized != path
            or path.startswith("/")
            or re.match(r"^[A-Za-z]:/", path)
            or "//" in path
            or any(segment in ("", ".", "..") for segment in segments)
        ):
            raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} contains an invalid path")
    return result


def _external_inputs(value: object, name: str) -> tuple[ObservedExternalInput, ...]:
    if type(value) is not tuple:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be an exact tuple")
    result = tuple(ObservedExternalInput.from_dict(item.to_dict()) for item in value)
    if not result:
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, f"{name} must not be empty")
    if tuple(item.name for item in result) != tuple(sorted(item.name for item in result)):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} must be normalized")
    if len({item.name for item in result}) != len(result):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} contains duplicates")
    return result


def _snapshot_hash(snapshot: LibrarySnapshot) -> str:
    if type(snapshot) is not LibrarySnapshot:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "library snapshot must be exact")
    return hashlib.sha256(_canonical(snapshot.to_dict())).hexdigest()


def _same_snapshot_or_fail(library: BehaviorLibrary, trusted: LibrarySnapshot) -> LibrarySnapshot:
    if type(library) is not BehaviorLibrary:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "library must be exact BehaviorLibrary")
    try:
        verification = library.current_snapshot(trusted_prior=trusted)
    except LibraryViolation as exc:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "library snapshot verification failed") from exc
    validate_snapshot_verification(verification)
    if verification.status is SnapshotVerificationStatus.UNANCHORED:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_UNANCHORED, "library snapshot is unanchored")
    if verification.status is not SnapshotVerificationStatus.VERIFIED_SAME:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "library snapshot changed")
    return verification.snapshot


def _same_lifecycle_snapshot_or_fail(
    store: LifecycleStore,
    trusted: LifecycleSnapshot,
) -> LifecycleSnapshot:
    if type(store) is not LifecycleStore:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "lifecycle store must be exact")
    validate_lifecycle_snapshot(trusted)
    try:
        current = store.snapshot(trusted_prior=trusted)
    except LifecycleViolation as exc:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "lifecycle snapshot verification failed") from exc
    validate_lifecycle_snapshot(current)
    if current.snapshot_id.value != trusted.snapshot_id.value:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "lifecycle snapshot changed")
    return current


@dataclass(frozen=True)
class CompatibilityValue:
    state: CompatibilityValueState
    label: str | None
    sha256: str | None
    refs: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        if type(self.state) is not CompatibilityValueState:
            raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "compatibility value state is invalid")
        _refs(self.refs, None, "compatibility_value.refs")
        if self.state in {
            CompatibilityValueState.PRESENT,
            CompatibilityValueState.EMPTY,
            CompatibilityValueState.NULL,
            CompatibilityValueState.FALSE,
            CompatibilityValueState.ZERO,
        }:
            _safe_id(self.label, "compatibility_value.label")
            _sha256(self.sha256, "compatibility_value.sha256")
        elif self.label is not None or self.sha256 is not None or self.refs:
            raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "absence state cannot carry a value")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "state": self.state.value,
            "label": self.label,
            "sha256": self.sha256,
            "refs": [item.to_dict() for item in self.refs],
        }


def compatibility_value(
    *,
    label: str,
    exact_value: object,
    refs: tuple[HashBoundRef, ...] = (),
) -> CompatibilityValue:
    normalized_refs = tuple(sorted((_ref(item, None, "value ref") for item in refs), key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    if exact_value is None:
        state = CompatibilityValueState.NULL
    elif exact_value is False:
        state = CompatibilityValueState.FALSE
    elif type(exact_value) is int and exact_value == 0:
        state = CompatibilityValueState.ZERO
    elif type(exact_value) in (str, tuple, list, dict) and len(exact_value) == 0:
        state = CompatibilityValueState.EMPTY
    else:
        state = CompatibilityValueState.PRESENT
    return CompatibilityValue(
        state,
        _safe_id(label, "compatibility value label"),
        hashlib.sha256(_canonical(exact_value)).hexdigest(),
        normalized_refs,
    )


def absent_compatibility_value(state: CompatibilityValueState) -> CompatibilityValue:
    if state is CompatibilityValueState.PRESENT:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "PRESENT requires exact evidence")
    return CompatibilityValue(state, None, None, ())


def _declaration_payload(value: CompatibilityEvaluatorDeclaration) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "configuration_id": value.configuration_id.to_dict(),
        "evaluator_identity": value.evaluator_identity.to_dict(),
        "evaluator_component_id": value.evaluator_component_id,
        "evaluator_component_version": value.evaluator_component_version,
        "active_policy_input": value.active_policy_input.to_dict(),
        "allowed_behavior_kinds": [item.value for item in value.allowed_behavior_kinds],
        "allowed_binding_kinds": [item.value for item in value.allowed_binding_kinds],
        "allowed_capabilities": list(value.allowed_capabilities),
        "allowed_scope": list(value.allowed_scope),
        "required_dimensions": [item.value for item in value.required_dimensions],
        "decision_precedence": [item.value for item in value.decision_precedence],
        "comparator_profiles": [list(item) for item in value.comparator_profiles],
        "selected_set_ceiling": value.selected_set_ceiling,
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


@dataclass(frozen=True, init=False)
class CompatibilityEvaluatorDeclaration:
    schema_version: str
    configuration_id: RecordId
    evaluator_identity: AuthorityIdentity
    evaluator_component_id: str
    evaluator_component_version: str
    active_policy_input: ObservedExternalInput
    allowed_behavior_kinds: tuple[BehaviorKind, ...]
    allowed_binding_kinds: tuple[BindingKind, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_scope: tuple[str, ...]
    required_dimensions: tuple[CompatibilityDimension, ...]
    decision_precedence: tuple[CompatibilityDecisionKind, ...]
    comparator_profiles: tuple[tuple[str, str], ...]
    selected_set_ceiling: int
    created_at_utc: datetime
    declaration_id: RecordId
    _authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityEvaluatorDeclaration:
        raise TypeError("CompatibilityEvaluatorDeclaration is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_evaluator_declaration(self)
        return {**_declaration_payload(self), "declaration_id": self.declaration_id.to_dict()}


def create_compatibility_evaluator_declaration(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator_identity: AuthorityIdentity,
    evaluator_component_id: str,
    evaluator_component_version: str,
    active_policy_input: ObservedExternalInput,
    allowed_behavior_kinds: tuple[BehaviorKind, ...],
    allowed_binding_kinds: tuple[BindingKind, ...],
    allowed_capabilities: tuple[str, ...],
    allowed_scope: tuple[str, ...],
    selected_set_ceiling: int,
    trusted_clock: Callable[[], datetime],
) -> CompatibilityEvaluatorDeclaration:
    configuration = require_stage4_authority_handle(authority_handle)
    evaluator = _authority(evaluator_identity)
    blocked = {
        configuration.platform_attester_actor.value,
        configuration.builder_actor.value,
        configuration.taint_classifier_authority.value,
        configuration.taint_reviewer_authority.value,
        configuration.supersession_reviewer_authority.value,
        configuration.revocation_reviewer_authority.value,
        configuration.lifecycle_writer_actor.value,
    }
    if configuration.governing_human_authority is not None:
        blocked.add(configuration.governing_human_authority.value)
    if evaluator.value in blocked:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "evaluator overlaps configured authority")
    if not callable(trusted_clock):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    if type(active_policy_input) is not ObservedExternalInput:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "active policy input must be exact")
    policy = ObservedExternalInput.from_dict(active_policy_input.to_dict())
    if policy.version != COMPATIBILITY_POLICY_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_POLICY, "compatibility policy version is unknown")
    if type(allowed_behavior_kinds) is not tuple or any(type(item) is not BehaviorKind for item in allowed_behavior_kinds):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "allowed behavior kinds are invalid")
    behavior_kinds = tuple(sorted(allowed_behavior_kinds, key=lambda item: item.value))
    if not behavior_kinds or len(set(behavior_kinds)) != len(behavior_kinds):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "allowed behavior kinds are empty or duplicate")
    if type(allowed_binding_kinds) is not tuple or any(type(item) is not BindingKind for item in allowed_binding_kinds):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "allowed binding kinds are invalid")
    binding_kinds = tuple(sorted(allowed_binding_kinds, key=lambda item: item.value))
    if len(set(binding_kinds)) != len(binding_kinds):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "allowed binding kinds contain duplicates")
    capabilities = _strings(tuple(sorted(allowed_capabilities)), "allowed_capabilities", nonempty=False)
    scope = _scopes(tuple(sorted(allowed_scope)), "allowed_scope", nonempty=True)
    if type(selected_set_ceiling) is not int or not (1 <= selected_set_ceiling <= MAX_INDEX_ENTRIES_V1):
        raise _fail(CompatibilityFailureCode.RESOURCE_LIMIT_EXCEEDED, "selected set ceiling is invalid")
    created_at = _timestamp(trusted_clock(), "declaration timestamp")
    result = object.__new__(CompatibilityEvaluatorDeclaration)
    object.__setattr__(result, "schema_version", COMPATIBILITY_EVALUATOR_DECLARATION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "evaluator_identity", evaluator)
    object.__setattr__(result, "evaluator_component_id", _safe_id(evaluator_component_id, "evaluator component"))
    object.__setattr__(result, "evaluator_component_version", _version(evaluator_component_version, "evaluator version"))
    object.__setattr__(result, "active_policy_input", policy)
    object.__setattr__(result, "allowed_behavior_kinds", behavior_kinds)
    object.__setattr__(result, "allowed_binding_kinds", binding_kinds)
    object.__setattr__(result, "allowed_capabilities", capabilities)
    object.__setattr__(result, "allowed_scope", scope)
    object.__setattr__(result, "required_dimensions", REQUIRED_COMPATIBILITY_DIMENSIONS)
    object.__setattr__(result, "decision_precedence", COMPATIBILITY_DECISION_PRECEDENCE)
    object.__setattr__(
        result,
        "comparator_profiles",
        tuple((item.value, COMPATIBILITY_COMPARATOR_PROFILE_V1) for item in REQUIRED_COMPATIBILITY_DIMENSIONS),
    )
    object.__setattr__(result, "selected_set_ceiling", selected_set_ceiling)
    object.__setattr__(result, "created_at_utc", created_at)
    object.__setattr__(result, "_authority_handle", authority_handle)
    object.__setattr__(result, "_trusted_seal", _DECLARATION_SEAL)
    payload = _canonical(_declaration_payload(result))
    object.__setattr__(
        result,
        "declaration_id",
        compute_record_id(
            domain=IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION,
            canonical_bytes=payload,
        ),
    )
    validate_compatibility_evaluator_declaration(result)
    return result


def validate_compatibility_evaluator_declaration(value: CompatibilityEvaluatorDeclaration) -> None:
    if type(value) is not CompatibilityEvaluatorDeclaration or getattr(value, "_trusted_seal", None) is not _DECLARATION_SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "evaluator declaration is not factory sealed")
    if value.schema_version != COMPATIBILITY_EVALUATOR_DECLARATION_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "evaluator declaration schema is unknown")
    try:
        configuration = require_stage4_authority_handle(value._authority_handle)
    except (AttributeError, ValueError) as exc:
        raise _fail(CompatibilityFailureCode.WRONG_AUTHORITY_HANDLE, "declaration authority handle is unavailable") from exc
    if configuration.configuration_id != value.configuration_id:
        raise _fail(CompatibilityFailureCode.WRONG_AUTHORITY_HANDLE, "declaration authority handle changed")
    _record(value.configuration_id, IdentityDomain.AUTHORITY_CONFIGURATION, "configuration_id")
    _authority(value.evaluator_identity)
    _safe_id(value.evaluator_component_id, "evaluator component")
    _version(value.evaluator_component_version, "evaluator version")
    policy = ObservedExternalInput.from_dict(value.active_policy_input.to_dict())
    if policy.version != COMPATIBILITY_POLICY_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_POLICY, "compatibility policy changed")
    if value.allowed_behavior_kinds != tuple(sorted(value.allowed_behavior_kinds, key=lambda item: item.value)) or any(type(item) is not BehaviorKind for item in value.allowed_behavior_kinds):
        raise _fail(CompatibilityFailureCode.EVALUATOR_DECLARATION_MISMATCH, "behavior kind allowance changed")
    if value.allowed_binding_kinds != tuple(sorted(value.allowed_binding_kinds, key=lambda item: item.value)) or any(type(item) is not BindingKind for item in value.allowed_binding_kinds):
        raise _fail(CompatibilityFailureCode.EVALUATOR_DECLARATION_MISMATCH, "binding kind allowance changed")
    _strings(value.allowed_capabilities, "allowed_capabilities", nonempty=False)
    _scopes(value.allowed_scope, "allowed_scope", nonempty=True)
    if value.required_dimensions != REQUIRED_COMPATIBILITY_DIMENSIONS:
        raise _fail(CompatibilityFailureCode.DIMENSION_MISSING, "required dimension registry changed")
    if value.decision_precedence != COMPATIBILITY_DECISION_PRECEDENCE:
        raise _fail(CompatibilityFailureCode.DECISION_MISMATCH, "decision precedence changed")
    expected_comparators = tuple((item.value, COMPATIBILITY_COMPARATOR_PROFILE_V1) for item in REQUIRED_COMPATIBILITY_DIMENSIONS)
    if value.comparator_profiles != expected_comparators:
        raise _fail(CompatibilityFailureCode.UNKNOWN_COMPARATOR, "comparator registry changed")
    if type(value.selected_set_ceiling) is not int or not (1 <= value.selected_set_ceiling <= MAX_INDEX_ENTRIES_V1):
        raise _fail(CompatibilityFailureCode.RESOURCE_LIMIT_EXCEEDED, "selected set ceiling changed")
    _timestamp(value.created_at_utc, "declaration timestamp")
    payload = _canonical(_declaration_payload(value))
    _record(value.declaration_id, IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION, "declaration_id")
    try:
        validate_record_id(value.declaration_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "declaration identity mismatch") from exc


@dataclass(frozen=True, init=False)
class CompatibilitySubjectEvidence:
    descriptor_id: RecordId
    unit: SynapseBehaviorUnit
    blob: BehaviorBlob
    manifest: BehaviorManifest
    index_entry: IndexEntry
    attestation: BehaviorAttestation | None
    bindings: tuple[object, ...]
    taint_root_basis: SourceTaintProfile | TaintDerivationRecord | None
    taint_source_profiles: tuple[SourceTaintProfile, ...]
    taint_derivations: tuple[TaintDerivationRecord, ...]
    taint_decisions: tuple[TaintAuthorityDecision, ...]
    lifecycle_record: LifecycleRecord
    lifecycle_snapshot: LifecycleSnapshot
    lifecycle_context: LifecycleContext
    taint_history_anchor: HistoryAnchor
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilitySubjectEvidence:
        raise TypeError("CompatibilitySubjectEvidence is factory-created")


class ConfiguredCompatibilityEvaluator:
    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_configuration_frozen", False):
            raise AttributeError("configured compatibility evaluator is write-once")
        object.__setattr__(self, name, value)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _CAPABILITY_SEAL or kwargs or len(args) != 16:
            raise TypeError("ConfiguredCompatibilityEvaluator is factory-created")
        (
            self._authority_handle,
            self._declaration,
            self._component_id,
            self._component_version,
            self._trusted_clock,
            self._platform_observation_provider,
            self._library,
            self._lifecycle_store,
            self._attestation_store,
            self._taint_store,
            self._evidence_resolver,
            self._binding_repo_root,
            self._conflict_assessor,
            self._retriever_actor,
            self._consumer_actor,
            self._score_provider_actor,
        ) = args
        self._instance_token = object()
        self._trusted_seal = _CAPABILITY_SEAL
        self._configuration_snapshot = (
            self._authority_handle,
            self._declaration,
            self._component_id,
            self._component_version,
            self._trusted_clock,
            self._platform_observation_provider,
            self._library,
            self._lifecycle_store,
            self._attestation_store,
            self._taint_store,
            self._evidence_resolver,
            self._binding_repo_root,
            self._conflict_assessor,
            self._retriever_actor,
            self._consumer_actor,
            self._score_provider_actor,
        )
        self._configuration_frozen = True

    @property
    def declaration(self) -> CompatibilityEvaluatorDeclaration:
        require_configured_compatibility_evaluator(self)
        return self._declaration

    @property
    def authority_handle(self) -> Stage4AuthorityHandle:
        require_configured_compatibility_evaluator(self)
        return self._authority_handle

    @property
    def library(self) -> BehaviorLibrary:
        require_configured_compatibility_evaluator(self)
        return self._library

    @property
    def lifecycle_store(self) -> LifecycleStore:
        require_configured_compatibility_evaluator(self)
        return self._lifecycle_store

    @property
    def retriever_actor(self) -> ActorIdentity:
        require_configured_compatibility_evaluator(self)
        return self._retriever_actor

    @property
    def consumer_actor(self) -> ActorIdentity:
        require_configured_compatibility_evaluator(self)
        return self._consumer_actor

    @property
    def score_provider_actor(self) -> ActorIdentity:
        require_configured_compatibility_evaluator(self)
        return self._score_provider_actor


def configure_compatibility_evaluator(
    *,
    authority_handle: Stage4AuthorityHandle,
    declaration: CompatibilityEvaluatorDeclaration,
    evaluator_component_id: str,
    evaluator_component_version: str,
    trusted_clock: Callable[[], datetime],
    platform_observation_provider: Callable[[], PlatformObservedProvenance],
    library: BehaviorLibrary,
    lifecycle_store: LifecycleStore,
    attestation_store: BehaviorAttestationStore,
    taint_store: TaintHistoryStore,
    evidence_resolver: Callable[[CompatibilitySubjectDescriptor], CompatibilitySubjectEvidence],
    binding_repo_root: Path,
    conflict_assessor: Callable[[CompatibilityContext, CompatibilityDecision, CompatibilityDecision, CompatibilitySubjectDescriptor, CompatibilitySubjectDescriptor], tuple[ConflictKind | None, tuple[HashBoundRef, ...]]],
    retriever_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
    score_provider_actor: ActorIdentity,
) -> ConfiguredCompatibilityEvaluator:
    configuration = require_stage4_authority_handle(authority_handle)
    validate_compatibility_evaluator_declaration(declaration)
    if declaration.configuration_id != configuration.configuration_id or declaration._authority_handle is not authority_handle:
        raise _fail(CompatibilityFailureCode.WRONG_AUTHORITY_HANDLE, "declaration belongs to another authority configuration")
    if evaluator_component_id != declaration.evaluator_component_id or evaluator_component_version != declaration.evaluator_component_version:
        raise _fail(CompatibilityFailureCode.EVALUATOR_DECLARATION_MISMATCH, "evaluator implementation differs from declaration")
    if not callable(trusted_clock) or not callable(platform_observation_provider) or not callable(evidence_resolver) or not callable(conflict_assessor):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "configured evaluator callables are invalid")
    if not isinstance(binding_repo_root, Path) or not binding_repo_root.is_absolute():
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "binding repository root must be an exact absolute Path")
    if type(library) is not BehaviorLibrary or type(lifecycle_store) is not LifecycleStore or type(attestation_store) is not BehaviorAttestationStore or type(taint_store) is not TaintHistoryStore:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "configured evaluator consumers are invalid")
    lifecycle_store.require_handle(authority_handle)
    attestation_store.require_handle(authority_handle)
    taint_store.require_handle(authority_handle)
    retriever = _actor(retriever_actor, "retriever_actor")
    consumer = _actor(consumer_actor, "consumer_actor")
    scorer = _actor(score_provider_actor, "score_provider_actor")
    participants = {retriever.value, consumer.value, scorer.value}
    if len(participants) != 3 or declaration.evaluator_identity.value in participants:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "evaluator, retriever, consumer, and scorer must be distinct")
    result = ConfiguredCompatibilityEvaluator(
        authority_handle,
        declaration,
        evaluator_component_id,
        evaluator_component_version,
        trusted_clock,
        platform_observation_provider,
        library,
        lifecycle_store,
        attestation_store,
        taint_store,
        evidence_resolver,
        binding_repo_root,
        conflict_assessor,
        retriever,
        consumer,
        scorer,
        _seal=_CAPABILITY_SEAL,
    )
    require_configured_compatibility_evaluator(result)
    return result


def require_configured_compatibility_evaluator(
    value: ConfiguredCompatibilityEvaluator,
    *,
    expected: ConfiguredCompatibilityEvaluator | None = None,
) -> None:
    if type(value) is not ConfiguredCompatibilityEvaluator or getattr(value, "_trusted_seal", None) is not _CAPABILITY_SEAL or type(getattr(value, "_instance_token", None)) is not object:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "evaluator capability is not configured")
    if expected is not None and value is not expected:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "evaluator capability object differs")
    snapshot = getattr(value, "_configuration_snapshot", None)
    current = (
        value._authority_handle,
        value._declaration,
        value._component_id,
        value._component_version,
        value._trusted_clock,
        value._platform_observation_provider,
        value._library,
        value._lifecycle_store,
        value._attestation_store,
        value._taint_store,
        value._evidence_resolver,
        value._binding_repo_root,
        value._conflict_assessor,
        value._retriever_actor,
        value._consumer_actor,
        value._score_provider_actor,
    )
    if type(snapshot) is not tuple or len(snapshot) != len(current):
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured evaluator snapshot is unavailable")
    scalar_indexes = {2, 3, 11}
    if any(
        current[index] != snapshot[index]
        if index in scalar_indexes
        else current[index] is not snapshot[index]
        for index in range(len(current))
    ):
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured evaluator state was replaced")
    require_stage4_authority_handle(value._authority_handle)
    validate_compatibility_evaluator_declaration(value._declaration)
    if value._declaration.evaluator_component_id != value._component_id or value._declaration.evaluator_component_version != value._component_version:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured evaluator implementation changed")
    if not callable(value._trusted_clock) or not callable(value._platform_observation_provider) or not callable(value._evidence_resolver) or not callable(value._conflict_assessor):
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured evaluator callable changed")
    if not isinstance(value._binding_repo_root, Path) or not value._binding_repo_root.is_absolute():
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured binding repository changed")
    if type(value._library) is not BehaviorLibrary or type(value._lifecycle_store) is not LifecycleStore or type(value._attestation_store) is not BehaviorAttestationStore or type(value._taint_store) is not TaintHistoryStore:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "configured evaluator consumer changed")
    value._lifecycle_store.require_handle(value._authority_handle)
    value._attestation_store.require_handle(value._authority_handle)
    value._taint_store.require_handle(value._authority_handle)
    configured_actors = tuple(
        _actor(getattr(value, name), name)
        for name in ("_retriever_actor", "_consumer_actor", "_score_provider_actor")
    )
    actor_values = {item.value for item in configured_actors}
    if len(actor_values) != len(configured_actors) or value._declaration.evaluator_identity.value in actor_values:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "configured evaluator actors are no longer distinct")


def _observation_payload(value: PlatformObservedProvenance) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "configuration_id": value.configuration_id.to_dict(),
        "repository_revision": value.repository_revision.to_dict(),
        "base_revision": value.base_revision.to_dict(),
        "task_contract_ref": value.task_contract_ref.to_dict(),
        "policy_inputs": [item.to_dict() for item in value.policy_inputs],
        "environment_inputs": [item.to_dict() for item in value.environment_inputs],
        "tool_inputs": [item.to_dict() for item in value.tool_inputs],
        "source_refs": [item.to_dict() for item in value.source_refs],
        "verification_refs": [item.to_dict() for item in value.verification_refs],
        "oracle_observation": value.oracle_observation.to_dict(),
    }


def _context_payload(value: CompatibilityContext) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "configuration_id": value.configuration_id.to_dict(),
        "observation_sha256": value.observation_sha256,
        "declaration_id": value.declaration_id.to_dict(),
        "repository_revision": value.repository_revision.to_dict(),
        "base_revision": value.base_revision.to_dict(),
        "task_contract_ref": value.task_contract_ref.to_dict(),
        "policy_inputs": [item.to_dict() for item in value.policy_inputs],
        "environment_inputs": [item.to_dict() for item in value.environment_inputs],
        "tool_inputs": [item.to_dict() for item in value.tool_inputs],
        "source_refs": [item.to_dict() for item in value.source_refs],
        "verification_refs": [item.to_dict() for item in value.verification_refs],
        "oracle_observation": value.oracle_observation.to_dict(),
        "library_snapshot": value.library_snapshot.to_dict(),
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot": value.lifecycle_snapshot.to_dict(),
        "consumer_actor": value.consumer_actor.to_dict(),
        "allowed_behavior_kinds": [item.value for item in value.allowed_behavior_kinds],
        "allowed_binding_kinds": [item.value for item in value.allowed_binding_kinds],
        "allowed_capabilities": list(value.allowed_capabilities),
        "allowed_scope": list(value.allowed_scope),
        "selected_set_ceiling": value.selected_set_ceiling,
        "compatibility_policy": value.compatibility_policy,
    }


@dataclass(frozen=True, init=False)
class CompatibilityContext:
    schema_version: str
    configuration_id: RecordId
    observation_sha256: str
    declaration_id: RecordId
    repository_revision: RepositoryRevision
    base_revision: RepositoryRevision
    task_contract_ref: HashBoundRef
    policy_inputs: tuple[ObservedExternalInput, ...]
    environment_inputs: tuple[ObservedExternalInput, ...]
    tool_inputs: tuple[ObservedExternalInput, ...]
    source_refs: tuple[HashBoundRef, ...]
    verification_refs: tuple[HashBoundRef, ...]
    oracle_observation: OracleObservation
    library_snapshot: LibrarySnapshot
    library_snapshot_sha256: str
    lifecycle_snapshot: LifecycleSnapshot
    consumer_actor: ActorIdentity
    allowed_behavior_kinds: tuple[BehaviorKind, ...]
    allowed_binding_kinds: tuple[BindingKind, ...]
    allowed_capabilities: tuple[str, ...]
    allowed_scope: tuple[str, ...]
    selected_set_ceiling: int
    compatibility_policy: str
    context_id: RecordId
    _observation: PlatformObservedProvenance
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityContext:
        raise TypeError("CompatibilityContext is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_context(self, evaluator=self._evaluator)
        return {**_context_payload(self), "context_id": self.context_id.to_dict()}


def create_compatibility_context(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    authority_handle: Stage4AuthorityHandle,
    observation: PlatformObservedProvenance,
    library_snapshot: LibrarySnapshot,
    lifecycle_snapshot: LifecycleSnapshot,
    consumer_actor: ActorIdentity,
    allowed_behavior_kinds: tuple[BehaviorKind, ...] | None = None,
    allowed_binding_kinds: tuple[BindingKind, ...] | None = None,
    allowed_capabilities: tuple[str, ...] | None = None,
    allowed_scope: tuple[str, ...] | None = None,
    selected_set_ceiling: int | None = None,
) -> CompatibilityContext:
    require_configured_compatibility_evaluator(evaluator)
    require_stage4_authority_handle(authority_handle, expected_handle=evaluator._authority_handle)
    validate_platform_observed_provenance(observation, authority_handle=authority_handle)
    declaration = evaluator._declaration
    if consumer_actor is not evaluator._consumer_actor:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "consumer must be exact configured actor")
    _same_snapshot_or_fail(evaluator._library, library_snapshot)
    _same_lifecycle_snapshot_or_fail(evaluator._lifecycle_store, lifecycle_snapshot)
    if declaration.active_policy_input not in observation.policy_inputs:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "active compatibility policy is not observed")
    effective_behaviors = declaration.allowed_behavior_kinds if allowed_behavior_kinds is None else allowed_behavior_kinds
    effective_bindings = declaration.allowed_binding_kinds if allowed_binding_kinds is None else allowed_binding_kinds
    if type(effective_behaviors) is not tuple or any(type(item) is not BehaviorKind for item in effective_behaviors):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "context behavior narrowing is invalid")
    if type(effective_bindings) is not tuple or any(type(item) is not BindingKind for item in effective_bindings):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "context binding narrowing is invalid")
    behavior_tuple = tuple(sorted(effective_behaviors, key=lambda item: item.value))
    binding_tuple = tuple(sorted(effective_bindings, key=lambda item: item.value))
    capabilities = declaration.allowed_capabilities if allowed_capabilities is None else _strings(tuple(sorted(allowed_capabilities)), "allowed_capabilities", nonempty=False)
    scope = declaration.allowed_scope if allowed_scope is None else _scopes(tuple(sorted(allowed_scope)), "allowed_scope", nonempty=True)
    ceiling = declaration.selected_set_ceiling if selected_set_ceiling is None else selected_set_ceiling
    if not set(behavior_tuple) <= set(declaration.allowed_behavior_kinds) or not set(binding_tuple) <= set(declaration.allowed_binding_kinds) or not set(capabilities) <= set(declaration.allowed_capabilities) or not set(scope) <= set(declaration.allowed_scope):
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "query narrowing broadens declaration allowance")
    if type(ceiling) is not int or not (1 <= ceiling <= declaration.selected_set_ceiling):
        raise _fail(CompatibilityFailureCode.RESOURCE_LIMIT_EXCEEDED, "context selected-set ceiling is invalid")
    result = object.__new__(CompatibilityContext)
    object.__setattr__(result, "schema_version", COMPATIBILITY_CONTEXT_V1)
    object.__setattr__(result, "configuration_id", observation.configuration_id)
    object.__setattr__(result, "observation_sha256", hashlib.sha256(_canonical(_observation_payload(observation))).hexdigest())
    object.__setattr__(result, "declaration_id", declaration.declaration_id)
    object.__setattr__(result, "repository_revision", RepositoryRevision.from_dict(observation.repository_revision.to_dict()))
    object.__setattr__(result, "base_revision", RepositoryRevision.from_dict(observation.base_revision.to_dict()))
    object.__setattr__(result, "task_contract_ref", _ref(observation.task_contract_ref, RefKind.CONTRACT_CONDITION, "task contract"))
    object.__setattr__(result, "policy_inputs", _external_inputs(observation.policy_inputs, "policy_inputs"))
    object.__setattr__(result, "environment_inputs", _external_inputs(observation.environment_inputs, "environment_inputs"))
    object.__setattr__(result, "tool_inputs", _external_inputs(observation.tool_inputs, "tool_inputs"))
    object.__setattr__(result, "source_refs", _refs(tuple(observation.source_refs), RefKind.SOURCE_EVIDENCE, "source_refs"))
    object.__setattr__(result, "verification_refs", _refs(tuple(observation.verification_refs), RefKind.SOURCE_EVIDENCE, "verification_refs"))
    object.__setattr__(result, "oracle_observation", OracleObservation.from_dict(observation.oracle_observation.to_dict()))
    object.__setattr__(result, "library_snapshot", LibrarySnapshot.from_dict(library_snapshot.to_dict()))
    object.__setattr__(result, "library_snapshot_sha256", _snapshot_hash(library_snapshot))
    object.__setattr__(result, "lifecycle_snapshot", lifecycle_snapshot)
    object.__setattr__(result, "consumer_actor", evaluator._consumer_actor)
    object.__setattr__(result, "allowed_behavior_kinds", behavior_tuple)
    object.__setattr__(result, "allowed_binding_kinds", binding_tuple)
    object.__setattr__(result, "allowed_capabilities", capabilities)
    object.__setattr__(result, "allowed_scope", scope)
    object.__setattr__(result, "selected_set_ceiling", ceiling)
    object.__setattr__(result, "compatibility_policy", COMPATIBILITY_POLICY_V1)
    object.__setattr__(result, "_observation", observation)
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_context_payload(result))
    object.__setattr__(result, "context_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_CONTEXT, canonical_bytes=payload))
    validate_compatibility_context(result, evaluator=evaluator)
    return result


def validate_compatibility_context(value: CompatibilityContext, *, evaluator: ConfiguredCompatibilityEvaluator) -> None:
    require_configured_compatibility_evaluator(evaluator)
    if type(value) is not CompatibilityContext or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "compatibility context is not factory sealed")
    if value._evaluator is not evaluator:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "context belongs to another evaluator")
    if value.schema_version != COMPATIBILITY_CONTEXT_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "compatibility context schema is unknown")
    if value.configuration_id != evaluator._declaration.configuration_id or value.declaration_id != evaluator._declaration.declaration_id:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "context authority or declaration changed")
    try:
        validate_platform_observed_provenance(value._observation, authority_handle=evaluator._authority_handle)
    except (AttributeError, ValueError) as exc:
        raise _fail(CompatibilityFailureCode.OBSERVATION_AUTHORITY_MISMATCH, "context observation is unavailable or invalid") from exc
    if hashlib.sha256(_canonical(_observation_payload(value._observation))).hexdigest() != value.observation_sha256:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "context observation identity changed")
    _sha256(value.observation_sha256, "observation_sha256")
    RepositoryRevision.from_dict(value.repository_revision.to_dict())
    RepositoryRevision.from_dict(value.base_revision.to_dict())
    _ref(value.task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    _external_inputs(value.policy_inputs, "policy_inputs")
    _external_inputs(value.environment_inputs, "environment_inputs")
    _external_inputs(value.tool_inputs, "tool_inputs")
    _refs(value.source_refs, RefKind.SOURCE_EVIDENCE, "source_refs")
    _refs(value.verification_refs, RefKind.SOURCE_EVIDENCE, "verification_refs")
    OracleObservation.from_dict(value.oracle_observation.to_dict())
    if _snapshot_hash(value.library_snapshot) != value.library_snapshot_sha256:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "library snapshot hash changed")
    validate_lifecycle_snapshot(value.lifecycle_snapshot)
    if value.consumer_actor is not evaluator._consumer_actor:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "context consumer changed")
    if not set(value.allowed_behavior_kinds) <= set(evaluator._declaration.allowed_behavior_kinds) or not set(value.allowed_binding_kinds) <= set(evaluator._declaration.allowed_binding_kinds):
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "context kind allowance broadened")
    _strings(value.allowed_capabilities, "allowed_capabilities", nonempty=False)
    _scopes(value.allowed_scope, "allowed_scope", nonempty=True)
    if not set(value.allowed_capabilities) <= set(evaluator._declaration.allowed_capabilities) or not set(value.allowed_scope) <= set(evaluator._declaration.allowed_scope):
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "context allowance broadened")
    if type(value.selected_set_ceiling) is not int or not (1 <= value.selected_set_ceiling <= evaluator._declaration.selected_set_ceiling):
        raise _fail(CompatibilityFailureCode.RESOURCE_LIMIT_EXCEEDED, "context selected-set ceiling changed")
    if value.compatibility_policy != COMPATIBILITY_POLICY_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_POLICY, "context policy is unknown")
    payload = _canonical(_context_payload(value))
    _record(value.context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "context_id")
    try:
        validate_record_id(value.context_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "context identity mismatch") from exc


def _descriptor_payload(value: CompatibilitySubjectDescriptor) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "content_key": value.content_key.to_dict(),
        "manifest_id": value.manifest_id.to_dict(),
        "blob_ref": value.blob_ref.to_dict(),
        "manifest_ref": value.manifest_ref.to_dict(),
        "behavior_kind": value.behavior_kind.value,
        "behavior_schema_version": value.behavior_schema_version,
        "language_version": value.language_version,
        "canonical_profile": value.canonical_profile,
        "compiler_profile": value.compiler_profile,
        "compiler_version": value.compiler_version,
        "compiler_target": value.compiler_target,
        "bytecode_version": value.bytecode_version,
        "program_sha256": value.program_sha256,
        "host_abi": value.host_abi,
        "required_capabilities": list(value.required_capabilities),
        "repository_revision": value.repository_revision.to_dict(),
        "task_contract_ref": value.task_contract_ref.to_dict(),
        "policy_inputs": [item.to_dict() for item in value.policy_inputs],
        "environment_inputs": [item.to_dict() for item in value.environment_inputs],
        "tool_inputs": [item.to_dict() for item in value.tool_inputs],
        "oracle_binding": value.oracle_binding.to_dict(),
        "binding_refs": [item.to_dict() for item in value.binding_refs],
        "allowed_scope": list(value.allowed_scope),
        "attestation_ref": value.attestation_ref.to_dict(),
        "lifecycle_subject_ref": value.lifecycle_subject_ref.to_dict(),
        "lifecycle_context": value.lifecycle_context.to_dict(),
        "lifecycle_head_record_id": value.lifecycle_head_record_id,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "taint_subject_ref": value.taint_subject_ref.to_dict(),
        "taint_profile_id": value.taint_profile_id.to_dict(),
        "taint_history_anchor_id": value.taint_history_anchor_id.to_dict(),
        "migration_relation_refs": [item.to_dict() for item in value.migration_relation_refs],
    }


@dataclass(frozen=True, init=False)
class CompatibilitySubjectDescriptor:
    schema_version: str
    content_key: ContentKey
    manifest_id: RecordId
    blob_ref: LibraryObjectRef
    manifest_ref: LibraryObjectRef
    behavior_kind: BehaviorKind
    behavior_schema_version: str
    language_version: str
    canonical_profile: str
    compiler_profile: str | None
    compiler_version: str | None
    compiler_target: str | None
    bytecode_version: str | None
    program_sha256: str | None
    host_abi: str | None
    required_capabilities: tuple[str, ...]
    repository_revision: RepositoryRevision
    task_contract_ref: HashBoundRef
    policy_inputs: tuple[ObservedExternalInput, ...]
    environment_inputs: tuple[ObservedExternalInput, ...]
    tool_inputs: tuple[ObservedExternalInput, ...]
    oracle_binding: OracleObservation
    binding_refs: tuple[HashBoundRef, ...]
    allowed_scope: tuple[str, ...]
    attestation_ref: HashBoundRef
    lifecycle_subject_ref: HashBoundRef
    lifecycle_context: LifecycleContext
    lifecycle_head_record_id: str
    lifecycle_snapshot_id: RecordId
    taint_subject_ref: HashBoundRef
    taint_profile_id: RecordId
    taint_history_anchor_id: RecordId
    migration_relation_refs: tuple[HashBoundRef, ...]
    descriptor_id: RecordId
    _unit: SynapseBehaviorUnit
    _blob: BehaviorBlob
    _manifest: BehaviorManifest
    _index_entry: IndexEntry
    _attestation: BehaviorAttestation
    _bindings: tuple[object, ...]
    _lifecycle_record: LifecycleRecord
    _lifecycle_snapshot: LifecycleSnapshot
    _taint_root_basis: SourceTaintProfile | TaintDerivationRecord
    _taint_history_anchor: HistoryAnchor
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilitySubjectDescriptor:
        raise TypeError("CompatibilitySubjectDescriptor is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_subject_descriptor(self)
        return {**_descriptor_payload(self), "descriptor_id": self.descriptor_id.to_dict()}


def _validate_binding_records(value: object) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "binding records must be an exact tuple")
    result: list[object] = []
    for item in value:
        if type(item) not in (PythonBinding, DocumentBinding, RequirementBinding):
            raise _fail(CompatibilityFailureCode.BINDING_INCOMPATIBLE, "binding record uses an unknown exact type")
        binding_to_ref(item)
        result.append(item)
    refs = tuple(binding_to_ref(item).ref_id for item in result)
    if len(set(refs)) != len(refs) or refs != tuple(sorted(refs)):
        raise _fail(CompatibilityFailureCode.BINDING_INCOMPATIBLE, "binding records must be normalized and duplicate-free")
    return tuple(result)


def create_compatibility_subject_descriptor(
    *,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
    index_entry: IndexEntry,
    attestation: BehaviorAttestation,
    bindings: tuple[object, ...],
    lifecycle_record: LifecycleRecord,
    lifecycle_snapshot: LifecycleSnapshot,
    taint_root_basis: SourceTaintProfile | TaintDerivationRecord,
    taint_history_anchor: HistoryAnchor,
    migration_relation_refs: tuple[HashBoundRef, ...] = (),
) -> CompatibilitySubjectDescriptor:
    validate_behavior_unit(unit)
    validate_behavior_blob(blob, unit=unit)
    if type(manifest) is not BehaviorManifest:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "manifest must be exact BehaviorManifest")
    manifest.to_dict(unit=unit, blob=blob)
    if type(index_entry) is not IndexEntry:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "index entry must be exact")
    index_entry.to_dict()
    if (
        index_entry.content_key != unit.content_key.value
        or index_entry.manifest_id != manifest.manifest_id.value
        or index_entry.behavior_kind != unit.core.behavior_kind.value
    ):
        raise _fail(CompatibilityFailureCode.INDEX_DESCRIPTOR_MISMATCH, "index entry differs from exact Unit/Manifest")
    if type(attestation) is not BehaviorAttestation:
        raise _fail(CompatibilityFailureCode.ATTESTATION_INVALID, "attestation must be exact")
    validate_behavior_attestation(attestation, expected_subject_content_key=unit.content_key)
    attestation_ref = behavior_attestation_to_ref(attestation)
    normalized_bindings = _validate_binding_records(bindings)
    observed_binding_refs = tuple(sorted((binding_to_ref(item) for item in normalized_bindings), key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    manifest_binding_refs = tuple(sorted(manifest.binding_refs, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    if any(item not in manifest_binding_refs for item in observed_binding_refs):
        raise _fail(CompatibilityFailureCode.BINDING_INCOMPATIBLE, "supporting binding is absent from exact Manifest")
    validate_lifecycle_record(lifecycle_record)
    validate_lifecycle_snapshot(lifecycle_snapshot)
    if lifecycle_record.subject_ref != attestation_ref:
        raise _fail(CompatibilityFailureCode.LIFECYCLE_NOT_CONSUMABLE, "lifecycle record belongs to another subject")
    matching_head = next((item for item in lifecycle_snapshot.heads if item.subject_ref == attestation_ref and item.context == lifecycle_record.context), None)
    if matching_head is None or matching_head.record_id != lifecycle_record.record_id.value:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "lifecycle record is not the exact snapshot head")
    if type(taint_root_basis) is SourceTaintProfile:
        validate_source_taint_profile(taint_root_basis, expected_subject_ref=attestation_ref)
        taint_profile_id = taint_root_basis.profile_id
        taint_subject_ref = taint_root_basis.subject_ref
    elif type(taint_root_basis) is TaintDerivationRecord:
        if taint_root_basis.subject_ref != attestation_ref:
            raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "taint derivation belongs to another subject")
        taint_profile_id = taint_root_basis.derivation_id
        taint_subject_ref = taint_root_basis.subject_ref
    else:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "taint root basis must be exact")
    validate_history_anchor(taint_history_anchor)
    if taint_history_anchor.history_domain is not HistoryDomain.TAINT:
        raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "taint history anchor uses another domain")
    compiler_binding = manifest.compiler_binding
    if compiler_binding is not None:
        if type(compiler_binding) is not CompilerBinding:
            raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "manifest compiler binding is invalid")
        validate_compiler_binding_for_unit(unit, compiler_binding)
    allowed_scope = tuple(sorted({item.path for item in normalized_bindings}))
    result = object.__new__(CompatibilitySubjectDescriptor)
    object.__setattr__(result, "schema_version", COMPATIBILITY_SUBJECT_DESCRIPTOR_V1)
    object.__setattr__(result, "content_key", unit.content_key)
    object.__setattr__(result, "manifest_id", manifest.manifest_id)
    object.__setattr__(result, "blob_ref", LibraryObjectRef.from_dict(index_entry.blob_ref.to_dict()))
    object.__setattr__(result, "manifest_ref", LibraryObjectRef.from_dict(index_entry.manifest_ref.to_dict()))
    object.__setattr__(result, "behavior_kind", unit.core.behavior_kind)
    object.__setattr__(result, "behavior_schema_version", unit.schema_version.value)
    object.__setattr__(result, "language_version", unit.core.language_version)
    object.__setattr__(result, "canonical_profile", STAGE4_CANONICAL_PROFILE_V1)
    object.__setattr__(result, "compiler_profile", None if compiler_binding is None else compiler_binding.compiler_adapter_profile)
    object.__setattr__(result, "compiler_version", None if compiler_binding is None else compiler_binding.compiler_identity)
    object.__setattr__(result, "compiler_target", None if compiler_binding is None else compiler_binding.compiler_target)
    object.__setattr__(result, "bytecode_version", None if compiler_binding is None else compiler_binding.bytecode_version)
    object.__setattr__(result, "program_sha256", None if compiler_binding is None else compiler_binding.actual_program_hash)
    object.__setattr__(result, "host_abi", None if compiler_binding is None else compiler_binding.host_abi_version)
    object.__setattr__(result, "required_capabilities", tuple(unit.core.capability_requirements))
    object.__setattr__(result, "repository_revision", RepositoryRevision.from_dict(attestation.repository_revision.to_dict()))
    object.__setattr__(result, "task_contract_ref", HashBoundRef.from_dict(attestation.task_contract_ref.to_dict()))
    object.__setattr__(result, "policy_inputs", tuple(ObservedExternalInput.from_dict(item.to_dict()) for item in attestation.policy_inputs))
    object.__setattr__(result, "environment_inputs", tuple(ObservedExternalInput.from_dict(item.to_dict()) for item in attestation.environment_inputs))
    object.__setattr__(result, "tool_inputs", tuple(ObservedExternalInput.from_dict(item.to_dict()) for item in attestation.tool_inputs))
    object.__setattr__(result, "oracle_binding", OracleObservation.from_dict(attestation.oracle_observation.to_dict()))
    object.__setattr__(result, "binding_refs", manifest_binding_refs)
    object.__setattr__(result, "allowed_scope", allowed_scope)
    object.__setattr__(result, "attestation_ref", attestation_ref)
    object.__setattr__(result, "lifecycle_subject_ref", attestation_ref)
    object.__setattr__(result, "lifecycle_context", LifecycleContext.from_dict(lifecycle_record.context.to_dict()))
    object.__setattr__(result, "lifecycle_head_record_id", lifecycle_record.record_id.value)
    object.__setattr__(result, "lifecycle_snapshot_id", lifecycle_snapshot.snapshot_id)
    object.__setattr__(result, "taint_subject_ref", taint_subject_ref)
    object.__setattr__(result, "taint_profile_id", _record(taint_profile_id, None, "taint_profile_id"))
    object.__setattr__(result, "taint_history_anchor_id", taint_history_anchor.anchor_id)
    object.__setattr__(result, "migration_relation_refs", tuple(sorted((_ref(item, None, "migration relation") for item in migration_relation_refs), key=lambda item: (item.kind.value, item.ref_id, item.sha256))))
    object.__setattr__(result, "_unit", unit)
    object.__setattr__(result, "_blob", blob)
    object.__setattr__(result, "_manifest", manifest)
    object.__setattr__(result, "_index_entry", index_entry)
    object.__setattr__(result, "_attestation", attestation)
    object.__setattr__(result, "_bindings", normalized_bindings)
    object.__setattr__(result, "_lifecycle_record", lifecycle_record)
    object.__setattr__(result, "_lifecycle_snapshot", lifecycle_snapshot)
    object.__setattr__(result, "_taint_root_basis", taint_root_basis)
    object.__setattr__(result, "_taint_history_anchor", taint_history_anchor)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_descriptor_payload(result))
    object.__setattr__(result, "descriptor_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, canonical_bytes=payload))
    validate_compatibility_subject_descriptor(result)
    return result


def validate_compatibility_subject_descriptor(value: CompatibilitySubjectDescriptor) -> None:
    if type(value) is not CompatibilitySubjectDescriptor or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "subject descriptor is not factory sealed")
    if value.schema_version != COMPATIBILITY_SUBJECT_DESCRIPTOR_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "subject descriptor schema is unknown")
    if type(value.content_key) is not ContentKey:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "descriptor content key is invalid")
    value.content_key.to_dict()
    _record(value.manifest_id, IdentityDomain.BEHAVIOR_MANIFEST, "manifest_id")
    if type(value.blob_ref) is not LibraryObjectRef or type(value.manifest_ref) is not LibraryObjectRef:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "descriptor object refs are invalid")
    LibraryObjectRef.from_dict(value.blob_ref.to_dict())
    LibraryObjectRef.from_dict(value.manifest_ref.to_dict())
    if type(value.behavior_kind) is not BehaviorKind:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "descriptor behavior kind is invalid")
    for field, name in (
        (value.behavior_schema_version, "behavior schema"),
        (value.language_version, "language version"),
        (value.canonical_profile, "canonical profile"),
    ):
        _version(field, name)
    compiler_fields = (
        value.compiler_profile,
        value.compiler_version,
        value.compiler_target,
        value.bytecode_version,
        value.program_sha256,
        value.host_abi,
    )
    if any(item is None for item in compiler_fields) and any(item is not None for item in compiler_fields):
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "compiler facts are partially absent")
    if value.compiler_profile is not None:
        for field, name in (
            (value.compiler_profile, "compiler profile"),
            (value.compiler_version, "compiler version"),
            (value.compiler_target, "compiler target"),
            (value.bytecode_version, "bytecode version"),
            (value.host_abi, "host ABI"),
        ):
            _version(field, name)
        _sha256(value.program_sha256, "program_sha256")
    _strings(value.required_capabilities, "required_capabilities", nonempty=False)
    RepositoryRevision.from_dict(value.repository_revision.to_dict())
    _ref(value.task_contract_ref, RefKind.CONTRACT_CONDITION, "task_contract_ref")
    _external_inputs(value.policy_inputs, "policy_inputs")
    _external_inputs(value.environment_inputs, "environment_inputs")
    _external_inputs(value.tool_inputs, "tool_inputs")
    OracleObservation.from_dict(value.oracle_binding.to_dict())
    _refs(value.binding_refs, RefKind.BINDING, "binding_refs")
    _scopes(value.allowed_scope, "allowed_scope", nonempty=False)
    _ref(value.attestation_ref, RefKind.SOURCE_EVIDENCE, "attestation_ref")
    _ref(value.lifecycle_subject_ref, None, "lifecycle_subject_ref")
    value.lifecycle_context.to_dict()
    _safe_id(value.lifecycle_head_record_id, "lifecycle_head_record_id")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "lifecycle_snapshot_id")
    _ref(value.taint_subject_ref, None, "taint_subject_ref")
    _record(value.taint_profile_id, None, "taint_profile_id")
    _record(value.taint_history_anchor_id, IdentityDomain.TAINT_HISTORY_ANCHOR, "taint_history_anchor_id")
    _refs(value.migration_relation_refs, None, "migration_relation_refs")
    validate_behavior_unit(value._unit)
    validate_behavior_blob(value._blob, unit=value._unit)
    value._manifest.to_dict(unit=value._unit, blob=value._blob)
    value._index_entry.to_dict()
    validate_behavior_attestation(value._attestation, expected_subject_content_key=value._unit.content_key)
    bindings = _validate_binding_records(value._bindings)
    validate_lifecycle_record(value._lifecycle_record)
    validate_lifecycle_snapshot(value._lifecycle_snapshot)
    validate_history_anchor(value._taint_history_anchor)
    compiler = value._manifest.compiler_binding
    if compiler is not None:
        validate_compiler_binding_for_unit(value._unit, compiler)
    expected_scope = tuple(sorted({item.path for item in bindings}))
    expected_compiler = None if compiler is None else (
        compiler.compiler_adapter_profile,
        compiler.compiler_identity,
        compiler.compiler_target,
        compiler.bytecode_version,
        compiler.actual_program_hash,
        compiler.host_abi_version,
    )
    observed_compiler = (
        value.compiler_profile,
        value.compiler_version,
        value.compiler_target,
        value.bytecode_version,
        value.program_sha256,
        value.host_abi,
    )
    expected_taint_id = value._taint_root_basis.profile_id if type(value._taint_root_basis) is SourceTaintProfile else value._taint_root_basis.derivation_id
    if (
        value.content_key != value._unit.content_key
        or value.manifest_id != value._manifest.manifest_id
        or value.blob_ref != value._index_entry.blob_ref
        or value.manifest_ref != value._index_entry.manifest_ref
        or value.behavior_kind is not value._unit.core.behavior_kind
        or value.behavior_schema_version != value._unit.schema_version.value
        or value.language_version != value._unit.core.language_version
        or value.canonical_profile != STAGE4_CANONICAL_PROFILE_V1
        or observed_compiler != ((None,) * 6 if expected_compiler is None else expected_compiler)
        or value.required_capabilities != value._unit.core.capability_requirements
        or value.repository_revision != value._attestation.repository_revision
        or value.task_contract_ref != value._attestation.task_contract_ref
        or value.policy_inputs != value._attestation.policy_inputs
        or value.environment_inputs != value._attestation.environment_inputs
        or value.tool_inputs != value._attestation.tool_inputs
        or value.oracle_binding != value._attestation.oracle_observation
        or value.binding_refs != tuple(sorted(value._manifest.binding_refs, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
        or value.allowed_scope != expected_scope
        or value.attestation_ref != behavior_attestation_to_ref(value._attestation)
        or value.lifecycle_subject_ref != value._lifecycle_record.subject_ref
        or value.lifecycle_context != value._lifecycle_record.context
        or value.lifecycle_head_record_id != value._lifecycle_record.record_id.value
        or value.lifecycle_snapshot_id != value._lifecycle_snapshot.snapshot_id
        or value.taint_subject_ref != value._taint_root_basis.subject_ref
        or value.taint_profile_id != expected_taint_id
        or value.taint_history_anchor_id != value._taint_history_anchor.anchor_id
    ):
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "descriptor differs from exact supporting records")
    payload = _canonical(_descriptor_payload(value))
    _record(value.descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "descriptor_id")
    try:
        validate_record_id(value.descriptor_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "descriptor identity mismatch") from exc


def compatibility_subject_descriptor_from_dict(
    value: object,
    *,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
    index_entry: IndexEntry,
    attestation: BehaviorAttestation,
    bindings: tuple[object, ...],
    lifecycle_record: LifecycleRecord,
    lifecycle_snapshot: LifecycleSnapshot,
    taint_root_basis: SourceTaintProfile | TaintDerivationRecord,
    taint_history_anchor: HistoryAnchor,
) -> CompatibilitySubjectDescriptor:
    if type(value) is not dict:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "descriptor transport must be exact dict")
    fields = set(_descriptor_payload_fields()) | {"descriptor_id"}
    if set(value) != fields:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "descriptor transport fields differ")
    if value["schema_version"] != COMPATIBILITY_SUBJECT_DESCRIPTOR_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "descriptor transport schema is unknown")
    descriptor = create_compatibility_subject_descriptor(
        unit=unit,
        blob=blob,
        manifest=manifest,
        index_entry=index_entry,
        attestation=attestation,
        bindings=bindings,
        lifecycle_record=lifecycle_record,
        lifecycle_snapshot=lifecycle_snapshot,
        taint_root_basis=taint_root_basis,
        taint_history_anchor=taint_history_anchor,
        migration_relation_refs=_ref_list(value["migration_relation_refs"], "migration_relation_refs"),
    )
    if value != descriptor.to_dict():
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "descriptor transport differs from exact supporting records")
    return descriptor


def _descriptor_payload_fields() -> tuple[str, ...]:
    return (
        "schema_version", "content_key", "manifest_id", "blob_ref", "manifest_ref",
        "behavior_kind", "behavior_schema_version", "language_version", "canonical_profile",
        "compiler_profile", "compiler_version", "compiler_target", "bytecode_version",
        "program_sha256", "host_abi",
        "required_capabilities", "repository_revision", "task_contract_ref", "policy_inputs",
        "environment_inputs", "tool_inputs", "oracle_binding", "binding_refs", "allowed_scope",
        "attestation_ref", "lifecycle_subject_ref", "lifecycle_context", "lifecycle_head_record_id",
        "lifecycle_snapshot_id", "taint_subject_ref", "taint_profile_id", "taint_history_anchor_id",
        "migration_relation_refs",
    )


def _exact_string_list(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} transport must be list")
    return tuple(value)


def _ref_list(value: object, name: str) -> tuple[HashBoundRef, ...]:
    if type(value) is not list:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} transport must be list")
    return tuple(HashBoundRef.from_dict(item) for item in value)


def _external_input_list(value: object, name: str) -> tuple[ObservedExternalInput, ...]:
    if type(value) is not list:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, f"{name} transport must be list")
    return tuple(ObservedExternalInput.from_dict(item) for item in value)


def reconcile_index_entry(index_entry: IndexEntry, descriptor: CompatibilitySubjectDescriptor) -> None:
    if type(index_entry) is not IndexEntry:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "index entry must be exact")
    index_entry.to_dict()
    validate_compatibility_subject_descriptor(descriptor)
    if (
        index_entry.content_key != descriptor.content_key.value
        or index_entry.manifest_id != descriptor.manifest_id.value
        or index_entry.blob_ref != descriptor.blob_ref
        or index_entry.manifest_ref != descriptor.manifest_ref
        or index_entry.behavior_kind != descriptor.behavior_kind.value
    ):
        raise _fail(CompatibilityFailureCode.INDEX_DESCRIPTOR_MISMATCH, "index discovery metadata differs from descriptor")


def create_compatibility_subject_evidence(
    *,
    descriptor: CompatibilitySubjectDescriptor,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
    index_entry: IndexEntry,
    attestation: BehaviorAttestation | None,
    bindings: tuple[object, ...],
    taint_root_basis: SourceTaintProfile | TaintDerivationRecord | None,
    taint_source_profiles: tuple[SourceTaintProfile, ...],
    taint_derivations: tuple[TaintDerivationRecord, ...],
    taint_decisions: tuple[TaintAuthorityDecision, ...],
    lifecycle_record: LifecycleRecord,
    lifecycle_snapshot: LifecycleSnapshot,
    lifecycle_context: LifecycleContext,
    taint_history_anchor: HistoryAnchor,
) -> CompatibilitySubjectEvidence:
    validate_compatibility_subject_descriptor(descriptor)
    if unit is not descriptor._unit or blob is not descriptor._blob or manifest is not descriptor._manifest or index_entry is not descriptor._index_entry:
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "evidence substituted descriptor supporting objects")
    if attestation is not None and attestation is not descriptor._attestation:
        raise _fail(CompatibilityFailureCode.ATTESTATION_INVALID, "evidence substituted descriptor attestation")
    normalized_bindings = _validate_binding_records(bindings)
    if taint_root_basis is not None and taint_root_basis is not descriptor._taint_root_basis:
        raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "evidence substituted descriptor taint basis")
    if type(taint_source_profiles) is not tuple or any(type(item) is not SourceTaintProfile for item in taint_source_profiles):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "taint source profiles are invalid")
    if type(taint_derivations) is not tuple or any(type(item) is not TaintDerivationRecord for item in taint_derivations):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "taint derivations are invalid")
    if type(taint_decisions) is not tuple or any(type(item) is not TaintAuthorityDecision for item in taint_decisions):
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "taint decisions are invalid")
    if lifecycle_record is not descriptor._lifecycle_record or lifecycle_snapshot is not descriptor._lifecycle_snapshot:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "evidence substituted lifecycle support")
    if lifecycle_context != descriptor.lifecycle_context:
        raise _fail(CompatibilityFailureCode.LIFECYCLE_NOT_CONSUMABLE, "evidence lifecycle context differs")
    validate_history_anchor(taint_history_anchor)
    if taint_history_anchor.to_dict() != descriptor._taint_history_anchor.to_dict():
        raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "evidence substituted taint history anchor")
    result = object.__new__(CompatibilitySubjectEvidence)
    object.__setattr__(result, "descriptor_id", descriptor.descriptor_id)
    object.__setattr__(result, "unit", unit)
    object.__setattr__(result, "blob", blob)
    object.__setattr__(result, "manifest", manifest)
    object.__setattr__(result, "index_entry", index_entry)
    object.__setattr__(result, "attestation", attestation)
    object.__setattr__(result, "bindings", normalized_bindings)
    object.__setattr__(result, "taint_root_basis", taint_root_basis)
    object.__setattr__(result, "taint_source_profiles", tuple(taint_source_profiles))
    object.__setattr__(result, "taint_derivations", tuple(taint_derivations))
    object.__setattr__(result, "taint_decisions", tuple(taint_decisions))
    object.__setattr__(result, "lifecycle_record", lifecycle_record)
    object.__setattr__(result, "lifecycle_snapshot", lifecycle_snapshot)
    object.__setattr__(result, "lifecycle_context", LifecycleContext.from_dict(lifecycle_context.to_dict()))
    object.__setattr__(result, "taint_history_anchor", descriptor._taint_history_anchor)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    validate_compatibility_subject_evidence(result, descriptor=descriptor)
    return result


def validate_compatibility_subject_evidence(
    value: CompatibilitySubjectEvidence,
    *,
    descriptor: CompatibilitySubjectDescriptor,
) -> None:
    validate_compatibility_subject_descriptor(descriptor)
    if type(value) is not CompatibilitySubjectEvidence or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "subject evidence is not factory sealed")
    if value.descriptor_id != descriptor.descriptor_id:
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "evidence belongs to another descriptor")
    if value.unit is not descriptor._unit or value.blob is not descriptor._blob or value.manifest is not descriptor._manifest or value.index_entry is not descriptor._index_entry:
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "evidence supporting objects changed")
    validate_behavior_unit(value.unit)
    validate_behavior_blob(value.blob, unit=value.unit)
    value.manifest.to_dict(unit=value.unit, blob=value.blob)
    value.index_entry.to_dict()
    if value.attestation is not None:
        if value.attestation is not descriptor._attestation:
            raise _fail(CompatibilityFailureCode.ATTESTATION_INVALID, "evidence attestation changed")
        validate_behavior_attestation(value.attestation, expected_subject_content_key=descriptor.content_key)
    _validate_binding_records(value.bindings)
    if value.taint_root_basis is not None and value.taint_root_basis is not descriptor._taint_root_basis:
        raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "evidence taint basis changed")
    validate_lifecycle_record(value.lifecycle_record)
    validate_lifecycle_snapshot(value.lifecycle_snapshot)
    if value.lifecycle_record is not descriptor._lifecycle_record or value.lifecycle_snapshot is not descriptor._lifecycle_snapshot or value.lifecycle_context != descriptor.lifecycle_context:
        raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "evidence lifecycle support changed")
    validate_history_anchor(value.taint_history_anchor)
    if value.taint_history_anchor is not descriptor._taint_history_anchor:
        raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "evidence taint anchor changed")


def validate_loaded_compatibility_subject(
    *,
    descriptor: CompatibilitySubjectDescriptor,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    manifest: BehaviorManifest,
) -> None:
    validate_compatibility_subject_descriptor(descriptor)
    validate_behavior_unit(unit)
    validate_behavior_blob(blob, unit=unit)
    manifest.to_dict(unit=unit, blob=blob)
    if (
        unit.to_dict() != descriptor._unit.to_dict()
        or blob.to_dict(unit=unit) != descriptor._blob.to_dict(unit=descriptor._unit)
        or manifest.to_dict(unit=unit, blob=blob) != descriptor._manifest.to_dict(unit=descriptor._unit, blob=descriptor._blob)
    ):
        raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "verified loaded Unit/Blob/Manifest differ from descriptor support")
    if manifest.compiler_binding is None:
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "verified loaded Manifest lacks compiler binding")
    validate_compiler_binding_for_unit(unit, manifest.compiler_binding)


def _consume_exact_bindings(
    evaluator: ConfiguredCompatibilityEvaluator,
    evidence: CompatibilitySubjectEvidence,
    repository_revision: RepositoryRevision,
) -> None:
    try:
        for binding in evidence.bindings:
            if type(binding) is PythonBinding:
                consume_python_binding(evaluator._binding_repo_root, binding, repository_revision=repository_revision)
            elif type(binding) is DocumentBinding:
                consume_document_binding(evaluator._binding_repo_root, binding, repository_revision=repository_revision)
            elif type(binding) is RequirementBinding:
                consume_requirement_binding(evaluator._binding_repo_root, binding, repository_revision=repository_revision)
            else:
                raise _fail(CompatibilityFailureCode.BINDING_INCOMPATIBLE, "fresh binding uses an unknown exact type")
    except BindingViolation as exc:
        raise _fail(CompatibilityFailureCode.BINDING_INCOMPATIBLE, "fresh repository binding consumption failed") from exc


@dataclass(frozen=True, init=False)
class CompatibilityDimensionRecord:
    schema_version: str
    dimension: CompatibilityDimension
    producer_value: CompatibilityValue
    consumer_value: CompatibilityValue
    comparator_id: str
    comparator_version: str
    result: DimensionResult
    reason: CompatibilityReason
    evidence_refs: tuple[HashBoundRef, ...]
    record_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityDimensionRecord:
        raise TypeError("CompatibilityDimensionRecord is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_dimension_record(self)
        return {**_dimension_payload(self), "record_id": self.record_id.to_dict()}


def _dimension_payload(value: CompatibilityDimensionRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "dimension": value.dimension.value,
        "producer_value": value.producer_value.to_dict(),
        "consumer_value": value.consumer_value.to_dict(),
        "comparator_id": value.comparator_id,
        "comparator_version": value.comparator_version,
        "result": value.result.value,
        "reason": value.reason.value,
        "evidence_refs": [item.to_dict() for item in value.evidence_refs],
    }


def _make_dimension(
    dimension: CompatibilityDimension,
    producer: CompatibilityValue,
    consumer: CompatibilityValue,
    passed: bool,
    reason: CompatibilityReason,
    evidence_refs: tuple[HashBoundRef, ...] = (),
) -> CompatibilityDimensionRecord:
    result = object.__new__(CompatibilityDimensionRecord)
    object.__setattr__(result, "schema_version", COMPATIBILITY_DIMENSION_RECORD_V1)
    object.__setattr__(result, "dimension", dimension)
    object.__setattr__(result, "producer_value", producer)
    object.__setattr__(result, "consumer_value", consumer)
    object.__setattr__(result, "comparator_id", f"{COMPATIBILITY_COMPARATOR_PROFILE_V1}:{dimension.value}")
    object.__setattr__(result, "comparator_version", COMPATIBILITY_COMPARATOR_PROFILE_V1)
    object.__setattr__(result, "result", DimensionResult.PASS if passed else DimensionResult.FAIL)
    object.__setattr__(result, "reason", reason)
    object.__setattr__(result, "evidence_refs", tuple(sorted(evidence_refs, key=lambda item: (item.kind.value, item.ref_id, item.sha256))))
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_dimension_payload(result))
    object.__setattr__(result, "record_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_EVIDENCE, canonical_bytes=payload))
    validate_compatibility_dimension_record(result)
    return result


def validate_compatibility_dimension_record(value: CompatibilityDimensionRecord) -> None:
    if type(value) is not CompatibilityDimensionRecord or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "dimension record is not sealed")
    if value.schema_version != COMPATIBILITY_DIMENSION_RECORD_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "dimension schema is unknown")
    if type(value.dimension) is not CompatibilityDimension or type(value.result) is not DimensionResult or type(value.reason) is not CompatibilityReason:
        raise _fail(CompatibilityFailureCode.DIMENSION_MISMATCH, "dimension enums are invalid")
    value.producer_value.to_dict()
    value.consumer_value.to_dict()
    if value.comparator_id != f"{COMPATIBILITY_COMPARATOR_PROFILE_V1}:{value.dimension.value}" or value.comparator_version != COMPATIBILITY_COMPARATOR_PROFILE_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_COMPARATOR, "dimension comparator is unknown")
    _refs(value.evidence_refs, None, "dimension evidence refs")
    payload = _canonical(_dimension_payload(value))
    _record(value.record_id, IdentityDomain.COMPATIBILITY_EVIDENCE, "dimension record id")
    try:
        validate_record_id(value.record_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "dimension identity mismatch") from exc


@dataclass(frozen=True, init=False)
class CompatibilityEvidence:
    schema_version: str
    descriptor_id: RecordId
    context_id: RecordId
    evaluator_declaration_id: RecordId
    policy_version: str
    completeness: EvidenceCompleteness
    dimensions: tuple[CompatibilityDimensionRecord, ...]
    library_snapshot_sha256: str
    lifecycle_snapshot_id: RecordId
    created_at_utc: datetime
    evidence_core_id: RecordId
    authority_decision_id: AuthorityDecisionId
    evidence_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityEvidence:
        raise TypeError("CompatibilityEvidence is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_evidence(self)
        return {**_evidence_core_payload(self), "evidence_core_id": self.evidence_core_id.to_dict(), "authority_decision_id": self.authority_decision_id.to_dict(), "evidence_id": self.evidence_id.to_dict()}


def _evidence_core_payload(value: CompatibilityEvidence) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "descriptor_id": value.descriptor_id.to_dict(),
        "context_id": value.context_id.to_dict(),
        "evaluator_declaration_id": value.evaluator_declaration_id.to_dict(),
        "policy_version": value.policy_version,
        "completeness": value.completeness.value,
        "dimensions": [item.to_dict() for item in value.dimensions],
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


def _evidence_final_payload(value: CompatibilityEvidence) -> dict[str, object]:
    return {
        **_evidence_core_payload(value),
        "evidence_core_id": value.evidence_core_id.to_dict(),
        "authority_decision_id": value.authority_decision_id.to_dict(),
    }


@dataclass(frozen=True, init=False)
class CompatibilityDecision:
    schema_version: str
    decision_kind: CompatibilityDecisionKind
    evidence: CompatibilityEvidence
    proposal_id: ProposalId
    independence_proof: IndependenceProof
    evaluator_identity: AuthorityIdentity
    decision_id: AuthorityDecisionId
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityDecision:
        raise TypeError("CompatibilityDecision is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_decision(self, evaluator=self._evaluator)
        return {
            "schema_version": self.schema_version,
            "decision_kind": self.decision_kind.value,
            "evidence": self.evidence.to_dict(),
            "proposal_id": self.proposal_id.to_dict(),
            "independence_proof": self.independence_proof.to_dict(),
            "evaluator_identity": self.evaluator_identity.to_dict(),
            "decision_id": self.decision_id.to_dict(),
        }


def _decision_identity_payload(kind: CompatibilityDecisionKind, evidence_core_id: RecordId, evaluator: CompatibilityEvaluatorDeclaration) -> dict[str, object]:
    return {
        "schema_version": COMPATIBILITY_DECISION_V1,
        "decision_kind": kind.value,
        "evidence_core_id": evidence_core_id.to_dict(),
        "evaluator_declaration_id": evaluator.declaration_id.to_dict(),
        "evaluator_identity": evaluator.evaluator_identity.to_dict(),
        "policy_version": COMPATIBILITY_POLICY_V1,
    }


def _expected_decision_kind(dimensions: tuple[CompatibilityDimensionRecord, ...]) -> CompatibilityDecisionKind:
    failures = {item.dimension: item.reason for item in dimensions if item.result is DimensionResult.FAIL}
    lifecycle = failures.get(CompatibilityDimension.LIFECYCLE)
    if lifecycle is CompatibilityReason.LIFECYCLE_REVOKED:
        return CompatibilityDecisionKind.REVOKED
    if lifecycle is CompatibilityReason.LIFECYCLE_QUARANTINED:
        return CompatibilityDecisionKind.QUARANTINED
    if lifecycle is CompatibilityReason.LIFECYCLE_SUPERSEDED:
        return CompatibilityDecisionKind.SUPERSEDED
    if lifecycle is CompatibilityReason.LIFECYCLE_STALE:
        return CompatibilityDecisionKind.STALE
    if failures.get(CompatibilityDimension.BINDINGS) is CompatibilityReason.BINDING_INVALID:
        return CompatibilityDecisionKind.INCOMPATIBLE_BINDING
    if CompatibilityDimension.EVIDENCE_COMPLETENESS in failures:
        return CompatibilityDecisionKind.INSUFFICIENT_COMPATIBILITY_EVIDENCE
    mapping = (
        (CompatibilityDimension.REPOSITORY_REVISION, CompatibilityDecisionKind.INCOMPATIBLE_REVISION),
        (CompatibilityDimension.TASK_CONTRACT_IDENTITY, CompatibilityDecisionKind.INCOMPATIBLE_TASK_CONTRACT),
        (CompatibilityDimension.BEHAVIOR_SCHEMA_AND_LANGUAGE, CompatibilityDecisionKind.INCOMPATIBLE_BEHAVIOR_VERSION),
        (CompatibilityDimension.CANONICALIZATION_AND_COMPILER, CompatibilityDecisionKind.INCOMPATIBLE_PROGRAM),
        (CompatibilityDimension.HOST_ABI_AND_CAPABILITIES, CompatibilityDecisionKind.FORBIDDEN_CAPABILITY if failures.get(CompatibilityDimension.HOST_ABI_AND_CAPABILITIES) is CompatibilityReason.FORBIDDEN_CAPABILITY else CompatibilityDecisionKind.INCOMPATIBLE_HOST_ABI),
        (CompatibilityDimension.POLICY, CompatibilityDecisionKind.INCOMPATIBLE_POLICY),
        (CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN, CompatibilityDecisionKind.INCOMPATIBLE_ENVIRONMENT if failures.get(CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN) is CompatibilityReason.ENVIRONMENT_MISMATCH else CompatibilityDecisionKind.INCOMPATIBLE_TOOLCHAIN),
        (CompatibilityDimension.ORACLE, CompatibilityDecisionKind.INCOMPATIBLE_ORACLE),
        (CompatibilityDimension.BINDINGS, CompatibilityDecisionKind.INCOMPATIBLE_BINDING),
        (CompatibilityDimension.ALLOWED_SCOPE, CompatibilityDecisionKind.INCOMPATIBLE_SCOPE),
    )
    for dimension, kind in mapping:
        if dimension in failures:
            return kind
    return CompatibilityDecisionKind.COMPATIBLE


def _find_external(items: tuple[ObservedExternalInput, ...], name: str) -> ObservedExternalInput | None:
    return next((item for item in items if item.name == name), None)


def _host_abi_from_observation(value: ObservedExternalInput | None) -> str | None:
    if value is None:
        return None
    snapshot = ObservedExternalInput.from_dict(value.to_dict())
    if snapshot.kind is not ExternalInputKind.ENVIRONMENT or snapshot.name != "host-abi":
        return None
    return _HOST_ABI_BY_OBSERVATION_VERSION.get(snapshot.version)


def _validate_evaluator_actor_independence(
    evaluator: ConfiguredCompatibilityEvaluator,
    *,
    context: CompatibilityContext | None = None,
    producer_actors: tuple[ActorIdentity, ...] = (),
    source_actors: tuple[ActorIdentity, ...] = (),
    derived_actors: tuple[ActorIdentity, ...] = (),
    proposer: ActorIdentity | None = None,
    executor: ActorIdentity | None = None,
) -> None:
    authority = evaluator._declaration.evaluator_identity.value
    participants = {
        evaluator._retriever_actor.value,
        evaluator._consumer_actor.value,
        evaluator._score_provider_actor.value,
        *(item.value for item in producer_actors),
        *(item.value for item in source_actors),
        *(item.value for item in derived_actors),
    }
    if context is not None:
        participants.add(context.oracle_observation.oracle_identity.value)
    if proposer is not None:
        participants.add(proposer.value)
    if executor is not None:
        participants.add(executor.value)
    if authority in participants:
        raise _fail(
            CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "compatibility evaluator overlaps a participating actor",
        )


@dataclass(frozen=True)
class _DimensionFacts:
    dimensions: tuple[CompatibilityDimensionRecord, ...]
    producer_actors: tuple[ActorIdentity, ...]
    source_actors: tuple[ActorIdentity, ...]
    derived_actors: tuple[ActorIdentity, ...]


def _dimension_facts(
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    subject_evidence: CompatibilitySubjectEvidence,
) -> _DimensionFacts:
    validate_compatibility_subject_evidence(subject_evidence, descriptor=descriptor)
    refs = tuple(sorted({item for item in (*context.verification_refs, descriptor.attestation_ref, *descriptor.binding_refs)}, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    dimensions: list[CompatibilityDimensionRecord] = []

    def exact_dimension(dimension: CompatibilityDimension, producer_data: object, consumer_data: object, *, evidence_refs: tuple[HashBoundRef, ...] = refs) -> None:
        producer = compatibility_value(label=f"producer-{dimension.value}", exact_value=producer_data, refs=evidence_refs)
        consumer = compatibility_value(label=f"consumer-{dimension.value}", exact_value=consumer_data, refs=evidence_refs)
        passed = producer.sha256 == consumer.sha256
        dimensions.append(_make_dimension(dimension, producer, consumer, passed, CompatibilityReason.EXACT_MATCH if passed else CompatibilityReason.VALUE_MISMATCH, evidence_refs))

    exact_dimension(CompatibilityDimension.REPOSITORY_REVISION, descriptor.repository_revision.to_dict(), context.repository_revision.to_dict())
    exact_dimension(CompatibilityDimension.TASK_CONTRACT_IDENTITY, descriptor.task_contract_ref.to_dict(), context.task_contract_ref.to_dict())
    exact_dimension(
        CompatibilityDimension.BEHAVIOR_SCHEMA_AND_LANGUAGE,
        [descriptor.behavior_schema_version, descriptor.language_version],
        [SchemaVersion.BEHAVIOR_UNIT_V1.value, LANGUAGE_VERSION],
    )
    compiler_binding = subject_evidence.manifest.compiler_binding
    compiler_available = compiler_binding is not None
    if compiler_binding is None:
        dimensions.append(_make_dimension(
            CompatibilityDimension.CANONICALIZATION_AND_COMPILER,
            absent_compatibility_value(CompatibilityValueState.MISSING),
            absent_compatibility_value(CompatibilityValueState.MISSING),
            False,
            CompatibilityReason.REQUIRED_EVIDENCE_MISSING,
            refs,
        ))
    else:
        validate_compiler_binding_for_unit(subject_evidence.unit, compiler_binding)
        exact_dimension(
            CompatibilityDimension.CANONICALIZATION_AND_COMPILER,
            [descriptor.canonical_profile, descriptor.compiler_profile, descriptor.compiler_version, descriptor.compiler_target, descriptor.bytecode_version, descriptor.program_sha256],
            [STAGE4_CANONICAL_PROFILE_V1, compiler_binding.compiler_adapter_profile, compiler_binding.compiler_identity, compiler_binding.compiler_target, compiler_binding.bytecode_version, compiler_binding.actual_program_hash],
        )
    producer_host_input = _find_external(descriptor.environment_inputs, "host-abi")
    consumer_host_input = _find_external(context.environment_inputs, "host-abi")
    observed_producer_abi = _host_abi_from_observation(producer_host_input)
    observed_consumer_abi = _host_abi_from_observation(consumer_host_input)
    host_refs = tuple(sorted({
        *refs,
        *(item.ref for item in (producer_host_input, consumer_host_input) if item is not None),
    }, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    producer_host_refs = tuple(sorted({
        *refs,
        *(item.ref for item in (producer_host_input,) if item is not None),
    }, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    consumer_host_refs = tuple(sorted({
        *refs,
        *(item.ref for item in (consumer_host_input,) if item is not None),
    }, key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
    producer = compatibility_value(
        label="producer-host-capabilities",
        exact_value=[
            descriptor.host_abi,
            None if producer_host_input is None else producer_host_input.to_dict(),
            list(descriptor.required_capabilities),
        ],
        refs=producer_host_refs,
    )
    consumer = compatibility_value(
        label="consumer-host-capabilities",
        exact_value=[
            observed_consumer_abi,
            None if consumer_host_input is None else consumer_host_input.to_dict(),
            list(context.allowed_capabilities),
        ],
        refs=consumer_host_refs,
    )
    host_matches = (
        descriptor.host_abi is not None
        and observed_producer_abi == descriptor.host_abi
        and observed_consumer_abi == descriptor.host_abi
    )
    capabilities_allowed = set(descriptor.required_capabilities) <= set(context.allowed_capabilities)
    dimensions.append(_make_dimension(CompatibilityDimension.HOST_ABI_AND_CAPABILITIES, producer, consumer, host_matches and capabilities_allowed, CompatibilityReason.EXACT_MATCH if host_matches and capabilities_allowed else (CompatibilityReason.FORBIDDEN_CAPABILITY if not capabilities_allowed else CompatibilityReason.VALUE_MISMATCH), host_refs))
    exact_dimension(CompatibilityDimension.POLICY, [item.to_dict() for item in descriptor.policy_inputs], [item.to_dict() for item in context.policy_inputs])
    producer_environment = tuple(item for item in descriptor.environment_inputs if item.name != "host-abi")
    consumer_environment = tuple(item for item in context.environment_inputs if item.name != "host-abi")
    environment_present = bool(producer_environment and consumer_environment)
    tools_present = bool(descriptor.tool_inputs and context.tool_inputs)
    environment_matches = producer_environment == consumer_environment
    tools_match = descriptor.tool_inputs == context.tool_inputs
    environment_tool_producer = compatibility_value(
        label="producer-environment-toolchain",
        exact_value=[[item.to_dict() for item in producer_environment], [item.to_dict() for item in descriptor.tool_inputs]],
        refs=refs,
    ) if environment_present and tools_present else absent_compatibility_value(CompatibilityValueState.MISSING)
    environment_tool_consumer = compatibility_value(
        label="consumer-environment-toolchain",
        exact_value=[[item.to_dict() for item in consumer_environment], [item.to_dict() for item in context.tool_inputs]],
        refs=refs,
    ) if environment_present and tools_present else absent_compatibility_value(CompatibilityValueState.MISSING)
    environment_tool_passed = environment_present and tools_present and environment_matches and tools_match
    environment_tool_reason = (
        CompatibilityReason.EXACT_MATCH
        if environment_tool_passed
        else CompatibilityReason.REQUIRED_EVIDENCE_MISSING
        if not environment_present or not tools_present
        else CompatibilityReason.ENVIRONMENT_MISMATCH
        if not environment_matches
        else CompatibilityReason.TOOLCHAIN_MISMATCH
    )
    dimensions.append(_make_dimension(
        CompatibilityDimension.ENVIRONMENT_AND_TOOLCHAIN,
        environment_tool_producer,
        environment_tool_consumer,
        environment_tool_passed,
        environment_tool_reason,
        refs,
    ))
    exact_dimension(CompatibilityDimension.ORACLE, descriptor.oracle_binding.to_dict(), context.oracle_observation.to_dict())

    binding_ok = False
    binding_complete = False
    binding_missing = False
    binding_invalid = False
    observed_binding_refs: tuple[HashBoundRef, ...] = ()
    try:
        observed_binding_refs = tuple(sorted((binding_to_ref(item) for item in subject_evidence.bindings), key=lambda item: (item.kind.value, item.ref_id, item.sha256)))
        expected_set = set(descriptor.binding_refs)
        observed_set = set(observed_binding_refs)
        binding_kinds = {item.binding_kind for item in subject_evidence.bindings}
        revisions_match = all(
            type(item.repository_revision) is RepositoryRevision
            and item.repository_revision.kind is RepositoryRevisionKind.GIT_COMMIT
            and item.repository_revision.git_sha is not None
            and item.repository_revision == descriptor.repository_revision
            and item.repository_revision == descriptor._attestation.repository_revision
            and item.repository_revision == context.repository_revision
            for item in subject_evidence.bindings
        )
        binding_kinds_allowed = binding_kinds <= set(context.allowed_binding_kinds)
        binding_ok = observed_binding_refs == descriptor.binding_refs and revisions_match and binding_kinds_allowed
        binding_missing = observed_set < expected_set and revisions_match and binding_kinds_allowed
        binding_invalid = not binding_ok and not binding_missing
        binding_complete = binding_ok
    except ValueError:
        binding_invalid = True
    binding_evidence_refs = {
        (item.kind.value, item.ref_id, item.sha256): item
        for item in (*descriptor.binding_refs, *observed_binding_refs)
    }
    producer_bindings = compatibility_value(label="producer-bindings", exact_value=[item.to_dict() for item in descriptor.binding_refs], refs=descriptor.binding_refs)
    consumer_bindings = (
        compatibility_value(label="consumer-bindings", exact_value=[item.to_dict() for item in observed_binding_refs], refs=observed_binding_refs)
        if observed_binding_refs
        else absent_compatibility_value(CompatibilityValueState.MISSING)
    )
    dimensions.append(_make_dimension(
        CompatibilityDimension.BINDINGS,
        producer_bindings,
        consumer_bindings,
        binding_ok,
        CompatibilityReason.EXACT_MATCH if binding_ok else (CompatibilityReason.REQUIRED_EVIDENCE_MISSING if binding_missing else CompatibilityReason.BINDING_INVALID),
        tuple(binding_evidence_refs[key] for key in sorted(binding_evidence_refs)),
    ))

    scope_ok = set(descriptor.allowed_scope) <= set(context.allowed_scope)
    dimensions.append(_make_dimension(
        CompatibilityDimension.ALLOWED_SCOPE,
        compatibility_value(label="producer-scope", exact_value=list(descriptor.allowed_scope)),
        compatibility_value(label="consumer-scope", exact_value=list(context.allowed_scope)),
        scope_ok,
        CompatibilityReason.EXACT_MATCH if scope_ok else CompatibilityReason.SCOPE_EXPANSION,
    ))

    lifecycle_state: LifecycleState | None = None
    lifecycle_available = False
    lifecycle_ok = False
    lifecycle_reason = CompatibilityReason.LIFECYCLE_NOT_CONSUMABLE
    try:
        if descriptor.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id:
            raise _fail(CompatibilityFailureCode.SNAPSHOT_DRIFT, "descriptor lifecycle snapshot differs")
        lifecycle_state = evaluator._lifecycle_store.current_state(subject_ref=descriptor.lifecycle_subject_ref, context=subject_evidence.lifecycle_context)
        lifecycle_available = lifecycle_state is not None
        head = evaluator._lifecycle_store.require_consumable(subject_ref=descriptor.lifecycle_subject_ref, context=subject_evidence.lifecycle_context)
        lifecycle_ok = head.record_id.value == descriptor.lifecycle_head_record_id
        lifecycle_reason = CompatibilityReason.EXACT_MATCH if lifecycle_ok else CompatibilityReason.LIFECYCLE_NOT_CONSUMABLE
    except (LifecycleViolation, ValueError):
        if lifecycle_state is LifecycleState.REVOKED:
            lifecycle_reason = CompatibilityReason.LIFECYCLE_REVOKED
        elif lifecycle_state is LifecycleState.QUARANTINED:
            lifecycle_reason = CompatibilityReason.LIFECYCLE_QUARANTINED
        elif lifecycle_state is LifecycleState.SUPERSEDED:
            lifecycle_reason = CompatibilityReason.LIFECYCLE_SUPERSEDED
        elif lifecycle_state is LifecycleState.STALE:
            lifecycle_reason = CompatibilityReason.LIFECYCLE_STALE
    dimensions.append(_make_dimension(
        CompatibilityDimension.LIFECYCLE,
        compatibility_value(label="producer-lifecycle", exact_value=[descriptor.lifecycle_head_record_id, descriptor.lifecycle_snapshot_id.to_dict()]),
        compatibility_value(label="consumer-lifecycle", exact_value=[None if lifecycle_state is None else lifecycle_state.value, context.lifecycle_snapshot.snapshot_id.to_dict()]),
        lifecycle_ok,
        lifecycle_reason,
    ))

    attestation_ok = False
    taint_ok = False
    taint_profile: SourceTaintProfile | None = None
    try:
        if subject_evidence.attestation is None:
            raise _fail(CompatibilityFailureCode.ATTESTATION_UNAVAILABLE, "attestation is absent")
        validate_behavior_attestation(subject_evidence.attestation, expected_subject_content_key=descriptor.content_key)
        if behavior_attestation_to_ref(subject_evidence.attestation) != descriptor.attestation_ref:
            raise _fail(CompatibilityFailureCode.ATTESTATION_INVALID, "attestation ref differs")
        require_behavior_attestation_consumable(
            attestation=subject_evidence.attestation,
            expected_subject_content_key=descriptor.content_key,
            authority_handle=evaluator._authority_handle,
            attestation_store=evaluator._attestation_store,
            lifecycle_store=evaluator._lifecycle_store,
            lifecycle_context=subject_evidence.lifecycle_context,
        )
        attestation_ok = True
    except ValueError:
        attestation_ok = False
    try:
        root = subject_evidence.taint_root_basis
        if root is None:
            raise _fail(CompatibilityFailureCode.TAINT_HISTORY_UNAVAILABLE, "taint basis is absent")
        if type(root) is SourceTaintProfile:
            validate_source_taint_profile(root, expected_subject_ref=descriptor.taint_subject_ref)
            taint_profile = root
            root_id = root.profile_id
        else:
            validate_taint_derivation(root, source_profiles=subject_evidence.taint_source_profiles, source_derivations=subject_evidence.taint_derivations)
            root_id = root.derivation_id
        if root_id != descriptor.taint_profile_id:
            raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "taint basis identity differs")
        anchor = evaluator._taint_store.current_anchor()
        validate_history_anchor(anchor)
        if anchor.history_domain is not HistoryDomain.TAINT or anchor.anchor_id != descriptor.taint_history_anchor_id:
            raise _fail(CompatibilityFailureCode.TAINT_NOT_CONSUMABLE, "taint history anchor differs")
        require_taint_consumable(
            authority_handle=evaluator._authority_handle,
            root_basis=root,
            source_profiles=subject_evidence.taint_source_profiles,
            derivations=subject_evidence.taint_derivations,
            decisions=subject_evidence.taint_decisions,
            history_store=evaluator._taint_store,
        )
        taint_ok = True
    except ValueError:
        taint_ok = False
    complete = (
        attestation_ok
        and taint_ok
        and binding_complete
        and lifecycle_available
        and compiler_available
        and environment_present
        and tools_present
    )
    dimensions.append(_make_dimension(
        CompatibilityDimension.EVIDENCE_COMPLETENESS,
        compatibility_value(label="producer-evidence", exact_value=[descriptor.attestation_ref.to_dict(), descriptor.taint_profile_id.to_dict(), descriptor.taint_history_anchor_id.to_dict(), [item.to_dict() for item in descriptor.binding_refs]]),
        compatibility_value(label="consumer-evidence", exact_value=[attestation_ok, taint_ok, binding_complete, lifecycle_available, compiler_available, environment_present, tools_present]),
        complete,
        CompatibilityReason.COMPLETE if complete else CompatibilityReason.REQUIRED_EVIDENCE_MISSING,
        refs,
    ))

    if tuple(item.dimension for item in dimensions) != REQUIRED_COMPATIBILITY_DIMENSIONS:
        raise _fail(CompatibilityFailureCode.DIMENSION_MISSING, "dimension evaluation is incomplete")
    attestation = subject_evidence.attestation
    producers = () if attestation is None else tuple(attestation.producer_actor_ids)
    sources = () if taint_profile is None else tuple(taint_profile.source_actor_ids)
    if not producers:
        producers = (evaluator._retriever_actor,)
    if not sources:
        sources = (evaluator._consumer_actor,)
    derived_values = {
        evaluator._retriever_actor.value: evaluator._retriever_actor,
        evaluator._consumer_actor.value: evaluator._consumer_actor,
        evaluator._score_provider_actor.value: evaluator._score_provider_actor,
        context.oracle_observation.oracle_identity.value: context.oracle_observation.oracle_identity,
    }
    if attestation is not None:
        derived_values[attestation.attester_identity.value] = attestation.attester_identity
        builder = attestation.builder_runtime_identity.builder_actor_identity
        derived_values[builder.value] = builder
    derived = tuple(derived_values[key] for key in sorted(derived_values))
    _validate_evaluator_actor_independence(
        evaluator,
        context=context,
        producer_actors=tuple(producers),
        source_actors=tuple(sources),
        derived_actors=derived,
        proposer=evaluator._retriever_actor,
    )
    return _DimensionFacts(tuple(dimensions), tuple(producers), tuple(sources), derived)


def evaluate_compatibility(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    index_entry: IndexEntry,
) -> CompatibilityDecision:
    require_configured_compatibility_evaluator(evaluator)
    validate_compatibility_context(context, evaluator=evaluator)
    _same_snapshot_or_fail(evaluator._library, context.library_snapshot)
    _same_lifecycle_snapshot_or_fail(evaluator._lifecycle_store, context.lifecycle_snapshot)
    validate_compatibility_subject_descriptor(descriptor)
    reconcile_index_entry(index_entry, descriptor)
    evidence = evaluator._evidence_resolver(descriptor)
    if type(evidence) is not CompatibilitySubjectEvidence:
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "evidence resolver returned an invalid bundle")
    facts = _dimension_facts(evaluator, context, descriptor, evidence)
    kind = _expected_decision_kind(facts.dimensions)
    created_at = _timestamp(evaluator._trusted_clock(), "compatibility decision timestamp")
    provisional = object.__new__(CompatibilityEvidence)
    object.__setattr__(provisional, "schema_version", COMPATIBILITY_EVIDENCE_V1)
    object.__setattr__(provisional, "descriptor_id", descriptor.descriptor_id)
    object.__setattr__(provisional, "context_id", context.context_id)
    object.__setattr__(provisional, "evaluator_declaration_id", evaluator._declaration.declaration_id)
    object.__setattr__(provisional, "policy_version", COMPATIBILITY_POLICY_V1)
    completeness = EvidenceCompleteness.COMPLETE if facts.dimensions[-1].result is DimensionResult.PASS else EvidenceCompleteness.INCOMPLETE
    object.__setattr__(provisional, "completeness", completeness)
    object.__setattr__(provisional, "dimensions", facts.dimensions)
    object.__setattr__(provisional, "library_snapshot_sha256", context.library_snapshot_sha256)
    object.__setattr__(provisional, "lifecycle_snapshot_id", context.lifecycle_snapshot.snapshot_id)
    object.__setattr__(provisional, "created_at_utc", created_at)
    object.__setattr__(provisional, "_trusted_seal", _SEAL)
    core_bytes = _canonical(_evidence_core_payload(provisional))
    core_id = compute_record_id(domain=IdentityDomain.COMPATIBILITY_EVIDENCE, canonical_bytes=core_bytes)
    object.__setattr__(provisional, "evidence_core_id", core_id)
    proposal_bytes = _canonical({
        "schema_version": CONFLICT_EVALUATION_REQUEST_V1,
        "context_id": context.context_id.to_dict(),
        "descriptor_id": descriptor.descriptor_id.to_dict(),
        "evidence_core_id": core_id.to_dict(),
        "policy_version": COMPATIBILITY_POLICY_V1,
    })
    proposal_id = compute_proposal_id(canonical_bytes=proposal_bytes)
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal_id,
        authority_identity=evaluator._declaration.evaluator_identity,
        authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
        reason_code=ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT,
        producer_actor_ids=facts.producer_actors,
        source_actor_ids=facts.source_actors,
        proposer_identity=evaluator._retriever_actor,
        executor_identity=None,
        subject_derived_actor_ids=facts.derived_actors,
        delegation_chain=(),
    )
    decision_bytes = _canonical(_decision_identity_payload(kind, core_id, evaluator._declaration))
    decision_id = compute_authority_decision_id(canonical_bytes=decision_bytes, independence_proof=proof)
    object.__setattr__(provisional, "authority_decision_id", decision_id)
    final_bytes = _canonical(_evidence_final_payload(provisional))
    object.__setattr__(provisional, "evidence_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_EVIDENCE, canonical_bytes=final_bytes))
    validate_compatibility_evidence(provisional)
    result = object.__new__(CompatibilityDecision)
    object.__setattr__(result, "schema_version", COMPATIBILITY_DECISION_V1)
    object.__setattr__(result, "decision_kind", kind)
    object.__setattr__(result, "evidence", provisional)
    object.__setattr__(result, "proposal_id", proposal_id)
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "evaluator_identity", evaluator._declaration.evaluator_identity)
    object.__setattr__(result, "decision_id", decision_id)
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    validate_compatibility_decision(result, evaluator=evaluator, context=context, descriptor=descriptor)
    return result


def validate_compatibility_evidence(value: CompatibilityEvidence) -> None:
    if type(value) is not CompatibilityEvidence or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "compatibility evidence is not sealed")
    if value.schema_version != COMPATIBILITY_EVIDENCE_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "compatibility evidence schema is unknown")
    _record(value.descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "evidence descriptor_id")
    _record(value.context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "evidence context_id")
    _record(value.evaluator_declaration_id, IdentityDomain.COMPATIBILITY_EVALUATOR_DECLARATION, "evaluator declaration id")
    if value.policy_version != COMPATIBILITY_POLICY_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_POLICY, "evidence policy is unknown")
    if type(value.completeness) is not EvidenceCompleteness or type(value.dimensions) is not tuple:
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "evidence completeness is invalid")
    for item in value.dimensions:
        validate_compatibility_dimension_record(item)
    observed = tuple(item.dimension for item in value.dimensions)
    if len(set(observed)) != len(observed):
        raise _fail(CompatibilityFailureCode.DIMENSION_DUPLICATE, "evidence contains duplicate dimensions")
    if observed != REQUIRED_COMPATIBILITY_DIMENSIONS:
        raise _fail(CompatibilityFailureCode.DIMENSION_MISSING, "evidence dimensions are incomplete or reordered")
    all_pass = all(item.result is DimensionResult.PASS for item in value.dimensions)
    if (value.completeness is EvidenceCompleteness.COMPLETE) != (value.dimensions[-1].result is DimensionResult.PASS):
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "evidence completeness contradicts dimension record")
    _sha256(value.library_snapshot_sha256, "library_snapshot_sha256")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "lifecycle_snapshot_id")
    _timestamp(value.created_at_utc, "evidence timestamp")
    core_bytes = _canonical(_evidence_core_payload(value))
    _record(value.evidence_core_id, IdentityDomain.COMPATIBILITY_EVIDENCE, "evidence_core_id")
    try:
        validate_record_id(value.evidence_core_id, canonical_bytes=core_bytes)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "evidence core identity mismatch") from exc
    if type(value.authority_decision_id) is not AuthorityDecisionId:
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "evidence decision identity is invalid")
    value.authority_decision_id.to_dict()
    final_bytes = _canonical(_evidence_final_payload(value))
    _record(value.evidence_id, IdentityDomain.COMPATIBILITY_EVIDENCE, "evidence_id")
    try:
        validate_record_id(value.evidence_id, canonical_bytes=final_bytes)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "final evidence identity mismatch") from exc
    if all_pass and value.completeness is not EvidenceCompleteness.COMPLETE:
        raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "all-pass evidence must be complete")


def validate_compatibility_decision(
    value: CompatibilityDecision,
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext | None = None,
    descriptor: CompatibilitySubjectDescriptor | None = None,
) -> None:
    require_configured_compatibility_evaluator(evaluator)
    if type(value) is not CompatibilityDecision or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "compatibility decision is not sealed")
    if value._evaluator is not evaluator:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "decision belongs to another evaluator")
    if value.schema_version != COMPATIBILITY_DECISION_V1 or type(value.schema_version) is not str or type(value.decision_kind) is not CompatibilityDecisionKind:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "compatibility decision schema or kind is invalid")
    validate_compatibility_evidence(value.evidence)
    if value.evidence.evaluator_declaration_id != evaluator._declaration.declaration_id or value.evaluator_identity != evaluator._declaration.evaluator_identity:
        raise _fail(CompatibilityFailureCode.EVALUATOR_DECLARATION_MISMATCH, "decision evaluator differs")
    if context is not None:
        validate_compatibility_context(context, evaluator=evaluator)
        if value.evidence.context_id != context.context_id or value.evidence.library_snapshot_sha256 != context.library_snapshot_sha256 or value.evidence.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id:
            raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "decision context differs")
    if descriptor is not None:
        validate_compatibility_subject_descriptor(descriptor)
        if value.evidence.descriptor_id != descriptor.descriptor_id:
            raise _fail(CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH, "decision descriptor differs")
    expected_kind = _expected_decision_kind(value.evidence.dimensions)
    if value.decision_kind is not expected_kind or value.decision_kind is CompatibilityDecisionKind.MIGRATION_REQUIRED:
        raise _fail(CompatibilityFailureCode.DECISION_MISMATCH, "decision kind does not follow complete evidence")
    if value.decision_kind is CompatibilityDecisionKind.COMPATIBLE:
        if value.evidence.completeness is not EvidenceCompleteness.COMPLETE or any(item.result is not DimensionResult.PASS for item in value.evidence.dimensions):
            raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "compatible decision lacks complete passing evidence")
    proposal_bytes = _canonical({
        "schema_version": CONFLICT_EVALUATION_REQUEST_V1,
        "context_id": value.evidence.context_id.to_dict(),
        "descriptor_id": value.evidence.descriptor_id.to_dict(),
        "evidence_core_id": value.evidence.evidence_core_id.to_dict(),
        "policy_version": COMPATIBILITY_POLICY_V1,
    })
    expected_proposal = compute_proposal_id(canonical_bytes=proposal_bytes)
    if type(value.proposal_id) is not ProposalId or value.proposal_id.to_dict() != expected_proposal.to_dict():
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "compatibility proposal identity differs")
    try:
        validate_independence_proof(value.independence_proof)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "compatibility independence proof is invalid") from exc
    _validate_evaluator_actor_independence(
        evaluator,
        context=context,
        producer_actors=value.independence_proof.producer_actor_ids,
        source_actors=value.independence_proof.source_actor_ids,
        derived_actors=value.independence_proof.subject_derived_actor_ids,
        proposer=value.independence_proof.proposer_identity,
        executor=value.independence_proof.executor_identity,
    )
    if value.independence_proof.subject_proposal_id.to_dict() != expected_proposal.to_dict() or value.independence_proof.authority_identity != evaluator._declaration.evaluator_identity or value.independence_proof.authority_role is not AuthorityRole.COMPATIBILITY_EVALUATOR or value.independence_proof.reason_code is not ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "compatibility independence proof differs")
    decision_bytes = _canonical(_decision_identity_payload(value.decision_kind, value.evidence.evidence_core_id, evaluator._declaration))
    expected_id = compute_authority_decision_id(canonical_bytes=decision_bytes, independence_proof=value.independence_proof)
    if type(value.decision_id) is not AuthorityDecisionId or value.decision_id.to_dict() != expected_id.to_dict() or value.evidence.authority_decision_id.to_dict() != expected_id.to_dict():
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "compatibility decision identity differs")


@dataclass(frozen=True, init=False)
class CompatibilityRevalidationRecord:
    schema_version: str
    stage: RevalidationStage
    context_id: RecordId
    descriptor_id: RecordId
    original_decision_id: AuthorityDecisionId
    prior_revalidation_id: RecordId | None
    library_snapshot_sha256: str
    lifecycle_snapshot_id: RecordId
    observation_sha256: str | None
    outcome: RevalidationOutcome
    failure_code: CompatibilityFailureCode | None
    cause_code: CompatibilityFailureCode | None
    created_at_utc: datetime
    revalidation_id: RecordId
    _context: CompatibilityContext
    _observation: PlatformObservedProvenance | None
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityRevalidationRecord:
        raise TypeError("CompatibilityRevalidationRecord is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_revalidation_record(self)
        return {**_revalidation_payload(self), "revalidation_id": self.revalidation_id.to_dict()}


def _revalidation_payload(value: CompatibilityRevalidationRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "stage": value.stage.value,
        "context_id": value.context_id.to_dict(),
        "descriptor_id": value.descriptor_id.to_dict(),
        "original_decision_id": value.original_decision_id.to_dict(),
        "prior_revalidation_id": None if value.prior_revalidation_id is None else value.prior_revalidation_id.to_dict(),
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "observation_sha256": value.observation_sha256,
        "outcome": value.outcome.value,
        "failure_code": None if value.failure_code is None else value.failure_code.value,
        "cause_code": None if value.cause_code is None else value.cause_code.value,
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


def _make_revalidation(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    stage: RevalidationStage,
    context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    original_decision: CompatibilityDecision,
    prior: CompatibilityRevalidationRecord | None,
) -> CompatibilityRevalidationRecord:
    validate_compatibility_decision(original_decision, evaluator=evaluator, context=context, descriptor=descriptor)
    if stage is RevalidationStage.BEFORE_CONSUMPTION:
        if prior is None:
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "consumption requires Stage 2 revalidation")
        validate_compatibility_revalidation_record(prior)
        if (
            prior.stage is not RevalidationStage.BEFORE_LOADING
            or prior.outcome is not RevalidationOutcome.PASSED
            or prior.context_id != context.context_id
            or prior.descriptor_id != descriptor.descriptor_id
            or prior.original_decision_id != original_decision.decision_id
            or prior.library_snapshot_sha256 != context.library_snapshot_sha256
            or prior.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id
            or prior._context is not context
            or prior.observation_sha256 != context.observation_sha256
        ):
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "consumption requires the exact passing Stage 2 chain")
    elif prior is not None:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "Stage 2 cannot have prior revalidation")
    fresh_observation: PlatformObservedProvenance | None = None
    observation_sha256: str | None = None
    outcome = RevalidationOutcome.PASSED
    failure: CompatibilityFailureCode | None = None
    cause: CompatibilityFailureCode | None = None
    try:
        observed = evaluator._platform_observation_provider()
        if type(observed) is not PlatformObservedProvenance:
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "platform observation provider returned an invalid value",
            )
        try:
            validate_platform_observed_provenance(
                observed,
                authority_handle=evaluator._authority_handle,
            )
        except (AttributeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.OBSERVATION_AUTHORITY_MISMATCH,
                "fresh platform observation is invalid for the configured authority",
            ) from exc
        fresh_observation = observed
        observation_sha256 = hashlib.sha256(
            _canonical(_observation_payload(fresh_observation))
        ).hexdigest()
        fresh_context = create_compatibility_context(
            evaluator=evaluator,
            authority_handle=evaluator._authority_handle,
            observation=fresh_observation,
            library_snapshot=context.library_snapshot,
            lifecycle_snapshot=context.lifecycle_snapshot,
            consumer_actor=evaluator._consumer_actor,
            allowed_behavior_kinds=context.allowed_behavior_kinds,
            allowed_binding_kinds=context.allowed_binding_kinds,
            allowed_capabilities=context.allowed_capabilities,
            allowed_scope=context.allowed_scope,
            selected_set_ceiling=context.selected_set_ceiling,
        )
        _same_snapshot_or_fail(evaluator._library, context.library_snapshot)
        _same_lifecycle_snapshot_or_fail(evaluator._lifecycle_store, context.lifecycle_snapshot)
        evidence = evaluator._evidence_resolver(descriptor)
        if type(evidence) is not CompatibilitySubjectEvidence:
            raise _fail(CompatibilityFailureCode.EVIDENCE_INCOMPLETE, "fresh evidence is invalid")
        validate_compatibility_subject_evidence(evidence, descriptor=descriptor)
        _consume_exact_bindings(evaluator, evidence, context.repository_revision)
        facts = _dimension_facts(evaluator, fresh_context, descriptor, evidence)
        if observation_sha256 != context.observation_sha256:
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "fresh platform observation differs from the Stage 1 context",
            )
        if tuple(item.to_dict() for item in facts.dimensions) != tuple(item.to_dict() for item in original_decision.evidence.dimensions):
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "compatibility evidence changed")
        if _expected_decision_kind(facts.dimensions) is not original_decision.decision_kind:
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "compatibility outcome changed")
    except CompatibilityViolation as exc:
        outcome = RevalidationOutcome.FAILED
        failure = CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        cause = exc.failure_code
    except ValueError:
        outcome = RevalidationOutcome.FAILED
        failure = CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        cause = CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    except Exception:
        outcome = RevalidationOutcome.FAILED
        failure = CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        cause = CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    result = object.__new__(CompatibilityRevalidationRecord)
    object.__setattr__(result, "schema_version", COMPATIBILITY_REVALIDATION_V1)
    object.__setattr__(result, "stage", stage)
    object.__setattr__(result, "context_id", context.context_id)
    object.__setattr__(result, "descriptor_id", descriptor.descriptor_id)
    object.__setattr__(result, "original_decision_id", original_decision.decision_id)
    object.__setattr__(result, "prior_revalidation_id", None if prior is None else prior.revalidation_id)
    object.__setattr__(result, "library_snapshot_sha256", context.library_snapshot_sha256)
    object.__setattr__(result, "lifecycle_snapshot_id", context.lifecycle_snapshot.snapshot_id)
    object.__setattr__(result, "observation_sha256", observation_sha256)
    object.__setattr__(result, "outcome", outcome)
    object.__setattr__(result, "failure_code", failure)
    object.__setattr__(result, "cause_code", cause)
    object.__setattr__(result, "created_at_utc", _timestamp(evaluator._trusted_clock(), "revalidation timestamp"))
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_observation", fresh_observation)
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_revalidation_payload(result))
    object.__setattr__(result, "revalidation_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_REVALIDATION, canonical_bytes=payload))
    validate_compatibility_revalidation_record(result)
    return result


def revalidate_before_loading(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    original_decision: CompatibilityDecision,
) -> CompatibilityRevalidationRecord:
    return _make_revalidation(evaluator=evaluator, stage=RevalidationStage.BEFORE_LOADING, context=context, descriptor=descriptor, original_decision=original_decision, prior=None)


def revalidate_before_consumption(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
    original_decision: CompatibilityDecision,
    before_loading: CompatibilityRevalidationRecord,
) -> CompatibilityRevalidationRecord:
    return _make_revalidation(evaluator=evaluator, stage=RevalidationStage.BEFORE_CONSUMPTION, context=context, descriptor=descriptor, original_decision=original_decision, prior=before_loading)


def consumption_finding_from_revalidation(
    value: CompatibilityRevalidationRecord,
    *,
    decision: CompatibilityDecision,
    conflict_scan: CompatibilityConflictScan | None = None,
) -> "object":
    """Project this owner's evidence into the typed finding the §22 gates read.

    The admission owner must not re-derive compatibility, and this owner must
    not decide admission. The projection is deliberately lossy in one direction
    only: it exposes whether the evidence is complete, whether the subject is
    compatible, whether state drifted since the original decision and whether a
    conflict remains unresolved, and it exposes nothing that would let a gate
    infer an admission from a stale status.

    A revalidation that did not reach the consumption stage is refused here
    rather than being read as "not drifted": absence of a fresh check is not
    evidence of stability.
    """

    from .admission import CompatibilityFinding

    validate_compatibility_revalidation_record(value)
    if value.stage is not RevalidationStage.BEFORE_CONSUMPTION:
        raise _fail(
            CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
            "consumption evidence requires a stage-3 revalidation record",
        )
    if type(decision) is not CompatibilityDecision:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "consumption evidence requires an exact decision")
    validate_compatibility_decision(decision, evaluator=decision._evaluator, context=value._context, descriptor=None)
    if value.original_decision_id != decision.decision_id:
        raise _fail(
            CompatibilityFailureCode.AUTHORITY_DECISION_INVALID,
            "revalidation does not belong to the supplied decision",
        )
    conflicts_unresolved = False
    if conflict_scan is not None:
        validate_compatibility_conflict_scan(conflict_scan, evaluator=decision._evaluator)
        conflicts_unresolved = conflict_scan.decision_kind is not ConflictDecisionKind.NO_CONFLICT_FOUND
    return CompatibilityFinding(
        compatible=decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE,
        evidence_complete=decision.evidence.completeness is EvidenceCompleteness.COMPLETE,
        drifted=value.outcome is not RevalidationOutcome.PASSED,
        conflicts_unresolved=conflicts_unresolved,
    )


def validate_compatibility_revalidation_record(value: CompatibilityRevalidationRecord) -> None:
    if type(value) is not CompatibilityRevalidationRecord or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "revalidation record is not sealed")
    if value.schema_version != COMPATIBILITY_REVALIDATION_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "revalidation schema is unknown")
    require_configured_compatibility_evaluator(value._evaluator)
    context = getattr(value, "_context", None)
    if type(context) is not CompatibilityContext:
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "revalidation context is unavailable or invalid")
    validate_compatibility_context(context, evaluator=value._evaluator)
    if type(value.stage) is not RevalidationStage or type(value.outcome) is not RevalidationOutcome:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "revalidation enums are invalid")
    _record(value.context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "revalidation context_id")
    _record(value.descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "revalidation descriptor_id")
    if type(value.original_decision_id) is not AuthorityDecisionId:
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "revalidation decision id is invalid")
    value.original_decision_id.to_dict()
    if value.stage is RevalidationStage.BEFORE_LOADING and value.prior_revalidation_id is not None:
        raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "Stage 2 has unexpected predecessor")
    if value.stage is RevalidationStage.BEFORE_CONSUMPTION:
        _record(value.prior_revalidation_id, IdentityDomain.COMPATIBILITY_REVALIDATION, "prior revalidation id")
    _sha256(value.library_snapshot_sha256, "revalidation snapshot hash")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "revalidation lifecycle snapshot")
    if (
        value.context_id != context.context_id
        or value.library_snapshot_sha256 != context.library_snapshot_sha256
        or value.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id
    ):
        raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "revalidation context binding differs")
    if value._observation is None:
        if value.observation_sha256 is not None:
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "revalidation observation absence is inconsistent")
    else:
        if type(value._observation) is not PlatformObservedProvenance:
            raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "revalidation observation is invalid")
        try:
            validate_platform_observed_provenance(
                value._observation,
                authority_handle=value._evaluator._authority_handle,
            )
        except (AttributeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.OBSERVATION_AUTHORITY_MISMATCH,
                "revalidation observation authority changed",
            ) from exc
        _sha256(value.observation_sha256, "revalidation observation hash")
        expected_observation_sha256 = hashlib.sha256(
            _canonical(_observation_payload(value._observation))
        ).hexdigest()
        if value.observation_sha256 != expected_observation_sha256:
            raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "revalidation observation identity mismatch")
    if value.outcome is RevalidationOutcome.PASSED:
        if (
            value._observation is None
            or value.observation_sha256 is None
            or value.failure_code is not None
            or value.cause_code is not None
        ):
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "passing revalidation evidence is incomplete")
        if value.observation_sha256 != context.observation_sha256:
            raise _fail(CompatibilityFailureCode.CONTEXT_MISMATCH, "passing revalidation observation differs from context")
    elif value.outcome is RevalidationOutcome.FAILED:
        if (
            value.failure_code is not CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
            or type(value.cause_code) is not CompatibilityFailureCode
        ):
            raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "failed revalidation cause is invalid")
    _timestamp(value.created_at_utc, "revalidation timestamp")
    payload = _canonical(_revalidation_payload(value))
    _record(value.revalidation_id, IdentityDomain.COMPATIBILITY_REVALIDATION, "revalidation_id")
    try:
        validate_record_id(value.revalidation_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "revalidation identity mismatch") from exc


def require_revalidation_passed(value: CompatibilityRevalidationRecord, *, expected_stage: RevalidationStage) -> None:
    validate_compatibility_revalidation_record(value)
    if value.stage is not expected_stage or value.outcome is not RevalidationOutcome.PASSED:
        raise _fail(CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED, "fresh compatibility revalidation did not pass")


@dataclass(frozen=True, init=False)
class ConflictEvidenceProposal:
    schema_version: str
    conflict_kind: ConflictKind
    proposer_actor: ActorIdentity
    left_descriptor_id: RecordId
    right_descriptor_id: RecordId
    scope: tuple[str, ...]
    binding_targets: tuple[str, ...]
    evidence_refs: tuple[HashBoundRef, ...]
    proposal_id: ProposalId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConflictEvidenceProposal:
        raise TypeError("ConflictEvidenceProposal is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_conflict_evidence_proposal(self)
        return {**_conflict_proposal_payload(self), "proposal_id": self.proposal_id.to_dict()}


def _conflict_proposal_payload(value: ConflictEvidenceProposal) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "conflict_kind": value.conflict_kind.value,
        "proposer_actor": value.proposer_actor.to_dict(),
        "left_descriptor_id": value.left_descriptor_id.to_dict(),
        "right_descriptor_id": value.right_descriptor_id.to_dict(),
        "scope": list(value.scope),
        "binding_targets": list(value.binding_targets),
        "evidence_refs": [item.to_dict() for item in value.evidence_refs],
    }


def create_conflict_evidence_proposal(
    *,
    conflict_kind: ConflictKind,
    proposer_actor: ActorIdentity,
    left_descriptor_id: RecordId,
    right_descriptor_id: RecordId,
    scope: tuple[str, ...],
    binding_targets: tuple[str, ...],
    evidence_refs: tuple[HashBoundRef, ...],
) -> ConflictEvidenceProposal:
    if type(conflict_kind) is not ConflictKind:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "conflict kind is invalid")
    result = object.__new__(ConflictEvidenceProposal)
    object.__setattr__(result, "schema_version", CONFLICT_EVIDENCE_PROPOSAL_V1)
    object.__setattr__(result, "conflict_kind", conflict_kind)
    object.__setattr__(result, "proposer_actor", _actor(proposer_actor, "conflict proposer"))
    object.__setattr__(result, "left_descriptor_id", _record(left_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "left descriptor"))
    object.__setattr__(result, "right_descriptor_id", _record(right_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "right descriptor"))
    if left_descriptor_id == right_descriptor_id:
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "conflict proposal is self-referential")
    object.__setattr__(result, "scope", _scopes(tuple(sorted(scope)), "conflict scope", nonempty=True))
    object.__setattr__(result, "binding_targets", _strings(tuple(sorted(binding_targets)), "binding targets", nonempty=True))
    object.__setattr__(result, "evidence_refs", tuple(sorted((_ref(item, None, "conflict evidence ref") for item in evidence_refs), key=lambda item: item.ref_id)))
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_conflict_proposal_payload(result))
    object.__setattr__(result, "proposal_id", compute_proposal_id(canonical_bytes=payload))
    validate_conflict_evidence_proposal(result)
    return result


def validate_conflict_evidence_proposal(value: ConflictEvidenceProposal) -> None:
    if type(value) is not ConflictEvidenceProposal or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "conflict proposal is not sealed")
    if value.schema_version != CONFLICT_EVIDENCE_PROPOSAL_V1 or type(value.schema_version) is not str or type(value.conflict_kind) is not ConflictKind:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "conflict proposal schema or kind is invalid")
    _actor(value.proposer_actor, "conflict proposer")
    _record(value.left_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "left descriptor")
    _record(value.right_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "right descriptor")
    if value.left_descriptor_id == value.right_descriptor_id:
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "conflict proposal is self-referential")
    _scopes(value.scope, "conflict scope", nonempty=True)
    _strings(value.binding_targets, "binding targets", nonempty=True)
    _refs(value.evidence_refs, None, "conflict evidence refs")
    expected = compute_proposal_id(canonical_bytes=_canonical(_conflict_proposal_payload(value)))
    if type(value.proposal_id) is not ProposalId or value.proposal_id.to_dict() != expected.to_dict():
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "conflict proposal identity mismatch")


@dataclass(frozen=True, init=False)
class ConflictPairAssessment:
    schema_version: str
    context_id: RecordId
    left_descriptor_id: RecordId
    right_descriptor_id: RecordId
    left_compatibility_evidence_id: RecordId
    right_compatibility_evidence_id: RecordId
    conflict_kind: ConflictKind | None
    evidence_refs: tuple[HashBoundRef, ...]
    proposal_dispositions: tuple[tuple[ProposalId, ConflictProposalDisposition], ...]
    evaluator_component_id: str
    evaluator_component_version: str
    assessment_id: RecordId
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConflictPairAssessment:
        raise TypeError("ConflictPairAssessment is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_conflict_pair_assessment(self, evaluator=self._evaluator)
        return {**_conflict_pair_payload(self), "assessment_id": self.assessment_id.to_dict()}


def _conflict_pair_payload(value: ConflictPairAssessment) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "context_id": value.context_id.to_dict(),
        "left_descriptor_id": value.left_descriptor_id.to_dict(),
        "right_descriptor_id": value.right_descriptor_id.to_dict(),
        "left_compatibility_evidence_id": value.left_compatibility_evidence_id.to_dict(),
        "right_compatibility_evidence_id": value.right_compatibility_evidence_id.to_dict(),
        "conflict_kind": None if value.conflict_kind is None else value.conflict_kind.value,
        "evidence_refs": [item.to_dict() for item in value.evidence_refs],
        "proposal_dispositions": [
            {"proposal_id": proposal_id.to_dict(), "disposition": disposition.value}
            for proposal_id, disposition in value.proposal_dispositions
        ],
        "evaluator_component_id": value.evaluator_component_id,
        "evaluator_component_version": value.evaluator_component_version,
    }


def _make_conflict_pair_assessment(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    left_descriptor: CompatibilitySubjectDescriptor,
    right_descriptor: CompatibilitySubjectDescriptor,
    left_decision: CompatibilityDecision,
    right_decision: CompatibilityDecision,
    proposals: tuple[ConflictEvidenceProposal, ...],
) -> ConflictPairAssessment:
    ordered = sorted(
        ((left_descriptor, left_decision), (right_descriptor, right_decision)),
        key=lambda item: item[0].descriptor_id.value,
    )
    (left_descriptor, left_decision), (right_descriptor, right_decision) = ordered
    assessed = evaluator._conflict_assessor(context, left_decision, right_decision, left_descriptor, right_descriptor)
    if type(assessed) is not tuple or len(assessed) != 2:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "configured conflict assessor returned an invalid result")
    conflict_kind, evidence_refs = assessed
    if conflict_kind is not None and type(conflict_kind) is not ConflictKind:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "configured conflict conclusion is unknown")
    normalized_refs = _refs(evidence_refs, None, "conflict assessment evidence refs")
    matching_proposals = tuple(
        proposal
        for proposal in proposals
        if {proposal.left_descriptor_id.value, proposal.right_descriptor_id.value}
        == {left_descriptor.descriptor_id.value, right_descriptor.descriptor_id.value}
    )
    dispositions = tuple(
        (proposal.proposal_id, ConflictProposalDisposition.RECORDED_UNTRUSTED_EVIDENCE)
        for proposal in sorted(matching_proposals, key=lambda item: item.proposal_id.record_id.value)
    )
    result = object.__new__(ConflictPairAssessment)
    object.__setattr__(result, "schema_version", CONFLICT_PAIR_ASSESSMENT_V1)
    object.__setattr__(result, "context_id", context.context_id)
    object.__setattr__(result, "left_descriptor_id", left_descriptor.descriptor_id)
    object.__setattr__(result, "right_descriptor_id", right_descriptor.descriptor_id)
    object.__setattr__(result, "left_compatibility_evidence_id", left_decision.evidence.evidence_id)
    object.__setattr__(result, "right_compatibility_evidence_id", right_decision.evidence.evidence_id)
    object.__setattr__(result, "conflict_kind", conflict_kind)
    object.__setattr__(result, "evidence_refs", normalized_refs)
    object.__setattr__(result, "proposal_dispositions", dispositions)
    object.__setattr__(result, "evaluator_component_id", evaluator._component_id)
    object.__setattr__(result, "evaluator_component_version", evaluator._component_version)
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    object.__setattr__(result, "assessment_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, canonical_bytes=_canonical(_conflict_pair_payload(result))))
    validate_conflict_pair_assessment(result, evaluator=evaluator)
    return result


def validate_conflict_pair_assessment(
    value: ConflictPairAssessment,
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
) -> None:
    require_configured_compatibility_evaluator(evaluator)
    if type(value) is not ConflictPairAssessment or getattr(value, "_trusted_seal", None) is not _SEAL or value._evaluator is not evaluator:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "conflict pair assessment is not evaluator sealed")
    if value.schema_version != CONFLICT_PAIR_ASSESSMENT_V1 or type(value.schema_version) is not str:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "conflict pair assessment schema is unknown")
    _record(value.context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "pair context id")
    _record(value.left_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "pair left descriptor")
    _record(value.right_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "pair right descriptor")
    if value.left_descriptor_id.value >= value.right_descriptor_id.value:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict pair is not in canonical order")
    _record(value.left_compatibility_evidence_id, IdentityDomain.COMPATIBILITY_EVIDENCE, "pair left evidence")
    _record(value.right_compatibility_evidence_id, IdentityDomain.COMPATIBILITY_EVIDENCE, "pair right evidence")
    if value.conflict_kind is not None and type(value.conflict_kind) is not ConflictKind:
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "pair conclusion is invalid")
    _refs(value.evidence_refs, None, "pair evidence refs")
    if type(value.proposal_dispositions) is not tuple:
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "proposal dispositions must be immutable")
    for proposal_id, disposition in value.proposal_dispositions:
        if type(proposal_id) is not ProposalId or type(disposition) is not ConflictProposalDisposition:
            raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "proposal disposition is invalid")
        proposal_id.to_dict()
    if value.evaluator_component_id != evaluator._component_id or value.evaluator_component_version != evaluator._component_version:
        raise _fail(CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH, "pair evaluator identity changed")
    _record(value.assessment_id, IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, "pair assessment id")
    try:
        validate_record_id(value.assessment_id, canonical_bytes=_canonical(_conflict_pair_payload(value)))
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "pair assessment identity mismatch") from exc


@dataclass(frozen=True, init=False)
class ConflictEvaluationRequest:
    schema_version: str
    context_id: RecordId
    library_snapshot_sha256: str
    lifecycle_snapshot_id: RecordId
    compatible_candidate_ids: tuple[RecordId, ...]
    negative_evidence_candidate_ids: tuple[RecordId, ...]
    considered_candidate_keys: tuple[str, ...]
    validated_candidate_ids: tuple[RecordId, ...]
    incomplete_candidate_keys: tuple[str, ...]
    pair_assessments: tuple[ConflictPairAssessment, ...]
    proposals: tuple[ConflictEvidenceProposal, ...]
    actor_coverage: tuple[ActorIdentity, ...]
    request_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConflictEvaluationRequest:
        raise TypeError("ConflictEvaluationRequest is evaluator-created")


@dataclass(frozen=True, init=False)
class CompatibilityConflictScan:
    schema_version: str
    request: ConflictEvaluationRequest
    decision_kind: ConflictDecisionKind
    proposal_id: ProposalId
    independence_proof: IndependenceProof
    decision_id: AuthorityDecisionId
    created_at_utc: datetime
    scan_id: RecordId
    _evaluator: ConfiguredCompatibilityEvaluator
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityConflictScan:
        raise TypeError("CompatibilityConflictScan is evaluator-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_conflict_scan(self, evaluator=self._evaluator)
        return {**_conflict_scan_payload(self), "scan_id": self.scan_id.to_dict()}


def _request_payload(value: ConflictEvaluationRequest) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "context_id": value.context_id.to_dict(),
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "compatible_candidate_ids": [item.to_dict() for item in value.compatible_candidate_ids],
        "negative_evidence_candidate_ids": [item.to_dict() for item in value.negative_evidence_candidate_ids],
        "considered_candidate_keys": list(value.considered_candidate_keys),
        "validated_candidate_ids": [item.to_dict() for item in value.validated_candidate_ids],
        "incomplete_candidate_keys": list(value.incomplete_candidate_keys),
        "pair_assessments": [item.to_dict() for item in value.pair_assessments],
        "proposals": [item.to_dict() for item in value.proposals],
        "actor_coverage": [item.to_dict() for item in value.actor_coverage],
    }


def _conflict_scan_payload(value: CompatibilityConflictScan) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "request": {**_request_payload(value.request), "request_id": value.request.request_id.to_dict()},
        "decision_kind": value.decision_kind.value,
        "proposal_id": value.proposal_id.to_dict(),
        "independence_proof": value.independence_proof.to_dict(),
        "decision_id": value.decision_id.to_dict(),
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


def evaluate_conflicts(
    *,
    evaluator: ConfiguredCompatibilityEvaluator,
    context: CompatibilityContext,
    decisions: tuple[CompatibilityDecision, ...],
    descriptors: tuple[CompatibilitySubjectDescriptor, ...],
    considered_index_entries: tuple[IndexEntry, ...],
    proposals: tuple[ConflictEvidenceProposal, ...],
) -> CompatibilityConflictScan:
    require_configured_compatibility_evaluator(evaluator)
    validate_compatibility_context(context, evaluator=evaluator)
    _same_snapshot_or_fail(evaluator._library, context.library_snapshot)
    _same_lifecycle_snapshot_or_fail(evaluator._lifecycle_store, context.lifecycle_snapshot)
    if type(decisions) is not tuple or type(descriptors) is not tuple or type(considered_index_entries) is not tuple or type(proposals) is not tuple:
        raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "conflict inputs must be exact tuples")
    considered_by_key: dict[str, IndexEntry] = {}
    for entry in considered_index_entries:
        if type(entry) is not IndexEntry:
            raise _fail(CompatibilityFailureCode.TYPE_MISMATCH, "conflict candidate index entry is invalid")
        entry.to_dict()
        key = f"{entry.content_key}|{entry.manifest_id}"
        if key in considered_by_key:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict candidate set contains duplicates")
        considered_by_key[key] = entry
    descriptor_by_id: dict[str, CompatibilitySubjectDescriptor] = {}
    descriptor_keys: dict[str, str] = {}
    for descriptor in descriptors:
        validate_compatibility_subject_descriptor(descriptor)
        if descriptor.descriptor_id.value in descriptor_by_id:
            raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "duplicate conflict descriptor")
        key = f"{descriptor.content_key.value}|{descriptor.manifest_id.value}"
        entry = considered_by_key.get(key)
        if entry is None:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "descriptor is absent from considered candidate set")
        reconcile_index_entry(entry, descriptor)
        descriptor_by_id[descriptor.descriptor_id.value] = descriptor
        descriptor_keys[descriptor.descriptor_id.value] = key
    compatible: list[RecordId] = []
    negative: list[RecordId] = []
    decided_descriptor_ids: set[str] = set()
    actors: dict[str, ActorIdentity] = {
        evaluator._retriever_actor.value: evaluator._retriever_actor,
        evaluator._consumer_actor.value: evaluator._consumer_actor,
        evaluator._score_provider_actor.value: evaluator._score_provider_actor,
    }
    for decision in decisions:
        validate_compatibility_decision(decision, evaluator=evaluator, context=context)
        descriptor = descriptor_by_id.get(decision.evidence.descriptor_id.value)
        if descriptor is None:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "decision descriptor is absent from full set")
        if descriptor.descriptor_id.value in decided_descriptor_ids:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "candidate has duplicate compatibility decisions")
        decided_descriptor_ids.add(descriptor.descriptor_id.value)
        if decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE:
            compatible.append(descriptor.descriptor_id)
        if descriptor.behavior_kind is BehaviorKind.REJECTED_HYPOTHESIS_GUARD:
            negative.append(descriptor.descriptor_id)
        for actor in (*decision.independence_proof.producer_actor_ids, *decision.independence_proof.source_actor_ids, *decision.independence_proof.subject_derived_actor_ids):
            actors[actor.value] = actor
    if decided_descriptor_ids != set(descriptor_by_id):
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "full candidate set lacks compatibility decisions")
    proposal_items = tuple(sorted(proposals, key=lambda item: item.proposal_id.record_id.value))
    known = set(descriptor_by_id)
    proposal_unknown = False
    for proposal in proposal_items:
        validate_conflict_evidence_proposal(proposal)
        if proposal.left_descriptor_id.value not in known or proposal.right_descriptor_id.value not in known:
            proposal_unknown = True
        actors[proposal.proposer_actor.value] = proposal.proposer_actor
    validated_keys = {descriptor_keys[item] for item in decided_descriptor_ids}
    incomplete_keys = tuple(sorted(set(considered_by_key) - validated_keys))
    if proposal_unknown:
        incomplete_keys = tuple(sorted((*incomplete_keys, "proposal-reference-unresolved")))
    decision_by_descriptor = {decision.evidence.descriptor_id.value: decision for decision in decisions}
    pair_assessments: list[ConflictPairAssessment] = []
    if not incomplete_keys:
        ordered_descriptors = tuple(sorted(descriptor_by_id.values(), key=lambda item: item.descriptor_id.value))
        for left_index, left_descriptor in enumerate(ordered_descriptors):
            for right_descriptor in ordered_descriptors[left_index + 1 :]:
                pair_assessments.append(_make_conflict_pair_assessment(
                    evaluator=evaluator,
                    context=context,
                    left_descriptor=left_descriptor,
                    right_descriptor=right_descriptor,
                    left_decision=decision_by_descriptor[left_descriptor.descriptor_id.value],
                    right_decision=decision_by_descriptor[right_descriptor.descriptor_id.value],
                    proposals=proposal_items,
                ))
    decision_kind = (
        ConflictDecisionKind.SCAN_INCOMPLETE
        if incomplete_keys
        else ConflictDecisionKind.UNRESOLVED_CONFLICT
        if any(item.conflict_kind is not None for item in pair_assessments)
        else ConflictDecisionKind.NO_CONFLICT_FOUND
    )
    request = object.__new__(ConflictEvaluationRequest)
    object.__setattr__(request, "schema_version", CONFLICT_EVALUATION_REQUEST_V1)
    object.__setattr__(request, "context_id", context.context_id)
    object.__setattr__(request, "library_snapshot_sha256", context.library_snapshot_sha256)
    object.__setattr__(request, "lifecycle_snapshot_id", context.lifecycle_snapshot.snapshot_id)
    object.__setattr__(request, "compatible_candidate_ids", tuple(sorted(compatible, key=lambda item: item.value)))
    object.__setattr__(request, "negative_evidence_candidate_ids", tuple(sorted(negative, key=lambda item: item.value)))
    object.__setattr__(request, "considered_candidate_keys", tuple(sorted(considered_by_key)))
    object.__setattr__(request, "validated_candidate_ids", tuple(sorted((item.descriptor_id for item in descriptors), key=lambda item: item.value)))
    object.__setattr__(request, "incomplete_candidate_keys", incomplete_keys)
    object.__setattr__(request, "pair_assessments", tuple(pair_assessments))
    object.__setattr__(request, "proposals", proposal_items)
    actor_coverage = tuple(actors[key] for key in sorted(actors))
    _validate_evaluator_actor_independence(
        evaluator,
        derived_actors=actor_coverage,
        proposer=evaluator._retriever_actor,
    )
    object.__setattr__(request, "actor_coverage", actor_coverage)
    object.__setattr__(request, "_trusted_seal", _SEAL)
    request_bytes = _canonical(_request_payload(request))
    object.__setattr__(request, "request_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, canonical_bytes=request_bytes))
    proposal_id = compute_proposal_id(canonical_bytes=request_bytes)
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal_id,
        authority_identity=evaluator._declaration.evaluator_identity,
        authority_role=AuthorityRole.COMPATIBILITY_EVALUATOR,
        reason_code=ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT,
        producer_actor_ids=(evaluator._retriever_actor,),
        source_actor_ids=(evaluator._consumer_actor,),
        proposer_identity=evaluator._retriever_actor,
        executor_identity=None,
        subject_derived_actor_ids=actor_coverage,
        delegation_chain=(),
    )
    decision_bytes = _canonical({"schema_version": COMPATIBILITY_CONFLICT_SCAN_V1, "request_id": request.request_id.to_dict(), "decision_kind": decision_kind.value})
    decision_id = compute_authority_decision_id(canonical_bytes=decision_bytes, independence_proof=proof)
    result = object.__new__(CompatibilityConflictScan)
    object.__setattr__(result, "schema_version", COMPATIBILITY_CONFLICT_SCAN_V1)
    object.__setattr__(result, "request", request)
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "proposal_id", proposal_id)
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "decision_id", decision_id)
    object.__setattr__(result, "created_at_utc", _timestamp(evaluator._trusted_clock(), "conflict scan timestamp"))
    object.__setattr__(result, "_evaluator", evaluator)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    scan_bytes = _canonical(_conflict_scan_payload(result))
    object.__setattr__(result, "scan_id", compute_record_id(domain=IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, canonical_bytes=scan_bytes))
    validate_compatibility_conflict_scan(result, evaluator=evaluator)
    return result


def validate_compatibility_conflict_scan(value: CompatibilityConflictScan, *, evaluator: ConfiguredCompatibilityEvaluator) -> None:
    require_configured_compatibility_evaluator(evaluator)
    if type(value) is not CompatibilityConflictScan or getattr(value, "_trusted_seal", None) is not _SEAL or value._evaluator is not evaluator:
        raise _fail(CompatibilityFailureCode.TRUSTED_OBJECT_FORGED, "conflict scan is not evaluator sealed")
    if value.schema_version != COMPATIBILITY_CONFLICT_SCAN_V1 or type(value.schema_version) is not str or type(value.decision_kind) is not ConflictDecisionKind:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "conflict scan schema or kind is invalid")
    request = value.request
    if type(request) is not ConflictEvaluationRequest or getattr(request, "_trusted_seal", None) is not _SEAL:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict request is not sealed")
    if request.schema_version != CONFLICT_EVALUATION_REQUEST_V1:
        raise _fail(CompatibilityFailureCode.UNKNOWN_SCHEMA, "conflict request schema is unknown")
    for proposal in request.proposals:
        validate_conflict_evidence_proposal(proposal)
    if type(request.considered_candidate_keys) is not tuple or type(request.incomplete_candidate_keys) is not tuple:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict candidate coverage is mutable")
    if request.considered_candidate_keys != tuple(sorted(set(request.considered_candidate_keys))):
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "considered candidate coverage is not exact")
    if request.incomplete_candidate_keys != tuple(sorted(set(request.incomplete_candidate_keys))):
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "incomplete candidate coverage is not exact")
    if type(request.validated_candidate_ids) is not tuple:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "validated candidate coverage is mutable")
    validated_ids = tuple(item.value for item in request.validated_candidate_ids)
    for item in request.validated_candidate_ids:
        _record(item, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "validated conflict candidate")
    if validated_ids != tuple(sorted(set(validated_ids))):
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "validated candidate coverage is not exact")
    if type(request.pair_assessments) is not tuple:
        raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "pair assessments are mutable")
    pair_keys: set[tuple[str, str]] = set()
    for assessment in request.pair_assessments:
        validate_conflict_pair_assessment(assessment, evaluator=evaluator)
        if assessment.context_id != request.context_id:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "pair assessment belongs to another context")
        key = (assessment.left_descriptor_id.value, assessment.right_descriptor_id.value)
        if key in pair_keys or key[0] not in validated_ids or key[1] not in validated_ids:
            raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "pair assessment coverage is invalid")
        pair_keys.add(key)
    required_pairs = {
        (left, right)
        for index, left in enumerate(validated_ids)
        for right in validated_ids[index + 1 :]
    }
    if not request.incomplete_candidate_keys:
        assessment_by_pair = {
            (item.left_descriptor_id.value, item.right_descriptor_id.value): item
            for item in request.pair_assessments
        }
        for proposal in request.proposals:
            pair = tuple(sorted((proposal.left_descriptor_id.value, proposal.right_descriptor_id.value)))
            assessment = assessment_by_pair.get(pair)
            if assessment is None or not any(proposal_id == proposal.proposal_id for proposal_id, _ in assessment.proposal_dispositions):
                raise _fail(CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE, "proposal disposition is absent from pair audit")
    request_bytes = _canonical(_request_payload(request))
    _record(request.request_id, IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, "conflict request id")
    try:
        validate_record_id(request.request_id, canonical_bytes=request_bytes)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "conflict request identity mismatch") from exc
    expected_kind = (
        ConflictDecisionKind.SCAN_INCOMPLETE
        if request.incomplete_candidate_keys or pair_keys != required_pairs
        else ConflictDecisionKind.UNRESOLVED_CONFLICT
        if any(item.conflict_kind is not None for item in request.pair_assessments)
        else ConflictDecisionKind.NO_CONFLICT_FOUND
    )
    if value.decision_kind is not expected_kind:
        raise _fail(CompatibilityFailureCode.CONFLICT_EVIDENCE_INVALID, "conflict decision contradicts complete request")
    expected_proposal = compute_proposal_id(canonical_bytes=request_bytes)
    if value.proposal_id.to_dict() != expected_proposal.to_dict():
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "conflict proposal identity differs")
    try:
        validate_independence_proof(value.independence_proof)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "conflict independence proof is invalid") from exc
    _validate_evaluator_actor_independence(
        evaluator,
        derived_actors=request.actor_coverage,
        proposer=value.independence_proof.proposer_identity,
        executor=value.independence_proof.executor_identity,
    )
    if value.independence_proof.authority_identity != evaluator._declaration.evaluator_identity or value.independence_proof.subject_proposal_id.to_dict() != expected_proposal.to_dict():
        raise _fail(CompatibilityFailureCode.EVALUATOR_NOT_INDEPENDENT, "conflict authority proof differs")
    decision_bytes = _canonical({"schema_version": COMPATIBILITY_CONFLICT_SCAN_V1, "request_id": request.request_id.to_dict(), "decision_kind": value.decision_kind.value})
    expected_decision = compute_authority_decision_id(canonical_bytes=decision_bytes, independence_proof=value.independence_proof)
    if value.decision_id.to_dict() != expected_decision.to_dict():
        raise _fail(CompatibilityFailureCode.AUTHORITY_DECISION_INVALID, "conflict authority decision identity differs")
    _timestamp(value.created_at_utc, "conflict timestamp")
    scan_bytes = _canonical(_conflict_scan_payload(value))
    _record(value.scan_id, IdentityDomain.COMPATIBILITY_CONFLICT_SCAN, "conflict scan id")
    try:
        validate_record_id(value.scan_id, canonical_bytes=scan_bytes)
    except ValueError as exc:
        raise _fail(CompatibilityFailureCode.INVALID_IDENTITY, "conflict scan identity mismatch") from exc


__all__ = (
    "COMPATIBILITY_EVALUATOR_DECLARATION_V1", "COMPATIBILITY_CONTEXT_V1",
    "COMPATIBILITY_SUBJECT_DESCRIPTOR_V1", "COMPATIBILITY_DIMENSION_RECORD_V1",
    "COMPATIBILITY_EVIDENCE_V1", "COMPATIBILITY_DECISION_V1",
    "COMPATIBILITY_REVALIDATION_V1", "CONFLICT_EVIDENCE_PROPOSAL_V1",
    "CONFLICT_EVALUATION_REQUEST_V1", "COMPATIBILITY_CONFLICT_SCAN_V1",
    "CONFLICT_PAIR_ASSESSMENT_V1",
    "COMPATIBILITY_POLICY_V1", "COMPATIBILITY_COMPARATOR_PROFILE_V1",
    "COMPATIBILITY_MEDIA_TYPE_V1", "CompatibilityFailureCode", "CompatibilityViolation",
    "CompatibilityDimension", "REQUIRED_COMPATIBILITY_DIMENSIONS", "DimensionResult",
    "CompatibilityReason", "CompatibilityValueState", "CompatibilityValue",
    "EvidenceCompleteness", "CompatibilityDecisionKind", "COMPATIBILITY_DECISION_PRECEDENCE",
    "RevalidationStage", "RevalidationOutcome", "ConflictKind", "ConflictDecisionKind",
    "ConflictProposalDisposition",
    "CompatibilityEvaluatorDeclaration", "ConfiguredCompatibilityEvaluator",
    "CompatibilityContext", "CompatibilitySubjectDescriptor", "CompatibilitySubjectEvidence",
    "CompatibilityDimensionRecord", "CompatibilityEvidence", "CompatibilityDecision",
    "CompatibilityRevalidationRecord", "ConflictEvidenceProposal", "ConflictPairAssessment",
    "ConflictEvaluationRequest",
    "CompatibilityConflictScan", "compatibility_value", "absent_compatibility_value",
    "create_compatibility_evaluator_declaration", "validate_compatibility_evaluator_declaration",
    "configure_compatibility_evaluator", "require_configured_compatibility_evaluator",
    "create_compatibility_context", "validate_compatibility_context",
    "create_compatibility_subject_descriptor", "validate_compatibility_subject_descriptor",
    "create_compatibility_subject_evidence", "validate_compatibility_subject_evidence",
    "validate_loaded_compatibility_subject",
    "compatibility_subject_descriptor_from_dict", "reconcile_index_entry",
    "validate_compatibility_dimension_record", "evaluate_compatibility",
    "validate_compatibility_evidence", "validate_compatibility_decision",
    "revalidate_before_loading", "revalidate_before_consumption",
    "validate_compatibility_revalidation_record", "require_revalidation_passed",
    "create_conflict_evidence_proposal", "validate_conflict_evidence_proposal",
    "validate_conflict_pair_assessment",
    "evaluate_conflicts", "validate_compatibility_conflict_scan",
)
