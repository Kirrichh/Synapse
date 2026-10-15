"""Only foreign-report parsing; this file does not mint verified C1 evidence."""
from copy import deepcopy

import pytest

from synapse.experiments.gold.runner.c1_boundary import _verification_command_observations
from synapse.experiments.gold.runner.vocabulary import GoldRunViolation
from tests.test_swebench_gold_runner import policy


@pytest.fixture
def report():
    command = list(policy().acceptance_commands[0])
    return {'phases': [
        {'name': 'baseline_1', 'command': command, 'returncode': 0, 'status': 'PASS'},
        {'name': 'apply_patch', 'status': 'PASS'},
        {'name': 'scope_check_after_patch', 'status': 'PASS'},
        {'name': 'reproduction_after', 'status': 'PASS'},
        {'name': 'acceptance_1', 'command': command, 'returncode': 0, 'status': 'PASS'},
        {'name': 'full_suite_1', 'command': command, 'returncode': 0, 'status': 'PASS'},
    ]}


def test_same_command_has_separate_occurrences_after_the_change(report):
    observed = _verification_command_observations(report, policy())
    assert observed == (('acceptance_1', policy().acceptance_commands[0], True),
                        ('full_suite_1', policy().full_suite_commands[0], True))
    report['phases'] = report['phases'][:1]
    assert not any(item[2] for item in _verification_command_observations(report, policy()))


@pytest.mark.parametrize('damage', ['omit', 'argv', 'false-success', 'bool-exit', 'failed-status',
    'before-edit', 'no-apply', 'reordered', 'no-reproduction'])
def test_missing_mismatched_and_out_of_order_checks_are_not_discharged(report, damage):
    phases = report['phases']
    if damage == 'omit':
        phases.pop(4)
    elif damage == 'argv':
        phases[4]['command'] = ['python', 'different.py']
    elif damage == 'false-success':
        phases[4]['returncode'] = 1
    elif damage == 'bool-exit':
        phases[4]['returncode'] = False
    elif damage == 'failed-status':
        phases[4]['status'] = 'FAIL'
    elif damage == 'before-edit':
        phases.insert(0, phases.pop(4))
    elif damage == 'no-apply':
        phases.pop(1)
    elif damage == 'no-reproduction':
        phases.pop(3)
    else:
        phases[4], phases[5] = phases[5], phases[4]
    assert not all(item[2] for item in _verification_command_observations(report, policy()))


@pytest.mark.parametrize('damage', ['duplicate', 'untyped-name', 'not-a-phase'])
def test_ambiguous_phase_records_are_rejected(report, damage):
    if damage == 'duplicate':
        report['phases'].append(deepcopy(report['phases'][-1]))
    elif damage == 'untyped-name':
        report['phases'][4]['name'] = []
    else:
        report['phases'][4] = 'PASS'
    with pytest.raises(GoldRunViolation):
        _verification_command_observations(report, policy())


def test_a_missing_report_does_not_become_an_empty_success():
    observed = _verification_command_observations({'phases': []}, policy())
    assert len(observed) == 2 and not any(item[2] for item in observed)
