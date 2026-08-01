"""Immutable repository knowledge snapshot contracts."""

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
    AuthorityDecisionId,
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
    ProposalId,
    SchemaVersion,
    create_common_envelope,
    create_independence_proof,
    create_history_anchor,
    compute_authority_decision_id,
    compute_proposal_id,
    authority_decision_id_from_dict,
    authority_decision_identity_bytes,
    compute_record_id,
    validate_common_envelope,
    common_envelope_from_dict,
    validate_history_anchor,
    validate_history_anchor_extension,
    validate_independence_proof,
    independence_proof_from_dict,
    validate_record_id,
    record_id_from_dict,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    require_stage4_knowledge_admission_authority_handle,
    validate_knowledge_admission_authority_binding,
)


SNAPSHOT_MANIFEST_CORE_SCHEMA_V1 = (
    "synapse.stage4.gold.snapshot-manifest-core/v1"
)
REPOSITORY_KNOWLEDGE_SNAPSHOT_SCHEMA_V1 = (
    "synapse.stage4.gold.repository-knowledge-snapshot/v1"
)
ATOMIC_SNAPSHOT_BOUNDARY_SCHEMA_V1 = (
    "synapse.stage4.gold.atomic-snapshot-boundary/v1"
)
SNAPSHOT_TRANSACTION_SCHEMA_V1 = "synapse.stage4.gold.snapshot-transaction/v1"
KNOWLEDGE_SNAPSHOT_HISTORY_FRAME_SCHEMA_V1 = (
    "synapse.stage4.gold.knowledge-snapshot-history-frame/v1"
)
SNAPSHOT_COMPLETENESS_DECISION_SCHEMA_V1 = (
    "synapse.stage4.gold.snapshot-completeness-decision/v1"
)
SNAPSHOT_VIEW_ENTRY_SCHEMA_V1 = "synapse.stage4.gold.snapshot-view-entry/v1"
SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.snapshot-store-sequence/v1"
)
SNAPSHOT_COMMIT_EVIDENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.snapshot-commit-evidence/v1"
)
KNOWLEDGE_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.knowledge+json"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/|-]{0,511}\Z")
_SEAL = object()
_EVALUATOR_SEAL = object()
_STORE_SEAL = object()
_COMMIT_EVIDENCE_SEAL = object()


class KnowledgeSnapshotFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    MALFORMED_RECORD = "MALFORMED_RECORD"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ENVELOPE_MISMATCH = "ENVELOPE_MISMATCH"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    COMPLETENESS_NOT_ESTABLISHED = "COMPLETENESS_NOT_ESTABLISHED"
    REQUIRED_STORE_MISSING = "REQUIRED_STORE_MISSING"
    REQUIRED_REF_MISSING = "REQUIRED_REF_MISSING"
    MIX_AND_MATCH_DETECTED = "MIX_AND_MATCH_DETECTED"
    CORRUPTED = "CORRUPTED"
    BOUNDARY_NOT_COMMITTED = "BOUNDARY_NOT_COMMITTED"
    BOUNDARY_EXPIRED = "BOUNDARY_EXPIRED"
    TRANSACTION_REPEATED = "TRANSACTION_REPEATED"
    ATTEMPT_BOUNDARY_REUSED = "ATTEMPT_BOUNDARY_REUSED"
    GENESIS_SEQUENCE_INVALID = "GENESIS_SEQUENCE_INVALID"
    PARENT_MISMATCH = "PARENT_MISMATCH"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"
    FAST_FORWARD_DETECTED = "FAST_FORWARD_DETECTED"
    SOURCE_HEAD_DRIFT = "SOURCE_HEAD_DRIFT"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    LOCK_BUSY = "LOCK_BUSY"


class KnowledgeSnapshotViolation(RuntimeError):
    def __init__(
        self,
        failure_code: KnowledgeSnapshotFailureCode,
        detail: str,
    ) -> None:
        if type(failure_code) is not KnowledgeSnapshotFailureCode:
            raise TypeError(
                "failure_code must be an exact KnowledgeSnapshotFailureCode"
            )
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be safe non-empty text")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(
    code: KnowledgeSnapshotFailureCode,
    detail: str,
) -> KnowledgeSnapshotViolation:
    return KnowledgeSnapshotViolation(code, detail)


class SnapshotCompletenessStatus(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE_REQUIRED_STORE = "INCOMPLETE_REQUIRED_STORE"
    INCOMPLETE_REQUIRED_BINDING = "INCOMPLETE_REQUIRED_BINDING"
    INCOMPLETE_REQUIRED_ATTESTATION = "INCOMPLETE_REQUIRED_ATTESTATION"
    INCOMPLETE_LIFECYCLE_STATE = "INCOMPLETE_LIFECYCLE_STATE"
    INCOMPLETE_COMPATIBILITY_DATA = "INCOMPLETE_COMPATIBILITY_DATA"
    INCONSISTENT_REFERENCES = "INCONSISTENT_REFERENCES"
    MIX_AND_MATCH_DETECTED = "MIX_AND_MATCH_DETECTED"
    ROLLBACK_DETECTED = "ROLLBACK_DETECTED"
    CORRUPTED = "CORRUPTED"


class SnapshotCompletenessReason(str, Enum):
    ALL_REQUIRED_INPUTS_VERIFIED = "ALL_REQUIRED_INPUTS_VERIFIED"
    REQUIRED_STORE_UNAVAILABLE = "REQUIRED_STORE_UNAVAILABLE"
    REQUIRED_BINDING_UNRESOLVED = "REQUIRED_BINDING_UNRESOLVED"
    REQUIRED_ATTESTATION_UNRESOLVED = "REQUIRED_ATTESTATION_UNRESOLVED"
    LIFECYCLE_STATE_UNRESOLVED = "LIFECYCLE_STATE_UNRESOLVED"
    COMPATIBILITY_DATA_INCOMPLETE = "COMPATIBILITY_DATA_INCOMPLETE"
    REFERENCES_INCONSISTENT = "REFERENCES_INCONSISTENT"
    CONTEXT_MIXED = "CONTEXT_MIXED"
    TRUSTED_PRIOR_ROLLBACK = "TRUSTED_PRIOR_ROLLBACK"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"


_STATUS_REASON = {
    SnapshotCompletenessStatus.COMPLETE: (
        SnapshotCompletenessReason.ALL_REQUIRED_INPUTS_VERIFIED
    ),
    SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE: (
        SnapshotCompletenessReason.REQUIRED_STORE_UNAVAILABLE
    ),
    SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_BINDING: (
        SnapshotCompletenessReason.REQUIRED_BINDING_UNRESOLVED
    ),
    SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION: (
        SnapshotCompletenessReason.REQUIRED_ATTESTATION_UNRESOLVED
    ),
    SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE: (
        SnapshotCompletenessReason.LIFECYCLE_STATE_UNRESOLVED
    ),
    SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA: (
        SnapshotCompletenessReason.COMPATIBILITY_DATA_INCOMPLETE
    ),
    SnapshotCompletenessStatus.INCONSISTENT_REFERENCES: (
        SnapshotCompletenessReason.REFERENCES_INCONSISTENT
    ),
    SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED: (
        SnapshotCompletenessReason.CONTEXT_MIXED
    ),
    SnapshotCompletenessStatus.ROLLBACK_DETECTED: (
        SnapshotCompletenessReason.TRUSTED_PRIOR_ROLLBACK
    ),
    SnapshotCompletenessStatus.CORRUPTED: (
        SnapshotCompletenessReason.INTEGRITY_FAILURE
    ),
}


class SnapshotObjectStatus(str, Enum):
    EXECUTABLE = "EXECUTABLE"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    REVOKED = "REVOKED"
    AUDIT_ONLY = "AUDIT_ONLY"


class SnapshotBoundaryPhase(str, Enum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _safe_text(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_TEXT_RE.fullmatch(value) is None:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} is invalid",
        )
    return value


def _sha256(value: object, name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must be lowercase SHA-256",
        )
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must be timezone-aware UTC",
        )
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _record(
    value: object,
    domain: IdentityDomain | None,
    name: str,
) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact RecordId",
        )
    value.to_dict()
    if domain is not None and value.domain is not domain:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            f"{name} uses the wrong identity domain",
        )
    return value


def _ref(
    value: object,
    name: str,
    *,
    expected_kind: RefKind = RefKind.ARTIFACT,
) -> HashBoundRef:
    if type(value) is not HashBoundRef or value.kind is not expected_kind:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact hash-bound reference",
        )
    return HashBoundRef.from_dict(value.to_dict())


def _refs(
    value: object,
    name: str,
    *,
    non_empty: bool,
) -> tuple[HashBoundRef, ...]:
    if type(value) is not tuple:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact tuple",
        )
    result = tuple(_ref(item, name) for item in value)
    if non_empty and not result:
        raise _fail(
            KnowledgeSnapshotFailureCode.REQUIRED_REF_MISSING,
            f"{name} is required",
        )
    canonical = tuple(sorted(result, key=lambda item: _canonical(item.to_dict())))
    if result != canonical or len({item.sha256 for item in result}) != len(result):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must be distinct and canonically ordered",
        )
    return result


def _clone_anchor(
    value: object,
    domain: HistoryDomain,
    configuration_id: RecordId,
) -> HistoryAnchor:
    if type(value) is not HistoryAnchor:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            "snapshot history root must be an exact HistoryAnchor",
        )
    validate_history_anchor(value)
    if (
        value.history_domain is not domain
        or value.configuration_id != configuration_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "snapshot history root belongs to another authority contour",
        )
    return value


@dataclass(frozen=True)
class SnapshotStoreSequence:
    schema_version: str
    store_name: str
    sequence: int
    head_identity: str

    def __post_init__(self) -> None:
        if self.schema_version != SNAPSHOT_STORE_SEQUENCE_SCHEMA_V1:
            raise _fail(
                KnowledgeSnapshotFailureCode.UNKNOWN_SCHEMA,
                "snapshot store sequence schema is unknown",
            )
        _safe_text(self.store_name, "store_name")
        if type(self.sequence) is not int or self.sequence < 0:
            raise _fail(
                KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
                "snapshot store sequence is invalid",
            )
        _safe_text(self.head_identity, "head_identity")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "store_name": self.store_name,
            "sequence": self.sequence,
            "head_identity": self.head_identity,
        }


