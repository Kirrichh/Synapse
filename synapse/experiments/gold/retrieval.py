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
from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
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
        "required_binding_targets": list(value.required_binding_targets),
        "selected_set_limit": value.selected_set_limit,
        "library_snapshot_sha256": value.library_snapshot_sha256,
        "lifecycle_snapshot_id": value.lifecycle_snapshot_id.to_dict(),
        "retrieval_policy": value.retrieval_policy,
    }


@dataclass(frozen=True, init=False)
class RetrievalQuery:
    schema_version: str
    compatibility_context_id: RecordId
    requested_behavior_kinds: tuple[BehaviorKind, ...]
    required_binding_targets: tuple[str, ...]
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
    require_stage4_authority_handle(value._authority_handle)
    require_configured_compatibility_evaluator(value._evaluator)
    if value._declaration is not value._evaluator.declaration or value._library is not value._evaluator.library:
        raise _fail(RetrievalFailureCode.WRONG_EVALUATOR_CAPABILITY, "retriever configuration changed")
    if value._retrieval_policy != RETRIEVAL_POLICY_V1 or not callable(value._trusted_clock) or not callable(value._descriptor_resolver) or not callable(value._conflict_proposal_resolver):
        raise _fail(RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER, "retriever policy or callables changed")
    require_configured_ranking_feature_provider(value._ranking_provider)


def create_retrieval_query(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    requested_behavior_kinds: tuple[BehaviorKind, ...],
    required_binding_targets: tuple[str, ...],
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
    targets = _strings(tuple(sorted(required_binding_targets)), "required_binding_targets", nonempty=False)
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
    _strings(value.required_binding_targets, "required_binding_targets", nonempty=False)
    if type(value.selected_set_limit) is not int or value.selected_set_limit < 1:
        raise _fail(RetrievalFailureCode.SELECTION_LIMIT_INVALID, "query selected-set limit changed")
    if type(value.library_snapshot_sha256) is not str or _SHA256_RE.fullmatch(value.library_snapshot_sha256) is None:
        raise _fail(RetrievalFailureCode.MALFORMED_QUERY, "query library snapshot hash is invalid")
    _record(value.lifecycle_snapshot_id, IdentityDomain.LIFECYCLE_SNAPSHOT, "query lifecycle snapshot id")
    if value.retrieval_policy != RETRIEVAL_POLICY_V1:
        raise _fail(RetrievalFailureCode.UNKNOWN_POLICY, "query retrieval policy is unknown")
    if context is not None:
        validate_compatibility_context(context, evaluator=retriever._evaluator)
        if value.compatibility_context_id != context.context_id or value.library_snapshot_sha256 != context.library_snapshot_sha256 or value.lifecycle_snapshot_id != context.lifecycle_snapshot.snapshot_id or not set(value.requested_behavior_kinds) <= set(context.allowed_behavior_kinds) or value.selected_set_limit > context.selected_set_ceiling:
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
        required_binding_targets=tuple(_list(value["required_binding_targets"], "required_binding_targets")),
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
) -> RetrievalCandidateAudit:
    ranking_key = None
    if ranking_feature is not None and descriptor is not None:
        ranking_key = (-ranking_feature.semantic_score_micros, descriptor.content_key.value, descriptor.manifest_id.value)
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
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_candidate_payload(result))
    object.__setattr__(result, "candidate_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_CANDIDATE, canonical_bytes=payload))
    validate_retrieval_candidate_audit(result)
    return result


def validate_retrieval_candidate_audit(value: RetrievalCandidateAudit) -> None:
    if type(value) is not RetrievalCandidateAudit or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate audit is not sealed")
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
    else:
        if value.compatibility_decision_id != value._compatibility_decision.decision_id or value.compatibility_kind is not value._compatibility_decision.decision_kind:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "candidate compatibility identity differs")
    if value._ranking_feature is None:
        if value.ranking_feature_id is not None or value.ranking_key is not None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "ranking absence was conflated")
    else:
        validate_ranking_feature_observation(value._ranking_feature, provider=value._ranking_feature._provider)
        expected_key = (-value._ranking_feature.semantic_score_micros, value._descriptor.content_key.value, value._descriptor.manifest_id.value)
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
        validate_retrieval_candidate_audit(candidate)
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
    if query is not None:
        validate_retrieval_query(query, retriever=retriever, context=context)
        if value.query_id != query.query_id:
            raise _fail(RetrievalFailureCode.CONTEXT_SUBSTITUTION, "retrieval decision belongs to another query")
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
        "created_at_utc": _timestamp_text(value.created_at_utc),
    }


