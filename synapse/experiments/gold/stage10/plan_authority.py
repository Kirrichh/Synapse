"""Independent authority decisions and immutable accepted operation plans."""

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
from ..contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    AuthorityIdentity,
    AuthorityRole,
    ExecutionId,
    IndependenceProof,
    ReasonCode,
    ProposalId,
    SchemaVersion,
    compute_authority_decision_id,
    compute_execution_id,
    create_independence_proof,
    validate_independence_proof,
)
from .intent import IntentCandidate, intent_payload_sha256, validate_intent_candidate
from .planning import (
    OPERATION_PROFILES,
    OperationKind,
    OperationPlanCandidate,
    RollbackPolicy,
    plan_payload_sha256,
    validate_operation_plan_against_intent,
    validate_operation_plan_candidate,
)


PLAN_POLICY_SCHEMA_V1 = "synapse.stage4.gold.stage10.plan-authority-policy/v1"
PLAN_DECISION_SCHEMA_V1 = "synapse.stage4.gold.stage10.plan-authority-decision/v1"
ACCEPTED_PLAN_SCHEMA_V1 = "synapse.stage4.gold.stage10.accepted-operation-plan/v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_AUTHORITY_SEAL = object()


class AuthorityFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    POLICY_MISCONFIGURED = "POLICY_MISCONFIGURED"
    POLICY_REJECTED = "POLICY_REJECTED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    HUMAN_APPROVAL_INVALID = "HUMAN_APPROVAL_INVALID"
    WRONG_AUTHORITY_ROLE = "WRONG_AUTHORITY_ROLE"
    INDEPENDENCE_UNPROVEN = "INDEPENDENCE_UNPROVEN"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    PLAN_NOT_ACCEPTED = "PLAN_NOT_ACCEPTED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PLAN_DRIFT = "PLAN_DRIFT"


class AuthorityViolation(ValueError):
    def __init__(self, failure_code: AuthorityFailureCode, detail: str) -> None:
        if type(failure_code) is not AuthorityFailureCode:
            raise TypeError("failure_code must be an exact AuthorityFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: AuthorityFailureCode, detail: str) -> AuthorityViolation:
    return AuthorityViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, f"{field} must be a safe identifier")
    return value


class PlanDecisionKind(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class PlanDecisionReason(str, Enum):
    POLICY_ACCEPTED = "POLICY_ACCEPTED"
    POLICY_REJECTED = "POLICY_REJECTED"
    IRREVERSIBLE_EFFECT = "IRREVERSIBLE_EFFECT"
    SENSITIVE_CAPABILITY = "SENSITIVE_CAPABILITY"
    OPEN_UNCERTAINTY = "OPEN_UNCERTAINTY"
    GOVERNING_HUMAN_ACCEPTED = "GOVERNING_HUMAN_ACCEPTED"
    GOVERNING_HUMAN_REJECTED = "GOVERNING_HUMAN_REJECTED"


@dataclass(frozen=True)
class PlanAuthorityPolicy:
    schema_version: str
    policy_version: str
    allowed_operation_kinds: tuple[OperationKind, ...]
    allowed_capabilities: tuple[str, ...]
    human_review_capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_plan_authority_policy(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "allowed_operation_kinds": [item.value for item in self.allowed_operation_kinds],
            "allowed_capabilities": list(self.allowed_capabilities),
            "human_review_capabilities": list(self.human_review_capabilities),
        }

    def canonical_bytes(self) -> bytes:
        validate_plan_authority_policy(self)
        return _canonical(self.to_dict())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def validate_plan_authority_policy(value: PlanAuthorityPolicy) -> None:
    if type(value) is not PlanAuthorityPolicy:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "policy must be exact")
    if value.schema_version != PLAN_POLICY_SCHEMA_V1:
        raise _fail(AuthorityFailureCode.UNKNOWN_SCHEMA, "plan policy schema is unknown")
    _identifier(value.policy_version, "policy_version")
    if type(value.allowed_operation_kinds) is not tuple or not value.allowed_operation_kinds:
        raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, "allowed operation kinds are required")
    if any(type(item) is not OperationKind for item in value.allowed_operation_kinds):
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "allowed operation kind must be exact")
    kind_values = tuple(item.value for item in value.allowed_operation_kinds)
    if kind_values != tuple(sorted(set(kind_values))):
        raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, "operation kinds must be sorted and unique")
    for name, entries in (
        ("allowed_capabilities", value.allowed_capabilities),
        ("human_review_capabilities", value.human_review_capabilities),
    ):
        if type(entries) is not tuple:
            raise _fail(AuthorityFailureCode.TYPE_MISMATCH, f"{name} must be a tuple")
        checked = tuple(_identifier(item, name) for item in entries)
        if checked != tuple(sorted(set(checked))):
            raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, f"{name} must be sorted and unique")
    if not set(value.human_review_capabilities).issubset(value.allowed_capabilities):
        raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, "human-review capabilities must be allowed")
    for kind in value.allowed_operation_kinds:
        if OPERATION_PROFILES[kind].capability not in value.allowed_capabilities:
            raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, "operation kind has no allowed capability")