def _store_sequences(value: object) -> tuple[SnapshotStoreSequence, ...]:
    if type(value) is not tuple or any(
        type(item) is not SnapshotStoreSequence for item in value
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            "store_sequences must contain exact SnapshotStoreSequence values",
        )
    for item in value:
        item.__post_init__()
    if (
        not value
        or value != tuple(sorted(value, key=lambda item: item.store_name))
        or len({item.store_name for item in value}) != len(value)
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "store sequences must be non-empty, distinct, and ordered",
        )
    required = {
        "admission",
        "compatibility",
        "library",
        "lifecycle",
        "provenance",
        "taint",
    }
    if {item.store_name for item in value} != required:
        raise _fail(
            KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
            "snapshot does not cover every required source store",
        )
    return value


@dataclass(frozen=True)
class SnapshotViewEntry:
    schema_version: str
    content_identity: str
    manifest_identity: str
    behavior_kind: str
    binding_refs: tuple[HashBoundRef, ...]
    attestation_refs: tuple[HashBoundRef, ...]
    lifecycle_ref: HashBoundRef
    admission_refs: tuple[HashBoundRef, ...]
    status: SnapshotObjectStatus
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SNAPSHOT_VIEW_ENTRY_SCHEMA_V1:
            raise _fail(
                KnowledgeSnapshotFailureCode.UNKNOWN_SCHEMA,
                "snapshot view entry schema is unknown",
            )
        _safe_text(self.content_identity, "content_identity")
        _safe_text(self.manifest_identity, "manifest_identity")
        _safe_text(self.behavior_kind, "behavior_kind")
        _refs(self.binding_refs, "binding_refs", non_empty=False)
        _refs(self.attestation_refs, "attestation_refs", non_empty=True)
        _ref(self.lifecycle_ref, "lifecycle_ref")
        _refs(self.admission_refs, "admission_refs", non_empty=False)
        if type(self.status) is not SnapshotObjectStatus:
            raise _fail(
                KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
                "snapshot object status is unknown",
            )
        if (
            type(self.reasons) is not tuple
            or any(type(item) is not str or not item for item in self.reasons)
            or self.reasons != tuple(sorted(self.reasons))
            or len(set(self.reasons)) != len(self.reasons)
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
                "snapshot view reasons are invalid",
            )

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "content_identity": self.content_identity,
            "manifest_identity": self.manifest_identity,
            "behavior_kind": self.behavior_kind,
            "binding_refs": [item.to_dict() for item in self.binding_refs],
            "attestation_refs": [item.to_dict() for item in self.attestation_refs],
            "lifecycle_ref": self.lifecycle_ref.to_dict(),
            "admission_refs": [item.to_dict() for item in self.admission_refs],
            "status": self.status.value,
            "reasons": list(self.reasons),
        }


def _views(
    value: object,
    name: str,
) -> tuple[SnapshotViewEntry, ...]:
    if type(value) is not tuple or any(type(item) is not SnapshotViewEntry for item in value):
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            f"{name} must contain exact SnapshotViewEntry values",
        )
    for item in value:
        item.__post_init__()
    if value != tuple(sorted(value, key=lambda item: item.content_identity)):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must use canonical content order",
        )
    if len({item.content_identity for item in value}) != len(value):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} contains duplicate content identities",
        )
    return value


def _validate_view_reference_closure(
    *,
    executable_view: tuple[SnapshotViewEntry, ...],
    audit_view: tuple[SnapshotViewEntry, ...],
    binding_refs: tuple[HashBoundRef, ...],
    attestation_refs: tuple[HashBoundRef, ...],
    historical_admission_refs: tuple[HashBoundRef, ...],
) -> None:
    expected_executable = tuple(
        item
        for item in audit_view
        if item.status is SnapshotObjectStatus.EXECUTABLE
    )
    if executable_view != expected_executable:
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "executable view is not the exact eligible audit projection",
        )
    declared_bindings = set(binding_refs)
    declared_attestations = set(attestation_refs)
    declared_admissions = set(historical_admission_refs)
    if any(
        not set(item.binding_refs).issubset(declared_bindings)
        or not set(item.attestation_refs).issubset(declared_attestations)
        or not set(item.admission_refs).issubset(declared_admissions)
        for item in audit_view
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "snapshot views cite refs outside the declared manifest closure",
        )


def _anchor_payload(anchor: HistoryAnchor) -> dict[str, object]:
    validate_history_anchor(anchor)
    return anchor.to_dict()


def _optional_record_payload(value: RecordId | None) -> object:
    return None if value is None else value.to_dict()


def snapshot_manifest_core_payload(
    *,
    library_root_sha256: str,
    index_root_sha256: str,
    lifecycle_root: HistoryAnchor,
    provenance_root: HistoryAnchor,
    taint_root: HistoryAnchor,
    admission_root: HistoryAnchor,
    compatibility_evidence_root: HistoryAnchor,
    behavior_refs: tuple[HashBoundRef, ...],
    binding_refs: tuple[HashBoundRef, ...],
    attestation_refs: tuple[HashBoundRef, ...],
    historical_admission_refs: tuple[HashBoundRef, ...],
    historical_retrieval_refs: tuple[HashBoundRef, ...],
    conflict_refs: tuple[HashBoundRef, ...],
    executable_view: tuple[SnapshotViewEntry, ...],
    audit_view: tuple[SnapshotViewEntry, ...],
    parent_snapshot_id: RecordId | None,
    parent_boundary_id: RecordId | None,
    store_sequences: tuple[SnapshotStoreSequence, ...],
    captured_at_utc: datetime,
    valid_until_utc: datetime,
) -> dict[str, object]:
    return {
        "schema_version": SNAPSHOT_MANIFEST_CORE_SCHEMA_V1,
        "canonical_profile": STAGE4_CANONICAL_PROFILE_V1,
        "canonical_codec": STABLE_CANONICAL_CODEC_ID,
        "library_root_sha256": library_root_sha256,
        "index_root_sha256": index_root_sha256,
        "lifecycle_root": _anchor_payload(lifecycle_root),
        "provenance_root": _anchor_payload(provenance_root),
        "taint_root": _anchor_payload(taint_root),
        "admission_root": _anchor_payload(admission_root),
        "compatibility_evidence_root": _anchor_payload(
            compatibility_evidence_root
        ),
        "behavior_refs": [item.to_dict() for item in behavior_refs],
        "binding_refs": [item.to_dict() for item in binding_refs],
        "attestation_refs": [item.to_dict() for item in attestation_refs],
        "historical_admission_refs": [
            item.to_dict() for item in historical_admission_refs
        ],
        "historical_retrieval_refs": [
            item.to_dict() for item in historical_retrieval_refs
        ],
        "conflict_refs": [item.to_dict() for item in conflict_refs],
        "executable_view": [item.to_dict() for item in executable_view],
        "audit_view": [item.to_dict() for item in audit_view],
        "parent_snapshot_id": _optional_record_payload(parent_snapshot_id),
        "parent_boundary_id": _optional_record_payload(parent_boundary_id),
        "store_sequences": [item.to_dict() for item in store_sequences],
        "captured_at_utc": _timestamp_text(captured_at_utc),
        "valid_until_utc": _timestamp_text(valid_until_utc),
    }


@dataclass(frozen=True, init=False)
class SnapshotManifestCore:
    envelope: CommonEnvelope
    library_root_sha256: str
    index_root_sha256: str
    lifecycle_root: HistoryAnchor
    provenance_root: HistoryAnchor
    taint_root: HistoryAnchor
    admission_root: HistoryAnchor
    compatibility_evidence_root: HistoryAnchor
    behavior_refs: tuple[HashBoundRef, ...]
    binding_refs: tuple[HashBoundRef, ...]
    attestation_refs: tuple[HashBoundRef, ...]
    historical_admission_refs: tuple[HashBoundRef, ...]
    historical_retrieval_refs: tuple[HashBoundRef, ...]
    conflict_refs: tuple[HashBoundRef, ...]
    executable_view: tuple[SnapshotViewEntry, ...]
    audit_view: tuple[SnapshotViewEntry, ...]
    parent_snapshot_id: RecordId | None
    parent_boundary_id: RecordId | None
    store_sequences: tuple[SnapshotStoreSequence, ...]
    captured_at_utc: datetime
    valid_until_utc: datetime
    _base_configuration_id: RecordId
    _overlay_configuration_id: RecordId
    _authority_binding: KnowledgeAdmissionAuthorityBinding
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotManifestCore:
        raise TypeError("SnapshotManifestCore is factory-created")

    @property
    def core_id(self) -> RecordId:
        validate_snapshot_manifest_core(self)
        return self.envelope.record_id

    def payload_dict(self) -> dict[str, object]:
        validate_snapshot_manifest_core(self)
        return snapshot_manifest_core_payload(
            library_root_sha256=self.library_root_sha256,
            index_root_sha256=self.index_root_sha256,
            lifecycle_root=self.lifecycle_root,
            provenance_root=self.provenance_root,
            taint_root=self.taint_root,
            admission_root=self.admission_root,
            compatibility_evidence_root=self.compatibility_evidence_root,
            behavior_refs=self.behavior_refs,
            binding_refs=self.binding_refs,
            attestation_refs=self.attestation_refs,
            historical_admission_refs=self.historical_admission_refs,
            historical_retrieval_refs=self.historical_retrieval_refs,
            conflict_refs=self.conflict_refs,
            executable_view=self.executable_view,
            audit_view=self.audit_view,
            parent_snapshot_id=self.parent_snapshot_id,
            parent_boundary_id=self.parent_boundary_id,
            store_sequences=self.store_sequences,
            captured_at_utc=self.captured_at_utc,
            valid_until_utc=self.valid_until_utc,
        )

    def to_dict(self) -> dict[str, object]:
        payload = self.payload_dict()
        return {"envelope": self.envelope.to_dict(), "payload": payload}


