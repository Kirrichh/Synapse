"""Stage 4 optimized codecs must retain the pre-optimization public contract."""
import json
import math
import os
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as c


PROFILE = c.STAGE4_CANONICAL_PROFILE_V1
CODEC = c.STABLE_CANONICAL_CODEC_ID


VECTORS = json.loads((Path(__file__).parent / 'data/stage4_baseline_1e226e52.json').read_text())


def observed(call, value):
    try:
        result = call(value)
        return {'kind': 'ok', 'value': result.hex() if isinstance(result, bytes) else result}
    except Exception as exc:
        code = getattr(exc, 'failure_code', None)
        return {'kind': 'error', 'class': type(exc).__module__ + '.' + type(exc).__qualname__,
                'failure_code': getattr(code, 'value', None), 'detail': getattr(exc, 'detail', None),
                'str': str(exc), 'args': list(exc.args)}


def samples():
    cycle_list = []
    cycle_list.append(cycle_list)
    cycle_dict = {}
    cycle_dict['self'] = cycle_dict
    deep = None
    for _ in range(130):
        deep = [deep]
    within = None
    for _ in range(128):
        within = [within]
    over = [within]
    return [None, {'a': ['e\u0301', -0.0, 2**53 - 1]}, {'é': 1, 'e\u0301': 2},
            {'é': 1, 'e\u0301': 2, 'bad': '\ud800'}, {'e\u0301': {'bad': '\ud800'}},
            {'é': 1, 'e\u0301': 2, 'bad': 2**60},
            {'e\u0301' * 130: {'bad': '\ud800'}}, cycle_list, cycle_dict, within, over, deep,
            math.nan, math.inf, -math.inf, 2**53, -(2**53), -(2**53 - 1), 1e300, -0.0,
            type('x' * 300, (), {})()]


def test_python_against_old_public_contract(monkeypatch):
    monkeypatch.setattr(c, '_native', None)
    for index, value in enumerate(samples()):
        assert VECTORS["encode"][index] == observed(
            lambda v: c.canonicalize_stage4_payload(v, profile_id=PROFILE, codec_id=CODEC), value)


@pytest.mark.parametrize('index,raw', list(enumerate([b'{"a":1,"a":2}', b'\xff', b'{ "a": 1 }', b'NaN', b'Infinity', b'1e999',
                                  b'"\\ud800"', b'{"e\\u0301":1,"\\u00e9":2}', b'-0.0', b'null'])))
@pytest.mark.parametrize('backend', ['python', 'native'])
def test_decode_against_old_public_contract(index, raw, backend, monkeypatch):
    if backend == 'native' and c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    if backend == 'python':
        monkeypatch.setattr(c, '_native', None)
    c._decoded_artifact_cache.clear()
    assert VECTORS["decode"][index] == observed(
        lambda v: c.decode_stage4_canonical_bytes(v, profile_id=PROFILE, codec_id=CODEC), raw)


def test_loaded_native_against_old_public_contract():
    if c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    for index, value in enumerate(samples()):
        assert VECTORS["encode"][index] == observed(
            lambda v: c.canonicalize_stage4_payload(v, profile_id=PROFILE, codec_id=CODEC), value)
