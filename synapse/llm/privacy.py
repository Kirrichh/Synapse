"""Privacy and free-tier enforcement for product LLM calls."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


class PrivacyPolicyError(ValueError):
    """Raised when a prompt is not eligible for the selected provider tier."""


EEA_CH_UK_REGIONS = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH", "UK", "GB",
}

SAFE_FREE_TIER_CLASSIFICATIONS = {"synthetic", "public", "open_source"}
PRIVATE_CLASSIFICATIONS = {"private", "confidential", "secret", "personal", "unknown"}

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*[A-Za-z0-9_./+=-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-[0-9A-Za-z]{20,}"),
)


@dataclass(frozen=True)
class PrivacyContext:
    data_classification: str = "unknown"
    repository_visibility: str = "unknown"
    contains_secrets: Optional[bool] = None
    contains_personal_data: Optional[bool] = None
    user_region: Optional[str] = None
    policy_approved_private_code: bool = False


def prompt_has_secret_like_content(prompt: str) -> bool:
    text = str(prompt)
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def enforce_privacy_for_tier(prompt: str, *, tier: str, context: PrivacyContext | None) -> None:
    """Fail closed for free/unpaid provider mode unless content is proven safe."""

    normalized_tier = (tier or "paid").strip().lower()
    if normalized_tier not in {"free", "unpaid"}:
        return
    if context is None:
        raise PrivacyPolicyError("privacy_free_tier_context_required")

    region = (context.user_region or "").strip().upper()
    if region in EEA_CH_UK_REGIONS:
        raise PrivacyPolicyError("privacy_free_tier_region_requires_paid")

    classification = (context.data_classification or "unknown").strip().lower()
    visibility = (context.repository_visibility or "unknown").strip().lower()
    if classification in PRIVATE_CLASSIFICATIONS or classification not in SAFE_FREE_TIER_CLASSIFICATIONS:
        raise PrivacyPolicyError("privacy_free_tier_private_or_unknown_content")
    if visibility in {"private", "closed", "unknown"}:
        raise PrivacyPolicyError("privacy_free_tier_private_repository")
    if context.contains_secrets is not False:
        raise PrivacyPolicyError("privacy_free_tier_secret_state_unknown_or_present")
    if context.contains_personal_data is not False:
        raise PrivacyPolicyError("privacy_free_tier_personal_data_unknown_or_present")
    if prompt_has_secret_like_content(prompt):
        raise PrivacyPolicyError("privacy_free_tier_secret_like_prompt")
