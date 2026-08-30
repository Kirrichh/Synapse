"""Typed deterministic operation-plan proposals for Stage 10."""

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
from .intent import (
    AcceptanceCriterion,
    AcceptanceKind,
    EffectConstraint,
    EffectDisposition,
    EffectKind,
    IntentCandidate,
    intent_payload_sha256,
    validate_intent_candidate,
)
from .repository_scope import (
    RepositoryScope,
    normalize_repository_path,
    validate_repository_scope,
)


OPERATION_PLAN_SCHEMA_V1 = "synapse.stage4.gold.stage10.operation-plan-candidate/v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_TOKEN = 512
_MAX_ARGV_BYTES = 8192


class PlanFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_ARGV = "MALFORMED_ARGV"
    DUPLICATE = "DUPLICATE"
    EMPTY_PLAN = "EMPTY_PLAN"
    INTENT_MISMATCH = "INTENT_MISMATCH"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    CAPABILITY_EXPANSION = "CAPABILITY_EXPANSION"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    OPERATION_OUTSIDE_SCOPE = "OPERATION_OUTSIDE_SCOPE"
    HIDDEN_OPERATION = "HIDDEN_OPERATION"
    CYCLIC_GRAPH = "CYCLIC_GRAPH"
    VERIFICATION_MISSING = "VERIFICATION_MISSING"
    VERIFICATION_CONFLICT = "VERIFICATION_CONFLICT"
    EFFECT_BINDING_INVALID = "EFFECT_BINDING_INVALID"
    EFFECT_COVERAGE_MISSING = "EFFECT_COVERAGE_MISSING"
    FORBIDDEN_EFFECT = "FORBIDDEN_EFFECT"
    ACCEPTANCE_BINDING_INVALID = "ACCEPTANCE_BINDING_INVALID"
    ACCEPTANCE_COVERAGE_MISSING = "ACCEPTANCE_COVERAGE_MISSING"
    COMMAND_POLICY_EXPANSION = "COMMAND_POLICY_EXPANSION"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class PlanViolation(ValueError):
    def __init__(self, failure_code: PlanFailureCode, detail: str) -> None:
        if type(failure_code) is not PlanFailureCode:
            raise TypeError("failure_code must be an exact PlanFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: PlanFailureCode, detail: str) -> PlanViolation:
    return PlanViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _fail(PlanFailureCode.MALFORMED_IDENTIFIER, f"{field} must be a safe identifier")
    if "*" in value or "?" in value:
        raise _fail(PlanFailureCode.CAPABILITY_EXPANSION, f"{field} cannot contain wildcards")
    return value


def _argv(value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(PlanFailureCode.MALFORMED_ARGV, "argv must be an exact tuple")
    total = 0
    for token in value:
        if type(token) is not str or not token or len(token) > _MAX_TOKEN or "\x00" in token:
            raise _fail(PlanFailureCode.MALFORMED_ARGV, "argv contains an invalid token")
        total += len(token.encode("utf-8"))
    if total > _MAX_ARGV_BYTES:
        raise _fail(PlanFailureCode.MALFORMED_ARGV, "argv exceeds its byte budget")
    return value


class OperationKind(str, Enum):
    INSPECT_READ = "INSPECT_READ"
    RETRIEVE_KNOWLEDGE = "RETRIEVE_KNOWLEDGE"
    REPLAY_BEHAVIOR = "REPLAY_BEHAVIOR"
    EDIT_CONTROLLED_CHANGE = "EDIT_CONTROLLED_CHANGE"
    RUN_VERIFICATION_COMMAND = "RUN_VERIFICATION_COMMAND"
    RECORD_ACTIVITY = "RECORD_ACTIVITY"
    PUBLISH_CANDIDATE = "PUBLISH_CANDIDATE"


class RollbackPolicy(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MECHANICAL_BY_TYPED_DECISION = "MECHANICAL_BY_TYPED_DECISION"
    IRREVERSIBLE_REQUIRES_HUMAN = "IRREVERSIBLE_REQUIRES_HUMAN"


class FailureAction(str, Enum):
    ABORT_PLAN = "ABORT_PLAN"
    QUARANTINE_OPERATION = "QUARANTINE_OPERATION"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class VerificationKind(str, Enum):
    HASH_MATCH = "HASH_MATCH"
    CONTRACT_CONDITION = "CONTRACT_CONDITION"
    COMMAND_RESULT = "COMMAND_RESULT"
    REPLAY_RESULT = "REPLAY_RESULT"
    DURABLE_RECEIPT = "DURABLE_RECEIPT"


@dataclass(frozen=True)
class OperationProfile:
    capability: str
    side_effecting: bool
    verification_required: bool
    rollback_policy: RollbackPolicy


OPERATION_PROFILES: dict[OperationKind, OperationProfile] = {
    OperationKind.INSPECT_READ: OperationProfile("repository.read", False, False, RollbackPolicy.NOT_APPLICABLE),
    OperationKind.RETRIEVE_KNOWLEDGE: OperationProfile("knowledge.retrieve", False, False, RollbackPolicy.NOT_APPLICABLE),
    OperationKind.REPLAY_BEHAVIOR: OperationProfile("behavior.replay", False, True, RollbackPolicy.NOT_APPLICABLE),
    OperationKind.EDIT_CONTROLLED_CHANGE: OperationProfile("repository.edit", True, True, RollbackPolicy.MECHANICAL_BY_TYPED_DECISION),
    OperationKind.RUN_VERIFICATION_COMMAND: OperationProfile("verification.run", True, True, RollbackPolicy.MECHANICAL_BY_TYPED_DECISION),
    OperationKind.RECORD_ACTIVITY: OperationProfile("activity.record", True, True, RollbackPolicy.IRREVERSIBLE_REQUIRES_HUMAN),
    OperationKind.PUBLISH_CANDIDATE: OperationProfile("candidate.publish", True, True, RollbackPolicy.IRREVERSIBLE_REQUIRES_HUMAN),
}
CAPABILITY_BY_OPERATION = {kind: profile.capability for kind, profile in OPERATION_PROFILES.items()}


@dataclass(frozen=True)
class VerificationObligation:
    kind: VerificationKind
    condition_ref: HashBoundRef
    failure_action: FailureAction

    def __post_init__(self) -> None:
        if type(self.kind) is not VerificationKind or type(self.failure_action) is not FailureAction:
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "verification enums must be exact")
        if type(self.condition_ref) is not HashBoundRef or self.condition_ref.kind is not RefKind.CONTRACT_CONDITION:
            raise _fail(PlanFailureCode.VERIFICATION_MISSING, "verification requires a contract-condition ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "condition_ref": self.condition_ref.to_dict(),
            "failure_action": self.failure_action.value,
        }


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    kind: OperationKind
    subject_paths: tuple[str, ...]
    input_refs: tuple[HashBoundRef, ...]
    argv: tuple[str, ...]
    depends_on: tuple[str, ...]
    capability: str
    verification: VerificationObligation | None
    effect_constraint_ids: tuple[str, ...] = ()
    acceptance_criterion_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.operation_id, "operation_id")
        if type(self.kind) is not OperationKind:
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "operation kind must be exact")
        if type(self.subject_paths) is not tuple:
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "subject_paths must be a tuple")
        normalized = tuple(normalize_repository_path(item, field_name="operation subject") for item in self.subject_paths)
        if normalized != tuple(sorted(set(normalized))):
            raise _fail(PlanFailureCode.DUPLICATE, "subject paths must be sorted and unique")
        if type(self.input_refs) is not tuple or any(type(item) is not HashBoundRef for item in self.input_refs):
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "input_refs must be exact hash-bound refs")
        ref_keys = tuple((item.kind.value, item.ref_id, item.sha256) for item in self.input_refs)
        if ref_keys != tuple(sorted(set(ref_keys))):
            raise _fail(PlanFailureCode.DUPLICATE, "input refs must be sorted and unique")
        _argv(self.argv)
        if self.kind is OperationKind.RUN_VERIFICATION_COMMAND:
            if not self.argv:
                raise _fail(PlanFailureCode.MALFORMED_ARGV, "verification command requires argv")
        elif self.argv:
            raise _fail(PlanFailureCode.MALFORMED_ARGV, "only a verification command may carry argv")
        if type(self.depends_on) is not tuple:
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "depends_on must be a tuple")
        dependencies = tuple(_identifier(item, "dependency") for item in self.depends_on)
        if dependencies != tuple(sorted(set(dependencies))) or self.operation_id in dependencies:
            raise _fail(PlanFailureCode.DUPLICATE, "dependencies must be sorted, unique, and non-self")
        _identifier(self.capability, "capability")
        profile = OPERATION_PROFILES[self.kind]
        if self.capability != profile.capability:
            raise _fail(PlanFailureCode.CAPABILITY_MISMATCH, "operation capability does not match kind")
        if profile.verification_required and type(self.verification) is not VerificationObligation:
            raise _fail(PlanFailureCode.VERIFICATION_MISSING, "operation requires verification")
        if not profile.verification_required and self.verification is not None:
            raise _fail(PlanFailureCode.VERIFICATION_CONFLICT, "operation kind does not accept verification")
        for field, entries in (
            ("effect_constraint_ids", self.effect_constraint_ids),
            ("acceptance_criterion_ids", self.acceptance_criterion_ids),
        ):
            if type(entries) is not tuple:
                raise _fail(PlanFailureCode.TYPE_MISMATCH, f"{field} must be a tuple")
            checked = tuple(_identifier(item, field) for item in entries)
            if checked != tuple(sorted(set(checked))):
                raise _fail(PlanFailureCode.DUPLICATE, f"{field} must be sorted and unique")

    @property
    def side_effecting(self) -> bool:
        return OPERATION_PROFILES[self.kind].side_effecting

    @property
    def rollback_policy(self) -> RollbackPolicy:
        return OPERATION_PROFILES[self.kind].rollback_policy

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "subject_paths": list(self.subject_paths),
            "input_refs": [item.to_dict() for item in self.input_refs],
            "argv": list(self.argv),
            "depends_on": list(self.depends_on),
            "capability": self.capability,
            "verification": None if self.verification is None else self.verification.to_dict(),
            "effect_constraint_ids": list(self.effect_constraint_ids),
            "acceptance_criterion_ids": list(self.acceptance_criterion_ids),
        }


