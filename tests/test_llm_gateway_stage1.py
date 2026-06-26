"""Stage 1 product LLM gateway contract tests."""
from __future__ import annotations

import hashlib
import socket
import urllib.error

import pytest

from synapse.builtins import AgentRuntime, LLMBackend
from synapse.golden_replay import CacheOnlyLLMBackend, DeterministicReplayError
from synapse.llm import LLMGateway, LLMGatewayConfig, LLMProviderStatus, LLMTokenStatus, PrivacyContext
from synapse.llm.gemini import DEFAULT_GEMINI_MODEL, GeminiProvider, parse_gemini_usage


def _gateway_with_response(response: dict, *, tier: str = "paid", model: str = DEFAULT_GEMINI_MODEL) -> LLMGateway:
    def transport(url, payload, timeout):
        assert "test-key" in url
        assert payload["contents"][0]["parts"][0]["text"]
        return response

    provider = GeminiProvider(api_key="test-key", model=model, transport=transport)
    return LLMGateway(
        LLMGatewayConfig(provider="gemini", model=model, api_key="test-key", tier=tier),
        provider_instance=provider,
    )


def _gemini_response(*, text: str = "provider text", usage: dict | None = None) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": usage or {
            "promptTokenCount": 3,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 5,
            "totalTokenCount": 12,
        },
    }


def test_default_llm_backend_remains_mock_compatible_and_exposes_typed_result(monkeypatch):
    monkeypatch.delenv("SYNAPSE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SYNAPSE_LLM_MODE", raising=False)

    backend = LLMBackend(default_model="mock", environ={})
    text = backend.complete("hello")

    assert text == "[mock] Hello! I am an AI assistant ready to help."
    assert backend.last_result is not None
    assert backend.last_result.provider == "mock"
    assert backend.last_result.status is LLMProviderStatus.COMPLETED
    assert backend.last_result.usage.token_status is LLMTokenStatus.UNAVAILABLE
    assert backend.call_count == 1


def test_agent_runtime_think_remains_string_compatible_and_keeps_typed_usage(monkeypatch):
    monkeypatch.delenv("SYNAPSE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("SYNAPSE_LLM_MODE", raising=False)

    agent = AgentRuntime("Thinker", "mock")
    result = agent.think("summarize this")

    assert isinstance(result, str)
    assert result.startswith("[mock]")
    assert agent.memory.short_term[-1] == {"prompt": "summarize this", "result": result}
    assert agent.llm.last_result.status is LLMProviderStatus.COMPLETED


def test_real_provider_mode_missing_api_key_fails_loud_without_mock_fallback():
    backend = LLMBackend(default_model=DEFAULT_GEMINI_MODEL, provider="gemini", environ={})

    typed = backend.complete_result("hello")

    assert typed.status is LLMProviderStatus.AUTH_ERROR
    assert typed.response_text == ""
    assert typed.error_message == "GEMINI_API_KEY not provided"
    with pytest.raises(RuntimeError, match="AUTH_ERROR"):
        backend.complete("hello")


def test_real_provider_mode_uses_product_gateway_and_provider_reported_usage():
    gateway = _gateway_with_response(_gemini_response())
    backend = LLMBackend(default_model=DEFAULT_GEMINI_MODEL, gateway=gateway)

    text = backend.complete("synthetic prompt")
    typed = backend.last_result

    assert text == "provider text"
    assert typed is not None
    assert typed.status is LLMProviderStatus.COMPLETED
    assert typed.provider == "gemini"
    assert typed.model == DEFAULT_GEMINI_MODEL
    assert typed.usage.token_status is LLMTokenStatus.PROVIDER_REPORTED
    assert typed.usage.input_tokens == 3
    assert typed.usage.output_tokens == 4
    assert typed.usage.total_tokens == 12
    assert typed.usage.thinking_included is True


def test_agent_runtime_think_can_use_env_enabled_product_gateway(monkeypatch):
    calls = []

    class FakeGeminiProvider:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["model"]))

        def complete(self, prompt, *, model=None, temperature=0.7, max_tokens=100):
            calls.append(("complete", prompt, model))
            return _gateway_with_response(_gemini_response(text="agent provider text")).complete(
                prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    monkeypatch.setenv("SYNAPSE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SYNAPSE_LLM_MODEL", DEFAULT_GEMINI_MODEL)
    monkeypatch.setenv("SYNAPSE_LLM_TIER", "paid")
    monkeypatch.setattr("synapse.llm.gateway.GeminiProvider", FakeGeminiProvider)

    agent = AgentRuntime("Thinker", DEFAULT_GEMINI_MODEL)
    result = agent.think("agent synthetic prompt")

    assert result == "agent provider text"
    assert agent.llm.last_result.provider == "gemini"
    assert agent.llm.last_result.usage.total_tokens == 12
    assert calls == [("init", DEFAULT_GEMINI_MODEL), ("complete", "agent synthetic prompt", DEFAULT_GEMINI_MODEL)]


def test_missing_total_count_maps_usage_to_unavailable_without_local_recompute():
    usage = parse_gemini_usage({
        "promptTokenCount": 7,
        "candidatesTokenCount": 11,
        "thoughtsTokenCount": 13,
    })

    assert usage.token_status is LLMTokenStatus.UNAVAILABLE
    assert usage.total_tokens is None
    assert usage.thinking_included is False
    assert usage.diagnostics["thoughtsTokenCount"] == 13


def test_thinking_token_consistency_guard_marks_inconsistent_provider_usage_false():
    usage = parse_gemini_usage({
        "promptTokenCount": 10,
        "candidatesTokenCount": 10,
        "thoughtsTokenCount": 10,
        "totalTokenCount": 20,
    })

    assert usage.token_status is LLMTokenStatus.PROVIDER_REPORTED
    assert usage.total_tokens == 20
    assert usage.thinking_included is False
    assert usage.diagnostics["totalTokenCount"] == 20


def test_thinking_token_guard_is_present_when_thoughts_are_absent():
    usage = parse_gemini_usage({
        "promptTokenCount": 2,
        "candidatesTokenCount": 3,
        "totalTokenCount": 5,
    })

    assert usage.token_status is LLMTokenStatus.PROVIDER_REPORTED
    assert usage.total_tokens == 5
    assert usage.thinking_included is False
    assert "thoughtsTokenCount" in usage.diagnostics


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        (lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError("u", 401, "auth", {}, None)), LLMProviderStatus.AUTH_ERROR),
        (lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError("u", 403, "auth", {}, None)), LLMProviderStatus.AUTH_ERROR),
        (lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError("u", 429, "rate", {}, None)), LLMProviderStatus.RATE_LIMITED),
        (lambda *_: (_ for _ in ()).throw(socket.timeout("slow")), LLMProviderStatus.TIMEOUT),
        (lambda *_: {"promptFeedback": {"blockReason": "SAFETY"}}, LLMProviderStatus.SAFETY_BLOCKED),
    ],
)
def test_gemini_provider_failures_map_to_typed_statuses(transport, expected):
    provider = GeminiProvider(api_key="test-key", transport=transport)

    result = provider.complete("synthetic prompt")

    assert result.status is expected
    assert result.response_text == ""
    assert result.usage.token_status is LLMTokenStatus.UNAVAILABLE


