"""Command changes cannot hide behind an unchanged edit operation or approval."""
from dataclasses import replace

import pytest

from acceptance.stage4.stage10._builders import plan_world
from acceptance.stage4.stage10.test_task_target_resolution import project
from synapse.experiments.gold.runner.attempt_plan import GoldAttemptPlanProfile, _proposal_inputs
from synapse.experiments.gold.runner.vocabulary import GoldRunViolation
from synapse.experiments.gold.stage10.intent import AcceptanceCriterion, AcceptanceKind, propose_intent
from synapse.experiments.gold.stage10.planning import (
    OperationKind, PlanViolation, plan_semantic_sha256, propose_operation_plan,
)
from synapse.experiments.gold.task_targets import read_task_targets, resolve_task_targets


@pytest.fixture
def command_plan(project):
    repo, task, authority = project
    original, plan, _, _, _, _ = plan_world()
    condition = task.acceptance[0].condition_ref
    task = replace(task, required_capabilities=('repository.edit', 'verification.run'),
        acceptance=task.acceptance + (
            AcceptanceCriterion('check-first', AcceptanceKind.VERIFICATION_COMMAND, condition, ('python', 'check.py')),
            AcceptanceCriterion('check-second', AcceptanceKind.VERIFICATION_COMMAND, condition, ('python', 'suite.py'))))
    raw = resolve_task_targets(task=task, repository_root=repo)
    profile = GoldAttemptPlanProfile(task_contract=task, repository_root=repo, target_resolution=raw,
        target_records=read_task_targets(raw, task=task, repository_root=repo),
        intent_proposer=original.proposer, intent_source_actor=original.source_actors[0],
        plan_proposer=plan.proposer, plan_source_actor=plan.source_actors[0],
        executor=original.proposer, reviewer_authority=authority.reviewer_authority,
        governing_human_authority=authority.governing_human_authority, policy_version='policy-v1')
    fields, plan_fields, policy = _proposal_inputs(profile, task.repository_revision_sha256,
        selected_behavior_refs=original.behavior_refs)
    intent = propose_intent(**fields, knowledge_snapshot_ref=original.knowledge_snapshot_ref)
    proposed = propose_operation_plan(intent=intent, **plan_fields)
    return profile, intent, proposed, plan_fields, policy


def test_commands_have_ordered_dependencies_and_individual_acceptance(command_plan):
    profile, intent, plan, _, policy = command_plan
    assert [item.kind for item in plan.operations] == [OperationKind.EDIT_CONTROLLED_CHANGE,
        OperationKind.RUN_VERIFICATION_COMMAND, OperationKind.RUN_VERIFICATION_COMMAND]
    assert [item.acceptance_criterion_ids for item in plan.operations] == [
        ('acceptance-main',), ('check-first',), ('check-second',)]
    assert [item.depends_on for item in plan.operations] == [(), ('operation-main',), ('operation-check-1',)]
    assert policy.allowed_capabilities == intent.required_capabilities
    assert plan.operations[0].input_refs
    assert [item.argv for item in plan.operations[1:]] == [item.argv for item in profile.task_contract.acceptance[1:]]


@pytest.mark.parametrize('damage', ['different-command', 'omit-check', 'duplicate-check'])
def test_accepted_task_cannot_silently_change_or_skip_checks(command_plan, damage):
    _, intent, plan, fields, _ = command_plan
    operations = list(plan.operations)
    if damage == 'different-command':
        operations[1] = replace(operations[1], argv=('python', 'unapproved.py'))
    elif damage == 'omit-check':
        operations.pop()
    else:
        operations.append(replace(operations[-1], operation_id='repeat-check'))
    with pytest.raises(PlanViolation):
        propose_operation_plan(intent=intent, **dict(fields, operations=tuple(operations)))


def test_removing_a_dependency_changes_actual_plan_identity(command_plan):
    _, intent, plan, fields, _ = command_plan
    altered = propose_operation_plan(intent=intent, **dict(fields,
        operations=(plan.operations[0], replace(plan.operations[1], depends_on=()), plan.operations[2])))
    assert plan_semantic_sha256(plan, intent=intent, policy_version='policy-v1') != (
        plan_semantic_sha256(altered, intent=intent, policy_version='policy-v1'))


def test_task_must_explicitly_grant_verification_capability(command_plan):
    profile, _, _, _, _ = command_plan
    task = replace(profile.task_contract, required_capabilities=('repository.edit',))
    raw = resolve_task_targets(task=task, repository_root=profile.repository_root)
    profile = replace(profile, task_contract=task, target_resolution=raw,
        target_records=read_task_targets(raw, task=task, repository_root=profile.repository_root))
    with pytest.raises(GoldRunViolation, match='explicit task v3 command contract'):
        _proposal_inputs(profile, task.repository_revision_sha256)
