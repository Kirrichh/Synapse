"""Shared bytes preserve each logical journal event's exact identity."""
from copy import deepcopy
import hashlib

import pytest

from synapse.experiments.gold import project_memory_store as M
from acceptance.stage4.stage16 import _shared_memory_events as E


@pytest.mark.parametrize("kind", ["SNAPSHOT_BOUNDARY", "MEMORY_STATE", "CONSOLIDATION_REPORTED",
                                 "REQUESTED", "FRAME_COMPLETED"])
def test_large_event_restores_exact_canonical_bytes_under_its_original_receipt(tmp_path, kind):
    store, value = M.ProjectMemoryStore(tmp_path), E.event(kind=kind)
    expected = M.canonical(value)
    with store.session() as guard:
        receipt = E.put(store, guard, value)
        assert E.put(store, guard, value) == receipt
    layout = E.manifest(store, receipt)
    assert layout["schema_version"] == "synapse.stage4.gold.memory-event-blocks/v1"
    assert layout["logical_sha256"] == hashlib.sha256(expected).hexdigest()
    assert layout["logical_byte_length"] == len(expected)
    assert receipt["ref"] == M.source_ref(expected, M.MEMORY_EVENT_V1).to_dict()
    reader = M.ProjectMemoryStore(tmp_path, read_only=True)
    assert M.canonical(reader.read(receipt)) == expected
    assert reader.inventory() == [(value, receipt)]


def test_shared_content_keeps_independent_provenance_status_and_publication_identity(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    values = [E.event(job=job, body=E.payload(source=source, generation=window))
              for job, source, window in [("a", "observer-a", 7), ("b", "observer-b", 8),
                                          ("c", "observer-c", 9)]]
    values[1]["payload"]["origin"]["status"] = "refuted"
    values[2]["payload"]["origin"]["status"] = "provisional"
    with store.session() as guard:
        receipts = [E.put(store, guard, value) for value in values]
    block_sets = [E.blocks(store, receipt) for receipt in receipts]
    assert set.intersection(*block_sets), "equal content must actually share retained physical bytes"
    assert len({receipt["ref"]["sha256"] for receipt in receipts}) == len(values)
    assert len({receipt["transaction_id"] for receipt in receipts}) == len(values)
    assert [store.read(receipt) for receipt in receipts] == values
    retained_bytes = sum(path.stat().st_size for path in set.union(*block_sets))
    assert retained_bytes < sum(len(M.canonical(value)) for value in values) * 0.6
    swapped = {**receipts[0], "ref": receipts[1]["ref"]}
    with pytest.raises(ValueError, match="retained identity"):
        store.read(swapped)


def test_legacy_and_shared_events_keep_one_inventory_with_detached_public_values(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    legacy, shared = E.event(job="a"), E.event(job="b")
    with store.session() as guard:
        old_receipt = E.commit_legacy(store, guard, legacy)
    old_bytes = E.physical_files(store.events / old_receipt["transaction_id"])
    with store.session() as guard:
        new_receipt = E.put(store, guard, shared)
        expected = sorted([(legacy, old_receipt), (deepcopy(shared), deepcopy(new_receipt))],
                          key=lambda item: item[1]["transaction_id"])
        exposed = store.inventory(guard=guard)
        shared["payload"]["versions"][0]["statement"]["free"] = -1
        exposed[0][0]["payload"]["versions"][1]["conditions"]["placement"].clear()
        exposed[0][1]["ref"].clear()
        assert store.inventory(guard=guard) == expected
    assert M.ProjectMemoryStore(tmp_path, read_only=True).inventory() == expected
    assert {path: path.read_bytes() for path in old_bytes} == old_bytes
    assert M.canonical(store.read(old_receipt)) == M.canonical(legacy)


def test_shared_form_does_not_widen_the_logical_event_budget(tmp_path, monkeypatch):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    monkeypatch.setattr(M, "MAX_MEMORY_EVENT_BYTES", len(M.canonical(value)) - 1)
    with store.session() as guard:
        with pytest.raises(ValueError, match="byte budget"):
            E.put(store, guard, value)
        assert store.inventory(guard=guard) == []
    assert not list((store.root / "blocks").glob("*.bin"))