@dataclass(frozen=True)
class PlanVerificationObligation:
    operation_id: str
    capability: str
    effect_constraint_ids: tuple[str, ...]
    acceptance_criterion_ids: tuple[str, ...]
    verification: VerificationObligation

    def __post_init__(self) -> None:
        _identifier(self.operation_id, "verification operation_id")
        _identifier(self.capability, "verification capability")
        for field, entries in (
            ("effect_constraint_ids", self.effect_constraint_ids),
            ("acceptance_criterion_ids", self.acceptance_criterion_ids),
        ):
            if type(entries) is not tuple:
                raise _fail(PlanFailureCode.TYPE_MISMATCH, f"verification {field} must be a tuple")
            checked = tuple(_identifier(item, field) for item in entries)
            if checked != tuple(sorted(set(checked))):
                raise _fail(PlanFailureCode.DUPLICATE, f"verification {field} must be sorted and unique")
        if not self.effect_constraint_ids and not self.acceptance_criterion_ids:
            raise _fail(PlanFailureCode.VERIFICATION_MISSING, "verification is not bound to intent")
        if type(self.verification) is not VerificationObligation:
            raise _fail(PlanFailureCode.VERIFICATION_MISSING, "verification obligation must be exact")

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "capability": self.capability,
            "effect_constraint_ids": list(self.effect_constraint_ids),
            "acceptance_criterion_ids": list(self.acceptance_criterion_ids),
            "verification": self.verification.to_dict(),
        }