def create_snapshot_manifest_core(
    *,
    envelope: CommonEnvelope,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    library_root_sha256: str,
    index_root_sha256: str,
    lifecycle_root: HistoryAnchor,
    provenance_root: HistoryAnchor,
    taint_root: HistoryAnchor,
    admission_root: HistoryAnchor,
    compatibility_evidence_root: HistoryAnchor,
    behavior_refs: tuple[HashBoundRef, ...],
    binding_refs: tuple[HashBoundRef, ...],
    attestation_refs: tuple[HashBoundRef, ...],
    historical_admission_refs: tuple[HashBoundRef, ...],
    historical_retrieval_refs: tuple[HashBoundRef, ...],
    conflict_refs: tuple[HashBoundRef, ...],
    executable_view: tuple[SnapshotViewEntry, ...],
    audit_view: tuple[SnapshotViewEntry, ...],
    parent_snapshot_id: RecordId | None,
    parent_boundary_id: RecordId | None,
    store_sequences: tuple[SnapshotStoreSequence, ...],
    captured_at_utc: datetime,
    valid_until_utc: datetime,
) -> SnapshotManifestCore:
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    library_root = _sha256(library_root_sha256, "library_root_sha256")
    index_root = _sha256(index_root_sha256, "index_root_sha256")
    lifecycle = _clone_anchor(
        lifecycle_root,
        HistoryDomain.LIFECYCLE,
        base.configuration_id,
    )
    provenance = _clone_anchor(
        provenance_root,
        HistoryDomain.PROVENANCE,
        base.configuration_id,
    )
    taint = _clone_anchor(
        taint_root,
        HistoryDomain.TAINT,
        base.configuration_id,
    )
    admission = _clone_anchor(
        admission_root,
        HistoryDomain.ADMISSION,
        overlay.configuration_id,
    )
    compatibility = _clone_anchor(
        compatibility_evidence_root,
        HistoryDomain.COMPATIBILITY,
        overlay.configuration_id,
    )
    behavior = _refs(behavior_refs, "behavior_refs", non_empty=False)
    bindings = _refs(binding_refs, "binding_refs", non_empty=False)
    attestations = _refs(attestation_refs, "attestation_refs", non_empty=False)
    historical_admissions = _refs(
        historical_admission_refs,
        "historical_admission_refs",
        non_empty=False,
    )
    historical_retrievals = _refs(
        historical_retrieval_refs,
        "historical_retrieval_refs",
        non_empty=False,
    )
    conflicts = _refs(conflict_refs, "conflict_refs", non_empty=False)
    executable = _views(executable_view, "executable_view")
    audit = _views(audit_view, "audit_view")
    _validate_view_reference_closure(
        executable_view=executable,
        audit_view=audit,
        binding_refs=bindings,
        attestation_refs=attestations,
        historical_admission_refs=historical_admissions,
    )
    if parent_snapshot_id is not None:
        _record(
            parent_snapshot_id,
            IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
            "parent_snapshot_id",
        )
    if parent_boundary_id is not None:
        _record(
            parent_boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "parent_boundary_id",
        )
    if (parent_snapshot_id is None) != (parent_boundary_id is None):
        raise _fail(
            KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
            "snapshot parent identities must be present or absent together",
        )
    sequences = _store_sequences(store_sequences)
    captured = _timestamp(captured_at_utc, "captured_at_utc")
    valid_until = _timestamp(valid_until_utc, "valid_until_utc")
    if valid_until <= captured:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "snapshot validity must end after capture",
        )
    payload = snapshot_manifest_core_payload(
        library_root_sha256=library_root,
        index_root_sha256=index_root,
        lifecycle_root=lifecycle,
        provenance_root=provenance,
        taint_root=taint,
        admission_root=admission,
        compatibility_evidence_root=compatibility,
        behavior_refs=behavior,
        binding_refs=bindings,
        attestation_refs=attestations,
        historical_admission_refs=historical_admissions,
        historical_retrieval_refs=historical_retrievals,
        conflict_refs=conflicts,
        executable_view=executable,
        audit_view=audit,
        parent_snapshot_id=parent_snapshot_id,
        parent_boundary_id=parent_boundary_id,
        store_sequences=sequences,
        captured_at_utc=captured,
        valid_until_utc=valid_until,
    )
    canonical = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=canonical)
    if (
        envelope.record_id.domain is not IdentityDomain.SNAPSHOT_MANIFEST_CORE
        or envelope.created_at_utc != captured
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "manifest envelope uses the wrong identity domain",
        )
    if envelope.producer_component != overlay.snapshot_coordinator_actor_identity.value:
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "manifest producer is not the configured snapshot coordinator",
        )
    expected_lineage = tuple(
        item
        for item in (
            (
                None
                if parent_snapshot_id is None
                else LineageParentRef(
                    parent_snapshot_id,
                    LineageEdgeKind.DERIVED_FROM,
                )
            ),
            (
                None
                if parent_boundary_id is None
                else LineageParentRef(
                    parent_boundary_id,
                    LineageEdgeKind.REFERENCES,
                )
            ),
        )
        if item is not None
    )
    if envelope.lineage_parent_ids != expected_lineage:
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "manifest lineage differs from its domain parent relations",
        )
    result = object.__new__(SnapshotManifestCore)
    for name, value in (
        ("envelope", envelope),
        ("library_root_sha256", library_root),
        ("index_root_sha256", index_root),
        ("lifecycle_root", lifecycle),
        ("provenance_root", provenance),
        ("taint_root", taint),
        ("admission_root", admission),
        ("compatibility_evidence_root", compatibility),
        ("behavior_refs", behavior),
        ("binding_refs", bindings),
        ("attestation_refs", attestations),
        ("historical_admission_refs", historical_admissions),
        ("historical_retrieval_refs", historical_retrievals),
        ("conflict_refs", conflicts),
        ("executable_view", executable),
        ("audit_view", audit),
        ("parent_snapshot_id", parent_snapshot_id),
        ("parent_boundary_id", parent_boundary_id),
        ("store_sequences", sequences),
        ("captured_at_utc", captured),
        ("valid_until_utc", valid_until),
        ("_base_configuration_id", base.configuration_id),
        ("_overlay_configuration_id", overlay.configuration_id),
        ("_authority_binding", authority_binding),
        ("_trusted_seal", _SEAL),
    ):
        object.__setattr__(result, name, value)
    validate_snapshot_manifest_core(result)
    return result


def validate_snapshot_manifest_core(value: SnapshotManifestCore) -> None:
    if (
        type(value) is not SnapshotManifestCore
        or getattr(value, "_trusted_seal", None) is not _SEAL
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "snapshot manifest core is not factory sealed",
        )
    base, overlay = validate_knowledge_admission_authority_binding(
        value._authority_binding
    )
    if (
        value._base_configuration_id != base.configuration_id
        or value._overlay_configuration_id != overlay.configuration_id
        or value.envelope.producer_component
        != overlay.snapshot_coordinator_actor_identity.value
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "manifest authority configuration changed",
        )
    if value.parent_snapshot_id is not None:
        _record(
            value.parent_snapshot_id,
            IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
            "parent_snapshot_id",
        )
    if value.parent_boundary_id is not None:
        _record(
            value.parent_boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "parent_boundary_id",
        )
    if (value.parent_snapshot_id is None) != (value.parent_boundary_id is None):
        raise _fail(
            KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
            "snapshot parent identities changed",
        )
    captured = _timestamp(value.captured_at_utc, "captured_at_utc")
    valid_until = _timestamp(value.valid_until_utc, "valid_until_utc")
    if valid_until <= captured:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "snapshot validity must end after capture",
        )
    payload = snapshot_manifest_core_payload(
        library_root_sha256=_sha256(
            value.library_root_sha256,
            "library_root_sha256",
        ),
        index_root_sha256=_sha256(value.index_root_sha256, "index_root_sha256"),
        lifecycle_root=_clone_anchor(
            value.lifecycle_root,
            HistoryDomain.LIFECYCLE,
            value._base_configuration_id,
        ),
        provenance_root=_clone_anchor(
            value.provenance_root,
            HistoryDomain.PROVENANCE,
            value._base_configuration_id,
        ),
        taint_root=_clone_anchor(
            value.taint_root,
            HistoryDomain.TAINT,
            value._base_configuration_id,
        ),
        admission_root=_clone_anchor(
            value.admission_root,
            HistoryDomain.ADMISSION,
            value._overlay_configuration_id,
        ),
        compatibility_evidence_root=_clone_anchor(
            value.compatibility_evidence_root,
            HistoryDomain.COMPATIBILITY,
            value._overlay_configuration_id,
        ),
        behavior_refs=_refs(value.behavior_refs, "behavior_refs", non_empty=False),
        binding_refs=_refs(value.binding_refs, "binding_refs", non_empty=False),
        attestation_refs=_refs(
            value.attestation_refs,
            "attestation_refs",
            non_empty=False,
        ),
        historical_admission_refs=_refs(
            value.historical_admission_refs,
            "historical_admission_refs",
            non_empty=False,
        ),
        historical_retrieval_refs=_refs(
            value.historical_retrieval_refs,
            "historical_retrieval_refs",
            non_empty=False,
        ),
        conflict_refs=_refs(value.conflict_refs, "conflict_refs", non_empty=False),
        executable_view=_views(value.executable_view, "executable_view"),
        audit_view=_views(value.audit_view, "audit_view"),
        parent_snapshot_id=value.parent_snapshot_id,
        parent_boundary_id=value.parent_boundary_id,
        store_sequences=_store_sequences(value.store_sequences),
        captured_at_utc=captured,
        valid_until_utc=valid_until,
    )
    canonical = _canonical(payload)
    validate_common_envelope(value.envelope, canonical_payload_bytes=canonical)
    if value.envelope.record_id.domain is not IdentityDomain.SNAPSHOT_MANIFEST_CORE:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "manifest identity domain changed",
        )
    if value.envelope.created_at_utc != captured:
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "manifest envelope timestamp differs from capture time",
        )
    expected_lineage = tuple(
        item
        for item in (
            (
                None
                if value.parent_snapshot_id is None
                else LineageParentRef(
                    value.parent_snapshot_id,
                    LineageEdgeKind.DERIVED_FROM,
                )
            ),
            (
                None
                if value.parent_boundary_id is None
                else LineageParentRef(
                    value.parent_boundary_id,
                    LineageEdgeKind.REFERENCES,
                )
            ),
        )
        if item is not None
    )
    if value.envelope.lineage_parent_ids != expected_lineage:
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "manifest lineage differs from its domain parent relations",
        )
    _validate_view_reference_closure(
        executable_view=value.executable_view,
        audit_view=value.audit_view,
        binding_refs=value.binding_refs,
        attestation_refs=value.attestation_refs,
        historical_admission_refs=value.historical_admission_refs,
    )


