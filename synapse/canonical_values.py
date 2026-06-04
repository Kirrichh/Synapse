"""Stable Canonical Identity value serialization core (Alpha3g SI1/SI2).

This module implements the standalone ``stable-canonical.v1`` value profile
approved by ``docs/RFC-STABLE-CANONICAL-IDENTITY.md``. It intentionally does
not integrate with ``interpreter.py``, ``state_overlay.py``, CVM, actor runtime,
canonical time, deterministic identity generation, FunctionDescriptor, or agent
snapshots in SI1/SI2.

Forensic invariants:
* all text and dict keys are Unicode NFC-normalized before serialization;
* lone surrogate code points are rejected before UTF-8 emission;
* ``bytes`` use RFC 4648 section 5 base64url without padding;
* ``set`` / ``frozenset`` values are sorted by canonical JSON bytes, not by
  Python ``repr`` or host hash order;
* unsupported or excessively deep object graphs fail closed with
  ``CanonicalSerializationError``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Set as AbstractSet
from dataclasses import dataclass
from typing import Any

PROFILE_ID = "stable-canonical.v1"
PROFILE_VERSION = "v1"
MAX_NESTING_DEPTH = 128
SAFE_INTEGER_MIN = -(2**53 - 1)
SAFE_INTEGER_MAX = 2**53 - 1


class CanonicalValueError(RuntimeError):
    """Base error for stable canonical value serialization failures."""


class CanonicalSerializationError(CanonicalValueError, TypeError):
    """Raised when a value is outside the stable-canonical.v1 allowlist."""


@dataclass(frozen=True)
class CanonicalBytes:
    """Canonical byte payload with the profile that produced it."""

    profile: str
    data: bytes

    def sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.data).hexdigest()


def _has_lone_surrogate(text: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def _canonical_text(text: str, *, path: str) -> str:
    if _has_lone_surrogate(text):
        raise CanonicalSerializationError(f"lone surrogate in canonical string at {path}")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:  # defensive; lone surrogates should catch this first
        raise CanonicalSerializationError(f"invalid Unicode scalar text at {path}") from exc
    return unicodedata.normalize("NFC", text)


def _base64url_nopad(data: bytes) -> str:
    """Return RFC 4648 section 5 base64url text without padding.

    Padding removal is part of the stable-canonical.v1 wire form. Callers that
    need to decode must restore padding according to standard base64url rules;
    this module is intentionally encode-only in SI1/SI2.
    """

    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _large_int_payload(value: int) -> dict[str, str]:
    # str(int) is canonical base-10 for Python ints: no + and no leading zeros.
    return {"__type__": "int", "value": str(value)}


def _bytes_payload(value: bytes | bytearray | memoryview) -> dict[str, str]:
    data = bytes(value)
    return {
        "__type__": "bytes",
        "encoding": "base64url-nopad",
        "data": _base64url_nopad(data),
    }


def _json_bytes(canonical_value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a canonical representation."""

    return json.dumps(
        canonical_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonicalize(value: Any) -> Any:
    """Return the stable-canonical.v1 JSON-compatible representation.

    The implementation is allowlist-based and fail-closed. Unsupported runtime
    values such as functions, closures, bound methods, host objects, agent
    instances, actor refs, promises, file handles, and arbitrary custom objects
    raise ``CanonicalSerializationError``. Object graphs deeper than
    ``MAX_NESTING_DEPTH`` are also rejected so canonicalization fails with a
    stable project error instead of a host-specific ``RecursionError``.
    """

    return _canonicalize(value, path="$", seen={}, depth=0)


def _canonicalize(value: Any, *, path: str, seen: dict[int, str], depth: int) -> Any:
    if depth > MAX_NESTING_DEPTH:
        raise CanonicalSerializationError(
            f"canonical value nesting exceeds limit {MAX_NESTING_DEPTH} at {path}"
        )
    # bool is a subclass of int, so it must be handled before int.
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, str):
        return _canonical_text(value, path=path)

    if isinstance(value, int):
        if SAFE_INTEGER_MIN <= value <= SAFE_INTEGER_MAX:
            return value
        return _large_int_payload(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalSerializationError(f"NaN and Infinity are forbidden at {path}")
        if value == 0.0:
            return 0.0
        # json.dumps(..., allow_nan=False) provides a deterministic finite
        # representation in supported Python versions. This is the SI1 runtime
        # core boundary; stricter decimal profiles are future work.
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        return _bytes_payload(value)

    if callable(value):
        raise CanonicalSerializationError(f"functions/callables are forbidden at {path}")

    if isinstance(value, (list, tuple)):
        return _canonicalize_sequence(value, path=path, seen=seen, depth=depth)

    if isinstance(value, Mapping):
        return _canonicalize_mapping(value, path=path, seen=seen, depth=depth)

    # Sets must be checked after Mapping/list so custom containers do not pass
    # accidentally. frozenset is also supported.
    if isinstance(value, (set, frozenset)) or (
        isinstance(value, AbstractSet) and not isinstance(value, (str, bytes, bytearray, memoryview))
    ):
        return _canonicalize_set(value, path=path, seen=seen, depth=depth)

    raise CanonicalSerializationError(f"unsupported canonical value type at {path}: {type(value).__name__}")


def _enter_container(value: Any, *, path: str, seen: dict[int, str]) -> int:
    object_id = id(value)
    if object_id in seen:
        raise CanonicalSerializationError(f"cycle detected at {path}; first seen at {seen[object_id]}")
    seen[object_id] = path
    return object_id


def _leave_container(object_id: int, seen: dict[int, str]) -> None:
    seen.pop(object_id, None)


def _canonicalize_sequence(value: Any, *, path: str, seen: dict[int, str], depth: int) -> list[Any]:
    object_id = _enter_container(value, path=path, seen=seen)
    try:
        return [_canonicalize(item, path=f"{path}[{index}]", seen=seen, depth=depth + 1) for index, item in enumerate(value)]
    finally:
        _leave_container(object_id, seen)


def _canonicalize_mapping(value: Mapping[Any, Any], *, path: str, seen: dict[int, str], depth: int) -> dict[str, Any]:
    object_id = _enter_container(value, path=path, seen=seen)
    try:
        canonical: dict[str, Any] = {}
        original_for_key: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalSerializationError(f"dict keys must be strings at {path}")
            canonical_key = _canonical_text(key, path=f"{path}.<key>")
            if canonical_key in canonical:
                previous = original_for_key[canonical_key]
                raise CanonicalSerializationError(
                    f"dict key collision after NFC normalization at {path}: {previous!r} and {key!r}"
                )
            original_for_key[canonical_key] = key
            canonical[canonical_key] = _canonicalize(item, path=f"{path}.{canonical_key}", seen=seen, depth=depth + 1)
        # Return a dict inserted in sorted order for deterministic display; JSON
        # emission also uses sort_keys=True as the source of truth.
        return {key: canonical[key] for key in sorted(canonical)}
    finally:
        _leave_container(object_id, seen)


def _canonicalize_set(value: Any, *, path: str, seen: dict[int, str], depth: int) -> dict[str, Any]:
    object_id = _enter_container(value, path=path, seen=seen)
    try:
        entries: list[tuple[bytes, Any]] = []
        for index, item in enumerate(value):
            canonical_item = _canonicalize(item, path=f"{path}.<set>[{index}]", seen=seen, depth=depth + 1)
            entries.append((_json_bytes(canonical_item), canonical_item))
        entries.sort(key=lambda pair: pair[0])
        return {"__type__": "set", "items": [item for _, item in entries]}
    finally:
        _leave_container(object_id, seen)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize ``value`` to stable-canonical.v1 JSON UTF-8 bytes.

    JSON emission uses ``sort_keys=True``, compact separators, ``allow_nan=False``,
    and ``ensure_ascii=False``. The canonicalizer normalizes/validates values
    before JSON encoding, so the bytes are suitable for cross-platform hashing.
    """

    return _json_bytes(canonicalize(value))


def canonical_bytes(value: Any) -> CanonicalBytes:
    """Return canonical bytes annotated with the stable profile id."""

    return CanonicalBytes(profile=PROFILE_ID, data=canonical_json_bytes(value))


def canonical_hash(value: Any) -> str:
    """Return ``sha256:<hex>`` over ``canonical_json_bytes(value)``.

    This is the stable-canonical.v1 value hash only. It is not a session identity,
    event hash-chain hash, canonical time source, or deterministic actor ID.
    """

    return canonical_bytes(value).sha256()


def stable_canonical_hash(value: Any) -> str:
    """Alias for ``canonical_hash`` with an explicit stable profile name."""

    return canonical_hash(value)


def is_canonical_serializable(value: Any) -> bool:
    """Return True when value is accepted by stable-canonical.v1."""

    try:
        canonicalize(value)
    except CanonicalSerializationError:
        return False
    return True


__all__ = [
    "CanonicalBytes",
    "CanonicalSerializationError",
    "CanonicalValueError",
    "PROFILE_ID",
    "PROFILE_VERSION",
    "MAX_NESTING_DEPTH",
    "SAFE_INTEGER_MAX",
    "SAFE_INTEGER_MIN",
    "canonical_bytes",
    "canonical_hash",
    "canonical_json_bytes",
    "canonicalize",
    "is_canonical_serializable",
    "stable_canonical_hash",
]
