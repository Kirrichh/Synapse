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
    RunId,
    AttemptId,
    RepositoryRevision,
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
    compute_payload_sha256,
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
    _artifact_bytes,
    _artifact_ref,
    _canonical,
    _consumption_request_payload,
    _decision_payload,
    _checked_dimensions,
    _derive_decision_kind,
    _diagnostics,
    _fail,
    _ordered_reasons,
    _REQUEST_SPEC,
    _record,
    _ref,
    _refs,
    _safe_text,
    _timestamp,
    _timestamp_text,
)
from .admission import (
    ConfiguredIngestionGateEvaluator,
    ConfiguredPublicationGateEvaluator,
    _validate_gate_decision,
    evaluate_ingestion_gate,
    evaluate_publication_gate,
    validate_consumption_decision,
    validate_ingestion_gate_decision,
    validate_publication_gate_decision,
)
from .compatibility_store import (
    CompatibilityEvidenceStore,
    CompatibilityStoredRecordKind,
)

class AdmissionCausalRecordKind(str, Enum):
    INGESTION_GATE_DECISION = "INGESTION_GATE_DECISION"
    PUBLICATION_GATE_DECISION = "PUBLICATION_GATE_DECISION"
    RETRIEVAL_GATE_DECISION = "RETRIEVAL_GATE_DECISION"
    CONSUMPTION_GATE_DECISION = "CONSUMPTION_GATE_DECISION"
    RETRIEVAL_DECISION = "RETRIEVAL_DECISION"
    RETRIEVAL_LOAD_DECISION = "RETRIEVAL_LOAD_DECISION"
    CONSUMPTION_COMPATIBILITY_REVALIDATION = (
        "CONSUMPTION_COMPATIBILITY_REVALIDATION"
    )


_DECISION_RECORD_KIND = {
    IngestionGateDecision: AdmissionCausalRecordKind.INGESTION_GATE_DECISION,
    PublicationGateDecision: AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION,
    RetrievalGateDecision: AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION,
    ConsumptionDecision: AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION,
}

_ADMISSION_RECORD_SCHEMA = {
    AdmissionCausalRecordKind.INGESTION_GATE_DECISION: (
        INGESTION_GATE_DECISION_SCHEMA_V1
    ),
    AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION: (
        PUBLICATION_GATE_DECISION_SCHEMA_V1
    ),
    AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION: (
        RETRIEVAL_GATE_DECISION_SCHEMA_V1
    ),
    AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION: (
        CONSUMPTION_GATE_DECISION_SCHEMA_V1
    ),
    AdmissionCausalRecordKind.RETRIEVAL_DECISION: (
        "synapse.stage4.gold.retrieval-decision/v2"
    ),
    AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION: (
        "synapse.stage4.gold.retrieval-load-decision/v2"
    ),
    AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION: (
        "synapse.stage4.gold.compatibility-revalidation/v2"
    ),
}

_ADMISSION_RECORD_DOMAIN = {
    AdmissionCausalRecordKind.INGESTION_GATE_DECISION: (
        IdentityDomain.AUTHORITY_DECISION
    ),
    AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION: (
        IdentityDomain.AUTHORITY_DECISION
    ),
    AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION: (
        IdentityDomain.AUTHORITY_DECISION
    ),
    AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION: (
        IdentityDomain.AUTHORITY_DECISION
    ),
    AdmissionCausalRecordKind.RETRIEVAL_DECISION: (
        IdentityDomain.RETRIEVAL_DECISION_V2
    ),
    AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION: (
        IdentityDomain.RETRIEVAL_LOAD_DECISION_V2
    ),
    AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION: (
        IdentityDomain.COMPATIBILITY_REVALIDATION_V2
    ),
}

_GATE_DURABLE_AUTHORITY = {
    AdmissionCausalRecordKind.INGESTION_GATE_DECISION: (
        INGESTION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.INGESTION_GATE_REQUEST,
        AuthorityRole.INGESTION_GATE_EVALUATOR,
        "ingestion_gate_evaluator",
    ),
    AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION: (
        PUBLICATION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.PUBLICATION_GATE_REQUEST,
        AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        "publication_gate_evaluator",
    ),
    AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION: (
        RETRIEVAL_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.RETRIEVAL_GATE_REQUEST,
        AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        "retrieval_gate_evaluator",
    ),
    AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION: (
        CONSUMPTION_GATE_REQUEST_SCHEMA_V1,
        IdentityDomain.CONSUMPTION_GATE_REQUEST,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        "consumption_gate_evaluator",
    ),
}

_GATE_REQUEST_FIELDS = {
    AdmissionCausalRecordKind.INGESTION_GATE_DECISION: {
        "schema_version", "subject_ref", "consumer_context", "authority_heads",
        "scope_ids", "capability_ids", "tool_ids", "oracle_ids",
        "observed_at_utc", "valid_until_utc", "predecessor_sequence",
        "source_transaction_id", "source_identity", "observed_input_refs",
        "predecessor_decision_ref",
    },
    AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION: {
        "schema_version", "subject_ref", "consumer_context", "authority_heads",
        "scope_ids", "capability_ids", "tool_ids", "oracle_ids",
        "observed_at_utc", "valid_until_utc", "predecessor_sequence",
        "publication_transaction_id", "ingested_candidate_ref",
        "ingestion_decision_ref", "attestation_ref", "binding_refs",
        "target_library_predecessor_root",
    },
    AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION: {
        "schema_version", "subject_ref", "consumer_context", "authority_heads",
        "scope_ids", "capability_ids", "tool_ids", "oracle_ids",
        "observed_at_utc", "valid_until_utc", "predecessor_sequence",
        "repository_knowledge_snapshot_id", "atomic_boundary_id",
        "boundary_commit_sequence", "candidate_ref",
        "compatibility_decision_ref", "conflict_decision_ref",
    },
    AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION: {
        "schema_version", "subject_ref", "consumer_context", "authority_heads",
        "scope_ids", "capability_ids", "tool_ids", "oracle_ids",
        "observed_at_utc", "valid_until_utc", "predecessor_sequence",
        "repository_knowledge_snapshot_id", "atomic_boundary_id",
        "boundary_commit_sequence", "retrieval_decision_ref",
        "load_decision_ref", "compatibility_revalidation_ref",
    },
}

_GATE_DURABLE_DECISION = {
    AdmissionCausalRecordKind.INGESTION_GATE_DECISION: (
        IngestionGateReason,
        INGESTION_REQUIRED_DIMENSIONS,
    ),
    AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION: (
        PublicationGateReason,
        PUBLICATION_REQUIRED_DIMENSIONS,
    ),
    AdmissionCausalRecordKind.RETRIEVAL_GATE_DECISION: (
        RetrievalGateReason,
        RETRIEVAL_REQUIRED_DIMENSIONS,
    ),
    AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION: (
        ConsumptionGateReason,
        CONSUMPTION_REQUIRED_DIMENSIONS,
    ),
}


@dataclass(frozen=True, init=False)
class AdmissionCommitEvidence:
    schema_version: str
    record_kind: AdmissionCausalRecordKind
    domain_schema: str
    record_identity: str
    artifact_ref: HashBoundRef
    predecessor_identity: str | None
    sequence: int
    fence_epoch: int
    rolling_root_sha256: str
    history_anchor: HistoryAnchor
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AdmissionCommitEvidence:
        raise TypeError("AdmissionCommitEvidence is store-created")

    def to_dict(self) -> dict[str, object]:
        validate_admission_commit_evidence(self)
        return {
            "schema_version": self.schema_version,
            "record_kind": self.record_kind.value,
            "domain_schema": self.domain_schema,
            "record_identity": self.record_identity,
            "artifact_ref": self.artifact_ref.to_dict(),
            "predecessor_identity": self.predecessor_identity,
            "sequence": self.sequence,
            "fence_epoch": self.fence_epoch,
            "rolling_root_sha256": self.rolling_root_sha256,
            "history_anchor": self.history_anchor.to_dict(),
        }


