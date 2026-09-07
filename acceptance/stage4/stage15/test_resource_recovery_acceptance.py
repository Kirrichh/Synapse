"""A killed process cannot acquire replacement historical resource samples."""

import subprocess
import sys

from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture
from synapse.experiments.gold.stage15.resource_accounting import ResourceRecorder, ResourceEvidence
from synapse.experiments.gold.stage15.telemetry import reference


def test_process_death_retains_unfinished_operation_without_inventing_its_end(tmp_path):
    process = subprocess.run([sys.executable, "-c", """
import os, sys
from pathlib import Path
from synapse.resource_usage import recording_resources, measure_operation
from synapse.experiments.gold.stage15.capture_store import CaptureStore
from synapse.experiments.gold.stage15.resource_accounting import ResourceRecorder
from synapse.experiments.gold.stage15.telemetry import reference
root = Path(sys.argv[1])
outcome = reference({'completed': True}, 'acceptance.result/v1')
store = CaptureStore(root / 'capture', run_id='killed-run', manifest_ref=outcome)
with recording_resources(ResourceRecorder(store)), measure_operation('runtime.execution'):
    with measure_operation('worker.delivery'):
        (root / 'effect').write_bytes(b'already happened')
        os._exit(73)
""", str(tmp_path)], capture_output=True, timeout=30)
    assert process.returncode == 73, process.stderr.decode()
    assert (tmp_path / "effect").read_bytes() == b"already happened"
    outcome = reference({"completed": True}, "acceptance.result/v1")
    store = CaptureStore(tmp_path / "capture", run_id="killed-run", manifest_ref=outcome)
    # Completing the run's durable observation cut records incompleteness; it
    # cannot reconstruct a clock reading from the existence of an effect.
    ResourceRecorder(store).seal(outcome)
    cut = store.resource_execution_cut()
    frames = inspect_capture(cut)
    assert sum(frame["kind"] == "RESOURCE_STARTED" for frame in frames) == 2
    assert all(frame["kind"] != "RESOURCE_FINISHED" for frame in frames)
    evidence = ResourceEvidence(cut=cut, run_id=store.run_id, outcome_ref=outcome)
    assert evidence.report().status == "INCOMPLETE"
    assert len([gap for gap in evidence.gaps if gap["code"] == "resource_operation_unfinished"]) == 2
    assert all(record["cpu_ns"] is None and record["io_write_bytes"] is None for record in evidence.infrastructure_records())
    retained = (store.root / "calls.v1").read_bytes()
    reopened = CaptureStore(store.root, run_id=store.run_id, manifest_ref=outcome)
    assert reopened.resource_execution_cut() == cut
    assert (store.root / "calls.v1").read_bytes() == retained
