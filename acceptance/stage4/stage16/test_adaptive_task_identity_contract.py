"""Historical evidence is opened only for the same logical task."""
from dataclasses import replace
import hashlib

import pytest

from acceptance.stage4.stage10._builders import plan_world
from synapse.experiments.gold import project_agents
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.source_verification import canonical
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope


REVISION = 'b' * 40
PATCH = 'c' * 64


def case(tmp_path, historical, *, digest=PATCH):
    claim = {'task_contract_ref': historical.reference.to_dict(),
             'repository_revision': historical.repository_revision_sha256, 'patch_sha256': digest}
    origin = {'transaction_id': 'missing'}
    subject = dict(claim)
    paths = sorted({effect.subject_path for effect in historical.effects if effect.subject_path is not None})
    court = {'subjects': {'s': {'state': 'ADMITTED', 'subject': subject,
             'support': [hashlib.sha256(canonical(origin)).hexdigest()]}},
             'judged': [{'learning': {'assertions': [{'claim': claim, 'status': 'CONFIRMED', 'origin': origin}]},
                         'observation': {'requirement': {'paths': paths}}}]}
    return {'source_snapshot': {'project_state_root': str(tmp_path)}}, court


@pytest.mark.parametrize('variation', ['identity', 'statement', 'acceptance', 'scope'])
def test_different_logical_tasks_with_same_paths_do_not_open_history(tmp_path, monkeypatch, variation):
    task = plan_world()[3].task_contract
    historical = replace(task, repository_revision_sha256=REVISION)
    if variation == 'identity':
        historical = replace(historical, task_id='other-task')
    elif variation == 'statement':
        historical = replace(historical, task_statement='A different requirement')
    elif variation == 'acceptance':
        historical = replace(historical, acceptance=(replace(historical.acceptance[0], criterion_id='other'),))
    else:
        historical = replace(historical, allowed_scope=create_repository_scope(('synapse',)))
    request, court = case(tmp_path, historical)
    monkeypatch.setattr(project_agents, 'reopen_completed_run', lambda *a, **kw: pytest.fail('unrelated run reopened'))
    assert project_agents._adaptive_patches(request, None, court, task, 'ALL') == []


def test_same_task_other_revision_still_requires_physical_evidence(tmp_path):
    task = plan_world()[3].task_contract
    request, court = case(tmp_path, replace(task, repository_revision_sha256=REVISION))
    with pytest.raises(PersistenceViolation):
        project_agents._adaptive_patches(request, None, court, task, 'ALL')


def test_same_task_other_revision_retains_the_verified_patch(tmp_path, monkeypatch):
    from synapse.experiments.gold import persistence
    from synapse.experiments.gold.stage10.context_codec import encode_canonical
    from synapse.experiments.gold.stage13.run_publication import REQUEST_SCHEMA_V4

    task = plan_world()[3].task_contract
    historical = replace(task, repository_revision_sha256=REVISION)
    raw = b'diff content\n'
    digest = hashlib.sha256(raw).hexdigest()
    request, court = case(tmp_path, historical, digest=digest)
    request['project_identity'] = {}
    publication = {'schema_version': REQUEST_SCHEMA_V4, 'domain': {
        'base_revision': REVISION, 'task_contract_ref': historical.reference.to_dict(), 'patch_sha256': digest},
        'unit': {'core': {'artifact_refs': [{'schema_id': 'synapse.stage4.gold.c1-patch-bytes/v1',
                                           'sha256': digest, 'byte_length': len(raw)}]}}}
    monkeypatch.setattr(persistence, 'read_committed_snapshot_transaction',
                        lambda *a, **kw: ({}, {'request.json': encode_canonical(publication), digest: raw}))
    monkeypatch.setattr(project_agents, 'reopen_completed_run', lambda *a: (
        {'declaration': {'task_contract': historical.to_dict()}}, None, None))
    court['judged'][0]['outcome'] = {}
    store = type('Store', (), {'read': lambda self, receipt: {'kind': 'OUTCOME_RECORDED', 'payload': {}}})()
    assert project_agents._adaptive_patches(request, store, court, task, 'ALL') == [
        {'repository_revision': REVISION, 'patch_sha256': digest, 'diff_text': raw.decode()}]
