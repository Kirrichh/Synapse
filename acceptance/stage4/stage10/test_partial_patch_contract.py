"""Pure partial-composition contracts; these inputs confer no C1 authority."""
from copy import deepcopy
import hashlib
import subprocess
import sys

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import task, information, proposal, feedback, encoded
from acceptance.stage4.stage10.test_retained_patch_contract import good_patch, retained
from synapse.canonical_values import canonical_json_bytes
from synapse.worker.input_contract import LocalInformationInput, WorkerTaskInput
from synapse.worker.local_edits import (
    LOCAL_EDIT_PROFILE_V3, LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROPOSAL_V1,
    propose_local_edits, validate_local_edit_result,
)


def inputs(*, outcomes=(False,), patch_material=True):
    data = task().to_dict()
    data['effects'].append({'kind': 'PATH_MODIFIED', 'disposition': 'EXPECTED', 'subject_path': 'src/scale.py'})
    public = WorkerTaskInput(canonical_json_bytes(data))
    patch = good_patch()
    source = {'role': 'REFERENCE', 'media_type': 'application/json', 'content_base64url': encoded(canonical_json_bytes({
        'repository_revision': 'a' * 40, 'applicability': 'UNASSESSED',
        'sources': [{'path': 'src/scale.py', 'content_base64url': encoded(b'def double(value):\n    return value\n')}]}))}
    local = information(extra=[source, *([retained(patch)] if patch_material else []),
        *(feedback(patch, outcome) for outcome in outcomes)])
    extension = {'schema_version': LOCAL_EDIT_PROPOSAL_V1, 'alternatives': [
        {'edits': [{'path': 'src/scale.py', 'old': 'return value', 'new': 'return value * 2'}]}]}
    return public, local, extension, patch


def interpret(public, local, extension, *, profile=LOCAL_EDIT_PROFILE_V4):
    result = propose_local_edits(task=public, information=local, proposal=extension, profile=profile)
    assert validate_local_edit_result(result,
        task_sha256=result['task_sha256'], information_sha256=local.sha256) == result
    return result


def test_checked_partial_composes_with_another_file_and_real_git_applies_new_patch(tmp_path):
    public, local, extension, old_patch = inputs()
    result = interpret(public, local, extension)
    assert result['candidate_origins'][0] == {'kind': 'LOCAL_PARTIAL_MEMORY',
        'patch_sha256': hashlib.sha256(old_patch.encode()).hexdigest(), 'index': 0}
    assert result['candidates'][0]['prior_outcome'] is None
    assert result['diff_text'] != old_patch and result['proposal'] == extension
    assert result['touched_files'] == ['src/calc.py', 'src/scale.py']
    assert result['status'] == 'UNVERIFIED_PATCH_PROPOSAL' and result['execution'] == 'NO_REPOSITORY_EFFECTS'
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/calc.py').write_bytes(b'def add(a, b):\n    return a - b\n')
    (tmp_path / 'src/scale.py').write_bytes(b'def double(value):\n    return value\n')
    subprocess.run(['git', 'apply', '--check', '-'], cwd=tmp_path, input=result['diff_text'].encode(), check=True)
    subprocess.run(['git', 'apply', '-'], cwd=tmp_path, input=result['diff_text'].encode(), check=True)
    subprocess.run([sys.executable, '-B', '-c',
        'from src.calc import add; from src.scale import double; assert add(-2,3)==1 and double(-2)==-4'],
        cwd=tmp_path, check=True)


@pytest.mark.parametrize('outcomes,material', [((), True), ((None,), True), ((1,), True),
    ((True, False), True), ((False,), False)])
def test_unknown_conflicting_or_missing_material_cannot_supply_a_checked_partial(outcomes, material):
    public, local, extension, _ = inputs(outcomes=outcomes, patch_material=material)
    result = interpret(public, local, extension)
    assert result['candidate_origins'] == [{'kind': 'PUBLIC_PROPOSAL', 'index': 0}]
    assert result['touched_files'] == ['src/scale.py']


def test_older_profile_does_not_silently_start_composing_negative_memory():
    public, local, extension, _ = inputs()
    result = interpret(public, local, extension, profile=LOCAL_EDIT_PROFILE_V3)
    assert result['candidate_origins'] == [{'kind': 'PUBLIC_PROPOSAL', 'index': 0}]


def test_exact_failed_patch_is_still_excluded_and_empty_extension_invents_nothing():
    public, local, _, _ = inputs()
    result = interpret(public, local, proposal(('a - b', 'a + b')))
    assert result['diff_text'] is None
    assert result['candidates'][0]['reason'] == 'EXACT_VERIFIED_PATCH_REJECTED'
    assert result['search']['omitted_compositions'][0]['reason'] == 'OVERLAPPING_PATHS'
    assert interpret(public, local, proposal())['candidates'] == []


def test_composed_patch_gets_its_own_negative_feedback_without_inheriting_success():
    public, local, extension, _ = inputs()
    first = interpret(public, local, extension)
    more = local.to_dict()
    more['items'].append(feedback(first['diff_text'], False))
    second = interpret(public, LocalInformationInput(canonical_json_bytes(more)), extension)
    assert second['candidates'][0]['reason'] == 'EXACT_VERIFIED_PATCH_REJECTED'
    assert second['selected_index'] == 1 and second['touched_files'] == ['src/scale.py']


def test_partial_cannot_expand_task_scope():
    public, local, extension, _ = inputs()
    changed = public.to_dict()
    changed['allowed_scope'] = ['src/scale.py']
    result = interpret(WorkerTaskInput(canonical_json_bytes(changed)), local, extension)
    assert all(item['kind'] == 'PUBLIC_PROPOSAL' for item in result['candidate_origins'])
    assert result['search']['excluded_memory'][0]['reason'] == 'PATCH_NOT_APPLICABLE'


@pytest.mark.parametrize('mutate', [
    lambda result: result['candidate_origins'][0].update(index=True),
    lambda result: result['candidate_origins'][0].update(patch_sha256=result['candidates'][0]['patch_sha256']),
    lambda result: result['candidate_origins'][0].update(kind='LOCAL_MEMORY'),
    lambda result: result['search'].update(omitted_compositions=[{'patch_sha256': 'a' * 64, 'index': 0, 'reason': 'SUCCESS'}]),
])
def test_transport_cannot_turn_a_composition_into_proven_success_or_change_its_origin(mutate):
    public, local, extension, _ = inputs()
    result = interpret(public, local, extension)
    changed = deepcopy(result)
    mutate(changed)
    with pytest.raises(ValueError):
        validate_local_edit_result(changed, task_sha256=result['task_sha256'], information_sha256=local.sha256)
