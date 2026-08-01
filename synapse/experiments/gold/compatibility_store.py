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
    AttemptId,
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
    ProposalId,
    ReasonCode,
    RecordId,
    RepositoryRevision,
    RepositoryRevisionKind,
    RunId,
    SchemaVersion,
    Stage4AuthorityHandle,
    compute_authority_decision_id,
    compute_payload_sha256,
    compute_proposal_id,
    compute_record_id,
    create_common_envelope,
    create_history_anchor,
    create_independence_proof,
    independence_proof_from_dict,
    record_id_from_dict,
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
from .coordination import (
    CoordinatedFenceLease,
    SnapshotCoordinationContext,
    coordinated_store_write,
    open_coordinated_snapshot_fence,
    require_coordinated_fence_lease,
    validate_snapshot_coordination_context,
)
from .persistence import (
    MAX_METADATA_BYTES_V1,
    PersistenceFailureCode,
    PersistenceViolation,
    append_journal_payload,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    scan_journal,
    truncate_journal_to_valid_prefix,
    write_staged_bytes,
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


class CompatibilityHistoryFailureCode(str, Enum):
    RECORD_NOT_DURABLE = "RECORD_NOT_DURABLE"
    PREDECESSOR_MISMATCH = "PREDECESSOR_MISMATCH"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"
    FAST_FORWARD_DETECTED = "FAST_FORWARD_DETECTED"
    LOCK_BUSY = "LOCK_BUSY"


class CompatibilityHistoryViolation(ValueError):
    def __init__(
        self,
        failure_code: CompatibilityHistoryFailureCode,
        detail: str,
    ) -> None:
        if type(failure_code) is not CompatibilityHistoryFailureCode:
            raise TypeError(
                "failure_code must be CompatibilityHistoryFailureCode"
            )
        if type(detail) is not str or not detail or len(detail) > 512:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: CompatibilityFailureCode | CompatibilityHistoryFailureCode,
    detail: str,
) -> CompatibilityViolation | CompatibilityHistoryViolation:
    if type(code) is CompatibilityHistoryFailureCode:
        return CompatibilityHistoryViolation(code, detail)
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
            "compatibility store canonical payload is invalid",
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


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} must be a bounded identifier",
        )
    return value


class CompatibilityStoredRecordKind(str, Enum):
    CONTEXT_V1 = "CONTEXT_V1"
    EVIDENCE_V1 = "EVIDENCE_V1"
    DECISION_V1 = "DECISION_V1"
    REVALIDATION_V1 = "REVALIDATION_V1"
    CONFLICT_SCAN_V1 = "CONFLICT_SCAN_V1"
    CONTEXT_V2 = "CONTEXT_V2"
    EVIDENCE_V2 = "EVIDENCE_V2"
    DECISION_V2 = "DECISION_V2"
    REVALIDATION_V2 = "REVALIDATION_V2"


_COMPATIBILITY_RECORD_SCHEMA = {
    CompatibilityStoredRecordKind.CONTEXT_V1: COMPATIBILITY_CONTEXT_V1,
    CompatibilityStoredRecordKind.EVIDENCE_V1: COMPATIBILITY_EVIDENCE_V1,
    CompatibilityStoredRecordKind.DECISION_V1: COMPATIBILITY_DECISION_V1,
    CompatibilityStoredRecordKind.REVALIDATION_V1: COMPATIBILITY_REVALIDATION_V1,
    CompatibilityStoredRecordKind.CONFLICT_SCAN_V1: COMPATIBILITY_CONFLICT_SCAN_V1,
    CompatibilityStoredRecordKind.CONTEXT_V2: COMPATIBILITY_CONTEXT_V2,
    CompatibilityStoredRecordKind.EVIDENCE_V2: COMPATIBILITY_EVIDENCE_V2,
    CompatibilityStoredRecordKind.DECISION_V2: COMPATIBILITY_DECISION_V2,
    CompatibilityStoredRecordKind.REVALIDATION_V2: COMPATIBILITY_REVALIDATION_V2,
}

_COMPATIBILITY_V2_DOMAIN = {
    CompatibilityStoredRecordKind.CONTEXT_V2: IdentityDomain.COMPATIBILITY_CONTEXT_V2,
    CompatibilityStoredRecordKind.EVIDENCE_V2: IdentityDomain.COMPATIBILITY_EVIDENCE_V2,
    CompatibilityStoredRecordKind.DECISION_V2: IdentityDomain.AUTHORITY_DECISION,
    CompatibilityStoredRecordKind.REVALIDATION_V2: (
        IdentityDomain.COMPATIBILITY_REVALIDATION_V2
    ),
}

_COMPATIBILITY_V2_FIELDS = {
    CompatibilityStoredRecordKind.CONTEXT_V2: {
        "schema_version",
        "repository_knowledge_snapshot_id",
        "atomic_boundary_id",
        "boundary_commit_sequence",
        "historical_context_ref",
        "declared_input_refs",
        "base_configuration_id",
        "knowledge_admission_configuration_id",
    },
    CompatibilityStoredRecordKind.EVIDENCE_V2: {
        "schema_version",
        "context_ref",
        "subject_ref",
        "dimensions",
        "source_evidence_refs",
        "observed_at_utc",
    },
    CompatibilityStoredRecordKind.DECISION_V2: {
        "schema_version",
        "context_ref",
        "evidence_ref",
        "decision_kind",
        "evaluator_declaration_id",
        "base_configuration_id",
        "knowledge_admission_configuration_id",
        "independence_proof",
        "evaluated_at_utc",
        "valid_until_utc",
    },
    CompatibilityStoredRecordKind.REVALIDATION_V2: {
        "schema_version",
        "context_ref",
        "decision_ref",
        "evidence_ref",
        "stage",
        "outcome",
        "checked_dimension_results",
        "observed_head_refs",
        "evaluated_at_utc",
        "predecessor_revalidation_id",
        "predecessor_revalidation_ref",
    },
}


def _record_text_from_transport(
    value: object,
    *,
    expected_domain: IdentityDomain,
    name: str,
) -> str:
    if (
        type(value) is not dict
        or set(value) != {"domain", "digest_sha256"}
        or value.get("domain") != expected_domain.value
        or type(value.get("digest_sha256")) is not str
        or _SHA256_RE.fullmatch(value["digest_sha256"]) is None
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} identity is invalid",
        )
    return f"{expected_domain.value}:{value['digest_sha256']}"


def _transport_ref(value: object, name: str) -> HashBoundRef:
    try:
        result = HashBoundRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} is invalid",
        ) from exc
    _ref(result, None, name)
    return result


def _transport_refs(value: object, name: str) -> tuple[HashBoundRef, ...]:
    if type(value) is not list:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            f"{name} transport must be an exact list",
        )
    result = tuple(_transport_ref(item, f"{name} entry") for item in value)
    keys = tuple((item.kind.value, item.ref_id, item.sha256) for item in result)
    if len(set(keys)) != len(keys) or keys != tuple(sorted(keys)):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            f"{name} must be canonical and duplicate-free",
        )
    return result