@dataclass(frozen=True, init=False)
class ConfiguredPlanAuthority:
    policy: PlanAuthorityPolicy
    reviewer_authority: AuthorityIdentity
    governing_human_authority: AuthorityIdentity | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ConfiguredPlanAuthority:
        raise TypeError("ConfiguredPlanAuthority is process-local and factory-created")


def configure_plan_authority(
    *,
    policy: PlanAuthorityPolicy,
    reviewer_authority: AuthorityIdentity,
    governing_human_authority: AuthorityIdentity | None,
) -> ConfiguredPlanAuthority:
    validate_plan_authority_policy(policy)
    if type(reviewer_authority) is not AuthorityIdentity:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "reviewer authority must be exact")
    if governing_human_authority is not None and type(governing_human_authority) is not AuthorityIdentity:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "governing human authority must be exact or None")
    if governing_human_authority == reviewer_authority:
        raise _fail(AuthorityFailureCode.INDEPENDENCE_UNPROVEN, "reviewer and governing human must differ")
    result = object.__new__(ConfiguredPlanAuthority)
    object.__setattr__(result, "policy", policy)
    object.__setattr__(result, "reviewer_authority", reviewer_authority)
    object.__setattr__(result, "governing_human_authority", governing_human_authority)
    object.__setattr__(result, "_trusted_seal", _AUTHORITY_SEAL)
    require_configured_plan_authority(result)
    return result


def require_configured_plan_authority(value: ConfiguredPlanAuthority) -> None:
    if type(value) is not ConfiguredPlanAuthority or getattr(value, "_trusted_seal", None) is not _AUTHORITY_SEAL:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "plan authority is not configured")
    validate_plan_authority_policy(value.policy)
    if type(value.reviewer_authority) is not AuthorityIdentity:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "configured reviewer is invalid")
    if value.governing_human_authority is not None and type(value.governing_human_authority) is not AuthorityIdentity:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "configured governing human is invalid")


@dataclass(frozen=True)
class PlanAuthorityDecision:
    schema_version: str
    decision_id: AuthorityDecisionId
    plan_proposal_id: ProposalId
    plan_sha256: str
    intent_proposal_id: ProposalId
    intent_sha256: str
    decision: PlanDecisionKind
    reason: PlanDecisionReason
    policy_version: str
    policy_sha256: str
    independence_proof: IndependenceProof
    human_approval_ref: HashBoundRef | None

    def canonical_bytes(self) -> bytes:
        validate_plan_authority_decision(self)
        return _canonical(_decision_payload(self))

    def to_dict(self) -> dict[str, object]:
        return {"decision_id": self.decision_id.to_dict(), "payload": _decision_payload(self)}


def _decision_payload(value: PlanAuthorityDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "plan_proposal_id": value.plan_proposal_id.to_dict(),
        "plan_sha256": value.plan_sha256,
        "intent_proposal_id": value.intent_proposal_id.to_dict(),
        "intent_sha256": value.intent_sha256,
        "decision": value.decision.value,
        "reason": value.reason.value,
        "policy_version": value.policy_version,
        "policy_sha256": value.policy_sha256,
        "independence_proof": value.independence_proof.to_dict(),
        "human_approval_ref": None if value.human_approval_ref is None else value.human_approval_ref.to_dict(),
    }


def _actual_participants(
    *,
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
) -> tuple[tuple[ActorIdentity, ...], tuple[ActorIdentity, ...]]:
    producer_by_id = {intent.proposer.value: intent.proposer, plan.proposer.value: plan.proposer}
    source_by_id = {item.value: item for item in (*intent.source_actors, *plan.source_actors)}
    return (
        tuple(producer_by_id[key] for key in sorted(producer_by_id)),
        tuple(source_by_id[key] for key in sorted(source_by_id)),
    )