@dataclass(frozen=True)
class OperationPlanCandidate:
    schema_version: str
    proposal_id: ProposalId
    intent_proposal_id: ProposalId
    intent_sha256: str
    proposer: ActorIdentity
    source_actors: tuple[ActorIdentity, ...]
    repository_revision_sha256: str
    knowledge_snapshot_ref: HashBoundRef
    allowed_scope: RepositoryScope
    capability_profile: tuple[str, ...]
    operations: tuple[OperationRecord, ...]
    execution_order: tuple[str, ...]

    def canonical_bytes(self) -> bytes:
        validate_operation_plan_candidate(self)
        return _canonical(_plan_payload(self))

    def to_dict(self) -> dict[str, object]:
        return {"proposal_id": self.proposal_id.to_dict(), "payload": _plan_payload(self)}


def _plan_payload(value: OperationPlanCandidate) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "intent_proposal_id": value.intent_proposal_id.to_dict(),
        "intent_sha256": value.intent_sha256,
        "proposer": value.proposer.to_dict(),
        "source_actors": [item.to_dict() for item in value.source_actors],
        "repository_revision_sha256": value.repository_revision_sha256,
        "knowledge_snapshot_ref": value.knowledge_snapshot_ref.to_dict(),
        "allowed_scope": value.allowed_scope.to_dict(),
        "capability_profile": list(value.capability_profile),
        "operations": [item.to_dict() for item in value.operations],
        "execution_order": list(value.execution_order),
    }


