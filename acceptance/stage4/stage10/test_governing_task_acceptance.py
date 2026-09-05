"""A valid planner proposal cannot redefine its governing task."""

from dataclasses import replace

import pytest

from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage10.intent import propose_intent
from synapse.experiments.gold.stage10.planning import propose_operation_plan
from synapse.experiments.gold.stage10.plan_authority import (
    AuthorityFailureCode, AuthorityViolation, PlanDecisionKind, decide_operation_plan,
    require_configured_plan_authority,
)
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
from acceptance.stage4.stage10._builders import hash_ref, plan_world


@pytest.mark.parametrize("field,value", [
    ("task_statement", "A different self-assigned task"),
    ("repository_revision_sha256", "b" * 40),
    ("target_bindings", (hash_ref(RefKind.BINDING, "different-target"),)),
    ("behavior_refs", (hash_ref(RefKind.ARTIFACT, "different-behavior"),)),
])
def test_rehashed_proposal_cannot_choose_new_governing_constraints(field, value):
    original, plan, _, authority, decision, _ = plan_world()
    task = replace(authority.task_contract, **{field: value})
    intent = propose_intent(
        **task.intent_fields(), task_contract_ref=task.reference,
        proposer=original.proposer, source_actors=original.source_actors,
        knowledge_snapshot_ref=original.knowledge_snapshot_ref,
    )
    proposal = propose_operation_plan(
        intent=intent, proposer=plan.proposer, source_actors=plan.source_actors,
        allowed_scope=plan.allowed_scope, capability_profile=plan.capability_profile,
        operations=plan.operations,
    )
    with pytest.raises(AuthorityViolation) as rejected:
        decide_operation_plan(
            plan=proposal, intent=intent, authority=authority, executor=None,
            requested_decision=PlanDecisionKind.ACCEPT,
            compatibility_evidence_refs=decision.compatibility_evidence_refs,
        )
    assert rejected.value.failure_code is AuthorityFailureCode.DECISION_MISMATCH


def test_operator_contract_roundtrip_preserves_exact_verification_and_reference():
    _, _, _, authority, _, _ = plan_world()
    decoded = GoverningTaskContract.from_dict(authority.task_contract.to_dict())
    assert decoded == authority.task_contract
    assert decoded.reference == authority.task_contract.reference
    assert decoded.canonical_bytes() == authority.task_contract.canonical_bytes()


def test_in_place_task_mutation_cannot_rewrite_the_authority_snapshot():
    _, _, _, authority, _, _ = plan_world()
    object.__setattr__(authority.task_contract, "task_statement", "Forged replacement task")
    with pytest.raises(AuthorityViolation) as rejected:
        require_configured_plan_authority(authority)
    assert rejected.value.failure_code is AuthorityFailureCode.DECISION_MISMATCH