def validate_admission_commit_evidence(value: AdmissionCommitEvidence) -> None:
    if (
        type(value) is not AdmissionCommitEvidence
        or getattr(value, "_trusted_seal", None) is not _COMMIT_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "admission commit evidence is not store sealed",
        )
    if value.schema_version != ADMISSION_COMMIT_EVIDENCE_SCHEMA_V1:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "admission commit evidence schema is unknown",
        )
    if type(value.record_kind) is not AdmissionCausalRecordKind:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "admission record kind is invalid",
        )
    if value.domain_schema != _ADMISSION_RECORD_SCHEMA[value.record_kind]:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "admission commit domain schema is invalid",
        )
    _safe_text(value.record_identity, "record_identity")
    _ref(value.artifact_ref, "artifact_ref")
    if value.artifact_ref.kind is not RefKind.ARTIFACT:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "admission commit artifact kind is invalid",
        )
    if value.predecessor_identity is not None:
        _safe_text(value.predecessor_identity, "predecessor_identity")
    if type(value.sequence) is not int or value.sequence < 1:
        raise _fail(
            AdmissionFailureCode.ROLLBACK_DETECTED,
            "admission commit sequence is invalid",
        )
    if (value.predecessor_identity is None) != (value.sequence == 1):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "admission commit predecessor and sequence disagree",
        )
    if type(value.fence_epoch) is not int or value.fence_epoch < 1:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "admission fence epoch is invalid",
        )
    if type(value.rolling_root_sha256) is not str or _SHA256_RE.fullmatch(
        value.rolling_root_sha256
    ) is None:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission rolling root is invalid",
        )
    validate_history_anchor(value.history_anchor)
    if (
        value.history_anchor.history_domain is not HistoryDomain.ADMISSION
        or value.history_anchor.entry_count != value.sequence
        or value.history_anchor.ordered_log_root_sha256
        != value.rolling_root_sha256
        or value.history_anchor.domain_heads != (value.record_identity,)
    ):
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission history anchor differs from committed evidence",
        )


@dataclass(frozen=True)
class AdmissionHistoryRecovery:
    history_anchor: HistoryAnchor
    committed_records: tuple[AdmissionCommitEvidence, ...]
    diagnostic: AdmissionViolation | PersistenceViolation | None
    valid_prefix_length: int
    invalid_suffix: bytes

    @property
    def head(self) -> AdmissionCommitEvidence | None:
        return self.committed_records[-1] if self.committed_records else None


def _admission_frame_payload(
    *,
    record_kind: AdmissionCausalRecordKind,
    domain_schema: str,
    record_identity: str,
    artifact_ref: HashBoundRef,
    predecessor_identity: str | None,
    sequence: int,
    fence_epoch: int,
) -> dict[str, object]:
    return {
        "schema_version": ADMISSION_HISTORY_FRAME_SCHEMA_V1,
        "record_kind": record_kind.value,
        "domain_schema": domain_schema,
        "record_identity": record_identity,
        "artifact_ref": artifact_ref.to_dict(),
        "predecessor_identity": predecessor_identity,
        "sequence": sequence,
        "fence_epoch": fence_epoch,
    }


def _admission_artifact_path(root: Path, artifact_ref: HashBoundRef) -> Path:
    return root / "artifacts" / f"{artifact_ref.sha256}.json"


def _publish_admission_artifact(
    *,
    root: Path,
    artifact_ref: HashBoundRef,
    value: bytes,
) -> None:
    path = _admission_artifact_path(root, artifact_ref)
    if path.exists():
        existing = read_regular_bytes(path, maximum_bytes=MAX_METADATA_BYTES_V1)
        if existing != value:
            raise _fail(
                AdmissionFailureCode.JOURNAL_CORRUPT,
                "immutable admission artifact identity collided",
            )
        return
    staged = write_staged_bytes(
        path.parent,
        final_name=path.name,
        operation_id=new_operation_id(),
        value=value,
        maximum_bytes=MAX_METADATA_BYTES_V1,
    )
    publish_immutable(staged, path)


def _parse_durable_enveloped_artifact(
    *,
    raw: bytes,
    expected_schema: str,
    expected_domain: IdentityDomain,
) -> tuple[dict[str, object], dict[str, object], RecordId]:
    if type(raw) is not bytes:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission artifact bytes are invalid",
        )
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission artifact is not strict JSON",
        ) from exc
    if (
        type(artifact) is not dict
        or set(artifact) != {"envelope", "payload"}
        or _canonical(artifact) != raw
        or type(artifact["envelope"]) is not dict
        or type(artifact["payload"]) is not dict
        or artifact["payload"].get("schema_version") != expected_schema
    ):
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "admission artifact shape or domain schema is invalid",
        )
    envelope = artifact["envelope"]
    payload = artifact["payload"]
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
        or envelope["schema_version"] != SchemaVersion.COMMON_ENVELOPE_V1.value
        or envelope["payload_sha256"]
        != compute_payload_sha256(canonical_bytes=payload_bytes)
        or type(envelope["lineage_parent_ids"]) is not list
        or not envelope["lineage_parent_ids"]
        or type(envelope["producer_component"]) is not str
        or not envelope["producer_component"]
        or type(envelope["policy_version"]) is not str
        or not envelope["policy_version"]
        or type(envelope["environment_profile_id"]) is not str
        or not envelope["environment_profile_id"]
    ):
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission artifact envelope is invalid",
        )
    RunId.from_dict(envelope["run_id"])
    AttemptId.from_dict(envelope["attempt_id"])
    RepositoryRevision.from_dict(envelope["repository_revision"])
    try:
        parsed_timestamp = datetime.strptime(
            envelope["created_at_utc"],
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission artifact timestamp is invalid",
        ) from exc
    if _timestamp_text(parsed_timestamp) != envelope["created_at_utc"]:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission artifact timestamp is not exact UTC",
        )
    for parent in envelope["lineage_parent_ids"]:
        if (
            type(parent) is not dict
            or set(parent) != {"parent_record_id", "edge_kind"}
            or type(parent["parent_record_id"]) is not dict
            or set(parent["parent_record_id"])
            != {"domain", "digest_sha256"}
            or parent["parent_record_id"].get("domain")
            not in {item.value for item in IdentityDomain}
            or type(parent["parent_record_id"].get("digest_sha256")) is not str
            or _SHA256_RE.fullmatch(
                parent["parent_record_id"]["digest_sha256"]
            )
            is None
            or parent.get("edge_kind")
            not in {item.value for item in LineageEdgeKind}
        ):
            raise _fail(
                AdmissionFailureCode.JOURNAL_CORRUPT,
                "admission artifact lineage is invalid",
            )
    record_id = record_id_from_dict(
        envelope["record_id"],
        canonical_bytes=payload_bytes,
    )
    if record_id.domain is not expected_domain:
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "admission artifact identity domain is invalid",
        )
    return envelope, payload, record_id


