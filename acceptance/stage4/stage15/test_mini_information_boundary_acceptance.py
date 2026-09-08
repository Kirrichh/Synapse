"""Actual local output and its transformed failure cannot become a model input."""

import base64
import hashlib
import json
import shlex
import sys

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry

from acceptance.stage4.stage10.test_worker_input_contract import task_input, information_input
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini


def test_transformed_private_tool_error_stops_before_a_second_provider_request(tmp_path):
    private = information_input(b"PRIVATE-NEGATIVE-EXPERIENCE: do not export")
    derived = hashlib.sha256(private.canonical_bytes).hexdigest()
    code = ("import hashlib, os; from pathlib import Path; "
            "value = Path(os.environ['SYNAPSE_MINI_INFORMATION_PATH']).read_bytes(); "
            "print(value.decode()); "
            "raise RuntimeError('derived-private-' + hashlib.sha256(value).hexdigest())")
    command = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
    with provider_endpoint(command=command) as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint, payload=task_input().text, information=private)
    assert len(requests) == 1, result
    assert result.status.value == "ERROR", result
    assert result.report.failure_reason == "mini_local_information_boundary"
    outgoing = json.dumps(requests)
    assert derived not in outgoing
    assert base64.urlsafe_b64encode(b"PRIVATE-NEGATIVE-EXPERIENCE: do not export").rstrip(b"=").decode() not in outgoing
    frames = inspect_capture(store.cut())
    closure, = [frame["payload"] for frame in frames if frame["kind"] == "INVOCATION_CLOSED"]
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
    assert trajectory["info"]["exit_status"] == "LocalInformationBoundary"
    assert trajectory["info"]["model_stats"]["api_calls"] == 1
    assert "derived-private-" + derived in json.dumps(trajectory)
    assert len([frame for frame in frames if frame["kind"] == "LOGICAL_OPEN"]) == 1
    report = reconcile_telemetry(store.cut()).to_dict()
    assert report["status"] == "COMPLETE", report
    assert report["source_totals"]["physical_provider_reported_tokens"] == 18