@dataclass(frozen=True, init=False)
class RetrievalLoadDecision:
    schema_version: str
    retrieval_decision_id: RecordId
    selected_candidate_id: RecordId
    descriptor_id: RecordId
    before_loading_revalidation_id: RecordId
    loaded_content_key: str
    loaded_manifest_id: str
    pre_load_snapshot_sha256: str
    post_load_snapshot_sha256: str
    outcome: LoadOutcome
    created_at_utc: datetime
    load_decision_id: RecordId
    _revalidation: CompatibilityRevalidationRecord
    _descriptor: CompatibilitySubjectDescriptor
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RetrievalLoadDecision:
        raise TypeError("RetrievalLoadDecision is retriever-created")

    def to_dict(self) -> dict[str, object]:
        validate_retrieval_load_decision(self)
        return {**_load_payload(self), "load_decision_id": self.load_decision_id.to_dict()}


@dataclass(frozen=True)
class RetrievalResult:
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
    )


def retrieve_and_load(
    *,
    retriever: ConfiguredRetriever,
    context: CompatibilityContext,
    query: RetrievalQuery,
) -> RetrievalResult:
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
            required_targets = set(query.required_binding_targets)
            if required_targets and not required_targets <= {item.ref_id for item in descriptor.binding_refs}:
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
    conflict_scan = evaluate_conflicts(evaluator=retriever._evaluator, context=context, decisions=tuple(decisions), descriptors=tuple(descriptors), proposals=proposals)
    validate_compatibility_conflict_scan(conflict_scan, evaluator=retriever._evaluator)
    audits: list[RetrievalCandidateAudit] = []
    if conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND:
        for entry, descriptor, decision, failure in discovered:
            if descriptor is None or decision is None:
                disposition = CandidateDisposition.POISONED_INDEX if failure is RetrievalFailureCode.DESCRIPTOR_INDEX_MISMATCH else CandidateDisposition.DESCRIPTOR_UNAVAILABLE
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=disposition, failure_code=failure or RetrievalFailureCode.DESCRIPTOR_MISSING, ranking_feature=None))
                continue
            if _is_eligible(decision):
                feature = retriever._ranking_provider.observe(query=query, context=context, descriptor=descriptor)
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=CandidateDisposition.ELIGIBLE, failure_code=None, ranking_feature=feature))
            else:
                audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=CandidateDisposition.REJECTED, failure_code=RetrievalFailureCode.COMPATIBILITY_REJECTED, ranking_feature=None))
    else:
        for entry, descriptor, decision, failure in discovered:
            audits.append(_make_candidate_audit(index_entry=entry, descriptor=descriptor, decision=decision, disposition=CandidateDisposition.REJECTED if descriptor is not None else CandidateDisposition.DESCRIPTOR_UNAVAILABLE, failure_code=failure or (RetrievalFailureCode.CONFLICT_SCAN_INCOMPLETE if conflict_scan.decision_kind is ConflictDecisionKind.SCAN_INCOMPLETE else RetrievalFailureCode.CONFLICT_UNRESOLVED), ranking_feature=None))
    ranked = sorted((item for item in audits if item.ranking_key is not None), key=lambda item: item.ranking_key)
    selected_original = tuple(ranked[: query.selected_set_limit]) if conflict_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND else ()
    selected_ids = {item.candidate_id.value for item in selected_original}
    final_audits = tuple(_mark_selected(item) if item.candidate_id.value in selected_ids else item for item in audits)
    selected = tuple(item for item in final_audits if item.disposition is CandidateDisposition.SELECTED)
    decision = _make_retrieval_decision(retriever=retriever, query=query, context=context, candidates=final_audits, conflict_scan=conflict_scan, selected=selected)
    loads: list[RetrievalLoadDecision] = []
    for candidate in selected:
        compatibility = candidate._compatibility_decision
        descriptor = candidate._descriptor
        if compatibility is None or descriptor is None:
            raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "selected candidate lacks compatibility evidence")
        revalidation = revalidate_before_loading(evaluator=retriever._evaluator, context=context, descriptor=descriptor, original_decision=compatibility)
        try:
            require_revalidation_passed(revalidation, expected_stage=RevalidationStage.BEFORE_LOADING)
        except CompatibilityViolation as exc:
            raise _fail(RetrievalFailureCode.SNAPSHOT_DRIFT, "fresh before-loading compatibility revalidation failed") from exc
        pre_snapshot = _same_snapshot(retriever._library, context.library_snapshot)
        loaded = retriever._library.get_verified_behavior(descriptor.content_key, descriptor.manifest_id)
        validate_verified_behavior_record(loaded)
        if loaded.unit.content_key.value != descriptor.content_key.value or loaded.manifest.manifest_id.value != descriptor.manifest_id.value:
            raise _fail(RetrievalFailureCode.LOADED_IDENTITY_MISMATCH, "verified loaded identity differs from descriptor")
        post_snapshot = _same_snapshot(retriever._library, context.library_snapshot)
        loads.append(_make_load_decision(retriever=retriever, retrieval_decision=decision, candidate=candidate, descriptor=descriptor, revalidation=revalidation, loaded=loaded, pre_snapshot=pre_snapshot, post_snapshot=post_snapshot))
    return RetrievalResult(decision, tuple(loads))


