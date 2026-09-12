"""Acceptance-only real Mini and independent C1 over two committed targets."""
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V2, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def execute_multi_target_case(root, monkeypatch, *, omit_second, verification_commands=False):
    case, _ = consumer_case(root, automatic_targets=True,
        verification_commands=verification_commands,
        extra_sources={'src/scale.py': 'def double(value):\n    return value\n'},
        task_statement='Fix add(a, b) in src/calc.py and double(value) in src/scale.py.')
    case = replace(case, cli_timeout_seconds=1800)
    declaration = json.loads(case.input_path.read_text())
    original = {path: (case.repo / path).read_bytes() for path in ('src/calc.py', 'src/scale.py')}
    checks = 'from src.calc import add; assert add(-2, 3) == 1 and add(4, 3) == 7'
    if not omit_second:
        checks += '; from src.scale import double; assert double(7) == 14 and double(-2) == -4'
    # The incomplete case deliberately has a narrower oracle. Its positive
    # observation must not discharge the still-missing second task effect.
    create_executing_oracle(root / 'harness', repo=case.repo,
        base_revision=declaration['config']['base_revision'], target_paths=tuple(original),
        command=(sys.executable, '-B', '-c', checks))
    edits = [{'path': 'src/calc.py', 'old': 'a - b', 'new': 'a + b'}]
    if not omit_second:
        edits.append({'path': 'src/scale.py', 'old': 'return value', 'new': 'return value * 2'})
    command = LOCAL_EDIT_COMMAND + json.dumps({'schema_version': LOCAL_EDIT_PROPOSAL_V1,
                                               'alternatives': [{'edits': edits}]})
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(command=command) as (endpoint, requests):
        mini = Path(sys.executable).parent / ('mini.exe' if sys.platform == 'win32' else 'mini')
        assert mini.is_file()
        declaration['config']['model'] = 'gpt-4o-mini'
        declaration['worker'] = {'provider': 'mini', 'command': [str(mini)], 'model': 'gpt-4o-mini',
            'timeout_seconds': 60, 'max_steps': 3, 'cost_limit': '1', 'input_profile': LOCAL_EDIT_PROFILE_V2,
            'accounting': {'profile': MINI_ACCOUNTING_PROFILE, 'endpoint': endpoint,
                           'credential_env': 'SYNAPSE_ACCEPTANCE_PROVIDER_KEY'}}
        case.input_path.write_text(json.dumps(declaration))
        code, pending = case.start()
        assert code == 3 and not requests, pending
        if verification_commands:
            approval = json.loads(Path(pending['request_path']).read_text())
            operations = approval['plan_contract']['operations']
            assert [item['kind'] for item in operations] == [
                'EDIT_CONTROLLED_CHANGE', 'RUN_VERIFICATION_COMMAND', 'RUN_VERIFICATION_COMMAND']
            assert [item['depends_on'] for item in operations] == [[], ['operation-main'], ['operation-check-1']]
            assert [item['argv'] for item in operations[1:]] == (
                declaration['command_policy']['acceptance_commands'] + declaration['command_policy']['full_suite_commands'])
        frozen = reopen_frozen_inputs(case.run_root)
        assert {item.path for item in frozen.resolve_targets()} == set(original)
        assert {item.qualname for item in frozen.resolve_targets()} >= {'add', 'double'}
        code, completed = case.approve(pending)
        assert code == 0, completed
        observed_path = root / 'harness/oracle_observations.jsonl'
        observed, = [json.loads(line) for line in observed_path.read_text().splitlines()]
        assert observed['before_returncode'] != 0 and observed['after_returncode'] == 0
        assert len(requests) == 1
        for private in ('local_edit_result', 'source_bindings', 'return a - b', 'return value\n'):
            assert private not in json.dumps(requests)
        assert {path: (case.repo / path).read_bytes() for path in original} == original
        records = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        attempt, = load_run_state(records).attempts
        if verification_commands:
            proof = attempt.result.structured_outcome['payload']['verification']['payload']
            assert proof['c1']['commands_complete'] is True
            assert [item['operation_id'] for item in proof['obligations']] == [
                'operation-edit-1', 'operation-edit-2', 'operation-check-1', 'operation-check-2'], proof['obligations']
            assert all(item['discharged'] is True for item in proof['obligations'])
            assert all(item['evidence_ref'] == proof['c1']['report_ref'] for item in proof['obligations'])
        publication = records.get(kind=RecordKind.PUBLICATION_RESULT, key='1')
        before = observed_path.read_bytes()
        code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
        assert code == 0 and resumed['outcome_status'] == completed['outcome_status'], resumed
        assert len(requests) == 1 and observed_path.read_bytes() == before
        return completed, attempt.result, publication.payload
