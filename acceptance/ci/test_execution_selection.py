"""Selection must exclude unrelated execution and preserve real test failures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from changes import changed_paths, comparison
from check_results import check
from pytest_selected import selected_arguments


def test_fixed_invocation_keeps_options_and_only_selected_cases():
    assert selected_arguments(['-q', '--tb=short', 'tests/test_old.py', 'tests/test_new.py'], {'tests/test_new.py'}) == ['-q', '--tb=short', 'tests/test_new.py']
    assert selected_arguments(['-q', 'tests/test_old.py'], {'tests/test_new.py'}) == []


def test_runner_executes_only_selected_behavior_and_propagates_failure(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_selected.py').write_text('def test_refusal():\n    assert False\n')
    (tests / 'test_unrelated.py').write_text('raise RuntimeError("Unrelated collection must not execute")\n')
    runner = Path(__file__).resolve().parents[2] / 'scripts/ci/pytest_selected.py'
    result = subprocess.run([sys.executable, str(runner), '-q', '-p', 'no:cacheprovider', 'tests/test_*.py'], cwd=tmp_path,
                            env={**os.environ, 'SELECTED_TESTS': json.dumps(['tests/test_selected.py'])}, capture_output=True, text=True)
    assert result.returncode == 1 and '1 failed' in result.stdout
    assert 'RuntimeError' not in result.stdout


@pytest.mark.parametrize('result', ['failure', 'cancelled', 'skipped', None])
def test_selected_jobs_cannot_be_green_when_evidence_is_missing(result):
    assert not check({'selected': {'result': result}}, {'selected'})
    assert check({'selected': {'result': 'success'}, 'unselected': {'result': 'skipped'}}, {'selected'})


def test_mutation_only_ui_selection_is_required_by_the_final_gate():
    gate = Path(__file__).resolve().parents[2] / 'scripts/ci/check_results.py'
    plan = {'groups': {}, 'durable': False, 'version': False, 'additional': [], 'tooling': False,
            'ui': {'python_mutations': True}}
    results = {'select': {'result': 'success'}, 'ui': {'result': 'skipped'}}
    rejected = subprocess.run([sys.executable, str(gate)], capture_output=True, text=True,
                              env={**os.environ, 'TEST_PLAN': json.dumps(plan), 'JOB_RESULTS': json.dumps(results)})
    assert rejected.returncode == 1 and 'did not succeed' in rejected.stderr


def test_feature_push_uses_complete_branch_comparison():
    event = {'repository': {'default_branch': 'main'}, 'ref': 'refs/heads/work', 'before': 'previous'}
    assert comparison(event, 'push', 'current') == ('refs/remotes/origin/main', 'current', True)
    event['ref'] = 'refs/heads/main'
    assert comparison(event, 'push', 'current') == ('previous', 'current', False)


def test_git_comparison_keeps_both_rename_boundaries_and_prior_branch_changes(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=tmp_path, stderr=subprocess.DEVNULL).decode().strip()
    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'Synthetic acceptance')
    git('config', 'user.email', 'acceptance@example.invalid')
    (tmp_path / 'owner.py').write_text('value = 1\n')
    git('add', '.')
    git('commit', '-qm', 'Synthetic baseline')
    base = git('rev-parse', 'HEAD')
    git('switch', '-qc', 'work')
    (tmp_path / 'owner.py').rename(tmp_path / 'consumer.py')
    git('add', '-A')
    git('commit', '-qm', 'Move owner')
    (tmp_path / 'notes.md').write_text('Synthetic notes\n')
    git('add', '.')
    git('commit', '-qm', 'Notes after code')
    _, paths = changed_paths(tmp_path, base, 'HEAD', True)
    assert paths == {'owner.py', 'consumer.py', 'notes.md'}
    with pytest.raises(subprocess.CalledProcessError):
        changed_paths(tmp_path, 'missing-comparison', 'HEAD', True)
