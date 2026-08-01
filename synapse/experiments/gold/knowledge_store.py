"""Atomic repository knowledge snapshots for Stage 4 Gold.

The module freezes pre-retrieval knowledge state and makes it consumer-visible
only through a checksum-valid terminal boundary committed under the shared
cross-process coordination fence. It does not retrieve, rank, replay, or
publish behavior.
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
    history_anchor_from_dict,
    validate_record_id,
    record_id_from_dict,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    require_stage4_knowledge_admission_authority_handle,
    validate_knowledge_admission_authority_binding,
)
from .coordination import (
    CoordinatedFenceLease,
    CoordinatedSnapshotFence,
    SnapshotCoordinationContext,
    open_coordinated_snapshot_fence,
    require_coordinated_fence_lease,
    validate_snapshot_coordination_context,
)
from .persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    MAX_METADATA_BYTES_V1,
    append_journal_payload,
    ensure_directory,
    new_operation_id,
    publish_immutable,
    read_regular_bytes,
    scan_journal,
    truncate_journal_to_valid_prefix,
    write_staged_bytes,
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


from .knowledge_contracts import *
from .knowledge_contracts import (
    _anchor_payload,
    _canonical,
    _clone_anchor,
    _fail,
    _optional_record_payload,
    _record,
    _ref,
    _safe_text,
    _sha256,
    _store_sequences,
    _timestamp,
    _validate_view_reference_closure,
    _views,
    _refs,
)
from .snapshot_adapters import SnapshotSourceHeadReader


def _validate_store_coordination_context(
    *,
    context: SnapshotCoordinationContext,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> None:
    validate_snapshot_coordination_context(context)
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if (
        context.base_configuration_id_text != base.configuration_id.value
        or context.knowledge_admission_configuration_id_text
        != overlay.configuration_id.value
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "coordination context authority identities differ",
        )


class SnapshotCommitEvidence:
    schema_version: str
    boundary_id: RecordId
    transaction_id: RecordId
    snapshot_id: RecordId
    commit_sequence: int
    terminal_frame_sha256: str
    terminal_frame_end_offset: int
    history_anchor: HistoryAnchor
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> SnapshotCommitEvidence:
        raise TypeError("SnapshotCommitEvidence is store-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_commit_evidence(self)
        return {
            "schema_version": self.schema_version,
            "boundary_id": self.boundary_id.to_dict(),
            "transaction_id": self.transaction_id.to_dict(),
            "snapshot_id": self.snapshot_id.to_dict(),
            "commit_sequence": self.commit_sequence,
            "terminal_frame_sha256": self.terminal_frame_sha256,
            "terminal_frame_end_offset": self.terminal_frame_end_offset,
            "history_anchor": self.history_anchor.to_dict(),
        }


def validate_snapshot_commit_evidence(value: SnapshotCommitEvidence) -> None:
    if (
        type(value) is not SnapshotCommitEvidence
        or getattr(value, "_trusted_seal", None) is not _COMMIT_EVIDENCE_SEAL
        or value.schema_version != SNAPSHOT_COMMIT_EVIDENCE_SCHEMA_V1
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED,
            "snapshot commit evidence is not store sealed",
        )
    _record(
        value.boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "boundary_id",
    )
    _record(
        value.transaction_id,
        IdentityDomain.SNAPSHOT_TRANSACTION,
        "transaction_id",
    )
    _record(
        value.snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "snapshot_id",
    )
    if type(value.commit_sequence) is not int or value.commit_sequence < 1:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "commit evidence sequence is invalid",
        )
    _sha256(value.terminal_frame_sha256, "terminal_frame_sha256")
    if (
        type(value.terminal_frame_end_offset) is not int
        or value.terminal_frame_end_offset < 1
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "terminal frame offset is invalid",
        )
    validate_history_anchor(value.history_anchor)
    if value.history_anchor.history_domain is not HistoryDomain.KNOWLEDGE_SNAPSHOT:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "snapshot commit evidence anchor domain is invalid",
        )


@dataclass(frozen=True, init=False)
class CommittedAtomicSnapshotBoundary:
    boundary: AtomicSnapshotBoundary
    commit_evidence: SnapshotCommitEvidence
    _store: KnowledgeSnapshotStore
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> CommittedAtomicSnapshotBoundary:
        raise TypeError("CommittedAtomicSnapshotBoundary is store-created")


@dataclass(frozen=True)
class RecoveredAtomicSnapshotBoundary:
    boundary_id: RecordId
    transaction_id: RecordId
    snapshot_id: RecordId
    completeness_decision_id: AuthorityDecisionId
    completeness_decision_sequence: int
    attempt_id_text: str
    parent_boundary_id_text: str | None
    start_sequence: int
    commit_sequence: int
    valid_until_utc: datetime
    source_heads: tuple[SnapshotStoreSequence, ...]
    context_fingerprint_sha256: str
    admission_anchor: HistoryAnchor
    compatibility_anchor: HistoryAnchor
    commit_evidence: SnapshotCommitEvidence

    def __post_init__(self) -> None:
        _record(
            self.boundary_id,
            IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
            "boundary_id",
        )
        _record(
            self.transaction_id,
            IdentityDomain.SNAPSHOT_TRANSACTION,
            "transaction_id",
        )
        _record(
            self.snapshot_id,
            IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
            "snapshot_id",
        )
        self.completeness_decision_id.to_dict()
        if (
            type(self.completeness_decision_sequence) is not int
            or self.completeness_decision_sequence < 1
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                "recovered completeness sequence is invalid",
            )
        _safe_text(self.attempt_id_text, "attempt_id_text")
        if self.parent_boundary_id_text is not None:
            _safe_text(
                self.parent_boundary_id_text,
                "parent_boundary_id_text",
            )
        if (
            type(self.start_sequence) is not int
            or type(self.commit_sequence) is not int
            or self.start_sequence < 0
            or self.commit_sequence < 1
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                "recovered boundary sequence is invalid",
            )
        _timestamp(self.valid_until_utc, "valid_until_utc")
        _store_sequences(self.source_heads)
        _sha256(
            self.context_fingerprint_sha256,
            "context_fingerprint_sha256",
        )
        validate_history_anchor(self.admission_anchor)
        validate_history_anchor(self.compatibility_anchor)
        if (
            self.admission_anchor.history_domain is not HistoryDomain.ADMISSION
            or self.compatibility_anchor.history_domain
            is not HistoryDomain.COMPATIBILITY
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
                "recovered snapshot history anchors use another domain",
            )
        validate_snapshot_commit_evidence(self.commit_evidence)


@dataclass(frozen=True)
class KnowledgeRecoveryResult:
    committed_boundaries: tuple[RecoveredAtomicSnapshotBoundary, ...]
    trusted_head: RecoveredAtomicSnapshotBoundary | None
    diagnostic: KnowledgeSnapshotFailureCode | None
    valid_prefix_length: int
    invalid_suffix_sha256: str | None
    torn_tail_sha256: str | None

    def __post_init__(self) -> None:
        if type(self.committed_boundaries) is not tuple or any(
            type(item) is not RecoveredAtomicSnapshotBoundary
            for item in self.committed_boundaries
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "recovery boundary set is invalid",
            )
        if self.trusted_head != (
            None if not self.committed_boundaries else self.committed_boundaries[-1]
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "recovery trusted head differs from committed prefix",
            )
        if self.diagnostic is not None and type(self.diagnostic) is not KnowledgeSnapshotFailureCode:
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "recovery diagnostic is invalid",
            )
        if type(self.valid_prefix_length) is not int or self.valid_prefix_length < 1:
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "recovery prefix length is invalid",
            )
        if self.invalid_suffix_sha256 is not None:
            _sha256(self.invalid_suffix_sha256, "invalid_suffix_sha256")
        if self.torn_tail_sha256 is not None:
            _sha256(self.torn_tail_sha256, "torn_tail_sha256")


def _terminal_frame_payload(
    *,
    phase: SnapshotBoundaryPhase,
    boundary: AtomicSnapshotBoundary,
    source_heads: tuple[SnapshotStoreSequence, ...],
    boundary_artifact_ref: HashBoundRef,
    fence_epoch: int,
) -> dict[str, object]:
    return {
        "schema_version": KNOWLEDGE_SNAPSHOT_HISTORY_FRAME_SCHEMA_V1,
        "phase": phase.value,
        "boundary_id": boundary.atomic_boundary_id.to_dict(),
        "transaction_id": boundary.transaction_id.to_dict(),
        "snapshot_id": boundary.snapshot_id.to_dict(),
        "attempt_id": boundary.envelope.attempt_id.to_dict(),
        "parent_boundary_id": _optional_record_payload(
            boundary.parent_boundary_id
        ),
        "start_sequence": boundary.start_sequence,
        "commit_sequence": boundary.commit_sequence,
        "expected_commit_nonce": boundary.expected_commit_nonce,
        "boundary_artifact_ref": boundary_artifact_ref.to_dict(),
        "source_heads": [item.to_dict() for item in source_heads],
        "fence_epoch": fence_epoch,
    }


def _artifact_path(root: Path, artifact_ref: HashBoundRef) -> Path:
    _ref(artifact_ref, "artifact_ref")
    return root / artifact_ref.sha256


def _persist_artifact(
    *,
    directory: Path,
    raw: bytes,
    artifact_ref: HashBoundRef,
) -> None:
    if hashlib.sha256(raw).hexdigest() != artifact_ref.sha256 or len(raw) != artifact_ref.byte_length:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "artifact bytes differ from their hash-bound reference",
        )
    destination = _artifact_path(directory, artifact_ref)
    if destination.exists():
        try:
            existing = read_regular_bytes(
                destination,
                maximum_bytes=MAX_METADATA_BYTES_V1,
            )
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeSnapshotFailureCode.CORRUPTED,
                "existing snapshot artifact is unreadable",
            ) from exc
        if existing != raw:
            raise _fail(
                KnowledgeSnapshotFailureCode.CORRUPTED,
                "existing snapshot artifact bytes differ",
            )
        return
    staged = write_staged_bytes(
        directory,
        final_name=artifact_ref.sha256,
        operation_id=new_operation_id(),
        value=raw,
        maximum_bytes=MAX_METADATA_BYTES_V1,
    )
    publish_immutable(staged, destination)


def _load_artifact(
    *,
    directory: Path,
    artifact_ref: HashBoundRef,
) -> tuple[dict[str, object], dict[str, object], bytes]:
    raw = read_regular_bytes(
        _artifact_path(directory, artifact_ref),
        maximum_bytes=MAX_METADATA_BYTES_V1,
    )
    if hashlib.sha256(raw).hexdigest() != artifact_ref.sha256 or len(raw) != artifact_ref.byte_length:
        raise _fail(
            KnowledgeSnapshotFailureCode.CORRUPTED,
            "snapshot artifact digest or length changed",
        )
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            KnowledgeSnapshotFailureCode.CORRUPTED,
            "snapshot artifact JSON is malformed",
        ) from exc
    if (
        type(decoded) is not dict
        or set(decoded) != {"envelope", "payload"}
        or type(decoded["envelope"]) is not dict
        or type(decoded["payload"]) is not dict
        or _canonical(decoded) != raw
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.CORRUPTED,
            "snapshot artifact is not an exact enveloped artifact",
        )
    return decoded["envelope"], decoded["payload"], raw


def _parse_timestamp_text(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} is invalid",
        )
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise _fail(
            KnowledgeSnapshotFailureCode.MALFORMED_RECORD,
            f"{name} is malformed",
        ) from exc
    return parsed


def _record_text_from_transport(value: object, name: str) -> str:
    if (
        type(value) is not dict
        or set(value) != {"domain", "digest_sha256"}
        or type(value.get("domain")) is not str
        or type(value.get("digest_sha256")) is not str
        or _SHA256_RE.fullmatch(value["digest_sha256"]) is None
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            f"{name} is malformed",
        )
    return f"{value['domain']}:{value['digest_sha256']}"


def _known_parent_bytes(
    value: object,
    *,
    known_identity_bytes: dict[str, bytes],
    name: str,
) -> bytes:
    text = _record_text_from_transport(value, name)
    canonical = known_identity_bytes.get(text)
    if type(canonical) is not bytes:
        raise _fail(
            KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
            f"{name} is not a known trusted parent",
        )
    return canonical


def _recovered_refs(
    value: object,
    name: str,
) -> tuple[HashBoundRef, ...]:
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            f"{name} transport is invalid",
        )
    result = tuple(HashBoundRef.from_dict(item) for item in value)
    _refs(result, name, non_empty=False)
    if value != [item.to_dict() for item in result]:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            f"{name} transport is not exact",
        )
    return result


def _recovered_view_entries(
    value: object,
    name: str,
) -> tuple[SnapshotViewEntry, ...]:
    fields = {
        "schema_version",
        "content_identity",
        "manifest_identity",
        "behavior_kind",
        "binding_refs",
        "attestation_refs",
        "lifecycle_ref",
        "admission_refs",
        "status",
        "reasons",
    }
    if type(value) is not list:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            f"{name} transport is invalid",
        )
    result: list[SnapshotViewEntry] = []
    for raw in value:
        if type(raw) is not dict or set(raw) != fields:
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                f"{name} entry shape is invalid",
            )
        if type(raw["reasons"]) is not list:
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                f"{name} reasons transport is invalid",
            )
        try:
            status = SnapshotObjectStatus(raw["status"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                f"{name} status is unknown",
            ) from exc
        result.append(
            SnapshotViewEntry(
                schema_version=raw["schema_version"],
                content_identity=raw["content_identity"],
                manifest_identity=raw["manifest_identity"],
                behavior_kind=raw["behavior_kind"],
                binding_refs=_recovered_refs(
                    raw["binding_refs"],
                    f"{name}.binding_refs",
                ),
                attestation_refs=_recovered_refs(
                    raw["attestation_refs"],
                    f"{name}.attestation_refs",
                ),
                lifecycle_ref=HashBoundRef.from_dict(raw["lifecycle_ref"]),
                admission_refs=_recovered_refs(
                    raw["admission_refs"],
                    f"{name}.admission_refs",
                ),
                status=status,
                reasons=tuple(raw["reasons"]),
            )
        )
    entries = tuple(result)
    _views(entries, name)
    if value != [item.to_dict() for item in entries]:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            f"{name} transport is not exact",
        )
    return entries


def _frame_record(
    *,
    frame_payload: bytes,
    end_offset: int,
    artifacts_directory: Path,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    overlay_configuration_id: RecordId,
    terminal_frame_sha256s: tuple[str, ...],
    terminal_boundary_heads: tuple[str, ...],
    known_identity_bytes: dict[str, bytes],
    known_decision_sequences: dict[str, int],
) -> RecoveredAtomicSnapshotBoundary:
    base_configuration, overlay_configuration = (
        validate_knowledge_admission_authority_binding(authority_binding)
    )
    if overlay_configuration.configuration_id != overlay_configuration_id:
        raise _fail(
            KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "snapshot recovery uses another authority overlay",
        )
    try:
        raw = json.loads(frame_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "snapshot history frame is malformed",
        ) from exc
    required = {
        "schema_version",
        "phase",
        "boundary_id",
        "transaction_id",
        "snapshot_id",
        "attempt_id",
        "parent_boundary_id",
        "start_sequence",
        "commit_sequence",
        "expected_commit_nonce",
        "boundary_artifact_ref",
        "source_heads",
        "fence_epoch",
    }
    if type(raw) is not dict or set(raw) != required or _canonical(raw) != frame_payload:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "snapshot history frame shape is invalid",
        )
    if (
        raw["schema_version"] != KNOWLEDGE_SNAPSHOT_HISTORY_FRAME_SCHEMA_V1
        or raw["phase"] != SnapshotBoundaryPhase.COMMITTED.value
        or type(raw["fence_epoch"]) is not int
        or raw["fence_epoch"] < 1
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "snapshot terminal frame schema/phase is invalid",
        )
    boundary_ref = HashBoundRef.from_dict(raw["boundary_artifact_ref"])
    boundary_envelope_raw, boundary_payload, _ = _load_artifact(
        directory=artifacts_directory,
        artifact_ref=boundary_ref,
    )
    boundary_payload_bytes = _canonical(boundary_payload)
    expected_boundary_fields = {
        "schema_version",
        "transaction_id",
        "library_root_sha256",
        "index_root_sha256",
        "lifecycle_root_id",
        "provenance_root_id",
        "taint_root_id",
        "admission_root_id",
        "compatibility_evidence_root_id",
        "manifest_ref",
        "manifest_payload_sha256",
        "manifest_envelope_sha256",
        "snapshot_id",
        "snapshot_ref",
        "start_sequence",
        "commit_sequence",
        "completeness_decision_ref",
        "completeness_decision_envelope_sha256",
        "parent_boundary_id",
        "expected_commit_nonce",
    }
    if (
        set(boundary_payload) != expected_boundary_fields
        or boundary_payload.get("schema_version")
        != ATOMIC_SNAPSHOT_BOUNDARY_SCHEMA_V1
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "boundary artifact payload shape is invalid",
        )
    snapshot_ref = HashBoundRef.from_dict(boundary_payload["snapshot_ref"])
    snapshot_envelope_raw, snapshot_payload, _ = _load_artifact(
        directory=artifacts_directory,
        artifact_ref=snapshot_ref,
    )
    snapshot_payload_bytes = _canonical(snapshot_payload)
    if (
        set(snapshot_payload)
        != {
            "schema_version",
            "manifest_core_id",
            "manifest_core_artifact_ref",
            "completeness_decision_id",
            "completeness_decision_artifact_ref",
            "parent_snapshot_id",
            "executable_view_sha256",
            "audit_view_sha256",
        }
        or snapshot_payload.get("schema_version")
        != REPOSITORY_KNOWLEDGE_SNAPSHOT_SCHEMA_V1
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "snapshot artifact payload shape is invalid",
        )
    core_ref = HashBoundRef.from_dict(
        snapshot_payload["manifest_core_artifact_ref"]
    )
    core_envelope_raw, core_payload, _ = _load_artifact(
        directory=artifacts_directory,
        artifact_ref=core_ref,
    )
    core_payload_bytes = _canonical(core_payload)
    core_fields = {
        "schema_version",
        "canonical_profile",
        "canonical_codec",
        "library_root_sha256",
        "index_root_sha256",
        "lifecycle_root",
        "provenance_root",
        "taint_root",
        "admission_root",
        "compatibility_evidence_root",
        "behavior_refs",
        "binding_refs",
        "attestation_refs",
        "historical_admission_refs",
        "historical_retrieval_refs",
        "conflict_refs",
        "executable_view",
        "audit_view",
        "parent_snapshot_id",
        "parent_boundary_id",
        "store_sequences",
        "captured_at_utc",
        "valid_until_utc",
    }
    if (
        set(core_payload) != core_fields
        or core_payload.get("schema_version") != SNAPSHOT_MANIFEST_CORE_SCHEMA_V1
        or core_payload.get("canonical_profile")
        != STAGE4_CANONICAL_PROFILE_V1
        or core_payload.get("canonical_codec") != STABLE_CANONICAL_CODEC_ID
        or _SHA256_RE.fullmatch(core_payload.get("library_root_sha256", ""))
        is None
        or _SHA256_RE.fullmatch(core_payload.get("index_root_sha256", ""))
        is None
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "manifest core artifact shape/profile is invalid",
        )
    behavior_refs = _recovered_refs(core_payload["behavior_refs"], "behavior_refs")
    binding_refs = _recovered_refs(core_payload["binding_refs"], "binding_refs")
    attestation_refs = _recovered_refs(
        core_payload["attestation_refs"],
        "attestation_refs",
    )
    historical_admission_refs = _recovered_refs(
        core_payload["historical_admission_refs"],
        "historical_admission_refs",
    )
    historical_retrieval_refs = _recovered_refs(
        core_payload["historical_retrieval_refs"],
        "historical_retrieval_refs",
    )
    conflict_refs = _recovered_refs(core_payload["conflict_refs"], "conflict_refs")
    executable_view = _recovered_view_entries(
        core_payload["executable_view"],
        "executable_view",
    )
    audit_view = _recovered_view_entries(core_payload["audit_view"], "audit_view")
    _validate_view_reference_closure(
        executable_view=executable_view,
        audit_view=audit_view,
        binding_refs=binding_refs,
        attestation_refs=attestation_refs,
        historical_admission_refs=historical_admission_refs,
    )
    captured_at = _parse_timestamp_text(
        core_payload["captured_at_utc"],
        "captured_at_utc",
    )
    valid_until = _parse_timestamp_text(
        core_payload["valid_until_utc"],
        "valid_until_utc",
    )
    if valid_until <= captured_at:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "manifest core validity interval is invalid",
        )
    core_parent_bytes: list[bytes] = []
    if core_payload.get("parent_snapshot_id") is not None:
        core_parent_bytes.append(
            _known_parent_bytes(
                core_payload["parent_snapshot_id"],
                known_identity_bytes=known_identity_bytes,
                name="core.parent_snapshot_id",
            )
        )
    if core_payload.get("parent_boundary_id") is not None:
        core_parent_bytes.append(
            _known_parent_bytes(
                core_payload["parent_boundary_id"],
                known_identity_bytes=known_identity_bytes,
                name="core.parent_boundary_id",
            )
        )
    if (core_payload.get("parent_snapshot_id") is None) != (
        core_payload.get("parent_boundary_id") is None
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
            "recovered manifest parent identities disagree",
        )
    core_envelope = common_envelope_from_dict(
        core_envelope_raw,
        canonical_payload_bytes=core_payload_bytes,
        lineage_parent_canonical_bytes=tuple(core_parent_bytes),
    )
    if core_envelope.record_id.domain is not IdentityDomain.SNAPSHOT_MANIFEST_CORE:
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "manifest core identity domain is invalid",
        )
    if core_envelope.created_at_utc != captured_at:
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "manifest core envelope timestamp differs from capture",
        )
    admission_anchor = history_anchor_from_dict(
        core_payload.get("admission_root"),
        expected_history_domain=HistoryDomain.ADMISSION,
        expected_configuration_id=overlay_configuration_id,
    )
    compatibility_anchor = history_anchor_from_dict(
        core_payload.get("compatibility_evidence_root"),
        expected_history_domain=HistoryDomain.COMPATIBILITY,
        expected_configuration_id=overlay_configuration_id,
    )
    if core_envelope.record_id.to_dict() != snapshot_payload["manifest_core_id"]:
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "snapshot manifest core identity differs",
        )
    decision_ref = HashBoundRef.from_dict(
        snapshot_payload["completeness_decision_artifact_ref"]
    )
    decision_envelope_raw, decision_payload, _ = _load_artifact(
        directory=artifacts_directory,
        artifact_ref=decision_ref,
    )
    decision_payload_bytes = _canonical(decision_payload)
    decision_fields = {
        "schema_version",
        "core_id",
        "core_payload_sha256",
        "core_artifact_ref",
        "checked_roots",
        "checked_ref_sha256s",
        "completeness_status",
        "reasons",
        "evaluator_declaration_id",
        "base_configuration_id",
        "knowledge_admission_configuration_id",
        "independence_proof",
        "predecessor_decision_id",
        "decision_sequence",
        "evaluated_at_utc",
    }
    declared_ref_sha256s = sorted(
        item.sha256
        for collection in (
            behavior_refs,
            binding_refs,
            attestation_refs,
            historical_admission_refs,
            historical_retrieval_refs,
            conflict_refs,
        )
        for item in collection
    )
    lifecycle_anchor = history_anchor_from_dict(
        core_payload["lifecycle_root"],
        expected_history_domain=HistoryDomain.LIFECYCLE,
        expected_configuration_id=base_configuration.configuration_id,
    )
    provenance_anchor = history_anchor_from_dict(
        core_payload["provenance_root"],
        expected_history_domain=HistoryDomain.PROVENANCE,
        expected_configuration_id=base_configuration.configuration_id,
    )
    taint_anchor = history_anchor_from_dict(
        core_payload["taint_root"],
        expected_history_domain=HistoryDomain.TAINT,
        expected_configuration_id=base_configuration.configuration_id,
    )
    expected_checked_roots = sorted(
        (
            core_payload["library_root_sha256"],
            core_payload["index_root_sha256"],
            admission_anchor.anchor_id.value,
            compatibility_anchor.anchor_id.value,
            lifecycle_anchor.anchor_id.value,
            provenance_anchor.anchor_id.value,
            taint_anchor.anchor_id.value,
        )
    )
    evaluated_at = _parse_timestamp_text(
        decision_payload.get("evaluated_at_utc"),
        "evaluated_at_utc",
    )
    if (
        set(decision_payload) != decision_fields
        or decision_payload.get("schema_version")
        != SNAPSHOT_COMPLETENESS_DECISION_SCHEMA_V1
        or decision_payload.get("core_id") != core_envelope.record_id.to_dict()
        or decision_payload.get("core_payload_sha256")
        != core_envelope.payload_sha256
        or HashBoundRef.from_dict(decision_payload["core_artifact_ref"])
        != core_ref
        or decision_payload.get("checked_roots") != expected_checked_roots
        or decision_payload.get("checked_ref_sha256s")
        != declared_ref_sha256s
        or decision_payload.get("completeness_status")
        != SnapshotCompletenessStatus.COMPLETE.value
        or decision_payload.get("reasons")
        != [SnapshotCompletenessReason.ALL_REQUIRED_INPUTS_VERIFIED.value]
        or decision_payload.get("evaluator_declaration_id")
        != overlay_configuration.snapshot_completeness_evaluator.declaration_id.to_dict()
        or decision_payload.get("base_configuration_id")
        != base_configuration.configuration_id.to_dict()
        or decision_payload.get("knowledge_admission_configuration_id")
        != overlay_configuration.configuration_id.to_dict()
        or evaluated_at < captured_at
        or evaluated_at >= valid_until
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.COMPLETENESS_NOT_ESTABLISHED,
            "recovered completeness authority is incomplete or mismatched",
        )
    proof = independence_proof_from_dict(
        decision_payload["independence_proof"],
        proposal_canonical_bytes=core_payload_bytes,
    )
    coordinator = ActorIdentity(core_envelope.producer_component)
    declaration = overlay_configuration.snapshot_completeness_evaluator
    if (
        proof.authority_identity != declaration.evaluator_identity
        or proof.authority_role is not declaration.authority_role
        or proof.reason_code is not declaration.independence_reason
        or coordinator not in proof.producer_actor_ids
        or proof.proposer_identity != coordinator
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "recovered completeness proof is not independent",
        )
    decision_id = authority_decision_id_from_dict(
        snapshot_payload["completeness_decision_id"],
        canonical_bytes=decision_payload_bytes,
        independence_proof=proof,
    )
    decision_parent_bytes = [core_payload_bytes]
    predecessor_raw = decision_payload.get("predecessor_decision_id")
    decision_sequence = decision_payload.get("decision_sequence")
    if type(decision_sequence) is not int or decision_sequence < 1:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "completeness decision sequence is invalid",
        )
    if predecessor_raw is not None:
        if type(predecessor_raw) is not dict or set(predecessor_raw) != {"record_id"}:
            raise _fail(
                KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
                "completeness predecessor identity is malformed",
            )
        decision_parent_bytes.append(
            _known_parent_bytes(
                predecessor_raw["record_id"],
                known_identity_bytes=known_identity_bytes,
                name="decision.predecessor_decision_id",
            )
        )
        predecessor_text = _record_text_from_transport(
            predecessor_raw["record_id"],
            "decision.predecessor_decision_id",
        )
        predecessor_sequence = known_decision_sequences.get(predecessor_text)
        if (
            type(predecessor_sequence) is not int
            or decision_sequence != predecessor_sequence + 1
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                "completeness predecessor sequence is not contiguous",
            )
    elif decision_sequence != 1:
        raise _fail(
            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
            "initial completeness decision must use sequence one",
        )
    decision_envelope = common_envelope_from_dict(
        decision_envelope_raw,
        canonical_payload_bytes=decision_payload_bytes,
        lineage_parent_canonical_bytes=tuple(decision_parent_bytes),
    )
    if (
        decision_envelope.record_id.domain is not IdentityDomain.AUTHORITY_DECISION
        or decision_payload.get("core_id") != core_envelope.record_id.to_dict()
        or decision_envelope.created_at_utc != evaluated_at
        or decision_envelope.producer_component
        != declaration.evaluator_component_identity.value
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "snapshot completeness decision differs from its core",
        )
    snapshot_parent_bytes = [
        core_payload_bytes,
        authority_decision_identity_bytes(
            independence_proof=proof,
            decision_canonical_bytes=decision_payload_bytes,
        ),
    ]
    if snapshot_payload.get("parent_snapshot_id") is not None:
        snapshot_parent_bytes.append(
            _known_parent_bytes(
                snapshot_payload["parent_snapshot_id"],
                known_identity_bytes=known_identity_bytes,
                name="snapshot.parent_snapshot_id",
            )
        )
    snapshot_envelope = common_envelope_from_dict(
        snapshot_envelope_raw,
        canonical_payload_bytes=snapshot_payload_bytes,
        lineage_parent_canonical_bytes=tuple(snapshot_parent_bytes),
    )
    expected_executable_digest = hashlib.sha256(
        _canonical([item.to_dict() for item in executable_view])
    ).hexdigest()
    expected_audit_digest = hashlib.sha256(
        _canonical([item.to_dict() for item in audit_view])
    ).hexdigest()
    if (
        snapshot_payload["manifest_core_artifact_ref"] != core_ref.to_dict()
        or snapshot_payload["completeness_decision_artifact_ref"]
        != decision_ref.to_dict()
        or snapshot_payload["completeness_decision_id"]
        != decision_id.to_dict()
        or snapshot_payload["parent_snapshot_id"]
        != core_payload["parent_snapshot_id"]
        or snapshot_payload["executable_view_sha256"]
        != expected_executable_digest
        or snapshot_payload["audit_view_sha256"] != expected_audit_digest
        or snapshot_envelope.created_at_utc < evaluated_at
        or snapshot_envelope.created_at_utc >= valid_until
        or snapshot_envelope.producer_component
        != core_envelope.producer_component
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "recovered repository snapshot authority graph is inconsistent",
        )
    snapshot_id_raw = boundary_payload.get("snapshot_id")
    if (
        snapshot_id_raw != raw["snapshot_id"]
        or snapshot_envelope.record_id.to_dict() != snapshot_id_raw
        or snapshot_envelope.record_id.domain
        is not IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "terminal frame snapshot identity differs",
        )
    boundary_parent_bytes = [snapshot_payload_bytes]
    if boundary_payload.get("parent_boundary_id") is not None:
        boundary_parent_bytes.append(
            _known_parent_bytes(
                boundary_payload["parent_boundary_id"],
                known_identity_bytes=known_identity_bytes,
                name="boundary.parent_boundary_id",
            )
        )
    boundary_envelope = common_envelope_from_dict(
        boundary_envelope_raw,
        canonical_payload_bytes=boundary_payload_bytes,
        lineage_parent_canonical_bytes=tuple(boundary_parent_bytes),
    )
    if (
        boundary_envelope.record_id.domain
        is not IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY
        or boundary_envelope.record_id.to_dict() != raw["boundary_id"]
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "terminal frame boundary artifact differs",
        )
    if (
        boundary_payload["library_root_sha256"]
        != core_payload["library_root_sha256"]
        or boundary_payload["index_root_sha256"]
        != core_payload["index_root_sha256"]
        or boundary_payload["lifecycle_root_id"]
        != lifecycle_anchor.anchor_id.to_dict()
        or boundary_payload["provenance_root_id"]
        != provenance_anchor.anchor_id.to_dict()
        or boundary_payload["taint_root_id"] != taint_anchor.anchor_id.to_dict()
        or boundary_payload["admission_root_id"]
        != admission_anchor.anchor_id.to_dict()
        or boundary_payload["compatibility_evidence_root_id"]
        != compatibility_anchor.anchor_id.to_dict()
        or boundary_payload["manifest_ref"] != core_ref.to_dict()
        or boundary_payload["manifest_payload_sha256"]
        != core_envelope.payload_sha256
        or boundary_payload["manifest_envelope_sha256"]
        != hashlib.sha256(_canonical(core_envelope.to_dict())).hexdigest()
        or boundary_payload["snapshot_ref"] != snapshot_ref.to_dict()
        or boundary_payload["completeness_decision_ref"]
        != decision_ref.to_dict()
        or boundary_payload["completeness_decision_envelope_sha256"]
        != hashlib.sha256(_canonical(decision_envelope.to_dict())).hexdigest()
        or boundary_payload["parent_boundary_id"]
        != core_payload["parent_boundary_id"]
        or boundary_envelope.created_at_utc < snapshot_envelope.created_at_utc
        or boundary_envelope.created_at_utc >= valid_until
        or boundary_envelope.producer_component
        != core_envelope.producer_component
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "recovered atomic boundary authority graph is inconsistent",
        )
    common_contexts = (
        core_envelope,
        decision_envelope,
        snapshot_envelope,
        boundary_envelope,
    )
    if any(
        item.run_id != boundary_envelope.run_id
        or item.attempt_id != boundary_envelope.attempt_id
        or item.repository_revision != boundary_envelope.repository_revision
        or item.policy_version != boundary_envelope.policy_version
        or item.environment_profile_id
        != boundary_envelope.environment_profile_id
        for item in common_contexts
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "recovered snapshot authority graph mixes common contexts",
        )
    transaction_payload = {
        "schema_version": SNAPSHOT_TRANSACTION_SCHEMA_V1,
        "snapshot_id": snapshot_envelope.record_id.to_dict(),
        "attempt_id": boundary_envelope.attempt_id.to_dict(),
        "transaction_nonce": _safe_text(
            boundary_payload["expected_commit_nonce"],
            "expected_commit_nonce",
        ),
    }
    transaction_id = record_id_from_dict(
        raw["transaction_id"],
        canonical_bytes=_canonical(transaction_payload),
    )
    if (
        transaction_id.domain is not IdentityDomain.SNAPSHOT_TRANSACTION
        or boundary_payload["transaction_id"] != raw["transaction_id"]
        or raw["attempt_id"] != boundary_envelope.attempt_id.to_dict()
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.IDENTITY_MISMATCH,
            "terminal transaction identity domain is invalid",
        )
    known_identity_bytes[core_envelope.record_id.value] = core_payload_bytes
    known_identity_bytes[decision_id.record_id.value] = (
        authority_decision_identity_bytes(
            independence_proof=proof,
            decision_canonical_bytes=decision_payload_bytes,
        )
    )
    known_identity_bytes[snapshot_envelope.record_id.value] = snapshot_payload_bytes
    known_identity_bytes[boundary_envelope.record_id.value] = boundary_payload_bytes
    source_data = raw["source_heads"]
    sequence_fields = {
        "schema_version",
        "store_name",
        "sequence",
        "head_identity",
    }
    if (
        type(source_data) is not list
        or any(type(item) is not dict or set(item) != sequence_fields for item in source_data)
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "terminal source heads are invalid",
        )
    source_heads = tuple(
        SnapshotStoreSequence(
            schema_version=item["schema_version"],
            store_name=item["store_name"],
            sequence=item["sequence"],
            head_identity=item["head_identity"],
        )
        for item in source_data
        if type(item) is dict
    )
    if len(source_heads) != len(source_data):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "terminal source head shape is invalid",
        )
    _store_sequences(source_heads)
    core_sequences = tuple(
        SnapshotStoreSequence(
            schema_version=item["schema_version"],
            store_name=item["store_name"],
            sequence=item["sequence"],
            head_identity=item["head_identity"],
        )
        for item in core_payload["store_sequences"]
        if type(item) is dict
    )
    if (
        type(core_payload["store_sequences"]) is not list
        or any(
            type(item) is not dict or set(item) != sequence_fields
            for item in core_payload["store_sequences"]
        )
        or len(core_sequences) != len(core_payload["store_sequences"])
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "manifest source sequence shape is invalid",
        )
    _store_sequences(core_sequences)
    if source_heads != core_sequences:
        raise _fail(
            KnowledgeSnapshotFailureCode.SOURCE_HEAD_DRIFT,
            "terminal source heads differ from the manifest",
        )
    start_sequence = raw["start_sequence"]
    commit_sequence = raw["commit_sequence"]
    if type(start_sequence) is not int or type(commit_sequence) is not int:
        raise _fail(
            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
            "terminal boundary sequences are invalid",
        )
    if (
        boundary_payload["start_sequence"] != start_sequence
        or boundary_payload["commit_sequence"] != commit_sequence
        or boundary_payload["parent_boundary_id"] != raw["parent_boundary_id"]
        or boundary_payload["expected_commit_nonce"]
        != raw["expected_commit_nonce"]
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "terminal frame differs from the boundary payload",
        )
    parent_raw = raw["parent_boundary_id"]
    parent_text = None
    if parent_raw is not None:
        if (
            type(parent_raw) is not dict
            or parent_raw.get("domain")
            != IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY.value
            or type(parent_raw.get("digest_sha256")) is not str
            or _SHA256_RE.fullmatch(parent_raw["digest_sha256"]) is None
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
                "terminal parent boundary identity is invalid",
            )
        parent_text = f"{parent_raw['domain']}:{parent_raw['digest_sha256']}"
    if HashBoundRef.from_dict(boundary_payload["manifest_ref"]) != core_ref:
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "boundary manifest reference differs from snapshot",
        )
    valid_until = _parse_timestamp_text(
        core_payload.get("valid_until_utc"),
        "valid_until_utc",
    )
    terminal_digest = hashlib.sha256(frame_payload).hexdigest()
    anchor = create_history_anchor(
        history_domain=HistoryDomain.KNOWLEDGE_SNAPSHOT,
        configuration_id=overlay_configuration_id,
        entry_sha256s=terminal_frame_sha256s,
        domain_heads=terminal_boundary_heads,
    )
    evidence = object.__new__(SnapshotCommitEvidence)
    for name, value in (
        ("schema_version", SNAPSHOT_COMMIT_EVIDENCE_SCHEMA_V1),
        ("boundary_id", boundary_envelope.record_id),
        ("transaction_id", transaction_id),
        ("snapshot_id", snapshot_envelope.record_id),
        ("commit_sequence", commit_sequence),
        ("terminal_frame_sha256", terminal_digest),
        ("terminal_frame_end_offset", end_offset),
        ("history_anchor", anchor),
        ("_trusted_seal", _COMMIT_EVIDENCE_SEAL),
    ):
        object.__setattr__(evidence, name, value)
    validate_snapshot_commit_evidence(evidence)
    return RecoveredAtomicSnapshotBoundary(
        boundary_id=boundary_envelope.record_id,
        transaction_id=transaction_id,
        snapshot_id=snapshot_envelope.record_id,
        completeness_decision_id=decision_id,
        completeness_decision_sequence=decision_sequence,
        attempt_id_text=boundary_envelope.attempt_id.value,
        parent_boundary_id_text=parent_text,
        start_sequence=start_sequence,
        commit_sequence=commit_sequence,
        valid_until_utc=valid_until,
        source_heads=source_heads,
        context_fingerprint_sha256=snapshot_context_fingerprint(core_envelope),
        admission_anchor=admission_anchor,
        compatibility_anchor=compatibility_anchor,
        commit_evidence=evidence,
    )


class KnowledgeSnapshotStore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _STORE_SEAL or kwargs or len(args) != 5:
            raise TypeError("KnowledgeSnapshotStore is factory-opened")
        root, authority_binding, context, trusted_anchor, allow_genesis = args
        if (
            not isinstance(root, Path)
            or not root.is_absolute()
            or type(allow_genesis) is not bool
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
                "knowledge snapshot store configuration is invalid",
            )
        _validate_store_coordination_context(
            context=context,
            authority_binding=authority_binding,
        )
        _, overlay = validate_knowledge_admission_authority_binding(
            authority_binding
        )
        self._root = root
        self._authority_binding = authority_binding
        self._context = context
        self._overlay_configuration_id = overlay.configuration_id
        self._fence = open_coordinated_snapshot_fence(context)
        self._trusted_anchor = trusted_anchor
        ensure_directory(root)
        with self._fence.acquire() as lease:
            with lease.store_lock(self._lock_path):
                ensure_directory(self._artifacts_directory)
                ensure_directory(self._quarantine_directory)
                scan_journal(self._journal_path)
                recovery = self.recover()
                if recovery.committed_boundaries and trusted_anchor is None:
                    raise _fail(
                        KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
                        "non-empty snapshot history requires a trusted anchor",
                    )
                if (
                    not recovery.committed_boundaries
                    and trusted_anchor is None
                    and not allow_genesis
                ):
                    raise _fail(
                        KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
                        "empty snapshot history requires explicit genesis",
                    )
                if trusted_anchor is not None:
                    self._validate_anchor(recovery, trusted_anchor)

    @property
    def _journal_path(self) -> Path:
        return self._root / "knowledge-snapshots-v1.journal"

    @property
    def _lock_path(self) -> Path:
        return self._root / "knowledge-snapshots-v1.lock"

    @property
    def _artifacts_directory(self) -> Path:
        return self._root / "artifacts"

    @property
    def _quarantine_directory(self) -> Path:
        return self._root / "quarantine"

    @property
    def coordination_context(self) -> SnapshotCoordinationContext:
        validate_snapshot_coordination_context(self._context)
        return self._context

    def require_authority_binding(
        self,
        authority_binding: KnowledgeAdmissionAuthorityBinding,
    ) -> None:
        validate_knowledge_admission_authority_binding(authority_binding)
        if self._authority_binding is not authority_binding:
            raise _fail(
                KnowledgeSnapshotFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
                "knowledge store uses another authority binding",
            )

    def recover(self) -> KnowledgeRecoveryResult:
        try:
            scan = scan_journal(self._journal_path)
        except PersistenceViolation as exc:
            raise _fail(
                KnowledgeSnapshotFailureCode.CORRUPTED,
                "snapshot journal structural integrity failed",
            ) from exc
        committed: list[RecoveredAtomicSnapshotBoundary] = []
        terminal_hashes: list[str] = []
        terminal_heads: list[str] = []
        prepared: dict[str, tuple[dict[str, object], int]] = {}
        transactions: set[str] = set()
        attempts: dict[str, str] = {}
        known_identity_bytes: dict[str, bytes] = {}
        known_decision_sequences: dict[str, int] = {}
        diagnostic: KnowledgeSnapshotFailureCode | None = None
        invalid_payloads: list[bytes] = []
        valid_prefix = len(b"SYNAPSE-S4-GOLD-JOURNAL\x00\x01")
        for index, frame in enumerate(scan.frames):
            try:
                raw = json.loads(frame.payload.decode("utf-8"))
                if (
                    type(raw) is not dict
                    or set(raw)
                    != {
                        "schema_version",
                        "phase",
                        "boundary_id",
                        "transaction_id",
                        "snapshot_id",
                        "attempt_id",
                        "parent_boundary_id",
                        "start_sequence",
                        "commit_sequence",
                        "expected_commit_nonce",
                        "boundary_artifact_ref",
                        "source_heads",
                        "fence_epoch",
                    }
                    or raw.get("schema_version")
                    != KNOWLEDGE_SNAPSHOT_HISTORY_FRAME_SCHEMA_V1
                    or _canonical(raw) != frame.payload
                ):
                    raise _fail(
                        KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                        "snapshot history frame is not canonical",
                    )
                phase = SnapshotBoundaryPhase(raw.get("phase"))
                transaction_raw = raw.get("transaction_id")
                transaction_text = json.dumps(
                    transaction_raw,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if phase is SnapshotBoundaryPhase.PREPARED:
                    if transaction_text in prepared or transaction_text in transactions:
                        raise _fail(
                            KnowledgeSnapshotFailureCode.TRANSACTION_REPEATED,
                            "snapshot transaction identity is repeated",
                        )
                    prepared[transaction_text] = (raw, frame.start_offset)
                    continue
                prior = prepared.pop(transaction_text, None)
                if prior is None:
                    raise _fail(
                        KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED,
                        "terminal frame has no exact PREPARED predecessor",
                    )
                prepared_raw, _ = prior
                for field in (
                    "boundary_id",
                    "transaction_id",
                    "snapshot_id",
                    "attempt_id",
                    "parent_boundary_id",
                    "start_sequence",
                    "commit_sequence",
                    "expected_commit_nonce",
                    "boundary_artifact_ref",
                    "source_heads",
                    "fence_epoch",
                ):
                    if prepared_raw.get(field) != raw.get(field):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
                            "PREPARED and COMMITTED frames differ",
                        )
                terminal_digest = hashlib.sha256(frame.payload).hexdigest()
                boundary_head = json.dumps(
                    raw["boundary_id"],
                    sort_keys=True,
                    separators=(",", ":"),
                )
                candidate = _frame_record(
                    frame_payload=frame.payload,
                    end_offset=frame.end_offset,
                    artifacts_directory=self._artifacts_directory,
                    authority_binding=self._authority_binding,
                    overlay_configuration_id=self._overlay_configuration_id,
                    terminal_frame_sha256s=tuple(
                        (*terminal_hashes, terminal_digest)
                    ),
                    terminal_boundary_heads=tuple(
                        (*terminal_heads, boundary_head)
                    ),
                    known_identity_bytes=known_identity_bytes,
                    known_decision_sequences=known_decision_sequences,
                )
                transaction_value = candidate.transaction_id.value
                if transaction_value in transactions:
                    raise _fail(
                        KnowledgeSnapshotFailureCode.TRANSACTION_REPEATED,
                        "snapshot transaction identity is repeated",
                    )
                if not committed:
                    if (
                        candidate.parent_boundary_id_text is not None
                        or candidate.start_sequence != 0
                        or candidate.commit_sequence != 1
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.GENESIS_SEQUENCE_INVALID,
                            "first committed boundary is not valid genesis",
                        )
                else:
                    parent = committed[-1]
                    expected_parent = parent.boundary_id.value
                    if candidate.parent_boundary_id_text != expected_parent:
                        known = {
                            item.boundary_id.value for item in committed[:-1]
                        }
                        code = (
                            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED
                            if candidate.parent_boundary_id_text in known
                            else KnowledgeSnapshotFailureCode.PARENT_MISMATCH
                        )
                        raise _fail(code, "snapshot boundary parent is not current")
                    if (
                        candidate.start_sequence > parent.commit_sequence
                        or candidate.commit_sequence
                        > parent.commit_sequence + 1
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.FAST_FORWARD_DETECTED,
                            "snapshot boundary skips a committed sequence",
                        )
                    if (
                        candidate.start_sequence < parent.commit_sequence
                        or candidate.commit_sequence
                        <= parent.commit_sequence
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                            "snapshot boundary sequence rolls back",
                        )
                previous_boundary = attempts.get(candidate.attempt_id_text)
                if (
                    previous_boundary is not None
                    and previous_boundary != candidate.boundary_id.value
                ):
                    raise _fail(
                        KnowledgeSnapshotFailureCode.ATTEMPT_BOUNDARY_REUSED,
                        "attempt is bound to another committed boundary",
                    )
                attempts[candidate.attempt_id_text] = candidate.boundary_id.value
                transactions.add(transaction_value)
                known_decision_sequences[
                    candidate.completeness_decision_id.record_id.value
                ] = candidate.completeness_decision_sequence
                committed.append(candidate)
                terminal_hashes.append(terminal_digest)
                terminal_heads.append(boundary_head)
                valid_prefix = frame.end_offset
            except (
                KnowledgeSnapshotViolation,
                ContractViolation,
                PersistenceViolation,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                diagnostic = (
                    exc.failure_code
                    if type(exc) is KnowledgeSnapshotViolation
                    else KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT
                )
                invalid_payloads.extend(
                    item.payload for item in scan.frames[index:]
                )
                break
        if diagnostic is None and prepared:
            first_uncommitted_offset = min(
                start_offset for _, start_offset in prepared.values()
            )
            diagnostic = KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED
            valid_prefix = first_uncommitted_offset
            invalid_payloads.extend(
                frame.payload
                for frame in scan.frames
                if frame.start_offset >= first_uncommitted_offset
            )
        invalid_digest = (
            None
            if not invalid_payloads
            else hashlib.sha256(b"".join(invalid_payloads)).hexdigest()
        )
        torn_digest = (
            None
            if not scan.torn_tail
            else hashlib.sha256(scan.torn_tail).hexdigest()
        )
        if diagnostic is None and scan.torn_tail:
            diagnostic = KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT
        return KnowledgeRecoveryResult(
            committed_boundaries=tuple(committed),
            trusted_head=None if not committed else committed[-1],
            diagnostic=diagnostic,
            valid_prefix_length=valid_prefix,
            invalid_suffix_sha256=invalid_digest,
            torn_tail_sha256=torn_digest,
        )

    def _validate_anchor(
        self,
        recovery: KnowledgeRecoveryResult,
        anchor: HistoryAnchor,
    ) -> None:
        validate_history_anchor(anchor)
        entries = tuple(
            item.commit_evidence.terminal_frame_sha256
            for item in recovery.committed_boundaries
        )
        prefix = recovery.committed_boundaries[: anchor.entry_count]
        heads = tuple(
            json.dumps(
                item.boundary_id.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in prefix
        )
        try:
            validate_history_anchor_extension(
                trusted_anchor=anchor,
                history_domain=HistoryDomain.KNOWLEDGE_SNAPSHOT,
                configuration_id=self._overlay_configuration_id,
                entry_sha256s=entries,
                prefix_domain_heads=heads,
            )
        except ContractViolation as exc:
            raise _fail(
                KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                "snapshot history is not an exact trusted extension",
            ) from exc

    def current_anchor(self) -> HistoryAnchor:
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise _fail(
                recovery.diagnostic,
                "snapshot history has an invalid suffix",
            )
        return create_history_anchor(
            history_domain=HistoryDomain.KNOWLEDGE_SNAPSHOT,
            configuration_id=self._overlay_configuration_id,
            entry_sha256s=tuple(
                item.commit_evidence.terminal_frame_sha256
                for item in recovery.committed_boundaries
            ),
            domain_heads=tuple(
                json.dumps(
                    item.boundary_id.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for item in recovery.committed_boundaries
            ),
        )

    def commit(
        self,
        *,
        evaluator: ConfiguredSnapshotCompletenessEvaluator,
        core: SnapshotManifestCore,
        decision: SnapshotCompletenessDecision,
        snapshot: RepositoryKnowledgeSnapshot,
        boundary: AtomicSnapshotBoundary,
        source_head_reader: SnapshotSourceHeadReader,
    ) -> CommittedAtomicSnapshotBoundary:
        validate_snapshot_manifest_core(core)
        validate_snapshot_completeness_decision(
            decision,
            evaluator=evaluator,
            core=core,
        )
        validate_repository_knowledge_snapshot(snapshot, evaluator=evaluator)
        validate_atomic_snapshot_boundary(boundary, evaluator=evaluator)
        commit_observed_at = _timestamp(
            evaluator.trusted_clock(),
            "snapshot commit trusted clock",
        )
        if (
            commit_observed_at < decision.evaluated_at_utc
            or commit_observed_at >= core.valid_until_utc
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.BOUNDARY_EXPIRED,
                "snapshot transaction is outside its trusted validity interval",
            )
        if (
            boundary._core is not core
            or boundary._decision is not decision
            or boundary._snapshot is not snapshot
        ):
            raise _fail(
                KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
                "snapshot transaction records are not the same authority graph",
            )
        if not hasattr(source_head_reader, "read_snapshot_heads"):
            raise _fail(
                KnowledgeSnapshotFailureCode.REQUIRED_STORE_MISSING,
                "snapshot source-head reader is unavailable",
            )
        try:
            with self._fence.acquire() as lease:
                with lease.store_lock(self._lock_path):
                    captured = source_head_reader.read_snapshot_heads(
                        fence_lease=lease
                    )
                    _store_sequences(captured)
                    if captured != core.store_sequences:
                        raise _fail(
                            KnowledgeSnapshotFailureCode.SOURCE_HEAD_DRIFT,
                            "captured source heads differ from the manifest",
                        )
                    recovery = self.recover()
                    if recovery.diagnostic is not None:
                        raise _fail(
                            recovery.diagnostic,
                            "snapshot journal has an invalid suffix",
                        )
                    parent = recovery.trusted_head
                    if parent is None:
                        if (
                            boundary.parent_boundary_id is not None
                            or boundary.start_sequence != 0
                            or boundary.commit_sequence != 1
                        ):
                            raise _fail(
                                KnowledgeSnapshotFailureCode.GENESIS_SEQUENCE_INVALID,
                                "snapshot transaction does not match empty history",
                            )
                    else:
                        if (
                            boundary.parent_boundary_id != parent.boundary_id
                        ):
                            raise _fail(
                                KnowledgeSnapshotFailureCode.PARENT_MISMATCH,
                                "snapshot transaction parent is not current",
                            )
                        if boundary.start_sequence > parent.commit_sequence:
                            raise _fail(
                                KnowledgeSnapshotFailureCode.FAST_FORWARD_DETECTED,
                                "snapshot transaction start skips the current head",
                            )
                        if boundary.start_sequence < parent.commit_sequence:
                            raise _fail(
                                KnowledgeSnapshotFailureCode.ROLLBACK_DETECTED,
                                "snapshot transaction start is stale",
                            )
                        if (
                            boundary.commit_sequence
                            > parent.commit_sequence + 1
                        ):
                            raise _fail(
                                KnowledgeSnapshotFailureCode.FAST_FORWARD_DETECTED,
                                "snapshot transaction commit skips a sequence",
                            )
                    if any(
                        item.transaction_id == boundary.transaction_id
                        for item in recovery.committed_boundaries
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.TRANSACTION_REPEATED,
                            "snapshot transaction identity already exists",
                        )
                    if any(
                        item.attempt_id_text == boundary.envelope.attempt_id.value
                        and item.boundary_id != boundary.atomic_boundary_id
                        for item in recovery.committed_boundaries
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.ATTEMPT_BOUNDARY_REUSED,
                            "attempt already has another committed boundary",
                        )
                    artifacts = (
                        (
                            core.envelope,
                            core.payload_dict(),
                            snapshot.manifest_core_artifact_ref,
                        ),
                        (
                            decision.envelope,
                            decision.payload_dict(),
                            snapshot.completeness_decision_artifact_ref,
                        ),
                        (
                            snapshot.envelope,
                            snapshot.payload_dict(),
                            enveloped_artifact_ref(
                                envelope=snapshot.envelope,
                                payload=snapshot.payload_dict(),
                            ),
                        ),
                        (
                            boundary.envelope,
                            boundary.payload_dict(),
                            enveloped_artifact_ref(
                                envelope=boundary.envelope,
                                payload=boundary.payload_dict(),
                            ),
                        ),
                    )
                    for envelope, payload, artifact_ref in artifacts:
                        _persist_artifact(
                            directory=self._artifacts_directory,
                            raw=enveloped_artifact_bytes(
                                envelope=envelope,
                                payload=payload,
                            ),
                            artifact_ref=artifact_ref,
                        )
                    boundary_ref = artifacts[-1][2]
                    prepared_payload = _terminal_frame_payload(
                        phase=SnapshotBoundaryPhase.PREPARED,
                        boundary=boundary,
                        source_heads=captured,
                        boundary_artifact_ref=boundary_ref,
                        fence_epoch=require_coordinated_fence_lease(
                            lease,
                            expected_context=self._context,
                        ),
                    )
                    append_journal_payload(
                        self._journal_path,
                        _canonical(prepared_payload),
                    )
                    reread = source_head_reader.read_snapshot_heads(
                        fence_lease=lease
                    )
                    _store_sequences(reread)
                    if reread != captured:
                        raise _fail(
                            KnowledgeSnapshotFailureCode.SOURCE_HEAD_DRIFT,
                            "source head changed between capture and commit",
                        )
                    prepared_scan = scan_journal(self._journal_path)
                    if (
                        prepared_scan.torn_tail
                        or not prepared_scan.frames
                        or prepared_scan.frames[-1].payload
                        != _canonical(prepared_payload)
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                            "prepared snapshot transaction is not the journal tail",
                        )
                    committed_payload = _terminal_frame_payload(
                        phase=SnapshotBoundaryPhase.COMMITTED,
                        boundary=boundary,
                        source_heads=captured,
                        boundary_artifact_ref=boundary_ref,
                        fence_epoch=lease.epoch,
                    )
                    append_journal_payload(
                        self._journal_path,
                        _canonical(committed_payload),
                    )
                    committed_recovery = self.recover()
                    if (
                        committed_recovery.diagnostic is not None
                        or committed_recovery.trusted_head is None
                        or committed_recovery.trusted_head.boundary_id
                        != boundary.atomic_boundary_id
                    ):
                        raise _fail(
                            KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED,
                            "terminal snapshot boundary was not reconstructed",
                        )
                    evidence = committed_recovery.trusted_head.commit_evidence
                    lease.record_store_mutation(
                        store_name="knowledge-snapshot",
                        head_identity=boundary.atomic_boundary_id.value,
                        store_sequence=boundary.commit_sequence,
                    )
                    self._trusted_anchor = evidence.history_anchor
                    result = object.__new__(
                        CommittedAtomicSnapshotBoundary
                    )
                    object.__setattr__(result, "boundary", boundary)
                    object.__setattr__(result, "commit_evidence", evidence)
                    object.__setattr__(result, "_store", self)
                    object.__setattr__(result, "_trusted_seal", _SEAL)
                    require_committed_atomic_snapshot_boundary(
                        result,
                        evaluator=evaluator,
                        expected_store=self,
                    )
                    return result
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    KnowledgeSnapshotFailureCode.LOCK_BUSY,
                    "snapshot coordinator or store lock is busy",
                ) from exc
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "snapshot persistence operation failed",
            ) from exc

    def repair_invalid_suffix(self) -> KnowledgeRecoveryResult:
        try:
            with self._fence.acquire() as lease:
                with lease.store_lock(self._lock_path):
                    recovery = self.recover()
                    if recovery.diagnostic is None:
                        return recovery
                    raw_journal = read_regular_bytes(
                        self._journal_path,
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    )
                    invalid_suffix = raw_journal[recovery.valid_prefix_length :]
                    digest = hashlib.sha256(invalid_suffix).hexdigest()
                    destination = (
                        self._quarantine_directory
                        / f"{digest}.invalid-suffix"
                    )
                    if not destination.exists():
                        staged = write_staged_bytes(
                            self._quarantine_directory,
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
                            KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                            "snapshot journal remains invalid after repair",
                        )
                    lease.record_store_mutation(
                        store_name="knowledge-snapshot",
                        head_identity=(
                            "genesis"
                            if repaired.trusted_head is None
                            else repaired.trusted_head.boundary_id.value
                        ),
                        store_sequence=(
                            0
                            if repaired.trusted_head is None
                            else repaired.trusted_head.commit_sequence
                        ),
                    )
                    return repaired
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    KnowledgeSnapshotFailureCode.LOCK_BUSY,
                    "snapshot repair fence is busy",
                ) from exc
            raise _fail(
                KnowledgeSnapshotFailureCode.JOURNAL_CORRUPT,
                "snapshot repair persistence failed",
            ) from exc


def open_knowledge_snapshot_store(
    *,
    root: Path,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    coordination_context: SnapshotCoordinationContext,
    trusted_anchor: HistoryAnchor | None = None,
    allow_genesis: bool = False,
) -> KnowledgeSnapshotStore:
    _validate_store_coordination_context(
        context=coordination_context,
        authority_binding=authority_binding,
    )
    return KnowledgeSnapshotStore(
        root,
        authority_binding,
        coordination_context,
        trusted_anchor,
        allow_genesis,
        _seal=_STORE_SEAL,
    )


def require_committed_atomic_snapshot_boundary(
    value: CommittedAtomicSnapshotBoundary,
    *,
    evaluator: ConfiguredSnapshotCompletenessEvaluator,
    expected_store: KnowledgeSnapshotStore,
    trusted_clock: Callable[[], datetime] | None = None,
) -> AtomicSnapshotBoundary:
    if (
        type(value) is not CommittedAtomicSnapshotBoundary
        or getattr(value, "_trusted_seal", None) is not _SEAL
        or value._store is not expected_store
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED,
            "atomic snapshot boundary lacks exact store commit evidence",
        )
    validate_atomic_snapshot_boundary(value.boundary, evaluator=evaluator)
    validate_snapshot_commit_evidence(value.commit_evidence)
    if (
        value.commit_evidence.boundary_id
        != value.boundary.atomic_boundary_id
        or value.commit_evidence.transaction_id
        != value.boundary.transaction_id
        or value.commit_evidence.snapshot_id != value.boundary.snapshot_id
        or value.commit_evidence.commit_sequence
        != value.boundary.commit_sequence
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.MIX_AND_MATCH_DETECTED,
            "snapshot commit evidence differs from its boundary",
        )
    recovery = expected_store.recover()
    if (
        recovery.diagnostic is not None
        or recovery.trusted_head is None
        or recovery.trusted_head.boundary_id
        != value.boundary.atomic_boundary_id
    ):
        raise _fail(
            KnowledgeSnapshotFailureCode.BOUNDARY_NOT_COMMITTED,
            "snapshot boundary is not the current trusted committed head",
        )
    if trusted_clock is not None:
        if not callable(trusted_clock):
            raise _fail(
                KnowledgeSnapshotFailureCode.TYPE_MISMATCH,
                "trusted_clock must be callable",
            )
        now = _timestamp(trusted_clock(), "trusted_clock")
        if now >= value.boundary._core.valid_until_utc:
            raise _fail(
                KnowledgeSnapshotFailureCode.BOUNDARY_EXPIRED,
                "snapshot boundary is expired",
            )
    return value.boundary
