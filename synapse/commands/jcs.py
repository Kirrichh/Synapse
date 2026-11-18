"""JCS command values, with precision checked before a command can be bound.

This codec belongs to new application commands, not historical evidence records or
the integer-only control profile. Decimal input must survive binary64 parsing
and ECMAScript serialization with the same decimal value. Greater precision
must be expressed as a schema-defined string, never silently rounded.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

import rfc8785

MAX_BYTES = 65536
MAX_DEPTH = 32


class InvalidCommandValue(ValueError):
    """A bounded, nonsecret diagnostic for invalid command content."""

    def __init__(self) -> None:
        super().__init__("INVALID_COMMAND_VALUE")


def _pairs(items: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise InvalidCommandValue()
        result[key] = value
    return result


def _number(token: str) -> int | float:
    try:
        number = float(token)
        if not math.isfinite(number):
            raise InvalidCommandValue()
        encoded = rfc8785.dumps(number).decode("ascii")
        if Decimal(token) != Decimal(encoded):
            raise InvalidCommandValue()
        # Preserve interoperable integral schema fields as integers. Larger
        # binary64 values remain floats so the JCS serializer never coerces ints.
        if number.is_integer() and abs(number) <= 9007199254740991:
            return int(number)
        return number
    except (ValueError, OverflowError, InvalidOperation):
        raise InvalidCommandValue() from None


def _constant(_: str) -> None:
    raise InvalidCommandValue()


def decode(payload: bytes) -> object:
    if type(payload) is not bytes or not 0 < len(payload) <= MAX_BYTES:
        raise InvalidCommandValue()
    try:
        text = payload.decode("utf-8", errors="strict")
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
                if depth > MAX_DEPTH:
                    raise InvalidCommandValue()
            elif char in "]}":
                depth -= 1
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_number,
                           parse_float=_number, parse_constant=_constant)
        canonical_bytes(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidCommandValue() from None


def canonical_bytes(value: object) -> bytes:
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if type(item) in (dict, list):
            if depth >= MAX_DEPTH:
                raise InvalidCommandValue()
            if type(item) is dict:
                for key in item:
                    if type(key) is not str:
                        raise InvalidCommandValue()
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            if any(0xD800 <= ord(char) <= 0xDFFF for char in item):
                raise InvalidCommandValue()
        elif type(item) is int:
            if abs(item) > 9007199254740991:
                # An in-memory integer has exact semantics. Never round it to
                # fit JCS's floating point representation.
                raise InvalidCommandValue()
        elif type(item) is float:
            if not math.isfinite(item):
                raise InvalidCommandValue()
        elif item is not None and type(item) is not bool:
            raise InvalidCommandValue()
    try:
        payload = rfc8785.dumps(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise InvalidCommandValue() from None
    if len(payload) > MAX_BYTES:
        raise InvalidCommandValue()
    return payload


def parameter_hash(params: dict) -> str:
    if type(params) is not dict:
        raise InvalidCommandValue()
    return hashlib.sha256(canonical_bytes(params)).hexdigest()
