"""Stage 4 candidate discovery, deterministic ranking, and verified loading.

Index and semantic-score data remain non-authoritative. This module stops
before replay, worker dispatch, or host execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Callable

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
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    IdentityDomain,
    ProposalId,
    RecordId,
    Stage4AuthorityHandle,
    compute_record_id,
    require_stage4_authority_handle,
    validate_record_id,
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

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_SEAL = object()
_PROVIDER_SEAL = object()
_RETRIEVER_SEAL = object()
_ENUMERATION_SEAL = object()


def _ref_key(value: HashBoundRef) -> str:
    """The identity a candidate presents to a gate: kind, id and content digest."""

    return f"{value.kind.value}\x00{value.ref_id}\x00{value.sha256}"


def candidate_subject_ref(descriptor: object) -> HashBoundRef:
    """The hash-bound reference by which a candidate is known to the §22 gates.

    Built from the descriptor's own content key and blob digest, so the thing
    the gate decides about is the exact object the loader would read — not a
    name that could later resolve elsewhere. A library reference carries a
    namespace and a digest; a gate reference has to be a ``HashBoundRef``, and
    this is the one conversion between them.
    """

    validate_compatibility_subject_descriptor(descriptor)
    identity = _canonical(
        {
            "content_key": descriptor.content_key.value,
            "manifest_id": descriptor.manifest_id.value,
            "blob_digest_sha256": descriptor.blob_ref.digest_sha256,
            "manifest_digest_sha256": descriptor.manifest_ref.digest_sha256,
        }
    )
    # ref_id is the blob digest rather than the content key: a content key
    # carries a schema prefix with a "/" in it, which a reference id may not
    # contain. The digest names the same object and needs no escaping. The
    # sha256 field binds all four identity fields together, so two candidates
    # sharing a blob but differing in manifest remain distinct to the gate.
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=descriptor.blob_ref.digest_sha256,
        schema_id=descriptor.schema_version,
        sha256=hashlib.sha256(identity).hexdigest(),
        byte_length=len(identity),
        media_type="application/json",
    )


class RetrievalFailureCode(str, Enum):
    MALFORMED_QUERY = "MALFORMED_QUERY"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    UNKNOWN_POLICY = "UNKNOWN_POLICY"
    UNKNOWN_RANKING_PROFILE = "UNKNOWN_RANKING_PROFILE"
    WRONG_CONFIGURED_RETRIEVER = "WRONG_CONFIGURED_RETRIEVER"
    WRONG_EVALUATOR_CAPABILITY = "WRONG_EVALUATOR_CAPABILITY"
    CONTEXT_SUBSTITUTION = "CONTEXT_SUBSTITUTION"
    CANDIDATE_SET_INCOMPLETE = "CANDIDATE_SET_INCOMPLETE"
    HIDDEN_TRUNCATION = "HIDDEN_TRUNCATION"
    DESCRIPTOR_MISSING = "DESCRIPTOR_MISSING"
    DESCRIPTOR_INDEX_MISMATCH = "DESCRIPTOR_INDEX_MISMATCH"
    COMPATIBILITY_MISSING = "COMPATIBILITY_MISSING"
    COMPATIBILITY_REJECTED = "COMPATIBILITY_REJECTED"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    CONFLICT_SCAN_INCOMPLETE = "CONFLICT_SCAN_INCOMPLETE"
    RANKING_INPUT_MISSING = "RANKING_INPUT_MISSING"
    RANKING_INPUT_MALFORMED = "RANKING_INPUT_MALFORMED"
    RANKING_INPUT_INCONSISTENT = "RANKING_INPUT_INCONSISTENT"
    RANKING_NONDETERMINISTIC = "RANKING_NONDETERMINISTIC"
    SELECTION_LIMIT_INVALID = "SELECTION_LIMIT_INVALID"
    SNAPSHOT_DRIFT = "SNAPSHOT_DRIFT"
    LOADING_FORBIDDEN = "LOADING_FORBIDDEN"
    LOADED_IDENTITY_MISMATCH = "LOADED_IDENTITY_MISMATCH"
    CONSUMPTION_REVALIDATION_FAILED = "CONSUMPTION_REVALIDATION_FAILED"
    TRUSTED_RECORD_FORGED = "TRUSTED_RECORD_FORGED"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"


class RetrievalViolation(ValueError):
    def __init__(self, failure_code: RetrievalFailureCode, detail: str) -> None:
        if type(failure_code) is not RetrievalFailureCode:
            raise TypeError("failure_code must be RetrievalFailureCode")
        if type(detail) is not str or not detail or len(detail) > 512:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: RetrievalFailureCode, detail: str) -> RetrievalViolation:
    return RetrievalViolation(code, detail)


class CandidateDisposition(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    SELECTED = "SELECTED"
    REJECTED = "REJECTED"
    DESCRIPTOR_UNAVAILABLE = "DESCRIPTOR_UNAVAILABLE"
    POISONED_INDEX = "POISONED_INDEX"


class RetrievalOutcome(str, Enum):
    SELECTED = "SELECTED"
    NO_CANDIDATES = "NO_CANDIDATES"
    CONFLICT_BLOCKED = "CONFLICT_BLOCKED"


class LoadOutcome(str, Enum):
    VERIFIED_LOADED = "VERIFIED_LOADED"
    REVALIDATION_BLOCKED = "REVALIDATION_BLOCKED"


def _canonical(value: object) -> bytes:
    try:
        return canonicalize_stage4_payload(
            value,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "canonical retrieval payload is invalid") from exc


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, f"{name} is invalid")
    return value


def _version(value: object, name: str) -> str:
    text = _safe_id(value, name)
    if "/v" not in text and not re.search(r"\d", text):
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, f"{name} is not versioned")
    return text


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, f"{name} must be timezone-aware UTC")
    return value


def _timestamp_text(value: datetime) -> str:
    return _timestamp(value, "timestamp").astimezone(timezone.utc).strftime(_UTC_FORMAT)


def _actor(value: object, name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, f"{name} must be exact ActorIdentity")
    return ActorIdentity.from_dict(value.to_dict())


def _record(value: object, domain: IdentityDomain, name: str) -> RecordId:
    if type(value) is not RecordId or value.domain is not domain:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, f"{name} identity is invalid")
    value.to_dict()
    return value


def _strings(value: object, name: str, *, nonempty: bool) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, f"{name} must be exact tuple")
    result = tuple(_safe_id(item, f"{name} entry") for item in value)
    if nonempty and not result:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MISSING, f"{name} must not be empty")
    if len(set(result)) != len(result) or result != tuple(sorted(result)):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, f"{name} must be sorted and duplicate-free")
    return result


def _score(value: object) -> int:
    if type(value) is not int or not (0 <= value <= 1_000_000):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "semantic score must be exact integer in 0..1_000_000")
    return value


def _same_snapshot(library: BehaviorLibrary, trusted: LibrarySnapshot) -> LibrarySnapshot:
    try:
        verification = library.current_snapshot(trusted_prior=trusted)
    except LibraryViolation as exc:
        raise _fail(RetrievalFailureCode.SNAPSHOT_DRIFT, "library snapshot verification failed") from exc
    validate_snapshot_verification(verification)
    if verification.status is not SnapshotVerificationStatus.VERIFIED_SAME:
        raise _fail(RetrievalFailureCode.SNAPSHOT_DRIFT, "library snapshot changed during retrieval")
    return verification.snapshot


def _query_payload(value: RetrievalQuery) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "compatibility_context_id": value.compatibility_context_id.to_dict(),
        "requested_behavior_kinds": [item.value for item in value.requested_behavior_kinds],
        "required_binding_targets": [item.to_dict() for item in value.required_binding_targets],
        "selected_set_limit": value.selected_set_limit,
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "retrieval_policy": value.retrieval_policy,
    }


@dataclass(frozen=True, init=False)
class RetrievalBindingTarget:
    schema_version: str
    binding_kind: BindingKind
    path: str
    module: str | None
    qualname: str | None
    symbol_kind: str | None
    document_id: str | None
    document_revision: str | None
    section_id: str | None
    requirement_ids: tuple[str, ...]
    binding_ref: HashBoundRef
    _binding: object
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalBindingTarget:
        raise TypeError("RetrievalBindingTarget is created only from an exact binding")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_binding_target(self)
        return {
            "schema_version": self.schema_version,
            "binding_kind": self.binding_kind.value,
            "path": self.path,
            "module": self.module,
            "qualname": self.qualname,
            "symbol_kind": self.symbol_kind,
            "document_id": self.document_id,
            "document_revision": self.document_revision,
            "section_id": self.section_id,
            "requirement_ids": list(self.requirement_ids),
            "binding_ref": self.binding_ref.to_dict(),
        }


def binding_to_retrieval_target(binding: object) -> RetrievalBindingTarget:
    if type(binding) not in (PythonBinding, DocumentBinding, RequirementBinding):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "retrieval target requires an exact binding object")
    ref = binding_to_ref(binding)
    result = object.__new__(RetrievalBindingTarget)
    object.__setattr__(result, "schema_version", RETRIEVAL_BINDING_TARGET_V1)
    object.__setattr__(result, "binding_kind", binding.binding_kind)
    object.__setattr__(result, "path", binding.path)
    object.__setattr__(result, "module", binding.module if type(binding) is PythonBinding else None)
    object.__setattr__(result, "qualname", binding.qualname if type(binding) is PythonBinding else None)
    object.__setattr__(result, "symbol_kind", binding.symbol_kind.value if type(binding) is PythonBinding else None)
    object.__setattr__(result, "document_id", binding.document_id if type(binding) in (DocumentBinding, RequirementBinding) else None)
    object.__setattr__(result, "document_revision", binding.document_revision if type(binding) in (DocumentBinding, RequirementBinding) else None)
    object.__setattr__(result, "section_id", binding.section_id if type(binding) in (DocumentBinding, RequirementBinding) else None)
    object.__setattr__(result, "requirement_ids", binding.requirement_ids if type(binding) is RequirementBinding else ())
    object.__setattr__(result, "binding_ref", ref)
    object.__setattr__(result, "_binding", binding)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    validate_retrieval_binding_target(result)
    return result


def validate_retrieval_binding_target(value: RetrievalBindingTarget) -> None:
    if type(value) is not RetrievalBindingTarget or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval binding target is not factory sealed")
    if value.schema_version != RETRIEVAL_BINDING_TARGET_V1 or type(value.binding_kind) is not BindingKind:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "retrieval binding target schema/kind is invalid")
    binding = value._binding
    if type(binding) not in (PythonBinding, DocumentBinding, RequirementBinding):
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval target lost its exact binding")
    expected_payload = {
        "schema_version": RETRIEVAL_BINDING_TARGET_V1,
        "binding_kind": binding.binding_kind,
        "path": binding.path,
        "module": binding.module if type(binding) is PythonBinding else None,
        "qualname": binding.qualname if type(binding) is PythonBinding else None,
        "symbol_kind": binding.symbol_kind.value if type(binding) is PythonBinding else None,
        "document_id": binding.document_id if type(binding) in (DocumentBinding, RequirementBinding) else None,
        "document_revision": binding.document_revision if type(binding) in (DocumentBinding, RequirementBinding) else None,
        "section_id": binding.section_id if type(binding) in (DocumentBinding, RequirementBinding) else None,
        "requirement_ids": binding.requirement_ids if type(binding) is RequirementBinding else (),
        "binding_ref": binding_to_ref(binding),
    }
    observed_payload = {name: getattr(value, name) for name in expected_payload}
    if observed_payload != expected_payload:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval target differs from exact binding semantics")


@dataclass(frozen=True, init=False)
class RetrievalQuery:
    schema_version: str
    compatibility_context_id: RecordId
    requested_behavior_kinds: tuple[BehaviorKind, ...]
    required_binding_targets: tuple[RetrievalBindingTarget, ...]
    selected_set_limit: int
    library_snapshot_sha256: str
    lifecycle_snapshot_id: RecordId
    retrieval_policy: str
    query_id: RecordId
    _retriever: ConfiguredRetriever
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalQuery:
        raise TypeError("RetrievalQuery is factory-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_query(self, retriever=self._retriever)
        return {**_query_payload(self), "query_id": self.query_id.to_dict()}


def _ranking_payload(value: RankingFeatureObservation) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "retrieval_query_id": value.retrieval_query_id.to_dict(),
        "compatibility_context_id": value.compatibility_context_id.to_dict(),
        "subject_descriptor_id": value.subject_descriptor_id.to_dict(),
        "scorer_component_id": value.scorer_component_id,
        "scorer_component_version": value.scorer_component_version,
        "scoring_profile": value.scoring_profile,
        "score_input_ref": value.score_input_ref.to_dict(),
        "semantic_score_micros": value.semantic_score_micros,
    }


@dataclass(frozen=True, init=False)
class RankingFeatureObservation:
    schema_version: str
    retrieval_query_id: RecordId
    compatibility_context_id: RecordId
    subject_descriptor_id: RecordId
    scorer_component_id: str
    scorer_component_version: str
    scoring_profile: str
    score_input_ref: HashBoundRef
    semantic_score_micros: int
    observation_id: RecordId
    _provider: ConfiguredRankingFeatureProvider
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RankingFeatureObservation:
        raise TypeError("RankingFeatureObservation is provider-created")

    def to_dict(self) -> dict[str, object]:
        validate_ranking_feature_observation(self, provider=self._provider)
        return {**_ranking_payload(self), "observation_id": self.observation_id.to_dict()}


class ConfiguredRankingFeatureProvider:
    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_configuration_frozen", False):
            raise AttributeError("configured ranking provider is write-once")
        object.__setattr__(self, name, value)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _PROVIDER_SEAL or kwargs or len(args) != 6:
            raise TypeError("ConfiguredRankingFeatureProvider is factory-created")
        (
            self._component_id,
            self._component_version,
            self._scoring_profile,
            self._scorer,
            self._input_resolver,
            self._actor_identity,
        ) = args
        self._instance_token = object()
        self._trusted_seal = _PROVIDER_SEAL
        self._observed_scores: dict[tuple[object, ...], int] = {}
        self._configuration_snapshot = (
            self._component_id,
            self._component_version,
            self._scoring_profile,
            self._scorer,
            self._input_resolver,
            self._actor_identity,
            self._observed_scores,
        )
        self._configuration_frozen = True

    @property
    def actor_identity(self) -> ActorIdentity:
        require_configured_ranking_feature_provider(self)
        return self._actor_identity

    def observe(
        self,
        *,
        query: RetrievalQuery,
        context: CompatibilityContext,
        descriptor: CompatibilitySubjectDescriptor,
    ) -> RankingFeatureObservation:
        require_configured_ranking_feature_provider(self)
        validate_retrieval_query(query, retriever=query._retriever)
        validate_compatibility_context(context, evaluator=query._retriever._evaluator)
        validate_compatibility_subject_descriptor(descriptor)
        if query.compatibility_context_id != context.context_id:
            raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "ranking query/context differ")
        score_input = self._input_resolver(query.query_id, descriptor.descriptor_id)
        if type(score_input) is not HashBoundRef:
            raise _fail(RetrievalFailureCode.RANKING_INPUT_MISSING, "score input resolver returned no exact ref")
        score_ref = HashBoundRef.from_dict(score_input.to_dict())
        score = _score(self._scorer(query.query_id, descriptor.descriptor_id, score_ref))
        key = (
            query.query_id.value,
            context.context_id.value,
            descriptor.descriptor_id.value,
            self._component_id,
            self._component_version,
            self._scoring_profile,
            score_ref.kind.value,
            score_ref.ref_id,
            score_ref.sha256,
        )
        previous = self._observed_scores.get(key)
        if previous is not None and previous != score:
            raise _fail(RetrievalFailureCode.RANKING_INPUT_INCONSISTENT, "configured scorer produced conflicting identity-bound observations")
        self._observed_scores[key] = score
        result = object.__new__(RankingFeatureObservation)
        object.__setattr__(result, "schema_version", RANKING_FEATURE_OBSERVATION_V1)
        object.__setattr__(result, "retrieval_query_id", query.query_id)
        object.__setattr__(result, "compatibility_context_id", context.context_id)
        object.__setattr__(result, "subject_descriptor_id", descriptor.descriptor_id)
        object.__setattr__(result, "scorer_component_id", self._component_id)
        object.__setattr__(result, "scorer_component_version", self._component_version)
        object.__setattr__(result, "scoring_profile", self._scoring_profile)
        object.__setattr__(result, "score_input_ref", score_ref)
        object.__setattr__(result, "semantic_score_micros", score)
        object.__setattr__(result, "_provider", self)
        object.__setattr__(result, "_trusted_seal", _SEAL)
        payload = _canonical(_ranking_payload(result))
        object.__setattr__(result, "observation_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_RANKING_FEATURE, canonical_bytes=payload))
        validate_ranking_feature_observation(result, provider=self)
        return result


def configure_ranking_feature_provider(
    *,
    component_id: str,
    component_version: str,
    scoring_profile: str,
    scorer: Callable[[RecordId, RecordId, HashBoundRef], int],
    input_ref_resolver: Callable[[RecordId, RecordId], HashBoundRef],
    actor_identity: ActorIdentity,
) -> ConfiguredRankingFeatureProvider:
    if scoring_profile != RANKING_PROFILE_V1 or type(scoring_profile) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_RANKING_PROFILE, "ranking profile is unknown")
    if not callable(scorer) or not callable(input_ref_resolver):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "ranking provider callables are invalid")
    result = ConfiguredRankingFeatureProvider(
        _safe_id(component_id, "score component"),
        _version(component_version, "score component version"),
        scoring_profile,
        scorer,
        input_ref_resolver,
        _actor(actor_identity, "score provider actor"),
        _seal=_PROVIDER_SEAL,
    )
    require_configured_ranking_feature_provider(result)
    return result


def require_configured_ranking_feature_provider(
    value: ConfiguredRankingFeatureProvider,
    *,
    expected: ConfiguredRankingFeatureProvider | None = None,
) -> None:
    if type(value) is not ConfiguredRankingFeatureProvider or getattr(value, "_trusted_seal", None) is not _PROVIDER_SEAL or type(getattr(value, "_instance_token", None)) is not object:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "ranking provider is not configured")
    if expected is not None and value is not expected:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "ranking provider object differs")
    snapshot = getattr(value, "_configuration_snapshot", None)
    current = (
        value._component_id,
        value._component_version,
        value._scoring_profile,
        value._scorer,
        value._input_resolver,
        value._actor_identity,
        value._observed_scores,
    )
    if type(snapshot) is not tuple or len(snapshot) != len(current):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "ranking provider snapshot is unavailable")
    scalar_indexes = {0, 1, 2}
    if any(
        current[index] != snapshot[index]
        if index in scalar_indexes
        else current[index] is not snapshot[index]
        for index in range(len(current))
    ):
        raise _fail(RetrievalFailureCode.RANKING_INPUT_MALFORMED, "configured ranking provider state was replaced")
    _safe_id(value._component_id, "score component")
    _version(value._component_version, "score component version")
    if value._scoring_profile != RANKING_PROFILE_V1 or not callable(value._scorer) or not callable(value._input_resolver):
        raise _fail(RetrievalFailureCode.UNKNOWN_RANKING_PROFILE, "configured ranking provider changed")
    _actor(value._actor_identity, "score provider actor")


def validate_ranking_feature_observation(
    value: RankingFeatureObservation,
    *,
    provider: ConfiguredRankingFeatureProvider,
) -> None:
    require_configured_ranking_feature_provider(provider)
    if type(value) is not RankingFeatureObservation or getattr(value, "_trusted_seal", None) is not _SEAL or value._provider is not provider:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "ranking observation is not provider sealed")
    if value.schema_version != RANKING_FEATURE_OBSERVATION_V1 or type(value.schema_version) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "ranking observation schema is unknown")
    _record(value.retrieval_query_id, IdentityDomain.RETRIEVAL_QUERY, "ranking query id")
    _record(value.compatibility_context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "ranking context id")
    _record(value.subject_descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "ranking descriptor id")
    if value.scorer_component_id != provider._component_id or value.scorer_component_version != provider._component_version or value.scoring_profile != provider._scoring_profile:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_INCONSISTENT, "ranking provider identity differs")
    HashBoundRef.from_dict(value.score_input_ref.to_dict())
    _score(value.semantic_score_micros)
    payload = _canonical(_ranking_payload(value))
    _record(value.observation_id, IdentityDomain.RETRIEVAL_RANKING_FEATURE, "ranking observation id")
    try:
        validate_record_id(value.observation_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.RANKING_INPUT_INCONSISTENT, "ranking observation identity mismatch") from exc


class ConfiguredRetriever:
    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_configuration_frozen", False):
            raise AttributeError("configured retriever is write-once")
        object.__setattr__(self, name, value)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _RETRIEVER_SEAL or kwargs or len(args) != 9:
            raise TypeError("ConfiguredRetriever is factory-created")
        (
            self._authority_handle,
            self._evaluator,
            self._declaration,
            self._retrieval_policy,
            self._trusted_clock,
            self._descriptor_resolver,
            self._conflict_proposal_resolver,
            self._ranking_provider,
            self._library,
        ) = args
        self._instance_token = object()
        self._trusted_seal = _RETRIEVER_SEAL
        self._configuration_snapshot = (
            self._authority_handle,
            self._evaluator,
            self._declaration,
            self._retrieval_policy,
            self._trusted_clock,
            self._descriptor_resolver,
            self._conflict_proposal_resolver,
            self._ranking_provider,
            self._library,
        )
        self._configuration_frozen = True


def configure_retriever(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredCompatibilityEvaluator,
    evaluator_declaration: object,
    retrieval_policy: str,
    trusted_clock: Callable[[], datetime],
    descriptor_resolver: Callable[[IndexEntry], CompatibilitySubjectDescriptor],
    conflict_proposal_resolver: Callable[[CompatibilityContext, tuple[CompatibilityDecision, ...], tuple[CompatibilitySubjectDescriptor, ...]], tuple[ConflictEvidenceProposal, ...]],
    ranking_provider: ConfiguredRankingFeatureProvider,
    library: BehaviorLibrary,
) -> ConfiguredRetriever:
    require_stage4_authority_handle(authority_handle)
    require_configured_compatibility_evaluator(evaluator)
    if authority_handle is not evaluator.authority_handle:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "retriever authority handle differs")
    if evaluator_declaration is not evaluator.declaration:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "retriever declaration is not exact evaluator declaration")
    if retrieval_policy != RETRIEVAL_POLICY_V1 or type(retrieval_policy) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_POLICY, "retrieval policy is unknown")
    if not callable(trusted_clock) or not callable(descriptor_resolver) or not callable(conflict_proposal_resolver):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "retriever callables are invalid")
    require_configured_ranking_feature_provider(ranking_provider)
    if ranking_provider.actor_identity != evaluator.score_provider_actor:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "score provider actor differs from evaluator configuration")
    if library is not evaluator.library or type(library) is not BehaviorLibrary:
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever library differs from evaluator library")
    result = ConfiguredRetriever(
        authority_handle,
        evaluator,
        evaluator_declaration,
        retrieval_policy,
        trusted_clock,
        descriptor_resolver,
        conflict_proposal_resolver,
        ranking_provider,
        library,
        _seal=_RETRIEVER_SEAL,
    )
    require_configured_retriever(result)
    return result


def require_configured_retriever(value: ConfiguredRetriever, *, expected: ConfiguredRetriever | None = None) -> None:
    if type(value) is not ConfiguredRetriever or getattr(value, "_trusted_seal", None) is not _RETRIEVER_SEAL or type(getattr(value, "_instance_token", None)) is not object:
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever is not configured")
    if expected is not None and value is not expected:
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever capability object differs")
    snapshot = getattr(value, "_configuration_snapshot", None)
    current = (
        value._authority_handle,
        value._evaluator,
        value._declaration,
        value._retrieval_policy,
        value._trusted_clock,
        value._descriptor_resolver,
        value._conflict_proposal_resolver,
        value._ranking_provider,
        value._library,
    )
    if type(snapshot) is not tuple or len(snapshot) != len(current):
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever configuration snapshot is unavailable")
    if any(
        current[index] != snapshot[index]
        if index == 3
        else current[index] is not snapshot[index]
        for index in range(len(current))
    ):
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "configured retriever state was replaced")
    require_stage4_authority_handle(value._authority_handle)
    require_configured_compatibility_evaluator(value._evaluator)
    if value._declaration is not value._evaluator.declaration or value._library is not value._evaluator.library:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "retriever configuration changed")
    if value._retrieval_policy != RETRIEVAL_POLICY_V1 or not callable(value._trusted_clock) or not callable(value._descriptor_resolver) or not callable(value._conflict_proposal_resolver):
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever policy or callables changed")
    require_configured_ranking_feature_provider(value._ranking_provider)
    if value._ranking_provider.actor_identity != value._evaluator.score_provider_actor:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "score provider actor differs from evaluator configuration")


def create_retrieval_query(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[object, ...],
    selected_set_limit: int,
) -> RetrievalQuery:
    require_configured_retriever(retriever)
    validate_compatibility_context(context, evaluator=retriever._evaluator)
    if context._evaluator is not retriever._evaluator:
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "query context belongs to another evaluator")
    if type(requested_behavior_kinds) is not tuple or any(type(item) is not BehaviorKind for item in requested_behavior_kinds):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "requested behavior kinds are invalid")
    kinds = tuple(sorted(requested_behavior_kinds, key=lambda item: item.value))
    if not kinds or len(set(kinds)) != len(kinds) or not set(kinds) <= set(context.allowed_behavior_kinds):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "requested behavior kinds broaden context")
    if type(required_binding_targets) is not tuple:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "required binding targets must be an exact tuple")
    targets = tuple(
        item if type(item) is RetrievalBindingTarget else binding_to_retrieval_target(item)
        for item in required_binding_targets
    )
    for item in targets:
        validate_retrieval_binding_target(item)
    if any(item.binding_kind not in context.allowed_binding_kinds for item in targets):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "required binding target broadens context binding kinds")
    targets = tuple(sorted(targets, key=lambda item: _canonical(item.to_dict())))
    target_payloads = tuple(_canonical(item.to_dict()) for item in targets)
    if len(set(target_payloads)) != len(target_payloads):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "required binding targets contain duplicates")
    if type(selected_set_limit) is not int or not (1 <= selected_set_limit <= context.selected_set_ceiling):
        raise _fail(RetrievalFailureCode.SELECTION_LIMIT_INVALID, "selected-set limit is invalid")
    result = object.__new__(RetrievalQuery)
    object.__setattr__(result, "schema_version", RETRIEVAL_QUERY_V1)
    object.__setattr__(result, "compatibility_context_id", context.context_id)
    object.__setattr__(result, "requested_behavior_kinds", kinds)
    object.__setattr__(result, "required_binding_targets", targets)
    object.__setattr__(result, "selected_set_limit", selected_set_limit)
    object.__setattr__(result, "library_snapshot_sha256", context.library_snapshot_sha256)
    object.__setattr__(result, "lifecycle_snapshot_id", context.lifecycle_snapshot.snapshot_id)
    object.__setattr__(result, "retrieval_policy", RETRIEVAL_POLICY_V1)
    object.__setattr__(result, "_retriever", retriever)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_query_payload(result))
    object.__setattr__(result, "query_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_QUERY, canonical_bytes=payload))
    validate_retrieval_query(result, retriever=retriever, context=context)
    return result


def validate_retrieval_query(
    value: RetrievalQuery,
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext | None = None,
) -> None:
    require_configured_retriever(retriever)
    if type(value) is not RetrievalQuery or getattr(value, "_trusted_seal", None) is not _SEAL or value._retriever is not retriever:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval query is not retriever sealed")
    if value.schema_version != RETRIEVAL_QUERY_V1 or type(value.schema_version) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "retrieval query schema is unknown")
    _record(value.compatibility_context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "query context id")
    if type(value.requested_behavior_kinds) is not tuple or not value.requested_behavior_kinds or any(type(item) is not BehaviorKind for item in value.requested_behavior_kinds) or value.requested_behavior_kinds != tuple(sorted(value.requested_behavior_kinds, key=lambda item: item.value)):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query behavior kinds changed")
    if type(value.required_binding_targets) is not tuple:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query binding targets are mutable")
    for item in value.required_binding_targets:
        validate_retrieval_binding_target(item)
    if value.required_binding_targets != tuple(sorted(value.required_binding_targets, key=lambda item: _canonical(item.to_dict()))):
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query binding targets are not normalized")
    if type(value.selected_set_limit) is not int or value.selected_set_limit < 1:
        raise _fail(RetrievalFailureCode.SELECTION_LIMIT_INVALID, "query selected-set limit changed")
    if type(value.library_snapshot_sha256) is not str or _SHA256_RE.fullmatch(value.library_snapshot_sha256) is None:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query library snapshot hash is invalid")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "query lifecycle snapshot id")
    if value.retrieval_policy != RETRIEVAL_POLICY_V1:
        raise _fail(RetrievalFailureCode.UNKNOWN_POLICY, "query retrieval policy is unknown")
    if context is not None:
        validate_compatibility_context(context, evaluator=retriever._evaluator)
        if value.compatibility_context_id != context.context_id or value.library_snapshot_sha256 != context.library_snapshot_sha256 or value.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id or not set(value.requested_behavior_kinds) <= set(context.allowed_behavior_kinds) or any(item.binding_kind not in context.allowed_binding_kinds for item in value.required_binding_targets) or value.selected_set_limit > context.selected_set_ceiling:
            raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "query differs from compatibility context")
    payload = _canonical(_query_payload(value))
    _record(value.query_id, IdentityDomain.RETRIEVAL_QUERY, "query_id")
    try:
        validate_record_id(value.query_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "query identity mismatch") from exc


def retrieval_query_from_dict(
    value: object,
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    required_bindings: tuple[object, ...] = (),
) -> RetrievalQuery:
    if type(value) is not dict or set(value) != set(_query_payload_fields()) | {"query_id"}:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query transport fields differ")
    if value["schema_version"] != RETRIEVAL_QUERY_V1:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "query transport schema is unknown")
    try:
        kinds = tuple(BehaviorKind(item) for item in _list(value["requested_behavior_kinds"], "requested_behavior_kinds"))
    except (TypeError, ValueError) as exc:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query behavior kind is unknown") from exc
    query = create_retrieval_query(
        retriever=retriever,
        context=context,
        requested_behavior_kinds=kinds,
        required_binding_targets=required_bindings,
        selected_set_limit=value["selected_set_limit"],
    )
    expected = query.to_dict()
    if value != expected:
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "query transport differs from exact context-bound query")
    return query


def _query_payload_fields() -> tuple[str, ...]:
    return (
        "schema_version", "compatibility_context_id", "requested_behavior_kinds",
        "required_binding_targets", "selected_set_limit", "library_snapshot_sha256",
        "lifecycle_snapshot_id", "retrieval_policy",
    )


def _list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, f"{name} transport must be list")
    return value


def _candidate_payload(value: RetrievalCandidateAudit) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "index_content_key": value.index_content_key,
        "index_manifest_id": value.index_manifest_id,
        "descriptor_id": None if value.descriptor_id is None else value.descriptor_id.to_dict(),
        "compatibility_decision_id": None if value.compatibility_decision_id is None else value.compatibility_decision_id.to_dict(),
        "compatibility_kind": None if value.compatibility_kind is None else value.compatibility_kind.value,
        "disposition": value.disposition.value,
        "failure_code": None if value.failure_code is None else value.failure_code.value,
        "ranking_feature_id": None if value.ranking_feature_id is None else value.ranking_feature_id.to_dict(),
        "ranking_key": None if value.ranking_key is None else list(value.ranking_key),
    }


@dataclass(frozen=True, init=False)
class RetrievalCandidateAudit:
    schema_version: str
    index_content_key: str
    index_manifest_id: str
    descriptor_id: RecordId | None
    compatibility_decision_id: AuthorityDecisionId | None
    compatibility_kind: CompatibilityDecisionKind | None
    disposition: CandidateDisposition
    failure_code: RetrievalFailureCode | None
    ranking_feature_id: RecordId | None
    ranking_key: tuple[int, str, str] | None
    candidate_id: RecordId
    _index_entry: IndexEntry
    _descriptor: CompatibilitySubjectDescriptor | None
    _compatibility_decision: CompatibilityDecision | None
    _ranking_feature: RankingFeatureObservation | None
    _retriever: ConfiguredRetriever
    _context: CompatibilityContext
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalCandidateAudit:
        raise TypeError("RetrievalCandidateAudit is retriever-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_candidate_audit(self)
        return {**_candidate_payload(self), "candidate_id": self.candidate_id.to_dict()}


def _make_candidate_audit(
    *,
    index_entry: IndexEntry,
    descriptor: CompatibilitySubjectDescriptor | None,
    decision: CompatibilityDecision | None,
    disposition: CandidateDisposition,
    failure_code: RetrievalFailureCode | None,
    ranking_feature: RankingFeatureObservation | None,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
) -> RetrievalCandidateAudit:
    require_configured_retriever(retriever)
    validate_compatibility_context(context, evaluator=retriever._evaluator)
    ranking_key = None
    if ranking_feature is not None and descriptor is not None:
        ranking_key = (-ranking_feature.semantic_score_micros, descriptor.manifest_id.value, descriptor.content_key.value)
    result = object.__new__(RetrievalCandidateAudit)
    object.__setattr__(result, "schema_version", RETRIEVAL_CANDIDATE_V1)
    object.__setattr__(result, "index_content_key", index_entry.content_key)
    object.__setattr__(result, "index_manifest_id", index_entry.manifest_id)
    object.__setattr__(result, "descriptor_id", None if descriptor is None else descriptor.descriptor_id)
    object.__setattr__(result, "compatibility_decision_id", None if decision is None else decision.decision_id)
    object.__setattr__(result, "compatibility_kind", None if decision is None else decision.decision_kind)
    object.__setattr__(result, "disposition", disposition)
    object.__setattr__(result, "failure_code", failure_code)
    object.__setattr__(result, "ranking_feature_id", None if ranking_feature is None else ranking_feature.observation_id)
    object.__setattr__(result, "ranking_key", ranking_key)
    object.__setattr__(result, "_index_entry", index_entry)
    object.__setattr__(result, "_descriptor", descriptor)
    object.__setattr__(result, "_compatibility_decision", decision)
    object.__setattr__(result, "_ranking_feature", ranking_feature)
    object.__setattr__(result, "_retriever", retriever)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_candidate_payload(result))
    object.__setattr__(result, "candidate_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_CANDIDATE, canonical_bytes=payload))
    validate_retrieval_candidate_audit(result)
    return result


def validate_retrieval_candidate_audit(
    value: RetrievalCandidateAudit,
    *,
    retriever: ConfiguredRetriever | None = None,
    context: CompatibilityContext | None = None,
) -> None:
    if type(value) is not RetrievalCandidateAudit or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate audit is not sealed")
    bound_retriever = value._retriever
    bound_context = value._context
    require_configured_retriever(bound_retriever)
    validate_compatibility_context(bound_context, evaluator=bound_retriever._evaluator)
    if retriever is not None and retriever is not bound_retriever:
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "candidate belongs to another retriever")
    if context is not None and context is not bound_context:
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "candidate belongs to another compatibility context")
    if value.schema_version != RETRIEVAL_CANDIDATE_V1 or type(value.schema_version) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "candidate schema is unknown")
    if type(value._index_entry) is not IndexEntry or value._index_entry.content_key != value.index_content_key or value._index_entry.manifest_id != value.index_manifest_id:
        raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "candidate index entry changed")
    value._index_entry.to_dict()
    if type(value.disposition) is not CandidateDisposition or (value.failure_code is not None and type(value.failure_code) is not RetrievalFailureCode):
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate outcome is invalid")
    if value._descriptor is None:
        if value.descriptor_id is not None or value._compatibility_decision is not None or value._ranking_feature is not None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "descriptor absence was conflated")
    else:
        validate_compatibility_subject_descriptor(value._descriptor)
        if value.descriptor_id != value._descriptor.descriptor_id:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate descriptor identity differs")
    if value._compatibility_decision is None:
        if value.compatibility_decision_id is not None or value.compatibility_kind is not None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "compatibility absence was conflated")
        if value.disposition in (CandidateDisposition.ELIGIBLE, CandidateDisposition.SELECTED) or value._ranking_feature is not None:
            raise _fail(RetrievalFailureCode.COMPATIBILITY_REJECTED, "candidate without compatibility evidence cannot be eligible")
    else:
        if value._descriptor is None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "compatibility decision lacks its exact descriptor")
        validate_compatibility_decision(
            value._compatibility_decision,
            evaluator=bound_retriever._evaluator,
            context=bound_context,
            descriptor=value._descriptor,
        )
        if value.compatibility_decision_id != value._compatibility_decision.decision_id or value.compatibility_kind is not value._compatibility_decision.decision_kind:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate compatibility identity differs")
    if value._ranking_feature is None:
        if value.ranking_feature_id is not None or value.ranking_key is not None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "ranking absence was conflated")
    else:
        validate_ranking_feature_observation(value._ranking_feature, provider=bound_retriever._ranking_provider)
        expected_key = (-value._ranking_feature.semantic_score_micros, value._descriptor.manifest_id.value, value._descriptor.content_key.value)
        if value.ranking_feature_id != value._ranking_feature.observation_id or value.ranking_key != expected_key:
            raise _fail(RetrievalFailureCode.RANKING_NONDETERMINISTIC, "candidate ranking key differs")
    payload = _canonical(_candidate_payload(value))
    _record(value.candidate_id, IdentityDomain.RETRIEVAL_CANDIDATE, "candidate_id")
    try:
        validate_record_id(value.candidate_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate audit identity mismatch") from exc


def _conflict_record_payload(value: RetrievalConflictRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "compatibility_scan_id": value.compatibility_scan_id.to_dict(),
        "authority_decision_id": value.authority_decision_id.to_dict(),
        "decision_kind": value.decision_kind.value,
        "proposal_ids": [item.to_dict() for item in value.proposal_ids],
        "compatible_candidate_ids": [item.to_dict() for item in value.compatible_candidate_ids],
        "negative_evidence_candidate_ids": [item.to_dict() for item in value.negative_evidence_candidate_ids],
    }


@dataclass(frozen=True, init=False)
class RetrievalConflictRecord:
    schema_version: str
    compatibility_scan_id: RecordId
    authority_decision_id: AuthorityDecisionId
    decision_kind: ConflictDecisionKind
    proposal_ids: tuple[ProposalId, ...]
    compatible_candidate_ids: tuple[RecordId, ...]
    negative_evidence_candidate_ids: tuple[RecordId, ...]
    conflict_record_id: RecordId
    _scan: CompatibilityConflictScan
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalConflictRecord:
        raise TypeError("RetrievalConflictRecord is retriever-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_conflict_record(self)
        return {**_conflict_record_payload(self), "conflict_record_id": self.conflict_record_id.to_dict()}


def _make_retrieval_conflict_record(scan: CompatibilityConflictScan) -> RetrievalConflictRecord:
    validate_compatibility_conflict_scan(scan, evaluator=scan._evaluator)
    result = object.__new__(RetrievalConflictRecord)
    object.__setattr__(result, "schema_version", RETRIEVAL_CONFLICT_RECORD_V1)
    object.__setattr__(result, "compatibility_scan_id", scan.scan_id)
    object.__setattr__(result, "authority_decision_id", scan.decision_id)
    object.__setattr__(result, "decision_kind", scan.decision_kind)
    object.__setattr__(result, "proposal_ids", tuple(item.proposal_id for item in scan.request.proposals))
    object.__setattr__(result, "compatible_candidate_ids", scan.request.compatible_candidate_ids)
    object.__setattr__(result, "negative_evidence_candidate_ids", scan.request.negative_evidence_candidate_ids)
    object.__setattr__(result, "_scan", scan)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_conflict_record_payload(result))
    object.__setattr__(result, "conflict_record_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_CONFLICT, canonical_bytes=payload))
    validate_retrieval_conflict_record(result)
    return result


def validate_retrieval_conflict_record(value: RetrievalConflictRecord) -> None:
    if type(value) is not RetrievalConflictRecord or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval conflict record is not sealed")
    if value.schema_version != RETRIEVAL_CONFLICT_RECORD_V1 or type(value.schema_version) is not str:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "retrieval conflict schema is unknown")
    scan = value._scan
    validate_compatibility_conflict_scan(scan, evaluator=scan._evaluator)
    expected_proposals = tuple(item.proposal_id for item in scan.request.proposals)
    if (
        value.compatibility_scan_id != scan.scan_id
        or value.authority_decision_id != scan.decision_id
        or value.decision_kind is not scan.decision_kind
        or value.proposal_ids != expected_proposals
        or value.compatible_candidate_ids != scan.request.compatible_candidate_ids
        or value.negative_evidence_candidate_ids != scan.request.negative_evidence_candidate_ids
    ):
        raise _fail(RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE, "retrieval conflict record differs from authority scan")
    payload = _canonical(_conflict_record_payload(value))
    _record(value.conflict_record_id, IdentityDomain.RETRIEVAL_CONFLICT, "retrieval conflict record id")
    try:
        validate_record_id(value.conflict_record_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval conflict record identity mismatch") from exc


def _is_eligible(decision: CompatibilityDecision) -> bool:
    return decision.decision_kind is CompatibilityDecisionKind.COMPATIBLE


def _decision_payload(value: RetrievalDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "query_id": value.query_id.to_dict(),
        "compatibility_context_id": value.compatibility_context_id.to_dict(),
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "considered_candidates": [item.to_dict() for item in value.considered_candidates],
        "ranking_feature_observations": [item.to_dict() for item in value.ranking_feature_observations],
        "conflict_scan_id": value.conflict_scan_id.to_dict(),
        "conflict_decision_id": value.conflict_decision_id.to_dict(),
        "conflict_records": [item.to_dict() for item in value.conflict_records],
        "selected_candidate_ids": [item.to_dict() for item in value.selected_candidate_ids],
        "outcome": value.outcome.value,
        "retrieval_policy": value.retrieval_policy,
        "retriever_actor": value.retriever_actor.to_dict(),
        "consumer_actor": value.consumer_actor.to_dict(),
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


@dataclass(frozen=True, init=False)
class RetrievalDecision:
    schema_version: str
    query_id: RecordId
    compatibility_context_id: RecordId
    library_snapshot_sha256: str
    lifecycle_snapshot_id: RecordId
    considered_candidates: tuple[RetrievalCandidateAudit, ...]
    ranking_feature_observations: tuple[RankingFeatureObservation, ...]
    conflict_scan_id: RecordId
    conflict_decision_id: AuthorityDecisionId
    conflict_records: tuple[RetrievalConflictRecord, ...]
    selected_candidate_ids: tuple[RecordId, ...]
    outcome: RetrievalOutcome
    retrieval_policy: str
    retriever_actor: ActorIdentity
    consumer_actor: ActorIdentity
    created_at_utc: datetime
    decision_id: RecordId
    _conflict_scan: CompatibilityConflictScan
    _retriever: ConfiguredRetriever
    _query: RetrievalQuery
    _context: CompatibilityContext
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalDecision:
        raise TypeError("RetrievalDecision is retriever-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_decision(self, retriever=self._retriever)
        return {**_decision_payload(self), "decision_id": self.decision_id.to_dict()}


def _make_retrieval_decision(
    *,
    retriever: ConfiguredRetriever,
    query: RetrievalQuery,
    context: CompatibilityContext,
    candidates: tuple[RetrievalCandidateAudit, ...],
    conflict_scan: CompatibilityConflictScan,
    selected: tuple[RetrievalCandidateAudit, ...],
) -> RetrievalDecision:
    if conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND:
        outcome = RetrievalOutcome.SELECTED if selected else RetrievalOutcome.NO_CANDIDATES
    else:
        outcome = RetrievalOutcome.CONFLICT_BLOCKED
        selected = ()
    result = object.__new__(RetrievalDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_DECISION_V1)
    object.__setattr__(result, "query_id", query.query_id)
    object.__setattr__(result, "compatibility_context_id", context.context_id)
    object.__setattr__(result, "library_snapshot_sha256", context.library_snapshot_sha256)
    object.__setattr__(result, "lifecycle_snapshot_id", context.lifecycle_snapshot.snapshot_id)
    object.__setattr__(result, "considered_candidates", candidates)
    observations = tuple(item._ranking_feature for item in candidates if item._ranking_feature is not None)
    object.__setattr__(result, "ranking_feature_observations", observations)
    object.__setattr__(result, "conflict_scan_id", conflict_scan.scan_id)
    object.__setattr__(result, "conflict_decision_id", conflict_scan.decision_id)
    object.__setattr__(result, "conflict_records", (_make_retrieval_conflict_record(conflict_scan),))
    object.__setattr__(result, "selected_candidate_ids", tuple(item.candidate_id for item in selected))
    object.__setattr__(result, "outcome", outcome)
    object.__setattr__(result, "retrieval_policy", RETRIEVAL_POLICY_V1)
    object.__setattr__(result, "retriever_actor", retriever._evaluator.retriever_actor)
    object.__setattr__(result, "consumer_actor", retriever._evaluator.consumer_actor)
    object.__setattr__(result, "created_at_utc", _timestamp(retriever._trusted_clock(), "retrieval decision timestamp"))
    object.__setattr__(result, "_conflict_scan", conflict_scan)
    object.__setattr__(result, "_retriever", retriever)
    object.__setattr__(result, "_query", query)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_decision_payload(result))
    object.__setattr__(result, "decision_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_DECISION, canonical_bytes=payload))
    validate_retrieval_decision(result, retriever=retriever, query=query, context=context)
    return result


def validate_retrieval_decision(
    value: RetrievalDecision,
    *,
    retriever: ConfiguredRetriever,
    query: RetrievalQuery | None = None,
    context: CompatibilityContext | None = None,
) -> None:
    require_configured_retriever(retriever)
    if type(value) is not RetrievalDecision or getattr(value, "_trusted_seal", None) is not _SEAL or value._retriever is not retriever:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval decision is not retriever sealed")
    bound_query = value._query
    bound_context = value._context
    if query is not None and query is not bound_query:
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "retrieval decision belongs to another query")
    if context is not None and context is not bound_context:
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "retrieval decision belongs to another context")
    validate_retrieval_query(bound_query, retriever=retriever, context=bound_context)
    validate_compatibility_context(bound_context, evaluator=retriever._evaluator)
    if value.schema_version != RETRIEVAL_DECISION_V1 or type(value.schema_version) is not str or type(value.outcome) is not RetrievalOutcome:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "retrieval decision schema or outcome is invalid")
    _record(value.query_id, IdentityDomain.RETRIEVAL_QUERY, "decision query id")
    _record(value.compatibility_context_id, IdentityDomain.COMPATIBILITY_CONTEXT, "decision context id")
    if type(value.library_snapshot_sha256) is not str or _SHA256_RE.fullmatch(value.library_snapshot_sha256) is None:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "decision snapshot hash is invalid")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "decision lifecycle snapshot id")
    if type(value.considered_candidates) is not tuple:
        raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "considered candidates are not immutable")
    for candidate in value.considered_candidates:
        validate_retrieval_candidate_audit(candidate, retriever=retriever, context=bound_context)
    if len({item.candidate_id.value for item in value.considered_candidates}) != len(value.considered_candidates):
        raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "candidate audit contains duplicates")
    expected_observations = tuple(item._ranking_feature for item in value.considered_candidates if item._ranking_feature is not None)
    if value.ranking_feature_observations != expected_observations:
        raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "ranking observation audit is incomplete")
    validate_compatibility_conflict_scan(value._conflict_scan, evaluator=retriever._evaluator)
    if value.conflict_scan_id != value._conflict_scan.scan_id or value.conflict_decision_id != value._conflict_scan.decision_id:
        raise _fail(RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict authority identity differs")
    if type(value.conflict_records) is not tuple or len(value.conflict_records) != 1:
        raise _fail(RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE, "retrieval conflict audit is incomplete")
    validate_retrieval_conflict_record(value.conflict_records[0])
    if value.conflict_records[0]._scan is not value._conflict_scan:
        raise _fail(RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE, "retrieval conflict audit substituted its authority scan")
    selected_lookup = {item.candidate_id.value: item for item in value.considered_candidates}
    selected = tuple(selected_lookup.get(item.value) for item in value.selected_candidate_ids)
    if any(item is None for item in selected) or any(item.disposition is not CandidateDisposition.SELECTED for item in selected if item is not None):
        raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "selected candidate audit is invalid")
    eligible_ranked = tuple(sorted((item for item in value.considered_candidates if item.ranking_key is not None), key=lambda item: item.ranking_key))
    if value.selected_candidate_ids != tuple(item.candidate_id for item in eligible_ranked[: len(value.selected_candidate_ids)]):
        raise _fail(RetrievalFailureCode.RANKING_NONDETERMINISTIC, "selected order differs from total ranking key")
    expected_outcome = RetrievalOutcome.CONFLICT_BLOCKED if value._conflict_scan.decision_kind is not ConflictDecisionKind.NO_CONFLICT_FOUND else (RetrievalOutcome.SELECTED if value.selected_candidate_ids else RetrievalOutcome.NO_CANDIDATES)
    if value.outcome is not expected_outcome:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval outcome contradicts audit")
    if value.retrieval_policy != RETRIEVAL_POLICY_V1 or value.retriever_actor != retriever._evaluator.retriever_actor or value.consumer_actor != retriever._evaluator.consumer_actor:
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retrieval policy or actors changed")
    _timestamp(value.created_at_utc, "retrieval timestamp")
    if (
        value.query_id != bound_query.query_id
        or value.compatibility_context_id != bound_context.context_id
        or value.library_snapshot_sha256 != bound_context.library_snapshot_sha256
        or value.lifecycle_snapshot_id != bound_context.lifecycle_snapshot.snapshot_id
    ):
        raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "retrieval decision context binding changed")
    payload = _canonical(_decision_payload(value))
    _record(value.decision_id, IdentityDomain.RETRIEVAL_DECISION, "retrieval decision id")
    try:
        validate_record_id(value.decision_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "retrieval decision identity mismatch") from exc


def _load_payload(value: RetrievalLoadDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "retrieval_decision_id": value.retrieval_decision_id.to_dict(),
        "selected_candidate_id": value.selected_candidate_id.to_dict(),
        "descriptor_id": value.descriptor_id.to_dict(),
        "before_loading_revalidation_id": value.before_loading_revalidation_id.to_dict(),
        "loaded_content_key": value.loaded_content_key,
        "loaded_manifest_id": value.loaded_manifest_id,
        "pre_load_snapshot_sha256": value.pre_load_snapshot_sha256,
        "post_load_snapshot_sha256": value.post_load_snapshot_sha256,
        "outcome": value.outcome.value,
        "failure_code": None if value.failure_code is None else value.failure_code.value,
        "cause_code": None if value.cause_code is None else value.cause_code.value,
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


@dataclass(frozen=True, init=False)
class RetrievalLoadDecision:
    schema_version: str
    retrieval_decision_id: RecordId
    selected_candidate_id: RecordId
    descriptor_id: RecordId
    before_loading_revalidation_id: RecordId
    loaded_content_key: str | None
    loaded_manifest_id: str | None
    pre_load_snapshot_sha256: str | None
    post_load_snapshot_sha256: str | None
    outcome: LoadOutcome
    failure_code: CompatibilityFailureCode | None
    cause_code: CompatibilityFailureCode | None
    created_at_utc: datetime
    load_decision_id: RecordId
    _revalidation: CompatibilityRevalidationRecord
    _descriptor: CompatibilitySubjectDescriptor
    _retrieval_decision: RetrievalDecision
    _candidate: RetrievalCandidateAudit
    _context: CompatibilityContext
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalLoadDecision:
        raise TypeError("RetrievalLoadDecision is retriever-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_load_decision(self)
        return {**_load_payload(self), "load_decision_id": self.load_decision_id.to_dict()}


@dataclass(frozen=True)
class RetrievalResult:
    """Audit evidence for one retrieval. **Not a consumption authority.**

    This record predates the §22 gates and is deliberately left as it was: it
    documents what was considered, ranked, selected and loaded, which is exactly
    the trace an auditor needs. What it does not do — and must never be read as
    doing — is admit anything for use.

    The knowledge a consumer may act on travels in an
    ``admission.AdmittedKnowledgeHandle``, which requires a durable consumption
    ADMIT and a fresh coherent head observation. A ``RetrievalResult`` cannot be
    turned into one, so a caller holding only this object holds audit material.
    """

    decision: RetrievalDecision
    load_decisions: tuple[RetrievalLoadDecision, ...]

    def __post_init__(self) -> None:
        validate_retrieval_decision(self.decision, retriever=self.decision._retriever)
        if type(self.load_decisions) is not tuple:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load decisions must be immutable")
        for item in self.load_decisions:
            validate_retrieval_load_decision(item)
        if tuple(item.selected_candidate_id for item in self.load_decisions) != self.decision.selected_candidate_ids:
            raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "load decision set differs from selection")


def _mark_selected(candidate: RetrievalCandidateAudit) -> RetrievalCandidateAudit:
    return _make_candidate_audit(
        index_entry=candidate._index_entry,
        descriptor=candidate._descriptor,
        decision=candidate._compatibility_decision,
        disposition=CandidateDisposition.SELECTED,
        failure_code=None,
        ranking_feature=candidate._ranking_feature,
        retriever=candidate._retriever,
        context=candidate._context,
    )


@dataclass(frozen=True)
class RetrievalEnumeration:
    """What a query found, before anything was selected or loaded.

    This is the half of retrieval that must happen *before* the §22 gates, and
    separating it out is what makes gating possible at all.

    The four-gate chain fixes one subject set at ingestion and carries it
    unchanged to consumption — ``require_gate_predecessor`` demands exact
    equality with its predecessor's subjects. A single function that discovered
    its own subject set by ranking a library index therefore could not be gated
    from the inside: at the moment it would need a publication decision over
    that set, the set does not exist yet, and manufacturing one inside the
    retrieval owner would be the retrieval owner claiming publication
    authority. That is a worse defect than the ungated path it would be trying
    to close.

    So enumeration ends here, and hands back the subject refs the chain runs
    over. ``subject_refs`` holds only candidates that resolved to a descriptor,
    because a candidate without one has no hash-bound identity to present to a
    gate; the rest stay in ``candidates`` as audit trace. Nothing here is
    selectable and nothing has been loaded.
    """

    candidates: tuple[RetrievalCandidateAudit, ...]
    subject_refs: tuple[HashBoundRef, ...]
    _conflict_scan: object
    _query: RetrievalQuery
    _context: CompatibilityContext
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalEnumeration:
        raise TypeError("RetrievalEnumeration is produced only by enumerate_retrieval_candidates")


def _enumeration_seal_check(value: RetrievalEnumeration) -> RetrievalEnumeration:
    if type(value) is not RetrievalEnumeration or getattr(value, "_trusted_seal", None) is not _ENUMERATION_SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "enumeration is not retriever-produced")
    return value


def enumerate_retrieval_candidates(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    query: RetrievalQuery,
) -> RetrievalEnumeration:
    """Enumerate and evaluate candidates. Select nothing, load nothing.

    Everything this does is evidence-gathering: it reads the index, resolves
    descriptors, evaluates compatibility and scans for conflicts. No candidate
    becomes selectable here and no behavior bytes are read, which is precisely
    why it is safe to run before any gate — and why the gate can run afterwards
    over a subject set that is now fixed.
    """

    require_configured_retriever(retriever)
    validate_compatibility_context(context, evaluator=retriever._evaluator)
    validate_retrieval_query(query, retriever=retriever, context=context)
    _same_snapshot(retriever._library, context.library_snapshot)
    entries = tuple(
        entry
        for entry in retriever._library.search_index()
        if entry.behavior_kind in {item.value for item in query.requested_behavior_kinds}
    )
    if len(entries) > MAX_INDEX_ENTRIES_V1:
        raise _fail(RetrievalFailureCode.RESOURCE_LIMIT_EXCEEDED, "candidate enumeration exceeds library limit")
    discovered: list[tuple[IndexEntry, CompatibilitySubjectDescriptor | None, CompatibilityDecision | None, RetrievalFailureCode | None]] = []
    descriptors: list[CompatibilitySubjectDescriptor] = []
    decisions: list[CompatibilityDecision] = []
    for entry in entries:
        descriptor: CompatibilitySubjectDescriptor | None = None
        try:
            descriptor = retriever._descriptor_resolver(entry)
            if type(descriptor) is not CompatibilitySubjectDescriptor:
                raise _fail(RetrievalFailureCode.DESCRIPTOR_MISSING, "descriptor resolver returned no exact descriptor")
            validate_compatibility_subject_descriptor(descriptor)
            reconcile_index_entry(entry, descriptor)
            required_targets = {_canonical(item.to_dict()) for item in query.required_binding_targets}
            candidate_targets = {
                _canonical(binding_to_retrieval_target(item).to_dict())
                for item in descriptor._bindings
            }
            if required_targets and not required_targets <= candidate_targets:
                raise _fail(RetrievalFailureCode.COMPATIBILITY_REJECTED, "candidate lacks required binding target")
            decision = evaluate_compatibility(evaluator=retriever._evaluator, context=context, descriptor=descriptor, index_entry=entry)
            validate_compatibility_decision(decision, evaluator=retriever._evaluator, context=context, descriptor=descriptor)
            discovered.append((entry, descriptor, decision, None))
            descriptors.append(descriptor)
            decisions.append(decision)
        except CompatibilityViolation as exc:
            code = RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH if exc.failure_code is CompatibilityFailureCode.INDEX_DESCRIPTOR_MISMATCH else RetrievalFailureCode.COMPATIBILITY_REJECTED
            discovered.append((entry, descriptor, None, code))
        except RetrievalViolation as exc:
            discovered.append((entry, None, None, exc.failure_code))
    proposals = retriever._conflict_proposal_resolver(context, tuple(decisions), tuple(descriptors))
    if type(proposals) is not tuple or any(type(item) is not ConflictEvidenceProposal for item in proposals):
        raise _fail(RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE, "conflict proposal resolver returned an invalid set")
    conflict_scan = evaluate_conflicts(
        evaluator=retriever._evaluator,
        context=context,
        decisions=tuple(decisions),
        descriptors=tuple(descriptors),
        considered_index_entries=entries,
        proposals=proposals,
    )
    validate_compatibility_conflict_scan(conflict_scan, evaluator=retriever._evaluator)
    audits: list[RetrievalCandidateAudit] = []
    if conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND:
        for entry, descriptor, decision, failure in discovered:
            if descriptor is None or decision is None:
                disposition = CandidateDisposition.POISONED_INDEX if failure is RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH else CandidateDisposition.DESCRIPTOR_UNAVAILABLE
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=disposition, failure_code=failure or RetrievalFailureCode.DESCRIPTOR_MISSING, ranking_feature=None, retriever=retriever, context=context))
                continue
            if _is_eligible(decision):
                feature = retriever._ranking_provider.observe(query=query, context=context, descriptor=descriptor)
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=CandidateDisposition.ELIGIBLE, failure_code=None, ranking_feature=feature, retriever=retriever, context=context))
            else:
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=CandidateDisposition.REJECTED, failure_code=RetrievalFailureCode.COMPATIBILITY_REJECTED, ranking_feature=None, retriever=retriever, context=context))
    else:
        for entry, descriptor, decision, failure in discovered:
            disposition = (
                CandidateDisposition.POISONED_INDEX
                if failure is RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH
                else CandidateDisposition.DESCRIPTOR_UNAVAILABLE
                if descriptor is None
                else CandidateDisposition.REJECTED
            )
            audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=disposition, failure_code=failure or (RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE if conflict_scan.decision_kind is ConflictDecisionKind.SCAN_INCOMPLETE else RetrievalFailureCode.CONFLICT_UNRESOLVED), ranking_feature=None, retriever=retriever, context=context))
    enumeration = object.__new__(RetrievalEnumeration)
    object.__setattr__(enumeration, "candidates", tuple(audits))
    object.__setattr__(
        enumeration,
        "subject_refs",
        tuple(candidate_subject_ref(item._descriptor) for item in audits if item._descriptor is not None),
    )
    object.__setattr__(enumeration, "_conflict_scan", conflict_scan)
    object.__setattr__(enumeration, "_query", query)
    object.__setattr__(enumeration, "_context", context)
    object.__setattr__(enumeration, "_trusted_seal", _ENUMERATION_SEAL)
    return enumeration


def select_and_load(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    query: RetrievalQuery,
    enumeration: RetrievalEnumeration,
    admitted_refs: tuple[HashBoundRef, ...],
) -> RetrievalResult:
    """Rank, select and load — among gate-admitted candidates only.

    ``admitted_refs`` is what the §22 retrieval gate returned for this
    enumeration's subject set. A candidate outside it is never ranked into the
    selectable set and its bytes are never read, which is the Patch 8
    requirement stated where it can be enforced: the gate runs before a
    candidate becomes selectable, and this owner offers no way in that skips it.

    A rejected candidate keeps its place in the audit trace. What it loses is
    eligibility, not its record.

    The result remains audit evidence and confers no consumption authority. It
    cannot be turned into an ``AdmittedKnowledgeHandle``; the four gates and
    ``admit_for_consumption`` remain the only route to consumable knowledge.
    """

    require_configured_retriever(retriever)
    _enumeration_seal_check(enumeration)
    if enumeration._query is not query or enumeration._context is not context:
        raise _fail(
            RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER,
            "enumeration belongs to another query or consumer context",
        )
    if type(admitted_refs) is not tuple or any(type(item) is not HashBoundRef for item in admitted_refs):
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "admitted refs must be an exact tuple of hash-bound references",
        )
    enumerated = {_ref_key(item) for item in enumeration.subject_refs}
    admitted = {_ref_key(item) for item in admitted_refs}
    if not admitted <= enumerated:
        raise _fail(
            RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE,
            "admitted refs include a candidate this enumeration never found",
        )
    conflict_scan = enumeration._conflict_scan
    audits = list(enumeration.candidates)
    eligible = tuple(
        item
        for item in audits
        if item.ranking_key is not None
        and item._descriptor is not None
        and _ref_key(candidate_subject_ref(item._descriptor)) in admitted
    )
    ranked = sorted(eligible, key=lambda item: item.ranking_key)
    selected_original = tuple(ranked[: query.selected_set_limit]) if conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND else ()
    selected_ids = {item.candidate_id.value for item in selected_original}
    final_audits = tuple(_mark_selected(item) if item.candidate_id.value in selected_ids else item for item in audits)
    selected_by_descriptor = {
        item.descriptor_id.value: item
        for item in final_audits
        if item.disposition is CandidateDisposition.SELECTED and item.descriptor_id is not None
    }
    selected = tuple(
        selected_by_descriptor[item.descriptor_id.value]
        for item in selected_original
        if item.descriptor_id is not None
    )
    decision = _make_retrieval_decision(retriever=retriever, query=query, context=context, candidates=final_audits, conflict_scan=conflict_scan, selected=selected)
    loads: list[RetrievalLoadDecision] = []
    for candidate in selected:
        compatibility = candidate._compatibility_decision
        descriptor = candidate._descriptor
        if compatibility is None or descriptor is None:
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "selected candidate lacks compatibility evidence")
        revalidation = revalidate_before_loading(evaluator=retriever._evaluator, context=context, descriptor=descriptor, original_decision=compatibility)
        if revalidation.outcome is RevalidationOutcome.FAILED:
            loads.append(
                _make_load_decision(
                    retriever=retriever,
                    retrieval_decision=decision,
                    candidate=candidate,
                    descriptor=descriptor,
                    revalidation=revalidation,
                    loaded=None,
                    pre_snapshot=None,
                    post_snapshot=None,
                    context=context,
                )
            )
            continue
        require_revalidation_passed(revalidation, expected_stage=RevalidationStage.BEFORE_LOADING)
        pre_snapshot = _same_snapshot(retriever._library, context.library_snapshot)
        loaded = retriever._library.get_verified_behavior(descriptor.content_key, descriptor.manifest_id)
        validate_verified_behavior_record(loaded)
        validate_loaded_compatibility_subject(
            descriptor=descriptor,
            unit=loaded.unit,
            blob=loaded.blob,
            manifest=loaded.manifest,
        )
        if loaded.unit.content_key.value != descriptor.content_key.value or loaded.manifest.manifest_id.value != descriptor.manifest_id.value:
            raise _fail(RetrievalFailureCode.LOADED_IDENTITY_MISMATCH, "verified loaded identity differs from descriptor")
        post_snapshot = _same_snapshot(retriever._library, context.library_snapshot)
        loads.append(_make_load_decision(retriever=retriever, retrieval_decision=decision, candidate=candidate, descriptor=descriptor, revalidation=revalidation, loaded=loaded, pre_snapshot=pre_snapshot, post_snapshot=post_snapshot, context=context))
    return RetrievalResult(decision, tuple(loads))


def _make_load_decision(
    *,
    retriever: ConfiguredRetriever,
    retrieval_decision: RetrievalDecision,
    candidate: RetrievalCandidateAudit,
    descriptor: CompatibilitySubjectDescriptor,
    revalidation: CompatibilityRevalidationRecord,
    loaded: VerifiedBehaviorRecord | None,
    pre_snapshot: LibrarySnapshot | None,
    post_snapshot: LibrarySnapshot | None,
    context: CompatibilityContext,
) -> RetrievalLoadDecision:
    validate_compatibility_revalidation_record(revalidation)
    if revalidation.outcome is RevalidationOutcome.PASSED:
        if type(loaded) is not VerifiedBehaviorRecord or type(pre_snapshot) is not LibrarySnapshot or type(post_snapshot) is not LibrarySnapshot:
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "passing revalidation requires verified loaded evidence")
        loaded_content_key = loaded.unit.content_key.value
        loaded_manifest_id = loaded.manifest.manifest_id.value
        pre_load_snapshot_sha256 = hashlib.sha256(_canonical(pre_snapshot.to_dict())).hexdigest()
        post_load_snapshot_sha256 = hashlib.sha256(_canonical(post_snapshot.to_dict())).hexdigest()
        outcome = LoadOutcome.VERIFIED_LOADED
        failure_code = None
        cause_code = None
    elif revalidation.outcome is RevalidationOutcome.FAILED:
        if loaded is not None or pre_snapshot is not None or post_snapshot is not None:
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "blocked revalidation cannot carry loaded evidence")
        loaded_content_key = None
        loaded_manifest_id = None
        pre_load_snapshot_sha256 = None
        post_load_snapshot_sha256 = None
        outcome = LoadOutcome.REVALIDATION_BLOCKED
        failure_code = revalidation.failure_code
        cause_code = revalidation.cause_code
    result = object.__new__(RetrievalLoadDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_LOAD_DECISION_V1)
    object.__setattr__(result, "retrieval_decision_id", retrieval_decision.decision_id)
    object.__setattr__(result, "selected_candidate_id", candidate.candidate_id)
    object.__setattr__(result, "descriptor_id", descriptor.descriptor_id)
    object.__setattr__(result, "before_loading_revalidation_id", revalidation.revalidation_id)
    object.__setattr__(result, "loaded_content_key", loaded_content_key)
    object.__setattr__(result, "loaded_manifest_id", loaded_manifest_id)
    object.__setattr__(result, "pre_load_snapshot_sha256", pre_load_snapshot_sha256)
    object.__setattr__(result, "post_load_snapshot_sha256", post_load_snapshot_sha256)
    object.__setattr__(result, "outcome", outcome)
    object.__setattr__(result, "failure_code", failure_code)
    object.__setattr__(result, "cause_code", cause_code)
    object.__setattr__(result, "created_at_utc", _timestamp(retriever._trusted_clock(), "load decision timestamp"))
    object.__setattr__(result, "_revalidation", revalidation)
    object.__setattr__(result, "_descriptor", descriptor)
    object.__setattr__(result, "_retrieval_decision", retrieval_decision)
    object.__setattr__(result, "_candidate", candidate)
    object.__setattr__(result, "_context", context)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_load_payload(result))
    object.__setattr__(result, "load_decision_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_LOAD_DECISION, canonical_bytes=payload))
    validate_retrieval_load_decision(result)
    return result


def validate_retrieval_load_decision(value: RetrievalLoadDecision) -> None:
    if type(value) is not RetrievalLoadDecision or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load decision is not sealed")
    if value.schema_version != RETRIEVAL_LOAD_DECISION_V1 or type(value.schema_version) is not str or type(value.outcome) is not LoadOutcome:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "load decision schema or outcome is invalid")
    _record(value.retrieval_decision_id, IdentityDomain.RETRIEVAL_DECISION, "load retrieval decision id")
    _record(value.selected_candidate_id, IdentityDomain.RETRIEVAL_CANDIDATE, "load selected candidate id")
    _record(value.descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "load descriptor id")
    _record(value.before_loading_revalidation_id, IdentityDomain.COMPATIBILITY_REVALIDATION, "load revalidation id")
    retrieval_decision = value._retrieval_decision
    candidate = value._candidate
    context = value._context
    validate_retrieval_decision(
        retrieval_decision,
        retriever=retrieval_decision._retriever,
        query=retrieval_decision._query,
        context=context,
    )
    validate_retrieval_candidate_audit(
        candidate,
        retriever=retrieval_decision._retriever,
        context=context,
    )
    validate_compatibility_revalidation_record(value._revalidation)
    validate_compatibility_subject_descriptor(value._descriptor)
    selected_candidate = next(
        (item for item in retrieval_decision.considered_candidates if item.candidate_id == value.selected_candidate_id),
        None,
    )
    if (
        value.retrieval_decision_id != retrieval_decision.decision_id
        or selected_candidate is not candidate
        or candidate.disposition is not CandidateDisposition.SELECTED
        or candidate._descriptor is not value._descriptor
        or candidate._compatibility_decision is None
        or value._revalidation.revalidation_id != value.before_loading_revalidation_id
        or value._revalidation._context is not context
        or value._revalidation.stage is not RevalidationStage.BEFORE_LOADING
        or value._revalidation.context_id != context.context_id
        or value._revalidation.descriptor_id != value.descriptor_id
        or value._revalidation.original_decision_id != candidate._compatibility_decision.decision_id
        or value._revalidation.library_snapshot_sha256 != context.library_snapshot_sha256
        or value._revalidation.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id
        or value._descriptor.descriptor_id != value.descriptor_id
    ):
        raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "load decision nested evidence differs")
    if value.outcome is LoadOutcome.VERIFIED_LOADED:
        if (
            value._revalidation.outcome is not RevalidationOutcome.PASSED
            or value._revalidation.observation_sha256 != context.observation_sha256
            or value.failure_code is not None
            or value.cause_code is not None
        ):
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "verified load revalidation evidence is invalid")
        _safe_id(value.loaded_content_key, "loaded content key")
        _safe_id(value.loaded_manifest_id, "loaded manifest id")
        if value.loaded_content_key != value._descriptor.content_key.value or value.loaded_manifest_id != value._descriptor.manifest_id.value:
            raise _fail(RetrievalFailureCode.LOADED_IDENTITY_MISMATCH, "load decision identity differs from descriptor")
        for digest in (value.pre_load_snapshot_sha256, value.post_load_snapshot_sha256):
            if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
                raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load snapshot hash is invalid")
        if value.pre_load_snapshot_sha256 != value.post_load_snapshot_sha256:
            raise _fail(RetrievalFailureCode.SNAPSHOT_DRIFT, "library changed during verified load")
    elif value.outcome is LoadOutcome.REVALIDATION_BLOCKED:
        if (
            value._revalidation.outcome is not RevalidationOutcome.FAILED
            or value.failure_code is not value._revalidation.failure_code
            or value.cause_code is not value._revalidation.cause_code
            or value.loaded_content_key is not None
            or value.loaded_manifest_id is not None
            or value.pre_load_snapshot_sha256 is not None
            or value.post_load_snapshot_sha256 is not None
        ):
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "blocked load evidence is invalid")
    _timestamp(value.created_at_utc, "load timestamp")
    payload = _canonical(_load_payload(value))
    _record(value.load_decision_id, IdentityDomain.RETRIEVAL_LOAD_DECISION, "load decision id")
    try:
        validate_record_id(value.load_decision_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load decision identity mismatch") from exc


def gate_selectable_candidates(
    *,
    controller: "object",
    candidates: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    requested: "object",
    publication_decision: "object",
) -> tuple[HashBoundRef, ...]:
    """Run the §22 retrieval gate before a candidate becomes selectable.

    Ranking is not authority. A candidate that ranks first is still not
    selectable until the retrieval gate admits it against this consumer context,
    so this owner asks the gate first and returns only what the gate admits.
    Rejected candidates stay visible to the audit record built by
    ``enumerate_retrieval_candidates``; what they lose is eligibility, not
    their trace.
    """

    from .admission import (
        GateKind,
        evaluate_retrieval_gate,
        require_configured_gate_controller,
    )

    require_configured_gate_controller(controller)
    if type(candidates) is not tuple:
        raise _fail(RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE, "candidate refs must be an exact tuple")
    if not candidates:
        return ()
    ordered = tuple(sorted(candidates, key=lambda item: f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}"))
    decision = evaluate_retrieval_gate(
        controller,
        subject_refs=ordered,
        consumer_context_ref=consumer_context_ref,
        requested=requested,
        predecessor=publication_decision,
    )
    if decision.gate_kind is not GateKind.RETRIEVAL:
        raise _fail(RetrievalFailureCode.COMPATIBILITY_REJECTED, "retrieval gate returned another gate kind")
    return decision.subject_refs if decision.admitted else ()


def revalidate_loaded_before_consumption(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    retrieval_decision: RetrievalDecision,
    load_decision: RetrievalLoadDecision,
) -> CompatibilityRevalidationRecord:
    require_configured_retriever(retriever)
    validate_compatibility_context(context, evaluator=retriever._evaluator)
    validate_retrieval_decision(retrieval_decision, retriever=retriever, context=context)
    validate_retrieval_load_decision(load_decision)
    if load_decision.outcome is not LoadOutcome.VERIFIED_LOADED:
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "blocked load cannot be revalidated for consumption")
    if (
        load_decision._retrieval_decision is not retrieval_decision
        or load_decision._context is not context
        or load_decision.retrieval_decision_id != retrieval_decision.decision_id
    ):
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "load belongs to another retrieval decision")
    candidate = next((item for item in retrieval_decision.considered_candidates if item.candidate_id == load_decision.selected_candidate_id), None)
    if candidate is None or candidate is not load_decision._candidate or candidate._descriptor is None or candidate._compatibility_decision is None:
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "selected candidate evidence is unavailable")
    record = revalidate_before_consumption(
        evaluator=retriever._evaluator,
        context=context,
        descriptor=candidate._descriptor,
        original_decision=candidate._compatibility_decision,
        before_loading=load_decision._revalidation,
    )
    return record


__all__ = (
    "RETRIEVAL_QUERY_V1", "RETRIEVAL_BINDING_TARGET_V1", "RETRIEVAL_CANDIDATE_V1",
    "RANKING_FEATURE_OBSERVATION_V1",
    "RETRIEVAL_CONFLICT_RECORD_V1", "RETRIEVAL_DECISION_V1", "RETRIEVAL_LOAD_DECISION_V1",
    "RETRIEVAL_POLICY_V1", "RANKING_PROFILE_V1", "RETRIEVAL_MEDIA_TYPE_V1",
    "RetrievalFailureCode", "RetrievalViolation", "CandidateDisposition", "RetrievalOutcome",
    "LoadOutcome", "RetrievalBindingTarget", "RetrievalQuery", "RankingFeatureObservation",
    "ConfiguredRankingFeatureProvider", "ConfiguredRetriever", "RetrievalCandidateAudit",
    "RetrievalConflictRecord",
    "RetrievalDecision", "RetrievalLoadDecision", "RetrievalResult",
    "configure_ranking_feature_provider", "require_configured_ranking_feature_provider",
    "validate_ranking_feature_observation", "configure_retriever", "require_configured_retriever",
    "binding_to_retrieval_target", "validate_retrieval_binding_target",
    "create_retrieval_query", "validate_retrieval_query", "retrieval_query_from_dict",
    "validate_retrieval_candidate_audit", "validate_retrieval_conflict_record",
    "validate_retrieval_decision",
    "enumerate_retrieval_candidates", "select_and_load",
    "RetrievalEnumeration", "candidate_subject_ref",
    "validate_retrieval_load_decision",
    "revalidate_loaded_before_consumption",
    "gate_selectable_candidates",
)