def _validate_durable_admission_artifact(
    *,
    record_kind: AdmissionCausalRecordKind,
    record_identity: str,
    artifact_ref: HashBoundRef,
    artifact: bytes,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    request_artifact: bytes | None = None,
    committed_records: tuple[AdmissionCommitEvidence, ...] = (),
) -> None:
    expected_schema = _ADMISSION_RECORD_SCHEMA[record_kind]
    expected_domain = _ADMISSION_RECORD_DOMAIN[record_kind]
    decision_envelope, payload, envelope_record_id = _parse_durable_enveloped_artifact(
        raw=artifact,
        expected_schema=expected_schema,
        expected_domain=expected_domain,
    )
    if (
        artifact_ref.sha256 != hashlib.sha256(artifact).hexdigest()
        or artifact_ref.byte_length != len(artifact)
        or artifact_ref.ref_id != f"artifact:{artifact_ref.sha256}"
        or artifact_ref.schema_id != ENVELOPED_ARTIFACT_SCHEMA_V1
        or artifact_ref.media_type != ENVELOPED_ARTIFACT_MEDIA_TYPE_V1
    ):
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "admission artifact differs from its enveloped-artifact ref",
        )
    gate_spec = _GATE_DURABLE_AUTHORITY.get(record_kind)
    if gate_spec is None:
        if record_identity != envelope_record_id.value:
            raise _fail(
                AdmissionFailureCode.IDENTITY_MISMATCH,
                "causal admission identity differs from its envelope",
            )
        return
    if request_artifact is None:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "gate request artifact is unavailable during revalidation",
        )
    decision_fields = {
        "schema_version",
        "request_ref",
        "decision_kind",
        "reasons",
        "checked_dimensions",
        "evaluator_declaration_id",
        "base_configuration_id",
        "knowledge_admission_configuration_id",
        "independence_proof",
        "evaluated_at_utc",
        "valid_until_utc",
        "predecessor_decision_id",
        "decision_sequence",
        "diagnostics",
    }
    if set(payload) != decision_fields:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "durable gate decision payload shape is invalid",
        )
    request_schema, request_domain, role, declaration_name = gate_spec
    request_envelope, request_payload, request_id = _parse_durable_enveloped_artifact(
        raw=request_artifact,
        expected_schema=request_schema,
        expected_domain=request_domain,
    )
    if set(request_payload) != _GATE_REQUEST_FIELDS[record_kind]:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "durable gate request payload shape is invalid",
        )
    request_ref = HashBoundRef.from_dict(payload["request_ref"])
    if (
        hashlib.sha256(request_artifact).hexdigest() != request_ref.sha256
        or len(request_artifact) != request_ref.byte_length
        or request_ref.ref_id != f"artifact:{request_ref.sha256}"
        or request_ref.schema_id != ENVELOPED_ARTIFACT_SCHEMA_V1
        or request_ref.media_type != ENVELOPED_ARTIFACT_MEDIA_TYPE_V1
    ):
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "gate request artifact differs from the decision ref",
        )
    proof = independence_proof_from_dict(
        payload["independence_proof"],
        proposal_canonical_bytes=_canonical(request_payload),
    )
    decision_id = compute_authority_decision_id(
        canonical_bytes=_canonical(payload),
        independence_proof=proof,
    )
    base, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    declaration = getattr(overlay, declaration_name)
    reason_type, required_dimensions = _GATE_DURABLE_DECISION[record_kind]
    try:
        decision_kind = GateDecisionKind(payload["decision_kind"])
        reasons = tuple(reason_type(item) for item in payload["reasons"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.REASON_UNKNOWN,
            "durable gate outcome or reason is unknown",
        ) from exc
    if type(payload["checked_dimensions"]) is not list:
        raise _fail(
            AdmissionFailureCode.DIMENSION_MISSING,
            "durable checked dimensions transport is invalid",
        )
    dimensions: list[GateCheckedDimension] = []
    for item in payload["checked_dimensions"]:
        if (
            type(item) is not dict
            or set(item)
            != {"schema_version", "dimension", "result", "evidence_refs"}
            or type(item["evidence_refs"]) is not list
        ):
            raise _fail(
                AdmissionFailureCode.DIMENSION_MISSING,
                "durable checked dimension shape is invalid",
            )
        try:
            result = GateDimensionResult(item["result"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                AdmissionFailureCode.DIMENSION_MISSING,
                "durable checked dimension result is unknown",
            ) from exc
        dimensions.append(
            GateCheckedDimension(
                schema_version=item["schema_version"],
                dimension=item["dimension"],
                result=result,
                evidence_refs=tuple(
                    HashBoundRef.from_dict(ref)
                    for ref in item["evidence_refs"]
                ),
            )
        )
    ordered_reasons = _ordered_reasons(tuple(reasons), reason_type=reason_type)
    checked_dimensions = _checked_dimensions(
        tuple(dimensions),
        required=required_dimensions,
    )
    if decision_kind is not _derive_decision_kind(
        reason_type=reason_type,
        reasons=ordered_reasons,
        checked_dimensions=checked_dimensions,
    ):
        raise _fail(
            AdmissionFailureCode.REASON_OUTCOME_MISMATCH,
            "durable gate outcome differs from its reasons and dimensions",
        )
    if tuple(reasons) != ordered_reasons:
        raise _fail(
            AdmissionFailureCode.REASON_OUTCOME_MISMATCH,
            "durable gate reasons are not in canonical order",
        )
    if type(payload["diagnostics"]) is not list or any(
        type(item) is not dict or set(item) != {"key", "value"}
        for item in payload["diagnostics"]
    ):
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "durable gate diagnostics are invalid",
        )
    _diagnostics(
        tuple((item["key"], item["value"]) for item in payload["diagnostics"])
    )
    try:
        evaluated_at = datetime.strptime(
            payload["evaluated_at_utc"],
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
        valid_until = datetime.strptime(
            payload["valid_until_utc"],
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.MALFORMED_DECISION,
            "durable gate decision timestamps are invalid",
        ) from exc
    sequence = payload["decision_sequence"]
    predecessor = payload["predecessor_decision_id"]
    if (
        _timestamp_text(evaluated_at) != payload["evaluated_at_utc"]
        or _timestamp_text(valid_until) != payload["valid_until_utc"]
        or valid_until <= evaluated_at
        or type(sequence) is not int
        or sequence < 1
        or (predecessor is None) != (sequence == 1)
    ):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "durable gate validity or predecessor sequence is invalid",
        )
    try:
        request_observed = datetime.strptime(
            request_payload["observed_at_utc"],
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
        request_expiry = datetime.strptime(
            request_payload["valid_until_utc"],
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.MALFORMED_REQUEST,
            "durable gate request timestamps are invalid",
        ) from exc
    if (
        _timestamp_text(request_observed) != request_payload["observed_at_utc"]
        or _timestamp_text(request_expiry) != request_payload["valid_until_utc"]
        or request_expiry <= request_observed
        or evaluated_at < request_observed
        or evaluated_at >= request_expiry
        or valid_until > request_expiry
    ):
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "durable gate decision is outside its request validity",
        )
    expected_lineage = [
        {
            "parent_record_id": request_id.to_dict(),
            "edge_kind": LineageEdgeKind.DERIVED_FROM.value,
        }
    ]
    prior_gate_records = tuple(
        item for item in committed_records if item.record_kind is record_kind
    )
    if sequence != len(prior_gate_records) + 1:
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "durable gate decision sequence is not contiguous",
        )
    if predecessor is not None:
        if (
            type(predecessor) is not dict
            or set(predecessor) != {"record_id"}
            or type(predecessor["record_id"]) is not dict
            or set(predecessor["record_id"])
            != {"domain", "digest_sha256"}
            or predecessor["record_id"].get("domain")
            != IdentityDomain.AUTHORITY_DECISION.value
            or type(predecessor["record_id"].get("digest_sha256")) is not str
            or _SHA256_RE.fullmatch(
                predecessor["record_id"]["digest_sha256"]
            )
            is None
        ):
            raise _fail(
                AdmissionFailureCode.PREDECESSOR_MISMATCH,
                "durable gate predecessor identity is invalid",
            )
        predecessor_text = (
            f"{IdentityDomain.AUTHORITY_DECISION.value}:"
            f"{predecessor['record_id']['digest_sha256']}"
        )
        if (
            not prior_gate_records
            or prior_gate_records[-1].record_identity != predecessor_text
        ):
            raise _fail(
                AdmissionFailureCode.PREDECESSOR_MISMATCH,
                "durable gate predecessor is not in the committed prefix",
            )
        expected_lineage.append(
            {
                "parent_record_id": predecessor["record_id"],
                "edge_kind": LineageEdgeKind.SUPERSEDES.value,
            }
        )
    if (
        record_identity != decision_id.record_id.value
        or payload["request_ref"] != request_ref.to_dict()
        or proof.subject_proposal_id
        != compute_proposal_id(canonical_bytes=_canonical(request_payload))
        or proof.authority_identity != declaration.evaluator_identity
        or proof.authority_role is not role
        or proof.reason_code is not declaration.independence_reason
        or payload.get("evaluator_declaration_id")
        != declaration.declaration_id.to_dict()
        or payload.get("base_configuration_id")
        != base.configuration_id.to_dict()
        or payload.get("knowledge_admission_configuration_id")
        != overlay.configuration_id.to_dict()
        or decision_envelope["lineage_parent_ids"] != expected_lineage
        or decision_envelope["run_id"] != request_envelope["run_id"]
        or decision_envelope["attempt_id"] != request_envelope["attempt_id"]
        or decision_envelope["repository_revision"]
        != request_envelope["repository_revision"]
        or decision_envelope["policy_version"]
        != request_envelope["policy_version"]
        or decision_envelope["environment_profile_id"]
        != request_envelope["environment_profile_id"]
        or decision_envelope["created_at_utc"]
        != payload["evaluated_at_utc"]
        or decision_envelope["producer_component"]
        != declaration.evaluator_component_identity.value
    ):
        raise _fail(
            AdmissionFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "durable gate authority binding is invalid",
        )


def _parse_admission_frame(value: bytes) -> dict[str, object]:
    try:
        data = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission frame is not strict JSON",
        ) from exc
    fields = {
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
        or set(data) != fields
        or _canonical(data) != value
    ):
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission frame shape is invalid",
        )
    if data["schema_version"] != ADMISSION_HISTORY_FRAME_SCHEMA_V1:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "admission frame schema is unknown",
        )
    try:
        kind = AdmissionCausalRecordKind(data["record_kind"])
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission frame kind is unknown",
        ) from exc
    if data["domain_schema"] != _ADMISSION_RECORD_SCHEMA[kind]:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA,
            "admission frame domain schema is unknown",
        )
    artifact_data = data["artifact_ref"]
    if type(artifact_data) is not dict:
        raise _fail(
            AdmissionFailureCode.JOURNAL_CORRUPT,
            "admission frame artifact ref is invalid",
        )
    artifact_ref = HashBoundRef.from_dict(artifact_data)
    return {
        **data,
        "record_kind": kind,
        "artifact_ref": artifact_ref,
    }


