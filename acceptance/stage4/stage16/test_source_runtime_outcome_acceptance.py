"""Real environment drift and process termination cannot become method guards."""

from copy import deepcopy
import json
import os
import sys

import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn, recall
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.source_operation_journal import read_source_operations
from synapse.experiments.gold.source_verification import (
    inspect_source_observations, inspect_source_execution_result,
)


@pytest.mark.parametrize('exit_code', [0, 3])
def test_changed_runtime_is_retained_after_both_passing_and_failing_commands(tmp_path, monkeypatch, exit_code):
    _, state, path = prepare(tmp_path, execute=True)
    # A private import search directory belongs only to this acceptance
    # process. The real child changes installed-package metadata there; both
    # measurements still use the product's unmodified runtime observer.
    packages = tmp_path / 'isolated_packages'
    packages.mkdir()
    monkeypatch.syspath_prepend(str(packages))
    metadata = packages / 'synapse_acceptance_drift-1.0.dist-info'
    program = (f'from pathlib import Path; p = Path({str(metadata)!r}); p.mkdir(); '
               'p.joinpath("METADATA").write_text("Metadata-Version: 2.1\\nName: synapse-acceptance-drift\\nVersion: 1.0\\n"); '
               f'print("double partial-result"); raise SystemExit({exit_code})')
    value = json.loads(path.read_text())
    value['claim'].update(kind='VERIFICATION_RECIPE', recipe={
        'command': [sys.executable, '-B', '-c', program], 'expectation': {
            'expected_exit_codes': [0], 'expected_nonzero_exit': False,
            'combined_output_contains': ['double partial-result'], 'combined_output_not_contains': [], 'timeout_seconds': 10}})
    path.write_text(json.dumps(value))
    code, result = execute_source_ingestion(state_root=state, input_path=path)
    assert code == 2 and result['status'] == 'INFRA_ERROR', result
    assert result['reason'] == 'SOURCE_RUNTIME_CHANGED'
    assert result['command_result']['status'] == ('PASS' if exit_code == 0 else 'FAIL')
    assert result['command_result']['returncode'] == exit_code
    assert result['worktree_clean'] is True and result['runtime_unchanged'] is False
    assert learn(state, path) == (code, result)
    code, found = recall(state, tmp_path, value['claim'])
    assert code == 0, found
    experience = found['selected'][0]['experience']
    assert experience['status'] == 'INFRA_ERROR' and experience['applicability'] == 'UNASSESSED'
    assert experience['bindings'][0]['qualname'] == 'double'
    assert experience['command_result']['stdout'] == 'double partial-result\n'
    assert experience['runtime_unchanged'] is False and experience['publication'] is None
    operation = read_source_operations(state)[0]
    observed = inspect_source_observations(value['claim'], operation['observations'], operation['evidence'])
    assert inspect_source_execution_result(result, claim=value['claim'], observed=observed) == result
    forged = deepcopy(result)
    forged.update(status='REJECTED', reason='SOURCE_COMMAND_CONTRACT_NOT_MET')
    with pytest.raises(ValueError, match='observed conditions'):
        inspect_source_execution_result(forged, claim=value['claim'], observed=observed)
    observations = deepcopy(operation['observations'])
    recheck = next(item for item in observations if item['phase'] == 'RUNTIME_RECHECKED')
    recheck['data']['unchanged'] = True
    with pytest.raises(ValueError, match='retained measurements'):
        inspect_source_observations(value['claim'], observations, operation['evidence'])


@pytest.mark.skipif(os.name != 'posix', reason='POSIX records process termination as a negative return code')
def test_signal_termination_is_unknown_even_when_recipe_expects_that_exit_code(tmp_path):
    _, state, path = prepare(tmp_path, execute=True)
    value = json.loads(path.read_text())
    value['claim'].update(kind='VERIFICATION_RECIPE', recipe={
        'command': [sys.executable, '-B', '-c',
                    'import os, signal; print("double partial-result", flush=True); os.kill(os.getpid(), signal.SIGTERM)'],
        'expectation': {'expected_exit_codes': [-15], 'expected_nonzero_exit': True,
            'combined_output_contains': ['double partial-result'], 'combined_output_not_contains': [], 'timeout_seconds': 10}})
    path.write_text(json.dumps(value))
    code, result = learn(state, path)
    assert code == 2 and result['status'] == 'INTERRUPTED', result
    assert result['reason'] == 'SOURCE_COMMAND_TERMINATED_BY_SIGNAL'
    assert result['command_result']['status'] == 'PASS'
    assert result['runtime_unchanged'] is True
    assert learn(state, path) == (code, result)
    code, found = recall(state, tmp_path, value['claim'])
    assert code == 0, found
    experience = found['selected'][0]['experience']
    assert experience['verification'] == 'UNVERIFIED' and experience['publication'] is None
    assert experience['execution'] == 'INTERRUPTED'