def _risk_reason(
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
    policy: PlanAuthorityPolicy,
) -> PlanDecisionReason | None:
    if intent.uncertainties:
        return PlanDecisionReason.OPEN_UNCERTAINTY
    if any(
        OPERATION_PROFILES[item.kind].rollback_policy is RollbackPolicy.IRREVERSIBLE_REQUIRES_HUMAN
        for item in plan.operations
    ):
        return PlanDecisionReason.IRREVERSIBLE_EFFECT
    if set(plan.capability_profile) & set(policy.human_review_capabilities):
        return PlanDecisionReason.SENSITIVE_CAPABILITY
    return None


def decide_operation_plan(
    *,
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
    authority: ConfiguredPlanAuthority,
    executor: ActorIdentity | None,
    requested_decision: PlanDecisionKind,
    human_approval_ref: HashBoundRef | None = None,
) -> PlanAuthorityDecision:
    validate_operation_plan_against_intent(plan, intent=intent)
    require_configured_plan_authority(authority)
    policy = authority.policy
    if type(requested_decision) is not PlanDecisionKind:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "authority and requested decision must be exact")
    if human_approval_ref is not None and (
        type(human_approval_ref) is not HashBoundRef
        or human_approval_ref.kind is not RefKind.CONTRACT_CONDITION
    ):
        raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_INVALID, "human approval ref is invalid")
    allowed_kinds = set(policy.allowed_operation_kinds)
    policy_allows = all(item.kind in allowed_kinds for item in plan.operations) and set(
        plan.capability_profile
    ).issubset(policy.allowed_capabilities)
    risk = _risk_reason(plan, intent, policy)
    if requested_decision is PlanDecisionKind.ACCEPT and not policy_allows:
        raise _fail(AuthorityFailureCode.POLICY_REJECTED, "policy does not allow the plan")
    if risk is not None and requested_decision is PlanDecisionKind.ACCEPT:
        if type(human_approval_ref) is not HashBoundRef or human_approval_ref.kind is not RefKind.CONTRACT_CONDITION:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_REQUIRED, "risky plan requires a hash-bound human approval")
        role = AuthorityRole.GOVERNING_HUMAN
        common_reason = ReasonCode.GOVERNING_HUMAN_INDEPENDENT
        reason = PlanDecisionReason.GOVERNING_HUMAN_ACCEPTED
        decision_authority = authority.governing_human_authority
        if decision_authority is None:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_REQUIRED, "no governing human authority is configured")
    elif requested_decision is PlanDecisionKind.ACCEPT:
        if human_approval_ref is not None:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_INVALID, "non-human decision cannot carry human approval")
        role = AuthorityRole.PLAN_REVIEWER
        common_reason = ReasonCode.PLAN_REVIEW_INDEPENDENT
        reason = PlanDecisionReason.POLICY_ACCEPTED
        decision_authority = authority.reviewer_authority
    elif requested_decision is PlanDecisionKind.REQUIRE_HUMAN_REVIEW:
        if risk is None:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_INVALID, "plan has no configured human-review trigger")
        if human_approval_ref is not None:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_INVALID, "review routing is not human approval")
        role = AuthorityRole.PLAN_REVIEWER
        common_reason = ReasonCode.PLAN_REVIEW_INDEPENDENT
        reason = risk
        decision_authority = authority.reviewer_authority
    else:
        role = AuthorityRole.GOVERNING_HUMAN if human_approval_ref is not None else AuthorityRole.PLAN_REVIEWER
        common_reason = (
            ReasonCode.GOVERNING_HUMAN_INDEPENDENT
            if role is AuthorityRole.GOVERNING_HUMAN
            else ReasonCode.PLAN_REVIEW_INDEPENDENT
        )
        reason = (
            PlanDecisionReason.GOVERNING_HUMAN_REJECTED
            if role is AuthorityRole.GOVERNING_HUMAN
            else PlanDecisionReason.POLICY_REJECTED
        )
        decision_authority = (
            authority.governing_human_authority
            if role is AuthorityRole.GOVERNING_HUMAN
            else authority.reviewer_authority
        )
        if decision_authority is None:
            raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_REQUIRED, "no governing human authority is configured")
    producers, sources = _actual_participants(plan=plan, intent=intent)
    proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=plan.proposal_id,
        authority_identity=decision_authority,
        authority_role=role,
        reason_code=common_reason,
        producer_actor_ids=producers,
        source_actor_ids=sources,
        proposer_identity=plan.proposer,
        executor_identity=executor,
        subject_derived_actor_ids=(),
        delegation_chain=(),
    )
    fields = dict(
        schema_version=PLAN_DECISION_SCHEMA_V1,
        plan_proposal_id=plan.proposal_id,
        plan_sha256=plan_payload_sha256(plan),
        intent_proposal_id=intent.proposal_id,
        intent_sha256=intent_payload_sha256(intent),
        decision=requested_decision,
        reason=reason,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        independence_proof=proof,
        human_approval_ref=human_approval_ref,
    )
    provisional = PlanAuthorityDecision(
        decision_id=compute_authority_decision_id(canonical_bytes=b"{}", independence_proof=proof),
        **fields,
    )
    decision_id = compute_authority_decision_id(
        canonical_bytes=_canonical(_decision_payload(provisional)),
        independence_proof=proof,
    )
    result = PlanAuthorityDecision(decision_id=decision_id, **fields)
    validate_decision_against_inputs(result, plan=plan, intent=intent, authority=authority)
    return result


