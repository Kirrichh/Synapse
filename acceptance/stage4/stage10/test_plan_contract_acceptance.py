from __future__ import annotations

from dataclasses import replace

import pytest

from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.stage10.intent import (
    AcceptanceCriterion,
    AcceptanceKind,
    EffectConstraint,
    EffectDisposition,
    EffectKind,
    IntentFailureCode,
    IntentViolation,
    propose_intent,
)
from synapse.experiments.gold.stage10.planning import (
    FailureAction,
    OperationKind,
    OperationRecord,
    PlanFailureCode,
    PlanViolation,
    VerificationKind,
    VerificationObligation,
    propose_operation_plan,
)
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope

from acceptance.stage4.stage10._builders import hash_ref, plan_world


def _repropose(intent, plan, operation, *, allowed_scope=None):
    return propose_operation_plan(
        intent=intent,
        proposer=plan.proposer,
        source_actors=plan.source_actors,
        allowed_scope=allowed_scope or plan.allowed_scope,
        capability_profile=plan.capability_profile,
        operations=(operation,),
    )


def test_path_effect_requires_an_exact_product_target() -> None:
    with pytest.raises(IntentViolation) as raised:
        EffectConstraint(
            constraint_id="effect-without-target",
            disposition=EffectDisposition.EXPECTED,
            kind=EffectKind.PATH_MODIFIED,
            subject_path=None,
            verification_ref=hash_ref(RefKind.CONTRACT_CONDITION, "missing-target"),
        )
    assert raised.value.failure_code is IntentFailureCode.UNVERIFIABLE_EFFECT


def test_plan_cannot_expand_scope_or_substitute_the_expected_target() -> None:
    intent, plan, *_ = plan_world()
    expanded = create_repository_scope(("synapse/experiments/gold",))

    with pytest.raises(PlanViolation) as scope:
        _repropose(intent, plan, plan.operations[0], allowed_scope=expanded)
    assert scope.value.failure_code is PlanFailureCode.SCOPE_EXPANSION

    substituted = replace(
        plan.operations[0],
        subject_paths=("synapse/experiments/gold/stage10/planning.py",),
    )
    with pytest.raises(PlanViolation) as target:
        _repropose(intent, plan, substituted)
    assert target.value.failure_code is PlanFailureCode.EFFECT_BINDING_INVALID


def test_plan_must_cover_every_expected_effect_and_acceptance() -> None:
    intent, plan, *_ = plan_world()

    with pytest.raises(PlanViolation) as effect:
        _repropose(
            intent,
            plan,
            replace(plan.operations[0], subject_paths=(), effect_constraint_ids=()),
        )
    assert effect.value.failure_code is PlanFailureCode.EFFECT_COVERAGE_MISSING

    with pytest.raises(PlanViolation) as acceptance:
        _repropose(
            intent,
            plan,
            replace(plan.operations[0], acceptance_criterion_ids=()),
        )
    assert acceptance.value.failure_code is PlanFailureCode.ACCEPTANCE_COVERAGE_MISSING


def test_plan_operation_cannot_touch_a_forbidden_product_path() -> None:
    base_intent, base_plan, *_ = plan_world()
    forbidden = EffectConstraint(
        constraint_id="effect-forbidden-planning",
        disposition=EffectDisposition.FORBIDDEN,
        kind=EffectKind.PATH_MODIFIED,
        subject_path="synapse/experiments/gold/stage10/planning.py",
        verification_ref=base_intent.acceptance[0].condition_ref,
    )
    intent = propose_intent(
        proposer=base_intent.proposer,
        task_contract_ref=base_intent.task_contract_ref,
        target_bindings=base_intent.target_bindings,
        behavior_refs=base_intent.behavior_refs,
        source_actors=base_intent.source_actors,
        task_statement=base_intent.task_statement,
        repository_revision_sha256=base_intent.repository_revision_sha256,
        knowledge_snapshot_ref=base_intent.knowledge_snapshot_ref,
        allowed_scope=base_intent.allowed_scope,
        required_capabilities=base_intent.required_capabilities,
        effects=(*base_intent.effects, forbidden),
        acceptance=base_intent.acceptance,
    )
    operation = replace(
        base_plan.operations[0],
        subject_paths=(
            "synapse/experiments/gold/stage10/context.py",
            "synapse/experiments/gold/stage10/planning.py",
        ),
    )

    with pytest.raises(PlanViolation) as raised:
        _repropose(intent, base_plan, operation)
    assert raised.value.failure_code is PlanFailureCode.FORBIDDEN_EFFECT


def test_verification_command_must_match_intent_argv_and_oracle() -> None:
    base_intent, base_plan, *_ = plan_world()
    condition = hash_ref(RefKind.CONTRACT_CONDITION, "command-condition")
    intent = propose_intent(
        proposer=base_intent.proposer,
        task_contract_ref=base_intent.task_contract_ref,
        target_bindings=base_intent.target_bindings,
        behavior_refs=base_intent.behavior_refs,
        source_actors=base_intent.source_actors,
        task_statement="Run the exact product verification command.",
        repository_revision_sha256=base_intent.repository_revision_sha256,
        knowledge_snapshot_ref=base_intent.knowledge_snapshot_ref,
        allowed_scope=base_intent.allowed_scope,
        required_capabilities=("verification.run",),
        effects=(
            EffectConstraint(
                constraint_id="effect-command",
                disposition=EffectDisposition.EXPECTED,
                kind=EffectKind.COMMAND_SUCCEEDS,
                subject_path=None,
                verification_ref=condition,
            ),
        ),
        acceptance=(
            AcceptanceCriterion(
                criterion_id="acceptance-command",
                kind=AcceptanceKind.VERIFICATION_COMMAND,
                condition_ref=condition,
                argv=("python", "-m", "pytest", "-q"),
            ),
        ),
    )
    operation = OperationRecord(
        operation_id="operation-command",
        kind=OperationKind.RUN_VERIFICATION_COMMAND,
        subject_paths=(),
        input_refs=(),
        argv=("python", "-m", "pytest", "-q"),
        depends_on=(),
        capability="verification.run",
        verification=VerificationObligation(
            kind=VerificationKind.COMMAND_RESULT,
            condition_ref=condition,
            failure_action=FailureAction.ABORT_PLAN,
        ),
        effect_constraint_ids=("effect-command",),
        acceptance_criterion_ids=("acceptance-command",),
    )
    command_plan = replace(base_plan, capability_profile=("verification.run",))

    valid = _repropose(intent, command_plan, operation)
    assert valid.operations[0].argv == intent.acceptance[0].argv

    with pytest.raises(PlanViolation) as argv:
        _repropose(intent, command_plan, replace(operation, argv=("python", "-m", "unittest")))
    assert argv.value.failure_code is PlanFailureCode.COMMAND_POLICY_EXPANSION

    other_oracle = hash_ref(RefKind.CONTRACT_CONDITION, "other-command-condition")
    with pytest.raises(PlanViolation) as oracle:
        _repropose(
            intent,
            command_plan,
            replace(
                operation,
                verification=replace(operation.verification, condition_ref=other_oracle),
            ),
        )
    assert oracle.value.failure_code is PlanFailureCode.EFFECT_BINDING_INVALID
