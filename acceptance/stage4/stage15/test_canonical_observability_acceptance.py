"""Actual canonical CLI, connected knowledge owners, Mini SDK and restart."""

from acceptance.stage4.stage15._run_case import completed_run
from synapse.experiments.gold.stage15.events import open_event_stream
from synapse.experiments.gold.stage15.run_observability import inspect_observability


def test_canonical_run_reconciles_physical_sources_and_does_not_repeat_calls(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch) as (case, finished, requests):
        assert finished["outcome_status"] == "NO_CANDIDATE", finished
        assert finished["result"]["telemetry_completeness"] == "COMPLETE"
        assert finished["result"]["structured_outcome"]["payload"]["telemetry_completeness"] == "COMPLETE"
        observations = finished["observability"]
        assert observations["telemetry_status"] == "COMPLETE", observations
        assert observations["artifact_status"] == "COMPLETE", observations
        assert observations["snapshot_statuses"] == ["COMPLETE"], observations
        assert observations["missing_phases"] == [], observations
        assert observations["measurement_gaps"] == [], observations
        stream = open_event_stream(run_root=case.run_root, assessment_key=observations["assessment_key"])
        assert stream.projection()["missing_phases"] == []
        inspected = inspect_observability(run_root=case.run_root, assessment_key=observations["assessment_key"])
        assert inspected["discrepancies"] == [], inspected["discrepancies"]
        before = finished["result"]
        monkeypatch.delenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY")
        code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
        assert code == 0, resumed
        assert resumed["result"] == before
        assert resumed["observability"] == observations
        assert len(requests) == 1
