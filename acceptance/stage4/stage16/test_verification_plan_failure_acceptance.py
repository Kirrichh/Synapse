"""Heavy shard: a real failed reproduction cannot discharge planned checks."""
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.runner.records import RunRecordStore, RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V2, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_failed_change_retains_evidence_and_never_claims_unexecuted_checks(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    case = replace(case, cli_timeout_seconds=1800)
    declaration = json.loads(case.input_path.read_text())
    original = (case.repo / 'src/calc.py').read_bytes()
    proposal = {'schema_version': LOCAL_EDIT_PROPOSAL_V1, 'alternatives': [
        {'edits': [{'path': 'src/calc.py', 'old': 'a - b', 'new': 'a * b'}]}]}
    monkeypatch.setenv('SYNAPSE_ACCEPTANCE_PROVIDER_KEY', 'acceptance-only')
    with provider_endpoint(command=LOCAL_EDIT_COMMAND + json.dumps(proposal)) as (endpoint, requests):
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
        code, completed = case.approve(pending)
        # C1's existing vocabulary means no *verified* candidate for C2 here;
        # the actual attempted patch and failed phase remain below that label.
        assert code == 0 and completed['outcome_status'] == 'NO_CANDIDATE', completed
        records = RunRecordStore(case.run_root, mutation_fence=FileSnapshotFence(case.run_root / 'run-coordinator'))
        attempt, = load_run_state(records).attempts
        proof = attempt.result.structured_outcome['payload']['verification']['payload']
        assert proof['failure_codes'] == []
        assert proof['c1']['commands_complete'] is False
        assert proof['c1']['oracle_result_ref'] is None and proof['c1']['oracle_resolved'] is None
        assert proof['reusable_candidates'] == []
        assert [item['operation_id'] for item in proof['obligations']] == [
            'operation-main', 'operation-check-1', 'operation-check-2']
        assert all(item['discharged'] is False and item['evidence_ref'] is None for item in proof['obligations'])
        # Read C1's actual report, including the successful pre-change baseline
        # and the failed reproduction. No scripted oracle verdict is supplied.
        reports = []
        for path in (case.run_root / 'controlled-change-reports').rglob('*.json'):
            value = json.loads(path.read_text())
            if value.get('schema') == 'personal_slice.report/v0.5.0':
                reports.append((path, path.read_bytes(), value))
        report_path, report_bytes, report = reports[0]
        assert len(reports) == 1
        phases = {item['name']: item for item in report['phases']}
        assert phases['baseline_1']['status'] == 'PASS'
        assert phases['apply_patch']['status'] == 'PASS'
        assert phases['reproduction_after']['status'] == 'FAIL'
        assert phases['reproduction_after']['returncode'] != 0
        assert 'acceptance_1' not in phases and 'full_suite_1' not in phases
        row, = [json.loads(line) for line in (case.run_root / 'gold_attempts.jsonl').read_text().splitlines()]
        assert row['payload']['oracle_invoked'] is False
        assert row['payload']['controlled_change_outcome'] == 'VERIFICATION_FAILED'
        assert row['payload']['failure_phase'] == 'reproduction_after'
        assert records.get(kind=RecordKind.PUBLICATION_RESULT, key='1') is not None
        assert len(requests) == 1
        assert 'return a - b' not in json.dumps(requests) and 'local_edit_result' not in json.dumps(requests)
        assert (case.repo / 'src/calc.py').read_bytes() == original
        before = (case.run_root / 'gold_attempts.jsonl').read_bytes()
        code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
        assert code == 0 and resumed['outcome_status'] == 'NO_CANDIDATE', resumed
        assert len(requests) == 1 and report_path.read_bytes() == report_bytes
        assert (case.run_root / 'gold_attempts.jsonl').read_bytes() == before
