"""Stage 4 optimized codecs must retain the pre-optimization public contract."""
import json
import math
import os

import pytest

from synapse import canonical_values as stable
from synapse.experiments.gold import canonicalization as c


PROFILE = c.STAGE4_CANONICAL_PROFILE_V1
CODEC = c.STABLE_CANONICAL_CODEC_ID


def baseline_encode(value):
    c._validate_data_only(value)
    try:
        result = stable.canonical_bytes(value)
    except stable.CanonicalSerializationError as exc:
        raise c._fail(c.CanonicalizationFailureCode.INVALID_CANONICAL_VALUE, "stable codec rejected payload") from exc
    if result.profile != CODEC or type(result.data) is not bytes:
        raise c._fail(c.CanonicalizationFailureCode.UNKNOWN_CODEC, "stable codec returned an incompatible result")
    return result.data


def baseline_decode(value):
    try:
        text = value.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise c._fail(c.CanonicalizationFailureCode.INVALID_UTF8, 'canonical bytes are not UTF-8') from exc
    try:
        decoded = json.loads(text, object_pairs_hook=c._object_without_duplicates, parse_constant=c._reject_json_constant)
    except c.CanonicalizationViolation:
        raise
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise c._fail(c.CanonicalizationFailureCode.NON_CANONICAL_BYTES, 'canonical JSON is malformed') from exc
    c._validate_data_only(decoded)
    if baseline_encode(decoded) != value:
        raise c._fail(c.CanonicalizationFailureCode.NON_CANONICAL_BYTES, 'bytes are not exact canonical encoding')
    return decoded


def observed(call, value):
    try:
        return ('ok', call(value))
    except Exception as exc:
        return ('error', type(exc), getattr(exc, 'failure_code', None), getattr(exc, 'detail', None), str(exc), exc.args)


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
    for value in samples():
        assert observed(baseline_encode, value) == observed(
            lambda v: c.canonicalize_stage4_payload(v, profile_id=PROFILE, codec_id=CODEC), value)


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'\xff', b'{ "a": 1 }', b'NaN', b'Infinity', b'1e999',
                                  b'"\\ud800"', b'{"e\\u0301":1,"\\u00e9":2}', b'-0.0', b'null'])
@pytest.mark.parametrize('backend', ['python', 'native'])
def test_decode_against_old_public_contract(raw, backend, monkeypatch):
    if backend == 'native' and c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    if backend == 'python':
        monkeypatch.setattr(c, '_native', None)
    c._decoded_artifact_cache.clear()
    assert observed(baseline_decode, raw) == observed(
        lambda v: c.decode_stage4_canonical_bytes(v, profile_id=PROFILE, codec_id=CODEC), raw)


def test_loaded_native_against_old_public_contract():
    if c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    for value in samples():
        assert observed(baseline_encode, value) == observed(
            lambda v: c.canonicalize_stage4_payload(v, profile_id=PROFILE, codec_id=CODEC), value)
