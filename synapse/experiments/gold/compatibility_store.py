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
from .snapshot_compatibility import *

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


@dataclass(frozen=True, init=False)
class CompatibilityCommitEvidence:
    schema_version: str
    record_kind: CompatibilityStoredRecordKind
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
            CompatibilityFailureCode.RECORD_NOT_DURABLE,
            "compatibility commit evidence is not store sealed",
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
            CompatibilityFailureCode.PREDECESSOR_MISMATCH,
            "compatibility commit sequence or predecessor is invalid",
        )
    if type(value.fence_epoch) is not int or value.fence_epoch < 1:
        raise _fail(
            CompatibilityFailureCode.RECORD_NOT_DURABLE,
            "compatibility fence epoch is invalid",
        )
    validate_history_anchor(value.history_anchor)
    if (
        value.history_anchor.history_domain is not HistoryDomain.COMPATIBILITY
        or value.history_anchor.entry_count != value.sequence
        or value.history_anchor.domain_heads != (value.record_identity,)
    ):
        raise _fail(
            CompatibilityFailureCode.JOURNAL_CORRUPT,
            "compatibility history anchor differs from commit evidence",
        )


@dataclass(frozen=True)
class CompatibilityHistoryRecovery:
    history_anchor: HistoryAnchor
    committed_records: tuple[CompatibilityCommitEvidence, ...]
    diagnostic: CompatibilityViolation | PersistenceViolation | None
    valid_prefix_length: int
    invalid_suffix: bytes

    @property
    def head(self) -> CompatibilityCommitEvidence | None:
        return self.committed_records[-1] if self.committed_records else None


def _compatibility_stored_artifact(
    value: object,
) -> tuple[CompatibilityStoredRecordKind, str, bytes, HashBoundRef]:
    if type(value) is SnapshotBoundCompatibilityContext:
        validate_snapshot_bound_compatibility_context(value)
        payload = _snapshot_context_v2_payload(value)
        kind = CompatibilityStoredRecordKind.CONTEXT_V2
        identity = value.context_id.value
        raw = _enveloped_compatibility_bytes(value.envelope, payload)
        ref = _enveloped_compatibility_ref(value.envelope, payload)
    elif type(value) is SnapshotBoundCompatibilityEvidence:
        validate_snapshot_bound_compatibility_evidence(value)
        payload = _snapshot_evidence_v2_payload(value)
        kind = CompatibilityStoredRecordKind.EVIDENCE_V2
        identity = value.evidence_id.value
        raw = _enveloped_compatibility_bytes(value.envelope, payload)
        ref = _enveloped_compatibility_ref(value.envelope, payload)
    elif type(value) is SnapshotBoundCompatibilityDecision:
        validate_snapshot_bound_compatibility_decision(value)
        payload = _snapshot_decision_v2_payload(value)
        kind = CompatibilityStoredRecordKind.DECISION_V2
        identity = value.decision_id.record_id.value
        raw = _enveloped_compatibility_bytes(value.envelope, payload)
        ref = _enveloped_compatibility_ref(value.envelope, payload)
    elif type(value) is SnapshotBoundCompatibilityRevalidation:
        validate_snapshot_bound_compatibility_revalidation(value)
        payload = _snapshot_revalidation_v2_payload(value)
        kind = CompatibilityStoredRecordKind.REVALIDATION_V2
        identity = value.revalidation_id.value
        raw = _enveloped_compatibility_bytes(value.envelope, payload)
        ref = _enveloped_compatibility_ref(value.envelope, payload)
    else:
        historical = {
            CompatibilityContext: (
                CompatibilityStoredRecordKind.CONTEXT_V1,
                validate_compatibility_context,
                _context_payload,
                "context_id",
            ),
            CompatibilityEvidence: (
                CompatibilityStoredRecordKind.EVIDENCE_V1,
                validate_compatibility_evidence,
                _evidence_final_payload,
                "evidence_id",
            ),
            CompatibilityDecision: (
                CompatibilityStoredRecordKind.DECISION_V1,
                validate_compatibility_decision,
                lambda item: {
                    **_decision_identity_payload(
                        item.kind,
                        item.evidence.evidence_core_id,
                        item._evaluator.declaration,
                    ),
                    "decision_id": item.decision_id.to_dict(),
                },
                "decision_id",
            ),
            CompatibilityRevalidationRecord: (
                CompatibilityStoredRecordKind.REVALIDATION_V1,
                validate_compatibility_revalidation_record,
                _revalidation_payload,
                "revalidation_id",
            ),
            CompatibilityConflictScan: (
                CompatibilityStoredRecordKind.CONFLICT_SCAN_V1,
                lambda item: validate_compatibility_conflict_scan(
                    item,
                    evaluator=item._evaluator,
                ),
                _conflict_scan_payload,
                "scan_id",
            ),
        }
        spec = historical.get(type(value))
        if spec is None:
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "compatibility store record kind is unsupported",
            )
        kind, validator, payload_factory, identity_name = spec
        try:
            validator(value)
        except TypeError as exc:
            raise _fail(
                CompatibilityFailureCode.TYPE_MISMATCH,
                "historical compatibility record requires its evaluator",
            ) from exc
        payload = payload_factory(value)
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


