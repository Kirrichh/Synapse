"""Requirements are immutable while independently resolved targets are checked."""
from dataclasses import replace
import subprocess

import pytest

from acceptance.stage4.stage10._builders import plan_world
from synapse.experiments.gold.bindings import binding_to_ref
from synapse.experiments.gold.stage10.context_codec import decode_canonical, encode_canonical
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract, TASK_CONTRACT_SCHEMA_V3
from synapse.experiments.gold.stage10.plan_authority import configure_plan_authority
from synapse.experiments.gold.task_targets import resolve_task_targets, read_task_targets


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    for argv in (('init', '-q'), ('config', 'user.name', 'Acceptance'),
                 ('config', 'user.email', 'acceptance@example.invalid')):
        subprocess.run(['git', *argv], cwd=repo, check=True, capture_output=True)
    (repo / 'calc.py').write_text('raise RuntimeError("must never execute")\n'
        'def add(a, b):\n    return a - b\n'
        'def unrelated():\n    return 0\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '-qm', 'source'], cwd=repo, check=True, capture_output=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    _, _, _, authority, _, _ = plan_world()
    task = replace(authority.task_contract, schema_version=TASK_CONTRACT_SCHEMA_V3,
        behavior_refs=(), target_bindings=(), task_statement='Fix add(a, b).',
        repository_revision_sha256=revision, allowed_scope=create_repository_scope(('calc.py',)),
        effects=(replace(authority.task_contract.effects[0], subject_path='calc.py'),))
    return repo, task, authority


def test_resolution_is_explained_committed_evidence_and_preserves_task_identity(project):
    repo, task, _ = project
    original = task.canonical_bytes()
    raw = resolve_task_targets(task=task, repository_root=repo)
    targets = read_task_targets(raw, task=task, repository_root=repo)
    assert {item.qualname for item in targets} == {'calc', 'add'}
    reasons = {item['binding']['qualname']: item['selection_reasons'] for item in decode_canonical(raw)['elements']}
    assert reasons == {'calc': ['EXPECTED_EFFECT_PATH'], 'add': ['TASK_MENTIONS_SYMBOL'], 'unrelated': []}
    (repo / 'calc.py').write_text('def invented():\n    return 999\n')
    assert resolve_task_targets(task=task, repository_root=repo) == raw
    assert task.canonical_bytes() == original
    assert GoverningTaskContract.from_dict(task.to_dict()) == task
    assert 'target_bindings' not in task.to_dict() and 'target_bindings' not in task.intent_fields()


@pytest.mark.parametrize('field', ['target_bindings', 'behavior_refs'])
def test_task_v3_refuses_caller_selected_bindings_and_knowledge(project, field):
    _, task, _ = project
    value = task.to_dict()
    value[field] = []
    with pytest.raises(ValueError):
        GoverningTaskContract.from_dict(value)


@pytest.mark.parametrize('damage', ['remove-element', 'choose-unrelated', 'different-task', 'extra-field'])
def test_authority_physically_rejects_rehashed_resolution_substitutions(project, damage):
    repo, task, authority = project
    value = decode_canonical(resolve_task_targets(task=task, repository_root=repo))
    if damage == 'remove-element':
        value['elements'].pop()
    elif damage == 'choose-unrelated':
        value['elements'][-1]['selection_reasons'] = ['TASK_MENTIONS_SYMBOL']
    elif damage == 'different-task':
        value['task_contract_ref'] = replace(task, task_statement='Fix unrelated').reference.to_dict()
    else:
        value['trusted'] = True
    with pytest.raises(ValueError, match='committed project evidence'):
        configure_plan_authority(task_contract=task, policy=authority.policy,
            reviewer_authority=authority.reviewer_authority,
            governing_human_authority=authority.governing_human_authority,
            compatibility_validator=authority.compatibility_validator,
            target_resolution=encode_canonical(value), repository_root=repo)


def test_task_v3_requires_resolved_targets_even_for_a_valid_intent(project):
    from synapse.experiments.gold.stage10.intent import propose_intent
    repo, task, _ = project
    original, _, _, _, _, _ = plan_world()
    targets = tuple(binding_to_ref(item) for item in read_task_targets(
        resolve_task_targets(task=task, repository_root=repo), task=task, repository_root=repo))
    intent = propose_intent(**task.intent_fields(), target_bindings=targets,
        behavior_refs=original.behavior_refs, task_contract_ref=task.reference,
        proposer=original.proposer, source_actors=original.source_actors,
        knowledge_snapshot_ref=original.knowledge_snapshot_ref)
    with pytest.raises(ValueError, match='resolved project targets'):
        task.validate_intent(intent)
    task.validate_intent(intent, resolved_target_bindings=targets)
    with pytest.raises(ValueError, match='resolved project targets'):
        task.validate_intent(intent, resolved_target_bindings=targets[:1])


def test_in_place_binding_mutation_cannot_rewire_independent_authority(project):
    from synapse.experiments.gold.stage10.plan_authority import AuthorityViolation, require_configured_plan_authority
    repo, task, authority = project
    checked = configure_plan_authority(task_contract=task, policy=authority.policy,
        reviewer_authority=authority.reviewer_authority,
        governing_human_authority=authority.governing_human_authority,
        compatibility_validator=authority.compatibility_validator,
        target_resolution=resolve_task_targets(task=task, repository_root=repo), repository_root=repo)
    object.__setattr__(checked.resolved_target_bindings[0], 'sha256', 'b' * 64)
    with pytest.raises(AuthorityViolation, match='resolved task targets were rewired'):
        require_configured_plan_authority(checked)


def test_duplicate_symbols_refuse_before_any_target_is_published(project):
    repo, task, _ = project
    with (repo / 'calc.py').open('a') as stream:
        stream.write('def add(a, b):\n    return a + b\n')
    subprocess.run(['git', 'commit', '-qam', 'ambiguous'], cwd=repo, check=True, capture_output=True)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    with pytest.raises(ValueError, match='duplicate declarations'):
        resolve_task_targets(task=replace(task, repository_revision_sha256=revision), repository_root=repo)


def test_automatic_target_approval_keeps_targets_fixed_across_knowledge_selection(project, tmp_path):
    from synapse.experiments.gold.runner.attempt_plan import GoldAttemptPlanProfile, _proposal_inputs, _approval_field
    from synapse.experiments.gold.stage10.approval import RunApprovalPolicy, APPROVAL_REQUEST_SCHEMA_V3
    from synapse.experiments.gold.stage10.intent import INTENT_SCHEMA_V3
    from synapse.experiments.gold.stage10.planning import OPERATION_PLAN_SCHEMA_V1, topological_operation_order
    repo, task, authority = project
    original, plan, _, _, _, _ = plan_world()
    raw = resolve_task_targets(task=task, repository_root=repo)
    profile = GoldAttemptPlanProfile(task_contract=task, repository_root=repo, target_resolution=raw,
        target_records=read_task_targets(raw, task=task, repository_root=repo),
        intent_proposer=original.proposer, intent_source_actor=original.source_actors[0],
        plan_proposer=plan.proposer, plan_source_actor=plan.source_actors[0],
        executor=original.proposer, reviewer_authority=authority.reviewer_authority,
        governing_human_authority=authority.governing_human_authority, policy_version='policy-v1')
    approval = RunApprovalPolicy(tmp_path / 'approvals', 'a' * 64, authority.governing_human_authority)
    requests = []
    for selected in ((), original.behavior_refs):
        intent_fields, plan_fields, policy = _proposal_inputs(profile, task.repository_revision_sha256,
            selected_behavior_refs=selected)
        requests.append(approval.request_for_contract(
            intent_contract={'schema_version': INTENT_SCHEMA_V3,
                **{key: _approval_field(value) for key, value in intent_fields.items()}},
            plan_contract={'schema_version': OPERATION_PLAN_SCHEMA_V1,
                'repository_revision_sha256': task.repository_revision_sha256,
                'execution_order': list(topological_operation_order(plan_fields['operations'])),
                **{key: _approval_field(value) for key, value in plan_fields.items()}},
            policy_sha256=policy.sha256, executor=profile.executor))
    assert requests[0] == requests[1]
    assert requests[0]['schema_version'] == APPROVAL_REQUEST_SCHEMA_V3
    assert requests[0]['intent_contract']['target_bindings']
