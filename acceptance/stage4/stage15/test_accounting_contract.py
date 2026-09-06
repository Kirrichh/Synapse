"""OBS-01/02 acceptance: immutable evidence and explicit inclusion semantics."""

from dataclasses import replace

import pytest

from synapse.experiments.gold.stage15.telemetry import (
    Component, CoreTelemetryEnvelope, LLMCallRecord, Phase, TelemetryViolation,
    UsageConsistency, UsageProfile, normalize_usage, reference,
)


def test_missing_usage_is_unknown_and_never_a_zero_call():
    for profile in UsageProfile:
        value = normalize_usage(profile, None)
        assert value.consistency is UsageConsistency.UNAVAILABLE
        assert value.component_total_tokens is None
        assert value.provider_total_tokens is None


def test_provider_total_never_silently_replaced_by_component_sum():
    value = normalize_usage(UsageProfile.OPENAI_CHAT,
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 19})
    assert value.consistency is UsageConsistency.TOTAL_MISMATCH
    assert (value.provider_total_tokens, value.component_total_tokens, value.mixed_unallocated_tokens) == (19, 15, 4)


def test_provider_cache_and_reasoning_have_explicit_inclusion_semantics():
    openai = normalize_usage(UsageProfile.OPENAI_CHAT, {
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        "prompt_tokens_details": {"cached_tokens": 90},
        "completion_tokens_details": {"reasoning_tokens": 40},
    })
    gemini = normalize_usage(UsageProfile.GEMINI_NATIVE, {
        "promptTokenCount": 100, "candidatesTokenCount": 10, "thoughtsTokenCount": 40,
        "cachedContentTokenCount": 90, "totalTokenCount": 150,
    })
    anthropic = normalize_usage(UsageProfile.ANTHROPIC_MESSAGES, {
        "input_tokens": 10, "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 10, "output_tokens": 50,
    })
    for value in (openai, gemini, anthropic):
        assert value.consistency is UsageConsistency.CONSISTENT
        assert (value.input_tokens, value.output_tokens, value.component_total_tokens) == (100, 50, 150)
    assert anthropic.provider_total_tokens is None


@pytest.mark.parametrize("usage,status", [
    ({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5,
      "prompt_tokens_details": {"cached_tokens": 4}}, UsageConsistency.DOUBLE_COUNT_RISK),
    ({"prompt_tokens": True, "completion_tokens": 3, "total_tokens": 4}, UsageConsistency.SOURCE_INCONSISTENT),
    ({"prompt_tokens": 2, "completion_tokens": 3}, UsageConsistency.INCOMPLETE),
])
def test_bad_or_partial_source_cannot_support_consistent_accounting(usage, status):
    assert normalize_usage(UsageProfile.OPENAI_CHAT, usage).consistency is status


def test_all_observation_bytes_are_bound_but_repeated_content_is_not_a_call_id():
    envelope = CoreTelemetryEnvelope("run-1", "attempt-1", Phase.WORKER, Component.WORKER_SUBPROCESS,
        "occurrence-1", "1" * 32, "2" * 16, None, "clock-1", 123, 5, 10, ())
    ref = reference({"request": "identical"}, "test.request/v1")
    record = LLMCallRecord(envelope, "call-1", "logical-1", "openai", "model-1", None, ref, ref, ref,
        "COMPLETED", normalize_usage(UsageProfile.OPENAI_CHAT,
            {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}))
    assert LLMCallRecord.from_dict(record.to_dict()) == record
    for changed in (
        replace(envelope, started_unix_ns=124), replace(envelope, ended_monotonic_ns=11),
        replace(envelope, trace_id="3" * 32), replace(envelope, lineage_refs=(ref,)),
    ):
        assert replace(record, envelope=changed).record_id != record.record_id
    assert replace(record, llm_call_id="call-2").record_id != record.record_id
    tampered = record.to_dict()
    tampered["envelope"]["started_unix_ns"] = 999
    with pytest.raises(TelemetryViolation):
        LLMCallRecord.from_dict(tampered)
