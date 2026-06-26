"""Product LLM gateway boundary for Synapse."""

from .gateway import LLMGateway, LLMGatewayConfig, build_gateway_from_env
from .models import LLMProviderStatus, LLMResult, LLMTokenStatus, LLMUsage
from .privacy import PrivacyContext, PrivacyPolicyError

__all__ = [
    "LLMGateway",
    "LLMGatewayConfig",
    "LLMProviderStatus",
    "LLMResult",
    "LLMTokenStatus",
    "LLMUsage",
    "PrivacyContext",
    "PrivacyPolicyError",
    "build_gateway_from_env",
]