def _parse_enveloped_compatibility_artifact(
    *,
    raw: bytes,
    record_kind: CompatibilityStoredRecordKind,
) -> tuple[dict[str, object], dict[str, object], RecordId]:
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility v2 artifact is not strict JSON",
        ) from exc
    if (
        type(artifact) is not dict
        or set(artifact) != {"envelope", "payload"}
        or _canonical(artifact) != raw
        or type(artifact["envelope"]) is not dict
        or type(artifact["payload"]) is not dict
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility v2 artifact shape is invalid",
        )
    envelope = artifact["envelope"]
    payload = artifact["payload"]
    if (
        set(payload) != _COMPATIBILITY_V2_FIELDS[record_kind]
        or payload.get("schema_version")
        != _COMPATIBILITY_RECORD_SCHEMA[record_kind]
    ):
        raise _fail(
            CompatibilityFailureCode.UNKNOWN_SCHEMA,
            "compatibility v2 payload schema is invalid",
        )
    envelope_fields = {
        "schema_version",
        "record_id",
        "run_id",
        "attempt_id",
        "created_at_utc",
        "producer_component",
        "repository_revision",
        "policy_version",
        "environment_profile_id",
        "lineage_parent_ids",
        "payload_sha256",
    }
    payload_bytes = _canonical(payload)
    if (
        set(envelope) != envelope_fields
        or envelope.get("schema_version")
        != SchemaVersion.COMMON_ENVELOPE_V1.value
        or envelope.get("payload_sha256")
        != compute_payload_sha256(canonical_bytes=payload_bytes)
        or type(envelope.get("producer_component")) is not str
        or not envelope["producer_component"]
        or type(envelope.get("policy_version")) is not str
        or not envelope["policy_version"]
        or type(envelope.get("environment_profile_id")) is not str
        or not envelope["environment_profile_id"]
        or type(envelope.get("lineage_parent_ids")) is not list
        or not envelope["lineage_parent_ids"]
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility v2 envelope is invalid",
        )
    RunId.from_dict(envelope["run_id"])
    AttemptId.from_dict(envelope["attempt_id"])
    RepositoryRevision.from_dict(envelope["repository_revision"])
    try:
        created = datetime.strptime(
            envelope["created_at_utc"],
            _UTC_FORMAT,
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility v2 envelope timestamp is invalid",
        ) from exc
    if _timestamp_text(created) != envelope["created_at_utc"]:
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility v2 envelope timestamp is not exact UTC",
        )
    for parent in envelope["lineage_parent_ids"]:
        if (
            type(parent) is not dict
            or set(parent) != {"parent_record_id", "edge_kind"}
            or type(parent.get("parent_record_id")) is not dict
            or set(parent["parent_record_id"])
            != {"domain", "digest_sha256"}
            or parent.get("edge_kind")
            not in {item.value for item in LineageEdgeKind}
        ):
            raise _fail(
                CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                "compatibility v2 lineage is invalid",
            )
        try:
            parent_domain = IdentityDomain(parent["parent_record_id"]["domain"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.INVALID_IDENTITY,
                "compatibility v2 lineage domain is invalid",
            ) from exc
        _record_text_from_transport(
            parent["parent_record_id"],
            expected_domain=parent_domain,
            name="compatibility lineage parent",
        )
    envelope_record_id = record_id_from_dict(
        envelope["record_id"],
        canonical_bytes=payload_bytes,
    )
    expected_domain = _COMPATIBILITY_V2_DOMAIN[record_kind]
    if envelope_record_id.domain is not expected_domain:
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "compatibility v2 envelope identity domain is invalid",
        )
    return envelope, payload, envelope_record_id


def _validate_dimension_transport(value: object) -> None:
    fields = {
        "schema_version",
        "dimension",
        "producer_value",
        "consumer_value",
        "comparator_id",
        "comparator_version",
        "result",
        "reason",
        "evidence_refs",
        "record_id",
    }
    if type(value) is not dict or set(value) != fields:
        raise _fail(
            CompatibilityFailureCode.DIMENSION_MISMATCH,
            "compatibility dimension transport shape is invalid",
        )
    try:
        dimension = CompatibilityDimension(value["dimension"])
        DimensionResult(value["result"])
        CompatibilityReason(value["reason"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            CompatibilityFailureCode.DIMENSION_MISMATCH,
            "compatibility dimension enum is invalid",
        ) from exc
    if (
        value["schema_version"] != COMPATIBILITY_DIMENSION_RECORD_V1
        or value["comparator_id"]
        != f"{COMPATIBILITY_COMPARATOR_PROFILE_V1}:{dimension.value}"
        or value["comparator_version"] != COMPATIBILITY_COMPARATOR_PROFILE_V1
    ):
        raise _fail(
            CompatibilityFailureCode.UNKNOWN_COMPARATOR,
            "compatibility dimension comparator is invalid",
        )
    for item_name in ("producer_value", "consumer_value"):
        item = value[item_name]
        if (
            type(item) is not dict
            or set(item) != {"state", "label", "sha256", "refs"}
        ):
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISMATCH,
                "compatibility value transport is invalid",
            )
        try:
            state = CompatibilityValueState(item["state"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISMATCH,
                "compatibility value state is invalid",
            ) from exc
        _transport_refs(item["refs"], "compatibility value refs")
        present_states = {
            CompatibilityValueState.PRESENT,
            CompatibilityValueState.EMPTY,
            CompatibilityValueState.NULL,
            CompatibilityValueState.FALSE,
            CompatibilityValueState.ZERO,
        }
        if state in present_states:
            _safe_id(item["label"], "compatibility value label")
            if (
                type(item["sha256"]) is not str
                or _SHA256_RE.fullmatch(item["sha256"]) is None
            ):
                raise _fail(
                    CompatibilityFailureCode.INVALID_IDENTITY,
                    "compatibility value hash is invalid",
                )
        elif item["label"] is not None or item["sha256"] is not None or item["refs"]:
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISMATCH,
                "compatibility absence state carries a value",
            )
    _transport_refs(value["evidence_refs"], "dimension evidence refs")
    identity_payload = {key: value[key] for key in fields if key != "record_id"}
    record_id_from_dict(
        value["record_id"],
        canonical_bytes=_canonical(identity_payload),
    )


