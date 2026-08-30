from __future__ import annotations

from dataclasses import replace

import pytest

from synapse.experiments.gold.contracts import AuthorityIdentity
from synapse.experiments.gold.stage10.plan_authority import (
    AuthorityViolation,
    PlanDecisionKind,
    configure_plan_authority,
    decide_operation_plan,
    require_no_plan_drift,
)
from synapse.experiments.gold.stage10.plan_transport import (
    decode_accepted_plan,
    encode_accepted_plan,
)

from acceptance.stage4.stage10._builders import plan_world


def test_plan_decision_and_accepted_plan_have_distinct_identities() -> None:
    intent, plan, _policy, authority, decision, accepted = plan_world()

    assert plan.proposal_id.record_id.digest_sha256 != decision.decision_id.record_id.digest_sha256
    assert accepted.accepted_plan_id.record_id.digest_sha256 != plan.proposal_id.record_id.digest_sha256
    encoded = encode_accepted_plan(accepted)
    assert decode_accepted_plan(encoded, intent=intent, authority=authority) == accepted


def test_actual_producer_cannot_be_configured_as_accepting_authority() -> None:
    intent, plan, policy, _authority, _decision, _accepted = plan_world()
    self_authority = configure_plan_authority(
        policy=policy,
        reviewer_authority=AuthorityIdentity(plan.proposer.value),
        governing_human_authority=AuthorityIdentity("separate-human"),
    )

    with pytest.raises(ValueError):
        decide_operation_plan(
            plan=plan,
            intent=intent,
            authority=self_authority,
            executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
        )


def test_changed_operation_requires_new_proposal_and_decision() -> None:
    _intent, plan, _policy, _authority, _decision, accepted = plan_world()
    changed_operation = replace(
        plan.operations[0],
        subject_paths=("synapse/experiments/gold/stage10/planning.py",),
    )
    changed = replace(plan, operations=(changed_operation,))

    with pytest.raises(ValueError):
        require_no_plan_drift(accepted, changed)
