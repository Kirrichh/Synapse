"""Stage 4 algorithmic read-reuse contracts; no authority reuse is implied."""
from __future__ import annotations

import copy
import json

import pytest

from synapse.experiments.gold import canonicalization as canon
from synapse.experiments.gold.runner.records import StoredRunRecord, _RunRecordReadIndex
from synapse.experiments.gold.stage13 import publication_store as publications
from synapse.experiments.gold.stage14.read_traversal import publication_read_scope


@pytest.fixture(autouse=True)
def clear_decoded_artifacts():
    canon._decoded_artifact_cache.clear()
    yield
    canon._decoded_artifact_cache.clear()


def canonical(value):
    return canon.canonicalize_stage4_payload(
        value, profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=canon.STABLE_CANONICAL_CODEC_ID)


def decode(raw):
    return canon.decode_stage4_canonical_bytes(
        raw, profile_id=canon.STAGE4_CANONICAL_PROFILE_V1,
        codec_id=canon.STABLE_CANONICAL_CODEC_ID)


def test_stage4_encode_and_decode_each_project_data_once(monkeypatch):
    monkeypatch.setattr(canon, "_native", None)
    value = {"z": [1, {"e\u0301": True}], "a": -0.0}
    calls = []
    original = canon._canonicalize_data_only

    def counted(item, **kwargs):
        if not kwargs.get("seen"):
            calls.append(item)
        return original(item, **kwargs)

    monkeypatch.setattr(canon, "_canonicalize_data_only", counted)
    monkeypatch.setattr(canon, "_validate_data_only", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("separate validation traversal must not run")))
    raw = canonical(value)
    assert decode(raw) == {"a": 0.0, "z": [1, {"é": True}]}
    assert len(calls) == 2


def test_decoded_artifact_cache_is_content_bound_bounded_and_mutation_isolated(monkeypatch):
    value = {"nested": {"items": [1, 2, 3]}, "text": "\u043f\u0430\u043c\u044f\u0442\u044c"}
    raw = canonical(value)
    first = decode(raw)
    first["nested"]["items"].append(4)
    first["text"] = "changed"
    assert decode(raw) == value
    info = canon._decoded_artifact_cache.info()
    assert (info.hits, info.misses, info.currsize) == (1, 1, 1)
    assert info.byte_length == len(raw)

    original_loads = json.loads
    calls = []
    monkeypatch.setattr(canon.json, "loads", lambda *a, **k: calls.append(a) or original_loads(*a, **k))
    decode(raw)
    assert calls == []

    other = canonical({"nested": {"items": [1, 2, 3]}, "text": "\u0434\u0440\u0443\u0433\u043e\u0439"})
    assert decode(other) != value
    assert canon._decoded_artifact_cache.info().currsize == 2


def test_decoder_does_not_cache_rejected_or_oversized_artifacts():
    for raw in (b'{ "a":1}', b'{"a":1,"a":2}', b'{"a":NaN}'):
        for _ in range(2):
            with pytest.raises(canon.CanonicalizationViolation):
                decode(raw)
    assert canon._decoded_artifact_cache.info().currsize == 0

    small = canon._ImmutableDecodedArtifactCache(max_entries=2, max_bytes=8, max_item_bytes=4)
    small.put(b"12345", {"not": "retained"})
    assert small.info().currsize == 0
    small.put(b"aaaa", {"a": 1})
    small.put(b"bbbb", {"b": 2})
    small.put(b"cccc", {"c": 3})
    assert small.info().currsize == 2
    assert small.info().byte_length == 8
    assert small.get(b"aaaa") is canon._CACHE_MISS
    small.clear()
    assert small.info().currsize == small.info().byte_length == 0


def test_publication_requests_reopen_physical_bytes_and_return_fresh_values(monkeypatch):
    root = publications.Path("/physical/publications")
    raw = canonical({"domain": {"operation_id": "one"}, "nested": {"value": 1}})
    calls = []

    def physical(root, *, transaction_id):
        calls.append((root, transaction_id))
        return {}, {"request.json": raw}

    monkeypatch.setattr(publications, "read_committed_snapshot_transaction", physical)
    with publication_read_scope():
        first = publications.read_prepared_publication_request(root, "tx")
        first["nested"]["value"] = 99
        second = publications.read_prepared_publication_request(root, "tx")
        assert second == json.loads(raw)
        assert len(calls) == 2
        raw = canonical({"domain": {"operation_id": "two"}, "nested": {"value": 2}})
        assert publications.read_prepared_publication_request(root, "tx") == json.loads(raw)
        assert len(calls) == 3
    publications.read_prepared_publication_request(root, "tx")
    assert len(calls) == 4


def test_run_record_index_is_reconstruction_scoped_and_returns_fresh_payloads():
    class Store:
        def __init__(self):
            self.calls = 0
            self.payload = {"nested": {"value": 1}}

        def get(self, *, kind, key):
            self.calls += 1
            return StoredRunRecord(kind, key, copy.deepcopy(self.payload), "a" * 64)

    physical = Store()
    index = _RunRecordReadIndex(physical)
    first = index.get(kind="run-manifest", key="manifest")
    first.payload["nested"]["value"] = 99
    second = index.get(kind="run-manifest", key="manifest")
    assert second.payload == {"nested": {"value": 1}}
    assert physical.calls == 1

    next_operation = index.read_index()
    assert next_operation is index  # A nested reconstruction retains its own cut.
    independent = _RunRecordReadIndex(physical)
    assert independent.get(kind="run-manifest", key="manifest").payload == {"nested": {"value": 1}}
    assert physical.calls == 2