def topological_operation_order(operations: tuple[OperationRecord, ...]) -> tuple[str, ...]:
    if type(operations) is not tuple or not operations:
        raise _fail(PlanFailureCode.EMPTY_PLAN, "plan requires at least one operation")
    by_id = {item.operation_id: item for item in operations}
    if len(by_id) != len(operations):
        raise _fail(PlanFailureCode.DUPLICATE, "operation id is duplicated")
    for item in operations:
        if any(dependency not in by_id for dependency in item.depends_on):
            raise _fail(PlanFailureCode.HIDDEN_OPERATION, "dependency names an undeclared operation")
    indegree = {key: len(item.depends_on) for key, item in by_id.items()}
    children: dict[str, list[str]] = {key: [] for key in by_id}
    for item in operations:
        for dependency in item.depends_on:
            children[dependency].append(item.operation_id)
    ready = sorted(key for key, count in indegree.items() if count == 0)
    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(result) != len(operations):
        raise _fail(PlanFailureCode.CYCLIC_GRAPH, "operation graph contains a cycle")
    return tuple(result)


def propose_operation_plan(
    *,
    intent: IntentCandidate,
    proposer: ActorIdentity,
    source_actors: tuple[ActorIdentity, ...],
    allowed_scope: RepositoryScope,
    capability_profile: tuple[str, ...],
    operations: tuple[OperationRecord, ...],
) -> OperationPlanCandidate:
    validate_intent_candidate(intent)
    fields = dict(
        schema_version=OPERATION_PLAN_SCHEMA_V1,
        intent_proposal_id=intent.proposal_id,
        intent_sha256=intent_payload_sha256(intent),
        proposer=proposer,
        source_actors=source_actors,
        repository_revision_sha256=intent.repository_revision_sha256,
        knowledge_snapshot_ref=intent.knowledge_snapshot_ref,
        allowed_scope=allowed_scope,
        capability_profile=capability_profile,
        operations=operations,
        execution_order=topological_operation_order(operations),
    )
    provisional = OperationPlanCandidate(proposal_id=compute_proposal_id(canonical_bytes=b"{}"), **fields)
    proposal_id = compute_proposal_id(canonical_bytes=_canonical(_plan_payload(provisional)))
    result = OperationPlanCandidate(proposal_id=proposal_id, **fields)
    validate_operation_plan_against_intent(result, intent=intent)
    return result


