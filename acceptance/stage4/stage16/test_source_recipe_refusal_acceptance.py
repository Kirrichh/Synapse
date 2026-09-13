"""Actual command outcomes, bounded interruption and unsafe source refusals."""

import json
import sys
import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn
from synapse.experiments.gold.knowledge_environment import open_gold_project


@pytest.mark.parametrize('program,expected,status', [
    ('print("actual-result")', 'invented-result', 'REJECTED'),
    ('import time; time.sleep(3)', 'done', 'INTERRUPTED'),
    ('from pathlib import Path; Path("calc.py").write_text("changed"); print("done")', 'done', 'REJECTED'),
])
def test_unconfirmed_recipe_never_becomes_knowledge(tmp_path, program, expected, status):
    repo, state, input_path = prepare(tmp_path, execute=True)
    value = json.loads(input_path.read_text())
    value['claim']['kind'] = 'VERIFICATION_RECIPE'
    value['claim']['recipe'] = {'command': [sys.executable, '-B', '-c', program], 'expectation': {
        'expected_exit_codes': [0], 'expected_nonzero_exit': False, 'combined_output_contains': [expected],
        'combined_output_not_contains': [], 'timeout_seconds': 1}}
    input_path.write_text(json.dumps(value))
    code, result = learn(state, input_path)
    assert code == 2 and result['status'] == status, result
    assert result['elapsed_ns'] > 0
    assert result['runtime_unchanged'] is True
    assert result['worktree_clean'] is ('write_text' not in program)
    assert not open_gold_project(state).library.search_index()
    assert (repo / 'calc.py').read_text() == 'def double(value):\n    return value * 2\n'
    code, reopened = learn(state, input_path)
    assert code == 2 and reopened == result  # No silent second external execution.


def test_recipe_requires_project_execute_entitlement(tmp_path):
    _, state, input_path = prepare(tmp_path)
    value = json.loads(input_path.read_text())
    value['claim']['kind'] = 'VERIFICATION_RECIPE'
    value['claim']['recipe'] = {'command': [sys.executable, '-B', '-c', 'print("done")'], 'expectation': {
        'expected_exit_codes': [0], 'expected_nonzero_exit': False, 'combined_output_contains': ['done'],
        'combined_output_not_contains': [], 'timeout_seconds': 1}}
    input_path.write_text(json.dumps(value))
    code, result = learn(state, input_path)
    assert code == 2 and 'capabilities' in result['reason']
    assert not open_gold_project(state).library.search_index()
