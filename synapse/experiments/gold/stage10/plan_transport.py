"""Strict canonical transport for Stage 10 plan records."""

from __future__ import annotations

from ..canonicalization import HashBoundRef
from ..contracts import ActorIdentity, IndependenceProof, compute_authority_decision_id
from .context_codec import decode_canonical, encode_canonical
from .intent import IntentCandidate
from .plan_authority import (
    AcceptedOperationPlan,
    ConfiguredPlanAuthority,
    PlanAuthorityDecision,
    PlanDecisionKind,
    PlanDecisionReason,
    accept_operation_plan,
    validate_decision_against_inputs,
)
from .planning import (
    FailureAction,
    OperationKind,
    OperationPlanCandidate,
    OperationRecord,
    PlanFailureCode,
    PlanViolation,
    VerificationKind,
    VerificationObligation,
    propose_operation_plan,
    plan_verification_obligations,
)
from .repository_scope import RepositoryScope


def encode_operation_plan(value: OperationPlanCandidate) -> bytes:
    return encode_canonical(value.to_dict())


def _plan_from_dict(value: object, *, intent: IntentCandidate) -> OperationPlanCandidate:
    if type(value) is not dict or set(value) != {"proposal_id", "payload"}:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "plan transport has an unknown shape")
    payload = value["payload"]
    required = {
        "schema_version",
        "intent_proposal_id",
        "intent_sha256",
        "proposer",
        "source_actors",
        "repository_revision_sha256",
        "knowledge_snapshot_ref",
        "allowed_scope",
        "capability_profile",
        "operations",
        "execution_order",
    }
    if type(payload) is not dict or set(payload) != required:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "plan payload has an unknown shape")
    sources = payload["source_actors"]
    capabilities = payload["capability_profile"]
    operations = payload["operations"]
    if type(sources) is not list or type(capabilities) is not list or type(operations) is not list:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "plan collections must be lists")
    parsed: list[OperationRecord] = []
    for item in operations:
        required_operation = {
            "operation_id",
            "kind",
            "subject_paths",
            "input_refs",
            "argv",
            "depends_on",
            "capability",
            "verification",
            "effect_constraint_ids",
            "acceptance_criterion_ids",
        }
        if type(item) is not dict or set(item) != required_operation:
            raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "operation transport has an unknown shape")
        verification_raw = item["verification"]
        verification = None
        if verification_raw is not None:
            if type(verification_raw) is not dict or set(verification_raw) != {
                "kind",
                "condition_ref",
                "failure_action",
            }:
                raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "verification transport has an unknown shape")
            verification = VerificationObligation(
                kind=VerificationKind(verification_raw["kind"]),
                condition_ref=HashBoundRef.from_dict(verification_raw["condition_ref"]),
                failure_action=FailureAction(verification_raw["failure_action"]),
            )
        for field in (
            "subject_paths",
            "input_refs",
            "argv",
            "depends_on",
            "effect_constraint_ids",
            "acceptance_criterion_ids",
        ):
            if type(item[field]) is not list:
                raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, f"operation {field} must be a list")
        parsed.append(
            OperationRecord(
                operation_id=item["operation_id"],
                kind=OperationKind(item["kind"]),
                subject_paths=tuple(item["subject_paths"]),
                input_refs=tuple(HashBoundRef.from_dict(ref) for ref in item["input_refs"]),
                argv=tuple(item["argv"]),
                depends_on=tuple(item["depends_on"]),
                capability=item["capability"],
                verification=verification,
                effect_constraint_ids=tuple(item["effect_constraint_ids"]),
                acceptance_criterion_ids=tuple(item["acceptance_criterion_ids"]),
            )
        )
    result = propose_operation_plan(
        intent=intent,
        proposer=ActorIdentity.from_dict(payload["proposer"]),
        source_actors=tuple(ActorIdentity.from_dict(item) for item in sources),
        allowed_scope=RepositoryScope.from_dict(payload["allowed_scope"]),
        capability_profile=tuple(capabilities),
        operations=tuple(parsed),
    )
    if result.proposal_id.to_dict() != value["proposal_id"] or result.to_dict() != value:
        raise PlanViolation(PlanFailureCode.IDENTITY_MISMATCH, "plan transport differs from canonical proposal")
    return result


def decode_operation_plan(value: object, *, intent: IntentCandidate) -> OperationPlanCandidate:
    decoded = decode_canonical(value)
    try:
        result = _plan_from_dict(decoded, intent=intent)
    except PlanViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "plan transport is invalid") from exc
    if encode_operation_plan(result) != value:
        raise PlanViolation(PlanFailureCode.IDENTITY_MISMATCH, "plan bytes do not round-trip")
    return result


