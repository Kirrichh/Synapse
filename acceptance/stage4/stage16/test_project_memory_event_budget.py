"""A host's per-event capacity preserves journal identity across storage formats."""
from __future__ import annotations

import pytest

from acceptance.stage4.stage16 import _shared_memory_events as E
from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.memory_event_budget import EVENT_BUDGET_ENV, MemoryEventBudgetExceeded


def _publish(store, value, representation):
    with store.session() as guard:
        if representation == "raw":
            return E.commit_legacy(store, guard, value)
        if representation == "flat-v1":
            return E.commit_legacy_shared(store, guard, value)
        return E.put(store, guard, value)


@pytest.mark.parametrize("representation", ["raw", "flat-v1", "tree-v2"])
def test_exact_event_capacity_reads_retained_identity_but_one_byte_less_refuses(tmp_path, representation):
    value = E.event(body={"routes": "verified route " * 8000})
    length = len(M.canonical(value))
    receipt = _publish(M.ProjectMemoryStore(tmp_path), value, representation)
    reader = M.ProjectMemoryStore(tmp_path, read_only=True, maximum_event_bytes=length)
    assert reader.read(receipt) == value
    limited = M.ProjectMemoryStore(tmp_path, read_only=True, maximum_event_bytes=length - 1)
    with pytest.raises(MemoryEventBudgetExceeded) as failure:
        limited.read(receipt)
    assert failure.value.required_bytes == length
    assert failure.value.maximum_bytes == length - 1
    with pytest.raises(MemoryEventBudgetExceeded):
        limited.inventory(kind="STARTED")
    assert reader.read(receipt) == value


def test_oversized_publication_refuses_before_owner_mutation_then_can_continue(tmp_path):
    value = E.event(body={"routes": "x" * 100000})
    store = M.ProjectMemoryStore(tmp_path, maximum_event_bytes=len(M.canonical(value)) - 1)
    with store.session() as guard:
        before = E.physical_files(store.root)
        with pytest.raises(MemoryEventBudgetExceeded):
            E.put(store, guard, value)
        assert E.physical_files(store.root) == before
        small = E.event(body={"routes": "accepted"})
        receipt = E.put(store, guard, small)
        assert store.read(receipt) == small
    assert store.inventory() == [(small, receipt)]


def test_event_capacity_does_not_limit_total_retained_journal_bytes(tmp_path):
    values = [E.event(job=job, body={"route": "x" * 100000}) for job in "abcd"]
    capacity = max(len(M.canonical(value)) for value in values)
    store = M.ProjectMemoryStore(tmp_path, maximum_event_bytes=capacity)
    with store.session() as guard:
        receipts = [E.put(store, guard, value) for value in values]
    reopened = M.ProjectMemoryStore(tmp_path, maximum_event_bytes=capacity)
    assert [reopened.read(receipt) for receipt in receipts] == values
    with reopened.session() as guard:
        assert len(reopened.inventory(guard=guard)) == len(values)
        collected = reopened.collect_unreferenced_blocks(guard=guard)
        assert collected["removed_blocks"] == 0
    assert sum(len(M.canonical(value)) for value in values) > capacity


def test_operator_environment_is_frozen_for_owner_but_explicit_capacity_takes_precedence(tmp_path, monkeypatch):
    value = E.event(body={"routes": "route" * 20000})
    length = len(M.canonical(value))
    monkeypatch.setenv(EVENT_BUDGET_ENV, str(length))
    store = M.ProjectMemoryStore(tmp_path)
    monkeypatch.setenv(EVENT_BUDGET_ENV, "1024")
    receipt = _publish(store, value, "tree-v2")
    assert store.read(receipt) == value
    with pytest.raises(MemoryEventBudgetExceeded):
        M.ProjectMemoryStore(tmp_path, read_only=True).read(receipt)
    explicit = M.ProjectMemoryStore(tmp_path, read_only=True, maximum_event_bytes=length)
    assert explicit.read(receipt) == value


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5, "1024"])
def test_invalid_explicit_capacity_cannot_create_storage(tmp_path, capacity):
    with pytest.raises(ValueError, match="positive integer"):
        M.ProjectMemoryStore(tmp_path, maximum_event_bytes=capacity)
    assert not (tmp_path / "project-memory").exists()


@pytest.mark.parametrize("capacity", ["", "0", "-1", "1.5", "unlimited", " 1024", "1_024"])
def test_invalid_operator_capacity_cannot_create_storage(tmp_path, monkeypatch, capacity):
    monkeypatch.setenv(EVENT_BUDGET_ENV, capacity)
    with pytest.raises(ValueError, match="positive integer"):
        M.ProjectMemoryStore(tmp_path)
    assert not (tmp_path / "project-memory").exists()