class AdmissionHistoryStore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _STORE_SEAL or kwargs or len(args) != 3:
            raise TypeError("AdmissionHistoryStore is factory-opened")
        root, authority_binding, coordination_context = args
        if not isinstance(root, Path) or not root.is_absolute():
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "admission store root must be absolute",
            )
        base, overlay = validate_knowledge_admission_authority_binding(
            authority_binding
        )
        validate_snapshot_coordination_context(coordination_context)
        if (
            coordination_context.base_configuration_id_text
            != base.configuration_id.value
            or coordination_context.knowledge_admission_configuration_id_text
            != overlay.configuration_id.value
        ):
            raise _fail(
                AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
                "admission store coordination context changed",
            )
        self._root = root
        self._authority_binding = authority_binding
        self._coordination_context = coordination_context
        self._fence = open_coordinated_snapshot_fence(coordination_context)
        self._journal_path = root / "admission.journal"
        self._store_lock_path = root / "admission.lock"
        if not root.exists():
            ensure_directory(root)
        artifacts = root / "artifacts"
        if not artifacts.exists():
            ensure_directory(artifacts)
        quarantine = root / "quarantine"
        if not quarantine.exists():
            ensure_directory(quarantine)
        with coordinated_store_write(
            fence=self._fence,
            context=self._coordination_context,
            store_lock_path=self._store_lock_path,
        ):
            recovery = self.recover()
            if recovery.diagnostic is not None:
                raise recovery.diagnostic

    @property
    def coordination_context(self) -> SnapshotCoordinationContext:
        validate_snapshot_coordination_context(self._coordination_context)
        return self._coordination_context

    @property
    def configuration_id(self) -> RecordId:
        _, overlay = validate_knowledge_admission_authority_binding(
            self._authority_binding
        )
        return overlay.configuration_id

    def recover(self) -> AdmissionHistoryRecovery:
        try:
            scan = scan_journal(self._journal_path)
        except PersistenceViolation as exc:
            anchor = create_history_anchor(
                history_domain=HistoryDomain.ADMISSION,
                configuration_id=self.configuration_id,
                entry_sha256s=(),
                domain_heads=(),
            )
            return AdmissionHistoryRecovery(anchor, (), exc, 0, b"")
        committed: list[AdmissionCommitEvidence] = []
        entry_sha256s: list[str] = []
        seen: set[str] = set()
        diagnostic: AdmissionViolation | PersistenceViolation | None = None
        valid_prefix = scan.valid_prefix_length
        for frame in scan.frames:
            try:
                data = _parse_admission_frame(frame.payload)
                identity = _safe_text(data["record_identity"], "record_identity")
                predecessor = data["predecessor_identity"]
                if predecessor is not None:
                    predecessor = _safe_text(predecessor, "predecessor_identity")
                sequence = data["sequence"]
                expected = len(committed) + 1
                if type(sequence) is not int or sequence < expected:
                    raise _fail(
                        AdmissionFailureCode.ROLLBACK_DETECTED,
                        "admission sequence rolled back",
                    )
                if sequence > expected:
                    raise _fail(
                        AdmissionFailureCode.FAST_FORWARD_DETECTED,
                        "admission sequence fast-forwarded",
                    )
                expected_predecessor = (
                    None if not committed else committed[-1].record_identity
                )
                if predecessor != expected_predecessor:
                    raise _fail(
                        AdmissionFailureCode.PREDECESSOR_MISMATCH,
                        "admission predecessor differs from trusted head",
                    )
                if identity in seen:
                    raise _fail(
                        AdmissionFailureCode.HISTORY_FORK,
                        "admission identity repeats",
                    )
                artifact_ref = data["artifact_ref"]
                raw = read_regular_bytes(
                    _admission_artifact_path(self._root, artifact_ref),
                    maximum_bytes=MAX_METADATA_BYTES_V1,
                )
                if (
                    hashlib.sha256(raw).hexdigest() != artifact_ref.sha256
                    or len(raw) != artifact_ref.byte_length
                ):
                    raise _fail(
                        AdmissionFailureCode.JOURNAL_CORRUPT,
                        "admission artifact differs from its committed ref",
                    )
                request_artifact = None
                if data["record_kind"] in _GATE_DURABLE_AUTHORITY:
                    artifact_data = json.loads(raw.decode("utf-8"))
                    request_ref = HashBoundRef.from_dict(
                        artifact_data["payload"]["request_ref"]
                    )
                    request_artifact = read_regular_bytes(
                        _admission_artifact_path(self._root, request_ref),
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    )
                _validate_durable_admission_artifact(
                    record_kind=data["record_kind"],
                    record_identity=identity,
                    artifact_ref=artifact_ref,
                    artifact=raw,
                    authority_binding=self._authority_binding,
                    request_artifact=request_artifact,
                    committed_records=tuple(committed),
                )
                entry_sha256s.append(hashlib.sha256(frame.payload).hexdigest())
                anchor = create_history_anchor(
                    history_domain=HistoryDomain.ADMISSION,
                    configuration_id=self.configuration_id,
                    entry_sha256s=tuple(entry_sha256s),
                    domain_heads=(identity,),
                )
                evidence = object.__new__(AdmissionCommitEvidence)
                object.__setattr__(
                    evidence,
                    "schema_version",
                    ADMISSION_COMMIT_EVIDENCE_SCHEMA_V1,
                )
                object.__setattr__(evidence, "record_kind", data["record_kind"])
                object.__setattr__(evidence, "domain_schema", data["domain_schema"])
                object.__setattr__(evidence, "record_identity", identity)
                object.__setattr__(evidence, "artifact_ref", artifact_ref)
                object.__setattr__(
                    evidence,
                    "predecessor_identity",
                    predecessor,
                )
                object.__setattr__(evidence, "sequence", sequence)
                object.__setattr__(evidence, "fence_epoch", data["fence_epoch"])
                object.__setattr__(
                    evidence,
                    "rolling_root_sha256",
                    anchor.ordered_log_root_sha256,
                )
                object.__setattr__(evidence, "history_anchor", anchor)
                object.__setattr__(evidence, "_trusted_seal", _COMMIT_SEAL)
                validate_admission_commit_evidence(evidence)
                committed.append(evidence)
                seen.add(identity)
                valid_prefix = frame.end_offset
            except (
                AdmissionViolation,
                PersistenceViolation,
                ContractViolation,
                KeyError,
                TypeError,
                ValueError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                diagnostic = (
                    exc
                    if isinstance(exc, (AdmissionViolation, PersistenceViolation))
                    else _fail(
                        AdmissionFailureCode.JOURNAL_CORRUPT,
                        "admission anchor validation failed",
                    )
                )
                valid_prefix = frame.start_offset
                break
        anchor = (
            committed[-1].history_anchor
            if committed
            else create_history_anchor(
                history_domain=HistoryDomain.ADMISSION,
                configuration_id=self.configuration_id,
                entry_sha256s=(),
                domain_heads=(),
            )
        )
        invalid_suffix = scan.torn_tail
        if diagnostic is not None:
            raw_journal = read_regular_bytes(
                self._journal_path,
                maximum_bytes=MAX_METADATA_BYTES_V1,
            )
            invalid_suffix = raw_journal[valid_prefix:]
        elif scan.torn_tail:
            diagnostic = _fail(
                AdmissionFailureCode.JOURNAL_CORRUPT,
                "admission journal has a torn tail",
            )
        return AdmissionHistoryRecovery(
            anchor,
            tuple(committed),
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
                expected_context=self._coordination_context,
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
            ancestor.history_domain is not HistoryDomain.ADMISSION
            or ancestor.configuration_id != self.configuration_id
        ):
            raise _fail(
                AdmissionFailureCode.PREDECESSOR_MISMATCH,
                "admission ancestor uses another history context",
            )
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        current = recovery.history_anchor
        if expected_current is not None:
            validate_history_anchor(expected_current)
            if current != expected_current:
                raise _fail(
                    AdmissionFailureCode.PREDECESSOR_MISMATCH,
                    "admission current head differs from durable history",
                )
        if ancestor.entry_count > len(recovery.committed_records):
            raise _fail(
                AdmissionFailureCode.ROLLBACK_DETECTED,
                "admission ancestor is newer than durable history",
            )
        expected_ancestor = (
            create_history_anchor(
                history_domain=HistoryDomain.ADMISSION,
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
                AdmissionFailureCode.HISTORY_FORK,
                "admission ancestor is not in the durable history prefix",
            )
        return current

    def append_decision(
        self,
        decision: object,
        *,
        fence_lease: CoordinatedFenceLease | None = None,
    ) -> AdmissionCommitEvidence:
        if type(decision) not in _DECISION_RECORD_KIND:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "admission store accepts only exact gate decisions",
            )
        _validate_gate_decision(decision)
        base, overlay = validate_knowledge_admission_authority_binding(
            self._authority_binding
        )
        if (
            decision.base_configuration_id != base.configuration_id
            or decision.knowledge_admission_configuration_id
            != overlay.configuration_id
        ):
            raise _fail(
                AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
                "admission decision belongs to another authority configuration",
            )
        payload = _decision_payload(decision)
        artifact = _artifact_bytes(decision.envelope, payload)
        artifact_ref = _artifact_ref(decision.envelope, payload)
        request = decision._request
        request_payload = _REQUEST_SPEC[type(request)][2](request)
        request_artifact = _artifact_bytes(request.envelope, request_payload)
        request_ref = _artifact_ref(request.envelope, request_payload)
        if request_ref != decision.request_ref:
            raise _fail(
                AdmissionFailureCode.IDENTITY_MISMATCH,
                "gate request differs from the decision request ref",
            )
        required_artifacts: tuple[
            tuple[HashBoundRef, AdmissionCausalRecordKind],
            ...,
        ]
        if (
            type(request) is IngestionGateRequest
            and request.predecessor_decision_ref is not None
        ):
            required_artifacts = (
                (
                    request.predecessor_decision_ref,
                    AdmissionCausalRecordKind.INGESTION_GATE_DECISION,
                ),
            )
        elif type(request) is PublicationGateRequest:
            required_artifacts = (
                (
                    request.ingestion_decision_ref,
                    AdmissionCausalRecordKind.INGESTION_GATE_DECISION,
                ),
            )
        elif type(request) is ConsumptionGateRequest:
            required_artifacts = (
                (
                    request.retrieval_decision_ref,
                    AdmissionCausalRecordKind.RETRIEVAL_DECISION,
                ),
                (
                    request.load_decision_ref,
                    AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION,
                ),
                (
                    request.compatibility_revalidation_ref,
                    AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION,
                ),
            )
        else:
            required_artifacts = ()
        return self._append(
            record_kind=_DECISION_RECORD_KIND[type(decision)],
            record_identity=decision.decision_id.record_id.value,
            artifact_ref=artifact_ref,
            artifact=artifact,
            expected_predecessor_sequence=request.predecessor_sequence,
            required_artifacts=required_artifacts,
            referenced_artifacts=((request_ref, request_artifact),),
            fence_lease=fence_lease,
        )

    def append_causal_artifact(
        self,
        *,
        record_kind: AdmissionCausalRecordKind,
        record_identity: RecordId,
        artifact_ref: HashBoundRef,
        artifact: bytes,
        fence_lease: CoordinatedFenceLease | None = None,
    ) -> AdmissionCommitEvidence:
        allowed = {
            AdmissionCausalRecordKind.RETRIEVAL_DECISION: (
                IdentityDomain.RETRIEVAL_DECISION_V2
            ),
            AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION: (
                IdentityDomain.RETRIEVAL_LOAD_DECISION_V2
            ),
            AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION: (
                IdentityDomain.COMPATIBILITY_REVALIDATION_V2
            ),
        }
        if type(record_kind) is not AdmissionCausalRecordKind or record_kind not in allowed:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "causal admission record kind is not permitted",
            )
        _record(record_identity, allowed[record_kind], "record_identity")
        _ref(artifact_ref, "artifact_ref")
        if (
            type(artifact) is not bytes
            or hashlib.sha256(artifact).hexdigest() != artifact_ref.sha256
            or len(artifact) != artifact_ref.byte_length
        ):
            raise _fail(
                AdmissionFailureCode.IDENTITY_MISMATCH,
                "causal admission artifact differs from its exact ref",
            )
        return self._append(
            record_kind=record_kind,
            record_identity=record_identity.value,
            artifact_ref=artifact_ref,
            artifact=artifact,
            expected_predecessor_sequence=None,
            required_artifacts=(),
            referenced_artifacts=(),
            fence_lease=fence_lease,
        )

    def _append(
        self,
        *,
        record_kind: AdmissionCausalRecordKind,
        record_identity: str,
        artifact_ref: HashBoundRef,
        artifact: bytes,
        expected_predecessor_sequence: int | None,
        required_artifacts: tuple[
            tuple[HashBoundRef, AdmissionCausalRecordKind],
            ...,
        ],
        referenced_artifacts: tuple[tuple[HashBoundRef, bytes], ...],
        fence_lease: CoordinatedFenceLease | None,
    ) -> AdmissionCommitEvidence:
        try:
            with coordinated_store_write(
                fence=self._fence,
                context=self._coordination_context,
                store_lock_path=self._store_lock_path,
                fence_lease=fence_lease,
            ) as lease:
                recovery = self.recover()
                if recovery.diagnostic is not None:
                    raise recovery.diagnostic
                if any(
                    item.record_identity == record_identity
                    for item in recovery.committed_records
                ):
                    raise _fail(
                        AdmissionFailureCode.HISTORY_FORK,
                        "admission record identity already exists",
                    )
                if (
                    expected_predecessor_sequence is not None
                    and expected_predecessor_sequence
                    != len(recovery.committed_records)
                ):
                    raise _fail(
                        AdmissionFailureCode.PREDECESSOR_MISMATCH,
                        "gate request predecessor is not the durable head",
                    )
                for required_ref, required_kind in required_artifacts:
                    matches = tuple(
                        item
                        for item in recovery.committed_records
                        if item.artifact_ref == required_ref
                        and item.record_kind is required_kind
                    )
                    if len(matches) != 1:
                        raise _fail(
                            AdmissionFailureCode.PREDECESSOR_MISMATCH,
                            "gate prerequisite is not uniquely durably included",
                        )
                predecessor = (
                    None if recovery.head is None else recovery.head.record_identity
                )
                sequence = len(recovery.committed_records) + 1
                for referenced_ref, referenced_value in referenced_artifacts:
                    _publish_admission_artifact(
                        root=self._root,
                        artifact_ref=referenced_ref,
                        value=referenced_value,
                    )
                _validate_durable_admission_artifact(
                    record_kind=record_kind,
                    record_identity=record_identity,
                    artifact_ref=artifact_ref,
                    artifact=artifact,
                    authority_binding=self._authority_binding,
                    request_artifact=(
                        None
                        if not referenced_artifacts
                        else referenced_artifacts[0][1]
                    ),
                    committed_records=recovery.committed_records,
                )
                _publish_admission_artifact(
                    root=self._root,
                    artifact_ref=artifact_ref,
                    value=artifact,
                )
                frame_payload = _admission_frame_payload(
                    record_kind=record_kind,
                    domain_schema=_ADMISSION_RECORD_SCHEMA[record_kind],
                    record_identity=record_identity,
                    artifact_ref=artifact_ref,
                    predecessor_identity=predecessor,
                    sequence=sequence,
                    fence_epoch=lease.epoch,
                )
                append_journal_payload(self._journal_path, _canonical(frame_payload))
                committed = self.recover()
                if committed.diagnostic is not None or committed.head is None:
                    raise _fail(
                        AdmissionFailureCode.DECISION_NOT_DURABLE,
                        "admission append was not durably recovered",
                    )
                evidence = committed.head
                if (
                    evidence.record_identity != record_identity
                    or evidence.artifact_ref != artifact_ref
                    or evidence.sequence != sequence
                ):
                    raise _fail(
                        AdmissionFailureCode.DECISION_NOT_DURABLE,
                        "admission committed head differs from appended record",
                    )
                lease.record_store_mutation(
                    store_name="admission",
                    head_identity=evidence.history_anchor.anchor_id.value,
                    store_sequence=evidence.sequence,
                )
                return evidence
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    AdmissionFailureCode.LOCK_BUSY,
                    "admission coordinator lock is busy",
                ) from exc
            raise

    def require_inclusion(
        self,
        *,
        record_identity: str,
        artifact_ref: HashBoundRef,
        expected_evidence: AdmissionCommitEvidence | None = None,
    ) -> AdmissionCommitEvidence:
        _safe_text(record_identity, "record_identity")
        _ref(artifact_ref, "artifact_ref")
        recovery = self.recover()
        if recovery.diagnostic is not None:
            raise recovery.diagnostic
        for evidence in recovery.committed_records:
            if (
                evidence.record_identity == record_identity
                and evidence.artifact_ref == artifact_ref
            ):
                if expected_evidence is not None:
                    validate_admission_commit_evidence(expected_evidence)
                    if evidence.to_dict() != expected_evidence.to_dict():
                        raise _fail(
                            AdmissionFailureCode.DECISION_NOT_DURABLE,
                            "admission inclusion evidence changed",
                        )
                return evidence
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "admission artifact is not in committed history",
        )

    def require_artifact_ref_inclusion(
        self,
        *,
        artifact_ref: HashBoundRef,
        expected_record_kind: AdmissionCausalRecordKind,
    ) -> AdmissionCommitEvidence:
        _ref(artifact_ref, "artifact_ref")
        if type(expected_record_kind) is not AdmissionCausalRecordKind:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "admission artifact inclusion kind is invalid",
            )
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
                AdmissionFailureCode.DECISION_NOT_DURABLE,
                "admission artifact ref is not uniquely durably included",
            )
        return matches[0]

    def read_committed_artifact(
        self,
        *,
        artifact_ref: HashBoundRef,
        expected_record_kind: AdmissionCausalRecordKind,
    ) -> tuple[AdmissionCommitEvidence, bytes]:
        evidence = self.require_artifact_ref_inclusion(
            artifact_ref=artifact_ref,
            expected_record_kind=expected_record_kind,
        )
        raw = read_regular_bytes(
            _admission_artifact_path(self._root, artifact_ref),
            maximum_bytes=MAX_METADATA_BYTES_V1,
        )
        if (
            hashlib.sha256(raw).hexdigest() != artifact_ref.sha256
            or len(raw) != artifact_ref.byte_length
        ):
            raise _fail(
                AdmissionFailureCode.JOURNAL_CORRUPT,
                "committed admission artifact bytes changed",
            )
        return evidence, raw

    def repair_invalid_suffix(self) -> AdmissionHistoryRecovery:
        try:
            with coordinated_store_write(
                fence=self._fence,
                context=self._coordination_context,
                store_lock_path=self._store_lock_path,
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
                        AdmissionFailureCode.JOURNAL_CORRUPT,
                        "admission journal remains invalid after repair",
                    )
                lease.record_store_mutation(
                    store_name="admission",
                    head_identity=repaired.history_anchor.anchor_id.value,
                    store_sequence=repaired.history_anchor.entry_count,
                )
                return repaired
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_BUSY:
                raise _fail(
                    AdmissionFailureCode.LOCK_BUSY,
                    "admission repair fence is busy",
                ) from exc
            raise _fail(
                AdmissionFailureCode.JOURNAL_CORRUPT,
                "admission repair persistence failed",
            ) from exc


