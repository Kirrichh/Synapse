"""Execute the final gate against missing selection and unsuccessful evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

GATE = Path(__file__).resolve().parents[2] / 'scripts/ci/check_results.py'
PLAN = {'groups': {'related': {'enabled': True}, 'unrelated': {'enabled': False}},
        'durable': False, 'version': False, 'additional': [], 'tooling': False, 'ui': {}}


def run(plan, results):
    environment = {**os.environ, 'TEST_PLAN': plan, 'JOB_RESULTS': json.dumps(results)}
    return subprocess.run([sys.executable, str(GATE)], capture_output=True, text=True, env=environment)


@pytest.mark.parametrize('plan', ['', ' ', '{', 'null', '[]', '{}'])
def test_missing_or_invalid_plan_cannot_turn_missing_checks_into_success(plan):
    result = run(plan, {'select': {'result': 'success'}})
    assert result.returncode != 0
    assert 'TEST_PLAN' in result.stderr and 'Traceback' not in result.stderr


@pytest.mark.parametrize('selection', ['failure', 'cancelled', 'skipped'])
def test_unsuccessful_selector_is_reported_before_attempting_to_read_its_empty_output(selection):
    result = run('', {'select': {'result': selection}})
    assert result.returncode != 0
    assert 'Test selection did not succeed' in result.stderr and 'Traceback' not in result.stderr


@pytest.mark.parametrize('status', ['failure', 'cancelled', 'skipped', None])
def test_declared_check_must_finish_successfully(status):
    results = {'select': {'result': 'success'}, 'unrelated': {'result': 'skipped'}}
    if status is not None:
        results['related'] = {'result': status}
    assert run(json.dumps(PLAN), results).returncode != 0
    results['related'] = {'result': 'success'}
    assert run(json.dumps(PLAN), results).returncode == 0


def test_selected_native_result_is_available_to_and_required_by_final_gate():
    import yaml
    from select_tests import matrices

    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/stage4-gold.yml').read_text())
    groups, _ = matrices(workflow, {'tests/test_stage4_gold_native.py'}, set())
    assert groups['stage4-native']['enabled']
    needs = workflow['jobs']['gold-slow']['needs']
    plan = {**PLAN, 'groups': {'stage4-native': groups['stage4-native'],
                                'unrelated': {'enabled': False}}}
    results = {name: {'result': 'success'} for name in needs}
    results['unrelated'] = {'result': 'skipped'}
    assert run(json.dumps(plan), results).returncode == 0
    for status in ('failure', 'cancelled', 'skipped', None):
        changed = dict(results)
        if status is None:
            changed.pop('stage4-native', None)
        else:
            changed['stage4-native'] = {'result': status}
        assert run(json.dumps(plan), changed).returncode != 0


@pytest.fixture
def codec_workspace(tmp_path):
    import yaml

    files = {
        'synapse/experiments/gold/canonicalization.py': '',
        'tests/test_stage4_gold_native.py': 'from synapse.experiments.gold import canonicalization\n',
        'native/stage4_canonical/src/lib.rs': '',
        'native/stage4_canonical/Cargo.toml': '',
        'native/stage4_canonical/Cargo.lock': '',
        'native/stage4_canonical/pyproject.toml': '',
        'docs/unrelated-selection-test.md': '',
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/stage4-gold.yml').read_text())
    return tmp_path, set(files), workflow


def test_unrelated_change_does_not_require_native(codec_workspace):
    from select_tests import build_plan

    root, files, workflow = codec_workspace
    plan = build_plan(root, files, {'docs/unrelated-selection-test.md'}, workflow)
    assert not plan['groups']['stage4-native']['enabled']
    results = {name: {'result': 'success'} for name in workflow['jobs']['gold-slow']['needs']}
    results['stage4-native'] = {'result': 'skipped'}
    assert run(json.dumps(plan), results).returncode == 0


@pytest.mark.parametrize('changed', [
    'native/stage4_canonical/src/lib.rs',
    'native/stage4_canonical/Cargo.toml',
    'native/stage4_canonical/Cargo.lock',
    'native/stage4_canonical/pyproject.toml',
    'synapse/experiments/gold/canonicalization.py',
    'tests/test_stage4_gold_native.py',
])
def test_codec_changes_select_and_require_native(codec_workspace, changed):
    from select_tests import build_plan

    root, files, workflow = codec_workspace
    plan = build_plan(root, files, {changed}, workflow)
    assert plan['groups']['stage4-native']['enabled']
    results = {name: {'result': 'success'} for name in workflow['jobs']['gold-slow']['needs']}
    assert run(json.dumps(plan), results).returncode == 0
    results['stage4-native'] = {'result': 'failure'}
    assert run(json.dumps(plan), results).returncode != 0


def test_native_workflow_runs_only_when_the_gate_requires_it():
    import yaml

    workflow = yaml.safe_load((GATE.parents[2] / '.github/workflows/stage4-gold.yml').read_text())
    native = workflow['jobs']['stage4-native']
    assert native['needs'] == 'select'
    assert native['if'] == "fromJSON(needs.select.outputs.plan).groups['stage4-native'].enabled"
    assert 'stage4-native' in workflow['jobs']['gold-slow']['needs']


def test_every_selected_job_must_have_a_successful_result():
    plan = {'groups': {'stage4-native': {'enabled': True}, 'other': {'enabled': True},
                       'unselected': {'enabled': False}},
            'durable': True, 'version': True, 'tooling': True, 'additional': ['tests/test_selected.py'],
            'ui': {'python': True}}
    names = {'select', 'stage4-native', 'other', 'durable', 'version', 'selection-acceptance',
             'additional-tests', 'ui'}
    results = {name: {'result': 'success'} for name in names}
    results['unselected'] = {'result': 'skipped'}
    assert run(json.dumps(plan), results).returncode == 0
    for name in names:
        for state in ('failure', 'cancelled', 'skipped', None):
            changed = dict(results)
            if state is None:
                del changed[name]
            else:
                changed[name] = {'result': state}
            assert run(json.dumps(plan), changed).returncode != 0, (name, state)