def test_free_tier_privacy_guard_fails_closed_without_safe_context():
    gateway = _gateway_with_response(_gemini_response(), tier="free")
    backend = LLMBackend(default_model=DEFAULT_GEMINI_MODEL, gateway=gateway)

    result = backend.complete_result("synthetic prompt without context")

    assert result.status is LLMProviderStatus.PROVIDER_ERROR
    assert result.error_message == "privacy_free_tier_context_required"


def test_free_tier_privacy_guard_allows_explicit_synthetic_public_context():
    gateway = _gateway_with_response(_gemini_response(), tier="free")
    backend = LLMBackend(default_model=DEFAULT_GEMINI_MODEL, gateway=gateway)

    result = backend.complete_result(
        "synthetic prompt",
        privacy_context=PrivacyContext(
            data_classification="synthetic",
            repository_visibility="public",
            contains_secrets=False,
            contains_personal_data=False,
        ),
    )

    assert result.status is LLMProviderStatus.COMPLETED
    assert result.usage.total_tokens == 12


def test_free_tier_rejects_private_content_region_and_paid_only_model():
    private_gateway = _gateway_with_response(_gemini_response(), tier="free")
    private_result = private_gateway.complete(
        "private repo diff",
        privacy_context=PrivacyContext(
            data_classification="private",
            repository_visibility="private",
            contains_secrets=False,
            contains_personal_data=False,
        ),
    )
    assert private_result.status is LLMProviderStatus.PROVIDER_ERROR
    assert private_result.error_message == "privacy_free_tier_private_or_unknown_content"

    region_gateway = LLMGateway(
        LLMGatewayConfig(provider="gemini", model=DEFAULT_GEMINI_MODEL, api_key="test-key", tier="free", user_region="DE"),
        provider_instance=_gateway_with_response(_gemini_response(), tier="free")._provider_instance,
    )
    region_result = region_gateway.complete(
        "synthetic prompt",
        privacy_context=PrivacyContext(
            data_classification="synthetic",
            repository_visibility="public",
            contains_secrets=False,
            contains_personal_data=False,
        ),
    )
    assert region_result.status is LLMProviderStatus.PROVIDER_ERROR
    assert region_result.error_message == "privacy_free_tier_region_requires_paid"

    model_gateway = LLMGateway(LLMGatewayConfig(provider="gemini", model="gemini-3.1-pro", api_key="test-key", tier="free"))
    model_result = model_gateway.complete(
        "synthetic prompt",
        privacy_context=PrivacyContext(
            data_classification="synthetic",
            repository_visibility="public",
            contains_secrets=False,
            contains_personal_data=False,
        ),
    )
    assert model_result.status is LLMProviderStatus.PROVIDER_ERROR
    assert "not eligible" in model_result.error_message


def test_cache_only_replay_backend_never_uses_env_provider_dispatch(monkeypatch):
    monkeypatch.setenv("SYNAPSE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    prompt = "cached prompt"
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    backend = CacheOnlyLLMBackend({
        f"{prompt_hash}:mock": {
            "prompt_hash": prompt_hash,
            "model": "mock",
            "result": "cached result",
        }
    })

    assert backend.complete(prompt, model="mock") == "cached result"
    assert backend.gateway is None
    assert backend.gateway_config.provider == "mock"
    assert backend.provider_call_count == 0
    with pytest.raises(DeterministicReplayError):
        backend.complete("missing prompt", model="mock")