def _validate_compatibility_artifact_bytes(
    *,
    record_kind: CompatibilityStoredRecordKind,
    record_identity: str,
    artifact_bytes: bytes,
    artifact_ref: HashBoundRef,
    authority_binding: KnowledgeAdmissionAuthorityBinding | None,
    committed_records: tuple[CompatibilityCommitEvidence, ...] = (),
    artifact_root: Path | None = None,
) -> None:
    if (
        type(artifact_bytes) is not bytes
        or artifact_ref.sha256 != hashlib.sha256(artifact_bytes).hexdigest()
        or artifact_ref.byte_length != len(artifact_bytes)
        or artifact_ref.ref_id != f"artifact:{artifact_ref.sha256}"
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "compatibility artifact differs from its exact ref",
        )
    if record_kind not in _COMPATIBILITY_V2_DOMAIN:
        try:
            payload = json.loads(artifact_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(
                CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                "historical compatibility artifact is not JSON",
            ) from exc
        if (
            type(payload) is not dict
            or _canonical(payload) != artifact_bytes
            or payload.get("schema_version")
            != _COMPATIBILITY_RECORD_SCHEMA[record_kind]
        ):
            raise _fail(
                CompatibilityFailureCode.UNKNOWN_SCHEMA,
                "historical compatibility artifact schema is invalid",
            )
        return
    if authority_binding is None:
        raise _fail(
            CompatibilityFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "v2 compatibility durability requires authority binding",
        )
    envelope, payload, envelope_record_id = _parse_enveloped_compatibility_artifact(
        raw=artifact_bytes,
        record_kind=record_kind,
    )
    base, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    if record_kind is CompatibilityStoredRecordKind.CONTEXT_V2:
        _record_text_from_transport(
            payload["repository_knowledge_snapshot_id"],
            expected_domain=IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
            name="repository knowledge snapshot",
        )
        _record_text_from_transport(
            payload["atomic_boundary_id"],
            expected_domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            name="atomic boundary",
        )
        if (
            type(payload["boundary_commit_sequence"]) is not int
            or payload["boundary_commit_sequence"] < 1
            or payload["base_configuration_id"]
            != base.configuration_id.to_dict()
            or payload["knowledge_admission_configuration_id"]
            != overlay.configuration_id.to_dict()
        ):
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility context authority is invalid",
            )
        _transport_ref(payload["historical_context_ref"], "historical context ref")
        if not _transport_refs(payload["declared_input_refs"], "declared input refs"):
            raise _fail(
                CompatibilityFailureCode.EVIDENCE_INCOMPLETE,
                "v2 compatibility declared inputs are empty",
            )
        if envelope["lineage_parent_ids"] != [
            {
                "parent_record_id": payload["repository_knowledge_snapshot_id"],
                "edge_kind": LineageEdgeKind.REFERENCES.value,
            },
            {
                "parent_record_id": payload["atomic_boundary_id"],
                "edge_kind": LineageEdgeKind.REFERENCES.value,
            },
        ]:
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility context lineage is invalid",
            )
    elif record_kind is CompatibilityStoredRecordKind.EVIDENCE_V2:
        context_ref = _transport_ref(payload["context_ref"], "context ref")
        _transport_ref(payload["subject_ref"], "subject ref")
        _transport_refs(payload["source_evidence_refs"], "source evidence refs")
        if type(payload["dimensions"]) is not list:
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISSING,
                "v2 compatibility dimensions transport is invalid",
            )
        dimensions = tuple(payload["dimensions"])
        if tuple(item.get("dimension") for item in dimensions) != tuple(
            item.value for item in REQUIRED_COMPATIBILITY_DIMENSIONS
        ):
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISSING,
                "v2 compatibility dimensions are incomplete or reordered",
            )
        for item in dimensions:
            _validate_dimension_transport(item)
        try:
            observed = datetime.strptime(
                payload["observed_at_utc"],
                _UTC_FORMAT,
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "v2 compatibility evidence timestamp is invalid",
            ) from exc
        if (
            _timestamp_text(observed) != payload["observed_at_utc"]
            or envelope["created_at_utc"] != payload["observed_at_utc"]
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "v2 compatibility evidence timestamp is not exact UTC",
            )
        context_commit = _require_committed_compatibility_ref(
            context_ref,
            CompatibilityStoredRecordKind.CONTEXT_V2,
            committed_records,
        )
        context_envelope, _, context_id = _load_committed_v2_artifact(
            artifact_root=artifact_root,
            evidence=context_commit,
        )
        _require_same_compatibility_context(envelope, context_envelope)
        if envelope["lineage_parent_ids"] != [
            {
                "parent_record_id": context_id.to_dict(),
                "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
            }
        ]:
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility evidence lineage is invalid",
            )
    elif record_kind is CompatibilityStoredRecordKind.DECISION_V2:
        context_ref = _transport_ref(payload["context_ref"], "context ref")
        evidence_ref = _transport_ref(payload["evidence_ref"], "evidence ref")
        try:
            kind = CompatibilityDecisionKind(payload["decision_kind"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.DECISION_MISMATCH,
                "v2 compatibility decision kind is invalid",
            ) from exc
        decision_basis = _canonical(
            {
                "schema_version": COMPATIBILITY_DECISION_V2,
                "context_ref": context_ref.to_dict(),
                "evidence_ref": evidence_ref.to_dict(),
                "decision_kind": kind.value,
                "evaluator_declaration_id": payload["evaluator_declaration_id"],
                "base_configuration_id": payload["base_configuration_id"],
                "knowledge_admission_configuration_id": payload[
                    "knowledge_admission_configuration_id"
                ],
            }
        )
        proof = independence_proof_from_dict(
            payload["independence_proof"],
            proposal_canonical_bytes=decision_basis,
        )
        decision_id = compute_authority_decision_id(
            canonical_bytes=_canonical(payload),
            independence_proof=proof,
        )
        try:
            evaluated = datetime.strptime(
                payload["evaluated_at_utc"],
                _UTC_FORMAT,
            ).replace(tzinfo=timezone.utc)
            expiry = datetime.strptime(
                payload["valid_until_utc"],
                _UTC_FORMAT,
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "v2 compatibility decision timestamps are invalid",
            ) from exc
        if (
            record_identity != decision_id.record_id.value
            or payload["evaluator_declaration_id"]
            != overlay.compatibility_evaluator_declaration_id.to_dict()
            or payload["base_configuration_id"] != base.configuration_id.to_dict()
            or payload["knowledge_admission_configuration_id"]
            != overlay.configuration_id.to_dict()
            or proof.authority_identity != overlay.compatibility_evaluator_identity
            or proof.authority_role is not AuthorityRole.COMPATIBILITY_EVALUATOR
            or proof.reason_code
            is not ReasonCode.COMPATIBILITY_EVALUATION_INDEPENDENT
            or expiry <= evaluated
            or _timestamp_text(evaluated) != payload["evaluated_at_utc"]
            or _timestamp_text(expiry) != payload["valid_until_utc"]
        ):
            raise _fail(
                CompatibilityFailureCode.AUTHORITY_DECISION_INVALID,
                "v2 compatibility decision authority is invalid",
            )
        context_commit = _require_committed_compatibility_ref(
            context_ref,
            CompatibilityStoredRecordKind.CONTEXT_V2,
            committed_records,
        )
        evidence_commit = _require_committed_compatibility_ref(
            evidence_ref,
            CompatibilityStoredRecordKind.EVIDENCE_V2,
            committed_records,
        )
        context_envelope, _, context_id = _load_committed_v2_artifact(
            artifact_root=artifact_root,
            evidence=context_commit,
        )
        evidence_envelope, _, evidence_id = _load_committed_v2_artifact(
            artifact_root=artifact_root,
            evidence=evidence_commit,
        )
        _require_same_compatibility_context(envelope, context_envelope)
        _require_same_compatibility_context(envelope, evidence_envelope)
        if envelope["lineage_parent_ids"] != [
            {
                "parent_record_id": context_id.to_dict(),
                "edge_kind": LineageEdgeKind.REFERENCES.value,
            },
            {
                "parent_record_id": evidence_id.to_dict(),
                "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
            },
        ]:
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility decision lineage is invalid",
            )
    else:
        context_ref = _transport_ref(payload["context_ref"], "context ref")
        decision_ref = _transport_ref(payload["decision_ref"], "decision ref")
        evidence_ref = _transport_ref(payload["evidence_ref"], "evidence ref")
        try:
            stage = RevalidationStage(payload["stage"])
            RevalidationOutcome(payload["outcome"])
            results = tuple(
                DimensionResult(item)
                for item in payload["checked_dimension_results"]
            )
            evaluated = datetime.strptime(
                payload["evaluated_at_utc"],
                _UTC_FORMAT,
            ).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as exc:
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "v2 compatibility revalidation transport is invalid",
            ) from exc
        if (
            len(results) != len(REQUIRED_COMPATIBILITY_DIMENSIONS)
            or _timestamp_text(evaluated) != payload["evaluated_at_utc"]
        ):
            raise _fail(
                CompatibilityFailureCode.DIMENSION_MISSING,
                "v2 compatibility revalidation dimensions are incomplete",
            )
        _transport_refs(payload["observed_head_refs"], "observed head refs")
        predecessor_id = payload["predecessor_revalidation_id"]
        predecessor_ref_value = payload["predecessor_revalidation_ref"]
        predecessor_ref = None
        if predecessor_id is not None or predecessor_ref_value is not None:
            if predecessor_id is None or predecessor_ref_value is None:
                raise _fail(
                    CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                    "v2 compatibility predecessor identity/ref is partial",
                )
            _record_text_from_transport(
                predecessor_id,
                expected_domain=IdentityDomain.COMPATIBILITY_REVALIDATION_V2,
                name="compatibility predecessor revalidation",
            )
            predecessor_ref = _transport_ref(
                predecessor_ref_value,
                "compatibility predecessor revalidation ref",
            )
        if (
            stage is RevalidationStage.BEFORE_LOADING
            and predecessor_ref is not None
        ) or (
            stage is RevalidationStage.BEFORE_CONSUMPTION
            and predecessor_ref is None
        ):
            raise _fail(
                CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                "v2 compatibility predecessor does not match revalidation stage",
            )
        dependency_evidence = tuple(
            _require_committed_compatibility_ref(ref, kind, committed_records)
            for ref, kind in (
            (context_ref, CompatibilityStoredRecordKind.CONTEXT_V2),
            (decision_ref, CompatibilityStoredRecordKind.DECISION_V2),
            (evidence_ref, CompatibilityStoredRecordKind.EVIDENCE_V2),
            )
        )
        dependencies = tuple(
            _load_committed_v2_artifact(
                artifact_root=artifact_root,
                evidence=item,
            )
            for item in dependency_evidence
        )
        predecessor_dependency = None
        if predecessor_ref is not None:
            predecessor_evidence = _require_committed_compatibility_ref(
                predecessor_ref,
                CompatibilityStoredRecordKind.REVALIDATION_V2,
                committed_records,
            )
            predecessor_dependency = _load_committed_v2_artifact(
                artifact_root=artifact_root,
                evidence=predecessor_evidence,
            )
        for dependency_envelope, _, _ in dependencies:
            _require_same_compatibility_context(envelope, dependency_envelope)
        context_id = dependencies[0][2]
        decision_envelope_id = dependencies[1][2]
        evidence_id = dependencies[2][2]
        expected_lineage = [
            {
                "parent_record_id": context_id.to_dict(),
                "edge_kind": LineageEdgeKind.REFERENCES.value,
            },
            {
                "parent_record_id": decision_envelope_id.to_dict(),
                "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
            },
            {
                "parent_record_id": evidence_id.to_dict(),
                "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
            },
        ]
        if predecessor_dependency is not None:
            predecessor_envelope, predecessor_payload, predecessor_record_id = (
                predecessor_dependency
            )
            _require_same_compatibility_context(envelope, predecessor_envelope)
            if (
                predecessor_record_id.to_dict() != predecessor_id
                or predecessor_payload["stage"]
                != RevalidationStage.BEFORE_LOADING.value
                or predecessor_payload["outcome"]
                != RevalidationOutcome.PASSED.value
            ):
                raise _fail(
                    CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED,
                    "v2 compatibility predecessor is not the passed loading record",
                )
            expected_lineage.append(
                {
                    "parent_record_id": predecessor_record_id.to_dict(),
                    "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
                }
            )
        if envelope["lineage_parent_ids"] != expected_lineage:
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "v2 compatibility revalidation lineage is invalid",
            )
    if (
        record_kind is not CompatibilityStoredRecordKind.DECISION_V2
        and record_identity != envelope_record_id.value
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "v2 compatibility record identity differs from its envelope",
        )


