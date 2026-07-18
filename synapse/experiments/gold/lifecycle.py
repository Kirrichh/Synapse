"""Append-only Stage 4 lifecycle, revocation, and supersession authority.

The journal is immutable history.  A configured platform writer appends
records under a non-blocking store lock and compare-and-swap predecessor.
Revocation and supersession are separate, proof-bound authority decisions;
neither operation deletes or rewrites prior records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Callable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    AuthorityRole,
    AuthorityIdentity,
    ContractViolation,
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    IndependenceProof,
    LifecycleReasonCode,
    ProposalId,
    RecordId,
    ReasonCode,
    SchemaVersion,
    Stage4AuthorityHandle,
    authority_decision_id_from_dict,
    compute_authority_decision_id,
    compute_proposal_id,
    compute_record_id,
    create_history_anchor,
    create_independence_proof,
    independence_proof_from_dict,
    record_id_from_dict,
    require_stage4_authority_handle,
    validate_history_anchor_extension,
    validate_independence_proof,
    validate_record_id,
)
from .persistence import (
    ExclusiveStoreLock,
    append_journal_payload,
    ensure_directory,
    initialize_journal,
    scan_journal,
)


LIFECYCLE_CONTEXT_V1 = "synapse.stage4.gold.lifecycle-context/v1"
LIFECYCLE_AUTHORITY_PROPOSAL_V1 = "synapse.stage4.gold.lifecycle-authority-proposal/v1"
LIFECYCLE_HEAD_V1 = "synapse.stage4.gold.lifecycle-head/v1"
LIFECYCLE_LOG_ROOT_PROFILE_V1 = "synapse.stage4.gold.lifecycle-log-root/v1"
LIFECYCLE_JOURNAL_NAME_V1 = "lifecycle-v1.journal"
LIFECYCLE_LOCK_NAME_V1 = "lifecycle-v1.lock"

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_RECORD_ID_PREFIX = IdentityDomain.LIFECYCLE_RECORD.value + ":"
_AUTHORITY_ID_PREFIX = IdentityDomain.AUTHORITY_DECISION.value + ":"
_TRUSTED_SEAL = object()
_TRUSTED_STORE_SEAL = object()
_TRUSTED_EVALUATOR_SEAL = object()
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class LifecycleFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    UNKNOWN_SCOPE = "UNKNOWN_SCOPE"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    UNSUPPORTED_TRANSITION = "UNSUPPORTED_TRANSITION"
    REASON_MISMATCH = "REASON_MISMATCH"
    MISSING_PREDECESSOR = "MISSING_PREDECESSOR"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"
    HISTORY_FORK = "HISTORY_FORK"
    HISTORY_ROLLBACK = "HISTORY_ROLLBACK"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    AUTHORITY_NOT_INDEPENDENT = "AUTHORITY_NOT_INDEPENDENT"
    REPLACEMENT_REQUIRED = "REPLACEMENT_REQUIRED"
    REPLACEMENT_FORBIDDEN = "REPLACEMENT_FORBIDDEN"
    GLOBAL_HUMAN_AUTHORITY_REQUIRED = "GLOBAL_HUMAN_AUTHORITY_REQUIRED"
    REF_KIND_MISMATCH = "REF_KIND_MISMATCH"
    RECORD_NOT_CONSUMABLE = "RECORD_NOT_CONSUMABLE"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    HISTORY_ANCHOR_REQUIRED = "HISTORY_ANCHOR_REQUIRED"
    GOVERNING_HUMAN_UNAVAILABLE = "GOVERNING_HUMAN_UNAVAILABLE"
    DECISION_ALREADY_APPLIED = "DECISION_ALREADY_APPLIED"
    AUTHORITY_HISTORY_FORK = "AUTHORITY_HISTORY_FORK"


class LifecycleViolation(RuntimeError):
    def __init__(self, failure_code: LifecycleFailureCode, detail: str) -> None:
        if type(failure_code) is not LifecycleFailureCode:
            raise TypeError("failure_code must be exact LifecycleFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: LifecycleFailureCode, detail: str) -> LifecycleViolation:
    return LifecycleViolation(code, detail)


class LifecycleState(str, Enum):
    OBSERVED = "OBSERVED"
    EXTRACTED = "EXTRACTED"
    DISTILLED = "DISTILLED"
    VALIDATED = "VALIDATED"
    ATTESTED = "ATTESTED"
    ADMITTED = "ADMITTED"
    INDEXED = "INDEXED"
    RETRIEVED = "RETRIEVED"
    REVALIDATED = "REVALIDATED"
    REPLAYED = "REPLAYED"
    CONSUMED = "CONSUMED"
    OUTCOME_LINKED = "OUTCOME_LINKED"
    REJECTED = "REJECTED"
    CONFLICTING = "CONFLICTING"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"
    REVOKED = "REVOKED"
    INCOMPATIBLE = "INCOMPATIBLE"
    QUARANTINED = "QUARANTINED"


class LifecycleScope(str, Enum):
    REVISION = "REVISION"
    BINDING = "BINDING"
    TASK_CLASS = "TASK_CLASS"
    POLICY = "POLICY"
    ENVIRONMENT = "ENVIRONMENT"
    GLOBAL = "GLOBAL"


class LifecycleAuthorityAction(str, Enum):
    SUPERSEDE = "SUPERSEDE"
    WITHDRAW = "WITHDRAW"
    REVOKE = "REVOKE"


class SupersessionDecisionKind(str, Enum):
    SUPERSEDE = "SUPERSEDE"
    WITHDRAW = "WITHDRAW"
    REJECT_SUPERSESSION = "REJECT_SUPERSESSION"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class LifecycleDecisionReason(str, Enum):
    SUPERSESSION_APPROVED = "SUPERSESSION_APPROVED"
    WITHDRAWAL_APPROVED = "WITHDRAWAL_APPROVED"
    SUPERSESSION_REJECTED = "SUPERSESSION_REJECTED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    REVOCATION_APPROVED = "REVOCATION_APPROVED"


_NORMAL_TRANSITIONS: dict[LifecycleState | None, tuple[LifecycleState, LifecycleReasonCode]] = {
    None: (LifecycleState.OBSERVED, LifecycleReasonCode.PLATFORM_OBSERVATION),
    LifecycleState.OBSERVED: (LifecycleState.EXTRACTED, LifecycleReasonCode.EXTRACTION_COMPLETED),
    LifecycleState.EXTRACTED: (LifecycleState.DISTILLED, LifecycleReasonCode.DISTILLATION_COMPLETED),
    LifecycleState.DISTILLED: (LifecycleState.VALIDATED, LifecycleReasonCode.VALIDATION_PASSED),
    LifecycleState.VALIDATED: (LifecycleState.ATTESTED, LifecycleReasonCode.ATTESTATION_BOUND),
    LifecycleState.ATTESTED: (LifecycleState.ADMITTED, LifecycleReasonCode.PUBLICATION_ADMITTED),
    LifecycleState.ADMITTED: (LifecycleState.INDEXED, LifecycleReasonCode.INDEX_COMMITTED),
    LifecycleState.INDEXED: (LifecycleState.RETRIEVED, LifecycleReasonCode.RETRIEVAL_SELECTED),
    LifecycleState.RETRIEVED: (LifecycleState.REVALIDATED, LifecycleReasonCode.REVALIDATION_PASSED),
    LifecycleState.REVALIDATED: (LifecycleState.REPLAYED, LifecycleReasonCode.REPLAY_COMPLETED),
    LifecycleState.REPLAYED: (LifecycleState.CONSUMED, LifecycleReasonCode.CONSUMPTION_COMPLETED),
    LifecycleState.CONSUMED: (LifecycleState.OUTCOME_LINKED, LifecycleReasonCode.OUTCOME_LINKED),
}
_EXCEPTION_REASONS = {
    LifecycleState.REJECTED: LifecycleReasonCode.POLICY_REJECTED,
    LifecycleState.CONFLICTING: LifecycleReasonCode.CONFLICT_DETECTED,
    LifecycleState.STALE: LifecycleReasonCode.STALE_CONTEXT,
    LifecycleState.REVOKED: LifecycleReasonCode.REVOCATION_APPROVED,
    LifecycleState.INCOMPATIBLE: LifecycleReasonCode.CONTEXT_INCOMPATIBLE,
    LifecycleState.QUARANTINED: LifecycleReasonCode.CORRUPTION_QUARANTINE,
}
_TERMINAL_STATES = {
    LifecycleState.REJECTED,
    LifecycleState.SUPERSEDED,
    LifecycleState.REVOKED,
    LifecycleState.INCOMPATIBLE,
    LifecycleState.QUARANTINED,
}
_CONSUMPTION_BLOCKING_STATES = {
    LifecycleState.REJECTED,
    LifecycleState.CONFLICTING,
    LifecycleState.SUPERSEDED,
    LifecycleState.STALE,
    LifecycleState.REVOKED,
    LifecycleState.INCOMPATIBLE,
    LifecycleState.QUARANTINED,
}


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID)


def _decode(value: bytes) -> object:
    return decode_stage4_canonical_bytes(value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID)


def _handle(value: Stage4AuthorityHandle, *, expected: Stage4AuthorityHandle | None = None):
    try:
        return require_stage4_authority_handle(value, expected_handle=expected)
    except ContractViolation as exc:
        raise _fail(LifecycleFailureCode.WRONG_AUTHORITY_HANDLE, "authority handle is invalid") from exc


def _configuration_id(value: object) -> RecordId:
    if type(value) is not RecordId or value.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "configuration identity is invalid")
    return value


def _version(value: object, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*", value) is None:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _format_timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, "created_at must be timezone-aware UTC")
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, "created_at must be an exact string")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, "created_at is not canonical UTC") from exc
    if _format_timestamp(parsed) != value:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, "created_at is not canonical UTC")
    return parsed


def _exact_dict(value: object, fields: tuple[str, ...], name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be an exact dict")
    if any(type(key) is not str for key in value) or set(value) != set(fields):
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, f"{name} fields are invalid")
    return value


def _exact_list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be an exact list")
    return value


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _actor(value: object, name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be ActorIdentity")
    try:
        return ActorIdentity.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} is invalid") from exc


def _actors(value: object, name: str, *, nonempty: bool) -> tuple[ActorIdentity, ...]:
    if type(value) not in (tuple, list):
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    result = tuple(_actor(item, name) for item in value)
    if nonempty and not result:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must not be empty")
    if len({item.value for item in result}) != len(result):
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} contains duplicates")
    return tuple(sorted(result, key=lambda item: item.value))


def _ref(value: object, expected_kind: RefKind | None, name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be HashBoundRef")
    try:
        result = HashBoundRef.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.REF_KIND_MISMATCH, f"{name} is invalid") from exc
    if expected_kind is not None and result.kind is not expected_kind:
        raise _fail(LifecycleFailureCode.REF_KIND_MISMATCH, f"{name} has the wrong kind")
    return result


def _refs(value: object, expected_kind: RefKind, name: str, *, nonempty: bool) -> tuple[HashBoundRef, ...]:
    if type(value) not in (tuple, list):
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    result = tuple(_ref(item, expected_kind, name) for item in value)
    if nonempty and not result:
        raise _fail(LifecycleFailureCode.REF_KIND_MISMATCH, f"{name} must not be empty")
    if len({item.ref_id for item in result}) != len(result) or len({item.sha256 for item in result}) != len(result):
        raise _fail(LifecycleFailureCode.REF_KIND_MISMATCH, f"{name} contains duplicate refs")
    return tuple(sorted(result, key=lambda item: item.ref_id))


def _record_text(value: object, prefix: str, name: str, *, allow_none: bool) -> str | None:
    if allow_none and value is None:
        return None
    if type(value) is not str or not value.startswith(prefix) or len(value) != len(prefix) + 64:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} is malformed")
    if _SHA256_RE.fullmatch(value[len(prefix):]) is None:
        raise _fail(LifecycleFailureCode.INVALID_IDENTIFIER, f"{name} is malformed")
    return value


@dataclass(frozen=True)
class LifecycleContext:
    schema_version: str
    scope: LifecycleScope
    context_id: str

    def __post_init__(self) -> None:
        _validate_context(self)

    def to_dict(self) -> dict[str, str]:
        _validate_context(self)
        return {"schema_version": self.schema_version, "scope": self.scope.value, "context_id": self.context_id}

    @classmethod
    def from_dict(cls, value: object) -> LifecycleContext:
        data = _exact_dict(value, ("schema_version", "scope", "context_id"), "lifecycle_context")
        try:
            scope = LifecycleScope(data["scope"])
        except (TypeError, ValueError) as exc:
            raise _fail(LifecycleFailureCode.UNKNOWN_SCOPE, "lifecycle scope is unknown") from exc
        return cls(data["schema_version"], scope, data["context_id"])


def _validate_context(value: LifecycleContext) -> None:
    if type(value) is not LifecycleContext:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "context must be exact LifecycleContext")
    if value.schema_version != LIFECYCLE_CONTEXT_V1 or type(value.schema_version) is not str:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "context schema is unknown")
    if type(value.scope) is not LifecycleScope:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCOPE, "context scope is unknown")
    if value.scope is LifecycleScope.GLOBAL:
        if value.context_id != "GLOBAL" or type(value.context_id) is not str:
            raise _fail(LifecycleFailureCode.CONTEXT_MISMATCH, "GLOBAL scope requires exact GLOBAL context")
    else:
        _safe_id(value.context_id, "context_id")


@dataclass(frozen=True, init=False)
class LifecycleAuthorityProposal:
    schema_version: str
    action: LifecycleAuthorityAction
    subject_ref: HashBoundRef
    replacement_ref: HashBoundRef | None
    context: LifecycleContext
    proposer_identity: ActorIdentity
    producer_actor_ids: tuple[ActorIdentity, ...]
    source_actor_ids: tuple[ActorIdentity, ...]
    evidence_refs: tuple[HashBoundRef, ...]
    compatibility_refs: tuple[HashBoundRef, ...]
    policy_refs: tuple[HashBoundRef, ...]
    reason_codes: tuple[str, ...]
    predecessor_decision_id: str | None
    decision_sequence: int
    proposal_id: ProposalId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> LifecycleAuthorityProposal:
        raise TypeError("LifecycleAuthorityProposal is created only by its validated factory")

    def to_dict(self) -> dict[str, object]:
        validate_lifecycle_authority_proposal(self)
        return {**_authority_proposal_payload(self), "proposal_id": self.proposal_id.to_dict()}


def _reason_codes(value: object) -> tuple[str, ...]:
    if type(value) not in (tuple, list):
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "reason_codes must be tuple or list")
    result = tuple(_safe_id(item, "reason_code") for item in value)
    if not result or len(set(result)) != len(result):
        raise _fail(LifecycleFailureCode.REASON_MISMATCH, "reason_codes must be non-empty and unique")
    return tuple(sorted(result))


def _authority_proposal_payload(value: LifecycleAuthorityProposal) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "action": value.action.value,
        "subject_ref": value.subject_ref.to_dict(),
        "replacement_ref": None if value.replacement_ref is None else value.replacement_ref.to_dict(),
        "context": value.context.to_dict(),
        "proposer_identity": value.proposer_identity.to_dict(),
        "producer_actor_ids": [item.to_dict() for item in value.producer_actor_ids],
        "source_actor_ids": [item.to_dict() for item in value.source_actor_ids],
        "evidence_refs": [item.to_dict() for item in value.evidence_refs],
        "compatibility_refs": [item.to_dict() for item in value.compatibility_refs],
        "policy_refs": [item.to_dict() for item in value.policy_refs],
        "reason_codes": list(value.reason_codes),
        "predecessor_decision_id": value.predecessor_decision_id,
        "decision_sequence": value.decision_sequence,
    }


def create_lifecycle_authority_proposal(
    *,
    action: LifecycleAuthorityAction,
    subject_ref: HashBoundRef,
    replacement_ref: HashBoundRef | None,
    context: LifecycleContext,
    proposer_identity: ActorIdentity,
    producer_actor_ids: object,
    source_actor_ids: object,
    evidence_refs: object,
    compatibility_refs: object,
    policy_refs: object,
    reason_codes: object,
    predecessor_decision_id: str | None,
    decision_sequence: int,
) -> LifecycleAuthorityProposal:
    if type(action) is not LifecycleAuthorityAction:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "authority action is unknown")
    subject = _ref(subject_ref, None, "subject_ref")
    replacement = None if replacement_ref is None else _ref(replacement_ref, None, "replacement_ref")
    if action is LifecycleAuthorityAction.SUPERSEDE:
        if replacement is None:
            raise _fail(LifecycleFailureCode.REPLACEMENT_REQUIRED, "SUPERSEDE requires an exact replacement")
        if replacement == subject:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "replacement must differ from subject")
    elif replacement is not None:
        raise _fail(LifecycleFailureCode.REPLACEMENT_FORBIDDEN, "WITHDRAW and REVOKE forbid replacement")
    _validate_context(context)
    if type(decision_sequence) is not int or decision_sequence < 1:
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "decision sequence must be positive")
    predecessor = _record_text(predecessor_decision_id, _AUTHORITY_ID_PREFIX, "predecessor_decision_id", allow_none=True)
    if (decision_sequence == 1) != (predecessor is None):
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "first decision alone has no predecessor")
    result = object.__new__(LifecycleAuthorityProposal)
    object.__setattr__(result, "schema_version", LIFECYCLE_AUTHORITY_PROPOSAL_V1)
    object.__setattr__(result, "action", action)
    object.__setattr__(result, "subject_ref", subject)
    object.__setattr__(result, "replacement_ref", replacement)
    object.__setattr__(result, "context", LifecycleContext.from_dict(context.to_dict()))
    object.__setattr__(result, "proposer_identity", _actor(proposer_identity, "proposer_identity"))
    object.__setattr__(result, "producer_actor_ids", _actors(producer_actor_ids, "producer_actor_ids", nonempty=True))
    object.__setattr__(result, "source_actor_ids", _actors(source_actor_ids, "source_actor_ids", nonempty=True))
    object.__setattr__(result, "evidence_refs", _refs(evidence_refs, RefKind.SOURCE_EVIDENCE, "evidence_refs", nonempty=True))
    object.__setattr__(result, "compatibility_refs", _refs(compatibility_refs, RefKind.SOURCE_EVIDENCE, "compatibility_refs", nonempty=False))
    object.__setattr__(result, "policy_refs", _refs(policy_refs, RefKind.CONTRACT_CONDITION, "policy_refs", nonempty=True))
    object.__setattr__(result, "reason_codes", _reason_codes(reason_codes))
    object.__setattr__(result, "predecessor_decision_id", predecessor)
    object.__setattr__(result, "decision_sequence", decision_sequence)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    object.__setattr__(result, "proposal_id", compute_proposal_id(canonical_bytes=_canonical(_authority_proposal_payload(result))))
    validate_lifecycle_authority_proposal(result)
    return result


def validate_lifecycle_authority_proposal(value: LifecycleAuthorityProposal) -> None:
    if type(value) is not LifecycleAuthorityProposal or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(LifecycleFailureCode.TRUSTED_OBJECT_FORGED, "authority proposal is not factory sealed")
    if value.schema_version != LIFECYCLE_AUTHORITY_PROPOSAL_V1 or type(value.action) is not LifecycleAuthorityAction:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "authority proposal schema or action is unknown")
    _ref(value.subject_ref, None, "subject_ref")
    if value.action is LifecycleAuthorityAction.SUPERSEDE:
        if value.replacement_ref is None or _ref(value.replacement_ref, None, "replacement_ref") == value.subject_ref:
            raise _fail(LifecycleFailureCode.REPLACEMENT_REQUIRED, "SUPERSEDE replacement is invalid")
    elif value.replacement_ref is not None:
        raise _fail(LifecycleFailureCode.REPLACEMENT_FORBIDDEN, "replacement is forbidden for this action")
    _validate_context(value.context)
    _actor(value.proposer_identity, "proposer_identity")
    _actors(value.producer_actor_ids, "producer_actor_ids", nonempty=True)
    _actors(value.source_actor_ids, "source_actor_ids", nonempty=True)
    _refs(value.evidence_refs, RefKind.SOURCE_EVIDENCE, "evidence_refs", nonempty=True)
    _refs(value.compatibility_refs, RefKind.SOURCE_EVIDENCE, "compatibility_refs", nonempty=False)
    _refs(value.policy_refs, RefKind.CONTRACT_CONDITION, "policy_refs", nonempty=True)
    _reason_codes(value.reason_codes)
    predecessor = _record_text(value.predecessor_decision_id, _AUTHORITY_ID_PREFIX, "predecessor_decision_id", allow_none=True)
    if type(value.decision_sequence) is not int or value.decision_sequence < 1 or ((value.decision_sequence == 1) != (predecessor is None)):
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "authority decision chain is invalid")
    expected = compute_proposal_id(canonical_bytes=_canonical(_authority_proposal_payload(value)))
    if type(value.proposal_id) is not ProposalId or value.proposal_id != expected:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "authority proposal identity mismatch")


def lifecycle_authority_proposal_from_dict(value: object) -> LifecycleAuthorityProposal:
    fields = (
        "schema_version", "action", "subject_ref", "replacement_ref", "context", "proposer_identity",
        "producer_actor_ids", "source_actor_ids", "evidence_refs", "compatibility_refs", "policy_refs",
        "reason_codes", "predecessor_decision_id", "decision_sequence", "proposal_id",
    )
    data = _exact_dict(value, fields, "lifecycle_authority_proposal")
    if data["schema_version"] != LIFECYCLE_AUTHORITY_PROPOSAL_V1 or type(data["schema_version"]) is not str:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "authority proposal schema is unknown")
    try:
        action = LifecycleAuthorityAction(data["action"])
    except (TypeError, ValueError) as exc:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "authority action is unknown") from exc
    result = create_lifecycle_authority_proposal(
        action=action,
        subject_ref=HashBoundRef.from_dict(data["subject_ref"]),
        replacement_ref=None if data["replacement_ref"] is None else HashBoundRef.from_dict(data["replacement_ref"]),
        context=LifecycleContext.from_dict(data["context"]),
        proposer_identity=ActorIdentity.from_dict(data["proposer_identity"]),
        producer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["producer_actor_ids"], "producer_actor_ids")),
        source_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["source_actor_ids"], "source_actor_ids")),
        evidence_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["evidence_refs"], "evidence_refs")),
        compatibility_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["compatibility_refs"], "compatibility_refs")),
        policy_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["policy_refs"], "policy_refs")),
        reason_codes=tuple(_exact_list(data["reason_codes"], "reason_codes")),
        predecessor_decision_id=data["predecessor_decision_id"],
        decision_sequence=data["decision_sequence"],
    )
    supplied = data["proposal_id"]
    if supplied != result.proposal_id.to_dict():
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport proposal identity mismatch")
    return result


@dataclass(frozen=True, init=False)
class SupersessionDecision:
    schema_version: SchemaVersion
    configuration_id: RecordId
    decision_kind: SupersessionDecisionKind
    proposal: LifecycleAuthorityProposal
    policy_version: str
    reason_code: LifecycleDecisionReason
    created_at: datetime
    independence_proof: IndependenceProof
    decision_id: AuthorityDecisionId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SupersessionDecision:
        raise TypeError("SupersessionDecision is created only by independent authority evaluation")

    def to_dict(self) -> dict[str, object]:
        validate_supersession_decision(self)
        return {
            "schema_version": self.schema_version.value,
            "configuration_id": self.configuration_id.to_dict(),
            "decision_kind": self.decision_kind.value,
            "proposal": self.proposal.to_dict(),
            "policy_version": self.policy_version,
            "reason_code": self.reason_code.value,
            "created_at": _format_timestamp(self.created_at),
            "independence_proof": self.independence_proof.to_dict(),
            "decision_id": self.decision_id.to_dict(),
        }


@dataclass(frozen=True, init=False)
class RevocationDecision:
    schema_version: SchemaVersion
    configuration_id: RecordId
    proposal: LifecycleAuthorityProposal
    policy_version: str
    reason_code: LifecycleDecisionReason
    created_at: datetime
    independence_proof: IndependenceProof
    decision_id: AuthorityDecisionId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RevocationDecision:
        raise TypeError("RevocationDecision is created only by independent authority evaluation")

    def to_dict(self) -> dict[str, object]:
        validate_revocation_decision(self)
        return {
            "schema_version": self.schema_version.value,
            "configuration_id": self.configuration_id.to_dict(),
            "proposal": self.proposal.to_dict(),
            "policy_version": self.policy_version,
            "reason_code": self.reason_code.value,
            "created_at": _format_timestamp(self.created_at),
            "independence_proof": self.independence_proof.to_dict(),
            "decision_id": self.decision_id.to_dict(),
        }


def _decision_payload(value: SupersessionDecision | RevocationDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "decision_kind": (
            value.decision_kind.value
            if type(value) is SupersessionDecision
            else LifecycleAuthorityAction.REVOKE.value
        ),
        "action": value.proposal.action.value,
        "proposal_id": value.proposal.proposal_id.to_dict(),
        "subject_ref": value.proposal.subject_ref.to_dict(),
        "replacement_ref": None if value.proposal.replacement_ref is None else value.proposal.replacement_ref.to_dict(),
        "context": value.proposal.context.to_dict(),
        "policy_version": value.policy_version,
        "reason_code": value.reason_code.value,
        "created_at": _format_timestamp(value.created_at),
        "predecessor_decision_id": value.proposal.predecessor_decision_id,
        "decision_sequence": value.proposal.decision_sequence,
    }


def _validate_proof_for_proposal(
    proposal: LifecycleAuthorityProposal,
    proof: IndependenceProof,
    expected_role: AuthorityRole,
    *,
    global_human_required: bool,
) -> None:
    validate_independence_proof(proof)
    required_role = AuthorityRole.GOVERNING_HUMAN if global_human_required else expected_role
    if proof.authority_role is not required_role:
        code = LifecycleFailureCode.GLOBAL_HUMAN_AUTHORITY_REQUIRED if proposal.context.scope is LifecycleScope.GLOBAL else LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT
        raise _fail(code, "authority role does not match lifecycle decision scope")
    if proof.subject_proposal_id != proposal.proposal_id or proof.proposer_identity != proposal.proposer_identity:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "independence proof is bound to another proposal")
    if proof.producer_actor_ids != proposal.producer_actor_ids or proof.source_actor_ids != proposal.source_actor_ids:
        raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "independence proof actor coverage differs from proposal")


class ConfiguredLifecycleAuthorityEvaluator:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_EVALUATOR_SEAL or kwargs or len(args) != 3:
            raise TypeError("ConfiguredLifecycleAuthorityEvaluator is created only by its factory")
        authority_handle, policy_version, trusted_clock = args
        _handle(authority_handle)
        self._authority_handle = authority_handle
        self._policy_version = _version(policy_version, "policy_version")
        if not callable(trusted_clock):
            raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
        self._trusted_clock = trusted_clock

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    def decide_supersession(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        proposal: LifecycleAuthorityProposal,
        decision_kind: SupersessionDecisionKind,
        executor_identity: ActorIdentity | None = None,
        subject_derived_actor_ids: tuple[ActorIdentity, ...] = (),
    ) -> SupersessionDecision:
        self.require_handle(authority_handle)
        return _seal_supersession_decision(
            authority_handle=authority_handle,
            evaluator=self,
            proposal=proposal,
            decision_kind=decision_kind,
            executor_identity=executor_identity,
            subject_derived_actor_ids=subject_derived_actor_ids,
            created_at=self._trusted_clock(),
        )

    def decide_revocation(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        proposal: LifecycleAuthorityProposal,
        executor_identity: ActorIdentity | None = None,
        subject_derived_actor_ids: tuple[ActorIdentity, ...] = (),
    ) -> RevocationDecision:
        self.require_handle(authority_handle)
        return _seal_revocation_decision(
            authority_handle=authority_handle,
            evaluator=self,
            proposal=proposal,
            executor_identity=executor_identity,
            subject_derived_actor_ids=subject_derived_actor_ids,
            created_at=self._trusted_clock(),
        )


def configure_lifecycle_authority_evaluator(
    *,
    authority_handle: Stage4AuthorityHandle,
    policy_version: str,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredLifecycleAuthorityEvaluator:
    _handle(authority_handle)
    return ConfiguredLifecycleAuthorityEvaluator(
        authority_handle,
        policy_version,
        trusted_clock,
        _seal=_TRUSTED_EVALUATOR_SEAL,
    )


def _configured_proof(
    *,
    configuration: object,
    proposal: LifecycleAuthorityProposal,
    authority_identity: AuthorityIdentity,
    role: AuthorityRole,
    reason: ReasonCode,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
) -> IndependenceProof:
    return create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal.proposal_id,
        authority_identity=authority_identity,
        authority_role=role,
        reason_code=reason,
        producer_actor_ids=proposal.producer_actor_ids,
        source_actor_ids=proposal.source_actor_ids,
        proposer_identity=proposal.proposer_identity,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
        delegation_chain=(),
    )


def _seal_supersession_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredLifecycleAuthorityEvaluator,
    proposal: LifecycleAuthorityProposal,
    decision_kind: SupersessionDecisionKind,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
    created_at: datetime,
) -> SupersessionDecision:
    configuration = _handle(authority_handle)
    evaluator.require_handle(authority_handle)
    validate_lifecycle_authority_proposal(proposal)
    if proposal.action not in (LifecycleAuthorityAction.SUPERSEDE, LifecycleAuthorityAction.WITHDRAW):
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession authority cannot issue revocation")
    if type(decision_kind) is not SupersessionDecisionKind:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession decision kind is unknown")
    if decision_kind is SupersessionDecisionKind.SUPERSEDE and proposal.action is not LifecycleAuthorityAction.SUPERSEDE:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "SUPERSEDE decision requires SUPERSEDE proposal")
    if decision_kind is SupersessionDecisionKind.WITHDRAW and proposal.action is not LifecycleAuthorityAction.WITHDRAW:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "WITHDRAW decision requires WITHDRAW proposal")
    positive = decision_kind in (SupersessionDecisionKind.SUPERSEDE, SupersessionDecisionKind.WITHDRAW)
    if positive and proposal.context.scope is LifecycleScope.GLOBAL:
        if configuration.governing_human_authority is None:
            raise _fail(LifecycleFailureCode.GOVERNING_HUMAN_UNAVAILABLE, "positive GLOBAL decision requires configured governing human")
        authority = configuration.governing_human_authority
        role = AuthorityRole.GOVERNING_HUMAN
        reason = ReasonCode.GOVERNING_HUMAN_INDEPENDENT
    else:
        authority = configuration.supersession_reviewer_authority
        role = AuthorityRole.SUPERSESSION_REVIEWER
        reason = ReasonCode.SUPERSESSION_REVIEW_INDEPENDENT
    proof = _configured_proof(
        configuration=configuration,
        proposal=proposal,
        authority_identity=authority,
        role=role,
        reason=reason,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
    )
    reasons = {
        SupersessionDecisionKind.SUPERSEDE: LifecycleDecisionReason.SUPERSESSION_APPROVED,
        SupersessionDecisionKind.WITHDRAW: LifecycleDecisionReason.WITHDRAWAL_APPROVED,
        SupersessionDecisionKind.REJECT_SUPERSESSION: LifecycleDecisionReason.SUPERSESSION_REJECTED,
        SupersessionDecisionKind.REQUIRE_HUMAN_REVIEW: LifecycleDecisionReason.HUMAN_REVIEW_REQUIRED,
    }
    result = object.__new__(SupersessionDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.SUPERSESSION_AUTHORITY_DECISION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "proposal", proposal)
    object.__setattr__(result, "policy_version", evaluator._policy_version)
    object.__setattr__(result, "reason_code", reasons[decision_kind])
    object.__setattr__(result, "created_at", _parse_timestamp(_format_timestamp(created_at)))
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    object.__setattr__(result, "decision_id", compute_authority_decision_id(canonical_bytes=_canonical(_decision_payload(result)), independence_proof=proof))
    validate_supersession_decision(result, authority_handle=authority_handle)
    return result


def _seal_revocation_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredLifecycleAuthorityEvaluator,
    proposal: LifecycleAuthorityProposal,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
    created_at: datetime,
) -> RevocationDecision:
    configuration = _handle(authority_handle)
    evaluator.require_handle(authority_handle)
    validate_lifecycle_authority_proposal(proposal)
    if proposal.action is not LifecycleAuthorityAction.REVOKE:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "revocation authority requires REVOKE proposal")
    proof = _configured_proof(
        configuration=configuration,
        proposal=proposal,
        authority_identity=configuration.revocation_reviewer_authority,
        role=AuthorityRole.REVOCATION_REVIEWER,
        reason=ReasonCode.REVOCATION_REVIEW_INDEPENDENT,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
    )
    result = object.__new__(RevocationDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.REVOCATION_AUTHORITY_DECISION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "proposal", proposal)
    object.__setattr__(result, "policy_version", evaluator._policy_version)
    object.__setattr__(result, "reason_code", LifecycleDecisionReason.REVOCATION_APPROVED)
    object.__setattr__(result, "created_at", _parse_timestamp(_format_timestamp(created_at)))
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    object.__setattr__(result, "decision_id", compute_authority_decision_id(canonical_bytes=_canonical(_decision_payload(result)), independence_proof=proof))
    validate_revocation_decision(result, authority_handle=authority_handle)
    return result


def create_supersession_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredLifecycleAuthorityEvaluator,
    proposal: LifecycleAuthorityProposal,
    decision_kind: SupersessionDecisionKind,
    executor_identity: ActorIdentity | None = None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...] = (),
) -> SupersessionDecision:
    if type(evaluator) is not ConfiguredLifecycleAuthorityEvaluator:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "lifecycle evaluator is invalid")
    return evaluator.decide_supersession(
        authority_handle=authority_handle,
        proposal=proposal,
        decision_kind=decision_kind,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
    )


def create_revocation_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredLifecycleAuthorityEvaluator,
    proposal: LifecycleAuthorityProposal,
    executor_identity: ActorIdentity | None = None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...] = (),
) -> RevocationDecision:
    if type(evaluator) is not ConfiguredLifecycleAuthorityEvaluator:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "lifecycle evaluator is invalid")
    return evaluator.decide_revocation(
        authority_handle=authority_handle,
        proposal=proposal,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
    )


def validate_supersession_decision(
    value: SupersessionDecision,
    *,
    authority_handle: Stage4AuthorityHandle | None = None,
) -> None:
    if type(value) is not SupersessionDecision or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(LifecycleFailureCode.TRUSTED_OBJECT_FORGED, "supersession decision is not authority sealed")
    if value.schema_version is not SchemaVersion.SUPERSESSION_AUTHORITY_DECISION_V1:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "supersession decision schema is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    if type(value.decision_kind) is not SupersessionDecisionKind:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession decision kind is invalid")
    validate_lifecycle_authority_proposal(value.proposal)
    if value.proposal.action not in (LifecycleAuthorityAction.SUPERSEDE, LifecycleAuthorityAction.WITHDRAW):
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession action is invalid")
    positive = value.decision_kind in (SupersessionDecisionKind.SUPERSEDE, SupersessionDecisionKind.WITHDRAW)
    if value.decision_kind is SupersessionDecisionKind.SUPERSEDE and value.proposal.action is not LifecycleAuthorityAction.SUPERSEDE:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "SUPERSEDE decision action mismatch")
    if value.decision_kind is SupersessionDecisionKind.WITHDRAW and value.proposal.action is not LifecycleAuthorityAction.WITHDRAW:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "WITHDRAW decision action mismatch")
    global_human = positive and value.proposal.context.scope is LifecycleScope.GLOBAL
    _validate_proof_for_proposal(
        value.proposal,
        value.independence_proof,
        AuthorityRole.SUPERSESSION_REVIEWER,
        global_human_required=global_human,
    )
    reasons = {
        SupersessionDecisionKind.SUPERSEDE: LifecycleDecisionReason.SUPERSESSION_APPROVED,
        SupersessionDecisionKind.WITHDRAW: LifecycleDecisionReason.WITHDRAWAL_APPROVED,
        SupersessionDecisionKind.REJECT_SUPERSESSION: LifecycleDecisionReason.SUPERSESSION_REJECTED,
        SupersessionDecisionKind.REQUIRE_HUMAN_REVIEW: LifecycleDecisionReason.HUMAN_REVIEW_REQUIRED,
    }
    if type(value.reason_code) is not LifecycleDecisionReason or value.reason_code is not reasons[value.decision_kind]:
        raise _fail(LifecycleFailureCode.REASON_MISMATCH, "supersession decision reason mismatch")
    _version(value.policy_version, "policy_version")
    _parse_timestamp(_format_timestamp(value.created_at))
    if authority_handle is not None:
        configuration = _handle(authority_handle)
        if configuration_id != configuration.configuration_id:
            raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "supersession configuration differs")
        expected_authority = configuration.governing_human_authority if global_human else configuration.supersession_reviewer_authority
        if expected_authority is None:
            raise _fail(LifecycleFailureCode.GOVERNING_HUMAN_UNAVAILABLE, "governing human is unavailable")
        if value.independence_proof.authority_identity != expected_authority:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "supersession authority is not configured")
    expected = compute_authority_decision_id(canonical_bytes=_canonical(_decision_payload(value)), independence_proof=value.independence_proof)
    if type(value.decision_id) is not AuthorityDecisionId or value.decision_id != expected:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "supersession decision identity mismatch")


def validate_revocation_decision(
    value: RevocationDecision,
    *,
    authority_handle: Stage4AuthorityHandle | None = None,
) -> None:
    if type(value) is not RevocationDecision or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(LifecycleFailureCode.TRUSTED_OBJECT_FORGED, "revocation decision is not authority sealed")
    if value.schema_version is not SchemaVersion.REVOCATION_AUTHORITY_DECISION_V1:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "revocation decision schema is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    validate_lifecycle_authority_proposal(value.proposal)
    if value.proposal.action is not LifecycleAuthorityAction.REVOKE:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "revocation action is invalid")
    _validate_proof_for_proposal(
        value.proposal,
        value.independence_proof,
        AuthorityRole.REVOCATION_REVIEWER,
        global_human_required=False,
    )
    if value.reason_code is not LifecycleDecisionReason.REVOCATION_APPROVED:
        raise _fail(LifecycleFailureCode.REASON_MISMATCH, "revocation decision reason mismatch")
    _version(value.policy_version, "policy_version")
    _parse_timestamp(_format_timestamp(value.created_at))
    if authority_handle is not None:
        configuration = _handle(authority_handle)
        if configuration_id != configuration.configuration_id:
            raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "revocation configuration differs")
        if value.independence_proof.authority_identity != configuration.revocation_reviewer_authority:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "revocation authority is not configured")
    expected = compute_authority_decision_id(canonical_bytes=_canonical(_decision_payload(value)), independence_proof=value.independence_proof)
    if type(value.decision_id) is not AuthorityDecisionId or value.decision_id != expected:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "revocation decision identity mismatch")


def _authority_decision_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    revocation: bool,
) -> SupersessionDecision | RevocationDecision:
    configuration = _handle(authority_handle)
    fields = (
        ("schema_version", "configuration_id", "proposal", "policy_version", "reason_code", "created_at", "independence_proof", "decision_id")
        if revocation
        else ("schema_version", "configuration_id", "decision_kind", "proposal", "policy_version", "reason_code", "created_at", "independence_proof", "decision_id")
    )
    data = _exact_dict(value, fields, "lifecycle_authority_decision")
    expected_schema = SchemaVersion.REVOCATION_AUTHORITY_DECISION_V1 if revocation else SchemaVersion.SUPERSESSION_AUTHORITY_DECISION_V1
    if data["schema_version"] != expected_schema.value or type(data["schema_version"]) is not str:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle authority decision schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport decision configuration differs")
    proposal = lifecycle_authority_proposal_from_dict(data["proposal"])
    proposal_bytes = _canonical(_authority_proposal_payload(proposal))
    proof = independence_proof_from_dict(data["independence_proof"], proposal_canonical_bytes=proposal_bytes)
    try:
        reason = LifecycleDecisionReason(data["reason_code"])
        kind = None if revocation else SupersessionDecisionKind(data["decision_kind"])
    except (TypeError, ValueError) as exc:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "transport decision enum is unknown") from exc
    created_at = _parse_timestamp(data["created_at"])
    policy_version = _version(data["policy_version"], "policy_version")
    result: SupersessionDecision | RevocationDecision
    if revocation:
        result = object.__new__(RevocationDecision)
    else:
        result = object.__new__(SupersessionDecision)
        object.__setattr__(result, "decision_kind", kind)
    object.__setattr__(result, "schema_version", expected_schema)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "proposal", proposal)
    object.__setattr__(result, "policy_version", policy_version)
    object.__setattr__(result, "reason_code", reason)
    object.__setattr__(result, "created_at", created_at)
    object.__setattr__(result, "independence_proof", proof)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_decision_payload(result))
    try:
        supplied = authority_decision_id_from_dict(data["decision_id"], canonical_bytes=payload, independence_proof=proof)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport authority decision identity is invalid") from exc
    expected_id = compute_authority_decision_id(canonical_bytes=payload, independence_proof=proof)
    if supplied != expected_id:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport authority decision identity changed")
    object.__setattr__(result, "decision_id", supplied)
    if revocation:
        validate_revocation_decision(result, authority_handle=authority_handle)
    else:
        validate_supersession_decision(result, authority_handle=authority_handle)
    return result


@dataclass(frozen=True, init=False)
class LifecycleRecord:
    schema_version: SchemaVersion
    configuration_id: RecordId
    subject_ref: HashBoundRef
    context: LifecycleContext
    from_state: LifecycleState | None
    to_state: LifecycleState
    reason_code: LifecycleReasonCode
    authority_identity: ActorIdentity
    platform_writer_identity: ActorIdentity
    evidence_refs: tuple[HashBoundRef, ...]
    predecessor_record_id: str | None
    subject_sequence: int
    journal_sequence: int
    supersession_decision: SupersessionDecision | None
    revocation_decision: RevocationDecision | None
    record_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> LifecycleRecord:
        raise TypeError("LifecycleRecord is created only by LifecycleStore.append")

    def to_dict(self) -> dict[str, object]:
        validate_lifecycle_record(self)
        return {**_record_payload(self), "record_id": self.record_id.to_dict()}


def _record_payload(value: LifecycleRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "subject_ref": value.subject_ref.to_dict(),
        "context": value.context.to_dict(),
        "from_state": None if value.from_state is None else value.from_state.value,
        "to_state": value.to_state.value,
        "reason_code": value.reason_code.value,
        "authority_identity": value.authority_identity.to_dict(),
        "platform_writer_identity": value.platform_writer_identity.to_dict(),
        "evidence_refs": [item.to_dict() for item in value.evidence_refs],
        "predecessor_record_id": value.predecessor_record_id,
        "subject_sequence": value.subject_sequence,
        "journal_sequence": value.journal_sequence,
        "supersession_decision": None if value.supersession_decision is None else value.supersession_decision.to_dict(),
        "revocation_decision": None if value.revocation_decision is None else value.revocation_decision.to_dict(),
    }


def _validate_transition(
    from_state: LifecycleState | None,
    to_state: LifecycleState,
    reason: LifecycleReasonCode,
    supersession: SupersessionDecision | None,
    revocation: RevocationDecision | None,
) -> None:
    if from_state in _TERMINAL_STATES:
        raise _fail(LifecycleFailureCode.UNSUPPORTED_TRANSITION, "terminal lifecycle identity cannot transition")
    normal = _NORMAL_TRANSITIONS.get(from_state)
    if normal == (to_state, reason):
        if supersession is not None or revocation is not None:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "normal transition cannot carry exception authority")
        return
    if from_state is LifecycleState.STALE and to_state is LifecycleState.REVALIDATED and reason is LifecycleReasonCode.REVALIDATION_RECOVERED:
        if supersession is not None or revocation is not None:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "revalidation cannot carry exception authority")
        return
    if to_state is LifecycleState.SUPERSEDED:
        if supersession is None or revocation is not None:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "SUPERSEDED requires exactly one supersession decision")
        if supersession.decision_kind is SupersessionDecisionKind.SUPERSEDE:
            expected = LifecycleReasonCode.SUPERSESSION_APPROVED
        elif supersession.decision_kind is SupersessionDecisionKind.WITHDRAW:
            expected = LifecycleReasonCode.WITHDRAWAL_APPROVED
        else:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "negative supersession decision cannot transition lifecycle")
        if reason is not expected:
            raise _fail(LifecycleFailureCode.REASON_MISMATCH, "supersession reason does not match decision kind")
        return
    expected_reason = _EXCEPTION_REASONS.get(to_state)
    if expected_reason is not None and reason is expected_reason:
        if to_state is LifecycleState.REVOKED:
            if revocation is None or supersession is not None:
                raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "REVOKED requires exactly one revocation decision")
        elif revocation is not None or supersession is not None:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "this exception transition carries no authority decision")
        return
    raise _fail(LifecycleFailureCode.UNSUPPORTED_TRANSITION, "lifecycle transition is not in the closed matrix")


def _create_lifecycle_record(
    *,
    configuration_id: RecordId,
    subject_ref: HashBoundRef,
    context: LifecycleContext,
    from_state: LifecycleState | None,
    to_state: LifecycleState,
    reason_code: LifecycleReasonCode,
    platform_writer_identity: ActorIdentity,
    evidence_refs: object,
    predecessor_record_id: str | None,
    subject_sequence: int,
    journal_sequence: int,
    supersession_decision: SupersessionDecision | None,
    revocation_decision: RevocationDecision | None,
) -> LifecycleRecord:
    if from_state is not None and type(from_state) is not LifecycleState:
        raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "from_state is unknown")
    if type(to_state) is not LifecycleState or type(reason_code) is not LifecycleReasonCode:
        raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "to_state or reason is unknown")
    if supersession_decision is not None:
        validate_supersession_decision(supersession_decision)
    if revocation_decision is not None:
        validate_revocation_decision(revocation_decision)
    _validate_transition(from_state, to_state, reason_code, supersession_decision, revocation_decision)
    subject = _ref(subject_ref, None, "subject_ref")
    context_snapshot = LifecycleContext.from_dict(context.to_dict())
    if supersession_decision is not None and (supersession_decision.proposal.subject_ref != subject or supersession_decision.proposal.context != context_snapshot):
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession decision subject/context mismatch")
    if revocation_decision is not None and (revocation_decision.proposal.subject_ref != subject or revocation_decision.proposal.context != context_snapshot):
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "revocation decision subject/context mismatch")
    writer = _actor(platform_writer_identity, "platform_writer_identity")
    authority = writer
    decision = supersession_decision if supersession_decision is not None else revocation_decision
    if decision is not None:
        if writer.value not in {item.value for item in decision.proposal.producer_actor_ids}:
            raise _fail(
                LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT,
                "configured platform writer must be covered as a producer actor",
            )
        if decision.independence_proof.authority_identity.value == writer.value:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "platform writer cannot approve its own exception decision")
        authority = ActorIdentity(decision.independence_proof.authority_identity.value)
    predecessor = _record_text(predecessor_record_id, _RECORD_ID_PREFIX, "predecessor_record_id", allow_none=True)
    if type(subject_sequence) is not int or subject_sequence < 1 or type(journal_sequence) is not int or journal_sequence < 1:
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "record sequences must be positive")
    if (subject_sequence == 1) != (predecessor is None):
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "first subject record alone has no predecessor")
    result = object.__new__(LifecycleRecord)
    object.__setattr__(result, "schema_version", SchemaVersion.LIFECYCLE_RECORD_V1)
    object.__setattr__(result, "configuration_id", _configuration_id(configuration_id))
    object.__setattr__(result, "subject_ref", subject)
    object.__setattr__(result, "context", context_snapshot)
    object.__setattr__(result, "from_state", from_state)
    object.__setattr__(result, "to_state", to_state)
    object.__setattr__(result, "reason_code", reason_code)
    object.__setattr__(result, "authority_identity", authority)
    object.__setattr__(result, "platform_writer_identity", writer)
    object.__setattr__(result, "evidence_refs", _refs(evidence_refs, RefKind.SOURCE_EVIDENCE, "evidence_refs", nonempty=True))
    object.__setattr__(result, "predecessor_record_id", predecessor)
    object.__setattr__(result, "subject_sequence", subject_sequence)
    object.__setattr__(result, "journal_sequence", journal_sequence)
    object.__setattr__(result, "supersession_decision", supersession_decision)
    object.__setattr__(result, "revocation_decision", revocation_decision)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_record_payload(result))
    object.__setattr__(result, "record_id", compute_record_id(domain=IdentityDomain.LIFECYCLE_RECORD, canonical_bytes=payload))
    validate_lifecycle_record(result)
    return result


def validate_lifecycle_record(
    value: LifecycleRecord,
    *,
    expected_configuration_id: RecordId | None = None,
    expected_platform_writer: ActorIdentity | None = None,
    authority_handle: Stage4AuthorityHandle | None = None,
) -> None:
    if type(value) is not LifecycleRecord or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(LifecycleFailureCode.TRUSTED_OBJECT_FORGED, "lifecycle record is not store sealed")
    if value.schema_version is not SchemaVersion.LIFECYCLE_RECORD_V1:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle record schema is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    _ref(value.subject_ref, None, "subject_ref")
    _validate_context(value.context)
    if value.from_state is not None and type(value.from_state) is not LifecycleState:
        raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "from_state is unknown")
    if type(value.to_state) is not LifecycleState or type(value.reason_code) is not LifecycleReasonCode:
        raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "to_state or reason is unknown")
    if value.supersession_decision is not None:
        validate_supersession_decision(value.supersession_decision, authority_handle=authority_handle)
    if value.revocation_decision is not None:
        validate_revocation_decision(value.revocation_decision, authority_handle=authority_handle)
    _validate_transition(value.from_state, value.to_state, value.reason_code, value.supersession_decision, value.revocation_decision)
    writer = _actor(value.platform_writer_identity, "platform_writer_identity")
    authority = _actor(value.authority_identity, "authority_identity")
    decision = value.supersession_decision if value.supersession_decision is not None else value.revocation_decision
    if decision is None:
        if authority != writer:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "normal record authority differs from configured writer")
    else:
        if decision.proposal.subject_ref != value.subject_ref or decision.proposal.context != value.context:
            raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "record decision subject/context mismatch")
        if writer.value not in {item.value for item in decision.proposal.producer_actor_ids}:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "record decision omits configured platform writer")
        if authority.value != decision.independence_proof.authority_identity.value or authority == writer:
            raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "record exception authority is invalid")
    _refs(value.evidence_refs, RefKind.SOURCE_EVIDENCE, "evidence_refs", nonempty=True)
    predecessor = _record_text(value.predecessor_record_id, _RECORD_ID_PREFIX, "predecessor_record_id", allow_none=True)
    if type(value.subject_sequence) is not int or value.subject_sequence < 1 or type(value.journal_sequence) is not int or value.journal_sequence < 1:
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "record sequences are invalid")
    if (value.subject_sequence == 1) != (predecessor is None):
        raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "record predecessor/sequence mismatch")
    if expected_platform_writer is not None and writer != _actor(expected_platform_writer, "expected_platform_writer"):
        raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "journal writer differs from configured platform writer")
    if expected_configuration_id is not None and configuration_id != _configuration_id(expected_configuration_id):
        raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "record configuration differs")
    payload = _canonical(_record_payload(value))
    if type(value.record_id) is not RecordId or value.record_id.domain is not IdentityDomain.LIFECYCLE_RECORD:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "lifecycle record identity domain is invalid")
    try:
        validate_record_id(value.record_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "lifecycle record identity mismatch") from exc


def lifecycle_record_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
) -> LifecycleRecord:
    configuration = _handle(authority_handle)
    fields = (
        "schema_version", "configuration_id", "subject_ref", "context", "from_state", "to_state", "reason_code",
        "authority_identity", "platform_writer_identity", "evidence_refs", "predecessor_record_id",
        "subject_sequence", "journal_sequence", "supersession_decision", "revocation_decision", "record_id",
    )
    data = _exact_dict(value, fields, "lifecycle_record")
    if data["schema_version"] != SchemaVersion.LIFECYCLE_RECORD_V1.value or type(data["schema_version"]) is not str:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle record schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport record configuration differs")
    try:
        from_state = None if data["from_state"] is None else LifecycleState(data["from_state"])
        to_state = LifecycleState(data["to_state"])
        reason = LifecycleReasonCode(data["reason_code"])
    except (TypeError, ValueError) as exc:
        raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "lifecycle state or reason is unknown") from exc
    supersession_raw = data["supersession_decision"]
    revocation_raw = data["revocation_decision"]
    supersession = None if supersession_raw is None else _authority_decision_from_dict(supersession_raw, authority_handle=authority_handle, revocation=False)
    revocation = None if revocation_raw is None else _authority_decision_from_dict(revocation_raw, authority_handle=authority_handle, revocation=True)
    if supersession is not None and type(supersession) is not SupersessionDecision:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "supersession transport type is invalid")
    if revocation is not None and type(revocation) is not RevocationDecision:
        raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "revocation transport type is invalid")
    result = _create_lifecycle_record(
        configuration_id=configuration.configuration_id,
        subject_ref=HashBoundRef.from_dict(data["subject_ref"]),
        context=LifecycleContext.from_dict(data["context"]),
        from_state=from_state,
        to_state=to_state,
        reason_code=reason,
        platform_writer_identity=ActorIdentity.from_dict(data["platform_writer_identity"]),
        evidence_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["evidence_refs"], "evidence_refs")),
        predecessor_record_id=data["predecessor_record_id"],
        subject_sequence=data["subject_sequence"],
        journal_sequence=data["journal_sequence"],
        supersession_decision=supersession,
        revocation_decision=revocation,
    )
    if data["authority_identity"] != result.authority_identity.to_dict():
        raise _fail(LifecycleFailureCode.AUTHORITY_NOT_INDEPENDENT, "transport authority identity changed")
    payload = _canonical(_record_payload(result))
    try:
        supplied = record_id_from_dict(data["record_id"], canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport lifecycle identity is invalid") from exc
    if supplied != result.record_id:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport lifecycle identity changed")
    validate_lifecycle_record(
        result,
        expected_configuration_id=configuration.configuration_id,
        expected_platform_writer=configuration.lifecycle_writer_actor,
        authority_handle=authority_handle,
    )
    return result


def _chain_key(subject: HashBoundRef, context: LifecycleContext) -> tuple[str, str, str, str]:
    return (subject.ref_id, subject.sha256, context.scope.value, context.context_id)


def validate_lifecycle_history(
    records: object,
    *,
    expected_platform_writer: ActorIdentity,
    expected_configuration_id: RecordId | None = None,
    authority_handle: Stage4AuthorityHandle | None = None,
) -> tuple[LifecycleRecord, ...]:
    if type(records) not in (tuple, list):
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "records must be tuple or list")
    result = tuple(records)
    heads: dict[tuple[str, str, str, str], LifecycleRecord] = {}
    seen_ids: set[str] = set()
    for expected_journal_sequence, record in enumerate(result, start=1):
        validate_lifecycle_record(
            record,
            expected_platform_writer=expected_platform_writer,
            expected_configuration_id=expected_configuration_id,
            authority_handle=authority_handle,
        )
        if record.journal_sequence != expected_journal_sequence:
            raise _fail(LifecycleFailureCode.HISTORY_FORK, "journal sequence is not contiguous")
        if record.record_id.value in seen_ids:
            raise _fail(LifecycleFailureCode.HISTORY_FORK, "lifecycle record identity is duplicated")
        seen_ids.add(record.record_id.value)
        key = _chain_key(record.subject_ref, record.context)
        head = heads.get(key)
        expected_predecessor = None if head is None else head.record_id.value
        expected_subject_sequence = 1 if head is None else head.subject_sequence + 1
        expected_from = None if head is None else head.to_state
        if record.predecessor_record_id != expected_predecessor or record.subject_sequence != expected_subject_sequence or record.from_state is not expected_from:
            raise _fail(LifecycleFailureCode.HISTORY_FORK, "subject/context chain has a fork or missing predecessor")
        heads[key] = record
    return result


@dataclass(frozen=True)
class LifecycleHead:
    schema_version: str
    subject_ref: HashBoundRef
    context: LifecycleContext
    state: LifecycleState
    record_id: str
    subject_sequence: int

    def to_dict(self) -> dict[str, object]:
        if self.schema_version != LIFECYCLE_HEAD_V1 or type(self.schema_version) is not str:
            raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle head schema is unknown")
        _ref(self.subject_ref, None, "head.subject_ref")
        _validate_context(self.context)
        if type(self.state) is not LifecycleState:
            raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "head state is unknown")
        _record_text(self.record_id, _RECORD_ID_PREFIX, "head.record_id", allow_none=False)
        if type(self.subject_sequence) is not int or self.subject_sequence < 1:
            raise _fail(LifecycleFailureCode.MISSING_PREDECESSOR, "head subject sequence is invalid")
        return {"schema_version": self.schema_version, "subject_ref": self.subject_ref.to_dict(), "context": self.context.to_dict(), "state": self.state.value, "record_id": self.record_id, "subject_sequence": self.subject_sequence}

    @classmethod
    def from_dict(cls, value: object) -> LifecycleHead:
        data = _exact_dict(value, ("schema_version", "subject_ref", "context", "state", "record_id", "subject_sequence"), "lifecycle_head")
        try:
            state = LifecycleState(data["state"])
        except (TypeError, ValueError) as exc:
            raise _fail(LifecycleFailureCode.UNKNOWN_STATE, "lifecycle head state is unknown") from exc
        result = cls(data["schema_version"], HashBoundRef.from_dict(data["subject_ref"]), LifecycleContext.from_dict(data["context"]), state, data["record_id"], data["subject_sequence"])
        result.to_dict()
        return result


@dataclass(frozen=True, init=False)
class LifecycleSnapshot:
    schema_version: SchemaVersion
    record_count: int
    log_root_sha256: str
    heads: tuple[LifecycleHead, ...]
    snapshot_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> LifecycleSnapshot:
        raise TypeError("LifecycleSnapshot is computed from complete validated history")

    def to_dict(self) -> dict[str, object]:
        validate_lifecycle_snapshot(self)
        return {**_snapshot_payload(self), "snapshot_id": self.snapshot_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        records: object,
        expected_platform_writer: ActorIdentity,
        trusted_prior: LifecycleSnapshot | None = None,
    ) -> LifecycleSnapshot:
        return lifecycle_snapshot_from_dict(
            value,
            records=records,
            expected_platform_writer=expected_platform_writer,
            trusted_prior=trusted_prior,
        )


def _log_roots(records: tuple[LifecycleRecord, ...]) -> tuple[str, ...]:
    root = hashlib.sha256(LIFECYCLE_LOG_ROOT_PROFILE_V1.encode("utf-8") + b"\x00").digest()
    roots: list[str] = []
    for record in records:
        transport = _canonical(record.to_dict())
        root = hashlib.sha256(LIFECYCLE_LOG_ROOT_PROFILE_V1.encode("utf-8") + b"\x00" + root + hashlib.sha256(transport).digest()).digest()
        roots.append(root.hex())
    return tuple(roots)


def _snapshot_payload(value: LifecycleSnapshot) -> dict[str, object]:
    return {"schema_version": value.schema_version.value, "record_count": value.record_count, "log_root_sha256": value.log_root_sha256, "heads": [item.to_dict() for item in value.heads]}


def create_lifecycle_snapshot(*, records: object, expected_platform_writer: ActorIdentity, trusted_prior: LifecycleSnapshot | None = None) -> LifecycleSnapshot:
    history = validate_lifecycle_history(records, expected_platform_writer=expected_platform_writer)
    roots = _log_roots(history)
    empty_root = hashlib.sha256(LIFECYCLE_LOG_ROOT_PROFILE_V1.encode("utf-8") + b"\x00").hexdigest()
    current_root = empty_root if not roots else roots[-1]
    if trusted_prior is not None:
        validate_lifecycle_snapshot(trusted_prior)
        if len(history) < trusted_prior.record_count:
            raise _fail(LifecycleFailureCode.HISTORY_ROLLBACK, "current history is shorter than trusted snapshot")
        prefix_root = empty_root if trusted_prior.record_count == 0 else roots[trusted_prior.record_count - 1]
        if prefix_root != trusted_prior.log_root_sha256:
            raise _fail(LifecycleFailureCode.HISTORY_ROLLBACK, "trusted snapshot is not a prefix of current history")
    heads_by_key: dict[tuple[str, str, str, str], LifecycleRecord] = {}
    for record in history:
        heads_by_key[_chain_key(record.subject_ref, record.context)] = record
    heads = tuple(
        LifecycleHead(LIFECYCLE_HEAD_V1, item.subject_ref, item.context, item.to_state, item.record_id.value, item.subject_sequence)
        for _, item in sorted(heads_by_key.items())
    )
    result = object.__new__(LifecycleSnapshot)
    object.__setattr__(result, "schema_version", SchemaVersion.LIFECYCLE_SNAPSHOT_V1)
    object.__setattr__(result, "record_count", len(history))
    object.__setattr__(result, "log_root_sha256", current_root)
    object.__setattr__(result, "heads", heads)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_snapshot_payload(result))
    object.__setattr__(result, "snapshot_id", compute_record_id(domain=IdentityDomain.LIFECYCLE_SNAPSHOT, canonical_bytes=payload))
    validate_lifecycle_snapshot(result)
    return result


def validate_lifecycle_snapshot(value: LifecycleSnapshot) -> None:
    if type(value) is not LifecycleSnapshot or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(LifecycleFailureCode.TRUSTED_OBJECT_FORGED, "lifecycle snapshot is not factory sealed")
    if value.schema_version is not SchemaVersion.LIFECYCLE_SNAPSHOT_V1:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle snapshot schema is unknown")
    if type(value.record_count) is not int or value.record_count < 0 or type(value.log_root_sha256) is not str or _SHA256_RE.fullmatch(value.log_root_sha256) is None:
        raise _fail(LifecycleFailureCode.HISTORY_ROLLBACK, "lifecycle snapshot count/root is invalid")
    if type(value.heads) is not tuple:
        raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "snapshot heads must be exact tuple")
    head_keys: set[tuple[str, str, str, str]] = set()
    for head in value.heads:
        head.to_dict()
        key = _chain_key(head.subject_ref, head.context)
        if key in head_keys:
            raise _fail(LifecycleFailureCode.HISTORY_FORK, "snapshot contains duplicate heads")
        head_keys.add(key)
    payload = _canonical(_snapshot_payload(value))
    if type(value.snapshot_id) is not RecordId or value.snapshot_id.domain is not IdentityDomain.LIFECYCLE_SNAPSHOT:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "snapshot identity domain is invalid")
    try:
        validate_record_id(value.snapshot_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "snapshot identity mismatch") from exc


def lifecycle_snapshot_from_dict(
    value: object,
    *,
    records: object,
    expected_platform_writer: ActorIdentity,
    trusted_prior: LifecycleSnapshot | None = None,
) -> LifecycleSnapshot:
    data = _exact_dict(value, ("schema_version", "record_count", "log_root_sha256", "heads", "snapshot_id"), "lifecycle_snapshot")
    if data["schema_version"] != SchemaVersion.LIFECYCLE_SNAPSHOT_V1.value or type(data["schema_version"]) is not str:
        raise _fail(LifecycleFailureCode.UNKNOWN_SCHEMA_VERSION, "lifecycle snapshot schema is unknown")
    expected = create_lifecycle_snapshot(records=records, expected_platform_writer=expected_platform_writer, trusted_prior=trusted_prior)
    supplied_heads = tuple(LifecycleHead.from_dict(item) for item in _exact_list(data["heads"], "heads"))
    if (
        data["record_count"] != expected.record_count
        or data["log_root_sha256"] != expected.log_root_sha256
        or supplied_heads != expected.heads
    ):
        raise _fail(LifecycleFailureCode.HISTORY_ROLLBACK, "transport snapshot does not describe exact reconstructed history")
    payload = _canonical(_snapshot_payload(expected))
    try:
        supplied_id = record_id_from_dict(data["snapshot_id"], canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport snapshot identity is invalid") from exc
    if supplied_id != expected.snapshot_id:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "transport snapshot identity changed")
    return expected


_LIFECYCLE_ENTRY_FIELDS = ("kind", "configuration_id", "entry_id", "payload")


def _lifecycle_entry(
    payload: bytes,
    *,
    authority_handle: Stage4AuthorityHandle,
) -> tuple[str, LifecycleRecord | SupersessionDecision | RevocationDecision, str, str]:
    configuration = _handle(authority_handle)
    raw = _exact_dict(_decode(payload), _LIFECYCLE_ENTRY_FIELDS, "lifecycle_history_entry")
    if raw["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(LifecycleFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "lifecycle entry configuration differs")
    kind = raw["kind"]
    if kind == "RECORD":
        value = lifecycle_record_from_dict(raw["payload"], authority_handle=authority_handle)
        expected_id = value.record_id.value
    elif kind == "SUPERSESSION_DECISION":
        value = _authority_decision_from_dict(raw["payload"], authority_handle=authority_handle, revocation=False)
        expected_id = value.decision_id.record_id.value
    elif kind == "REVOCATION_DECISION":
        value = _authority_decision_from_dict(raw["payload"], authority_handle=authority_handle, revocation=True)
        expected_id = value.decision_id.record_id.value
    else:
        raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "lifecycle history entry kind is unknown")
    if raw["entry_id"] != expected_id:
        raise _fail(LifecycleFailureCode.IDENTITY_MISMATCH, "lifecycle wrapper identity differs from payload")
    return kind, value, expected_id, hashlib.sha256(payload).hexdigest()


def _validate_lifecycle_entries(
    entries: tuple[tuple[str, LifecycleRecord | SupersessionDecision | RevocationDecision, str, str], ...],
    *,
    authority_handle: Stage4AuthorityHandle,
) -> None:
    configuration = _handle(authority_handle)
    seen: set[str] = set()
    decisions: dict[str, tuple[int, SupersessionDecision | RevocationDecision]] = {}
    decision_heads: dict[tuple[str, str, str, str], tuple[str, int]] = {}
    records: list[LifecycleRecord] = []
    applied: set[str] = set()
    for position, (kind, value, entry_id, _) in enumerate(entries):
        if entry_id in seen:
            raise _fail(LifecycleFailureCode.HISTORY_FORK, "lifecycle ordered history contains a duplicate identity")
        seen.add(entry_id)
        if kind == "RECORD":
            if type(value) is not LifecycleRecord:
                raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "lifecycle record entry type is invalid")
            decision = value.supersession_decision if value.supersession_decision is not None else value.revocation_decision
            if decision is not None:
                decision_id = decision.decision_id.record_id.value
                persisted = decisions.get(decision_id)
                if persisted is None or persisted[0] >= position:
                    raise _fail(LifecycleFailureCode.DECISION_MISMATCH, "positive authority decision was not persisted before transition")
                if decision_id in applied:
                    raise _fail(LifecycleFailureCode.DECISION_ALREADY_APPLIED, "authority decision was applied more than once")
                applied.add(decision_id)
            records.append(value)
            continue
        if type(value) not in (SupersessionDecision, RevocationDecision):
            raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "authority entry type is invalid")
        proposal = value.proposal
        key = _chain_key(proposal.subject_ref, proposal.context)
        previous = decision_heads.get(key)
        expected_predecessor = None if previous is None else previous[0]
        expected_sequence = 1 if previous is None else previous[1] + 1
        if proposal.predecessor_decision_id != expected_predecessor or proposal.decision_sequence != expected_sequence:
            raise _fail(LifecycleFailureCode.AUTHORITY_HISTORY_FORK, "lifecycle authority history has a fork")
        decision_heads[key] = (entry_id, expected_sequence)
        decisions[entry_id] = (position, value)
    validate_lifecycle_history(
        tuple(records),
        expected_platform_writer=configuration.lifecycle_writer_actor,
        expected_configuration_id=configuration.configuration_id,
        authority_handle=authority_handle,
    )


def _lifecycle_anchor_heads(entries) -> tuple[str, ...]:
    record_heads: dict[tuple[str, str, str, str], LifecycleRecord] = {}
    decision_heads: dict[tuple[str, str, str, str], SupersessionDecision | RevocationDecision] = {}
    for kind, value, _, _ in entries:
        if kind == "RECORD":
            record_heads[_chain_key(value.subject_ref, value.context)] = value
        else:
            decision_heads[_chain_key(value.proposal.subject_ref, value.proposal.context)] = value
    result = [f"RECORD|{'|'.join(key)}|{value.record_id.value}" for key, value in sorted(record_heads.items())]
    result.extend(f"DECISION|{'|'.join(key)}|{value.decision_id.record_id.value}" for key, value in sorted(decision_heads.items()))
    return tuple(result)


class LifecycleStore:
    """Single-machine append-only store guarded by one capability and external anchor."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_STORE_SEAL or kwargs or len(args) != 4:
            raise TypeError("LifecycleStore is opened only by open_lifecycle_store")
        root, authority_handle, trusted_anchor, allow_genesis = args
        if not isinstance(root, Path) or type(allow_genesis) is not bool:
            raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "store configuration is invalid")
        self._root = root
        self._authority_handle = authority_handle
        configuration = _handle(authority_handle)
        self._configuration_id = configuration.configuration_id
        self._writer = _actor(configuration.lifecycle_writer_actor, "platform_writer_identity")
        self._trusted_anchor = None
        ensure_directory(root)
        initialize_journal(self._journal_path)
        entries = self._entries()
        if entries and trusted_anchor is None:
            raise _fail(LifecycleFailureCode.HISTORY_ANCHOR_REQUIRED, "non-empty lifecycle history requires trusted anchor")
        if not entries and trusted_anchor is None and not allow_genesis:
            raise _fail(LifecycleFailureCode.HISTORY_ANCHOR_REQUIRED, "empty lifecycle history requires explicit genesis")
        self._trusted_anchor = trusted_anchor
        if trusted_anchor is not None:
            self._validate_anchor(entries, trusted_anchor)

    @property
    def _journal_path(self) -> Path:
        return self._root / LIFECYCLE_JOURNAL_NAME_V1

    @property
    def _lock_path(self) -> Path:
        return self._root / LIFECYCLE_LOCK_NAME_V1

    @property
    def platform_writer_identity(self) -> ActorIdentity:
        return _actor(self._writer, "platform_writer_identity")

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    def _entries(self):
        initialize_journal(self._journal_path)
        try:
            scan = scan_journal(self._journal_path)
            if scan.torn_tail:
                raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "lifecycle journal has a torn tail")
            entries = tuple(_lifecycle_entry(frame.payload, authority_handle=self._authority_handle) for frame in scan.frames)
            _validate_lifecycle_entries(entries, authority_handle=self._authority_handle)
            if self._trusted_anchor is not None:
                self._validate_anchor(entries, self._trusted_anchor)
            return entries
        except LifecycleViolation:
            raise
        except Exception as exc:
            raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "lifecycle journal cannot be reconstructed") from exc

    def _validate_anchor(self, entries, anchor: HistoryAnchor) -> None:
        try:
            prefix = entries[: anchor.entry_count]
            validate_history_anchor_extension(
                trusted_anchor=anchor,
                history_domain=HistoryDomain.LIFECYCLE,
                configuration_id=self._configuration_id,
                entry_sha256s=tuple(item[3] for item in entries),
                prefix_domain_heads=_lifecycle_anchor_heads(prefix),
            )
        except ContractViolation as exc:
            raise _fail(LifecycleFailureCode.HISTORY_ROLLBACK, "lifecycle history is not an exact trusted extension") from exc

    def current_anchor(self) -> HistoryAnchor:
        entries = self._entries()
        return create_history_anchor(
            history_domain=HistoryDomain.LIFECYCLE,
            configuration_id=self._configuration_id,
            entry_sha256s=tuple(item[3] for item in entries),
            domain_heads=_lifecycle_anchor_heads(entries),
        )

    def _append_entry(self, *, authority_handle: Stage4AuthorityHandle, kind: str, entry_id: str, payload: dict[str, object]) -> HistoryAnchor:
        self.require_handle(authority_handle)
        wrapper = {"kind": kind, "configuration_id": self._configuration_id.to_dict(), "entry_id": entry_id, "payload": payload}
        raw = _canonical(wrapper)
        with ExclusiveStoreLock(self._lock_path):
            entries = self._entries()
            candidate = (*entries, _lifecycle_entry(raw, authority_handle=authority_handle))
            _validate_lifecycle_entries(candidate, authority_handle=authority_handle)
            append_journal_payload(self._journal_path, raw)
            anchor = self.current_anchor()
            self._trusted_anchor = anchor
            return anchor

    def records(self) -> tuple[LifecycleRecord, ...]:
        return tuple(value for kind, value, _, _ in self._entries() if kind == "RECORD")

    def authority_decisions(self) -> tuple[SupersessionDecision | RevocationDecision, ...]:
        return tuple(value for kind, value, _, _ in self._entries() if kind != "RECORD")

    def persist_authority_decision(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        decision: SupersessionDecision | RevocationDecision,
    ) -> HistoryAnchor:
        if type(decision) is SupersessionDecision:
            validate_supersession_decision(decision, authority_handle=authority_handle)
            kind = "SUPERSESSION_DECISION"
        elif type(decision) is RevocationDecision:
            validate_revocation_decision(decision, authority_handle=authority_handle)
            kind = "REVOCATION_DECISION"
        else:
            raise _fail(LifecycleFailureCode.TYPE_MISMATCH, "authority decision type is invalid")
        return self._append_entry(
            authority_handle=authority_handle,
            kind=kind,
            entry_id=decision.decision_id.record_id.value,
            payload=decision.to_dict(),
        )

    def append(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        subject_ref: HashBoundRef,
        context: LifecycleContext,
        to_state: LifecycleState,
        reason_code: LifecycleReasonCode,
        evidence_refs: object,
        expected_predecessor_record_id: str | None,
        expected_subject_sequence: int,
        supersession_decision: SupersessionDecision | None = None,
        revocation_decision: RevocationDecision | None = None,
    ) -> LifecycleRecord:
        self.require_handle(authority_handle)
        subject = _ref(subject_ref, None, "subject_ref")
        context_snapshot = LifecycleContext.from_dict(context.to_dict())
        with ExclusiveStoreLock(self._lock_path):
            history = self.records()
            key = _chain_key(subject, context_snapshot)
            head = next((item for item in reversed(history) if _chain_key(item.subject_ref, item.context) == key), None)
            actual_predecessor = None if head is None else head.record_id.value
            actual_sequence = 1 if head is None else head.subject_sequence + 1
            if expected_predecessor_record_id != actual_predecessor or expected_subject_sequence != actual_sequence:
                raise _fail(LifecycleFailureCode.CONCURRENT_UPDATE, "expected predecessor/sequence does not match durable head")
            record = _create_lifecycle_record(
                configuration_id=self._configuration_id,
                subject_ref=subject,
                context=context_snapshot,
                from_state=None if head is None else head.to_state,
                to_state=to_state,
                reason_code=reason_code,
                platform_writer_identity=self._writer,
                evidence_refs=evidence_refs,
                predecessor_record_id=actual_predecessor,
                subject_sequence=actual_sequence,
                journal_sequence=len(history) + 1,
                supersession_decision=supersession_decision,
                revocation_decision=revocation_decision,
            )
            validate_lifecycle_history(
                (*history, record),
                expected_platform_writer=self._writer,
                expected_configuration_id=self._configuration_id,
                authority_handle=authority_handle,
            )
        self._append_entry(
            authority_handle=authority_handle,
            kind="RECORD",
            entry_id=record.record_id.value,
            payload=record.to_dict(),
        )
        committed = self.records()
        if not committed or committed[-1].record_id != record.record_id:
            raise _fail(LifecycleFailureCode.JOURNAL_CORRUPT, "appended lifecycle record was not durably reconstructed")
        return committed[-1]

    def snapshot(self, *, trusted_prior: LifecycleSnapshot | None = None) -> LifecycleSnapshot:
        return create_lifecycle_snapshot(records=self.records(), expected_platform_writer=self._writer, trusted_prior=trusted_prior)

    def require_consumable(self, *, subject_ref: HashBoundRef, context: LifecycleContext) -> LifecycleRecord:
        subject = _ref(subject_ref, None, "subject_ref")
        requested = LifecycleContext.from_dict(context.to_dict())
        applicable = [
            item for item in self.records()
            if item.subject_ref == subject and (item.context == requested or item.context.scope is LifecycleScope.GLOBAL)
        ]
        if not applicable:
            raise _fail(LifecycleFailureCode.RECORD_NOT_CONSUMABLE, "subject has no applicable lifecycle record")
        global_head = next((item for item in reversed(applicable) if item.context.scope is LifecycleScope.GLOBAL), None)
        exact_head = next((item for item in reversed(applicable) if item.context == requested), None)
        for head in (global_head, exact_head):
            if head is not None and head.to_state in _CONSUMPTION_BLOCKING_STATES:
                raise _fail(LifecycleFailureCode.RECORD_NOT_CONSUMABLE, "applicable lifecycle state blocks consumption")
        head = exact_head if exact_head is not None else global_head
        if head is None:
            raise _fail(LifecycleFailureCode.RECORD_NOT_CONSUMABLE, "subject has no applicable lifecycle head")
        if head.to_state not in {
            LifecycleState.ADMITTED, LifecycleState.INDEXED, LifecycleState.RETRIEVED,
            LifecycleState.REVALIDATED, LifecycleState.REPLAYED, LifecycleState.CONSUMED,
            LifecycleState.OUTCOME_LINKED,
        }:
            raise _fail(LifecycleFailureCode.RECORD_NOT_CONSUMABLE, "subject has not reached an admissible state")
        return head

    def current_state(self, *, subject_ref: HashBoundRef, context: LifecycleContext) -> LifecycleState | None:
        subject = _ref(subject_ref, None, "subject_ref")
        requested = LifecycleContext.from_dict(context.to_dict())
        applicable = [item for item in self.records() if item.subject_ref == subject and (item.context == requested or item.context.scope is LifecycleScope.GLOBAL)]
        global_head = next((item for item in reversed(applicable) if item.context.scope is LifecycleScope.GLOBAL), None)
        exact_head = next((item for item in reversed(applicable) if item.context == requested), None)
        head = exact_head if exact_head is not None else global_head
        return None if head is None else head.to_state


