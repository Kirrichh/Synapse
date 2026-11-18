"""The same raw-message corpus is consumed by Python, Rust, and TypeScript."""
import json
from pathlib import Path

import pytest
from synapse.machine.codec import decode
from synapse.machine.contract import ProtocolError, validate

CORPUS = json.loads(Path(__file__).with_name("corpus.json").read_text())


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["behavior"])
def test_cross_language_wire_contract(case):
    if case["valid"]:
        assert validate(decode(case["raw"].encode())) == case["value"]
    else:
        with pytest.raises(ProtocolError):
            validate(decode(case["raw"].encode()))
