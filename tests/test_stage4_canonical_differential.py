"""Stage 4 optimized codecs must retain the pre-optimization public contract."""
import json
import os
from pathlib import Path

import pytest

from synapse.experiments.gold import canonicalization as c
from acceptance.stage4.canonical_baseline_cases import DECODE_SAMPLES, fingerprint, samples


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



def test_python_against_old_public_contract(monkeypatch):
    monkeypatch.setattr(c, '_native', None)
    assert_encode_samples_match_vectors(samples())


@pytest.mark.parametrize('index,raw', list(enumerate(DECODE_SAMPLES)))
@pytest.mark.parametrize('backend', ['python', 'native'])
def test_decode_against_old_public_contract(index, raw, backend, monkeypatch):
    if backend == 'native' and c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    if backend == 'python':
        monkeypatch.setattr(c, '_native', None)
    c._decoded_artifact_cache.clear()
    assert_decode_sample_matches_vector(index, raw)


def test_loaded_native_against_old_public_contract():
    if c._native is None:
        if os.environ.get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1':
            pytest.fail('Stage 4 native backend was required but not loaded')
        pytest.skip('Stage 4 native backend is unavailable or Unicode-incompatible')
    assert_encode_samples_match_vectors(samples())


def assert_encode_samples_match_vectors(values):
    assert len(values) == len(VECTORS['encode']) == len(VECTORS['encode_inputs'])
    for value, expected, input_hash in zip(values, VECTORS['encode'], VECTORS['encode_inputs']):
        assert fingerprint(value) == input_hash
        assert expected == observed(
            lambda v: c.canonicalize_stage4_payload(v, profile_id=PROFILE, codec_id=CODEC), value)


def assert_decode_sample_matches_vector(index, raw):
    assert fingerprint(raw) == VECTORS["decode_inputs"][index]
    assert VECTORS["decode"][index] == observed(
        lambda v: c.decode_stage4_canonical_bytes(v, profile_id=PROFILE, codec_id=CODEC), raw)


def test_baseline_vectors_are_bound_to_their_inputs(monkeypatch):
    monkeypatch.setattr(c, '_native', None)
    values = samples()
    assert VECTORS['encode'][7] == VECTORS['encode'][8]
    assert_encode_samples_match_vectors(values)
    swapped = values.copy()
    swapped[7], swapped[8] = swapped[8], swapped[7]
    with pytest.raises(AssertionError):
        assert_encode_samples_match_vectors(swapped)
    replaced = values.copy()
    replaced[7] = values[8]
    with pytest.raises(AssertionError):
        assert_encode_samples_match_vectors(replaced)


def test_decode_vectors_reject_reordered_inputs_with_identical_refusals(monkeypatch):
    monkeypatch.setattr(c, '_native', None)
    assert len(DECODE_SAMPLES) == len(VECTORS['decode']) == len(VECTORS['decode_inputs'])
    assert VECTORS['decode'][2] == VECTORS['decode'][8]
    reordered = DECODE_SAMPLES.copy()
    reordered[2], reordered[8] = reordered[8], reordered[2]
    with pytest.raises(AssertionError):
        assert_decode_sample_matches_vector(2, reordered[2])
    with pytest.raises(AssertionError):
        assert_decode_sample_matches_vector(8, reordered[8])