def snapshot_bound_compatibility_artifact_bytes(value: object) -> bytes:
    kind, _, raw, _ = _compatibility_stored_artifact(value)
    if kind not in {
        CompatibilityStoredRecordKind.CONTEXT_V2,
        CompatibilityStoredRecordKind.EVIDENCE_V2,
        CompatibilityStoredRecordKind.DECISION_V2,
        CompatibilityStoredRecordKind.REVALIDATION_V2,
    }:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "current authority requires a snapshot-bound compatibility artifact",
        )
    return raw


def snapshot_bound_compatibility_artifact_ref(value: object) -> HashBoundRef:
    kind, _, _, ref = _compatibility_stored_artifact(value)
    if kind not in {
        CompatibilityStoredRecordKind.CONTEXT_V2,
        CompatibilityStoredRecordKind.EVIDENCE_V2,
        CompatibilityStoredRecordKind.DECISION_V2,
        CompatibilityStoredRecordKind.REVALIDATION_V2,
    }:
        raise _fail(
            CompatibilityFailureCode.TYPE_MISMATCH,
            "current authority requires a snapshot-bound compatibility artifact",
        )
    return ref


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
        diagnostic: CompatibilityViolation | PersistenceViolation | None = None
        valid_prefix = scan.valid_prefix_length
        for frame in scan.frames:
            try:
                try:
                    data = json.loads(frame.payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _fail(
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
                        "compatibility frame is not strict JSON",
                    ) from exc
                required = {
                    "schema_version",
                    "record_kind",
                    "record_identity",
                    "artifact_ref",
                    "predecessor_identity",
                    "sequence",
                    "fence_epoch",
                }
                if type(data) is not dict or set(data) != required:
                    raise _fail(
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
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
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
                        "compatibility frame enum or ref is invalid",
                    ) from exc
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
                        CompatibilityFailureCode.ROLLBACK_DETECTED,
                        "compatibility sequence rolled back",
                    )
                if sequence > expected:
                    raise _fail(
                        CompatibilityFailureCode.FAST_FORWARD_DETECTED,
                        "compatibility sequence fast-forwarded",
                    )
                expected_predecessor = (
                    None if not evidence else evidence[-1].record_identity
                )
                if predecessor != expected_predecessor:
                    raise _fail(
                        CompatibilityFailureCode.PREDECESSOR_MISMATCH,
                        "compatibility predecessor differs from trusted head",
                    )
                if identity in seen:
                    raise _fail(
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
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
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
                        "compatibility artifact differs from its ref",
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
            except (CompatibilityViolation, PersistenceViolation, ValueError) as exc:
                diagnostic = (
                    exc
                    if isinstance(exc, (CompatibilityViolation, PersistenceViolation))
                    else _fail(
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
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
                CompatibilityFailureCode.JOURNAL_CORRUPT,
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
                        CompatibilityFailureCode.JOURNAL_CORRUPT,
                        "compatibility record identity already exists",
                    )
                path = _compatibility_artifact_path(self._root, ref)
                if path.exists():
                    if read_regular_bytes(
                        path,
                        maximum_bytes=MAX_METADATA_BYTES_V1,
                    ) != raw:
                        raise _fail(
                            CompatibilityFailureCode.JOURNAL_CORRUPT,
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
                        CompatibilityFailureCode.RECORD_NOT_DURABLE,
                        "compatibility append was not durably recovered",
                    )
                if (
                    committed.head.record_identity != identity
                    or committed.head.artifact_ref != ref
                ):
                    raise _fail(
                        CompatibilityFailureCode.RECORD_NOT_DURABLE,
                        "compatibility committed head changed",
                    )
                lease.record_store_mutation(
                    store_name="compatibility",
                    head_identity=committed.history_anchor.anchor_id.value,
                    store_sequence=sequence,
                )
                return committed.head
        except PersistenceViolation as exc:
            if exc.failure_code is PersistenceFailureCode.LOCK_FAILED:
                raise _fail(
                    CompatibilityFailureCode.LOCK_BUSY,
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
                            CompatibilityFailureCode.RECORD_NOT_DURABLE,
                            "compatibility inclusion evidence changed",
                        )
                return item
        raise _fail(
            CompatibilityFailureCode.RECORD_NOT_DURABLE,
            "compatibility artifact is not durably included",
        )


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
