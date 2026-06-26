"""Manual synthetic smoke for the product LLM gateway."""
from __future__ import annotations

from synapse.builtins import LLMBackend

from .gemini import DEFAULT_GEMINI_MODEL
from .privacy import PrivacyContext


def main() -> int:
    backend = LLMBackend(default_model=DEFAULT_GEMINI_MODEL, provider="gemini")
    result = backend.complete_result(
        "Synthetic non-private smoke prompt: reply with one short sentence.",
        privacy_context=PrivacyContext(
            data_classification="synthetic",
            repository_visibility="public",
            contains_secrets=False,
            contains_personal_data=False,
        ),
        max_tokens=64,
    )
    print(f"status={result.status.value}")
    print(f"provider={result.provider}")
    print(f"model={result.model}")
    print(f"token_status={result.usage.token_status.value}")
    print(f"total_tokens={result.usage.total_tokens}")
    print(f"thinking_included={str(result.usage.thinking_included).lower()}")
    if result.error_message:
        print(f"error={result.error_message}")
    return 0 if result.status.value == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
