"""Explicit live-provider preflight, run only by the dedicated manual/authorized CI job."""

import json
import os

from acceptance.agents.coding_agents import CREDENTIAL_ENV
from acceptance.stage4.stage15.test_provider_capture_acceptance import candidate, run_actual_agent
from acceptance.stage4.stage16.live_worker_evidence import require_connection_capture
from synapse.agents.model_broker import GEMINI_CHAT_ENDPOINT
from synapse.experiments.gold.stage15.capture_store import inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry


def test_live_gemini_agent_continues_after_a_real_provider_reply(tmp_path, monkeypatch):
    assert os.environ.get("GEMINI_API_KEY"), "this explicit live job requires its provider credential"
    monkeypatch.setenv(CREDENTIAL_ENV, os.environ["GEMINI_API_KEY"])
    # Three allowed turns can each spend 30 seconds in the real provider.
    # Leave time for process startup and terminal capture; preserve timeouts as failures.
    store, result = run_actual_agent(tmp_path, GEMINI_CHAT_ENDPOINT, model="gemini-3.1-flash-lite",
        timeout_seconds=120, payload=(
            "This task checks a real conversation continuation. Your first reply must be exactly the word "
            "READY. When you are asked to reply again, reply exactly COMPLETE. Do not propose any edit."))
    frames = inspect_capture(store.cut())
    report = reconcile_telemetry(store.cut()).to_dict()
    (tmp_path / "reconciliation.json").write_text(json.dumps(report, indent=2))
    (tmp_path / "agent-result.json").write_text(json.dumps({
        "status": result.status.value, "failure_reason": result.report.failure_reason,
        "diagnostics": dict(result.diagnostics),
        "usage": {"status": result.usage.token_status.value, "total_tokens": result.usage.total_tokens}},
        indent=2, default=str))
    assert candidate(result)["status"] == "NO_PATCH", result
    calls = [frame for frame in frames if frame["kind"] == "LOGICAL_OPEN"]
    assert len(calls) >= 2, "the model did not perform the requested continuation"
    assert all(frame["payload"]["provider"] == "gemini" for frame in frames if frame["kind"] == "INVOCATION_OPEN")
    capture = require_connection_capture(store.cut())
    (tmp_path / "connection-capture.json").write_text(json.dumps(capture, indent=2))
    if capture["unknown_usage_call_ids"]:
        print("Connection recovered; provider did not report usage for retained failed requests.")