def _make_load_decision(
    *,
    retriever: ConfiguredRetriever,
    retrieval_decision: RetrievalDecision,
    candidate: RetrievalCandidateAudit,
    descriptor: CompatibilitySubjectDescriptor,
    revalidation: CompatibilityRevalidationRecord,
    loaded: VerifiedBehaviorRecord,
    pre_snapshot: LibrarySnapshot,
    post_snapshot: LibrarySnapshot,
) -> RetrievalLoadDecision:
    result = object.__new__(RetrievalLoadDecision)
    object.__setattr__(result, "schema_version", RETRIEVAL_LOAD_DECISION_V1)
    object.__setattr__(result, "retrieval_decision_id", retrieval_decision.decision_id)
    object.__setattr__(result, "selected_candidate_id", candidate.candidate_id)
    object.__setattr__(result, "descriptor_id", descriptor.descriptor_id)
    object.__setattr__(result, "before_loading_revalidation_id", revalidation.revalidation_id)
    object.__setattr__(result, "loaded_content_key", loaded.unit.content_key.value)
    object.__setattr__(result, "loaded_manifest_id", loaded.manifest.manifest_id.value)
    object.__setattr__(result, "pre_load_snapshot_sha256", hashlib.sha256(_canonical(pre_snapshot.to_dict())).hexdigest())
    object.__setattr__(result, "post_load_snapshot_sha256", hashlib.sha256(_canonical(post_snapshot.to_dict())).hexdigest())
    object.__setattr__(result, "outcome", LoadOutcome.VERIFIED_LOADED)
    object.__setattr__(result, "created_at_utc", _timestamp(retriever._trusted_clock(), "load decision timestamp"))
    object.__setattr__(result, "_revalidation", revalidation)
    object.__setattr__(result, "_descriptor", descriptor)
    object.__setattr__(result, "_trusted_seal", _SEAL)
    payload = _canonical(_load_payload(result))
    object.__setattr__(result, "load_decision_id", compute_record_id(domain=IdentityDomain.RETRIEVAL_LOAD_DECISION, canonical_bytes=payload))
    validate_retrieval_load_decision(result)
    return result


