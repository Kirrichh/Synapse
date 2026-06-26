"""Product LLM gateway configuration and dispatch."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .gemini import DEFAULT_GEMINI_MODEL, GEMINI_PROVIDER, GeminiProvider, is_free_tier_eligible_model
from .models import LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage
from .privacy import PrivacyContext, PrivacyPolicyError, enforce_privacy_for_tier


@dataclass(frozen=True)
class LLMGatewayConfig:
    provider: str = "mock"
    model: str = DEFAULT_GEMINI_MODEL
    api_key: Optional[str] = None
    tier: str = "paid"
    user_region: Optional[str] = None
    timeout: float = 30.0

    @property
    def real_provider_enabled(self) -> bool:
        return self.provider.lower() == GEMINI_PROVIDER


def _env_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def config_from_env(
    *,
    default_model: str = "mock",
    provider: str | None = None,
    mode: str | None = None,
    api_key: str | None = None,
    tier: str | None = None,
    user_region: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMGatewayConfig:
    env = environ or os.environ
    env_mode = (mode or env.get("SYNAPSE_LLM_MODE") or "").strip().lower()
    env_provider = (provider or env.get("SYNAPSE_LLM_PROVIDER") or "").strip().lower()
    if env_mode in {"real", "provider"} and not env_provider:
        env_provider = GEMINI_PROVIDER
    if not env_provider:
        env_provider = "mock"

    resolved_model = env.get("SYNAPSE_LLM_MODEL") or default_model
    if not resolved_model or resolved_model == "mock":
        resolved_model = DEFAULT_GEMINI_MODEL if env_provider == GEMINI_PROVIDER else "mock"

    resolved_tier = (
        tier
        or env.get("SYNAPSE_LLM_TIER")
        or ("free" if _env_bool(env.get("SYNAPSE_LLM_FREE_TIER")) else "paid")
    ).strip().lower()
    resolved_key = api_key or env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
    timeout_value = env.get("SYNAPSE_LLM_TIMEOUT_SECONDS")
    try:
        timeout = float(timeout_value) if timeout_value else 30.0
    except ValueError:
        timeout = 30.0

    return LLMGatewayConfig(
        provider=env_provider,
        model=resolved_model,
        api_key=resolved_key,
        tier=resolved_tier,
        user_region=user_region or env.get("SYNAPSE_LLM_USER_REGION"),
        timeout=timeout,
    )


def _failure(status: LLMProviderStatus, *, provider: str, model: str, message: str) -> LLMResult:
    return LLMResult(
        status=status,
        provider=provider,
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


class LLMGateway:
    """Single product dispatch point for real LLM providers."""

    def __init__(self, config: LLMGatewayConfig, *, provider_instance: Any | None = None):
        self.config = config
        self._provider_instance = provider_instance

    def _gemini_provider(self) -> GeminiProvider:
        if self._provider_instance is not None:
            return self._provider_instance
        assert self.config.api_key is not None
        return GeminiProvider(
            api_key=self.config.api_key,
            model=self.config.model,
            timeout=self.config.timeout,
        )

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 100,
        privacy_context: PrivacyContext | None = None,
    ) -> LLMResult:
        if not self.config.real_provider_enabled:
            return _failure(
                LLMProviderStatus.PROVIDER_ERROR,
                provider=self.config.provider,
                model=model or self.config.model,
                message="real provider is not enabled",
            )
        if self.config.provider != GEMINI_PROVIDER:
            return _failure(
                LLMProviderStatus.PROVIDER_ERROR,
                provider=self.config.provider,
                model=model or self.config.model,
                message="unsupported llm provider",
            )
        selected_model = model or self.config.model
        if self.config.tier in {"free", "unpaid"} and not is_free_tier_eligible_model(selected_model):
            return _failure(
                LLMProviderStatus.PROVIDER_ERROR,
                provider=GEMINI_PROVIDER,
                model=selected_model,
                message="configured model is not eligible for free-tier mode",
            )
        if not self.config.api_key:
            return _failure(
                LLMProviderStatus.AUTH_ERROR,
                provider=GEMINI_PROVIDER,
                model=selected_model,
                message="GEMINI_API_KEY not provided",
            )
        try:
            context = privacy_context
            if context is not None and context.user_region is None and self.config.user_region is not None:
                context = PrivacyContext(
                    data_classification=context.data_classification,
                    repository_visibility=context.repository_visibility,
                    contains_secrets=context.contains_secrets,
                    contains_personal_data=context.contains_personal_data,
                    user_region=self.config.user_region,
                    policy_approved_private_code=context.policy_approved_private_code,
                )
            enforce_privacy_for_tier(prompt, tier=self.config.tier, context=context)
        except PrivacyPolicyError as exc:
            return _failure(
                LLMProviderStatus.PROVIDER_ERROR,
                provider=GEMINI_PROVIDER,
                model=selected_model,
                message=str(exc),
            )
        return self._gemini_provider().complete(
            prompt,
            model=selected_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )


def build_gateway_from_env(**overrides: Any) -> LLMGateway:
    return LLMGateway(config_from_env(**overrides))