def _require_committed_compatibility_ref(
    ref: HashBoundRef,
    record_kind: CompatibilityStoredRecordKind,
    committed_records: tuple[CompatibilityCommitEvidence, ...],
) -> CompatibilityCommitEvidence:
    matches = tuple(
        item
        for item in committed_records
        if item.record_kind is record_kind and item.artifact_ref == ref
    )
    if len(matches) != 1:
        raise _fail(
            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
            "compatibility authority dependency is not uniquely durably included",
        )
    return matches[0]


def _load_committed_v2_artifact(
    *,
    artifact_root: Path | None,
    evidence: CompatibilityCommitEvidence,
) -> tuple[dict[str, object], dict[str, object], RecordId]:
    if artifact_root is None:
        raise _fail(
            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
            "compatibility artifact root is unavailable",
        )
    raw = read_regular_bytes(
        _compatibility_artifact_path(artifact_root, evidence.artifact_ref),
        maximum_bytes=MAX_METADATA_BYTES_V1,
    )
    if (
        hashlib.sha256(raw).hexdigest() != evidence.artifact_ref.sha256
        or len(raw) != evidence.artifact_ref.byte_length
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility dependency differs from its committed ref",
        )
    return _parse_enveloped_compatibility_artifact(
        raw=raw,
        record_kind=evidence.record_kind,
    )


