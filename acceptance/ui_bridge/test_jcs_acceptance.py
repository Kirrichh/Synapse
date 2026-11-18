"""Command hashing observes bytes and refusals, not old evidence identities."""
import hashlib
import json
from pathlib import Path

import pytest

from synapse.commands import jcs
from synapse.machine.codec import decode as control_decode
from synapse.machine.contract import ProtocolError

CASES = json.loads(Path(__file__).with_name("jcs-corpus.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case"])
def test_shared_command_values(case):
    raw = case["raw"].encode("utf-8")
    if "canonical" not in case:
        with pytest.raises(jcs.InvalidCommandValue, match="^INVALID_COMMAND_VALUE$"):
            jcs.decode(raw)
        return
    value = jcs.decode(raw)
    canonical = case["canonical"].encode("utf-8")
    assert jcs.canonical_bytes(value) == canonical
    assert jcs.canonical_bytes(jcs.decode(canonical)) == canonical
    if isinstance(value, dict):
        assert jcs.parameter_hash(value) == hashlib.sha256(canonical).hexdigest()


def test_parameter_identity_ignores_spelling_but_preserves_significance():
    left = jcs.decode(b'{"a":1.0,"b":[1,2]}')
    right = jcs.decode(b'{"b":[1,2],"a":1e0}')
    assert jcs.parameter_hash(left) == jcs.parameter_hash(right)
    assert jcs.parameter_hash(left) != jcs.parameter_hash({"a": 1, "b": [2, 1]})
    assert jcs.parameter_hash({"text": "e\u0301"}) != jcs.parameter_hash({"text": "\u00e9"})


@pytest.mark.parametrize("value", [9007199254740993, 9007199254740992, float("inf"),
    float("nan"), {1: "value"}, (1,), "\ud800", b"secret"])
def test_in_memory_values_never_coerce_or_disclose(value):
    with pytest.raises(jcs.InvalidCommandValue) as error:
        jcs.canonical_bytes(value)
    assert str(error.value) == "INVALID_COMMAND_VALUE"


def test_input_output_bounds_and_control_profile_are_independent():
    for raw in (b"", b'"\xff"', b" " * 65537,
                b"[" * 33 + b"0" + b"]" * 33):
        with pytest.raises(jcs.InvalidCommandValue):
            jcs.decode(raw)
    raw = b"[" * 32 + b"0" + b"]" * 32
    assert jcs.canonical_bytes(jcs.decode(raw)) == raw
    with pytest.raises(jcs.InvalidCommandValue):
        jcs.canonical_bytes("x" * 65536)
    with pytest.raises(jcs.InvalidCommandValue):
        jcs.parameter_hash([])
    for raw in (b"-0", b"1.0", b"1e0"):
        with pytest.raises(ProtocolError):
            control_decode(raw)
        assert jcs.decode(raw) in (0, 1)