def validate_plan_authority_decision(value: PlanAuthorityDecision) -> None:
    if type(value) is not PlanAuthorityDecision:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "decision must be exact")
    if value.schema_version != PLAN_DECISION_SCHEMA_V1:
        raise _fail(AuthorityFailureCode.UNKNOWN_SCHEMA, "decision schema is unknown")
    if type(value.decision) is not PlanDecisionKind or type(value.reason) is not PlanDecisionReason:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "decision enums must be exact")
    if type(value.plan_proposal_id) is not ProposalId or type(value.intent_proposal_id) is not ProposalId:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "decision proposal ids must be exact")
    _identifier(value.policy_version, "policy_version")
    if type(value.policy_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.policy_sha256) is None:
        raise _fail(AuthorityFailureCode.POLICY_MISCONFIGURED, "policy hash is malformed")
    validate_independence_proof(value.independence_proof)
    if value.independence_proof.subject_proposal_id.to_dict() != value.plan_proposal_id.to_dict():
        raise _fail(AuthorityFailureCode.INDEPENDENCE_UNPROVEN, "proof belongs to another plan")
    human_reasons = {
        PlanDecisionReason.GOVERNING_HUMAN_ACCEPTED,
        PlanDecisionReason.GOVERNING_HUMAN_REJECTED,
    }
    if value.reason in human_reasons:
        if (
            value.independence_proof.authority_role is not AuthorityRole.GOVERNING_HUMAN
            or type(value.human_approval_ref) is not HashBoundRef
            or value.human_approval_ref.kind is not RefKind.CONTRACT_CONDITION
        ):
            raise _fail(AuthorityFailureCode.WRONG_AUTHORITY_ROLE, "human decision lacks exact human authority evidence")
    elif value.human_approval_ref is not None:
        raise _fail(AuthorityFailureCode.HUMAN_APPROVAL_INVALID, "non-human decision carries human approval")
    if value.decision is PlanDecisionKind.ACCEPT and value.reason not in {
        PlanDecisionReason.POLICY_ACCEPTED,
        PlanDecisionReason.GOVERNING_HUMAN_ACCEPTED,
    }:
        raise _fail(AuthorityFailureCode.DECISION_MISMATCH, "accept decision reason is inconsistent")
    if value.decision is PlanDecisionKind.REQUIRE_HUMAN_REVIEW and value.reason not in {
        PlanDecisionReason.IRREVERSIBLE_EFFECT,
        PlanDecisionReason.SENSITIVE_CAPABILITY,
        PlanDecisionReason.OPEN_UNCERTAINTY,
    }:
        raise _fail(AuthorityFailureCode.DECISION_MISMATCH, "human-review decision reason is inconsistent")
    expected = compute_authority_decision_id(
        canonical_bytes=_canonical(_decision_payload(value)),
        independence_proof=value.independence_proof,
    )
    if value.decision_id.to_dict() != expected.to_dict():
        raise _fail(AuthorityFailureCode.IDENTITY_MISMATCH, "decision id does not match payload")


def validate_decision_against_inputs(
    value: PlanAuthorityDecision,
    *,
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
    authority: ConfiguredPlanAuthority,
) -> None:
    validate_plan_authority_decision(value)
    validate_operation_plan_against_intent(plan, intent=intent)
    require_configured_plan_authority(authority)
    policy = authority.policy
    if (
        value.plan_proposal_id.to_dict() != plan.proposal_id.to_dict()
        or value.plan_sha256 != plan_payload_sha256(plan)
        or value.intent_proposal_id.to_dict() != intent.proposal_id.to_dict()
        or value.intent_sha256 != intent_payload_sha256(intent)
    ):
        raise _fail(AuthorityFailureCode.DECISION_MISMATCH, "decision is bound to different proposal bytes")
    if value.policy_version != policy.policy_version or value.policy_sha256 != policy.sha256:
        raise _fail(AuthorityFailureCode.DECISION_MISMATCH, "decision is bound to a different policy")
    producers, sources = _actual_participants(plan=plan, intent=intent)
    proof = value.independence_proof
    configured_identity = (
        authority.governing_human_authority
        if proof.authority_role is AuthorityRole.GOVERNING_HUMAN
        else authority.reviewer_authority
    )
    if configured_identity is None or proof.authority_identity != configured_identity:
        raise _fail(AuthorityFailureCode.WRONG_AUTHORITY_ROLE, "decision was not made by the configured authority")
    if (
        tuple(item.value for item in proof.producer_actor_ids) != tuple(item.value for item in producers)
        or tuple(item.value for item in proof.source_actor_ids) != tuple(item.value for item in sources)
        or proof.proposer_identity != plan.proposer
    ):
        raise _fail(AuthorityFailureCode.INDEPENDENCE_UNPROVEN, "proof omits an actual participant")