def create_knowledge_domain_envelope(
    *,
    caller_envelope: CommonEnvelope,
    caller_canonical_payload_bytes: bytes,
    identity_domain: IdentityDomain,
    domain_payload: dict[str, object],
    producer_component: str,
    created_at_utc: datetime,
    lineage_parent_ids: tuple[LineageParentRef, ...],
) -> CommonEnvelope:
    validate_common_envelope(
        caller_envelope,
        canonical_payload_bytes=caller_canonical_payload_bytes,
    )
    allowed = {
        IdentityDomain.SNAPSHOT_MANIFEST_CORE,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        IdentityDomain.AUTHORITY_DECISION,
    }
    if type(identity_domain) is not IdentityDomain or identity_domain not in allowed:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "knowledge domain envelope identity domain is not allowed",
        )
    return create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=identity_domain,
        canonical_payload_bytes=_canonical(domain_payload),
        run_id=caller_envelope.run_id,
        attempt_id=caller_envelope.attempt_id,
        created_at_utc=_timestamp(created_at_utc, "created_at_utc"),
        producer_component=_safe_text(
            producer_component,
            "producer_component",
        ),
        repository_revision=caller_envelope.repository_revision,
        policy_version=caller_envelope.policy_version,
        environment_profile_id=caller_envelope.environment_profile_id,
        lineage_parent_ids=lineage_parent_ids,
    )


def enveloped_artifact_bytes(
    *,
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> bytes:
    canonical_payload = _canonical(payload)
    validate_common_envelope(
        envelope,
        canonical_payload_bytes=canonical_payload,
    )
    return _canonical({"envelope": envelope.to_dict(), "payload": payload})


def enveloped_artifact_ref(
    *,
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> HashBoundRef:
    raw = enveloped_artifact_bytes(envelope=envelope, payload=payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    )


@dataclass(frozen=True)
class SnapshotCompletenessObservation:
    observed_store_sequences: tuple[SnapshotStoreSequence, ...]
    resolved_ref_sha256s: tuple[str, ...]
    unresolved_binding_sha256s: tuple[str, ...]
    unresolved_attestation_sha256s: tuple[str, ...]
    unresolved_lifecycle_identities: tuple[str, ...]
    compatibility_data_missing: tuple[str, ...]
    inconsistent_reference_identities: tuple[str, ...]
    observed_context_fingerprints: tuple[str, ...]
    trusted_prior_sequences: tuple[SnapshotStoreSequence, ...]
    integrity_failures: tuple[str, ...]

    def __post_init__(self) -> None:
        _store_sequences(self.observed_store_sequences)
        _validated_ordered_sha_tuple(
            self.resolved_ref_sha256s,
            "resolved_ref_sha256s",
        )
        _validated_ordered_sha_tuple(
            self.unresolved_binding_sha256s,
            "unresolved_binding_sha256s",
        )
        _validated_ordered_sha_tuple(
            self.unresolved_attestation_sha256s,
            "unresolved_attestation_sha256s",
        )
        _ordered_texts(
            self.unresolved_lifecycle_identities,
            "unresolved_lifecycle_identities",
        )
        _ordered_texts(
            self.compatibility_data_missing,
            "compatibility_data_missing",
        )
        _ordered_texts(
            self.inconsistent_reference_identities,
            "inconsistent_reference_identities",
        )
        _validated_ordered_sha_tuple(
            self.observed_context_fingerprints,
            "observed_context_fingerprints",
        )
        if type(self.trusted_prior_sequences) is not tuple or any(
            type(item) is not SnapshotStoreSequence
            for item in self.trusted_prior_sequences
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
                "trusted_prior_sequences are invalid",
            )
        for item in self.trusted_prior_sequences:
            item.__post_init__()
        if (
            self.trusted_prior_sequences
            != tuple(
                sorted(
                    self.trusted_prior_sequences,
                    key=lambda item: item.store_name,
                )
            )
            or len(
                {item.store_name for item in self.trusted_prior_sequences}
            )
            != len(self.trusted_prior_sequences)
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
                "trusted prior sequences must be distinct and ordered",
            )
        _ordered_texts(self.integrity_failures, "integrity_failures")


def _ordered_texts(value: object, name: str) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or any(type(item) is not str or not item for item in value)
        or value != tuple(sorted(value))
        or len(set(value)) != len(value)
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must be a distinct ordered tuple",
        )
    return value


def _validated_ordered_sha_tuple(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            f"{name} must be an exact tuple",
        )
    result = tuple(_sha256(item, name) for item in value)
    if result != tuple(sorted(result)) or len(set(result)) != len(result):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} must be distinct and ordered",
        )
    return result


def snapshot_context_fingerprint(envelope: CommonEnvelope) -> str:
    envelope.record_id.to_dict()
    payload = {
        "run_id": envelope.run_id.to_dict(),
        "attempt_id": envelope.attempt_id.to_dict(),
        "repository_revision": envelope.repository_revision.to_dict(),
        "policy_version": envelope.policy_version,
        "environment_profile_id": envelope.environment_profile_id,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True, init=False)
class ConfiguredSnapshotCompletenessEvaluator:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    trusted_clock: Callable[[], datetime]
    observation_provider: Callable[
        [SnapshotManifestCore],
        SnapshotCompletenessObservation,
    ]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> ConfiguredSnapshotCompletenessEvaluator:
        raise TypeError(
            "ConfiguredSnapshotCompletenessEvaluator is factory-created"
        )


def configure_snapshot_completeness_evaluator(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    trusted_clock: Callable[[], datetime],
    observation_provider: Callable[
        [SnapshotManifestCore],
        SnapshotCompletenessObservation,
    ],
) -> ConfiguredSnapshotCompletenessEvaluator:
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    declaration = overlay.snapshot_completeness_evaluator
    if declaration.authority_role.value != "SNAPSHOT_COMPLETENESS_EVALUATOR":
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "snapshot completeness evaluator declaration is wrong",
        )
    if not callable(trusted_clock) or not callable(observation_provider):
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            "snapshot evaluator dependencies must be callable",
        )
    result = object.__new__(ConfiguredSnapshotCompletenessEvaluator)
    object.__setattr__(result, "authority_binding", authority_binding)
    object.__setattr__(result, "trusted_clock", trusted_clock)
    object.__setattr__(result, "observation_provider", observation_provider)
    object.__setattr__(
        result,
        "_configuration_snapshot",
        (authority_binding, trusted_clock, observation_provider),
    )
    object.__setattr__(result, "_trusted_seal", _EVALUATOR_SEAL)
    require_configured_snapshot_completeness_evaluator(result)
    return result


def require_configured_snapshot_completeness_evaluator(
    value: ConfiguredSnapshotCompletenessEvaluator,
    *,
    expected: ConfiguredSnapshotCompletenessEvaluator | None = None,
) -> None:
    if (
        type(value) is not ConfiguredSnapshotCompletenessEvaluator
        or getattr(value, "_trusted_seal", None) is not _EVALUATOR_SEAL
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "snapshot completeness evaluator is not configured",
        )
    if expected is not None and value is not expected:
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "snapshot completeness evaluator instance differs",
        )
    validate_knowledge_admission_authority_binding(value.authority_binding)
    if (
        not callable(value.trusted_clock)
        or not callable(value.observation_provider)
        or type(getattr(value, "_configuration_snapshot", None)) is not tuple
        or len(value._configuration_snapshot) != 3
        or value._configuration_snapshot[0] is not value.authority_binding
        or value._configuration_snapshot[1] is not value.trusted_clock
        or value._configuration_snapshot[2] is not value.observation_provider
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "snapshot completeness evaluator dependencies changed",
        )