def validate_operation_plan_candidate(value: OperationPlanCandidate) -> None:
    if type(value) is not OperationPlanCandidate:
        raise _fail(PlanFailureCode.TYPE_MISMATCH, "plan must be an exact OperationPlanCandidate")
    if value.schema_version != OPERATION_PLAN_SCHEMA_V1:
        raise _fail(PlanFailureCode.UNKNOWN_SCHEMA, "operation plan schema is unknown")
    if type(value.proposer) is not ActorIdentity:
        raise _fail(PlanFailureCode.TYPE_MISMATCH, "plan proposer must be exact")
    if type(value.source_actors) is not tuple or not value.source_actors or any(type(item) is not ActorIdentity for item in value.source_actors):
        raise _fail(PlanFailureCode.TYPE_MISMATCH, "plan source actors must be a non-empty tuple")
    if len({item.value for item in value.source_actors}) != len(value.source_actors):
        raise _fail(PlanFailureCode.DUPLICATE, "plan source actor is duplicated")
    validate_repository_scope(value.allowed_scope)
    if type(value.capability_profile) is not tuple or not value.capability_profile:
        raise _fail(PlanFailureCode.TYPE_MISMATCH, "capability profile must be non-empty")
    capabilities = tuple(_identifier(item, "capability") for item in value.capability_profile)
    if capabilities != tuple(sorted(set(capabilities))):
        raise _fail(PlanFailureCode.DUPLICATE, "capability profile must be sorted and unique")
    if type(value.operations) is not tuple or not value.operations:
        raise _fail(PlanFailureCode.EMPTY_PLAN, "plan requires operations")
    for operation in value.operations:
        if type(operation) is not OperationRecord:
            raise _fail(PlanFailureCode.TYPE_MISMATCH, "operation must be exact")
        OperationRecord(**operation.__dict__)
        if operation.capability not in capabilities:
            raise _fail(PlanFailureCode.CAPABILITY_EXPANSION, "operation capability is outside plan profile")
        if any(not value.allowed_scope.covers(path) for path in operation.subject_paths):
            raise _fail(PlanFailureCode.OPERATION_OUTSIDE_SCOPE, "operation subject is outside plan scope")
    expected_order = topological_operation_order(value.operations)
    if type(value.execution_order) is not tuple or value.execution_order != expected_order:
        raise _fail(PlanFailureCode.CYCLIC_GRAPH, "execution order is not the deterministic graph order")
    expected_id = compute_proposal_id(canonical_bytes=_canonical(_plan_payload(value)))
    if value.proposal_id.to_dict() != expected_id.to_dict():
        raise _fail(PlanFailureCode.IDENTITY_MISMATCH, "plan id does not match canonical payload")


_EFFECT_OPERATION_KINDS: dict[EffectKind, frozenset[OperationKind]] = {
    EffectKind.PATH_CREATED: frozenset({OperationKind.EDIT_CONTROLLED_CHANGE}),
    EffectKind.PATH_MODIFIED: frozenset({OperationKind.EDIT_CONTROLLED_CHANGE}),
    EffectKind.PATH_DELETED: frozenset({OperationKind.EDIT_CONTROLLED_CHANGE}),
    EffectKind.COMMAND_SUCCEEDS: frozenset({OperationKind.RUN_VERIFICATION_COMMAND}),
    EffectKind.DIAGNOSTIC_ABSENT: frozenset(
        {OperationKind.RUN_VERIFICATION_COMMAND, OperationKind.REPLAY_BEHAVIOR}
    ),
    EffectKind.ARTIFACT_PUBLISHED: frozenset({OperationKind.PUBLISH_CANDIDATE}),
}


def _forbidden_intersects(operation: OperationRecord, effect: EffectConstraint) -> bool:
    if effect.kind in {EffectKind.PATH_CREATED, EffectKind.PATH_MODIFIED, EffectKind.PATH_DELETED}:
        return operation.side_effecting and effect.subject_path in operation.subject_paths
    if effect.kind is EffectKind.ARTIFACT_PUBLISHED:
        return operation.kind is OperationKind.PUBLISH_CANDIDATE and (
            effect.subject_path is None or effect.subject_path in operation.subject_paths
        )
    return (
        operation.kind in _EFFECT_OPERATION_KINDS[effect.kind]
        and operation.verification is not None
        and operation.verification.condition_ref == effect.verification_ref
    )


