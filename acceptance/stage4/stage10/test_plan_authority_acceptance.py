from __future__ import annotations

from dataclasses import replace

import pytest

from synapse.experiments.gold.contracts import AuthorityIdentity
from synapse.experiments.gold.stage10.plan_authority import (
    PLAN_POLICY_SCHEMA_V1,
    AuthorityFailureCode,
    AuthorityViolation,
    PlanAuthorityPolicy,
    PlanDecisionKind,
    configure_plan_authority,
    decide_operation_plan,
    require_no_plan_drift,
)
from synapse.experiments.gold.stage10.planning import OperationKind
from synapse.experiments.gold.stage10.plan_transport import (
    decode_accepted_plan,
    encode_accepted_plan,
)

from acceptance.stage4.stage10._builders import plan_world, validate_plan_compatibility


def test_plan_decision_and_accepted_plan_have_distinct_identities() -> None:
    intent, plan, _policy, authority, decision, accepted = plan_world()

    assert plan.proposal_id.record_id.digest_sha256 != decision.decision_id.record_id.digest_sha256
    assert accepted.accepted_plan_id.record_id.digest_sha256 != plan.proposal_id.record_id.digest_sha256
    encoded = encode_accepted_plan(accepted)
    assert decode_accepted_plan(
        encoded,
        intent=intent,
        authority=authority,
    ) == accepted
    assert decision.validated_scope == plan.allowed_scope
    assert decision.capability_profile == plan.capability_profile
    assert decision.oracle_ref == intent.acceptance[0].condition_ref
    assert decision.knowledge_snapshot_ref == intent.knowledge_snapshot_ref
    assert decision.verification_obligations[0].operation_id == plan.operations[0].operation_id


def test_actual_producer_cannot_be_configured_as_accepting_authority() -> None:
    intent, plan, policy, _authority, decision, _accepted = plan_world()
    self_authority = configure_plan_authority(
        policy=policy,
        reviewer_authority=AuthorityIdentity(plan.proposer.value),
        governing_human_authority=AuthorityIdentity("separate-human"),
        compatibility_validator=validate_plan_compatibility,
    )

    with pytest.raises(ValueError):
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=self_authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=decision.compatibility_evidence_refs,
        )


def test_accept_requires_compatibility_evidence_and_exact_validation() -> None:
    intent, plan, _policy, authority, decision, _accepted = plan_world()

    with pytest.raises(AuthorityViolation) as missing:
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
        )
    assert missing.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID

    rejecting_authority = configure_plan_authority(
        policy=authority.policy,
        reviewer_authority=AuthorityIdentity("rejecting-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("rejecting-governing-human"),
        compatibility_validator=lambda _plan, _intent, _refs: (),
    )
    with pytest.raises(AuthorityViolation) as invalid:
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=rejecting_authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=decision.compatibility_evidence_refs,
        )
    assert invalid.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID


def test_authority_refuses_missing_or_rewired_compatibility_configuration() -> None:
    intent, plan, policy, authority, decision, _accepted = plan_world()

    with pytest.raises(AuthorityViolation) as missing:
        configure_plan_authority(
            policy=policy,
            reviewer_authority=AuthorityIdentity("missing-validator-reviewer"),
            governing_human_authority=AuthorityIdentity("missing-validator-human"),
            compatibility_validator=None,
        )
    assert missing.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID

    object.__setattr__(
        authority,
        "compatibility_validator",
        lambda _plan, _intent, refs: refs,
    )
    with pytest.raises(AuthorityViolation) as rewired:
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=decision.compatibility_evidence_refs,
        )
    assert rewired.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID


def test_policy_disallowed_plan_cannot_receive_accept() -> None:
    intent, plan, _policy, _authority, decision, _accepted = plan_world()
    denying_policy = PlanAuthorityPolicy(
        schema_version=PLAN_POLICY_SCHEMA_V1,
        policy_version="acceptance-denying-policy-v1",
        allowed_operation_kinds=(OperationKind.INSPECT_READ,),
        allowed_capabilities=("repository.read",),
        human_review_capabilities=(),
    )
    denying_authority = configure_plan_authority(
        policy=denying_policy,
        reviewer_authority=AuthorityIdentity("denying-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("denying-governing-human"),
        compatibility_validator=validate_plan_compatibility,
    )

    with pytest.raises(AuthorityViolation) as raised:
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=denying_authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=decision.compatibility_evidence_refs,
        )
    assert raised.value.failure_code is AuthorityFailureCode.POLICY_REJECTED


def test_changed_operation_requires_new_proposal_and_decision() -> None:
    _intent, plan, _policy, _authority, _decision, accepted = plan_world()
    changed_operation = replace(
        plan.operations[0],
        subject_paths=("synapse/experiments/gold/stage10/planning.py",),
    )
    changed = replace(plan, operations=(changed_operation,))

    with pytest.raises(ValueError):
        require_no_plan_drift(accepted, changed)