def _snapshot_completeness_findings(
    *,
    core: SnapshotManifestCore,
    observation: SnapshotCompletenessObservation,
) -> tuple[SnapshotCompletenessStatus, tuple[SnapshotCompletenessReason, ...]]:
    validate_snapshot_manifest_core(core)
    observation.__post_init__()
    reasons: list[SnapshotCompletenessReason] = []
    if observation.integrity_failures:
        reasons.append(SnapshotCompletenessReason.INTEGRITY_FAILURE)
    prior_by_store = {
        item.store_name: item for item in observation.trusted_prior_sequences
    }
    current_by_store = {item.store_name: item for item in core.store_sequences}
    if any(
        name in current_by_store
        and prior.sequence > current_by_store[name].sequence
        for name, prior in prior_by_store.items()
    ):
        reasons.append(SnapshotCompletenessReason.TRUSTED_PRIOR_ROLLBACK)
    expected_context = snapshot_context_fingerprint(core.envelope)
    if (
        not observation.observed_context_fingerprints
        or any(
            item != expected_context
            for item in observation.observed_context_fingerprints
        )
    ):
        reasons.append(SnapshotCompletenessReason.CONTEXT_MIXED)
    if observation.inconsistent_reference_identities:
        reasons.append(SnapshotCompletenessReason.REFERENCES_INCONSISTENT)
    if observation.observed_store_sequences != core.store_sequences:
        reasons.append(SnapshotCompletenessReason.REQUIRED_STORE_UNAVAILABLE)
    if observation.unresolved_binding_sha256s:
        reasons.append(SnapshotCompletenessReason.REQUIRED_BINDING_UNRESOLVED)
    if observation.unresolved_attestation_sha256s:
        reasons.append(
            SnapshotCompletenessReason.REQUIRED_ATTESTATION_UNRESOLVED
        )
    if observation.unresolved_lifecycle_identities:
        reasons.append(SnapshotCompletenessReason.LIFECYCLE_STATE_UNRESOLVED)
    if observation.compatibility_data_missing:
        reasons.append(
            SnapshotCompletenessReason.COMPATIBILITY_DATA_INCOMPLETE
        )
    declared_ref_sha256s = tuple(
        sorted(
            item.sha256
            for collection in (
                core.behavior_refs,
                core.binding_refs,
                core.attestation_refs,
                core.historical_admission_refs,
                core.historical_retrieval_refs,
                core.conflict_refs,
            )
            for item in collection
        )
    )
    if observation.resolved_ref_sha256s != declared_ref_sha256s:
        if SnapshotCompletenessReason.REFERENCES_INCONSISTENT not in reasons:
            reasons.append(SnapshotCompletenessReason.REFERENCES_INCONSISTENT)
    if not reasons:
        return (
            SnapshotCompletenessStatus.COMPLETE,
            (SnapshotCompletenessReason.ALL_REQUIRED_INPUTS_VERIFIED,),
        )
    ordered_reasons = tuple(
        item for item in SnapshotCompletenessReason if item in reasons
    )
    status_precedence = (
        SnapshotCompletenessStatus.CORRUPTED,
        SnapshotCompletenessStatus.ROLLBACK_DETECTED,
        SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED,
        SnapshotCompletenessStatus.INCONSISTENT_REFERENCES,
        SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE,
        SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_BINDING,
        SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION,
        SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE,
        SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA,
    )
    reason_status = {reason: status for status, reason in _STATUS_REASON.items()}
    status = next(
        item
        for item in status_precedence
        if _STATUS_REASON[item] in ordered_reasons
    )
    return status, ordered_reasons


def _decision_payload(
    *,
    core: SnapshotManifestCore,
    core_artifact_ref: HashBoundRef,
    checked_roots: tuple[str, ...],
    checked_ref_sha256s: tuple[str, ...],
    status: SnapshotCompletenessStatus,
    reasons: tuple[SnapshotCompletenessReason, ...],
    evaluator_declaration_id: RecordId,
    base_configuration_id: RecordId,
    overlay_configuration_id: RecordId,
    independence_proof: IndependenceProof,
    predecessor_decision_id: AuthorityDecisionId | None,
    decision_sequence: int,
    evaluated_at_utc: datetime,
) -> dict[str, object]:
    return {
        "schema_version": SNAPSHOT_COMPLETENESS_DECISION_SCHEMA_V1,
        "core_id": core.core_id.to_dict(),
        "core_payload_sha256": core.envelope.payload_sha256,
        "core_artifact_ref": core_artifact_ref.to_dict(),
        "checked_roots": list(checked_roots),
        "checked_ref_sha256s": list(checked_ref_sha256s),
        "completeness_status": status.value,
        "reasons": [item.value for item in reasons],
        "evaluator_declaration_id": evaluator_declaration_id.to_dict(),
        "base_configuration_id": base_configuration_id.to_dict(),
        "knowledge_admission_configuration_id": (
            overlay_configuration_id.to_dict()
        ),
        "independence_proof": independence_proof.to_dict(),
        "predecessor_decision_id": (
            None
            if predecessor_decision_id is None
            else predecessor_decision_id.to_dict()
        ),
        "decision_sequence": decision_sequence,
        "evaluated_at_utc": _timestamp_text(evaluated_at_utc),
    }


@dataclass(frozen=True, init=False)
class SnapshotCompletenessDecision:
    envelope: CommonEnvelope
    core_id: RecordId
    core_payload_sha256: str
    core_artifact_ref: HashBoundRef
    checked_roots: tuple[str, ...]
    checked_ref_sha256s: tuple[str, ...]
    completeness_status: SnapshotCompletenessStatus
    reasons: tuple[SnapshotCompletenessReason, ...]
    evaluator_declaration_id: RecordId
    base_configuration_id: RecordId
    knowledge_admission_configuration_id: RecordId
    independence_proof: IndependenceProof
    predecessor_decision_id: AuthorityDecisionId | None
    decision_sequence: int
    evaluated_at_utc: datetime
    decision_id: AuthorityDecisionId
    _core: SnapshotManifestCore
    _evaluator: ConfiguredSnapshotCompletenessEvaluator
    _observation: SnapshotCompletenessObservation
    _predecessor_decision: SnapshotCompletenessDecision | None
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> SnapshotCompletenessDecision:
        raise TypeError("SnapshotCompletenessDecision is evaluator-created")

    def payload_dict(self) -> dict[str, object]:
        validate_snapshot_completeness_decision(
            self,
            evaluator=self._evaluator,
            core=self._core,
        )
        return _decision_payload(
            core=self._core,
            core_artifact_ref=self.core_artifact_ref,
            checked_roots=self.checked_roots,
            checked_ref_sha256s=self.checked_ref_sha256s,
            status=self.completeness_status,
            reasons=self.reasons,
            evaluator_declaration_id=self.evaluator_declaration_id,
            base_configuration_id=self.base_configuration_id,
            overlay_configuration_id=self.knowledge_admission_configuration_id,
            independence_proof=self.independence_proof,
            predecessor_decision_id=self.predecessor_decision_id,
            decision_sequence=self.decision_sequence,
            evaluated_at_utc=self.evaluated_at_utc,
        )

    def to_dict(self) -> dict[str, object]:
        payload = self.payload_dict()
        return {
            "envelope": self.envelope.to_dict(),
            "payload": payload,
            "decision_id": self.decision_id.to_dict(),
        }


def evaluate_snapshot_completeness(
    *,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    core: SnapshotManifestCore,
    producer_actor_ids: tuple[ActorIdentity, ...],
    source_actor_ids: tuple[ActorIdentity, ...],
    proposer_identity: ActorIdentity,
    executor_identity: ActorIdentity | None,
    predecessor_decision: SnapshotCompletenessDecision | None = None,
) -> SnapshotCompletenessDecision:
    require_configured_snapshot_completeness_evaluator(evaluator)
    validate_snapshot_manifest_core(core)
    base, overlay = validate_knowledge_admission_authority_binding(
        evaluator.authority_binding
    )
    declaration = overlay.snapshot_completeness_evaluator
    coordinator = ActorIdentity(core.envelope.producer_component)
    if (
        type(producer_actor_ids) is not tuple
        or coordinator not in producer_actor_ids
        or proposer_identity != coordinator
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "completeness proof must cover the snapshot coordinator",
        )
    try:
        observation = evaluator.observation_provider(core)
    except KnowledgeSnapshotViolation:
        raise
    except Exception as exc:
        raise _fail(
            KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
            "snapshot completeness observation failed closed",
        ) from exc
    if type(observation) is not SnapshotCompletenessObservation:
        raise _fail(
            KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
            "snapshot completeness observation is unavailable",
        )
    status, reasons = _snapshot_completeness_findings(
        core=core,
        observation=observation,
    )
    core_payload = core.payload_dict()
    core_artifact = enveloped_artifact_ref(
        envelope=core.envelope,
        payload=core_payload,
    )
    core_canonical = _canonical(core_payload)
    proposal_id: ProposalId = compute_proposal_id(
        canonical_bytes=core_canonical
    )
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal_id,
        authority_identity=declaration.evaluator_identity,
        authority_role=declaration.authority_role,
        reason_code=declaration.independence_reason,
        producer_actor_ids=producer_actor_ids,
        source_actor_ids=source_actor_ids,
        proposer_identity=proposer_identity,
        executor_identity=executor_identity,
        subject_derived_actor_ids=(),
        delegation_chain=(),
    )
    validate_independence_proof(proof)
    if predecessor_decision is None:
        predecessor_id = None
        sequence = 1
    else:
        validate_snapshot_completeness_decision(
            predecessor_decision,
            evaluator=evaluator,
            core=predecessor_decision._core,
        )
        predecessor_id = predecessor_decision.decision_id
        sequence = predecessor_decision.decision_sequence + 1
    evaluated = _timestamp(evaluator.trusted_clock(), "evaluated_at_utc")
    if evaluated < core.captured_at_utc or evaluated >= core.valid_until_utc:
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness evaluation is outside core validity",
        )
    roots = tuple(
        sorted(
            (
                core.library_root_sha256,
                core.index_root_sha256,
                core.lifecycle_root.anchor_id.value,
                core.provenance_root.anchor_id.value,
                core.taint_root.anchor_id.value,
                core.admission_root.anchor_id.value,
                core.compatibility_evidence_root.anchor_id.value,
            )
        )
    )
    checked_refs = observation.resolved_ref_sha256s
    payload = _decision_payload(
        core=core,
        core_artifact_ref=core_artifact,
        checked_roots=roots,
        checked_ref_sha256s=checked_refs,
        status=status,
        reasons=reasons,
        evaluator_declaration_id=declaration.declaration_id,
        base_configuration_id=base.configuration_id,
        overlay_configuration_id=overlay.configuration_id,
        independence_proof=proof,
        predecessor_decision_id=predecessor_id,
        decision_sequence=sequence,
        evaluated_at_utc=evaluated,
    )
    canonical = _canonical(payload)
    lineage = (
        LineageParentRef(core.core_id, LineageEdgeKind.REFERENCES),
        *(
            ()
            if predecessor_id is None
            else (
                LineageParentRef(
                    predecessor_id.record_id,
                    LineageEdgeKind.SUPERSEDES,
                ),
            )
        ),
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.AUTHORITY_DECISION,
        canonical_payload_bytes=canonical,
        run_id=core.envelope.run_id,
        attempt_id=core.envelope.attempt_id,
        created_at_utc=evaluated,
        producer_component=declaration.evaluator_component_identity.value,
        repository_revision=core.envelope.repository_revision,
        policy_version=core.envelope.policy_version,
        environment_profile_id=core.envelope.environment_profile_id,
        lineage_parent_ids=lineage,
    )
    result = object.__new__(SnapshotCompletenessDecision)
    for name, value in (
        ("envelope", envelope),
        ("core_id", core.core_id),
        ("core_payload_sha256", core.envelope.payload_sha256),
        ("core_artifact_ref", core_artifact),
        ("checked_roots", roots),
        ("checked_ref_sha256s", checked_refs),
        ("completeness_status", status),
        ("reasons", reasons),
        ("evaluator_declaration_id", declaration.declaration_id),
        ("base_configuration_id", base.configuration_id),
        ("knowledge_admission_configuration_id", overlay.configuration_id),
        ("independence_proof", proof),
        ("predecessor_decision_id", predecessor_id),
        ("decision_sequence", sequence),
        ("evaluated_at_utc", evaluated),
        (
            "decision_id",
            compute_authority_decision_id(
                independence_proof=proof,
                canonical_bytes=canonical,
            ),
        ),
        ("_core", core),
        ("_evaluator", evaluator),
        ("_observation", observation),
        ("_predecessor_decision", predecessor_decision),
        ("_trusted_seal", _SEAL),
    ):
        object.__setattr__(result, name, value)
    validate_snapshot_completeness_decision(
        result,
        evaluator=evaluator,
        core=core,
    )
    return result


