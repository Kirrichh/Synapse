"""Explicit live-provider preflight, run only by the dedicated manual/authorized CI job."""

import json
import os
from dataclasses import asdict

from acceptance.stage4.stage15.test_provider_capture_acceptance import run_actual_mini
from synapse.experiments.gold.stage15.capture_store import inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.provider_transport import GEMINI_CHAT_ENDPOINT


def test_live_gemini_worker_continues_after_a_real_tool_result(tmp_path):
    assert os.environ.get("GEMINI_API_KEY"), "this explicit live job requires its provider credential"
    store, result = run_actual_mini(tmp_path, GEMINI_CHAT_ENDPOINT, model="gemini-3.1-flash-lite",
        credential_env="GEMINI_API_KEY", payload=(
            "Inspect this empty Git repository. First use the bash tool to run pwd. "
            "Wait for its result, then in your next reply use the bash tool to run "
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT. Do not combine the two steps; "
            "this task checks a real tool-result continuation. Do not modify files."))
    frames = inspect_capture(store.cut())
    report = reconcile_telemetry(store.cut()).to_dict()
    (tmp_path / "reconciliation.json").write_text(json.dumps(report, indent=2))
    (tmp_path / "worker-result.json").write_text(json.dumps(asdict(result), indent=2, default=str))
    assert result.status.value == "NO_PATCH", result
    calls = [frame for frame in frames if frame["kind"] == "LOGICAL_OPEN"]
    assert len(calls) >= 2, "the model did not perform the requested continuation"
    assert all(frame["payload"]["provider"] == "gemini" for frame in frames if frame["kind"] == "INVOCATION_OPEN")
    assert report["status"] == "COMPLETE", report