@dataclass(frozen=True)
class AcceptedOperationPlan:
    schema_version: str
    accepted_plan_id: ExecutionId
    candidate: OperationPlanCandidate
    decision: PlanAuthorityDecision

    def canonical_bytes(self) -> bytes:
        validate_accepted_operation_plan(self)
        return _canonical(_accepted_payload(self))

    def to_dict(self) -> dict[str, object]:
        return {"accepted_plan_id": self.accepted_plan_id.to_dict(), "payload": _accepted_payload(self)}


def _accepted_payload(value: AcceptedOperationPlan) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "candidate": value.candidate.to_dict(),
        "decision": value.decision.to_dict(),
    }


def accept_operation_plan(
    *,
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
    decision: PlanAuthorityDecision,
    authority: ConfiguredPlanAuthority,
) -> AcceptedOperationPlan:
    validate_decision_against_inputs(decision, plan=plan, intent=intent, authority=authority)
    if decision.decision is not PlanDecisionKind.ACCEPT:
        raise _fail(AuthorityFailureCode.PLAN_NOT_ACCEPTED, "only an ACCEPT decision creates an accepted plan")
    provisional = AcceptedOperationPlan(
        schema_version=ACCEPTED_PLAN_SCHEMA_V1,
        accepted_plan_id=compute_execution_id(canonical_bytes=b"{}", authority_decision_id=decision.decision_id),
        candidate=plan,
        decision=decision,
    )
    accepted_id = compute_execution_id(
        canonical_bytes=_canonical(_accepted_payload(provisional)),
        authority_decision_id=decision.decision_id,
    )
    result = AcceptedOperationPlan(
        schema_version=ACCEPTED_PLAN_SCHEMA_V1,
        accepted_plan_id=accepted_id,
        candidate=plan,
        decision=decision,
    )
    validate_accepted_operation_plan(result)
    return result


def validate_accepted_operation_plan(value: AcceptedOperationPlan) -> None:
    if type(value) is not AcceptedOperationPlan:
        raise _fail(AuthorityFailureCode.TYPE_MISMATCH, "accepted plan must be exact")
    if value.schema_version != ACCEPTED_PLAN_SCHEMA_V1:
        raise _fail(AuthorityFailureCode.UNKNOWN_SCHEMA, "accepted plan schema is unknown")
    validate_operation_plan_candidate(value.candidate)
    validate_plan_authority_decision(value.decision)
    if value.decision.decision is not PlanDecisionKind.ACCEPT:
        raise _fail(AuthorityFailureCode.PLAN_NOT_ACCEPTED, "accepted plan carries a non-accept decision")
    if value.decision.plan_proposal_id.to_dict() != value.candidate.proposal_id.to_dict() or value.decision.plan_sha256 != plan_payload_sha256(value.candidate):
        raise _fail(AuthorityFailureCode.PLAN_DRIFT, "accepted plan candidate differs from authority decision")
    expected = compute_execution_id(
        canonical_bytes=_canonical(_accepted_payload(value)),
        authority_decision_id=value.decision.decision_id,
    )
    if value.accepted_plan_id.to_dict() != expected.to_dict():
        raise _fail(AuthorityFailureCode.IDENTITY_MISMATCH, "accepted plan id does not match exact bytes")


def require_no_plan_drift(
    accepted: AcceptedOperationPlan,
    candidate: OperationPlanCandidate,
) -> None:
    validate_accepted_operation_plan(accepted)
    validate_operation_plan_candidate(candidate)
    if candidate.proposal_id.to_dict() != accepted.candidate.proposal_id.to_dict() or plan_payload_sha256(candidate) != plan_payload_sha256(accepted.candidate):
        raise _fail(AuthorityFailureCode.PLAN_DRIFT, "candidate content changed after acceptance")