def validate_snapshot_completeness_decision(
    value: SnapshotCompletenessDecision,
    *,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    core: SnapshotManifestCore,
    _seen: set[int] | None = None,
) -> None:
    seen = set() if _seen is None else _seen
    if id(value) in seen:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "snapshot completeness predecessor graph is circular",
        )
    seen.add(id(value))
    require_configured_snapshot_completeness_evaluator(evaluator)
    validate_snapshot_manifest_core(core)
    bound_evaluator = getattr(value, "_evaluator", None)
    bound_core = getattr(value, "_core", None)
    if (
        type(value) is not SnapshotCompletenessDecision
        or getattr(value, "_trusted_seal", None) is not _SEAL
        or bound_evaluator is not evaluator
        or bound_core is not core
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "snapshot completeness decision is not evaluator sealed",
        )
    base, overlay = validate_knowledge_admission_authority_binding(
        evaluator.authority_binding
    )
    declaration = overlay.snapshot_completeness_evaluator
    observation = getattr(value, "_observation", None)
    if type(observation) is not SnapshotCompletenessObservation:
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness observation changed",
        )
    expected_status, expected_reasons = _snapshot_completeness_findings(
        core=core,
        observation=observation,
    )
    expected_roots = tuple(
        sorted(
            (
                core.library_root_sha256,
                core.index_root_sha256,
                core.lifecycle_root.anchor_id.value,
                core.provenance_root.anchor_id.value,
                core.taint_root.anchor_id.value,
                core.admission_root.anchor_id.value,
                core.compatibility_evidence_root.anchor_id.value,
            )
        )
    )
    if (
        value.core_id != core.core_id
        or value.core_payload_sha256 != core.envelope.payload_sha256
        or value.core_artifact_ref
        != enveloped_artifact_ref(
            envelope=core.envelope,
            payload=core.payload_dict(),
        )
        or value.evaluator_declaration_id != declaration.declaration_id
        or value.base_configuration_id != base.configuration_id
        or value.knowledge_admission_configuration_id != overlay.configuration_id
        or value.completeness_status is not expected_status
        or value.reasons != expected_reasons
        or value.checked_roots != expected_roots
        or value.checked_ref_sha256s
        != observation.resolved_ref_sha256s
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "snapshot completeness decision binding changed",
        )
    validate_independence_proof(value.independence_proof)
    coordinator = ActorIdentity(core.envelope.producer_component)
    if (
        value.independence_proof.authority_identity
        != declaration.evaluator_identity
        or value.independence_proof.authority_role
        is not declaration.authority_role
        or value.independence_proof.reason_code
        is not declaration.independence_reason
        or coordinator not in value.independence_proof.producer_actor_ids
        or value.independence_proof.proposer_identity != coordinator
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "snapshot completeness authority proof changed",
        )
    if type(value.completeness_status) is not SnapshotCompletenessStatus:
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness status is unknown",
        )
    evaluated = _timestamp(value.evaluated_at_utc, "evaluated_at_utc")
    if evaluated < core.captured_at_utc or evaluated >= core.valid_until_utc:
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness evaluation is outside core validity",
        )
    if (
        type(value.reasons) is not tuple
        or not value.reasons
        or any(type(item) is not SnapshotCompletenessReason for item in value.reasons)
        or value.reasons
        != tuple(item for item in SnapshotCompletenessReason if item in value.reasons)
        or _STATUS_REASON[value.completeness_status] not in value.reasons
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness reasons do not support the status",
        )
    if type(value.checked_roots) is not tuple or value.checked_roots != expected_roots:
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "snapshot completeness roots changed",
        )
    _validated_ordered_sha_tuple(
        value.checked_ref_sha256s,
        "checked_ref_sha256s",
    )
    if type(value.decision_sequence) is not int or value.decision_sequence < 1:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "snapshot completeness decision sequence is invalid",
        )
    evaluated = _timestamp(value.evaluated_at_utc, "evaluated_at_utc")
    predecessor = getattr(value, "_predecessor_decision", None)
    if predecessor is value:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "snapshot completeness predecessor is circular",
        )
    if predecessor is None:
        valid_predecessor = (
            value.predecessor_decision_id is None
            and value.decision_sequence == 1
        )
    else:
        valid_predecessor = (
            type(predecessor) is SnapshotCompletenessDecision
            and getattr(predecessor, "_evaluator", None) is evaluator
            and type(getattr(predecessor, "_core", None))
            is SnapshotManifestCore
        )
        if valid_predecessor:
            validate_snapshot_completeness_decision(
                predecessor,
                evaluator=evaluator,
                core=predecessor._core,
                _seen=seen,
            )
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
    if not valid_predecessor:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "snapshot completeness predecessor changed",
        )
    payload = _decision_payload(
        core=core,
        core_artifact_ref=value.core_artifact_ref,
        checked_roots=value.checked_roots,
        checked_ref_sha256s=value.checked_ref_sha256s,
        status=value.completeness_status,
        reasons=value.reasons,
        evaluator_declaration_id=value.evaluator_declaration_id,
        base_configuration_id=value.base_configuration_id,
        overlay_configuration_id=value.knowledge_admission_configuration_id,
        independence_proof=value.independence_proof,
        predecessor_decision_id=value.predecessor_decision_id,
        decision_sequence=value.decision_sequence,
        evaluated_at_utc=value.evaluated_at_utc,
    )
    canonical = _canonical(payload)
    validate_common_envelope(value.envelope, canonical_payload_bytes=canonical)
    if value.envelope.record_id.domain is not IdentityDomain.AUTHORITY_DECISION:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "snapshot completeness envelope domain changed",
        )
    expected_lineage = (
        LineageParentRef(core.core_id, LineageEdgeKind.REFERENCES),
        *(
            ()
            if predecessor is None
            else (
                LineageParentRef(
                    predecessor.decision_id.record_id,
                    LineageEdgeKind.SUPERSEDES,
                ),
            )
        ),
    )
    if (
        value.envelope.lineage_parent_ids != expected_lineage
        or value.envelope.created_at_utc != evaluated
        or value.envelope.producer_component
        != declaration.evaluator_component_identity.value
        or value.envelope.run_id != core.envelope.run_id
        or value.envelope.attempt_id != core.envelope.attempt_id
        or value.envelope.repository_revision
        != core.envelope.repository_revision
        or value.envelope.policy_version != core.envelope.policy_version
        or value.envelope.environment_profile_id
        != core.envelope.environment_profile_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "snapshot completeness envelope context or lineage changed",
        )
    expected_id = compute_authority_decision_id(
        independence_proof=value.independence_proof,
        canonical_bytes=canonical,
    )
    if value.decision_id != expected_id:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "snapshot completeness authority identity changed",
        )


def repository_knowledge_snapshot_payload(
    *,
    core: SnapshotManifestCore,
    core_artifact_ref: HashBoundRef,
    decision: SnapshotCompletenessDecision,
    decision_artifact_ref: HashBoundRef,
    parent_snapshot_id: RecordId | None,
    executable_view_sha256: str,
    audit_view_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": REPOSITORY_KNOWLEDGE_SNAPSHOT_SCHEMA_V1,
        "manifest_core_id": core.core_id.to_dict(),
        "manifest_core_artifact_ref": core_artifact_ref.to_dict(),
        "completeness_decision_id": decision.decision_id.to_dict(),
        "completeness_decision_artifact_ref": decision_artifact_ref.to_dict(),
        "parent_snapshot_id": _optional_record_payload(parent_snapshot_id),
        "executable_view_sha256": executable_view_sha256,
        "audit_view_sha256": audit_view_sha256,
    }