def _require_same_compatibility_context(
    left: dict[str, object],
    right: dict[str, object],
) -> None:
    context_fields = (
        "run_id",
        "attempt_id",
        "repository_revision",
        "policy_version",
        "environment_profile_id",
    )
    if any(left[name] != right[name] for name in context_fields):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "committed compatibility graph mixes common envelope contexts",
        )


_STORED_ARTIFACT_SEAL = object()


@dataclass(frozen=True, init=False)
class CompatibilityStoredArtifact:
    record_kind: CompatibilityStoredRecordKind
    record_identity: str
    artifact_bytes: bytes
    artifact_ref: HashBoundRef
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityStoredArtifact:
        raise TypeError("CompatibilityStoredArtifact is factory-created")


def create_compatibility_stored_artifact(
    *,
    record_kind: CompatibilityStoredRecordKind,
    record_identity: str,
    artifact_bytes: bytes,
    artifact_ref: HashBoundRef,
) -> CompatibilityStoredArtifact:
    if (
        type(record_kind) is not CompatibilityStoredRecordKind
        or record_kind not in _COMPATIBILITY_V2_DOMAIN
    ):
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "compatibility stored artifact requires an exact v2 record kind",
        )
    identity = _safe_id(record_identity, "compatibility record identity")
    if type(artifact_bytes) is not bytes:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "compatibility stored artifact bytes must be exact bytes",
        )
    _ref(artifact_ref, RefKind.ARTIFACT, "compatibility artifact ref")
    if (
        artifact_ref.sha256 != hashlib.sha256(artifact_bytes).hexdigest()
        or artifact_ref.byte_length != len(artifact_bytes)
        or artifact_ref.ref_id != f"artifact:{artifact_ref.sha256}"
        or artifact_ref.schema_id != ENVELOPED_ARTIFACT_SCHEMA_V1
        or artifact_ref.media_type != ENVELOPED_ARTIFACT_MEDIA_TYPE_V1
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "compatibility stored artifact differs from its hash-bound ref",
        )
    result = object.__new__(CompatibilityStoredArtifact)
    object.__setattr__(result, "record_kind", record_kind)
    object.__setattr__(result, "record_identity", identity)
    object.__setattr__(result, "artifact_bytes", artifact_bytes)
    object.__setattr__(result, "artifact_ref", artifact_ref)
    object.__setattr__(result, "_trusted_seal", _STORED_ARTIFACT_SEAL)
    validate_compatibility_stored_artifact(result)
    return result


def validate_compatibility_stored_artifact(
    value: CompatibilityStoredArtifact,
) -> None:
    if (
        type(value) is not CompatibilityStoredArtifact
        or getattr(value, "_trusted_seal", None) is not _STORED_ARTIFACT_SEAL
        or type(value.record_kind) is not CompatibilityStoredRecordKind
        or value.record_kind not in _COMPATIBILITY_V2_DOMAIN
    ):
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "compatibility stored artifact is not factory sealed",
        )
    _safe_id(value.record_identity, "compatibility record identity")
    if type(value.artifact_bytes) is not bytes:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "compatibility stored artifact bytes changed",
        )
    _ref(value.artifact_ref, RefKind.ARTIFACT, "compatibility artifact ref")
    if (
        value.artifact_ref.sha256
        != hashlib.sha256(value.artifact_bytes).hexdigest()
        or value.artifact_ref.byte_length != len(value.artifact_bytes)
        or value.artifact_ref.ref_id != f"artifact:{value.artifact_ref.sha256}"
        or value.artifact_ref.schema_id != ENVELOPED_ARTIFACT_SCHEMA_V1
        or value.artifact_ref.media_type != ENVELOPED_ARTIFACT_MEDIA_TYPE_V1
    ):
        raise _fail(
            CompatibilityFailureCode.INVALID_IDENTITY,
            "compatibility stored artifact integrity changed",
        )


@dataclass(frozen=True, init=False)
class CompatibilityCommitEvidence:
    schema_version: str
    record_kind: CompatibilityStoredRecordKind
    domain_schema: str
    record_identity: str
    artifact_ref: HashBoundRef
    predecessor_identity: str | None
    sequence: int
    fence_epoch: int
    history_anchor: HistoryAnchor
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityCommitEvidence:
        raise TypeError("CompatibilityCommitEvidence is store-created")

    def to_dict(self) -> dict[str, object]:
        validate_compatibility_commit_evidence(self)
        return {
            "schema_version": self.schema_version,
            "record_kind": self.record_kind.value,
            "domain_schema": self.domain_schema,
            "record_identity": self.record_identity,
            "artifact_ref": self.artifact_ref.to_dict(),
            "predecessor_identity": self.predecessor_identity,
            "sequence": self.sequence,
            "fence_epoch": self.fence_epoch,
            "history_anchor": self.history_anchor.to_dict(),
        }


def validate_compatibility_commit_evidence(
    value: CompatibilityCommitEvidence,
) -> None:
    if (
        type(value) is not CompatibilityCommitEvidence
        or getattr(value, "_trusted_seal", None) is not _COMMIT_SEAL
        or value.schema_version != COMPATIBILITY_COMMIT_EVIDENCE_SCHEMA_V1
        or type(value.record_kind) is not CompatibilityStoredRecordKind
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
            "compatibility commit evidence is not store sealed",
        )
    if value.domain_schema != _COMPATIBILITY_RECORD_SCHEMA[value.record_kind]:
        raise _fail(
            CompatibilityFailureCode.UNKNOWN_SCHEMA,
            "compatibility commit domain schema is invalid",
        )
    _safe_id(value.record_identity, "compatibility record identity")
    _ref(value.artifact_ref, RefKind.ARTIFACT, "compatibility artifact ref")
    if value.predecessor_identity is not None:
        _safe_id(value.predecessor_identity, "compatibility predecessor")
    if (
        type(value.sequence) is not int
        or value.sequence < 1
        or (value.predecessor_identity is None) != (value.sequence == 1)
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.PREDECESSOR_MISMATCH,
            "compatibility commit sequence or predecessor is invalid",
        )
    if type(value.fence_epoch) is not int or value.fence_epoch < 1:
        raise _fail(
            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
            "compatibility fence epoch is invalid",
        )
    validate_history_anchor(value.history_anchor)
    if (
        value.history_anchor.history_domain is not HistoryDomain.COMPATIBILITY
        or value.history_anchor.entry_count != value.sequence
        or value.history_anchor.domain_heads != (value.record_identity,)
    ):
        raise _fail(
            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
            "compatibility history anchor differs from commit evidence",
        )


@dataclass(frozen=True)
class CompatibilityHistoryRecovery:
    history_anchor: HistoryAnchor
    committed_records: tuple[CompatibilityCommitEvidence, ...]
    diagnostic: (
        CompatibilityViolation
        | CompatibilityHistoryViolation
        | PersistenceViolation
        | None
    )
    valid_prefix_length: int
    invalid_suffix: bytes

    @property
    def head(self) -> CompatibilityCommitEvidence | None:
        return self.committed_records[-1] if self.committed_records else None


