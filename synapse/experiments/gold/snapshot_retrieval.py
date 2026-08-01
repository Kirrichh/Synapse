"""Stage 4 candidate discovery, deterministic ranking, and verified loading.

Index and semantic-score data remain non-authoritative. This module stops
before replay, worker dispatch, or host execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Callable, Protocol

from .behavior import BehaviorKind
from .bindings import (
    BindingKind,
    DocumentBinding,
    PythonBinding,
    RequirementBinding,
    binding_to_ref,
)
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    ContentKey,
    RefKind,
    canonicalize_stage4_payload,
)
from .compatibility import (
    COMPATIBILITY_POLICY_V1,
    CompatibilityConflictScan,
    CompatibilityContext,
    CompatibilityDecision,
    CompatibilityDecisionKind,
    CompatibilityFailureCode,
    CompatibilityRevalidationRecord,
    CompatibilitySubjectDescriptor,
    CompatibilityViolation,
    ConfiguredCompatibilityEvaluator,
    ConflictDecisionKind,
    ConflictEvidenceProposal,
    RevalidationOutcome,
    RevalidationStage,
    evaluate_compatibility,
    evaluate_conflicts,
    reconcile_index_entry,
    require_configured_compatibility_evaluator,
    require_revalidation_passed,
    revalidate_before_consumption,
    revalidate_before_loading,
    validate_compatibility_conflict_scan,
    validate_compatibility_context,
    validate_compatibility_decision,
    validate_compatibility_revalidation_record,
    validate_compatibility_subject_descriptor,
    validate_loaded_compatibility_subject,
)
from .snapshot_compatibility import (
    ConfiguredSnapshotCompatibility,
    SnapshotBoundCompatibilityContext,
    SnapshotBoundCompatibilityEvidence,
    SnapshotBoundCompatibilityRevalidation,
    require_configured_snapshot_compatibility,
    snapshot_bound_compatibility_artifact_ref,
    validate_snapshot_bound_compatibility_revalidation,
)
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    CommonEnvelope,
    ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    ENVELOPED_ARTIFACT_SCHEMA_V1,
    IdentityDomain,
    LineageEdgeKind,
    LineageParentRef,
    ProposalId,
    RecordId,
    SchemaVersion,
    Stage4AuthorityHandle,
    create_common_envelope,
    compute_record_id,
    require_stage4_authority_handle,
    validate_common_envelope,
    validate_record_id,
)
from .authority_overlay import (
    KnowledgeAdmissionAuthorityBinding,
    validate_knowledge_admission_authority_binding,
)
from .admission_contracts import (
    GateAuthorityHeads,
    GateConsumerContext,
    GateDecisionKind,
    SealedRetrievalAuthorityResolver,
    seal_retrieval_authority_resolver,
    validate_gate_authority_heads,
    validate_sealed_retrieval_authority_resolver,
)
from .admission_store import (
    AdmissionCausalRecordKind,
    AdmissionCommitEvidence,
    AdmittedKnowledgeHandle,
    RetrievalAdmissionBinding,
    validate_admission_commit_evidence,
    validate_admitted_knowledge_handle,
    validate_retrieval_admission_binding,
)
from .knowledge_contracts import (
    AtomicSnapshotBoundary,
    ConfiguredSnapshotCompletenessEvaluator,
    RepositoryKnowledgeSnapshot,
    SnapshotManifestCore,
    SnapshotObjectStatus,
    SnapshotViewEntry,
    validate_repository_knowledge_snapshot,
    validate_snapshot_manifest_core,
)
from .library import (
    MAX_INDEX_ENTRIES_V1,
    BehaviorLibrary,
    IndexEntry,
    LibrarySnapshot,
    LibraryViolation,
    SnapshotVerificationStatus,
    VerifiedBehaviorRecord,
    validate_snapshot_verification,
    validate_verified_behavior_record,
)


RETRIEVAL_QUERY_V1 = "synapse.stage4.gold.retrieval-query/v1"
RETRIEVAL_BINDING_TARGET_V1 = "synapse.stage4.gold.retrieval-binding-target/v1"
RETRIEVAL_CANDIDATE_V1 = "synapse.stage4.gold.retrieval-candidate/v1"
RANKING_FEATURE_OBSERVATION_V1 = "synapse.stage4.gold.ranking-feature-observation/v1"
RETRIEVAL_CONFLICT_RECORD_V1 = "synapse.stage4.gold.retrieval-conflict-record/v1"
RETRIEVAL_DECISION_V1 = "synapse.stage4.gold.retrieval-decision/v1"
RETRIEVAL_LOAD_DECISION_V1 = "synapse.stage4.gold.retrieval-load-decision/v1"
RETRIEVAL_POLICY_V1 = "synapse.stage4.gold.retrieval-policy/v1"
RANKING_PROFILE_V1 = "synapse.stage4.gold.ranking-profile/v1"
RETRIEVAL_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.retrieval+json"
RETRIEVAL_QUERY_V2 = "synapse.stage4.gold.retrieval-query/v2"
RETRIEVAL_DECISION_V2 = "synapse.stage4.gold.retrieval-decision/v2"
RETRIEVAL_LOAD_DECISION_V2 = (
    "synapse.stage4.gold.retrieval-load-decision/v2"
)

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SNAPSHOT_RETRIEVAL_SEAL = object()
from .retrieval import *
from .patch6_adapters import (
    ConfiguredPatch6RetrievalAdapter,
    require_patch6_compatibility_adapter,
    require_patch6_retrieval_adapter,
)


def _fail(
    code: RetrievalFailureCode,
    detail: str,
) -> RetrievalViolation:
    return RetrievalViolation(code, detail)


def _canonical(value: object) -> bytes:
    try:
        return canonicalize_stage4_payload(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot retrieval canonical payload is invalid",
        ) from exc


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            f"{name} is invalid",
        )
    return value


def _record(
    value: object,
    domain: IdentityDomain,
    name: str,
) -> RecordId:
    if type(value) is not RecordId:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            f"{name} must be an exact RecordId",
        )
    value.to_dict()
    if value.domain is not domain:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            f"{name} uses the wrong identity domain",
        )
    return value


def _timestamp(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            f"{name} must be timezone-aware UTC",
        )
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").astimezone(timezone.utc).strftime(
        _UTC_FORMAT
    )


def _score(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise _fail(
            RetrievalFailureCode.RANKING_INPUT_MALFORMED,
            "semantic score must be an exact integer in 0..1_000_000",
        )
    return value


def _retrieval_artifact_bytes(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> bytes:
    payload_bytes = _canonical(payload)
    validate_common_envelope(envelope, canonical_payload_bytes=payload_bytes)
    return _canonical({"envelope": envelope.to_dict(), "payload": payload})


def snapshot_bound_retrieval_artifact_bytes(
    *,
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> bytes:
    return _retrieval_artifact_bytes(envelope, payload)


def _retrieval_artifact_ref(
    envelope: CommonEnvelope,
    payload: dict[str, object],
) -> HashBoundRef:
    raw = _retrieval_artifact_bytes(envelope, payload)
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=f"artifact:{digest}",
        schema_id=ENVELOPED_ARTIFACT_SCHEMA_V1,
        sha256=digest,
        byte_length=len(raw),
        media_type=ENVELOPED_ARTIFACT_MEDIA_TYPE_V1,
    )


def _validate_record_id_transport(
    value: object,
    *,
    domain: IdentityDomain,
    name: str,
) -> None:
    if (
        type(value) is not dict
        or set(value) != {"domain", "digest_sha256"}
        or value.get("domain") != domain.value
        or type(value.get("digest_sha256")) is not str
        or _SHA256_RE.fullmatch(value["digest_sha256"]) is None
    ):
        raise _fail(
            RetrievalFailureCode.IDENTITY_MISMATCH,
            f"{name} identity transport is invalid",
        )


def _validate_ranking_observation_transport(
    value: object,
    *,
    expected_query_id: dict[str, object],
) -> int:
    fields = {
        "schema_version",
        "query_id",
        "compatibility_context_id",
        "descriptor_id",
        "score_input_ref",
        "semantic_score_micros",
        "scorer_component_id",
        "scorer_component_version",
        "scoring_profile",
        "observation_id",
    }
    if type(value) is not dict or set(value) != fields:
        raise _fail(
            RetrievalFailureCode.RANKING_INPUT_MALFORMED,
            "persisted ranking observation shape is invalid",
        )
    if (
        value["schema_version"] != RANKING_FEATURE_OBSERVATION_V1
        or value["query_id"] != expected_query_id
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "persisted ranking observation uses another query",
        )
    _validate_record_id_transport(
        value["query_id"],
        domain=IdentityDomain.RETRIEVAL_QUERY_V2,
        name="ranking query",
    )
    _validate_record_id_transport(
        value["compatibility_context_id"],
        domain=IdentityDomain.COMPATIBILITY_CONTEXT_V2,
        name="ranking compatibility context",
    )
    _validate_record_id_transport(
        value["descriptor_id"],
        domain=IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR,
        name="ranking descriptor",
    )
    HashBoundRef.from_dict(value["score_input_ref"])
    score = _score(value["semantic_score_micros"])
    _safe_id(value["scorer_component_id"], "ranking scorer component")
    _safe_id(value["scorer_component_version"], "ranking scorer version")
    _safe_id(value["scoring_profile"], "ranking scoring profile")
    observation_payload = {
        key: value[key]
        for key in fields
        if key != "observation_id"
    }
    expected_observation_id = compute_record_id(
        domain=IdentityDomain.RETRIEVAL_RANKING_FEATURE,
        canonical_bytes=_canonical(observation_payload),
    )
    if value["observation_id"] != expected_observation_id.to_dict():
        raise _fail(
            RetrievalFailureCode.IDENTITY_MISMATCH,
            "persisted ranking observation identity changed",
        )
    return score


def validate_snapshot_bound_retrieval_decision_artifact(
    artifact: bytes,
    *,
    expected_envelope: CommonEnvelope,
    expected_producer_component: str,
) -> RecordId:
    if type(artifact) is not bytes:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "retrieval decision artifact must be exact bytes",
        )
    try:
        raw = json.loads(artifact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "retrieval decision artifact is malformed",
        ) from exc
    if (
        type(raw) is not dict
        or set(raw) != {"envelope", "payload"}
        or _canonical(raw) != artifact
        or raw["envelope"] != expected_envelope.to_dict()
        or type(raw["payload"]) is not dict
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "retrieval decision artifact envelope/payload shape changed",
        )
    payload = raw["payload"]
    fields = {
        "schema_version",
        "query_id",
        "repository_knowledge_snapshot_id",
        "atomic_boundary_id",
        "boundary_commit_sequence",
        "considered_candidates",
        "selected_content_identities",
        "conflict_scan_ref",
        "created_at_utc",
    }
    if type(payload) is not dict or set(payload) != fields:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "retrieval decision payload shape changed",
        )
    payload_bytes = _canonical(payload)
    validate_common_envelope(
        expected_envelope,
        canonical_payload_bytes=payload_bytes,
    )
    if (
        payload["schema_version"] != RETRIEVAL_DECISION_V2
        or expected_envelope.record_id.domain
        is not IdentityDomain.RETRIEVAL_DECISION_V2
        or expected_envelope.producer_component != expected_producer_component
        or type(payload["boundary_commit_sequence"]) is not int
        or payload["boundary_commit_sequence"] < 1
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval decision schema, producer, or boundary changed",
        )
    if (
        len(expected_envelope.lineage_parent_ids) != 1
        or expected_envelope.lineage_parent_ids[0].edge_kind
        is not LineageEdgeKind.DERIVED_FROM
        or payload["query_id"]
        != expected_envelope.lineage_parent_ids[0].parent_record_id.to_dict()
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval decision query lineage changed",
        )
    _validate_record_id_transport(
        payload["query_id"],
        domain=IdentityDomain.RETRIEVAL_QUERY_V2,
        name="retrieval query",
    )
    _validate_record_id_transport(
        payload["repository_knowledge_snapshot_id"],
        domain=IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        name="retrieval snapshot",
    )
    _validate_record_id_transport(
        payload["atomic_boundary_id"],
        domain=IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        name="retrieval boundary",
    )
    HashBoundRef.from_dict(payload["conflict_scan_ref"])
    created_at_text = payload["created_at_utc"]
    if type(created_at_text) is not str:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval decision timestamp is invalid",
        )
    try:
        created_at = datetime.strptime(created_at_text, _UTC_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval decision timestamp is malformed",
        ) from exc
    if (
        _timestamp_text(created_at) != created_at_text
        or expected_envelope.created_at_utc != created_at
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "retrieval decision timestamp differs from its envelope",
        )

    candidates = payload["considered_candidates"]
    selected = payload["selected_content_identities"]
    if type(candidates) is not list or type(selected) is not list:
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "persisted retrieval candidate sets are malformed",
        )
    audits: list[tuple[str, bool, int | None]] = []
    candidate_fields = {
        "schema_version",
        "content_identity",
        "manifest_identity",
        "compatibility_decision_ref",
        "retrieval_gate_decision_ref",
        "gate_outcome",
        "failure_reason",
        "ranking_observation",
        "selected",
    }
    for candidate in candidates:
        if type(candidate) is not dict or set(candidate) != candidate_fields:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "persisted candidate audit shape is invalid",
            )
        if candidate["schema_version"] != (
            "synapse.stage4.gold.snapshot-candidate-audit/v1"
        ):
            raise _fail(
                RetrievalFailureCode.UNKNOWN_SCHEMA,
                "persisted candidate audit schema is unknown",
            )
        content_identity = _safe_id(
            candidate["content_identity"],
            "candidate content identity",
        )
        _safe_id(candidate["manifest_identity"], "candidate manifest identity")
        HashBoundRef.from_dict(candidate["compatibility_decision_ref"])
        HashBoundRef.from_dict(candidate["retrieval_gate_decision_ref"])
        try:
            outcome = GateDecisionKind(candidate["gate_outcome"])
        except (TypeError, ValueError) as exc:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "persisted candidate gate outcome is unknown",
            ) from exc
        failure_reason = candidate["failure_reason"]
        if failure_reason is not None:
            _safe_id(failure_reason, "candidate failure reason")
        if type(candidate["selected"]) is not bool:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "persisted candidate selected state is invalid",
            )
        observation = candidate["ranking_observation"]
        score = None
        if observation is not None:
            score = _validate_ranking_observation_transport(
                observation,
                expected_query_id=payload["query_id"],
            )
        if (
            outcome is GateDecisionKind.ADMIT
            and (score is None or failure_reason is not None)
        ) or (
            outcome is not GateDecisionKind.ADMIT
            and (score is not None or candidate["selected"] or failure_reason is None)
        ):
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "persisted candidate authority metadata is inconsistent",
            )
        audits.append((content_identity, candidate["selected"], score))
    identities = tuple(item[0] for item in audits)
    if identities != tuple(sorted(identities)) or len(set(identities)) != len(
        identities
    ):
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "persisted candidate audit is unordered or duplicated",
        )
    expected_selected = tuple(
        identity
        for identity, is_selected, _ in sorted(
            audits,
            key=lambda item: (
                -(item[2] if item[2] is not None else -1),
                item[0],
            ),
        )
        if is_selected
    )
    if (
        any(type(item) is not str for item in selected)
        or tuple(selected) != expected_selected
        or len(set(selected)) != len(selected)
    ):
        raise _fail(
            RetrievalFailureCode.RANKING_NONDETERMINISTIC,
            "persisted selected set differs from admitted ranking order",
        )
    return expected_envelope.record_id


@dataclass(frozen=True)
class _DurableRetrievalAuthorityResolver:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    admission_binding: RetrievalAdmissionBinding
    producer_component: str

    def resolve_retrieval_authority(
        self,
        *,
        retrieval_decision_ref: HashBoundRef,
        expected_envelope: CommonEnvelope,
    ) -> tuple[RecordId, HashBoundRef]:
        validate_retrieval_admission_binding(
            self.admission_binding,
            authority_binding=self.authority_binding,
        )
        evidence, artifact = (
            self.admission_binding.admission_store.read_committed_artifact(
                artifact_ref=retrieval_decision_ref,
                expected_record_kind=AdmissionCausalRecordKind.RETRIEVAL_DECISION,
            )
        )
        record_id = validate_snapshot_bound_retrieval_decision_artifact(
            artifact,
            expected_envelope=expected_envelope,
            expected_producer_component=self.producer_component,
        )
        if evidence.record_identity != record_id.value:
            raise _fail(
                RetrievalFailureCode.IDENTITY_MISMATCH,
                "retrieval history identity differs from its artifact",
            )
        return record_id, retrieval_decision_ref


def _create_durable_retrieval_authority_resolver(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    patch6_adapter: ConfiguredPatch6RetrievalAdapter,
    admission_binding: RetrievalAdmissionBinding,
) -> SealedRetrievalAuthorityResolver:
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    validate_retrieval_admission_binding(
        admission_binding,
        authority_binding=authority_binding,
    )
    evaluator = require_retriever_evaluator(
        require_patch6_retrieval_adapter(patch6_adapter)
    )
    implementation = _DurableRetrievalAuthorityResolver(
        authority_binding=authority_binding,
        admission_binding=admission_binding,
        producer_component=evaluator.retriever_actor.value,
    )
    return seal_retrieval_authority_resolver(
        implementation=implementation,
        profile_id=overlay.retrieval_authority_resolver_profile_id,
        component_identity=overlay.retrieval_authority_resolver_component_identity,
    )


_SNAPSHOT_RETRIEVER_SEAL = object()


@dataclass(frozen=True, init=False)
class ConfiguredSnapshotRetrieval:
    authority_binding: KnowledgeAdmissionAuthorityBinding
    patch6_adapter: ConfiguredPatch6RetrievalAdapter
    compatibility: ConfiguredSnapshotCompatibility
    admission_binding: RetrievalAdmissionBinding
    retrieval_authority_resolver: SealedRetrievalAuthorityResolver
    trusted_clock: Callable[[], datetime]
    _configuration_snapshot: tuple[object, ...]
    _trusted_seal: object

    def __new__(
        cls,
        *args: object,
        **kwargs: object,
    ) -> ConfiguredSnapshotRetrieval:
        raise TypeError("ConfiguredSnapshotRetrieval is factory-created")


def configure_snapshot_retrieval(
    *,
    authority_binding: KnowledgeAdmissionAuthorityBinding,
    patch6_adapter: ConfiguredPatch6RetrievalAdapter,
    compatibility: ConfiguredSnapshotCompatibility,
    admission_binding: RetrievalAdmissionBinding,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredSnapshotRetrieval:
    _, overlay = validate_knowledge_admission_authority_binding(
        authority_binding
    )
    retriever = require_patch6_retrieval_adapter(patch6_adapter)
    evaluator, _ = require_configured_snapshot_compatibility(compatibility)
    validate_retrieval_admission_binding(
        admission_binding,
        authority_binding=authority_binding,
    )
    retrieval_authority_resolver = _create_durable_retrieval_authority_resolver(
        authority_binding=authority_binding,
        patch6_adapter=patch6_adapter,
        admission_binding=admission_binding,
    )
    if (
        compatibility.authority_binding is not authority_binding
        or require_retriever_evaluator(retriever) is not evaluator
        or retrieval_authority_resolver.profile_id
        != overlay.retrieval_authority_resolver_profile_id
        or retrieval_authority_resolver.component_identity
        != overlay.retrieval_authority_resolver_component_identity
        or not callable(trusted_clock)
    ):
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot retrieval dependencies use another authority configuration",
        )
    result = object.__new__(ConfiguredSnapshotRetrieval)
    object.__setattr__(result, "authority_binding", authority_binding)
    object.__setattr__(result, "patch6_adapter", patch6_adapter)
    object.__setattr__(result, "compatibility", compatibility)
    object.__setattr__(result, "admission_binding", admission_binding)
    object.__setattr__(
        result,
        "retrieval_authority_resolver",
        retrieval_authority_resolver,
    )
    object.__setattr__(result, "trusted_clock", trusted_clock)
    object.__setattr__(
        result,
        "_configuration_snapshot",
        (
            authority_binding,
            patch6_adapter,
            compatibility,
            admission_binding,
            retrieval_authority_resolver,
            trusted_clock,
        ),
    )
    object.__setattr__(result, "_trusted_seal", _SNAPSHOT_RETRIEVER_SEAL)
    require_configured_snapshot_retrieval(result)
    return result


def require_configured_snapshot_retrieval(
    value: ConfiguredSnapshotRetrieval,
    *,
    expected: ConfiguredSnapshotRetrieval | None = None,
) -> ConfiguredRetriever:
    if (
        type(value) is not ConfiguredSnapshotRetrieval
        or getattr(value, "_trusted_seal", None) is not _SNAPSHOT_RETRIEVER_SEAL
    ):
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot retriever is not factory sealed",
        )
    if expected is not None and value is not expected:
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot retriever capability instance differs",
        )
    _, overlay = validate_knowledge_admission_authority_binding(
        value.authority_binding
    )
    retriever = require_patch6_retrieval_adapter(value.patch6_adapter)
    evaluator, _ = require_configured_snapshot_compatibility(
        value.compatibility
    )
    validate_retrieval_admission_binding(
        value.admission_binding,
        authority_binding=value.authority_binding,
    )
    validate_sealed_retrieval_authority_resolver(
        value.retrieval_authority_resolver
    )
    snapshot = getattr(value, "_configuration_snapshot", None)
    if (
        value.compatibility.authority_binding is not value.authority_binding
        or require_retriever_evaluator(retriever) is not evaluator
        or value.retrieval_authority_resolver.profile_id
        != overlay.retrieval_authority_resolver_profile_id
        or value.retrieval_authority_resolver.component_identity
        != overlay.retrieval_authority_resolver_component_identity
        or not callable(value.trusted_clock)
        or type(snapshot) is not tuple
        or len(snapshot) != 6
        or snapshot[0] is not value.authority_binding
        or snapshot[1] is not value.patch6_adapter
        or snapshot[2] is not value.compatibility
        or snapshot[3] is not value.admission_binding
        or snapshot[4] is not value.retrieval_authority_resolver
        or snapshot[5] is not value.trusted_clock
    ):
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot retriever capability changed",
        )
    return retriever


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalQuery:
    schema_version: str
    envelope: CommonEnvelope
    query_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    compatibility_context_ref: HashBoundRef
    requested_behavior_kinds: tuple[BehaviorKind, ...]
    required_binding_targets: tuple[RetrievalBindingTarget, ...]
    selected_set_limit: int
    consumer_context: GateConsumerContext
    authority_heads: GateAuthorityHeads
    _retriever: ConfiguredSnapshotRetrieval
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalQuery:
        raise TypeError("SnapshotBoundRetrievalQuery is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_query(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": snapshot_bound_retrieval_query_payload(
                repository_knowledge_snapshot_id=(
                    self.repository_knowledge_snapshot_id
                ),
                atomic_boundary_id=self.atomic_boundary_id,
                boundary_commit_sequence=self.boundary_commit_sequence,
                compatibility_context_ref=self.compatibility_context_ref,
                requested_behavior_kinds=self.requested_behavior_kinds,
                required_binding_targets=self.required_binding_targets,
                selected_set_limit=self.selected_set_limit,
                consumer_context=self.consumer_context,
                authority_heads=self.authority_heads,
            ),
        }


def snapshot_bound_retrieval_query_payload(
    *,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    compatibility_context_ref: HashBoundRef,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[RetrievalBindingTarget, ...],
    selected_set_limit: int,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
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
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "boundary commit sequence is invalid",
        )
    if (
        type(compatibility_context_ref) is not HashBoundRef
        or compatibility_context_ref.kind is not RefKind.ARTIFACT
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "compatibility context ref is invalid",
        )
    if (
        type(requested_behavior_kinds) is not tuple
        or not requested_behavior_kinds
        or any(type(item) is not BehaviorKind for item in requested_behavior_kinds)
        or requested_behavior_kinds
        != tuple(sorted(set(requested_behavior_kinds), key=lambda item: item.value))
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query behavior kinds are invalid",
        )
    if type(required_binding_targets) is not tuple:
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query binding targets are mutable",
        )
    for item in required_binding_targets:
        validate_retrieval_binding_target(item)
    if required_binding_targets != tuple(
        sorted(required_binding_targets, key=lambda item: _canonical(item.to_dict()))
    ):
        raise _fail(
            RetrievalFailureCode.MALFORMED_QUERY,
            "snapshot query binding targets are not canonical",
        )
    if type(selected_set_limit) is not int or not 1 <= selected_set_limit <= MAX_INDEX_ENTRIES_V1:
        raise _fail(
            RetrievalFailureCode.SELECTION_LIMIT_INVALID,
            "snapshot query selected-set limit is invalid",
        )
    consumer_context.__post_init__()
    authority_heads.to_dict()
    return {
        "schema_version": RETRIEVAL_QUERY_V2,
        "repository_knowledge_snapshot_id": (
            repository_knowledge_snapshot_id.to_dict()
        ),
        "atomic_boundary_id": atomic_boundary_id.to_dict(),
        "boundary_commit_sequence": boundary_commit_sequence,
        "compatibility_context_ref": compatibility_context_ref.to_dict(),
        "requested_behavior_kinds": [
            item.value for item in requested_behavior_kinds
        ],
        "required_binding_targets": [
            item.to_dict() for item in required_binding_targets
        ],
        "selected_set_limit": selected_set_limit,
        "consumer_context": consumer_context.to_dict(),
        "authority_heads": authority_heads.to_dict(),
    }


def create_retrieval_query(
    *,
    retriever: ConfiguredSnapshotRetrieval,
    envelope: CommonEnvelope,
    repository_knowledge_snapshot_id: RecordId,
    atomic_boundary_id: RecordId,
    boundary_commit_sequence: int,
    compatibility_context_ref: HashBoundRef,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[RetrievalBindingTarget, ...],
    selected_set_limit: int,
    consumer_context: GateConsumerContext,
    authority_heads: GateAuthorityHeads,
) -> SnapshotBoundRetrievalQuery:
    require_configured_snapshot_retrieval(retriever)
    result = object.__new__(SnapshotBoundRetrievalQuery)
    object.__setattr__(result, "schema_version", RETRIEVAL_QUERY_V2)
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
    object.__setattr__(
        result,
        "compatibility_context_ref",
        compatibility_context_ref,
    )
    object.__setattr__(
        result,
        "requested_behavior_kinds",
        requested_behavior_kinds,
    )
    object.__setattr__(
        result,
        "required_binding_targets",
        required_binding_targets,
    )
    object.__setattr__(result, "selected_set_limit", selected_set_limit)
    object.__setattr__(result, "consumer_context", consumer_context)
    object.__setattr__(result, "authority_heads", authority_heads)
    object.__setattr__(result, "_retriever", retriever)
    payload = snapshot_bound_retrieval_query_payload(
        repository_knowledge_snapshot_id=repository_knowledge_snapshot_id,
        atomic_boundary_id=atomic_boundary_id,
        boundary_commit_sequence=boundary_commit_sequence,
        compatibility_context_ref=compatibility_context_ref,
        requested_behavior_kinds=requested_behavior_kinds,
        required_binding_targets=required_binding_targets,
        selected_set_limit=selected_set_limit,
        consumer_context=consumer_context,
        authority_heads=authority_heads,
    )
    validate_common_envelope(envelope, canonical_payload_bytes=_canonical(payload))
    evaluator = require_retriever_evaluator(
        require_configured_snapshot_retrieval(retriever)
    )
    if (
        envelope.record_id.domain is not IdentityDomain.RETRIEVAL_QUERY_V2
        or envelope.producer_component != evaluator.retriever_actor.value
        or envelope.lineage_parent_ids
        != (
            LineageParentRef(
                repository_knowledge_snapshot_id,
                LineageEdgeKind.REFERENCES,
            ),
            LineageParentRef(
                atomic_boundary_id,
                LineageEdgeKind.REFERENCES,
            ),
        )
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "snapshot retrieval query envelope authority is invalid",
        )
    object.__setattr__(result, "query_id", envelope.record_id)
    object.__setattr__(
        result,
        "_trusted_seal",
        _SNAPSHOT_RETRIEVAL_SEAL,
    )
    validate_snapshot_bound_retrieval_query(result)
    return result


def validate_snapshot_bound_retrieval_query(
    value: SnapshotBoundRetrievalQuery,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalQuery
        or getattr(value, "_trusted_seal", None)
        is not _SNAPSHOT_RETRIEVAL_SEAL
        or value.schema_version != RETRIEVAL_QUERY_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval query is not factory sealed",
        )
    bound_retriever = getattr(value, "_retriever", None)
    if type(bound_retriever) is not ConfiguredSnapshotRetrieval:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval query capability is unavailable",
        )
    base_retriever = require_configured_snapshot_retrieval(bound_retriever)
    evaluator = require_retriever_evaluator(base_retriever)
    validate_gate_authority_heads(
        value.authority_heads,
        authority_binding=bound_retriever.authority_binding,
    )
    payload = snapshot_bound_retrieval_query_payload(
        repository_knowledge_snapshot_id=value.repository_knowledge_snapshot_id,
        atomic_boundary_id=value.atomic_boundary_id,
        boundary_commit_sequence=value.boundary_commit_sequence,
        compatibility_context_ref=value.compatibility_context_ref,
        requested_behavior_kinds=value.requested_behavior_kinds,
        required_binding_targets=value.required_binding_targets,
        selected_set_limit=value.selected_set_limit,
        consumer_context=value.consumer_context,
        authority_heads=value.authority_heads,
    )
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    if (
        value.query_id != value.envelope.record_id
        or value.query_id.domain is not IdentityDomain.RETRIEVAL_QUERY_V2
        or value.envelope.producer_component != evaluator.retriever_actor.value
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
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval query identity changed",
        )


@dataclass(frozen=True)
class ResolvedSnapshotCandidate:
    view_entry: SnapshotViewEntry
    index_entry: IndexEntry
    descriptor: CompatibilitySubjectDescriptor
    content_key: ContentKey
    manifest_id: RecordId
    subject_ref: HashBoundRef
    compatibility_context: SnapshotBoundCompatibilityContext
    historical_compatibility_context: CompatibilityContext
    source_evidence_refs: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        self.view_entry.__post_init__()
        if self.view_entry.status is not SnapshotObjectStatus.EXECUTABLE:
            raise _fail(
                RetrievalFailureCode.LOADING_FORBIDDEN,
                "resolved candidate is not in the executable view",
            )
        IndexEntry.from_dict(self.index_entry.to_dict())
        validate_compatibility_subject_descriptor(self.descriptor)
        self.content_key.to_dict()
        _record(
            self.manifest_id,
            IdentityDomain.BEHAVIOR_MANIFEST,
            "candidate manifest id",
        )
        if (
            self.view_entry.content_identity != self.content_key.value
            or self.view_entry.manifest_identity != self.manifest_id.value
            or self.index_entry.content_key != self.content_key.value
            or self.index_entry.manifest_id != self.manifest_id.value
            or self.index_entry.behavior_kind != self.view_entry.behavior_kind
        ):
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH,
                "frozen view, index entry, and trusted identities differ",
            )
        if type(self.subject_ref) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_MISSING,
                "resolved candidate subject ref is invalid",
            )
        if (
            type(self.source_evidence_refs) is not tuple
            or not self.source_evidence_refs
            or any(type(item) is not HashBoundRef for item in self.source_evidence_refs)
        ):
            raise _fail(
                RetrievalFailureCode.DESCRIPTOR_MISSING,
                "resolved candidate evidence refs are incomplete",
            )


@dataclass(frozen=True)
class SnapshotRankingObservation:
    schema_version: str
    query_id: RecordId
    compatibility_context_id: RecordId
    descriptor_id: RecordId
    score_input_ref: HashBoundRef
    semantic_score_micros: int
    scorer_component_id: str
    scorer_component_version: str
    scoring_profile: str
    observation_id: RecordId

    def __post_init__(self) -> None:
        if self.schema_version != RANKING_FEATURE_OBSERVATION_V1:
            raise _fail(
                RetrievalFailureCode.UNKNOWN_SCHEMA,
                "snapshot ranking observation schema is unknown",
            )
        _record(self.query_id, IdentityDomain.RETRIEVAL_QUERY_V2, "ranking query")
        _record(
            self.compatibility_context_id,
            IdentityDomain.COMPATIBILITY_CONTEXT_V2,
            "ranking compatibility context",
        )
        _record(
            self.descriptor_id,
            IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR,
            "ranking descriptor",
        )
        if type(self.score_input_ref) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.RANKING_INPUT_MALFORMED,
                "ranking score input ref is invalid",
            )
        HashBoundRef.from_dict(self.score_input_ref.to_dict())
        _score(self.semantic_score_micros)
        _safe_id(self.scorer_component_id, "ranking scorer component")
        _safe_id(self.scorer_component_version, "ranking scorer version")
        _safe_id(self.scoring_profile, "ranking scoring profile")
        payload = {
            "schema_version": self.schema_version,
            "query_id": self.query_id.to_dict(),
            "compatibility_context_id": self.compatibility_context_id.to_dict(),
            "descriptor_id": self.descriptor_id.to_dict(),
            "score_input_ref": self.score_input_ref.to_dict(),
            "semantic_score_micros": self.semantic_score_micros,
            "scorer_component_id": self.scorer_component_id,
            "scorer_component_version": self.scorer_component_version,
            "scoring_profile": self.scoring_profile,
        }
        _record(
            self.observation_id,
            IdentityDomain.RETRIEVAL_RANKING_FEATURE,
            "ranking observation",
        )
        validate_record_id(self.observation_id, canonical_bytes=_canonical(payload))

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "query_id": self.query_id.to_dict(),
            "compatibility_context_id": self.compatibility_context_id.to_dict(),
            "descriptor_id": self.descriptor_id.to_dict(),
            "score_input_ref": self.score_input_ref.to_dict(),
            "semantic_score_micros": self.semantic_score_micros,
            "scorer_component_id": self.scorer_component_id,
            "scorer_component_version": self.scorer_component_version,
            "scoring_profile": self.scoring_profile,
            "observation_id": self.observation_id.to_dict(),
        }


def observe_snapshot_ranking(
    adapter: ConfiguredPatch6RetrievalAdapter,
    *,
    query: SnapshotBoundRetrievalQuery,
    context: SnapshotBoundCompatibilityContext,
    historical_query: RetrievalQuery,
    historical_context: CompatibilityContext,
    descriptor: CompatibilitySubjectDescriptor,
) -> SnapshotRankingObservation:
    require_patch6_retrieval_adapter(adapter)
    validate_snapshot_bound_retrieval_query(query)
    validate_compatibility_context(
        historical_context,
        evaluator=require_patch6_compatibility_adapter(
            query._retriever.compatibility.patch6_adapter
        ),
    )
    validate_compatibility_subject_descriptor(descriptor)
    observation = adapter.observe_ranking_feature(
        query=historical_query,
        context=historical_context,
        descriptor=descriptor,
    )
    if historical_query.compatibility_context_id != historical_context.context_id:
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "snapshot ranking subordinate query/context differ",
        )
    payload = {
        "schema_version": RANKING_FEATURE_OBSERVATION_V1,
        "query_id": query.query_id.to_dict(),
        "compatibility_context_id": context.context_id.to_dict(),
        "descriptor_id": descriptor.descriptor_id.to_dict(),
        "score_input_ref": observation.score_input_ref.to_dict(),
        "semantic_score_micros": observation.semantic_score_micros,
        "scorer_component_id": observation.scorer_component_id,
        "scorer_component_version": observation.scorer_component_version,
        "scoring_profile": observation.scoring_profile,
    }
    result = SnapshotRankingObservation(
        RANKING_FEATURE_OBSERVATION_V1,
        query.query_id,
        context.context_id,
        descriptor.descriptor_id,
        observation.score_input_ref,
        observation.semantic_score_micros,
        observation.scorer_component_id,
        observation.scorer_component_version,
        observation.scoring_profile,
        compute_record_id(
            domain=IdentityDomain.RETRIEVAL_RANKING_FEATURE,
            canonical_bytes=_canonical(payload),
        ),
    )
    result.__post_init__()
    return result


@dataclass(frozen=True)
class SnapshotCandidateAudit:
    schema_version: str
    content_identity: str
    manifest_identity: str
    compatibility_decision_ref: HashBoundRef
    retrieval_gate_decision_ref: HashBoundRef
    gate_outcome: GateDecisionKind
    failure_reason: str | None
    ranking_observation: SnapshotRankingObservation | None
    selected: bool

    def __post_init__(self) -> None:
        if self.schema_version != "synapse.stage4.gold.snapshot-candidate-audit/v1":
            raise _fail(
                RetrievalFailureCode.UNKNOWN_SCHEMA,
                "snapshot candidate audit schema is unknown",
            )
        _safe_id(self.content_identity, "candidate content identity")
        _safe_id(self.manifest_identity, "candidate manifest identity")
        if type(self.compatibility_decision_ref) is not HashBoundRef or type(
            self.retrieval_gate_decision_ref
        ) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate authority refs are invalid",
            )
        HashBoundRef.from_dict(self.compatibility_decision_ref.to_dict())
        HashBoundRef.from_dict(self.retrieval_gate_decision_ref.to_dict())
        if type(self.gate_outcome) is not GateDecisionKind:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate gate outcome is invalid",
            )
        if self.failure_reason is not None:
            _safe_id(self.failure_reason, "candidate failure reason")
        if self.ranking_observation is not None:
            self.ranking_observation.__post_init__()
        if type(self.selected) is not bool:
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate selected state is invalid",
            )
        if (
            self.gate_outcome is not GateDecisionKind.ADMIT
            and (self.ranking_observation is not None or self.selected)
        ):
            raise _fail(
                RetrievalFailureCode.LOADING_FORBIDDEN,
                "non-admitted candidate acquired ranking or selection",
            )
        if (
            self.gate_outcome is GateDecisionKind.ADMIT
            and self.ranking_observation is None
        ) or (
            self.gate_outcome is GateDecisionKind.ADMIT
            and self.failure_reason is not None
        ) or (
            self.gate_outcome is not GateDecisionKind.ADMIT
            and self.failure_reason is None
        ):
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "candidate audit outcome metadata is inconsistent",
            )

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "schema_version": self.schema_version,
            "content_identity": self.content_identity,
            "manifest_identity": self.manifest_identity,
            "compatibility_decision_ref": self.compatibility_decision_ref.to_dict(),
            "retrieval_gate_decision_ref": self.retrieval_gate_decision_ref.to_dict(),
            "gate_outcome": self.gate_outcome.value,
            "failure_reason": self.failure_reason,
            "ranking_observation": (
                None
                if self.ranking_observation is None
                else self.ranking_observation.to_dict()
            ),
            "selected": self.selected,
        }


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalDecision:
    schema_version: str
    envelope: CommonEnvelope
    decision_id: RecordId
    query_id: RecordId
    repository_knowledge_snapshot_id: RecordId
    atomic_boundary_id: RecordId
    boundary_commit_sequence: int
    considered_candidates: tuple[SnapshotCandidateAudit, ...]
    selected_content_identities: tuple[str, ...]
    conflict_scan_ref: HashBoundRef
    created_at_utc: datetime
    _query: SnapshotBoundRetrievalQuery
    _retriever: ConfiguredSnapshotRetrieval
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalDecision:
        raise TypeError("SnapshotBoundRetrievalDecision is retriever-created")

    def payload_dict(self) -> dict[str, object]:
        return {
            "schema_version": RETRIEVAL_DECISION_V2,
            "query_id": self.query_id.to_dict(),
            "repository_knowledge_snapshot_id": (
                self.repository_knowledge_snapshot_id.to_dict()
            ),
            "atomic_boundary_id": self.atomic_boundary_id.to_dict(),
            "boundary_commit_sequence": self.boundary_commit_sequence,
            "considered_candidates": [
                item.to_dict() for item in self.considered_candidates
            ],
            "selected_content_identities": list(
                self.selected_content_identities
            ),
            "conflict_scan_ref": self.conflict_scan_ref.to_dict(),
            "created_at_utc": _timestamp_text(self.created_at_utc),
        }

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def validate_snapshot_bound_retrieval_decision(
    value: SnapshotBoundRetrievalDecision,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalDecision
        or getattr(value, "_trusted_seal", None)
        is not _SNAPSHOT_RETRIEVAL_SEAL
        or value.schema_version != RETRIEVAL_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval decision is not retriever sealed",
        )
    bound_query = getattr(value, "_query", None)
    bound_retriever = getattr(value, "_retriever", None)
    if (
        type(bound_query) is not SnapshotBoundRetrievalQuery
        or type(bound_retriever) is not ConfiguredSnapshotRetrieval
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval decision graph is unavailable",
        )
    require_configured_snapshot_retrieval(bound_retriever)
    validate_snapshot_bound_retrieval_query(bound_query)
    if bound_query._retriever is not bound_retriever:
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "snapshot retrieval decision uses another configured retriever",
        )
    _record(value.query_id, IdentityDomain.RETRIEVAL_QUERY_V2, "decision query")
    _record(
        value.repository_knowledge_snapshot_id,
        IdentityDomain.REPOSITORY_KNOWLEDGE_SNAPSHOT,
        "decision snapshot",
    )
    _record(
        value.atomic_boundary_id,
        IdentityDomain.ATOMIC_SNAPSHOT_BOUNDARY,
        "decision boundary",
    )
    if (
        type(value.boundary_commit_sequence) is not int
        or value.boundary_commit_sequence < 1
        or type(value.conflict_scan_ref) is not HashBoundRef
    ):
        raise _fail(
            RetrievalFailureCode.CONTEXT_SUBSTITUTION,
            "snapshot retrieval decision boundary or conflict ref is invalid",
        )
    HashBoundRef.from_dict(value.conflict_scan_ref.to_dict())
    if type(value.considered_candidates) is not tuple:
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "snapshot candidate audit is mutable",
        )
    for item in value.considered_candidates:
        item.__post_init__()
    identities = tuple(item.content_identity for item in value.considered_candidates)
    if identities != tuple(sorted(identities)) or len(set(identities)) != len(identities):
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "snapshot candidate audit is incomplete or unordered",
        )
    selected = tuple(
        item.content_identity
        for item in sorted(
            (
                item
                for item in value.considered_candidates
                if item.selected
            ),
            key=lambda item: (
                -item.ranking_observation.semantic_score_micros,
                item.content_identity,
            ),
        )
    )
    if (
        selected != value.selected_content_identities
        or len(selected) > bound_query.selected_set_limit
    ):
        raise _fail(
            RetrievalFailureCode.RANKING_NONDETERMINISTIC,
            "snapshot selected set differs from ranked admitted candidates",
        )
    payload = value.payload_dict()
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    created_at = _timestamp(
        value.created_at_utc,
        "snapshot retrieval decision timestamp",
    )
    evaluator = require_retriever_evaluator(
        require_configured_snapshot_retrieval(bound_retriever)
    )
    if (
        value.decision_id != value.envelope.record_id
        or value.decision_id.domain is not IdentityDomain.RETRIEVAL_DECISION_V2
        or value.query_id != bound_query.query_id
        or value.repository_knowledge_snapshot_id
        != bound_query.repository_knowledge_snapshot_id
        or value.atomic_boundary_id != bound_query.atomic_boundary_id
        or value.boundary_commit_sequence
        != bound_query.boundary_commit_sequence
        or value.envelope.created_at_utc != created_at
        or value.envelope.producer_component != evaluator.retriever_actor.value
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                bound_query.query_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        or value.envelope.run_id != bound_query.envelope.run_id
        or value.envelope.attempt_id != bound_query.envelope.attempt_id
        or value.envelope.repository_revision
        != bound_query.envelope.repository_revision
        or value.envelope.policy_version
        != bound_query.envelope.policy_version
        or value.envelope.environment_profile_id
        != bound_query.envelope.environment_profile_id
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot retrieval decision identity changed",
        )


def snapshot_bound_retrieval_decision_ref(
    value: SnapshotBoundRetrievalDecision,
) -> HashBoundRef:
    validate_snapshot_bound_retrieval_decision(value)
    return _retrieval_artifact_ref(value.envelope, value.payload_dict())


@dataclass(frozen=True, init=False)
class SnapshotBoundRetrievalLoadDecision:
    schema_version: str
    envelope: CommonEnvelope
    load_decision_id: RecordId
    retrieval_decision_ref: HashBoundRef
    selected_content_identity: str
    selected_manifest_identity: str
    loaded_subject_ref: HashBoundRef
    before_loading_revalidation_ref: HashBoundRef
    pre_load_library_root: str
    post_load_library_root: str
    created_at_utc: datetime
    _query: SnapshotBoundRetrievalQuery
    _retrieval_decision: SnapshotBoundRetrievalDecision
    _before_loading: SnapshotBoundCompatibilityRevalidation
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SnapshotBoundRetrievalLoadDecision:
        raise TypeError("SnapshotBoundRetrievalLoadDecision is retriever-created")

    def payload_dict(self) -> dict[str, object]:
        return {
            "schema_version": RETRIEVAL_LOAD_DECISION_V2,
            "retrieval_decision_ref": self.retrieval_decision_ref.to_dict(),
            "selected_content_identity": self.selected_content_identity,
            "selected_manifest_identity": self.selected_manifest_identity,
            "loaded_subject_ref": self.loaded_subject_ref.to_dict(),
            "before_loading_revalidation_ref": (
                self.before_loading_revalidation_ref.to_dict()
            ),
            "pre_load_library_root": self.pre_load_library_root,
            "post_load_library_root": self.post_load_library_root,
            "created_at_utc": _timestamp_text(self.created_at_utc),
        }

    def to_dict(self) -> dict[str, object]:
        validate_snapshot_bound_retrieval_load_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "payload": self.payload_dict(),
        }


def validate_snapshot_bound_retrieval_load_decision(
    value: SnapshotBoundRetrievalLoadDecision,
) -> None:
    if (
        type(value) is not SnapshotBoundRetrievalLoadDecision
        or getattr(value, "_trusted_seal", None)
        is not _SNAPSHOT_RETRIEVAL_SEAL
        or value.schema_version != RETRIEVAL_LOAD_DECISION_V2
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot load decision is not retriever sealed",
        )
    bound_query = getattr(value, "_query", None)
    bound_decision = getattr(value, "_retrieval_decision", None)
    bound_revalidation = getattr(value, "_before_loading", None)
    if (
        type(bound_query) is not SnapshotBoundRetrievalQuery
        or type(bound_decision) is not SnapshotBoundRetrievalDecision
        or type(bound_revalidation) is not SnapshotBoundCompatibilityRevalidation
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot load decision graph is unavailable",
        )
    validate_snapshot_bound_retrieval_query(bound_query)
    validate_snapshot_bound_retrieval_decision(bound_decision)
    validate_snapshot_bound_compatibility_revalidation(bound_revalidation)
    if (
        bound_decision._query is not bound_query
        or bound_revalidation.stage is not RevalidationStage.BEFORE_LOADING
        or bound_revalidation.outcome is not RevalidationOutcome.PASSED
    ):
        raise _fail(
            RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED,
            "snapshot load decision lacks exact loading revalidation",
        )
    if (
        value.pre_load_library_root != value.post_load_library_root
        or _SHA256_RE.fullmatch(value.pre_load_library_root) is None
    ):
        raise _fail(
            RetrievalFailureCode.SNAPSHOT_DRIFT,
            "library root drifted during verified load",
        )
    _safe_id(
        value.selected_content_identity,
        "selected content identity",
    )
    _safe_id(
        value.selected_manifest_identity,
        "selected manifest identity",
    )
    for ref, name in (
        (value.retrieval_decision_ref, "retrieval decision ref"),
        (value.loaded_subject_ref, "loaded subject ref"),
        (
            value.before_loading_revalidation_ref,
            "before-loading revalidation ref",
        ),
    ):
        if type(ref) is not HashBoundRef:
            raise _fail(
                RetrievalFailureCode.TRUSTED_RECORD_FORGED,
                f"{name} is invalid",
            )
        HashBoundRef.from_dict(ref.to_dict())
    matching_audits = tuple(
        item
        for item in bound_decision.considered_candidates
        if item.content_identity == value.selected_content_identity
    )
    if (
        len(matching_audits) != 1
        or not matching_audits[0].selected
        or matching_audits[0].manifest_identity
        != value.selected_manifest_identity
    ):
        raise _fail(
            RetrievalFailureCode.LOADED_IDENTITY_MISMATCH,
            "snapshot load identity differs from selected audit metadata",
        )
    payload = value.payload_dict()
    validate_common_envelope(value.envelope, canonical_payload_bytes=_canonical(payload))
    created_at = _timestamp(value.created_at_utc, "snapshot load timestamp")
    evaluator = require_retriever_evaluator(
        require_configured_snapshot_retrieval(bound_query._retriever)
    )
    if (
        value.load_decision_id != value.envelope.record_id
        or value.load_decision_id.domain
        is not IdentityDomain.RETRIEVAL_LOAD_DECISION_V2
        or value.retrieval_decision_ref
        != snapshot_bound_retrieval_decision_ref(bound_decision)
        or value.before_loading_revalidation_ref
        != snapshot_bound_compatibility_artifact_ref(bound_revalidation)
        or value.selected_content_identity
        not in bound_decision.selected_content_identities
        or value.envelope.created_at_utc != created_at
        or value.envelope.producer_component != evaluator.retriever_actor.value
        or value.envelope.lineage_parent_ids
        != (
            LineageParentRef(
                bound_decision.decision_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        )
        or value.envelope.run_id != bound_query.envelope.run_id
        or value.envelope.attempt_id != bound_query.envelope.attempt_id
        or value.envelope.repository_revision
        != bound_query.envelope.repository_revision
        or value.envelope.policy_version
        != bound_query.envelope.policy_version
        or value.envelope.environment_profile_id
        != bound_query.envelope.environment_profile_id
    ):
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "snapshot load decision identity changed",
        )


def snapshot_bound_retrieval_load_decision_ref(
    value: SnapshotBoundRetrievalLoadDecision,
) -> HashBoundRef:
    validate_snapshot_bound_retrieval_load_decision(value)
    return _retrieval_artifact_ref(value.envelope, value.payload_dict())


def create_snapshot_bound_retrieval_decision(
    *,
    query: SnapshotBoundRetrievalQuery,
    candidate_audits: tuple[SnapshotCandidateAudit, ...],
    conflict_ref: HashBoundRef,
    created_at_utc: datetime,
) -> SnapshotBoundRetrievalDecision:
    validate_snapshot_bound_retrieval_query(query)
    if (
        type(candidate_audits) is not tuple
        or any(
            type(item) is not SnapshotCandidateAudit
            for item in candidate_audits
        )
    ):
        raise _fail(
            RetrievalFailureCode.RANKING_INPUT_MALFORMED,
            "retrieval decision requires an immutable candidate audit",
        )
    for item in candidate_audits:
        item.__post_init__()
    if type(conflict_ref) is not HashBoundRef:
        raise _fail(
            RetrievalFailureCode.CONFLICT_UNRESOLVED,
            "retrieval conflict ref is invalid",
        )
    HashBoundRef.from_dict(conflict_ref.to_dict())
    selected = tuple(
        item.content_identity
        for item in sorted(
            (item for item in candidate_audits if item.selected),
            key=lambda item: (
                -item.ranking_observation.semantic_score_micros,
                item.content_identity,
            ),
        )
    )
    result = object.__new__(SnapshotBoundRetrievalDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_DECISION_V2)
    object.__setattr__(result, "query_id", query.query_id)
    object.__setattr__(
        result,
        "repository_knowledge_snapshot_id",
        query.repository_knowledge_snapshot_id,
    )
    object.__setattr__(result, "atomic_boundary_id", query.atomic_boundary_id)
    object.__setattr__(
        result,
        "boundary_commit_sequence",
        query.boundary_commit_sequence,
    )
    object.__setattr__(result, "considered_candidates", candidate_audits)
    object.__setattr__(result, "selected_content_identities", selected)
    object.__setattr__(result, "conflict_scan_ref", conflict_ref)
    object.__setattr__(
        result,
        "created_at_utc",
        _timestamp(created_at_utc, "snapshot retrieval decision timestamp"),
    )
    payload = result.payload_dict()
    evaluator = require_retriever_evaluator(
        require_configured_snapshot_retrieval(query._retriever)
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_DECISION_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=result.created_at_utc,
        producer_component=evaluator.retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                query.query_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "decision_id", envelope.record_id)
    object.__setattr__(result, "_query", query)
    object.__setattr__(result, "_retriever", query._retriever)
    object.__setattr__(
        result,
        "_trusted_seal",
        _SNAPSHOT_RETRIEVAL_SEAL,
    )
    validate_snapshot_bound_retrieval_decision(result)
    return result


def create_snapshot_bound_retrieval_load_decision(
    *,
    query: SnapshotBoundRetrievalQuery,
    retrieval_decision: SnapshotBoundRetrievalDecision,
    candidate: ResolvedSnapshotCandidate,
    loaded_subject_ref: HashBoundRef,
    before_loading: SnapshotBoundCompatibilityRevalidation,
    pre_load_library_root: str,
    post_load_library_root: str,
    created_at_utc: datetime,
) -> SnapshotBoundRetrievalLoadDecision:
    validate_snapshot_bound_retrieval_query(query)
    validate_snapshot_bound_retrieval_decision(retrieval_decision)
    if retrieval_decision._query is not query:
        raise _fail(
            RetrievalFailureCode.TRUSTED_RECORD_FORGED,
            "load decision uses another retrieval query",
        )
    if type(candidate) is not ResolvedSnapshotCandidate:
        raise _fail(
            RetrievalFailureCode.DESCRIPTOR_MISSING,
            "load decision candidate is invalid",
        )
    candidate.__post_init__()
    if type(loaded_subject_ref) is not HashBoundRef:
        raise _fail(
            RetrievalFailureCode.LOADED_IDENTITY_MISMATCH,
            "loaded subject ref is invalid",
        )
    HashBoundRef.from_dict(loaded_subject_ref.to_dict())
    validate_snapshot_bound_compatibility_revalidation(before_loading)
    if (
        before_loading.stage is not RevalidationStage.BEFORE_LOADING
        or before_loading.outcome is not RevalidationOutcome.PASSED
    ):
        raise _fail(
            RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED,
            "load decision requires passed before-loading revalidation",
        )
    result = object.__new__(SnapshotBoundRetrievalLoadDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_LOAD_DECISION_V2)
    object.__setattr__(
        result,
        "retrieval_decision_ref",
        snapshot_bound_retrieval_decision_ref(retrieval_decision),
    )
    object.__setattr__(
        result,
        "selected_content_identity",
        candidate.content_key.value,
    )
    object.__setattr__(
        result,
        "selected_manifest_identity",
        candidate.manifest_id.value,
    )
    object.__setattr__(result, "loaded_subject_ref", loaded_subject_ref)
    object.__setattr__(
        result,
        "before_loading_revalidation_ref",
        snapshot_bound_compatibility_artifact_ref(before_loading),
    )
    object.__setattr__(
        result,
        "pre_load_library_root",
        pre_load_library_root,
    )
    object.__setattr__(
        result,
        "post_load_library_root",
        post_load_library_root,
    )
    object.__setattr__(
        result,
        "created_at_utc",
        _timestamp(created_at_utc, "snapshot load timestamp"),
    )
    payload = result.payload_dict()
    evaluator = require_retriever_evaluator(
        require_configured_snapshot_retrieval(query._retriever)
    )
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V1,
        identity_domain=IdentityDomain.RETRIEVAL_LOAD_DECISION_V2,
        canonical_payload_bytes=_canonical(payload),
        run_id=query.envelope.run_id,
        attempt_id=query.envelope.attempt_id,
        created_at_utc=result.created_at_utc,
        producer_component=evaluator.retriever_actor.value,
        repository_revision=query.envelope.repository_revision,
        policy_version=query.envelope.policy_version,
        environment_profile_id=query.envelope.environment_profile_id,
        lineage_parent_ids=(
            LineageParentRef(
                retrieval_decision.decision_id,
                LineageEdgeKind.DERIVED_FROM,
            ),
        ),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "load_decision_id", envelope.record_id)
    object.__setattr__(result, "_query", query)
    object.__setattr__(
        result,
        "_retrieval_decision",
        retrieval_decision,
    )
    object.__setattr__(result, "_before_loading", before_loading)
    object.__setattr__(
        result,
        "_trusted_seal",
        _SNAPSHOT_RETRIEVAL_SEAL,
    )
    validate_snapshot_bound_retrieval_load_decision(result)
    return result


class FreshCompatibilityEvidenceProvider(Protocol):
    def __call__(
        self,
        candidate: ResolvedSnapshotCandidate,
        stage: RevalidationStage,
    ) -> SnapshotBoundCompatibilityEvidence:
        ...


@dataclass(frozen=True)
class SnapshotRetrievalResult:
    decision: SnapshotBoundRetrievalDecision
    decision_commit: AdmissionCommitEvidence
    load_decisions: tuple[SnapshotBoundRetrievalLoadDecision, ...]
    consumption_decision_refs: tuple[HashBoundRef, ...]
    admitted_handles: tuple[AdmittedKnowledgeHandle, ...]

    def __post_init__(self) -> None:
        validate_snapshot_bound_retrieval_decision(self.decision)
        validate_admission_commit_evidence(self.decision_commit)
        if (
            self.decision_commit.record_kind
            is not AdmissionCausalRecordKind.RETRIEVAL_DECISION
            or self.decision_commit.record_identity != self.decision.decision_id.value
            or self.decision_commit.artifact_ref
            != snapshot_bound_retrieval_decision_ref(self.decision)
        ):
            raise _fail(
                RetrievalFailureCode.TRUSTED_RECORD_FORGED,
                "snapshot retrieval decision is not durably bound",
            )
        if (
            type(self.load_decisions) is not tuple
            or type(self.consumption_decision_refs) is not tuple
            or type(self.admitted_handles) is not tuple
        ):
            raise _fail(
                RetrievalFailureCode.TRUSTED_RECORD_FORGED,
                "snapshot retrieval result collections are mutable",
            )
        for item in self.load_decisions:
            validate_snapshot_bound_retrieval_load_decision(item)
        for item in self.consumption_decision_refs:
            if type(item) is not HashBoundRef or item.kind is not RefKind.ARTIFACT:
                raise _fail(
                    RetrievalFailureCode.TRUSTED_RECORD_FORGED,
                    "snapshot consumption decision ref is invalid",
                )
        if (
            len(self.load_decisions) != len(self.consumption_decision_refs)
            or len(self.admitted_handles) > len(self.consumption_decision_refs)
        ):
            raise _fail(
                RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
                "snapshot load, consumption, and handle cardinalities differ",
            )
        for item in self.admitted_handles:
            validate_admitted_knowledge_handle(item)
        if any(
            item.loaded_subject_ref
            not in {
                load.loaded_subject_ref for load in self.load_decisions
            }
            for item in self.admitted_handles
        ):
            raise _fail(
                RetrievalFailureCode.LOADED_IDENTITY_MISMATCH,
                "admitted handle does not belong to a verified load",
            )