@dataclass(frozen=True, init=False)
class RepositoryKnowledgeSnapshot:
    envelope: CommonEnvelope
    manifest_core_id: RecordId
    manifest_core_artifact_ref: HashBoundRef
    completeness_decision_id: AuthorityDecisionId
    completeness_decision_artifact_ref: HashBoundRef
    parent_snapshot_id: RecordId | None
    executable_view_sha256: str
    audit_view_sha256: str
    _core: SnapshotManifestCore
    _decision: SnapshotCompletenessDecision
    _evaluator: ConfiguredSnapshotCompletenessEvaluator
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> RepositoryKnowledgeSnapshot:
        raise TypeError("RepositoryKnowledgeSnapshot is factory-created")

    @property
    def snapshot_id(self) -> RecordId:
        validate_repository_knowledge_snapshot(
            self,
            evaluator=self._evaluator,
        )
        return self.envelope.record_id

    def payload_dict(self) -> dict[str, object]:
        validate_repository_knowledge_snapshot(
            self,
            evaluator=self._evaluator,
        )
        return repository_knowledge_snapshot_payload(
            core=self._core,
            core_artifact_ref=self.manifest_core_artifact_ref,
            decision=self._decision,
            decision_artifact_ref=self.completeness_decision_artifact_ref,
            parent_snapshot_id=self.parent_snapshot_id,
            executable_view_sha256=self.executable_view_sha256,
            audit_view_sha256=self.audit_view_sha256,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def create_repository_knowledge_snapshot(
    *,
    envelope: CommonEnvelope,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    core: SnapshotManifestCore,
    completeness_decision: SnapshotCompletenessDecision,
) -> RepositoryKnowledgeSnapshot:
    validate_snapshot_manifest_core(core)
    validate_snapshot_completeness_decision(
        completeness_decision,
        evaluator=evaluator,
        core=core,
    )
    if (
        completeness_decision.completeness_status
        is not SnapshotCompletenessStatus.COMPLETE
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "repository knowledge snapshot requires COMPLETE authority",
        )
    core_ref = enveloped_artifact_ref(
        envelope=core.envelope,
        payload=core.payload_dict(),
    )
    decision_ref = enveloped_artifact_ref(
        envelope=completeness_decision.envelope,
        payload=completeness_decision.payload_dict(),
    )
    executable_digest = hashlib.sha256(
        _canonical([item.to_dict() for item in core.executable_view])
    ).hexdigest()
    audit_digest = hashlib.sha256(
        _canonical([item.to_dict() for item in core.audit_view])
    ).hexdigest()
    payload = repository_knowledge_snapshot_payload(
        core=core,
        core_artifact_ref=core_ref,
        decision=completeness_decision,
        decision_artifact_ref=decision_ref,
        parent_snapshot_id=core.parent_snapshot_id,
        executable_view_sha256=executable_digest,
        audit_view_sha256=audit_digest,
    )
    canonical = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=canonical)
    if (
        envelope.record_id.domain
        is not IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "repository snapshot envelope uses the wrong identity domain",
        )
    if (
        envelope.created_at_utc < completeness_decision.evaluated_at_utc
        or envelope.created_at_utc >= core.valid_until_utc
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "repository snapshot timestamp is outside its authority interval",
        )
    expected_lineage = [
        LineageParentRef(core.core_id, LineageEdgeKind.REFERENCES),
        LineageParentRef(
            completeness_decision.decision_id.record_id,
            LineageEdgeKind.REFERENCES,
        ),
    ]
    if core.parent_snapshot_id is not None:
        expected_lineage.append(
            LineageParentRef(
                core.parent_snapshot_id,
                LineageEdgeKind.DERIVED_FROM,
            )
        )
    if envelope.lineage_parent_ids != tuple(expected_lineage):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "repository snapshot lineage differs from its domain parents",
        )
    result = object.__new__(RepositoryKnowledgeSnapshot)
    for name, value in (
        ("envelope", envelope),
        ("manifest_core_id", core.core_id),
        ("manifest_core_artifact_ref", core_ref),
        ("completeness_decision_id", completeness_decision.decision_id),
        ("completeness_decision_artifact_ref", decision_ref),
        ("parent_snapshot_id", core.parent_snapshot_id),
        ("executable_view_sha256", executable_digest),
        ("audit_view_sha256", audit_digest),
        ("_core", core),
        ("_decision", completeness_decision),
        ("_evaluator", evaluator),
        ("_trusted_seal", _SEAL),
    ):
        object.__setattr__(result, name, value)
    validate_repository_knowledge_snapshot(result, evaluator=evaluator)
    return result


def validate_repository_knowledge_snapshot(
    value: RepositoryKnowledgeSnapshot,
    *,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
) -> None:
    require_configured_snapshot_completeness_evaluator(evaluator)
    if (
        type(value) is not RepositoryKnowledgeSnapshot
        or getattr(value, "_trusted_seal", None) is not _SEAL
        or value._evaluator is not evaluator
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "repository knowledge snapshot is not factory sealed",
        )
    validate_snapshot_manifest_core(value._core)
    validate_snapshot_completeness_decision(
        value._decision,
        evaluator=evaluator,
        core=value._core,
    )
    if (
        value._decision.completeness_status
        is not SnapshotCompletenessStatus.COMPLETE
        or value.manifest_core_id != value._core.core_id
        or value.completeness_decision_id != value._decision.decision_id
        or value.parent_snapshot_id != value._core.parent_snapshot_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "repository snapshot authority graph changed",
        )
    expected_core_ref = enveloped_artifact_ref(
        envelope=value._core.envelope,
        payload=value._core.payload_dict(),
    )
    expected_decision_ref = enveloped_artifact_ref(
        envelope=value._decision.envelope,
        payload=value._decision.payload_dict(),
    )
    expected_executable = hashlib.sha256(
        _canonical([item.to_dict() for item in value._core.executable_view])
    ).hexdigest()
    expected_audit = hashlib.sha256(
        _canonical([item.to_dict() for item in value._core.audit_view])
    ).hexdigest()
    if (
        value.manifest_core_artifact_ref != expected_core_ref
        or value.completeness_decision_artifact_ref != expected_decision_ref
        or value.executable_view_sha256 != expected_executable
        or value.audit_view_sha256 != expected_audit
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "repository snapshot artifact/view binding changed",
        )
    payload = repository_knowledge_snapshot_payload(
        core=value._core,
        core_artifact_ref=value.manifest_core_artifact_ref,
        decision=value._decision,
        decision_artifact_ref=value.completeness_decision_artifact_ref,
        parent_snapshot_id=value.parent_snapshot_id,
        executable_view_sha256=value.executable_view_sha256,
        audit_view_sha256=value.audit_view_sha256,
    )
    validate_common_envelope(
        value.envelope,
        canonical_payload_bytes=_canonical(payload),
    )
    if (
        value.envelope.record_id.domain
        is not IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "repository snapshot identity domain changed",
        )
    if (
        value.envelope.created_at_utc < value._decision.evaluated_at_utc
        or value.envelope.created_at_utc >= value._core.valid_until_utc
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "repository snapshot timestamp is outside its authority interval",
        )
    expected_lineage = [
        LineageParentRef(value._core.core_id, LineageEdgeKind.REFERENCES),
        LineageParentRef(
            value._decision.decision_id.record_id,
            LineageEdgeKind.REFERENCES,
        ),
    ]
    if value.parent_snapshot_id is not None:
        expected_lineage.append(
            LineageParentRef(
                value.parent_snapshot_id,
                LineageEdgeKind.DERIVED_FROM,
            )
        )
    if (
        value.envelope.lineage_parent_ids != tuple(expected_lineage)
        or value.envelope.producer_component
        != value._core.envelope.producer_component
        or value.envelope.run_id != value._core.envelope.run_id
        or value.envelope.attempt_id != value._core.envelope.attempt_id
        or value.envelope.repository_revision
        != value._core.envelope.repository_revision
        or value.envelope.policy_version != value._core.envelope.policy_version
        or value.envelope.environment_profile_id
        != value._core.envelope.environment_profile_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "repository snapshot common context differs from its manifest",
        )


def create_snapshot_transaction_id(
    *,
    snapshot: RepositoryKnowledgeSnapshot,
    transaction_nonce: str,
) -> RecordId:
    _safe_text(transaction_nonce, "transaction_nonce")
    payload = {
        "schema_version": SNAPSHOT_TRANSACTION_SCHEMA_V1,
        "snapshot_id": snapshot.snapshot_id.to_dict(),
        "attempt_id": snapshot.envelope.attempt_id.to_dict(),
        "transaction_nonce": transaction_nonce,
    }
    return compute_record_id(
        domain=IdentityDomain.SNAPSHOT_TRANSACTION,
        canonical_bytes=_canonical(payload),
    )


def atomic_snapshot_boundary_payload(
    *,
    transaction_id: RecordId,
    core: SnapshotManifestCore,
    snapshot: RepositoryKnowledgeSnapshot,
    completeness_decision: SnapshotCompletenessDecision,
    parent_boundary_id: RecordId | None,
    start_sequence: int,
    commit_sequence: int,
    expected_commit_nonce: str,
) -> dict[str, object]:
    manifest_envelope_hash = hashlib.sha256(
        _canonical(core.envelope.to_dict())
    ).hexdigest()
    decision_envelope_hash = hashlib.sha256(
        _canonical(completeness_decision.envelope.to_dict())
    ).hexdigest()
    return {
        "schema_version": ATOMIC_SNAPSHOT_BOUNDARY_SCHEMA_V1,
        "transaction_id": transaction_id.to_dict(),
        "library_root_sha256": core.library_root_sha256,
        "index_root_sha256": core.index_root_sha256,
        "lifecycle_root_id": core.lifecycle_root.anchor_id.to_dict(),
        "provenance_root_id": core.provenance_root.anchor_id.to_dict(),
        "taint_root_id": core.taint_root.anchor_id.to_dict(),
        "admission_root_id": core.admission_root.anchor_id.to_dict(),
        "compatibility_evidence_root_id": (
            core.compatibility_evidence_root.anchor_id.to_dict()
        ),
        "manifest_ref": snapshot.manifest_core_artifact_ref.to_dict(),
        "manifest_payload_sha256": core.envelope.payload_sha256,
        "manifest_envelope_sha256": manifest_envelope_hash,
        "snapshot_id": snapshot.snapshot_id.to_dict(),
        "snapshot_ref": enveloped_artifact_ref(
            envelope=snapshot.envelope,
            payload=snapshot.payload_dict(),
        ).to_dict(),
        "start_sequence": start_sequence,
        "commit_sequence": commit_sequence,
        "completeness_decision_ref": (
            snapshot.completeness_decision_artifact_ref.to_dict()
        ),
        "completeness_decision_envelope_sha256": decision_envelope_hash,
        "parent_boundary_id": _optional_record_payload(parent_boundary_id),
        "expected_commit_nonce": expected_commit_nonce,
    }