@dataclass(frozen=True, init=False)
class CommittedIngestionGateDecision:
    decision: IngestionGateDecision
    commit_evidence: AdmissionCommitEvidence
    _store: AdmissionHistoryStore
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> CommittedIngestionGateDecision:
        raise TypeError("CommittedIngestionGateDecision is store-created")


@dataclass(frozen=True, init=False)
class CommittedPublicationGateDecision:
    decision: PublicationGateDecision
    commit_evidence: AdmissionCommitEvidence
    _store: AdmissionHistoryStore
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> CommittedPublicationGateDecision:
        raise TypeError("CommittedPublicationGateDecision is store-created")


_COMMITTED_GATE_SEAL = object()


def _validate_committed_gate(
    value: object,
    *,
    result_type: type,
    decision_type: type,
    expected_kind: AdmissionCausalRecordKind,
    expected_store: AdmissionHistoryStore | None,
) -> None:
    if (
        type(value) is not result_type
        or getattr(value, "_trusted_seal", None) is not _COMMITTED_GATE_SEAL
        or type(getattr(value, "decision", None)) is not decision_type
        or type(getattr(value, "commit_evidence", None))
        is not AdmissionCommitEvidence
        or type(getattr(value, "_store", None)) is not AdmissionHistoryStore
    ):
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "committed gate result is not store sealed",
        )
    if expected_store is not None and value._store is not expected_store:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "committed gate result belongs to another store",
        )
    if decision_type is IngestionGateDecision:
        validate_ingestion_gate_decision(value.decision)
    else:
        validate_publication_gate_decision(value.decision)
    validate_admission_commit_evidence(value.commit_evidence)
    decision_ref = _artifact_ref(
        value.decision.envelope,
        _decision_payload(value.decision),
    )
    committed = value._store.require_inclusion(
        record_identity=value.decision.decision_id.record_id.value,
        artifact_ref=decision_ref,
        expected_evidence=value.commit_evidence,
    )
    if committed.record_kind is not expected_kind:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "committed gate result uses another record kind",
        )


