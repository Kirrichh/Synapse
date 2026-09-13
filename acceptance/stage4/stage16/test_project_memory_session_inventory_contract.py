"""Small real-file regressions for one held memory-owner inventory."""
from collections import Counter
from copy import deepcopy

import pytest

from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.persistence import PersistenceViolation


def _put(store, guard, *, job="a", kind="REQUESTED", payload=None):
    return store.put(kind=kind, job_key=job * 64,
        payload={"value": "original"} if payload is None else payload, guard=guard)


def test_one_owner_session_reads_old_events_once_and_includes_each_new_commit(tmp_path, monkeypatch):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        prior = [_put(store, guard, job=job) for job in ("a", "b")]
    reads = Counter()
    physical_read = M.read_committed_snapshot_transaction

    def counted(*args, **kwargs):
        reads[kwargs["transaction_id"]] += 1
        return physical_read(*args, **kwargs)

    monkeypatch.setattr(M, "read_committed_snapshot_transaction", counted)
    with store.session() as guard:
        assert len(store.inventory(guard=guard)) == 2
        added = [_put(store, guard, job="c", kind=kind)
                 for kind in ("REQUESTED", "STARTED", "FRAME_COMPLETED")]
        inventory = store.inventory(guard=guard)
        assert {receipt["transaction_id"] for _, receipt in inventory} == {
            receipt["transaction_id"] for receipt in prior + added}
    assert reads == Counter({receipt["transaction_id"]: 1 for receipt in prior + added})


def test_each_new_session_and_unguarded_inventory_reads_physical_events_again(tmp_path, monkeypatch):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = _put(store, guard)
    reads = []
    physical_read = M.read_committed_snapshot_transaction

    def counted(*args, **kwargs):
        reads.append(kwargs["transaction_id"])
        return physical_read(*args, **kwargs)

    monkeypatch.setattr(M, "read_committed_snapshot_transaction", counted)
    for _ in range(2):
        with store.session() as guard:
            assert store.inventory(guard=guard)[0][1] == receipt
    assert store.inventory()[0][1] == receipt
    assert M.ProjectMemoryStore(tmp_path, read_only=True).inventory()[0][1] == receipt
    assert reads == [receipt["transaction_id"]] * 4


