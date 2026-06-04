"""Canonical integrate path parsing and memory-key encoding.

Alpha3g / P0.3.0 I1 implements only the standalone path grammar required by
``RFC-INTEGRATE-REPLAY-APPLIER``.  This module intentionally has no dependency
on the interpreter, actor runtime, CVM, or replay applier.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Final


_SAFE_KEY_CHARS: Final[set[int]] = set(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_HEX_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-F]{2}$")
_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SUPPORTED_NAMESPACES: Final[frozenset[str]] = frozenset({"env", "memory"})


class CanonicalPathError(ValueError):
    """Raised when an integrate path or key is not canonical."""


@dataclass(frozen=True, order=True)
class CanonicalPath:
    """Parsed canonical integrate path.

    ``namespace`` is currently ``env`` or ``memory``.  ``key`` is the decoded,
    NFC-normalized logical key.  ``path`` is the canonical encoded path string.
    """

    namespace: str
    key: str
    path: str

    @property
    def segments(self) -> tuple[str, str]:
        return (self.namespace, self.key)


def _validate_unicode_scalar_text(value: str, *, field: str = "value") -> str:
    if not isinstance(value, str):
        raise CanonicalPathError(f"{field} must be a str")
    for ch in value:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            raise CanonicalPathError(f"{field} contains a lone surrogate code point")
    return unicodedata.normalize("NFC", value)


def canonical_key_encode(key: str) -> str:
    """Encode a memory key using Alpha3g canonical percent encoding.

    Algorithm from RFC-INTEGRATE §4.2:
    Unicode scalar validation -> NFC normalization -> UTF-8 bytes -> literal
    ``[A-Za-z0-9_.-]`` -> uppercase ``%XX`` for every other byte.
    """

    normalized = _validate_unicode_scalar_text(key, field="memory key")
    out: list[str] = []
    for byte in normalized.encode("utf-8"):
        if byte in _SAFE_KEY_CHARS:
            out.append(chr(byte))
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def canonical_key_decode(encoded: str) -> str:
    """Decode and validate a canonical memory-key path segment.

    The function rejects lowercase percent escapes, malformed escapes, literal
    unsafe characters, invalid UTF-8, non-NFC decoded text, and non-canonical
    alternative encodings.
    """

    if not isinstance(encoded, str):
        raise CanonicalPathError("encoded memory key must be a str")

    data = bytearray()
    idx = 0
    while idx < len(encoded):
        ch = encoded[idx]
        byte = ord(ch)
        if ch == "%":
            pair = encoded[idx + 1 : idx + 3]
            if len(pair) != 2 or not _HEX_RE.match(pair):
                raise CanonicalPathError("invalid or non-uppercase percent escape")
            data.append(int(pair, 16))
            idx += 3
            continue
        if byte > 0x7F or byte not in _SAFE_KEY_CHARS:
            raise CanonicalPathError("memory key contains a non-canonical literal character")
        data.append(byte)
        idx += 1

    try:
        decoded = bytes(data).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CanonicalPathError("memory key is not valid UTF-8") from exc

    normalized = _validate_unicode_scalar_text(decoded, field="memory key")
    if normalized != decoded:
        raise CanonicalPathError("memory key is not NFC-normalized")
    if canonical_key_encode(decoded) != encoded:
        raise CanonicalPathError("memory key segment is not canonical")
    return decoded


def canonical_env_encode(name: str) -> str:
    """Validate and encode an environment binding name for ``/env/<name>``.

    Alpha3g v1 environment paths intentionally support only normal language
    identifiers.  This avoids importing memory-key percent encoding into env
    semantics by accident.
    """

    normalized = _validate_unicode_scalar_text(name, field="environment name")
    if normalized != name:
        raise CanonicalPathError("environment name must already be NFC-normalized")
    if not _ENV_NAME_RE.match(normalized):
        raise CanonicalPathError("environment name must be a Synapse identifier")
    return normalized


def make_env_path(name: str) -> str:
    return f"/env/{canonical_env_encode(name)}"


def make_memory_path(key: str) -> str:
    return f"/memory/{canonical_key_encode(key)}"


def parse_canonical_path(path: str) -> CanonicalPath:
    """Parse a canonical Alpha3g integrate path.

    Valid v1 write-set paths are exactly ``/env/<identifier>`` and
    ``/memory/<canonical_key>``.  Bare paths, unknown namespaces, extra path
    segments, ``/memory`` without the trailing key separator, and non-canonical
    encodings are rejected.
    """

    if not isinstance(path, str):
        raise CanonicalPathError("path must be a str")
    if not path.startswith("/"):
        raise CanonicalPathError("path must start with '/' and include a namespace")
    if path == "/":
        raise CanonicalPathError("path must include a namespace and key")
    if "//" in path:
        # ``/memory/`` is the only valid path with a trailing separator and an
        # empty user key; doubled separators elsewhere are ambiguous.
        raise CanonicalPathError("path contains an empty structural segment")

    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "":
        raise CanonicalPathError("path must have namespace and key segments")
    namespace = parts[1]
    if namespace not in _SUPPORTED_NAMESPACES:
        raise CanonicalPathError(f"unsupported namespace: {namespace!r}")
    if len(parts) != 3:
        raise CanonicalPathError("v1 paths must contain exactly one user-data segment")

    encoded_key = parts[2]
    if namespace == "env":
        if encoded_key == "":
            raise CanonicalPathError("/env/ requires a non-empty environment name")
        key = canonical_env_encode(encoded_key)
        canonical = make_env_path(key)
    elif namespace == "memory":
        # Empty key is valid only as the exact path ``/memory/``.
        key = canonical_key_decode(encoded_key)
        canonical = make_memory_path(key)
    else:  # pragma: no cover - namespace guard above is exhaustive in v1.
        raise CanonicalPathError(f"unsupported namespace: {namespace!r}")

    if canonical != path:
        raise CanonicalPathError("path is not in canonical form")
    return CanonicalPath(namespace=namespace, key=key, path=canonical)


def is_canonical_path(path: str) -> bool:
    try:
        parse_canonical_path(path)
    except CanonicalPathError:
        return False
    return True


__all__ = [
    "CanonicalPath",
    "CanonicalPathError",
    "canonical_key_encode",
    "canonical_key_decode",
    "canonical_env_encode",
    "make_env_path",
    "make_memory_path",
    "parse_canonical_path",
    "is_canonical_path",
]
