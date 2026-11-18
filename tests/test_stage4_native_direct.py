"""Exercise the installed native extension independently of public fallback."""
from types import BuiltinFunctionType
import os

import pytest

from synapse.experiments.gold import canonicalization as c


def native():
    if c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    assert isinstance(c._native.encode, BuiltinFunctionType)
    return c._native


@pytest.mark.parametrize('value, expected', [
    (None, b'null'), ({'e\u0301': [True, -0.0, 2**53 - 1]}, '{"é":[true,0.0,9007199254740991]}'.encode()),
    ('\n"\\', b'"\\n\\"\\\\"'), (1e300, b'1e+300'),
])
def test_native_direct_emits_exact_bytes_without_python_fallback(monkeypatch, value, expected):
    backend = native()
    monkeypatch.setattr(c, '_encode_stage4_data_only_python', lambda value: pytest.fail('Python fallback was called'))
    assert backend.encode(value) == expected
    assert c.canonicalize_stage4_payload(value, profile_id=c.STAGE4_CANONICAL_PROFILE_V1,
                                         codec_id=c.STABLE_CANONICAL_CODEC_ID) == expected


def cyclic():
    result = []
    result.append(result)
    return result


def nested(depth):
    value = None
    for _ in range(depth):
        value = [value]
    return value


def test_native_direct_accepts_maximum_depth():
    assert native().encode(nested(128)) == b'[' * 128 + b'null' + b']' * 128


@pytest.mark.parametrize('value', [2**53, float('nan'), float('inf'), '\ud800',
    {'é': 1, 'e\u0301': 2}, {'é': 1, 'e\u0301': 2, 'bad': '\ud800'}, cyclic(), nested(129)])
def test_native_direct_rejects_invalid_values(value):
    backend = native()
    with pytest.raises(backend.NativeError):
        backend.encode(value)
