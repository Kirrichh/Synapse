"""Contract of store D's removal markers (review AUD-5).

A body leaves store D only behind a marker naming why. The marker only
strengthens: ``compacted`` becomes ``forgotten`` with the forget's tombstone; a
compaction never lowers ``forgotten``; a body already forgotten keeps the
tombstone of the forget it left with; repeating an act changes nothing;
writing the same content again restores the body and clears the marker; a
marker with an unknown reason is no explanation.
"""
from __future__ import annotations

import json

import pytest

from synapse.memory_consolidation.tools.evidence import EvidenceStore
from synapse.memory_consolidation.tools.journal import GatewayIntegrityError


def _stored(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    ref, _ = store.put({"answer": 20})
    return store, ref


def _why(store, ref):
    gone = store.gone(ref)
    return None if gone is None else (gone["reason"], gone["tombstone"])


def test_a_marker_only_strengthens(tmp_path):
    store, ref = _stored(tmp_path)
    store.discard(ref, "compacted")
    assert store.get(ref) is None and _why(store, ref) == ("compacted", None)
    store.discard(ref, "compacted")
    assert _why(store, ref) == ("compacted", None)
    store.discard(ref, "forgotten", tombstone="tmb_first")
    assert _why(store, ref) == ("forgotten", "tmb_first")
    store.discard(ref, "compacted")
    store.discard(ref, "forgotten", tombstone="tmb_later")
    assert _why(store, ref) == ("forgotten", "tmb_first")  # It left with the first forget.

    restored, preexisting = store.put({"answer": 20})
    assert (restored, preexisting) == (ref, False) and store.get(ref)["answer"] == 20 and store.gone(ref) is None


def test_a_marker_with_an_unknown_reason_explains_nothing(tmp_path):
    store, ref = _stored(tmp_path)
    store.discard(ref, "compacted")
    marker = tmp_path / "evidence" / f"{ref}.gone.json"
    marker.write_text(json.dumps({**json.loads(marker.read_text()), "reason": "lost"}))
    with pytest.raises(GatewayIntegrityError):
        store.gone(ref)
    with pytest.raises(GatewayIntegrityError):
        store.discard(ref, "forgotten", tombstone="tmb_1")