@dataclass(frozen=True, init=False)
class AtomicSnapshotBoundary:
    envelope: CommonEnvelope
    transaction_id: RecordId
    snapshot_id: RecordId
    parent_boundary_id: RecordId | None
    start_sequence: int
    commit_sequence: int
    expected_commit_nonce: str
    manifest_ref: HashBoundRef
    manifest_payload_sha256: str
    manifest_envelope_sha256: str
    completeness_decision_ref: HashBoundRef
    completeness_decision_envelope_sha256: str
    _core: SnapshotManifestCore
    _snapshot: RepositoryKnowledgeSnapshot
    _decision: SnapshotCompletenessDecision
    _evaluator: ConfiguredSnapshotCompletenessEvaluator
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> AtomicSnapshotBoundary:
        raise TypeError("AtomicSnapshotBoundary is factory-created")

    @property
    def atomic_boundary_id(self) -> RecordId:
        validate_atomic_snapshot_boundary(self, evaluator=self._evaluator)
        return self.envelope.record_id

    def payload_dict(self) -> dict[str, object]:
        validate_atomic_snapshot_boundary(self, evaluator=self._evaluator)
        return atomic_snapshot_boundary_payload(
            transaction_id=self.transaction_id,
            core=self._core,
            snapshot=self._snapshot,
            completeness_decision=self._decision,
            parent_boundary_id=self.parent_boundary_id,
            start_sequence=self.start_sequence,
            commit_sequence=self.commit_sequence,
            expected_commit_nonce=self.expected_commit_nonce,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def create_atomic_snapshot_boundary(
    *,
    envelope: CommonEnvelope,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    transaction_id: RecordId,
    core: SnapshotManifestCore,
    snapshot: RepositoryKnowledgeSnapshot,
    completeness_decision: SnapshotCompletenessDecision,
    parent_boundary_id: RecordId | None,
    start_sequence: int,
    commit_sequence: int,
    expected_commit_nonce: str,
) -> AtomicSnapshotBoundary:
    validate_repository_knowledge_snapshot(snapshot, evaluator=evaluator)
    validate_snapshot_completeness_decision(
        completeness_decision,
        evaluator=evaluator,
        core=core,
    )
    _record(
        transaction_id,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        "transaction_id",
    )
    if parent_boundary_id is not None:
        _record(
            parent_boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "parent_boundary_id",
        )
    if type(start_sequence) is not int or type(commit_sequence) is not int:
        raise _fail(
            KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
            "boundary sequences must be exact integers",
        )
    if parent_boundary_id is None:
        if start_sequence != 0 or commit_sequence != 1:
            raise _fail(
                KnowledgeSnapshotFailureCode.GENESIS_SEQUENCE_INVALID,
                "genesis boundary must use sequence 0 to 1",
            )
    elif commit_sequence != start_sequence + 1:
        code = (
            KnowledgeSnapshotFailureCode.FAST_FORWARD_DETECTED
            if commit_sequence > start_sequence + 1
            else KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED
        )
        raise _fail(code, "non-genesis boundary sequence is not contiguous")
    nonce = _safe_text(expected_commit_nonce, "expected_commit_nonce")
    expected_transaction_id = create_snapshot_transaction_id(
        snapshot=snapshot,
        transaction_nonce=nonce,
    )
    if (
        transaction_id != expected_transaction_id
        or parent_boundary_id != core.parent_boundary_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "boundary transaction or parent differs from its snapshot graph",
        )
    payload = atomic_snapshot_boundary_payload(
        transaction_id=transaction_id,
        core=core,
        snapshot=snapshot,
        completeness_decision=completeness_decision,
        parent_boundary_id=parent_boundary_id,
        start_sequence=start_sequence,
        commit_sequence=commit_sequence,
        expected_commit_nonce=nonce,
    )
    canonical = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=canonical)
    if envelope.record_id.domain is not IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY:
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary envelope uses the wrong identity domain",
        )
    if (
        envelope.created_at_utc < snapshot.envelope.created_at_utc
        or envelope.created_at_utc >= core.valid_until_utc
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary timestamp is outside its snapshot interval",
        )
    expected_lineage = [
        LineageParentRef(snapshot.snapshot_id, LineageEdgeKind.REFERENCES),
    ]
    if parent_boundary_id is not None:
        expected_lineage.append(
            LineageParentRef(
                parent_boundary_id,
                LineageEdgeKind.DERIVED_FROM,
            )
        )
    if envelope.lineage_parent_ids != tuple(expected_lineage):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary lineage differs from its domain parents",
        )
    result = object.__new__(AtomicSnapshotBoundary)
    for name, value in (
        ("envelope", envelope),
        ("transaction_id", transaction_id),
        ("snapshot_id", snapshot.snapshot_id),
        ("parent_boundary_id", parent_boundary_id),
        ("start_sequence", start_sequence),
        ("commit_sequence", commit_sequence),
        ("expected_commit_nonce", nonce),
        ("manifest_ref", snapshot.manifest_core_artifact_ref),
        ("manifest_payload_sha256", core.envelope.payload_sha256),
        (
            "manifest_envelope_sha256",
            hashlib.sha256(_canonical(core.envelope.to_dict())).hexdigest(),
        ),
        (
            "completeness_decision_ref",
            snapshot.completeness_decision_artifact_ref,
        ),
        (
            "completeness_decision_envelope_sha256",
            hashlib.sha256(
                _canonical(completeness_decision.envelope.to_dict())
            ).hexdigest(),
        ),
        ("_core", core),
        ("_snapshot", snapshot),
        ("_decision", completeness_decision),
        ("_evaluator", evaluator),
        ("_trusted_seal", _SEAL),
    ):
        object.__setattr__(result, name, value)
    validate_atomic_snapshot_boundary(result, evaluator=evaluator)
    return result


def validate_atomic_snapshot_boundary(
    value: AtomicSnapshotBoundary,
    *,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
) -> None:
    if (
        type(value) is not AtomicSnapshotBoundary
        or getattr(value, "_trusted_seal", None) is not _SEAL
        or value._evaluator is not evaluator
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            "atomic snapshot boundary is not factory sealed",
        )
    validate_repository_knowledge_snapshot(value._snapshot, evaluator=evaluator)
    validate_snapshot_completeness_decision(
        value._decision,
        evaluator=evaluator,
        core=value._core,
    )
    _record(
        value.transaction_id,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        "transaction_id",
    )
    expected_transaction_id = create_snapshot_transaction_id(
        snapshot=value._snapshot,
        transaction_nonce=_safe_text(
            value.expected_commit_nonce,
            "expected_commit_nonce",
        ),
    )
    if (
        value.transaction_id != expected_transaction_id
        or value.parent_boundary_id != value._core.parent_boundary_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "boundary transaction or parent changed",
        )
    if value.snapshot_id != value._snapshot.snapshot_id:
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "boundary snapshot identity changed",
        )
    if value.parent_boundary_id is None:
        if value.start_sequence != 0 or value.commit_sequence != 1:
            raise _fail(
                KnowledgeSnapshotFailureCode.GENESIS_SEQUENCE_INVALID,
                "genesis boundary sequence changed",
            )
    else:
        _record(
            value.parent_boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "parent_boundary_id",
        )
        if value.commit_sequence != value.start_sequence + 1:
            code = (
                KnowledgeSnapshotFailureCode.FAST_FORWARD_DETECTED
                if value.commit_sequence > value.start_sequence + 1
                else KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED
            )
            raise _fail(code, "boundary sequence changed")
    payload = atomic_snapshot_boundary_payload(
        transaction_id=value.transaction_id,
        core=value._core,
        snapshot=value._snapshot,
        completeness_decision=value._decision,
        parent_boundary_id=value.parent_boundary_id,
        start_sequence=value.start_sequence,
        commit_sequence=value.commit_sequence,
        expected_commit_nonce=_safe_text(
            value.expected_commit_nonce,
            "expected_commit_nonce",
        ),
    )
    validate_common_envelope(
        value.envelope,
        canonical_payload_bytes=_canonical(payload),
    )
    if value.envelope.record_id.domain is not IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "atomic boundary identity domain changed",
        )
    if (
        value.envelope.created_at_utc < value._snapshot.envelope.created_at_utc
        or value.envelope.created_at_utc >= value._core.valid_until_utc
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary timestamp is outside its snapshot interval",
        )
    if (
        value.manifest_ref != value._snapshot.manifest_core_artifact_ref
        or value.manifest_payload_sha256 != value._core.envelope.payload_sha256
        or value.completeness_decision_ref
        != value._snapshot.completeness_decision_artifact_ref
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "atomic boundary artifact graph changed",
        )
    expected_lineage = [
        LineageParentRef(
            value._snapshot.snapshot_id,
            LineageEdgeKind.REFERENCES,
        ),
    ]
    if value.parent_boundary_id is not None:
        expected_lineage.append(
            LineageParentRef(
                value.parent_boundary_id,
                LineageEdgeKind.DERIVED_FROM,
            )
        )
    expected_manifest_envelope = hashlib.sha256(
        _canonical(value._core.envelope.to_dict())
    ).hexdigest()
    expected_decision_envelope = hashlib.sha256(
        _canonical(value._decision.envelope.to_dict())
    ).hexdigest()
    if (
        value.manifest_envelope_sha256 != expected_manifest_envelope
        or value.completeness_decision_envelope_sha256
        != expected_decision_envelope
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary envelope hashes changed",
        )
    if (
        value.envelope.lineage_parent_ids != tuple(expected_lineage)
        or value.envelope.producer_component
        != value._core.envelope.producer_component
        or value.envelope.run_id != value._core.envelope.run_id
        or value.envelope.attempt_id != value._core.envelope.attempt_id
        or value.envelope.repository_revision
        != value._core.envelope.repository_revision
        or value.envelope.policy_version != value._core.envelope.policy_version
        or value.envelope.environment_profile_id
        != value._core.envelope.environment_profile_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.ENVELOPE_MISMATCH,
            "atomic boundary common context differs from its manifest",
        )
