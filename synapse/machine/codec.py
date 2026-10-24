"""Bounded byte framing and strict JSON, independent of session semantics."""
from __future__ import annotations

import json
import struct
from typing import BinaryIO

from .contract import PROFILE, ProtocolError

SAFE_INTEGER = 9007199254740991


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ProtocolError("INVALID_JSON")
        result[key] = value
    return result


def _integer(token: str) -> int:
    value = int(token)
    if token == "-0" or abs(value) > SAFE_INTEGER:
        raise ProtocolError("INVALID_JSON")
    return value


def _constant(_: str) -> None:
    raise ProtocolError("INVALID_JSON")


def _depth(text: str) -> None:
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > PROFILE["max_json_depth"]:
                raise ProtocolError("JSON_LIMIT")
        elif char in "]}":
            depth -= 1


def decode(payload: bytes) -> object:
    if len(payload) > PROFILE["max_frame_bytes"]:
        raise ProtocolError("FRAME_TOO_LARGE")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ProtocolError("INVALID_UTF8") from None
    _depth(text)
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer,
                           parse_float=_constant, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise ProtocolError("INVALID_JSON") from None
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise ProtocolError("INVALID_JSON")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value


def _read_exact(stream: BinaryIO, length: int, *, header: bool = False) -> bytes | None:
    buffer = bytearray()
    while len(buffer) < length:
        part = stream.read(length - len(buffer))
        if not part:
            if header and not buffer:
                return None
            raise ProtocolError("TRUNCATED_FRAME")
        buffer.extend(part)
    return bytes(buffer)


def read_frame(stream: BinaryIO, limit: int) -> bytes | None:
    header = _read_exact(stream, 4, header=True)
    if header is None:
        return None
    length = struct.unpack(">I", header)[0]
    if length == 0:
        raise ProtocolError("INVALID_LENGTH")
    if length > limit:
        raise ProtocolError("FRAME_TOO_LARGE")
    return _read_exact(stream, length)


def encode(value: dict, limit: int) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    if not 0 < len(payload) <= limit:
        raise ProtocolError("FRAME_TOO_LARGE")
    return struct.pack(">I", len(payload)) + payload


def write_frame(stream: BinaryIO, frame: bytes) -> None:
    remaining = memoryview(frame)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise ProtocolError("PIPE_CLOSED")
        remaining = remaining[written:]
    stream.flush()
