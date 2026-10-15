"""Interrupted shared publication preserves committed roots through garbage collection."""
import pytest

from acceptance.stage4.stage16 import _shared_memory_events as E
from synapse.experiments.gold import memory_blocks as B
from synapse.experiments.gold import persistence as P
from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.memory_event_storage import MemoryEventStorage
from synapse.experiments.gold.memory_event_encoding import encode_event


def _interrupt(patch, point):
    if point.startswith("block_"):
        publish = B.publish_immutable

        def interrupted(*args, **kwargs):
            if point == "block_published":
                publish(*args, **kwargs)
            raise RuntimeError("acceptance interruption during shared block publication")

        patch.setattr(B, "publish_immutable", interrupted)
    else:
        commit = M.commit_snapshot_transaction

        def interrupted(*args, **kwargs):
            if point == "after_event_commit":
                commit(*args, **kwargs)
            raise RuntimeError("acceptance interruption at the terminal event commit")

        patch.setattr(M, "commit_snapshot_transaction", interrupted)


@pytest.mark.parametrize("point", ["block_staged", "block_published", "before_event_commit", "after_event_commit"])
def test_interruption_never_exposes_an_event_without_its_complete_blocks(tmp_path, monkeypatch, point):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    with monkeypatch.context() as patch:
        _interrupt(patch, point)
        with pytest.raises(JournalAdapterViolation):
            with store.session() as guard:
                E.put(store, guard, value)
    visible = M.ProjectMemoryStore(tmp_path, read_only=True).inventory()
    assert len(visible) == int(point == "after_event_commit")
    assert store.fence.current_epoch() % 2 == 1
    before = E.physical_files(store.root / "blocks")
    with store.session() as guard:
        collected = store.collect_unreferenced_blocks(guard=guard)
        if visible:
            assert collected["removed_bytes"] == 0
            assert E.physical_files(store.root / "blocks") == before
        else:
            assert collected["removed_bytes"] == sum(map(len, before.values())) > 0
        recovered = E.put(store, guard, value)
        assert E.put(store, guard, value) == recovered
        assert store.inventory(guard=guard) == [(value, recovered)]
    assert store.fence.current_epoch() % 2 == 0
    assert store.read(recovered) == value
    if visible:
        assert recovered == visible[0][1]


def _publish_unreferenced(store, guard):
    value = E.event(job="f", body=E.payload(source="abandoned-observer", generation=99))
    with M.store_transaction(store.fence, guard=guard) as ticket:
        MemoryEventStorage(store.root).prepare(M.canonical(value), ticket=ticket)


