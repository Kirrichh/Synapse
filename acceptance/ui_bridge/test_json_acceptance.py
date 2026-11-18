"""Strict input rejects ambiguity before schema or command evaluation."""
import pytest

from synapse.machine.codec import decode
from synapse.machine.contract import ProtocolError, validate
from machine_peer import hello, request


@pytest.mark.parametrize("raw,code", [
    (b'\xff', "INVALID_UTF8"), (b'{', "INVALID_JSON"),
    (b'{"a":1,"a":2}', "INVALID_JSON"),
    (b'{"a":{"b":1,"b":2}}', "INVALID_JSON"),
    (b'{"a":1,"\\u0061":2}', "INVALID_JSON"),
    (b'"\\ud800"', "INVALID_JSON"),
    (b'{"\\udfff":1}', "INVALID_JSON"),
    (b'NaN', "INVALID_JSON"), (b'Infinity', "INVALID_JSON"),
    (b'1e999', "INVALID_JSON"), (b'1.0', "INVALID_JSON"),
    (b'9007199254740992', "INVALID_JSON"),
    (b'-9007199254740992', "INVALID_JSON"),
    (b'[' * 33 + b'0' + b']' * 33, "JSON_LIMIT"),
])
def test_ambiguous_or_unbounded_json_is_refused(raw, code):
    with pytest.raises(ProtocolError, match=code):
        decode(raw)


def test_safe_integer_unicode_and_depth_boundaries():
    assert decode(b'9007199254740991') == 9007199254740991
    assert decode(b'-9007199254740991') == -9007199254740991
    assert decode(b'"\\ud83d\\ude00"') == "\U0001f600"
    assert decode(b'"[\\\"{\\\\]"') == '["{\\]'
    assert decode(b'[' * 32 + b'0' + b']' * 32) is not None


@pytest.mark.parametrize("value", [hello(extra="ignored"), hello(protocol_min="1"),
    hello(protocol_min=True), request(params=[]), request(request_id="x\nsecret"),
    request(request_id="x\n"), request(request_id=""), request(request_id="x" * 65),
    {"type": "hello", "schema_version": "1.0"}, [], None])
def test_schema_does_not_strip_coerce_or_fill(value):
    with pytest.raises(ProtocolError, match="SCHEMA_INVALID"):
        validate(value)


def test_unsupported_version_is_distinct():
    with pytest.raises(ProtocolError, match="SCHEMA_UNSUPPORTED"):
        validate(hello(schema_version="2.0"))


def test_decoding_cannot_bypass_the_release_payload_bound():
    with pytest.raises(ProtocolError, match="FRAME_TOO_LARGE"):
        decode(b'"' + b'x' * 65536 + b'"')


def test_unknown_exception_codes_cannot_become_diagnostic_content():
    error = ProtocolError("secret credential")
    assert error.code == "INTERNAL_ERROR"
    assert str(error) == "INTERNAL_ERROR"