def _validate_effect_contract(value: OperationPlanCandidate, intent: IntentCandidate) -> None:
    by_id = {effect.constraint_id: effect for effect in intent.effects}
    expected = {
        effect.constraint_id
        for effect in intent.effects
        if effect.disposition is EffectDisposition.EXPECTED
    }
    bound: list[str] = []
    forbidden = tuple(
        effect for effect in intent.effects if effect.disposition is EffectDisposition.FORBIDDEN
    )
    for operation in value.operations:
        operation_effects = []
        for constraint_id in operation.effect_constraint_ids:
            effect = by_id.get(constraint_id)
            if effect is None or effect.disposition is not EffectDisposition.EXPECTED:
                raise _fail(PlanFailureCode.EFFECT_BINDING_INVALID, "operation binds an unknown or forbidden effect")
            if operation.kind not in _EFFECT_OPERATION_KINDS[effect.kind]:
                raise _fail(PlanFailureCode.EFFECT_BINDING_INVALID, "operation kind cannot produce its bound effect")
            if effect.subject_path is not None and effect.subject_path not in operation.subject_paths:
                raise _fail(PlanFailureCode.EFFECT_BINDING_INVALID, "operation does not target its bound effect path")
            if operation.verification is None or operation.verification.condition_ref != effect.verification_ref:
                raise _fail(PlanFailureCode.EFFECT_BINDING_INVALID, "effect is not bound to its required verification")
            operation_effects.append(effect)
            bound.append(constraint_id)
        if any(_forbidden_intersects(operation, effect) for effect in forbidden):
            raise _fail(PlanFailureCode.FORBIDDEN_EFFECT, "operation intersects a forbidden intent effect")
        effect_paths = {effect.subject_path for effect in operation_effects if effect.subject_path is not None}
        if operation.side_effecting and not set(operation.subject_paths).issubset(effect_paths):
            raise _fail(PlanFailureCode.HIDDEN_OPERATION, "side-effect path is not bound to an expected effect")
    if len(bound) != len(set(bound)):
        raise _fail(PlanFailureCode.EFFECT_BINDING_INVALID, "an expected effect is bound more than once")
    if set(bound) != expected:
        raise _fail(PlanFailureCode.EFFECT_COVERAGE_MISSING, "plan does not cover every expected effect")


@dataclass(frozen=True)
class _AcceptanceBindingResult:
    criterion_id: str
    command_binding: bool


def _validate_acceptance_binding(
    *,
    operation: OperationRecord,
    criterion_id: str,
    criterion: AcceptanceCriterion | None,
) -> _AcceptanceBindingResult:
    if criterion is None or operation.verification is None:
        raise _fail(
            PlanFailureCode.ACCEPTANCE_BINDING_INVALID,
            "operation binds unknown acceptance",
        )
    if type(criterion) is not AcceptanceCriterion:
        raise _fail(
            PlanFailureCode.ACCEPTANCE_BINDING_INVALID,
            "operation binds unknown acceptance",
        )
    if operation.verification.condition_ref != criterion.condition_ref:
        raise _fail(
            PlanFailureCode.ACCEPTANCE_BINDING_INVALID,
            "acceptance oracle differs from verification",
        )
    if criterion.kind is AcceptanceKind.VERIFICATION_COMMAND:
        if (
            operation.kind is not OperationKind.RUN_VERIFICATION_COMMAND
            or operation.verification.kind is not VerificationKind.COMMAND_RESULT
            or operation.argv != criterion.argv
        ):
            raise _fail(
                PlanFailureCode.COMMAND_POLICY_EXPANSION,
                "verification argv differs from intent",
            )
        return _AcceptanceBindingResult(criterion_id, True)
    if criterion.kind is AcceptanceKind.REPLAY_OBSERVATION:
        if (
            operation.kind is not OperationKind.REPLAY_BEHAVIOR
            or operation.verification.kind is not VerificationKind.REPLAY_RESULT
        ):
            raise _fail(
                PlanFailureCode.ACCEPTANCE_BINDING_INVALID,
                "replay acceptance has no replay verification",
            )
    elif operation.verification.kind is not VerificationKind.CONTRACT_CONDITION:
        raise _fail(
            PlanFailureCode.ACCEPTANCE_BINDING_INVALID,
            "contract acceptance has no contract verification",
        )
    return _AcceptanceBindingResult(criterion_id, False)


