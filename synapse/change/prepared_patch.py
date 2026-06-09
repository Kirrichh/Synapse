"""Prepared-patch metadata for the current controlled-change acquisition path."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class PreparedPatchMetadata:
    provider: str = "prepared_patch"
    model: None = None
    input_tokens: None = None
    output_tokens: None = None
    token_status: str = "NOT_APPLICABLE_VARIANT_A"

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def prepared_patch_metadata() -> PreparedPatchMetadata:
    return PreparedPatchMetadata()