def validate_committed_ingestion_gate_decision(
    value: CommittedIngestionGateDecision,
    *,
    expected_store: AdmissionHistoryStore | None = None,
) -> None:
    _validate_committed_gate(
        value,
        result_type=CommittedIngestionGateDecision,
        decision_type=IngestionGateDecision,
        expected_kind=AdmissionCausalRecordKind.INGESTION_GATE_DECISION,
        expected_store=expected_store,
    )


def validate_committed_publication_gate_decision(
    value: CommittedPublicationGateDecision,
    *,
    expected_store: AdmissionHistoryStore | None = None,
) -> None:
    _validate_committed_gate(
        value,
        result_type=CommittedPublicationGateDecision,
        decision_type=PublicationGateDecision,
        expected_kind=AdmissionCausalRecordKind.PUBLICATION_GATE_DECISION,
        expected_store=expected_store,
    )


def evaluate_and_commit_ingestion_gate(
    *,
    evaluator: ConfiguredIngestionGateEvaluator,
    admission_store: AdmissionHistoryStore,
    request: IngestionGateRequest,
    predecessor_decision: IngestionGateDecision | None = None,
) -> CommittedIngestionGateDecision:
    if type(admission_store) is not AdmissionHistoryStore:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "ingestion gate requires an exact admission store",
        )
    decision = evaluate_ingestion_gate(
        evaluator=evaluator,
        request=request,
        predecessor_decision=predecessor_decision,
    )
    evidence = admission_store.append_decision(decision)
    result = object.__new__(CommittedIngestionGateDecision)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "commit_evidence", evidence)
    object.__setattr__(result, "_store", admission_store)
    object.__setattr__(result, "_trusted_seal", _COMMITTED_GATE_SEAL)
    validate_committed_ingestion_gate_decision(
        result,
        expected_store=admission_store,
    )
    return result


def evaluate_and_commit_publication_gate(
    *,
    evaluator: ConfiguredPublicationGateEvaluator,
    admission_store: AdmissionHistoryStore,
    request: PublicationGateRequest,
    predecessor_decision: PublicationGateDecision | None = None,
) -> CommittedPublicationGateDecision:
    if type(admission_store) is not AdmissionHistoryStore:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "publication gate requires an exact admission store",
        )
    decision = evaluate_publication_gate(
        evaluator=evaluator,
        request=request,
        predecessor_decision=predecessor_decision,
    )
    evidence = admission_store.append_decision(decision)
    result = object.__new__(CommittedPublicationGateDecision)
    object.__setattr__(result, "decision", decision)
    object.__setattr__(result, "commit_evidence", evidence)
    object.__setattr__(result, "_store", admission_store)
    object.__setattr__(result, "_trusted_seal", _COMMITTED_GATE_SEAL)
    validate_committed_publication_gate_decision(
        result,
        expected_store=admission_store,
    )
    return result


def open_admission_history_store(
    root: Path,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    coordination_context: SnapshotCoordinationContext,
) -> AdmissionHistoryStore:
    return AdmissionHistoryStore(
        root,
        authority_binding,
        coordination_context,
        _seal=_STORE_SEAL,
    )


@dataclass(frozen=True)
class RetrievalAdmissionBinding:
    admission_store: AdmissionHistoryStore
    coordination_context: SnapshotCoordinationContext
    knowledge_boundary_resolver: SealedKnowledgeBoundaryResolver
    _authority_binding: KnowledgeAdmissionAuthorityBinding
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalAdmissionBinding:
        raise TypeError("RetrievalAdmissionBinding is factory-created")


def create_retrieval_admission_binding(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    admission_store: AdmissionHistoryStore,
    coordination_context: SnapshotCoordinationContext,
    knowledge_boundary_resolver: SealedKnowledgeBoundaryResolver,
) -> RetrievalAdmissionBinding:
    if type(admission_store) is not AdmissionHistoryStore:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "retrieval admission store is invalid",
        )
    _, overlay = validate_knowledge_admission_authority_binding(authority_binding)
    validate_snapshot_coordination_context(coordination_context)
    validate_sealed_knowledge_boundary_resolver(knowledge_boundary_resolver)
    if (
        admission_store.coordination_context is not coordination_context
        or admission_store.configuration_id != overlay.configuration_id
        or coordination_context.knowledge_admission_configuration_id_text
        != overlay.configuration_id.value
        or knowledge_boundary_resolver.profile_id
        != overlay.knowledge_boundary_resolver_profile_id
        or knowledge_boundary_resolver.component_identity
        != overlay.knowledge_boundary_resolver_component_identity
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval admission dependencies use another configuration",
        )
    result = object.__new__(RetrievalAdmissionBinding)
    object.__setattr__(result, "admission_store", admission_store)
    object.__setattr__(result, "coordination_context", coordination_context)
    object.__setattr__(
        result,
        "knowledge_boundary_resolver",
        knowledge_boundary_resolver,
    )
    object.__setattr__(result, "_authority_binding", authority_binding)
    object.__setattr__(result, "_trusted_seal", _BINDING_SEAL)
    validate_retrieval_admission_binding(
        result,
        authority_binding=authority_binding,
    )
    return result


