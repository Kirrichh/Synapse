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
from .admission_contracts import _artifact_bytes, _artifact_ref, _canonical, _fail, _record, _ref, _refs, _safe_text, _timestamp, _timestamp_text

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


@dataclass(frozen=True, init=False)
class AdmissionCommitEvidence:
    schema_version: str
    record_kind: AdmissionCausalRecordKind
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
    record_identity: str,
    artifact_ref: HashBoundRef,
    predecessor_identity: str | None,
    sequence: int,
    fence_epoch: int,
) -> dict[str, object]:
    return {
        "schema_version": ADMISSION_HISTORY_FRAME_SCHEMA_V1,
        "record_kind": record_kind.value,
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
        "record_identity",
        "artifact_ref",
        "predecessor_identity",
        "sequence",
        "fence_epoch",
    }
    if type(data) is not dict or set(data) != fields:
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
            except (AdmissionViolation, PersistenceViolation, ContractViolation) as exc:
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
        return self._append(
            record_kind=_DECISION_RECORD_KIND[type(decision)],
            record_identity=decision.decision_id.record_id.value,
            artifact_ref=artifact_ref,
            artifact=artifact,
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
            fence_lease=fence_lease,
        )

    def _append(
        self,
        *,
        record_kind: AdmissionCausalRecordKind,
        record_identity: str,
        artifact_ref: HashBoundRef,
        artifact: bytes,
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
                predecessor = (
                    None if recovery.head is None else recovery.head.record_identity
                )
                sequence = len(recovery.committed_records) + 1
                _publish_admission_artifact(
                    root=self._root,
                    artifact_ref=artifact_ref,
                    value=artifact,
                )
                frame_payload = _admission_frame_payload(
                    record_kind=record_kind,
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
            if exc.failure_code is PersistenceFailureCode.LOCK_FAILED:
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
    admission_commit_evidence: AdmissionCommitEvidence,
    loaded_subject_ref: HashBoundRef,
    boundary_resolution: KnowledgeBoundaryResolution,
) -> AdmittedKnowledgeHandle:
    validate_consumption_gate_request(consumption_request)
    validate_consumption_decision(consumption_decision)
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
    boundary_resolution.__post_init__()
    if (
        boundary_resolution.repository_knowledge_snapshot_id
        != consumption_request.repository_knowledge_snapshot_id
        or boundary_resolution.atomic_boundary_id
        != consumption_request.atomic_boundary_id
        or boundary_resolution.commit_sequence
        != consumption_request.boundary_commit_sequence
        or boundary_resolution.context_fingerprint_sha256
        != _context_fingerprint(consumption_request.envelope)
        or boundary_resolution.admission_anchor.entry_count
        > committed.history_anchor.entry_count
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
    boundary_resolver: SealedKnowledgeBoundaryResolver,
    current_heads: GateAuthorityHeads,
    at_utc: datetime,
) -> HashBoundRef:
    validate_admitted_knowledge_handle(value)
    validate_consumption_gate_request(consumption_request)
    validate_consumption_decision(consumption_decision)
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
    resolution = boundary_resolver.resolve_boundary(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        commit_sequence=value.boundary_commit_sequence,
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