def validate_retrieval_load_decision(value: RetrievalLoadDecision) -> None:
    if type(value) is not RetrievalLoadDecision or getattr(value, "_trusted_seal", None) is not _SEAL:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load decision is not sealed")
    if value.schema_version != RETRIEVAL_LOAD_DECISION_V1 or type(value.schema_version) is not str or value.outcome is not LoadOutcome.VERIFIED_LOADED:
        raise _fail(RetrievalFailureCode.UNKNOWN_SCHEMA, "load decision schema or outcome is invalid")
    _record(value.retrieval_decision_id, IdentityDomain.RETRIEVAL_DECISION, "load retrieval decision id")
    _record(value.selected_candidate_id, IdentityDomain.RETRIEVAL_CANDIDATE, "load selected candidate id")
    _record(value.descriptor_id, IdentityDomain.COMPATIBILITY_SUBJECT_DESCRIPTOR, "load descriptor id")
    _record(value.before_loading_revalidation_id, IdentityDomain.COMPATIBILITY_REVALIDATION, "load revalidation id")
    validate_compatibility_revalidation_record(value._revalidation)
    validate_compatibility_subject_descriptor(value._descriptor)
    if value._revalidation.revalidation_id != value.before_loading_revalidation_id or value._revalidation.stage is not RevalidationStage.BEFORE_LOADING or value._descriptor.descriptor_id != value.descriptor_id:
        raise _fail(RetrievalFailureCode.LOADING_FORBIDDEN, "load decision nested evidence differs")
    _safe_id(value.loaded_content_key, "loaded content key")
    _safe_id(value.loaded_manifest_id, "loaded manifest id")
    if value.loaded_content_key != value._descriptor.content_key.value or value.loaded_manifest_id != value._descriptor.manifest_id.value:
        raise _fail(RetrievalFailureCode.LOADED_IDENTITY_MISMATCH, "load decision identity differs from descriptor")
    for digest in (value.pre_load_snapshot_sha256, value.post_load_snapshot_sha256):
        if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load snapshot hash is invalid")
    if value.pre_load_snapshot_sha256 != value.post_load_snapshot_sha256:
        raise _fail(RetrievalFailureCode.SNAPSHOT_DRIFT, "library changed during verified load")
    _timestamp(value.created_at_utc, "load timestamp")
    payload = _canonical(_load_payload(value))
    _record(value.load_decision_id, IdentityDomain.RETRIEVAL_LOAD_DECISION, "load decision id")
    try:
        validate_record_id(value.load_decision_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(RetrievalFailureCode.TRUSTED_RECORD_FORGED, "load decision identity mismatch") from exc


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
    if load_decision.retrieval_decision_id != retrieval_decision.decision_id:
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "load belongs to another retrieval decision")
    candidate = next((item for item in retrieval_decision.considered_candidates if item.candidate_id == load_decision.selected_candidate_id), None)
    if candidate is None or candidate._descriptor is None or candidate._compatibility_decision is None:
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "selected candidate evidence is unavailable")
    record = revalidate_before_consumption(
        evaluator=retriever._evaluator,
        context=context,
        descriptor=candidate._descriptor,
        original_decision=candidate._compatibility_decision,
        before_loading=load_decision._revalidation,
    )
    try:
        require_revalidation_passed(record, expected_stage=RevalidationStage.BEFORE_CONSUMPTION)
    except CompatibilityViolation as exc:
        raise _fail(RetrievalFailureCode.CONSUMPTION_REVALIDATION_FAILED, "fresh before-consumption compatibility revalidation failed") from exc
    return record


__all__ = (
    "RETRIEVAL_QUERY_V1", "RETRIEVAL_CANDIDATE_V1", "RANKING_FEATURE_OBSERVATION_V1",
    "RETRIEVAL_CONFLICT_RECORD_V1", "RETRIEVAL_DECISION_V1", "RETRIEVAL_LOAD_DECISION_V1",
    "RETRIEVAL_POLICY_V1", "RANKING_PROFILE_V1", "RETRIEVAL_MEDIA_TYPE_V1",
    "RetrievalFailureCode", "RetrievalViolation", "CandidateDisposition", "RetrievalOutcome",
    "LoadOutcome", "RetrievalQuery", "RankingFeatureObservation",
    "ConfiguredRankingFeatureProvider", "ConfiguredRetriever", "RetrievalCandidateAudit",
    "RetrievalConflictRecord",
    "RetrievalDecision", "RetrievalLoadDecision", "RetrievalResult",
    "configure_ranking_feature_provider", "require_configured_ranking_feature_provider",
    "validate_ranking_feature_observation", "configure_retriever", "require_configured_retriever",
    "create_retrieval_query", "validate_retrieval_query", "retrieval_query_from_dict",
    "validate_retrieval_candidate_audit", "validate_retrieval_conflict_record",
    "validate_retrieval_decision",
    "retrieve_and_load", "validate_retrieval_load_decision",
    "revalidate_loaded_before_consumption",
)
