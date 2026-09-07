"""Projection gaps are visible and repair never reopens an external effect."""

import json

from acceptance.stage4.stage15._run_case import completed_run
from synapse.experiments.gold.stage15.events import open_event_stream
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.experiments.gold.stage15.telemetry import reference


def test_recovery_republishes_missing_event_and_graph_with_same_outcome(tmp_path, monkeypatch):
    with completed_run(tmp_path, monkeypatch) as (case, finished, requests):
        key = finished["observability"]["assessment_key"]
        stream = open_event_stream(run_root=case.run_root, assessment_key=key)
        worker_event = next(e for e in stream.expected if e.phase.value == "WORKER")
        event_file = next((case.run_root / "run-records" / "gold-event").glob(worker_event.event_id + ".*.json"))
        graph_file = next((case.run_root / "run-records" / "observability-lineage").glob(key + ".*.json"))
        event_raw, graph_raw = event_file.read_bytes(), graph_file.read_bytes()
        cut_file, = (case.run_root / "run-records" / "resource-cut").glob("*.json")
        cut_body = json.loads(cut_file.read_text())
        cut_ref = reference(cut_body, cut_body["schema_version"])
        resource_graph = next((case.run_root / "run-records" / "observability-lineage").glob(cut_ref.sha256 + ".*.json"))
        resource_record = next(path for path in (case.run_root / "run-records" / "observation").glob("*.json")
            if json.loads(path.read_text()).get("operation") == "observation.publish")
        missing_resources = {path: path.read_bytes() for path in (cut_file, resource_graph, resource_record)}
        ledger = case.run_root / "stage15" / "capture" / "calls.v1"
        measured_sources = ledger.read_bytes()
        for path in missing_resources:
            path.unlink()
        event_file.unlink()
        graph_file.unlink()
        view = open_event_stream(run_root=case.run_root, assessment_key=key).projection()
        assert {"attempt_id": worker_event.attempt_id, "phase": "WORKER"} in view["missing_phases"]
        inspected = inspect_observability(run_root=case.run_root, assessment_key=key)
        codes = {f["code"] for f in inspected["discrepancies"]}
        assert "event_phase_gap" in codes
        assert "observability_lineage_missing_or_changed" in codes
        assert "resource_cut_unavailable_or_changed" in codes
        assert not event_file.exists() and not graph_file.exists()
        code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
        assert code == 0, resumed
        assert resumed["result"] == finished["result"]
        assert resumed["observability"] == finished["observability"]
        assert event_file.read_bytes() == event_raw
        assert graph_file.read_bytes() == graph_raw
        for path, raw in missing_resources.items():
            assert path.read_bytes() == raw
        assert ledger.read_bytes() == measured_sources
        assert len(requests) == 1
