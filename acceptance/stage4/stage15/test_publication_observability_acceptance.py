"""Actual Mini patch, C1 verification, oracle and atomic knowledge publication."""

import json
import shlex

from acceptance.stage4.stage15._run_case import completed_run
from acceptance.stage4.stage15._retained_sources import changed_source, inventory
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from tests.test_swebench_gold_runner import NEW_SOURCE


def test_captured_patch_publication_retains_real_verification_measurements(tmp_path, monkeypatch):
    script = 'from pathlib import Path; Path("src/calc.py").write_text(' + repr(NEW_SOURCE) + ')'
    command = 'python -c ' + shlex.quote(script) + ' && echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
    with completed_run(tmp_path, monkeypatch, provider_command=command, oracle_result=False) as (case, finished, requests):
        result = finished['result']
        assert result['structured_outcome']['payload']['status'] == 'VERIFIED_REUSABLE_PARTIAL', finished
        assert result['structured_outcome']['payload']['publication_result'] == 'COMMITTED'
        assert result['telemetry_completeness'] == 'COMPLETE'
        observation = finished['observability']
        assert observation['telemetry_status'] == observation['artifact_status'] == 'COMPLETE', observation
        assert observation['snapshot_statuses'] == ['COMPLETE']
        assert observation['measurement_gaps'] == []
        records = [json.loads(p.read_bytes()) for p in (case.run_root / 'run-records' / 'observation').glob('*.json')]
        measured = next(r for r in records if r.get('record_class') == 'VerificationTelemetryRecord')
        assert measured['commands'], measured
        assert measured['oracle_duration_seconds'] is not None
        reports = list((case.run_root / 'controlled-change-reports').rglob('*.json'))
        assert reports
        with changed_source(reports[0], None):
            before = inventory(tmp_path)
            inspected = inspect_observability(run_root=case.run_root, assessment_key=observation['assessment_key'])
            assert inspected['artifact_report']['status'] == 'MISSING_BLOB', inspected
            assert inventory(tmp_path) == before
        code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
        assert code == 0, resumed
        assert resumed['result'] == result
        assert len(requests) == 1
        assert json.loads((tmp_path / 'harness' / 'oracle_state.json').read_text())['calls'] == 1
