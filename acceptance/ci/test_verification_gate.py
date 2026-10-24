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