def _compatibility_stored_artifact(
    value: object,
) -> tuple[CompatibilityStoredRecordKind, str, bytes, HashBoundRef]:
    if type(value) is CompatibilityStoredArtifact:
        validate_compatibility_stored_artifact(value)
        kind = value.record_kind
        identity = value.record_identity
        raw = value.artifact_bytes
        ref = value.artifact_ref
    else:
        historical = {
            CompatibilityContext: (
                CompatibilityStoredRecordKind.CONTEXT_V1,
                "context_id",
            ),
            CompatibilityEvidence: (
                CompatibilityStoredRecordKind.EVIDENCE_V1,
                "evidence_id",
            ),
            CompatibilityDecision: (
                CompatibilityStoredRecordKind.DECISION_V1,
                "decision_id",
            ),
            CompatibilityRevalidationRecord: (
                CompatibilityStoredRecordKind.REVALIDATION_V1,
                "revalidation_id",
            ),
            CompatibilityConflictScan: (
                CompatibilityStoredRecordKind.CONFLICT_SCAN_V1,
                "scan_id",
            ),
        }
        spec = historical.get(type(value))
        if spec is None:
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "compatibility store record kind is unsupported",
            )
        kind, identity_name = spec
        serializer = getattr(value, "to_dict", None)
        if not callable(serializer):
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "historical compatibility record has no public serializer",
            )
        payload = serializer()
        if type(payload) is not dict:
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "historical compatibility serialization is invalid",
            )
        raw = _canonical(payload)
        digest = hashlib.sha256(raw).hexdigest()
        ref = HashBoundRef(
            kind=RefKind.ARTIFACT,
            ref_id=f"artifact:{digest}",
            schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
            sha256=digest,
            byte_length=len(raw),
            media_type=COMPATIBILITY_MEDIA_TYPE_V1,
        )
        identity_value = getattr(value, identity_name)
        identity = (
            identity_value.record_id.value
            if type(identity_value) is AuthorityDecisionId
            else identity_value.value
        )
    return kind, identity, raw, ref


def _compatibility_store_frame(
    *,
    kind: CompatibilityStoredRecordKind,
    identity: str,
    artifact_ref: HashBoundRef,
    predecessor: str | None,
    sequence: int,
    fence_epoch: int,
) -> bytes:
    return _canonical(
        {
            "schema_version": COMPATIBILITY_HISTORY_FRAME_SCHEMA_V1,
            "record_kind": kind.value,
            "domain_schema": _COMPATIBILITY_RECORD_SCHEMA[kind],
            "record_identity": identity,
            "artifact_ref": artifact_ref.to_dict(),
            "predecessor_identity": predecessor,
            "sequence": sequence,
            "fence_epoch": fence_epoch,
        }
    )


def _compatibility_artifact_path(root: Path, ref: HashBoundRef) -> Path:
    return root / "artifacts" / f"{ref.sha256}.json"


