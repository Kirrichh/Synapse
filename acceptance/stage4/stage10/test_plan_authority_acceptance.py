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
    validate_plan_authority_decision,
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
        task_contract=_authority.task_contract,
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
        task_contract=authority.task_contract,
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
            task_contract=authority.task_contract,
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
        task_contract=_authority.task_contract,
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


def test_mutant_accept_without_compatibility_evidence_under_a_lax_validator_is_killed() -> None:
    """The authority owns "ACCEPT requires compatibility", not its validator.

    The invariant sits on two layers: the authority checks that an ACCEPT names
    evidence, and the injected validator checks that the named evidence is the
    right evidence. Each layer alone makes the other look redundant, and the
    suite above proves each of them only through the other — the missing-refs
    case runs under a strict validator that would refuse an empty set anyway,
    and the lax-validator case supplies refs the authority would demand anyway.

    So both could be weakened together and nothing would notice: with the
    authority's own check gone, a validator that simply returns what it was
    given accepts an empty set, and an ACCEPT is granted naming no compatibility
    evidence at all. This pins the authority-side half on its own — a validator
    that refuses nothing must still not be able to produce that decision.
    """

    intent, plan, _policy, authority, _decision, _accepted = plan_world()
    lax_authority = configure_plan_authority(
        policy=authority.policy,
        task_contract=authority.task_contract,
        reviewer_authority=AuthorityIdentity("lax-plan-reviewer"),
        governing_human_authority=AuthorityIdentity("lax-governing-human"),
        # Refuses nothing: it echoes whatever it is handed, empty included.
        compatibility_validator=lambda _plan, _intent, refs: refs,
    )

    with pytest.raises(AuthorityViolation) as refused:
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=lax_authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=(),
        )
    assert refused.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID


def test_an_accept_decision_record_naming_no_compatibility_evidence_is_invalid() -> None:
    """The same invariant on the record, so a decision cannot be edited into it.

    ``decide_operation_plan`` is not the only way a decision reaches a consumer:
    one can be restored from bytes or rebuilt field by field. Validation of the
    record refuses an ACCEPT with an empty evidence set for the same reason the
    authority refuses to mint one.
    """

    _intent, _plan, _policy, _authority, decision, _accepted = plan_world()
    stripped = replace(decision, compatibility_evidence_refs=())

    with pytest.raises(AuthorityViolation) as refused:
        validate_plan_authority_decision(stripped)
    assert refused.value.failure_code is AuthorityFailureCode.COMPATIBILITY_INVALID
