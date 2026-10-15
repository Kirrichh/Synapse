"""Retain oversized logical owner events through bounded physical tree members."""
import hashlib

import pytest

from acceptance.stage4.stage16 import _shared_memory_events as E
from synapse.experiments.gold import project_memory_store as M


def _fleet_configuration():
    bundle = "permit only the declared peer certificate and quorum epoch;\n" * 600
    return {"source": "certificate-inventory", "observed_at": "2026-10-14T09:00:00Z",
            "nodes": [{"node": number, "failure_zone": number % 3, "epoch": 19,
                       "trusted_peer_configuration": bundle} for number in range(600)]}


@pytest.mark.parametrize("kind", sorted(M.EVENT_KINDS))
def test_every_owner_event_kind_can_retain_more_than_16_mib_exactly(tmp_path, kind):
    store = M.ProjectMemoryStore(tmp_path)
    value = E.event(kind=kind, body=_fleet_configuration())
    expected = M.canonical(value)
    assert len(expected) > 16 * 1024 * 1024
    with store.session() as guard:
        receipt = E.put(store, guard, value)
        assert E.put(store, guard, value) == receipt
    layout = E.manifest(store, receipt)
    assert len(M.canonical(layout)) <= 64 * 1024
    assert layout["root"]["height"] >= 2
    assert layout["logical_byte_length"] == len(expected)
    assert layout["logical_sha256"] == hashlib.sha256(expected).hexdigest()
    blocks = E.blocks(store, receipt)
    assert all(0 < path.stat().st_size <= 64 * 1024 for path in blocks)
    reader = M.ProjectMemoryStore(tmp_path, read_only=True)
    assert M.canonical(reader.read(receipt)) == expected
    with store.session() as guard:
        assert store.collect_unreferenced_blocks(guard=guard)["removed_bytes"] == 0
    assert M.canonical(reader.read(receipt)) == expected
    assert receipt["ref"] == M.source_ref(expected, M.MEMORY_EVENT_V1).to_dict()
