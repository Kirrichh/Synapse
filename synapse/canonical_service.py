"""Stable canonical integration service and migration-drift analysis.

Alpha3g P0.4.6 / SI3 introduces this module as an anti-corruption layer
between the approved ``stable-canonical.v1`` value core and future runtime
consumers such as StateOverlay, Integrate, Dream state-delta, and actor IDs.

The service intentionally does **not** import or modify ``state_overlay.py``,
``interpreter.py``, CVM/opcodes, actor runtime, golden replay helpers, or any
existing Alpha3g hash path. Its only runtime dependency is the standalone
``synapse.canonical_values`` module.

Two profiles are represented here:

* ``stable-canonical.v1`` delegates to ``synapse.canonical_values``.
* ``alpha3g.local-json.v1`` is a small, isolated compatibility hasher used only
  for migration analysis. It mirrors the current StateOverlay local JSON subset
  without importing StateOverlay.

No migration is performed by this module. ``compare_profile_hashes`` measures
whether a value would drift between the legacy local profile and the stable
profile so future migration patches can make explicit, testable decisions.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from synapse.canonical_values import (
    CanonicalSerializationError,
    PROFILE_ID as STABLE_CANONICAL_PROFILE,
    canonical_bytes,
    stable_canonical_hash as _stable_canonical_hash,
)

ALPHA3G_LOCAL_JSON_PROFILE = "alpha3g.local-json.v1"
SUPPORTED_PROFILES = (STABLE_CANONICAL_PROFILE, ALPHA3G_LOCAL_JSON_PROFILE)


class CanonicalProfileError(RuntimeError):
    """Raised when an unsupported canonical profile is requested."""


class DriftCategory(str, Enum):
    """Machine-readable migration drift categories used by SI3 analysis."""

    NONE = "none"
    LOCAL_TYPE_REJECTION = "local_type_rejection"
    STABLE_TYPE_REJECTION = "stable_type_rejection"
    BOTH_REJECTED = "both_rejected"
    FLOAT_NORMALIZATION = "float_normalization"
    LARGE_INT_WRAPPER = "large_int_wrapper"
    BYTES_WRAPPER = "bytes_wrapper"
    SET_ORDERING = "set_ordering"
    KEY_NORMALIZATION = "key_normalization"
    VALUE_NORMALIZATION = "value_normalization"
    HASH_DRIFT = "hash_drift"


@dataclass(frozen=True)
class ProfileHashComparison:
    """Result of comparing Alpha3g local and stable canonical hashes."""

    local_hash: str | None
    stable_hash: str | None
    drift_detected: bool
    drift_category: str
    local_profile: str = ALPHA3G_LOCAL_JSON_PROFILE
    stable_profile: str = STABLE_CANONICAL_PROFILE
    local_error: str | None = None
    stable_error: str | None = None


def stable_canonical_bytes(value: Any, *, profile: str = STABLE_CANONICAL_PROFILE) -> bytes:
    """Return canonical bytes for the requested stable profile.

    SI3 supports ``stable-canonical.v1`` only. The explicit ``profile`` argument
    exists so future applier registries and migration tools do not rely on an
    implicit global default.
    """

    _require_profile(profile, STABLE_CANONICAL_PROFILE)
    return canonical_bytes(value).data


def stable_canonical_hash(value: Any, *, profile: str = STABLE_CANONICAL_PROFILE) -> str:
    """Return ``sha256:<hex>`` for the requested stable profile."""

    _require_profile(profile, STABLE_CANONICAL_PROFILE)
    return _stable_canonical_hash(value)


def alpha3g_local_json_hash(value: Any, *, profile: str = ALPHA3G_LOCAL_JSON_PROFILE) -> str:
    """Return the isolated Alpha3g local JSON compatibility hash.

    This function exists for drift analysis only. It must not become a new
    production consumer path and it intentionally mirrors the legacy local JSON
    subset instead of importing ``StateOverlay``.
    """

    _require_profile(profile, ALPHA3G_LOCAL_JSON_PROFILE)
    payload = _alpha3g_local_json_bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def compare_profile_hashes(value: Any) -> ProfileHashComparison:
    """Compare local Alpha3g and stable canonical hashes for ``value``.

    The result is intentionally descriptive rather than corrective. No migration,
    conversion, or fallback occurs here. Future SI4 migration patches can use the
    category to decide whether a value is compatible, needs explicit migration,
    or must remain under its recorded legacy profile.
    """

    local_hash: str | None = None
    stable_hash: str | None = None
    local_error: str | None = None
    stable_error: str | None = None

    try:
        local_hash = alpha3g_local_json_hash(value)
    except Exception as exc:  # intentionally captured for comparison report only
        local_error = f"{type(exc).__name__}: {exc}"

    try:
        stable_hash = stable_canonical_hash(value)
    except Exception as exc:  # intentionally captured for comparison report only
        stable_error = f"{type(exc).__name__}: {exc}"

    category = _classify_drift(value, local_hash, stable_hash, local_error, stable_error)
    return ProfileHashComparison(
        local_hash=local_hash,
        stable_hash=stable_hash,
        drift_detected=category != DriftCategory.NONE.value,
        drift_category=category,
        local_error=local_error,
        stable_error=stable_error,
    )


def _require_profile(requested: str, expected: str) -> None:
    if requested != expected:
        raise CanonicalProfileError(f"unsupported canonical profile: {requested!r}; expected {expected!r}")


def _alpha3g_local_json_bytes(value: Any) -> bytes:
    canonical = _alpha3g_local_canonicalize(value)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def _alpha3g_local_canonicalize(value: Any) -> Any:
    """Mirror the legacy StateOverlay local JSON subset without importing it."""

    if callable(value):
        raise CanonicalSerializationError("functions/callables are forbidden in alpha3g.local-json.v1 values")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalSerializationError("NaN and Infinity are not canonical JSON values")
        return value
    if isinstance(value, list):
        return [_alpha3g_local_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_alpha3g_local_canonicalize(item) for item in value]
    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError("dict keys in alpha3g.local-json.v1 values must be strings")
            canonical[key] = _alpha3g_local_canonicalize(item)
        return canonical
    raise CanonicalSerializationError(f"unsupported alpha3g.local-json.v1 value type: {type(value).__name__}")


def _classify_drift(
    value: Any,
    local_hash: str | None,
    stable_hash: str | None,
    local_error: str | None,
    stable_error: str | None,
) -> str:
    if local_error and stable_error:
        return DriftCategory.BOTH_REJECTED.value
    if local_error and stable_hash is not None:
        if _contains_set(value):
            return DriftCategory.SET_ORDERING.value
        if _contains_bytes(value):
            return DriftCategory.BYTES_WRAPPER.value
        return DriftCategory.LOCAL_TYPE_REJECTION.value
    if stable_error and local_hash is not None:
        return DriftCategory.STABLE_TYPE_REJECTION.value
    if local_hash == stable_hash:
        return DriftCategory.NONE.value
    if _contains_float_negative_zero(value):
        return DriftCategory.FLOAT_NORMALIZATION.value
    if _contains_large_int(value):
        return DriftCategory.LARGE_INT_WRAPPER.value
    if _contains_key_normalization(value):
        return DriftCategory.KEY_NORMALIZATION.value
    if _contains_value_normalization(value):
        return DriftCategory.VALUE_NORMALIZATION.value
    return DriftCategory.HASH_DRIFT.value


def _walk(value: Any) -> Any:
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _walk(item)


def _contains_set(value: Any) -> bool:
    return any(isinstance(item, (set, frozenset)) for item in _walk(value))


def _contains_bytes(value: Any) -> bool:
    return any(isinstance(item, (bytes, bytearray, memoryview)) for item in _walk(value))


def _contains_large_int(value: Any) -> bool:
    # Import lazily by value to keep this service independent from implementation details.
    from synapse.canonical_values import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN

    return any(isinstance(item, int) and not isinstance(item, bool) and not (SAFE_INTEGER_MIN <= item <= SAFE_INTEGER_MAX) for item in _walk(value))


def _contains_float_negative_zero(value: Any) -> bool:
    return any(isinstance(item, float) and item == 0.0 and math.copysign(1.0, item) < 0 for item in _walk(value))


def _contains_key_normalization(value: Any) -> bool:
    import unicodedata

    if isinstance(value, dict):
        seen: set[str] = set()
        for key, item in value.items():
            if isinstance(key, str):
                normalized = unicodedata.normalize("NFC", key)
                if normalized != key or normalized in seen:
                    return True
                seen.add(normalized)
            if _contains_key_normalization(item):
                return True
    elif isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_key_normalization(item) for item in value)
    return False


def _contains_value_normalization(value: Any) -> bool:
    import unicodedata

    for item in _walk(value):
        if isinstance(item, str) and unicodedata.normalize("NFC", item) != item:
            return True
    return False


__all__ = [
    "ALPHA3G_LOCAL_JSON_PROFILE",
    "DriftCategory",
    "CanonicalProfileError",
    "ProfileHashComparison",
    "STABLE_CANONICAL_PROFILE",
    "SUPPORTED_PROFILES",
    "alpha3g_local_json_hash",
    "compare_profile_hashes",
    "stable_canonical_bytes",
    "stable_canonical_hash",
]
