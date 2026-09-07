"""Explicit live-provider preflight, run only by the dedicated manual/authorized CI job."""

import json
import os

from acceptance.stage4.stage15.test_provider_capture_acceptance import run_actual_mini
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.provider_transport import GEMINI_CHAT_ENDPOINT


def test_live_gemini_worker_continues_after_a_real_tool_result(tmp_path):
    assert os.environ.get("GEMINI_API_KEY"), "this explicit live job requires its provider credential"
    # Three allowed turns can each spend 30 seconds in the real provider.
    # Leave time for process startup and terminal capture; preserve timeouts as failures.
    store, result = run_actual_mini(tmp_path, GEMINI_CHAT_ENDPOINT, model="gemini-3.1-flash-lite",
        credential_env="GEMINI_API_KEY", timeout_seconds=120, payload=(
            "Inspect this empty Git repository. First use the bash tool to run pwd. "
            "Wait for its result, then in your next reply use the bash tool to run "
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT. Do not combine the two steps; "
            "this task checks a real tool-result continuation. Do not modify files."))
    frames = inspect_capture(store.cut())
    report = reconcile_telemetry(store.cut()).to_dict()
    (tmp_path / "reconciliation.json").write_text(json.dumps(report, indent=2))
    (tmp_path / "worker-result.json").write_text(json.dumps({
        "status": result.status.value, "failure_reason": result.report.failure_reason,
        "summary": result.report.summary, "diagnostics": dict(result.diagnostics),
        "usage": {"status": result.usage.token_status.value, "total_tokens": result.usage.total_tokens,
                  "input_tokens": result.usage.input_tokens, "output_tokens": result.usage.output_tokens,
                  "thinking_tokens": result.usage.thinking_tokens}}, indent=2, default=str))
    assert result.status.value == "NO_PATCH", result
    calls = [frame for frame in frames if frame["kind"] == "LOGICAL_OPEN"]
    assert len(calls) >= 2, "the model did not perform the requested continuation"
    closure, = [f["payload"] for f in frames if f["kind"] == "INVOCATION_CLOSED"]
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
    messages = trajectory["messages"]
    observations = [i for i, m in enumerate(messages) if m.get("role") == "tool"
        and m.get("extra", {}).get("returncode") == 0
        and m["extra"].get("raw_output", "").strip() == str(tmp_path / "repo")]
    assert observations, "the model did not actually execute pwd"
    assert any(a["command"] == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        for m in messages[observations[0] + 1:] for a in m.get("extra", {}).get("actions", [])), (
        "the model did not submit after receiving the real tool result")
    assert all(frame["payload"]["provider"] == "gemini" for frame in frames if frame["kind"] == "INVOCATION_OPEN")
    assert report["status"] == "COMPLETE", report
