"""Immutable Stage 10 intent proposals with closed constraints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from ..canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from ..contracts import ActorIdentity, ProposalId, compute_proposal_id
from .repository_scope import RepositoryScope, validate_repository_scope


INTENT_SCHEMA_V1 = "synapse.stage4.gold.stage10.intent-candidate/v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_STATEMENT = 4096


class IntentFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_TEXT = "MALFORMED_TEXT"
    DUPLICATE = "DUPLICATE"
    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"
    CAPABILITY_WILDCARD = "CAPABILITY_WILDCARD"
    EFFECT_OUTSIDE_SCOPE = "EFFECT_OUTSIDE_SCOPE"
    EFFECT_CONFLICT = "EFFECT_CONFLICT"
    UNVERIFIABLE_EFFECT = "UNVERIFIABLE_EFFECT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    AUTHORITY_IN_PROPOSAL = "AUTHORITY_IN_PROPOSAL"


class IntentViolation(ValueError):
    def __init__(self, failure_code: IntentFailureCode, detail: str) -> None:
        if type(failure_code) is not IntentFailureCode:
            raise TypeError("failure_code must be an exact IntentFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: IntentFailureCode, detail: str) -> IntentViolation:
    return IntentViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _fail(IntentFailureCode.MALFORMED_IDENTIFIER, f"{field} must be a safe identifier")
    if "*" in value or "?" in value:
        raise _fail(IntentFailureCode.CAPABILITY_WILDCARD, f"{field} cannot contain wildcards")
    return value


def _statement(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > _MAX_STATEMENT or "\x00" in value:
        raise _fail(IntentFailureCode.MALFORMED_TEXT, f"{field} must be a bounded non-empty string")
    return value


def _actor(value: object, field: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, f"{field} must be an exact ActorIdentity")
    _identifier(value.value, field)
    return value


def _condition_ref(value: object, field: str) -> HashBoundRef:
    if type(value) is not HashBoundRef or value.kind is not RefKind.CONTRACT_CONDITION:
        raise _fail(IntentFailureCode.UNVERIFIABLE_EFFECT, f"{field} must be a contract-condition ref")
    return value


class EffectDisposition(str, Enum):
    EXPECTED = "EXPECTED"
    FORBIDDEN = "FORBIDDEN"


class EffectKind(str, Enum):
    PATH_CREATED = "PATH_CREATED"
    PATH_MODIFIED = "PATH_MODIFIED"
    PATH_DELETED = "PATH_DELETED"
    COMMAND_SUCCEEDS = "COMMAND_SUCCEEDS"
    DIAGNOSTIC_ABSENT = "DIAGNOSTIC_ABSENT"
    ARTIFACT_PUBLISHED = "ARTIFACT_PUBLISHED"


@dataclass(frozen=True)
class EffectConstraint:
    constraint_id: str
    disposition: EffectDisposition
    kind: EffectKind
    subject_path: str | None
    verification_ref: HashBoundRef

    def __post_init__(self) -> None:
        _identifier(self.constraint_id, "constraint_id")
        if type(self.disposition) is not EffectDisposition or type(self.kind) is not EffectKind:
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "effect enums must be exact")
        if self.subject_path is not None and type(self.subject_path) is not str:
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "effect subject_path must be a string or None")
        _condition_ref(self.verification_ref, "verification_ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "disposition": self.disposition.value,
            "kind": self.kind.value,
            "subject_path": self.subject_path,
            "verification_ref": self.verification_ref.to_dict(),
        }


class AcceptanceKind(str, Enum):
    CONTRACT_CONDITION = "CONTRACT_CONDITION"
    VERIFICATION_COMMAND = "VERIFICATION_COMMAND"
    REPLAY_OBSERVATION = "REPLAY_OBSERVATION"


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    kind: AcceptanceKind
    condition_ref: HashBoundRef
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.criterion_id, "criterion_id")
        if type(self.kind) is not AcceptanceKind:
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "acceptance kind must be exact")
        _condition_ref(self.condition_ref, "condition_ref")
        if type(self.argv) is not tuple or any(type(item) is not str or not item for item in self.argv):
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "acceptance argv must be a tuple of tokens")
        if self.kind is AcceptanceKind.VERIFICATION_COMMAND and not self.argv:
            raise _fail(IntentFailureCode.UNVERIFIABLE_EFFECT, "verification command requires argv")
        if self.kind is not AcceptanceKind.VERIFICATION_COMMAND and self.argv:
            raise _fail(IntentFailureCode.UNVERIFIABLE_EFFECT, "only command acceptance carries argv")

    def to_dict(self) -> dict[str, object]:
        return {
            "criterion_id": self.criterion_id,
            "kind": self.kind.value,
            "condition_ref": self.condition_ref.to_dict(),
            "argv": list(self.argv),
        }


@dataclass(frozen=True)
class IntentCandidate:
    schema_version: str
    proposal_id: ProposalId
    proposer: ActorIdentity
    source_actors: tuple[ActorIdentity, ...]
    task_statement: str
    repository_revision_sha256: str
    knowledge_snapshot_ref: HashBoundRef
    allowed_scope: RepositoryScope
    required_capabilities: tuple[str, ...]
    effects: tuple[EffectConstraint, ...]
    acceptance: tuple[AcceptanceCriterion, ...]
    uncertainties: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        validate_intent_candidate(self)
        return _canonical(_intent_payload(self))

    def to_dict(self) -> dict[str, object]:
        return {"proposal_id": self.proposal_id.to_dict(), "payload": _intent_payload(self)}


def _intent_payload(value: IntentCandidate) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "proposer": value.proposer.to_dict(),
        "source_actors": [item.to_dict() for item in value.source_actors],
        "task_statement": value.task_statement,
        "repository_revision_sha256": value.repository_revision_sha256,
        "knowledge_snapshot_ref": value.knowledge_snapshot_ref.to_dict(),
        "allowed_scope": value.allowed_scope.to_dict(),
        "required_capabilities": list(value.required_capabilities),
        "effects": [item.to_dict() for item in value.effects],
        "acceptance": [item.to_dict() for item in value.acceptance],
        "uncertainties": list(value.uncertainties),
    }


def propose_intent(
    *,
    proposer: ActorIdentity,
    source_actors: tuple[ActorIdentity, ...],
    task_statement: str,
    repository_revision_sha256: str,
    knowledge_snapshot_ref: HashBoundRef,
    allowed_scope: RepositoryScope,
    required_capabilities: tuple[str, ...],
    effects: tuple[EffectConstraint, ...],
    acceptance: tuple[AcceptanceCriterion, ...],
    uncertainties: tuple[str, ...] = (),
) -> IntentCandidate:
    fields = dict(
        schema_version=INTENT_SCHEMA_V1,
        proposer=proposer,
        source_actors=source_actors,
        task_statement=task_statement,
        repository_revision_sha256=repository_revision_sha256,
        knowledge_snapshot_ref=knowledge_snapshot_ref,
        allowed_scope=allowed_scope,
        required_capabilities=required_capabilities,
        effects=effects,
        acceptance=acceptance,
        uncertainties=uncertainties,
    )
    provisional = IntentCandidate(proposal_id=compute_proposal_id(canonical_bytes=b"{}"), **fields)
    proposal_id = compute_proposal_id(canonical_bytes=_canonical(_intent_payload(provisional)))
    result = IntentCandidate(proposal_id=proposal_id, **fields)
    validate_intent_candidate(result)
    return result


def validate_intent_candidate(value: IntentCandidate) -> None:
    if type(value) is not IntentCandidate:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, "intent must be an exact IntentCandidate")
    if value.schema_version != INTENT_SCHEMA_V1:
        raise _fail(IntentFailureCode.UNKNOWN_SCHEMA, "intent schema is unknown")
    _actor(value.proposer, "proposer")
    if type(value.source_actors) is not tuple or not value.source_actors:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, "source_actors must be a non-empty tuple")
    actor_values = tuple(_actor(item, "source actor").value for item in value.source_actors)
    if len(set(actor_values)) != len(actor_values):
        raise _fail(IntentFailureCode.DUPLICATE, "source_actors contains a duplicate")
    _statement(value.task_statement, "task_statement")
    if type(value.repository_revision_sha256) is not str or re.fullmatch(r"[0-9a-f]{40,64}", value.repository_revision_sha256) is None:
        raise _fail(IntentFailureCode.MALFORMED_IDENTIFIER, "repository revision must be a lowercase git hash")
    if type(value.knowledge_snapshot_ref) is not HashBoundRef or value.knowledge_snapshot_ref.kind is not RefKind.KNOWLEDGE_SNAPSHOT:
        raise _fail(IntentFailureCode.SNAPSHOT_REQUIRED, "intent requires a knowledge-snapshot ref")
    validate_repository_scope(value.allowed_scope)
    if type(value.required_capabilities) is not tuple or not value.required_capabilities:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, "required_capabilities must be non-empty")
    capabilities = tuple(_identifier(item, "capability") for item in value.required_capabilities)
    if capabilities != tuple(sorted(set(capabilities))):
        raise _fail(IntentFailureCode.DUPLICATE, "capabilities must be sorted and unique")
    if type(value.effects) is not tuple or not value.effects:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, "effects must be a non-empty tuple")
    effect_keys: set[tuple[EffectKind, str | None]] = set()
    ids: set[str] = set()
    dispositions: dict[tuple[EffectKind, str | None], EffectDisposition] = {}
    for effect in value.effects:
        if type(effect) is not EffectConstraint:
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "effect must be exact")
        EffectConstraint(**effect.__dict__)
        if effect.constraint_id in ids:
            raise _fail(IntentFailureCode.DUPLICATE, "effect id is duplicated")
        ids.add(effect.constraint_id)
        if effect.subject_path is not None and not value.allowed_scope.covers(effect.subject_path):
            raise _fail(IntentFailureCode.EFFECT_OUTSIDE_SCOPE, "effect subject is outside allowed scope")
        key = (effect.kind, effect.subject_path)
        previous = dispositions.get(key)
        if previous is not None and previous is not effect.disposition:
            raise _fail(IntentFailureCode.EFFECT_CONFLICT, "the same effect is expected and forbidden")
        dispositions[key] = effect.disposition
        effect_keys.add(key)
    if type(value.acceptance) is not tuple or not value.acceptance:
        raise _fail(IntentFailureCode.UNVERIFIABLE_EFFECT, "acceptance criteria are required")
    criterion_ids: set[str] = set()
    for criterion in value.acceptance:
        if type(criterion) is not AcceptanceCriterion:
            raise _fail(IntentFailureCode.TYPE_MISMATCH, "acceptance criterion must be exact")
        AcceptanceCriterion(**criterion.__dict__)
        if criterion.criterion_id in criterion_ids:
            raise _fail(IntentFailureCode.DUPLICATE, "acceptance criterion id is duplicated")
        criterion_ids.add(criterion.criterion_id)
    if type(value.uncertainties) is not tuple:
        raise _fail(IntentFailureCode.TYPE_MISMATCH, "uncertainties must be a tuple")
    for item in value.uncertainties:
        _statement(item, "uncertainty")
    if len(set(value.uncertainties)) != len(value.uncertainties):
        raise _fail(IntentFailureCode.DUPLICATE, "uncertainties contain a duplicate")
    expected_id = compute_proposal_id(canonical_bytes=_canonical(_intent_payload(value)))
    if value.proposal_id.to_dict() != expected_id.to_dict():
        raise _fail(IntentFailureCode.IDENTITY_MISMATCH, "intent id does not match canonical payload")


def intent_payload_sha256(value: IntentCandidate) -> str:
    validate_intent_candidate(value)
    return hashlib.sha256(value.canonical_bytes()).hexdigest()