def test_owner_inventory_is_detached_from_input_payload_and_returned_records(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    payload = {"items": ["original"]}
    with store.session() as guard:
        returned = _put(store, guard, payload=payload)
        retained = deepcopy(returned)
        payload["items"].append("caller mutation")
        returned.clear()
        exposed = store.inventory(guard=guard)
        exposed[0][0]["payload"]["items"].append("view mutation")
        exposed[0][1].clear()
        exposed.clear()
        assert _put(store, guard, payload={"items": ["original"]}) == retained
        with pytest.raises(ValueError, match="cannot be rewritten"):
            _put(store, guard, payload={"items": ["changed"]})
        assert store.read(retained)["payload"] == {"items": ["original"]}


@pytest.mark.parametrize("failure_point", ["before_commit", "after_commit", "readback"])
def test_caught_mutation_failure_invalidates_session_until_a_fresh_recovery(tmp_path, monkeypatch, failure_point):
    store = M.ProjectMemoryStore(tmp_path)
    commit = M.commit_snapshot_transaction

    def interrupted_commit(*args, **kwargs):
        if failure_point == "after_commit":
            commit(*args, **kwargs)
        raise RuntimeError("acceptance interruption at event commit")

    def unavailable_readback(transaction):
        raise OSError("acceptance committed-event readback unavailable")

    with store.session() as guard:
        with monkeypatch.context() as patch:
            if failure_point == "readback":
                patch.setattr(store, "_read", unavailable_readback)
                expected = OSError
            else:
                patch.setattr(M, "commit_snapshot_transaction", interrupted_commit)
                expected = JournalAdapterViolation
            with pytest.raises(expected):
                _put(store, guard)
        with pytest.raises(ValueError, match="uninterrupted owner session"):
            store.inventory(guard=guard)
        with pytest.raises(ValueError, match="uninterrupted owner session"):
            _put(store, guard)
    before = {path: path.read_bytes() for path in store.events.rglob("*") if path.is_file()}
    visible = M.ProjectMemoryStore(tmp_path, read_only=True).inventory()
    assert len(visible) == int(failure_point != "before_commit")
    with store.session() as guard:
        recovered = _put(store, guard)
        assert _put(store, guard) == recovered
        assert store.inventory(guard=guard)[0][1] == recovered
    assert {path: path.read_bytes() for path in before} == before
    assert len(store.inventory()) == 1
    if visible:
        assert recovered == visible[0][1]
    assert store.fence.current_epoch() % 2 == 0


def test_unpublished_preparation_still_consumes_the_transaction_budget(tmp_path, monkeypatch):
    store = M.ProjectMemoryStore(tmp_path)

    def interrupted(*args, **kwargs):
        raise RuntimeError("acceptance interruption before commit")

    with monkeypatch.context() as patch:
        patch.setattr(M, "commit_snapshot_transaction", interrupted)
        with pytest.raises(JournalAdapterViolation):
            with store.session() as guard:
                _put(store, guard)
    monkeypatch.setattr(M, "MAX_MEMORY_EVENTS", 1)
    before = {path: path.read_bytes() for path in store.events.rglob("*") if path.is_file()}
    with store.session() as guard:
        assert store.inventory(guard=guard) == []
        with pytest.raises(ValueError, match="event budget"):
            _put(store, guard)
    assert store.inventory() == []
    assert {path: path.read_bytes() for path in before} == before


def test_read_and_idempotent_put_recheck_bytes_then_next_session_refuses_changed_event(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = _put(store, guard)
        member = store.events / receipt["transaction_id"] / "event.json"
        raw = member.read_bytes()
        member.write_bytes(raw.replace(b"original", b"modified"))
        with pytest.raises(PersistenceViolation):
            store.read(receipt)
        with pytest.raises(PersistenceViolation):
            _put(store, guard)
        with pytest.raises(ValueError, match="uninterrupted owner session"):
            store.inventory(guard=guard)
    epoch = store.fence.current_epoch()
    with pytest.raises(PersistenceViolation):
        with store.session():
            pytest.fail("changed committed bytes must not produce an owner inventory")
    assert store.fence.current_epoch() == epoch
    with pytest.raises(PersistenceViolation):
        M.ProjectMemoryStore(tmp_path, read_only=True).read(receipt)


def test_guarded_inventory_and_idempotent_put_reject_a_retired_session_guard(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as old_guard:
        _put(store, old_guard)
    with pytest.raises(JournalAdapterViolation):
        store.inventory(guard=old_guard)
    with store.session() as guard:
        with pytest.raises(JournalAdapterViolation):
            _put(store, old_guard)
        assert len(store.inventory(guard=guard)) == 1


def test_invalid_visible_event_is_checked_before_abandoned_epoch_recovery(tmp_path, monkeypatch):
    store = M.ProjectMemoryStore(tmp_path)
    commit = M.commit_snapshot_transaction

    def interrupted(*args, **kwargs):
        commit(*args, **kwargs)
        raise RuntimeError("acceptance interruption after visible commit")

    with monkeypatch.context() as patch:
        patch.setattr(M, "commit_snapshot_transaction", interrupted)
        with pytest.raises(JournalAdapterViolation):
            with store.session() as guard:
                _put(store, guard)
    epoch = store.fence.current_epoch()
    assert epoch % 2 == 1
    member = next(store.events.glob("*/event.json"))
    member.write_bytes(member.read_bytes().replace(b"original", b"modified"))
    recovery_calls = []
    monkeypatch.setattr(M.FileSnapshotFence, "recover_abandoned_interval",
        lambda self, **kwargs: recovery_calls.append(kwargs))
    with pytest.raises(PersistenceViolation):
        with store.session():
            pytest.fail("invalid visible events must prevent recovery")
    assert recovery_calls == []
    assert store.fence.current_epoch() == epoch