def validate_retrieval_admission_binding(
    value: RetrievalAdmissionBinding,
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
) -> None:
    if (
        type(value) is not RetrievalAdmissionBinding
        or getattr(value, "_trusted_seal", None) is not _BINDING_SEAL
        or value._authority_binding is not authority_binding
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "retrieval admission binding is not factory sealed",
        )
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    if (
        type(value.admission_store) is not AdmissionHistoryStore
        or value.admission_store.coordination_context is not value.coordination_context
        or value.admission_store.configuration_id != overlay.configuration_id
    ):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "retrieval admission store binding changed",
        )
    validate_sealed_knowledge_boundary_resolver(
        value.knowledge_boundary_resolver
    )
    if (
        value.knowledge_boundary_resolver.profile_id
        != overlay.knowledge_boundary_resolver_profile_id
        or value.knowledge_boundary_resolver.component_identity
        != overlay.knowledge_boundary_resolver_component_identity
    ):
        raise _fail(
            AdmissionFailureCode.RESOLVER_MISMATCH,
            "knowledge boundary resolver binding changed",
        )


def _context_fingerprint(envelope: CommonEnvelope) -> str:
    value = {
        "run_id": envelope.run_id.to_dict(),
        "attempt_id": envelope.attempt_id.to_dict(),
        "repository_revision": envelope.repository_revision.to_dict(),
        "policy_version": envelope.policy_version,
        "environment_profile_id": envelope.environment_profile_id,
    }
    return hashlib.sha256(_canonical(value)).hexdigest()


def gate_context_fingerprint(envelope: CommonEnvelope) -> str:
    if type(envelope) is not CommonEnvelope:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "gate context requires an exact CommonEnvelope",
        )
    return _context_fingerprint(envelope)


@dataclass(frozen=True, init=False)
class AdmittedKnowledgeHandle:
    schema_version: str
    envelope: CommonEnvelope
    handle_id: RecordId
    consumption_decision_id: AuthorityDecisionId
    consumption_decision_ref: HashBoundRef
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    loaded_subject_ref: HashBoundRef
    admission_commit_evidence: AdmissionCommitEvidence
    valid_until_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AdmittedKnowledgeHandle:
        raise TypeError("AdmittedKnowledgeHandle is issued only after durable consumption ADMIT")

    def to_dict(self) -> dict[str, object]:
        validate_admitted_knowledge_handle(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": _admitted_handle_payload(self),
        }


def _admitted_handle_payload(value: AdmittedKnowledgeHandle) -> dict[str, object]:
    return {
        "schema_version": ADMITTED_KNOWLEDGE_HANDLE_SCHEMA_V1,
        "consumption_decision_id": value.consumption_decision_id.to_dict(),
        "consumption_decision_ref": value.consumption_decision_ref.to_dict(),
        "repository_knowledge_snapshot_id": (
            value.repository_knowledge_snapshot_id.to_dict()
        ),
        "atomic_boundary_id": value.atomic_boundary_id.to_dict(),
        "boundary_commit_sequence": value.boundary_commit_sequence,
        "loaded_subject_ref": value.loaded_subject_ref.to_dict(),
        "admission_commit_evidence": value.admission_commit_evidence.to_dict(),
        "valid_until_utc": _timestamp_text(value.valid_until_utc),
    }


def create_admitted_knowledge_handle(
    *,
    consumption_request: ConsumptionGateRequest,
    consumption_decision: ConsumptionDecision,
    admission_store: AdmissionHistoryStore,
    compatibility_store: CompatibilityEvidenceStore,
    admission_commit_evidence: AdmissionCommitEvidence,
    loaded_subject_ref: HashBoundRef,
    retrieval_admission_binding: RetrievalAdmissionBinding,
) -> AdmittedKnowledgeHandle:
    validate_consumption_gate_request(consumption_request)
    validate_consumption_decision(consumption_decision)
    if (
        type(admission_store) is not AdmissionHistoryStore
        or type(compatibility_store) is not CompatibilityEvidenceStore
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "handle issuance requires exact durable history stores",
        )
    validate_retrieval_admission_binding(
        retrieval_admission_binding,
        authority_binding=admission_store._authority_binding,
    )
    if retrieval_admission_binding.admission_store is not admission_store:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "handle issuance uses another admission binding",
        )
    if consumption_decision.decision_kind is not GateDecisionKind.ADMIT:
        raise _fail(
            AdmissionFailureCode.HANDLE_FORBIDDEN,
            "non-ADMIT consumption decision cannot issue a handle",
        )
    request_ref = _artifact_ref(
        consumption_request.envelope,
        _consumption_request_payload(consumption_request),
    )
    if consumption_decision.request_ref != request_ref:
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "consumption decision belongs to another request",
        )
    decision_payload = _decision_payload(consumption_decision)
    decision_ref = _artifact_ref(consumption_decision.envelope, decision_payload)
    committed = admission_store.require_inclusion(
        record_identity=consumption_decision.decision_id.record_id.value,
        artifact_ref=decision_ref,
        expected_evidence=admission_commit_evidence,
    )
    retrieval_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.retrieval_decision_ref,
        expected_record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
    )
    loading_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.load_decision_ref,
        expected_record_kind=AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION,
    )
    revalidation_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.compatibility_revalidation_ref,
        expected_record_kind=(
            AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION
        ),
    )
    compatibility_inclusion = (
        compatibility_store.require_artifact_ref_inclusion(
            artifact_ref=consumption_request.compatibility_revalidation_ref,
            expected_record_kind=(
                CompatibilityStoredRecordKind.REVALIDATION_V2
            ),
        )
    )
    if not (
        retrieval_inclusion.sequence
        < loading_inclusion.sequence
        < revalidation_inclusion.sequence
        < committed.sequence
        and compatibility_inclusion.history_anchor
        == consumption_request.authority_heads.compatibility
    ):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "consumption causal evidence is not durably ordered",
        )
    boundary_resolution = (
        retrieval_admission_binding.knowledge_boundary_resolver.resolve_boundary(
        repository_knowledge_snapshot_id=(
            consumption_request.repository_knowledge_snapshot_id
        ),
        atomic_boundary_id=consumption_request.atomic_boundary_id,
        commit_sequence=consumption_request.boundary_commit_sequence,
        )
    )
    admission_store.require_anchor_ancestry(
        boundary_resolution.admission_anchor,
    )
    admission_store.require_anchor_ancestry(
        consumption_request.authority_heads.admission,
        expected_current=committed.history_anchor,
    )
    compatibility_store.require_anchor_ancestry(
        boundary_resolution.compatibility_anchor,
        expected_current=consumption_request.authority_heads.compatibility,
    )
    if (
        boundary_resolution.repository_knowledge_snapshot_id
        != consumption_request.repository_knowledge_snapshot_id
        or boundary_resolution.atomic_boundary_id
        != consumption_request.atomic_boundary_id
        or boundary_resolution.commit_sequence
        != consumption_request.boundary_commit_sequence
        or boundary_resolution.context_fingerprint_sha256
        != _context_fingerprint(consumption_request.envelope)
    ):
        raise _fail(
            AdmissionFailureCode.BOUNDARY_INVALID,
            "consumption request is not bound to the resolved boundary",
        )
    _ref(loaded_subject_ref, "loaded_subject_ref")
    if loaded_subject_ref != consumption_request.subject_ref:
        raise _fail(
            AdmissionFailureCode.HANDLE_FORBIDDEN,
            "loaded subject differs from the consumption request",
        )
    valid_until = min(
        consumption_request.valid_until_utc,
        consumption_decision.valid_until_utc,
        boundary_resolution.valid_until_utc,
    )
    candidate = object.__new__(AdmittedKnowledgeHandle)
    object.__setattr__(
        candidate,
        "schema_version",
        ADMITTED_KNOWLEDGE_HANDLE_SCHEMA_V1,
    )
    object.__setattr__(
        candidate,
        "consumption_decision_id",
        consumption_decision.decision_id,
    )
    object.__setattr__(candidate, "consumption_decision_ref", decision_ref)
    object.__setattr__(
        candidate,
        "repository_knowledge_snapshot_id",
        consumption_request.repository_knowledge_snapshot_id,
    )
    object.__setattr__(
        candidate,
        "atomic_boundary_id",
        consumption_request.atomic_boundary_id,
    )
    object.__setattr__(
        candidate,
        "boundary_commit_sequence",
        consumption_request.boundary_commit_sequence,
    )
    object.__setattr__(candidate, "loaded_subject_ref", loaded_subject_ref)
    object.__setattr__(candidate, "admission_commit_evidence", committed)
    object.__setattr__(candidate, "valid_until_utc", valid_until)
    payload = _admitted_handle_payload(candidate)
    payload_bytes = _canonical(payload)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.ADMITTED_KNOWLEDGE_HANDLE,
        canonical_payload_bytes=payload_bytes,
        run_id=consumption_request.envelope.run_id,
        attempt_id=consumption_request.envelope.attempt_id,
        created_at_utc=consumption_decision.evaluated_at_utc,
        producer_component=consumption_decision.envelope.producer_component,
        repository_revision=consumption_request.envelope.repository_revision,
        policy_version=consumption_request.envelope.policy_version,
        environment_profile_id=consumption_request.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                consumption_decision.envelope.record_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(candidate, "envelope", envelope)
    object.__setattr__(candidate, "handle_id", envelope.record_id)
    object.__setattr__(candidate, "_trusted_seal", _HANDLE_SEAL)
    validate_admitted_knowledge_handle(candidate)
    return candidate