def test_collection_marks_all_historical_committed_events_before_removing_only_orphans(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    old, recent = E.event(), E.event(job="b", body=E.payload(source="newer-observer", generation=8))
    with store.session() as guard:
        receipts = [E.put(store, guard, value) for value in [old, recent]]
        old_blocks, recent_blocks = [E.blocks(store, receipt) for receipt in receipts]
        assert old_blocks & recent_blocks and old_blocks - recent_blocks
        live = {path: path.read_bytes() for path in old_blocks | recent_blocks}
        _publish_unreferenced(store, guard)
        before = E.physical_files(store.root / "blocks")
        orphaned = set(before) - set(live)
        assert orphaned
        result = store.collect_unreferenced_blocks(guard=guard)
        assert result["removed_blocks"] == len(orphaned)
        assert result["removed_bytes"] == sum(len(before[path]) for path in orphaned)
        assert result["retained_blocks"] == len(live)
        assert E.physical_files(store.root / "blocks") == live
        assert [store.read(receipt) for receipt in receipts] == [old, recent]
        assert store.collect_unreferenced_blocks(guard=guard)["removed_bytes"] == 0
    assert [M.ProjectMemoryStore(tmp_path, read_only=True).read(receipt) for receipt in receipts] == [old, recent]


def test_collection_validates_even_an_excluded_root_before_deleting_any_orphan(tmp_path):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    with store.session() as guard:
        receipt = E.put(store, guard, value)
        _publish_unreferenced(store, guard)
        path = sorted(E.blocks(store, receipt))[0]
        original = path.read_bytes()
        path.write_bytes(b"!" + original[1:])
        before = E.physical_files(store.root / "blocks")
        with pytest.raises((JournalAdapterViolation, ValueError)):
            store.collect_unreferenced_blocks(guard=guard)
        assert E.physical_files(store.root / "blocks") == before
        with pytest.raises(ValueError, match="uninterrupted owner session"):
            store.inventory(kind="STARTED", guard=guard)
        path.write_bytes(original)
    with store.session() as guard:
        assert store.collect_unreferenced_blocks(guard=guard)["removed_blocks"] > 0
        assert store.read(receipt) == value


def test_collection_requires_the_live_writing_owner(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = E.put(store, guard, E.event())
        before = E.physical_files(store.root / "blocks")
        reader = M.ProjectMemoryStore(tmp_path, read_only=True)
        with pytest.raises(TypeError, match="reader cannot"):
            reader.collect_unreferenced_blocks(guard=guard)
    with pytest.raises(JournalAdapterViolation):
        store.collect_unreferenced_blocks(guard=guard)
    assert E.physical_files(store.root / "blocks") == before
    assert store.read(receipt) == E.event()


@pytest.mark.parametrize("damage", ["broken_symlink", "directory", "conflict"])
def test_collection_does_not_delete_before_rejecting_an_invalid_global_inventory(tmp_path, damage):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = E.put(store, guard, E.event())
        _publish_unreferenced(store, guard)
        if damage in {"broken_symlink", "directory"}:
            marker = store.events / receipt["transaction_id"] / "commit-marker.json"
            marker.unlink()
            if damage == "directory":
                marker.mkdir()
            else:
                marker.symlink_to(tmp_path / "missing-marker")
        elif damage == "conflict":
            E.commit_legacy(store, guard, E.event(body={"conflicting": True}))
        before = E.physical_files(store.root / "blocks")
        with pytest.raises((JournalAdapterViolation, ValueError, P.PersistenceViolation)):
            store.collect_unreferenced_blocks(guard=guard)
        assert E.physical_files(store.root / "blocks") == before


@pytest.mark.parametrize("namespace", ["blocks", "transaction"])
def test_parent_directory_sync_failure_prevents_dependent_event_publication(tmp_path, monkeypatch, namespace):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    sync, observed = P._sync_directory, []

    def interrupted(path):
        blocks_created = path == store.root and (store.root / "blocks").exists()
        transaction_created = path == store.events and any(store.events.iterdir())
        interrupted_namespace = blocks_created if namespace == "blocks" else transaction_created
        if interrupted_namespace:
            observed.append(path)
            raise OSError("acceptance directory persistence interrupted")
        return sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(P, "_sync_directory", interrupted)
        with pytest.raises(JournalAdapterViolation):
            with store.session() as guard:
                E.put(store, guard, value)
    assert observed
    assert M.ProjectMemoryStore(tmp_path, read_only=True).inventory() == []
    with store.session() as guard:
        receipt = E.put(store, guard, value)
    assert store.read(receipt) == value


def _interrupt_last_shared_block_sync(store, value, monkeypatch):
    _, encoded = encode_event(M.canonical(value))
    expected = {store.root / "blocks" / (digest + ".bin"): raw for digest, raw in encoded.items()}
    sync, observed = P._sync_directory, []

    def interrupted(path):
        if path == store.root / "blocks" and all(member.exists() for member in expected):
            observed.append(path)
            raise OSError("acceptance final block link was not persisted")
        return sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(P, "_sync_directory", interrupted)
        with pytest.raises(JournalAdapterViolation):
            with store.session() as guard:
                E.put(store, guard, value)
    assert observed
    assert {path: path.read_bytes() for path in expected} == expected
    assert M.ProjectMemoryStore(store.root.parent, read_only=True).inventory() == []
    return expected


@pytest.mark.parametrize("retry_sync", ["succeeds", "fails"])
def test_retry_of_complete_but_unsynced_blocks_must_persist_their_directory_before_commit(tmp_path, monkeypatch,
                                                                                       retry_sync):
    store, value = M.ProjectMemoryStore(tmp_path), E.event()
    expected = _interrupt_last_shared_block_sync(store, value, monkeypatch)
    sync, commit, observed = P._sync_directory, M.commit_snapshot_transaction, []

    def checked_sync(path):
        if path == store.root / "blocks" and retry_sync == "fails":
            observed.append("block_sync_failed")
            raise OSError("acceptance retained block directory is still not persisted")
        result = sync(path)
        if path == store.root / "blocks":
            observed.append("blocks_persisted")
        return result

    def checked_commit(*args, **kwargs):
        assert "blocks_persisted" in observed
        observed.append("event_commit")
        return commit(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(P, "_sync_directory", checked_sync)
        patch.setattr(M, "commit_snapshot_transaction", checked_commit)
        if retry_sync == "fails":
            with pytest.raises(JournalAdapterViolation):
                with store.session() as guard:
                    E.put(store, guard, value)
        else:
            with store.session() as guard:
                receipt = E.put(store, guard, value)
            assert store.read(receipt) == value
    assert {path: path.read_bytes() for path in expected} == expected
    if retry_sync == "fails":
        assert observed == ["block_sync_failed"]
        assert M.ProjectMemoryStore(tmp_path, read_only=True).inventory() == []
    else:
        assert observed.index("blocks_persisted") < observed.index("event_commit")