def _validate_acceptance_contract(value: OperationPlanCandidate, intent: IntentCandidate) -> None:
    by_id = {criterion.criterion_id: criterion for criterion in intent.acceptance}
    bound: list[str] = []
    for operation in value.operations:
        command_bindings = 0
        for criterion_id in operation.acceptance_criterion_ids:
            result = _validate_acceptance_binding(
                operation=operation,
                criterion_id=criterion_id,
                criterion=by_id.get(criterion_id),
            )
            if result.command_binding:
                command_bindings += 1
            bound.append(result.criterion_id)
        if operation.argv and command_bindings != 1:
            raise _fail(PlanFailureCode.COMMAND_POLICY_EXPANSION, "command is not bound to one exact intent argv")
        if operation.side_effecting and not (
            operation.effect_constraint_ids or operation.acceptance_criterion_ids
        ):
            raise _fail(PlanFailureCode.HIDDEN_OPERATION, "side effect is not bound to intent")
    if len(bound) != len(set(bound)):
        raise _fail(PlanFailureCode.ACCEPTANCE_BINDING_INVALID, "acceptance is bound more than once")
    if set(bound) != set(by_id):
        raise _fail(PlanFailureCode.ACCEPTANCE_COVERAGE_MISSING, "plan does not cover every acceptance criterion")


def validate_operation_plan_against_intent(
    value: OperationPlanCandidate,
    *,
    intent: IntentCandidate,
) -> None:
    validate_operation_plan_candidate(value)
    validate_intent_candidate(intent)
    if value.intent_proposal_id.to_dict() != intent.proposal_id.to_dict() or value.intent_sha256 != intent_payload_sha256(intent):
        raise _fail(PlanFailureCode.INTENT_MISMATCH, "plan is not bound to the supplied intent")
    if value.repository_revision_sha256 != intent.repository_revision_sha256 or value.knowledge_snapshot_ref != intent.knowledge_snapshot_ref:
        raise _fail(PlanFailureCode.INTENT_MISMATCH, "plan drifted from intent snapshot")
    if not intent.allowed_scope.contains_scope(value.allowed_scope):
        raise _fail(PlanFailureCode.SCOPE_EXPANSION, "plan scope is wider than intent scope")
    if not set(value.capability_profile).issubset(intent.required_capabilities):
        raise _fail(PlanFailureCode.CAPABILITY_EXPANSION, "plan capability profile is wider than intent")
    _validate_effect_contract(value, intent)
    _validate_acceptance_contract(value, intent)


def plan_verification_obligations(
    value: OperationPlanCandidate,
) -> tuple[PlanVerificationObligation, ...]:
    validate_operation_plan_candidate(value)
    operations = {operation.operation_id: operation for operation in value.operations}
    return tuple(
        PlanVerificationObligation(
            operation_id=operation_id,
            capability=operations[operation_id].capability,
            effect_constraint_ids=operations[operation_id].effect_constraint_ids,
            acceptance_criterion_ids=operations[operation_id].acceptance_criterion_ids,
            verification=operations[operation_id].verification,
        )
        for operation_id in value.execution_order
        if operations[operation_id].verification is not None
    )


def plan_payload_sha256(value: OperationPlanCandidate) -> str:
    validate_operation_plan_candidate(value)
    return hashlib.sha256(value.canonical_bytes()).hexdigest()