class CompatibilityEvidenceStore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _STORE_SEAL or kwargs or len(args) != 3:
            raise TypeError("CompatibilityEvidenceStore is factory-opened")
        root, authority_binding, context = args
        if not isinstance(root, Path) or not root.is_absolute():
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "compatibility store root must be absolute",
            )
        base, overlay = validate_knowledge_admission_authority_binding(
            authority_binding
        )
        validate_snapshot_coordination_context(context)
        if (
            context.base_configuration_id_text != base.configuration_id.value
            or context.knowledge_admission_configuration_id_text
            != overlay.configuration_id.value
        ):
            raise _fail(
                CompatibilityFailureCode.CONTEXT_MISMATCH,
                "compatibility store coordination context changed",
            )
        self._root = root
        self._authority_binding = authority_binding
        self._context = context
        self._fence = open_coordinated_snapshot_fence(context)
        self._journal_path = root / "compatibility.journal"
        self._lock_path = root / "compatibility.lock"
        if not root.exists():
            ensure_directory(root)
        if not (root / "artifacts").exists():
            ensure_directory(root / "artifacts")
        if not (root / "quarantine").exists():
            ensure_directory(root / "quarantine")
        with coordinated_store_write(
            fence=self._fence,
            context=self._context,
            store_lock_path=self._lock_path,
        ):
            recovery = self.recover()
            if recovery.diagnostic is not None:
                raise recovery.diagnostic

    @property
    def coordination_context(self) -> SnapshotCoordinationContext:
        validate_snapshot_coordination_context(self._context)
        return self._context

    @property
    def configuration_id(self) -> RecordId:
        _, overlay = validate_knowledge_admission_authority_binding(
            self._authority_binding
        )
        return overlay.configuration_id

    def recover(self) -> CompatibilityHistoryRecovery:
        empty = create_history_anchor(
            history_domain=HistoryDomain.COMPATIBILITY,
            configuration_id=self.configuration_id,
            entry_sha256s=(),
            domain_heads=(),
        )
        try:
            scan = scan_journal(self._journal_path)
        except PersistenceViolation as exc:
            return CompatibilityHistoryRecovery(empty, (), exc, 0, b"")
        evidence: list[CompatibilityCommitEvidence] = []
        entry_hashes: list[str] = []
        seen: set[str] = set()
        diagnostic: (
            CompatibilityViolation
            | CompatibilityHistoryViolation
            | PersistenceViolation
            | None
        ) = None
        valid_prefix = scan.valid_prefix_length
        for frame in scan.frames:
            try:
                try:
                    data = json.loads(frame.payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility frame is not strict JSON",
                    ) from exc
                required = {
                    "schema_version",
                    "record_kind",
                    "domain_schema",
                    "record_identity",
                    "artifact_ref",
                    "predecessor_identity",
                    "sequence",
                    "fence_epoch",
                }
                if (
                    type(data) is not dict
                    or set(data) != required
                    or _canonical(data) != frame.payload
                ):
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility frame shape is invalid",
                    )
                if data["schema_version"] != COMPATIBILITY_HISTORY_FRAME_SCHEMA_V1:
                    raise _fail(
                        CompatibilityFailureCode.UNKNOWN_SCHEMA,
                        "compatibility frame schema is unknown",
                    )
                try:
                    kind = CompatibilityStoredRecordKind(data["record_kind"])
                    ref = HashBoundRef.from_dict(data["artifact_ref"])
                except (TypeError, ValueError) as exc:
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility frame enum or ref is invalid",
                    ) from exc
                if data["domain_schema"] != _COMPATIBILITY_RECORD_SCHEMA[kind]:
                    raise _fail(
                        CompatibilityFailureCode.UNKNOWN_SCHEMA,
                        "compatibility frame domain schema is unknown",
                    )
                identity = _safe_id(
                    data["record_identity"],
                    "compatibility record identity",
                )
                predecessor = data["predecessor_identity"]
                if predecessor is not None:
                    predecessor = _safe_id(
                        predecessor,
                        "compatibility predecessor",
                    )
                sequence = data["sequence"]
                expected = len(evidence) + 1
                if type(sequence) is not int or sequence < expected:
                    raise _fail(
                        CompatibilityHistoryFailureCode.ROLLBACK_DETECTED,
                        "compatibility sequence rolled back",
                    )
                if sequence > expected:
                    raise _fail(
                        CompatibilityHistoryFailureCode.FAST_FORWARD_DETECTED,
                        "compatibility sequence fast-forwarded",
                    )
                expected_predecessor = (
                    None if not evidence else evidence[-1].record_identity
                )
                if predecessor != expected_predecessor:
                    raise _fail(
                        CompatibilityHistoryFailureCode.PREDECESSOR_MISMATCH,
                        "compatibility predecessor differs from trusted head",
                    )
                if identity in seen:
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility record identity repeats",
                    )
                raw = read_regular_bytes(
                    _compatibility_artifact_path(self._root, ref),
                    maximum_bytes=MAX_METADATA_BYTES_V1,
                )
                if (
                    hashlib.sha256(raw).hexdigest() != ref.sha256
                    or len(raw) != ref.byte_length
                ):
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility artifact differs from its ref",
                    )
                _validate_compatibility_artifact_bytes(
                    record_kind=kind,
                    record_identity=identity,
                    artifact_bytes=raw,
                    artifact_ref=ref,
                    authority_binding=self._authority_binding,
                    committed_records=tuple(evidence),
                    artifact_root=self._root,
                )
                entry_hashes.append(hashlib.sha256(frame.payload).hexdigest())
                anchor = create_history_anchor(
                    history_domain=HistoryDomain.COMPATIBILITY,
                    configuration_id=self.configuration_id,
                    entry_sha256s=tuple(entry_hashes),
                    domain_heads=(identity,),
                )
                item = object.__new__(CompatibilityCommitEvidence)
                object.__setattr__(
                    item,
                    "schema_version",
                    COMPATIBILITY_COMMIT_EVIDENCE_SCHEMA_V1,
                )
                object.__setattr__(item, "record_kind", kind)
                object.__setattr__(item, "domain_schema", data["domain_schema"])
                object.__setattr__(item, "record_identity", identity)
                object.__setattr__(item, "artifact_ref", ref)
                object.__setattr__(item, "predecessor_identity", predecessor)
                object.__setattr__(item, "sequence", sequence)
                object.__setattr__(item, "fence_epoch", data["fence_epoch"])
                object.__setattr__(item, "history_anchor", anchor)
                object.__setattr__(item, "_trusted_seal", _COMMIT_SEAL)
                validate_compatibility_commit_evidence(item)
                evidence.append(item)
                seen.add(identity)
                valid_prefix = frame.end_offset
            except (
                CompatibilityViolation,
                CompatibilityHistoryViolation,
                PersistenceViolation,
                ContractViolation,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                diagnostic = (
                    exc
                    if isinstance(
                        exc,
                        (
                            CompatibilityViolation,
                            CompatibilityHistoryViolation,
                            PersistenceViolation,
                        ),
                    )
                    else _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility history validation failed",
                    )
                )
                valid_prefix = frame.start_offset
                break
        invalid_suffix = scan.torn_tail
        if diagnostic is not None:
            raw_journal = read_regular_bytes(
                self._journal_path,
                maximum_bytes=MAX_METADATA_BYTES_V1,
            )
            invalid_suffix = raw_journal[valid_prefix:]
        elif scan.torn_tail:
            diagnostic = _fail(
                CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                "compatibility journal has a torn tail",
            )
        return CompatibilityHistoryRecovery(
            evidence[-1].history_anchor if evidence else empty,
            tuple(evidence),
            diagnostic,
            valid_prefix,
            invalid_suffix,
        )

    def current_anchor(
        self,
        *,
        fence_lease: CoordinatedFenceLease | None = None,
    ) -> HistoryAnchor:
        if fence_lease is not None:
            require_coordinated_fence_lease(
                fence_lease,
                expected_context=self._context,
            )
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        return recovery.history_anchor

    def require_anchor_ancestry(
        self,
        ancestor: HistoryAnchor,
        *,
        expected_current: HistoryAnchor | None = None,
    ) -> HistoryAnchor:
        validate_history_anchor(ancestor)
        if (
            ancestor.history_domain is not HistoryDomain.COMPATIBILITY
            or ancestor.configuration_id != self.configuration_id
        ):
            raise _fail(
                CompatibilityHistoryFailureCode.PREDECESSOR_MISMATCH,
                "compatibility ancestor uses another history context",
            )
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        current = recovery.history_anchor
        if expected_current is not None:
            validate_history_anchor(expected_current)
            if current != expected_current:
                raise _fail(
                    CompatibilityHistoryFailureCode.PREDECESSOR_MISMATCH,
                    "compatibility current head differs from durable history",
                )
        if ancestor.entry_count > len(recovery.committed_records):
            raise _fail(
                CompatibilityHistoryFailureCode.ROLLBACK_DETECTED,
                "compatibility ancestor is newer than durable history",
            )
        expected_ancestor = (
            create_history_anchor(
                history_domain=HistoryDomain.COMPATIBILITY,
                configuration_id=self.configuration_id,
                entry_sha256s=(),
                domain_heads=(),
            )
            if ancestor.entry_count == 0
            else recovery.committed_records[
                ancestor.entry_count - 1
            ].history_anchor
        )
        if ancestor != expected_ancestor:
            raise _fail(
                CompatibilityHistoryFailureCode.PREDECESSOR_MISMATCH,
                "compatibility ancestor is not in the durable history prefix",
            )
        return current

    def append(
        self,
        value: object,
        *,
        fence_lease: CoordinatedFenceLease | None = None,
    ) -> CompatibilityCommitEvidence:
        kind, identity, raw, ref = _compatibility_stored_artifact(value)
        try:
            with coordinated_store_write(
                fence=self._fence,
                context=self._context,
                store_lock_path=self._lock_path,
                fence_lease=fence_lease,
            ) as lease:
                prior = self.recover()
                if prior.diagnostic is not None:
                    raise prior.diagnostic
                if any(item.record_identity == identity for item in prior.committed_records):
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility record identity already exists",
                    )
                _validate_compatibility_artifact_bytes(
                    record_kind=kind,
                    record_identity=identity,
                    artifact_bytes=raw,
                    artifact_ref=ref,
                    authority_binding=self._authority_binding,
                    committed_records=prior.committed_records,
                    artifact_root=self._root,
                )
                path = _compatibility_artifact_path(self._root, ref)
                if path.exists():
                    if read_regular_bytes(
                        path,
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    ) != raw:
                        raise _fail(
                            CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                            "immutable compatibility artifact collided",
                        )
                else:
                    staged = write_staged_bytes(
                        path.parent,
                        final_name=path.name,
                        operation_id=new_operation_id(),
                        value=raw,
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    )
                    publish_immutable(staged, path)
                predecessor = (
                    None if prior.head is None else prior.head.record_identity
                )
                sequence = len(prior.committed_records) + 1
                frame = _compatibility_store_frame(
                    kind=kind,
                    identity=identity,
                    artifact_ref=ref,
                    predecessor=predecessor,
                    sequence=sequence,
                    fence_epoch=lease.epoch,
                )
                append_journal_payload(self._journal_path, frame)
                committed = self.recover()
                if committed.diagnostic is not None or committed.head is None:
                    raise _fail(
                        CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
                        "compatibility append was not durably recovered",
                    )
                if (
                    committed.head.record_identity != identity
                    or committed.head.artifact_ref != ref
                ):
                    raise _fail(
                        CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
                        "compatibility committed head changed",
                    )
                lease.record_store_mutation(
                    store_name="compatibility",
                    head_identity=committed.history_anchor.anchor_id.value,
                    store_sequence=sequence,
                )
                return committed.head
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    CompatibilityHistoryFailureCode.LOCK_BUSY,
                    "compatibility coordinator lock is busy",
                ) from exc
            raise

    def require_inclusion(
        self,
        value: object,
        *,
        expected_evidence: CompatibilityCommitEvidence | None = None,
    ) -> CompatibilityCommitEvidence:
        _, identity, _, ref = _compatibility_stored_artifact(value)
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        for item in recovery.committed_records:
            if item.record_identity == identity and item.artifact_ref == ref:
                if expected_evidence is not None:
                    validate_compatibility_commit_evidence(expected_evidence)
                    if item.to_dict() != expected_evidence.to_dict():
                        raise _fail(
                            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
                            "compatibility inclusion evidence changed",
                        )
                return item
        raise _fail(
            CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
            "compatibility artifact is not durably included",
        )

    def require_artifact_ref_inclusion(
        self,
        *,
        artifact_ref: HashBoundRef,
        expected_record_kind: CompatibilityStoredRecordKind,
    ) -> CompatibilityCommitEvidence:
        if (
            type(artifact_ref) is not HashBoundRef
            or artifact_ref.kind is not RefKind.ARTIFACT
            or type(expected_record_kind) is not CompatibilityStoredRecordKind
        ):
            raise _fail(
                CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
                "compatibility artifact inclusion query is invalid",
            )
        HashBoundRef.from_dict(artifact_ref.to_dict())
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        matches = tuple(
            item
            for item in recovery.committed_records
            if item.artifact_ref == artifact_ref
            and item.record_kind is expected_record_kind
        )
        if len(matches) != 1:
            raise _fail(
                CompatibilityHistoryFailureCode.RECORD_NOT_DURABLE,
                "compatibility artifact ref is not uniquely durably included",
            )
        return matches[0]

    def repair_invalid_suffix(self) -> CompatibilityHistoryRecovery:
        try:
            with coordinated_store_write(
                fence=self._fence,
                context=self._context,
                store_lock_path=self._lock_path,
            ) as lease:
                recovery = self.recover()
                if recovery.diagnostic is None:
                    return recovery
                invalid_suffix = recovery.invalid_suffix
                digest = hashlib.sha256(invalid_suffix).hexdigest()
                quarantine = self._root / "quarantine"
                destination = quarantine / f"{digest}.invalid-suffix"
                if not destination.exists():
                    staged = write_staged_bytes(
                        quarantine,
                        final_name=destination.name,
                        operation_id=new_operation_id(),
                        value=invalid_suffix,
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    )
                    publish_immutable(staged, destination)
                truncate_journal_to_valid_prefix(
                    self._journal_path,
                    recovery.valid_prefix_length,
                )
                repaired = self.recover()
                if repaired.diagnostic is not None:
                    raise _fail(
                        CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                        "compatibility journal remains invalid after repair",
                    )
                lease.record_store_mutation(
                    store_name="compatibility",
                    head_identity=repaired.history_anchor.anchor_id.value,
                    store_sequence=repaired.history_anchor.entry_count,
                )
                return repaired
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    CompatibilityHistoryFailureCode.LOCK_BUSY,
                    "compatibility repair fence is busy",
                ) from exc
            raise _fail(
                CompatibilityHistoryFailureCode.JOURNAL_CORRUPT,
                "compatibility repair persistence failed",
            ) from exc


