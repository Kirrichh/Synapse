"""Bad accounting after a real effect cannot rewrite correctness or retry it."""

from acceptance.stage4.stage15._run_case import completed_run


def test_provider_total_mismatch_blocks_accounting_without_rewriting_outcome(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch, usage_total=20) as (case, finished, requests):
        assert finished['outcome_status'] == 'NO_CANDIDATE', finished
        assert finished['result']['telemetry_completeness'] == 'INCOMPLETE'
        assert finished['observability']['telemetry_status'] == 'TOTAL_MISMATCH'
        assert finished['observability']['artifact_status'] == 'COMPLETE'
        before = finished['result']
        code, resumed = case.cli('project', 'resume', '--run-dir', case.run_root)
        assert code == 0, resumed
        assert resumed['result'] == before
        assert len(requests) == 1