def validate_admitted_knowledge_handle(value: AdmittedKnowledgeHandle) -> None:
    if (
        type(value) is not AdmittedKnowledgeHandle
        or getattr(value, "_trusted_seal", None) is not _HANDLE_SEAL
        or value.schema_version != ADMITTED_KNOWLEDGE_HANDLE_SCHEMA_V1
    ):
        raise _fail(
            AdmissionFailureCode.HANDLE_FORBIDDEN,
            "admitted knowledge handle is not factory sealed",
        )
    value.consumption_decision_id.to_dict()
    _ref(value.consumption_decision_ref, "consumption_decision_ref")
    _record(
        value.repository_knowledge_snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "repository_knowledge_snapshot_id",
    )
    _record(
        value.atomic_boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "atomic_boundary_id",
    )
    _ref(value.loaded_subject_ref, "loaded_subject_ref")
    validate_admission_commit_evidence(value.admission_commit_evidence)
    if (
        value.admission_commit_evidence.record_kind
        is not AdmissionCausalRecordKind.CONSUMPTION_GATE_DECISION
        or value.admission_commit_evidence.record_identity
        != value.consumption_decision_id.record_id.value
        or value.admission_commit_evidence.artifact_ref
        != value.consumption_decision_ref
    ):
        raise _fail(
            AdmissionFailureCode.HANDLE_FORBIDDEN,
            "handle consumption authority is not durably bound",
        )
    if type(value.boundary_commit_sequence) is not int or value.boundary_commit_sequence < 1:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_INVALID,
            "handle boundary sequence is invalid",
        )
    _timestamp(value.valid_until_utc, "handle.valid_until_utc")
    payload = _admitted_handle_payload(value)
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.envelope.record_id.domain is not IdentityDomain.ADMITTED_KNOWLEDGE_HANDLE
        or value.handle_id != value.envelope.record_id
    ):
        raise _fail(
            AdmissionFailureCode.IDENTITY_MISMATCH,
            "admitted knowledge handle identity changed",
        )


def require_consumption_admitted(
    value: AdmittedKnowledgeHandle,
    *,
    consumption_request: ConsumptionGateRequest,
    consumption_decision: ConsumptionDecision,
    admission_store: AdmissionHistoryStore,
    compatibility_store: CompatibilityEvidenceStore,
    retrieval_admission_binding: RetrievalAdmissionBinding,
    current_heads: GateAuthorityHeads,
    at_utc: datetime,
) -> HashBoundRef:
    validate_admitted_knowledge_handle(value)
    validate_consumption_gate_request(consumption_request)
    validate_consumption_decision(consumption_decision)
    if (
        type(admission_store) is not AdmissionHistoryStore
        or type(compatibility_store) is not CompatibilityEvidenceStore
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "handle use requires exact durable history stores",
        )
    validate_retrieval_admission_binding(
        retrieval_admission_binding,
        authority_binding=admission_store._authority_binding,
    )
    if retrieval_admission_binding.admission_store is not admission_store:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "handle use has another admission binding",
        )
    if consumption_decision.decision_kind is not GateDecisionKind.ADMIT:
        raise _fail(
            AdmissionFailureCode.HANDLE_FORBIDDEN,
            "consumption authority is no longer ADMIT",
        )
    if (
        value.consumption_decision_id != consumption_decision.decision_id
        or value.repository_knowledge_snapshot_id
        != consumption_request.repository_knowledge_snapshot_id
        or value.atomic_boundary_id != consumption_request.atomic_boundary_id
        or value.boundary_commit_sequence
        != consumption_request.boundary_commit_sequence
        or value.loaded_subject_ref != consumption_request.subject_ref
        or value.envelope.run_id != consumption_request.envelope.run_id
        or value.envelope.attempt_id != consumption_request.envelope.attempt_id
        or value.envelope.repository_revision
        != consumption_request.envelope.repository_revision
        or value.envelope.policy_version
        != consumption_request.envelope.policy_version
        or value.envelope.environment_profile_id
        != consumption_request.envelope.environment_profile_id
    ):
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "admitted handle context changed before consumption",
        )
    now = _timestamp(at_utc, "at_utc")
    if now >= value.valid_until_utc or now >= consumption_decision.valid_until_utc:
        raise _fail(
            AdmissionFailureCode.DECISION_EXPIRED,
            "admitted knowledge handle expired",
        )
    admission_store.require_inclusion(
        record_identity=value.consumption_decision_id.record_id.value,
        artifact_ref=value.consumption_decision_ref,
        expected_evidence=value.admission_commit_evidence,
    )
    retrieval_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.retrieval_decision_ref,
        expected_record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
    )
    loading_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.load_decision_ref,
        expected_record_kind=AdmissionCausalRecordKind.RETRIEVAL_LOAD_DECISION,
    )
    revalidation_inclusion = admission_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.compatibility_revalidation_ref,
        expected_record_kind=(
            AdmissionCausalRecordKind.CONSUMPTION_COMPATIBILITY_REVALIDATION
        ),
    )
    compatibility_store.require_artifact_ref_inclusion(
        artifact_ref=consumption_request.compatibility_revalidation_ref,
        expected_record_kind=CompatibilityStoredRecordKind.REVALIDATION_V2,
    )
    if not (
        retrieval_inclusion.sequence
        < loading_inclusion.sequence
        < revalidation_inclusion.sequence
        < value.admission_commit_evidence.sequence
    ):
        raise _fail(
            AdmissionFailureCode.PREDECESSOR_MISMATCH,
            "consumption causal evidence order changed before use",
        )
    resolution = (
        retrieval_admission_binding.knowledge_boundary_resolver.resolve_boundary(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        commit_sequence=value.boundary_commit_sequence,
        )
    )
    admission_store.require_anchor_ancestry(
        resolution.admission_anchor,
    )
    admission_store.require_anchor_ancestry(
        consumption_request.authority_heads.admission,
        expected_current=current_heads.admission,
    )
    compatibility_store.require_anchor_ancestry(
        resolution.compatibility_anchor,
    )
    compatibility_store.require_anchor_ancestry(
        consumption_request.authority_heads.compatibility,
        expected_current=current_heads.compatibility,
    )
    if (
        resolution.context_fingerprint_sha256
        != _context_fingerprint(consumption_request.envelope)
        or resolution.valid_until_utc <= now
    ):
        raise _fail(
            AdmissionFailureCode.BOUNDARY_INVALID,
            "knowledge boundary is stale or changed",
        )
    current_heads.to_dict()
    consumption_request.authority_heads.to_dict()
    if (
        current_heads.lifecycle != consumption_request.authority_heads.lifecycle
        or current_heads.provenance != consumption_request.authority_heads.provenance
        or current_heads.taint != consumption_request.authority_heads.taint
        or current_heads.admission.entry_count
        < value.admission_commit_evidence.history_anchor.entry_count
        or current_heads.compatibility.entry_count
        < consumption_request.authority_heads.compatibility.entry_count
    ):
        raise _fail(
            AdmissionFailureCode.CONTEXT_MISMATCH,
            "authority heads changed outside permitted causal extensions",
        )
    return value.loaded_subject_ref