def open_compatibility_evidence_store(
    root: Path,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    coordination_context: SnapshotCoordinationContext,
) -> CompatibilityEvidenceStore:
    return CompatibilityEvidenceStore(
        root,
        authority_binding,
        coordination_context,
        _seal=_STORE_SEAL,
    )


@dataclass(frozen=True, init=False)
class CompatibilityDurabilityBinding:
    evidence_store: CompatibilityEvidenceStore
    coordination_context: SnapshotCoordinationContext
    _authority_binding: KnowledgeAdmissionAuthorityBinding
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CompatibilityDurabilityBinding:
        raise TypeError("CompatibilityDurabilityBinding is factory-created")


def create_compatibility_durability_binding(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    evidence_store: CompatibilityEvidenceStore,
    coordination_context: SnapshotCoordinationContext,
) -> CompatibilityDurabilityBinding:
    _, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    if (
        type(evidence_store) is not CompatibilityEvidenceStore
        or evidence_store.coordination_context is not coordination_context
        or evidence_store.configuration_id != overlay.configuration_id
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "compatibility durability dependencies use another configuration",
        )
    result = object.__new__(CompatibilityDurabilityBinding)
    object.__setattr__(result, "evidence_store", evidence_store)
    object.__setattr__(result, "coordination_context", coordination_context)
    object.__setattr__(result, "_authority_binding", authority_binding)
    object.__setattr__(result, "_trusted_seal", _DURABILITY_BINDING_SEAL)
    validate_compatibility_durability_binding(
        result,
        authority_binding=authority_binding,
    )
    return result


def validate_compatibility_durability_binding(
    value: CompatibilityDurabilityBinding,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> None:
    if (
        type(value) is not CompatibilityDurabilityBinding
        or getattr(value, "_trusted_seal", None) is not _DURABILITY_BINDING_SEAL
        or value._authority_binding is not authority_binding
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "compatibility durability binding is not factory sealed",
        )
    _, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    validate_snapshot_coordination_context(value.coordination_context)
    if (
        type(value.evidence_store) is not CompatibilityEvidenceStore
        or value.evidence_store.coordination_context
        is not value.coordination_context
        or value.evidence_store.configuration_id != overlay.configuration_id
    ):
        raise _fail(
            CompatibilityFailureCode.CONTEXT_MISMATCH,
            "compatibility durability binding changed",
        )
