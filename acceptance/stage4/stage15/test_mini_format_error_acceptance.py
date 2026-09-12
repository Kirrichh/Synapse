"""Paid responses with invalid actions remain in actual Mini and C1 accounting."""

import json

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.mini_adapter import mini_trajectory_response_messages

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini


@pytest.mark.parametrize(("model", "path"), [
    ("gpt-4o-mini", "/v1/chat/completions"),
    ("gemini-3.1-flash-lite", "/v1beta/openai/chat/completions"),
])
def test_format_error_then_tool_continuation_retains_every_paid_response(tmp_path, model, path):
    with provider_endpoint(model=model, path=path, usage_details=False,
            commands=(None, "pwd", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")) as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint, model=model)
    frames = inspect_capture(store.cut())
    closure, = [f["payload"] for f in frames if f["kind"] == "INVOCATION_CLOSED"]
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
    messages = mini_trajectory_response_messages(trajectory)
    assert len(requests) == len(messages) == trajectory["info"]["model_stats"]["api_calls"] == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["extra"]["interrupt_type"] == "FormatError"
    assert [m["extra"]["response"]["id"] for m in messages] == ["chatcmpl-1", "chatcmpl-2", "chatcmpl-3"]
    assert [f["payload"]["status"] for f in frames if f["kind"] == "LOGICAL_CLOSED"] == [
        "FAILED", "COMPLETED", "COMPLETED"]
    assert result.status.value == "NO_PATCH"
    assert result.usage.total_tokens == 54
    report = reconcile_telemetry(store.cut()).to_dict()
    assert report["status"] == "COMPLETE", report
    assert report["source_totals"]["physical_provider_reported_tokens"] == 54
    assert report["source_totals"]["trajectory_success_response_tokens"] == 54
