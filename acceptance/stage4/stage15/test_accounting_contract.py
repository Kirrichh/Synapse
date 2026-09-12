"""OBS-01/02 acceptance: immutable evidence and explicit inclusion semantics."""

from dataclasses import replace

import pytest

from synapse.llm.capture import CaptureUnavailable
from synapse.worker.provider_transport import GEMINI_CHAT_ENDPOINT, MiniProviderConfiguration
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


@pytest.mark.parametrize("profile", (UsageProfile.OPENAI_CHAT, UsageProfile.GEMINI_OPENAI_CHAT))
@pytest.mark.parametrize("field", ("prompt_tokens_details", "completion_tokens_details"))
@pytest.mark.parametrize("value", (False, 0, "", []))
def test_present_malformed_detail_cannot_turn_into_absent_usage(profile, field, value):
    raw = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, field: value}
    assert normalize_usage(profile, raw).consistency is UsageConsistency.SOURCE_INCONSISTENT


@pytest.mark.parametrize("value", (False, 0.0, "0", -1, float("nan"), float("inf")))
def test_gemini_tool_prompt_count_requires_an_exact_count(value):
    raw = {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 12,
           "toolUsePromptTokenCount": value}
    assert normalize_usage(UsageProfile.GEMINI_NATIVE, raw).consistency is UsageConsistency.SOURCE_INCONSISTENT


@pytest.mark.parametrize("value", (None, 0))
def test_absent_or_zero_gemini_tool_prompt_count_retains_its_semantics(value):
    raw = {"promptTokenCount": 10, "candidatesTokenCount": 2, "totalTokenCount": 12,
           "toolUsePromptTokenCount": value}
    usage = normalize_usage(UsageProfile.GEMINI_NATIVE, raw)
    assert usage.consistency is UsageConsistency.CONSISTENT
    assert usage.component_total_tokens == usage.provider_total_tokens == 12


def test_provider_total_never_silently_replaced_by_component_sum():
    value = normalize_usage(UsageProfile.OPENAI_CHAT,
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 19})
    assert value.consistency is UsageConsistency.TOTAL_MISMATCH
    assert (value.provider_total_tokens, value.component_total_tokens, value.mixed_unallocated_tokens) == (19, 15, 4)


def test_gemini_compatible_usage_keeps_missing_subsets_unknown():
    value = normalize_usage(UsageProfile.GEMINI_OPENAI_CHAT,
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert value.consistency is UsageConsistency.CONSISTENT
    assert value.component_total_tokens == 15
    assert value.thinking_tokens is value.cache_read_tokens is None
    mismatch = normalize_usage(UsageProfile.GEMINI_OPENAI_CHAT,
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 19,
         "completion_tokens_details": {"reasoning_tokens": 4}})
    assert mismatch.consistency is UsageConsistency.TOTAL_MISMATCH
    assert (mismatch.provider_total_tokens, mismatch.component_total_tokens) == (19, 15)


def test_gemini_provider_binding_uses_the_exact_compatible_endpoint():
    configuration = MiniProviderConfiguration("gemini-3.1-flash-lite", credential_env="GEMINI_API_KEY",
                                              endpoint=GEMINI_CHAT_ENDPOINT)
    assert configuration.provider == "gemini"
    for endpoint in ("https://generativelanguage.googleapis.com/v1/chat/completions",
                     "https://example.invalid/v1beta/openai/chat/completions",
                     GEMINI_CHAT_ENDPOINT + "?key=unretained",
                     GEMINI_CHAT_ENDPOINT + "#fragment"):
        with pytest.raises(CaptureUnavailable):
            MiniProviderConfiguration("gemini-3.1-flash-lite", api_key="unread", endpoint=endpoint)
    with pytest.raises(CaptureUnavailable):
        MiniProviderConfiguration("gpt-4o-mini", api_key="unread", endpoint=GEMINI_CHAT_ENDPOINT)


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


@pytest.mark.parametrize('http_status,error,profile,expected', [
    (503, [{'error': {'code': 503, 'message': 'Busy', 'status': 'UNAVAILABLE'}}], UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.UNAVAILABLE),
    (200, [{'error': {'code': 503, 'message': 'Busy', 'status': 'UNAVAILABLE'}}], UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
    (503, [{'error': {'code': 500, 'message': 'Busy', 'status': 'UNAVAILABLE'}}], UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
    (503, [{'error': {'code': True, 'message': 'Busy', 'status': 'UNAVAILABLE'}}], UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
    (503, [{'error': {'code': 503, 'message': 0, 'status': 'UNAVAILABLE'}}], UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
    (503, {'usage': ['not a usage object']}, UsageProfile.GEMINI_OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
    (503, [{'error': {'code': 503, 'message': 'Busy', 'status': 'UNAVAILABLE'}}], UsageProfile.OPENAI_CHAT, UsageConsistency.SOURCE_INCONSISTENT),
])
def test_error_envelope_is_not_a_usage_object(http_status, error, profile, expected):
    import json
    from synapse.experiments.gold.stage15.telemetry import call_record_from_capture
    ref = reference({'input': 'physical request'}, 'acceptance.input/v1')
    response = json.dumps(error).encode()
    result = call_record_from_capture(run_id='run-1',
        invocation={'attempt_id': 'attempt-1', 'invocation_ref': ref.to_dict(),
                    'usage_profile': profile.value, 'provider': 'gemini' if profile is UsageProfile.GEMINI_OPENAI_CHAT else 'openai', 'model': 'model'},
        started={'call_id': 'call-1', 'logical_call_id': 'logical-1', 'clock_domain': 'clock-1',
                 'started_unix_ns': '123', 'started_monotonic_ns': '5', 'request_ref': ref.to_dict()},
        terminal={'status_code': http_status, 'ended_monotonic_ns': '10',
                  'response_ref': reference(error, 'acceptance.response/v1').to_dict()},
        response=response, capture_ref=ref)
    assert result.usage.consistency is expected
    assert result.usage.provider_total_tokens is None and result.usage.component_total_tokens is None
    assert result.status == ('COMPLETED' if http_status == 200 else 'FAILED')