def encode_plan_decision(value: PlanAuthorityDecision) -> bytes:
    return encode_canonical(value.to_dict())


def decode_plan_decision(
    value: object,
    *,
    plan: OperationPlanCandidate,
    intent: IntentCandidate,
    authority: ConfiguredPlanAuthority,
) -> PlanAuthorityDecision:
    decoded = decode_canonical(value)
    if type(decoded) is not dict or set(decoded) != {"decision_id", "payload"}:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "decision transport has an unknown shape")
    payload = decoded["payload"]
    required = {
        "schema_version",
        "plan_proposal_id",
        "plan_sha256",
        "intent_proposal_id",
        "intent_sha256",
        "decision",
        "reason",
        "policy_version",
        "policy_sha256",
        "independence_proof",
        "human_approval_ref",
        "validated_scope",
        "capability_profile",
        "oracle_ref",
        "knowledge_snapshot_ref",
        "compatibility_evidence_refs",
        "verification_obligations",
    }
    if type(payload) is not dict or set(payload) != required:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "decision payload has an unknown shape")
    try:
        compatibility_refs = payload["compatibility_evidence_refs"]
        if type(compatibility_refs) is not list:
            raise TypeError("compatibility evidence refs must be a list")
        proof = IndependenceProof.from_dict(
            payload["independence_proof"],
            proposal_canonical_bytes=plan.canonical_bytes(),
        )
        approval = (
            None
            if payload["human_approval_ref"] is None
            else HashBoundRef.from_dict(payload["human_approval_ref"])
        )
        result = PlanAuthorityDecision(
            schema_version=payload["schema_version"],
            decision_id=compute_authority_decision_id(canonical_bytes=encode_canonical(payload), independence_proof=proof),
            plan_proposal_id=plan.proposal_id, plan_sha256=payload["plan_sha256"],
            intent_proposal_id=intent.proposal_id, intent_sha256=payload["intent_sha256"],
            decision=PlanDecisionKind(payload["decision"]),
            reason=PlanDecisionReason(payload["reason"]),
            policy_version=payload["policy_version"], policy_sha256=payload["policy_sha256"],
            independence_proof=proof,
            human_approval_ref=approval,
            validated_scope=RepositoryScope.from_dict(payload["validated_scope"]),
            capability_profile=tuple(payload["capability_profile"]),
            oracle_ref=HashBoundRef.from_dict(payload["oracle_ref"]),
            knowledge_snapshot_ref=HashBoundRef.from_dict(payload["knowledge_snapshot_ref"]),
            compatibility_evidence_refs=tuple(
                HashBoundRef.from_dict(item) for item in compatibility_refs
            ),
            verification_obligations=plan_verification_obligations(plan),
        )
        validate_decision_against_inputs(result, plan=plan, intent=intent, authority=authority)
    except (TypeError, ValueError) as exc:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "decision transport is invalid") from exc
    if result.to_dict() != decoded or encode_plan_decision(result) != value:
        raise PlanViolation(PlanFailureCode.IDENTITY_MISMATCH, "decision bytes do not round-trip")
    return result


def encode_accepted_plan(value: AcceptedOperationPlan) -> bytes:
    return encode_canonical(value.to_dict())


def decode_accepted_plan(
    value: object,
    *,
    intent: IntentCandidate,
    authority: ConfiguredPlanAuthority,
) -> AcceptedOperationPlan:
    decoded = decode_canonical(value)
    if type(decoded) is not dict or set(decoded) != {"accepted_plan_id", "payload"}:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "accepted plan transport has an unknown shape")
    payload = decoded["payload"]
    if type(payload) is not dict or set(payload) != {"schema_version", "candidate", "decision"}:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "accepted plan payload has an unknown shape")
    try:
        plan = _plan_from_dict(payload["candidate"], intent=intent)
        decision_bytes = encode_canonical(payload["decision"])
        decision = decode_plan_decision(
            decision_bytes,
            plan=plan,
            intent=intent,
            authority=authority,
        )
        result = accept_operation_plan(
            plan=plan,
            intent=intent,
            decision=decision,
            authority=authority,
        )
    except PlanViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise PlanViolation(PlanFailureCode.TYPE_MISMATCH, "accepted plan transport is invalid") from exc
    if result.to_dict() != decoded or encode_accepted_plan(result) != value:
        raise PlanViolation(PlanFailureCode.IDENTITY_MISMATCH, "accepted plan bytes do not round-trip")
    return result
