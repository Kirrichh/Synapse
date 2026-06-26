"""Typed result and usage models for the product LLM gateway."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional


class LLMProviderStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_ERROR = "AUTH_ERROR"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    TIMEOUT = "TIMEOUT"


class LLMTokenStatus(str, Enum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class LLMUsage:
    token_status: LLMTokenStatus
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    thinking_included: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_status": self.token_status.value,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "thinking_included": self.thinking_included,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class LLMResult:
    status: LLMProviderStatus
    provider: str
    model: str
    response_text: str
    usage: LLMUsage
    error_message: Optional[str] = None
    raw_diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_diagnostics", MappingProxyType(dict(self.raw_diagnostics)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "provider": self.provider,
            "model": self.model,
            "response_text": self.response_text,
            "usage": self.usage.to_dict(),
            "error_message": self.error_message,
            "raw_diagnostics": dict(self.raw_diagnostics),
        }
