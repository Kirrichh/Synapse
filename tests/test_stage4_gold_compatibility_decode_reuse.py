"""Compatibility transport reuse, with small physical history read contracts.

Synthetic frame records exercise byte storage only. They establish no evaluator
result, compatibility evidence or admission authority.
"""
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from synapse.experiments.gold import compatibility_store as CS
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.persistence import JOURNAL_FRAME_MAGIC_V1, encode_journal_frame
from tests.gold_store_fence import fence_for


@pytest.fixture(autouse=True)
def isolated_decode_reuse():
    CS._reused_decode_frame.cache_clear()
    yield
    CS._reused_decode_frame.cache_clear()


def frame_bytes(value='original', *, sequence=1, parent=None, coordinator='a' * 32):
    canonical = CS._canonical({'transport_fixture': value})
    digest = hashlib.sha256(canonical).hexdigest()
    ref = HashBoundRef(RefKind.ARTIFACT, digest, 'synapse.test.compatibility-record/v1',
                       digest, len(canonical), 'application/json')
    return CS._frame_payload(kind=CS.CompatibilityRecordKind.CONTEXT, record_ref=ref,
        canonical_bytes=canonical, sequence=sequence,
        parent_anchor=CS._anchor_chain(())[-1] if parent is None else parent,
        coordinator_id=coordinator)


def count_decodes(monkeypatch):
    original, calls = CS.decode_stage4_canonical_bytes, []

    def decode(raw, **kwargs):
        calls.append(raw)
        return original(raw, **kwargs)

    monkeypatch.setattr(CS, 'decode_stage4_canonical_bytes', decode)
    return calls


def write_history(path, *payloads):
    path.write_bytes(JOURNAL_FRAME_MAGIC_V1 + b''.join(encode_journal_frame(raw) for raw in payloads))


def physical_store(tmp_path):
    fence = fence_for(tmp_path)
    root = tmp_path / 'compatibility'
    root.mkdir()
    path = root / CS.COMPATIBILITY_HISTORY_FILE_V1
    raw = frame_bytes(coordinator=fence.coordinator_id())
    write_history(path, raw)
    return CS.FileCompatibilityStore(root, mutation_fence=fence, read_only=True), raw


def test_equal_complete_bytes_reuse_an_immutable_frame(monkeypatch):
    calls = count_decodes(monkeypatch)
    raw = frame_bytes()
    first = CS._decode_frame(raw)
    assert CS._decode_frame(bytes(bytearray(raw))) is first
    assert calls == [raw]
    with pytest.raises(FrozenInstanceError):
        first.sequence = 7
    with pytest.raises(FrozenInstanceError):
        first.record_ref.sha256 = '0' * 64
    projection = first.record_ref.to_dict()
    projection['sha256'] = '0' * 64
    assert CS._decode_frame(raw).record_ref.to_dict() != projection
    assert type(first.canonical_bytes) is type(first.frame_bytes) is bytes


def test_changed_physical_bytes_and_missing_file_are_observed(tmp_path, monkeypatch):
    calls = count_decodes(monkeypatch)
    original_scan, scans = CS.scan_journal, []

    def scan(path, **kwargs):
        scans.append(path)
        assert kwargs == {'create_if_missing': False}
        return original_scan(path, **kwargs)

    monkeypatch.setattr(CS, 'scan_journal', scan)
    store, raw = physical_store(tmp_path)
    assert store.current_sequence() == 1
    changed = frame_bytes('changed', coordinator=store.mutation_fence.coordinator_id())
    write_history(store.path, changed)
    expected = CS._decode_frame(changed)
    assert store.resolve_ref(expected.record_ref) == expected.canonical_bytes
    assert calls == [raw, changed]
    store.path.unlink()
    with pytest.raises(CS.CompatibilityStoreViolation) as missing:
        store.current_sequence()
    assert missing.value.failure_code is CS.CompatibilityStoreFailureCode.HISTORY_CORRUPT
    assert scans == [store.path] * 4
    assert not store.path.exists()


@pytest.mark.parametrize('damage', ['noncanonical', 'shape', 'record-bytes'])
def test_invalid_changed_bytes_are_rejected_and_not_retained(monkeypatch, damage):
    calls = count_decodes(monkeypatch)
    raw = frame_bytes()
    CS._decode_frame(raw)
    data = json.loads(raw)
    if damage == 'noncanonical':
        changed = raw + b' '
    else:
        if damage == 'shape':
            del data['coordinator_id']
        else:
            data['canonical_record'] = '{}'
        changed = CS._canonical(data)
    for _ in range(2):
        with pytest.raises(CS.CompatibilityStoreViolation) as invalid:
            CS._decode_frame(changed)
        assert invalid.value.failure_code is CS.CompatibilityStoreFailureCode.HISTORY_CORRUPT
    assert calls == [raw, changed, changed]
    assert CS._reused_decode_frame.cache_info().currsize == 1


@pytest.mark.parametrize(('change', 'code'), [
    ('sequence', CS.CompatibilityStoreFailureCode.SEQUENCE_GAP),
    ('parent', CS.CompatibilityStoreFailureCode.HISTORY_FORKED),
    ('coordinator', CS.CompatibilityStoreFailureCode.COORDINATOR_MISMATCH),
    ('duplicate', CS.CompatibilityStoreFailureCode.RECORD_DUPLICATE),
])
def test_history_checks_still_run_for_already_decoded_frames(tmp_path, monkeypatch, change, code):
    calls = count_decodes(monkeypatch)
    store, raw = physical_store(tmp_path)
    coordinator = store.mutation_fence.coordinator_id()
    options = {'coordinator': coordinator}
    if change == 'sequence':
        options['sequence'] = 2
    elif change == 'parent':
        options['parent'] = '0' * 64
    elif change == 'coordinator':
        options['coordinator'] = '0' * 32 if coordinator != '0' * 32 else '1' * 32
    else:
        options.update(sequence=2, parent=CS._anchor_chain((raw,))[-1])
    changed = frame_bytes(**options)
    CS._decode_frame(changed)  # Transport validity never validates the containing history.
    write_history(store.path, *((raw, changed) if change == 'duplicate' else (changed,)))
    with pytest.raises(CS.CompatibilityStoreViolation) as invalid:
        store.current_anchor()
    assert invalid.value.failure_code is code
    assert calls == [raw, changed]


def test_decode_capacity_is_bounded_and_eviction_recomputes(monkeypatch):
    calls = count_decodes(monkeypatch)
    capacity = CS._reused_decode_frame.cache_info().maxsize
    assert 0 < capacity * CS._FRAME_REUSE_MAX_BYTES <= 64 * 1024 * 1024
    first = frame_bytes(sequence=1)
    for sequence in range(1, capacity + 2):
        CS._decode_frame(frame_bytes(sequence=sequence))
    assert len(calls) == capacity + 1
    assert CS._reused_decode_frame.cache_info().currsize == capacity
    CS._decode_frame(frame_bytes(sequence=capacity + 1))
    assert len(calls) == capacity + 1
    CS._decode_frame(first)
    assert len(calls) == capacity + 2


def test_oversized_frame_uses_the_same_decoder_without_retention(monkeypatch):
    calls = count_decodes(monkeypatch)
    raw = frame_bytes('x' * CS._FRAME_REUSE_MAX_BYTES)
    assert len(raw) > CS._FRAME_REUSE_MAX_BYTES
    first, second = CS._decode_frame(raw), CS._decode_frame(raw)
    assert first == second and first is not second
    assert calls == [raw, raw]
    assert CS._reused_decode_frame.cache_info().currsize == 0
