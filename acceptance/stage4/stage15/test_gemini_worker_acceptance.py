"""Actual Mini/SDK calls through Gemini's compatible endpoint and durable capture."""

import json

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, TelemetryStatus
from synapse.experiments.gold.stage15.telemetry import UsageProfile, normalize_usage


def test_gemini_success_retains_totals_without_inventing_subset_measurements(tmp_path):
    with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                           usage_details=False) as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint, model="gemini-3.1-flash-lite")
    assert result.status.value == "NO_PATCH", result
    assert len(requests) == 1
    report = reconcile_telemetry(store.cut())
    assert report.status is TelemetryStatus.COMPLETE, report.to_dict()
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] == 18
    frames = inspect_capture(store.cut())
    receipt, = [item["payload"] for item in frames if item["kind"] == "CALL_RESPONSE"]
    body = json.loads(read_source(store.root, HashBoundRef.from_dict(receipt["response_ref"])))
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    usage = normalize_usage(UsageProfile.GEMINI_OPENAI_CHAT, body["usage"])
    assert usage.thinking_tokens is usage.cache_read_tokens is None


def test_gemini_sdk_retry_retains_provider_identity_and_unknown_failed_usage(tmp_path):
    with provider_endpoint(first_status=503, model="gemini-3.1-flash-lite",
                           path="/v1beta/openai/chat/completions") as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint, model="gemini-3.1-flash-lite")
    assert result.status.value == "NO_PATCH", result
    assert len(requests) == 2
    assert all(item["model"] == "gemini-3.1-flash-lite" for item in requests)
    frames = inspect_capture(store.cut())
    invocation, = [item["payload"] for item in frames if item["kind"] == "INVOCATION_OPEN"]
    assert invocation["provider"] == "gemini"
    assert invocation["usage_profile"] == UsageProfile.GEMINI_OPENAI_CHAT.value
    calls = [item["payload"] for item in frames if item["kind"] == "CALL_STARTED"]
    assert len(calls) == len({item["call_id"] for item in calls}) == 2
    assert len({item["logical_call_id"] for item in calls}) == 1
    responses = [item["payload"] for item in frames if item["kind"] == "CALL_RESPONSE"]
    assert [item["status_code"] for item in responses] == [503, 200]
    body = json.loads(read_source(store.root, HashBoundRef.from_dict(responses[-1]["response_ref"])))
    usage = normalize_usage(UsageProfile.GEMINI_OPENAI_CHAT, body["usage"])
    assert (usage.input_tokens, usage.output_tokens, usage.thinking_tokens, usage.cache_read_tokens) == (11, 7, 2, 3)
    assert usage.component_total_tokens == usage.provider_total_tokens == 18
    report = reconcile_telemetry(store.cut())
    assert report.status is TelemetryStatus.MISSING_CALL, report.to_dict()
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] is None
