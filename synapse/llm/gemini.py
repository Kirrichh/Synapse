"""Gemini provider implementation for the Synapse product LLM gateway."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional

from .models import LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage


GEMINI_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


def is_free_tier_eligible_model(model: str) -> bool:
    normalized = (model or "").lower()
    return (
        normalized.startswith("gemini-")
        and ("flash" in normalized or "flash-lite" in normalized)
        and "pro" not in normalized
    )


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def parse_gemini_usage(usage_metadata: Mapping[str, Any] | None) -> LLMUsage:
    metadata = dict(usage_metadata or {})
    input_tokens = _int_or_none(metadata.get("promptTokenCount"))
    output_tokens = _int_or_none(metadata.get("candidatesTokenCount"))
    thought_tokens = _int_or_none(metadata.get("thoughtsTokenCount"))
    total_tokens = _int_or_none(metadata.get("totalTokenCount"))

    diagnostics = {
        "promptTokenCount": input_tokens,
        "candidatesTokenCount": output_tokens,
        "thoughtsTokenCount": thought_tokens,
        "totalTokenCount": total_tokens,
    }
    if total_tokens is None:
        return LLMUsage(
            token_status=LLMTokenStatus.UNAVAILABLE,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=None,
            thinking_included=False,
            diagnostics=diagnostics,
        )

    components = [value for value in (input_tokens, output_tokens, thought_tokens) if value is not None]
    thinking_included = False if thought_tokens is None else total_tokens >= sum(components)
    return LLMUsage(
        token_status=LLMTokenStatus.PROVIDER_REPORTED,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        thinking_included=thinking_included,
        diagnostics=diagnostics,
    )


ProviderTransport = Callable[[str, Mapping[str, Any], float, Mapping[str, str]], Mapping[str, Any]]


def _default_http_post(
    url: str,
    payload: Mapping[str, Any],
    timeout: float,
    headers: Mapping[str, str],
) -> Mapping[str, Any]:
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json", **dict(headers)},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _error_result(status: LLMProviderStatus, *, model: str, message: str) -> LLMResult:
    return LLMResult(
        status=status,
        provider=GEMINI_PROVIDER,
        model=model,
        response_text="",
        usage=LLMUsage(
            token_status=LLMTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            thinking_included=False,
            diagnostics={},
        ),
        error_message=message,
    )


class GeminiProvider:
    """Small stdlib Gemini client. API keys are passed only via headers."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        endpoint_base: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 30.0,
        transport: ProviderTransport | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.endpoint_base = endpoint_base.rstrip("/")
        self.timeout = timeout
        self.transport = transport or _default_http_post

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 100,
    ) -> LLMResult:
        selected_model = model or self.model
        payload = {
            "contents": [{"role": "user", "parts": [{"text": str(prompt)}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        url = f"{self.endpoint_base}/models/{selected_model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        try:
            response = self.transport(url, payload, self.timeout, headers)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                return _error_result(LLMProviderStatus.AUTH_ERROR, model=selected_model, message=f"Gemini auth failure HTTP {exc.code}")
            if exc.code == 429:
                return _error_result(LLMProviderStatus.RATE_LIMITED, model=selected_model, message="Gemini rate limit HTTP 429")
            return _error_result(LLMProviderStatus.PROVIDER_ERROR, model=selected_model, message=f"Gemini provider HTTP {exc.code}")
        except (TimeoutError, socket.timeout):
            return _error_result(LLMProviderStatus.TIMEOUT, model=selected_model, message="Gemini provider timeout")
        except Exception as exc:
            return _error_result(LLMProviderStatus.PROVIDER_ERROR, model=selected_model, message=f"Gemini provider error: {type(exc).__name__}")

        prompt_feedback = response.get("promptFeedback") if isinstance(response, Mapping) else None
        if isinstance(prompt_feedback, Mapping) and prompt_feedback.get("blockReason"):
            return _error_result(
                LLMProviderStatus.SAFETY_BLOCKED,
                model=selected_model,
                message=f"Gemini safety block: {prompt_feedback.get('blockReason')}",
            )

        candidates = response.get("candidates", []) if isinstance(response, Mapping) else []
        text_parts: list[str] = []
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, Mapping):
                continue
            finish_reason = candidate.get("finishReason")
            if finish_reason in {"SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
                return _error_result(LLMProviderStatus.SAFETY_BLOCKED, model=selected_model, message=f"Gemini safety block: {finish_reason}")
            content = candidate.get("content", {})
            parts = content.get("parts", []) if isinstance(content, Mapping) else []
            for part in parts if isinstance(parts, list) else []:
                if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])

        usage = parse_gemini_usage(response.get("usageMetadata") if isinstance(response, Mapping) else None)
        return LLMResult(
            status=LLMProviderStatus.COMPLETED,
            provider=GEMINI_PROVIDER,
            model=selected_model,
            response_text="".join(text_parts),
            usage=usage,
            raw_diagnostics={"usageMetadata": usage.to_dict()["diagnostics"]},
        )
