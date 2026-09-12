"""Memory supplies exact local edits absent from the public model proposal."""
from copy import deepcopy
import hashlib
import subprocess

import pytest

from acceptance.stage4.stage10.test_local_edit_contract import task, information, proposal, feedback, encoded
from synapse.worker.local_edits import LOCAL_EDIT_PROFILE_V3, propose_local_edits, validate_local_edit_result


def retained(patch):
    return {'role': 'REFERENCE', 'media_type': 'application/json', 'content_base64url': encoded(patch.encode())}


def good_patch(source=None):
    private = information() if source is None else information(source=source)
    return propose_local_edits(task=task(), information=private, proposal=proposal(('a - b', 'a + b')))['diff_text']


def interpret(private, public=None):
    return propose_local_edits(task=task(), information=private,
        proposal=proposal(('a - b', '0')) if public is None else public, profile=LOCAL_EDIT_PROFILE_V3)


def test_retained_patch_supplies_the_solution_that_the_model_did_not_propose():
    patch = good_patch()
    private = information(extra=[retained(patch), feedback(patch, True)])
    result = interpret(private)
    assert result['diff_text'] == patch
    assert result['proposal'] == proposal(('a - b', '0'))
    assert result['candidate_origins'][result['selected_index']] == {
        'kind': 'LOCAL_MEMORY', 'patch_sha256': hashlib.sha256(patch.encode()).hexdigest()}
    assert result['execution'] == 'NO_REPOSITORY_EFFECTS' and result['status'] == 'UNVERIFIED_PATCH_PROPOSAL'
    assert validate_local_edit_result(result, task_sha256=result['task_sha256'], information_sha256=private.sha256) == result
    assert interpret(information(extra=[feedback(patch, True)]))['diff_text'] != patch
    assert interpret(information(extra=[retained(patch)]))['diff_text'] != patch


def test_retained_patch_can_supply_a_candidate_for_an_empty_public_proposal():
    patch = good_patch()
    result = interpret(information(extra=[retained(patch), feedback(patch, True)]), proposal())
    assert result['diff_text'] == patch and result['selected_index'] == 0


def test_independent_repeated_evidence_does_not_repeat_the_same_patch_candidate():
    patch = good_patch()
    once = interpret(information(extra=[retained(patch), feedback(patch, True)]), proposal())
    repeated = interpret(information(extra=[retained(patch), feedback(patch, True),
                                           retained(patch), feedback(patch, True)]), proposal())
    assert repeated['candidate_origins'] == once['candidate_origins']
    assert repeated['effective_proposal'] == once['effective_proposal']
    assert repeated['diff_text'] == once['diff_text'] and len(repeated['candidates']) == 1


@pytest.mark.parametrize('outcomes', [(False,), (None,), (True, False), (1,), ('true',)])
def test_negative_unknown_conflicting_and_untyped_feedback_cannot_supply_a_positive_method(outcomes):
    patch = good_patch()
    result = interpret(information(extra=[retained(patch), *(feedback(patch, outcome) for outcome in outcomes)]))
    assert all(item['kind'] == 'PUBLIC_PROPOSAL' for item in result['candidate_origins'])
    assert result['diff_text'] != patch


@pytest.mark.parametrize('source', [None, 'def add(a, b):\n    return a * b\n'])
def test_retained_patch_needs_the_exact_available_source(source):
    patch = good_patch()
    result = interpret(information(source=source, extra=[retained(patch), feedback(patch, True)]))
    assert result['diff_text'] is None
    assert result['search']['excluded_memory'][0]['reason'] == 'PATCH_NOT_APPLICABLE'


@pytest.mark.parametrize('change', [
    lambda patch: patch.replace('@@ -1,2 +1,2 @@', '@@ -1,3 +1,2 @@'),
    lambda patch: patch.replace('@@ -1,2 +1,2 @@', '@@ -2,2 +1,2 @@'),
    lambda patch: patch.replace('src/calc.py', '../escape.py'),
    lambda patch: patch + 'unexpected trailer\n',
    lambda patch: patch.replace('-    return a - b', '-    return another_source'),
])
def test_a_positive_label_does_not_override_hunk_counts_scope_or_source_binding(change):
    patch = change(good_patch())
    result = interpret(information(extra=[retained(patch), feedback(patch, True)]))
    assert all(item['kind'] != 'LOCAL_MEMORY' for item in result['candidate_origins'])
    assert result['search']['excluded_memory'] == [
        {'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'reason': 'PATCH_NOT_APPLICABLE'}]


def test_exact_unicode_and_multiple_hunks_keep_the_original_patch_bytes():
    source = 'label = "e\u0301"\n' + ''.join(f'line_{index} = {index}\n' for index in range(18)) + 'def add(a, b):\n    return a - b\n'
    patch = propose_local_edits(task=task(), information=information(source=source),
        proposal=proposal((source, source.replace('"e\u0301"', '"e\u0301x"').replace('a - b', 'a + b'))))['diff_text']
    assert patch.count('@@ -') == 2
    result = interpret(information(source=source, extra=[retained(patch), feedback(patch, True)]), proposal())
    assert result['diff_text'] == patch


@pytest.mark.parametrize('mutate', [
    lambda result: result['candidate_origins'][0].update(patch_sha256='0' * 64),
    lambda result: result['candidate_origins'][0].update(kind='PUBLIC_PROPOSAL'),
    lambda result: result['search'].update(candidate_limit=True),
    lambda result: result.pop('proposal'),
])
def test_transport_rejects_substituted_origin_or_search_contract(mutate):
    patch = good_patch()
    private = information(extra=[retained(patch), feedback(patch, True)])
    result = interpret(private)
    changed = deepcopy(result)
    mutate(changed)
    with pytest.raises(ValueError):
        validate_local_edit_result(changed, task_sha256=result['task_sha256'], information_sha256=private.sha256)


@pytest.mark.parametrize('before,after', [
    ('a\nb\n', 'first\na\nb\n'),
    ('a\nb\n', 'a\nb\nlast\n'),
    ('first\na\nb\n', 'a\nb\n'),
    ('a\nb\nlast\n', 'a\nb\n'),
    ('a\nb\n', 'a\n'),
    ('a\nb\n', 'b\n'),
    ('a\nb\n', 'z\n'),
    ('a\n\nb\n', 'a\n\n\nb\n'),
])
def test_recalled_insertions_and_deletions_agree_with_real_git_application(tmp_path, before, after):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', 'config', 'core.autocrlf', 'false'], cwd=tmp_path, check=True)
    path = tmp_path / 'src/calc.py'
    path.parent.mkdir()
    path.write_bytes(before.encode())
    private = information(source=before)
    patch = propose_local_edits(task=task(), information=private,
        proposal=proposal((before, after)))['diff_text']
    result = interpret(information(source=before, extra=[retained(patch), feedback(patch, True)]), proposal())
    assert result['diff_text'] == patch
    checked = subprocess.run(['git', 'apply', '--check', '-'], cwd=tmp_path,
        input=result['diff_text'].encode(), capture_output=True)
    assert checked.returncode == 0, checked.stderr
    subprocess.run(['git', 'apply', '-'], cwd=tmp_path, input=result['diff_text'].encode(), check=True)
    assert path.read_bytes() == after.encode()