def open_lifecycle_store(
    *,
    root: Path,
    authority_handle: Stage4AuthorityHandle,
    trusted_anchor: HistoryAnchor | None = None,
    allow_genesis: bool = False,
) -> LifecycleStore:
    _handle(authority_handle)
    return LifecycleStore(root, authority_handle, trusted_anchor, allow_genesis, _seal=_TRUSTED_STORE_SEAL)


__all__ = (
    "LIFECYCLE_CONTEXT_V1", "LIFECYCLE_AUTHORITY_PROPOSAL_V1", "LIFECYCLE_HEAD_V1",
    "LifecycleFailureCode", "LifecycleViolation", "LifecycleState", "LifecycleScope",
    "LifecycleAuthorityAction", "SupersessionDecisionKind", "LifecycleDecisionReason",
    "LifecycleContext", "LifecycleAuthorityProposal",
    "create_lifecycle_authority_proposal", "validate_lifecycle_authority_proposal",
    "lifecycle_authority_proposal_from_dict", "SupersessionDecision", "RevocationDecision",
    "ConfiguredLifecycleAuthorityEvaluator", "configure_lifecycle_authority_evaluator",
    "create_supersession_decision", "create_revocation_decision", "validate_supersession_decision",
    "validate_revocation_decision", "LifecycleRecord", "validate_lifecycle_record",
    "lifecycle_record_from_dict", "validate_lifecycle_history", "LifecycleHead",
    "LifecycleSnapshot", "create_lifecycle_snapshot", "validate_lifecycle_snapshot", "lifecycle_snapshot_from_dict",
    "LifecycleStore", "open_lifecycle_store",
)
