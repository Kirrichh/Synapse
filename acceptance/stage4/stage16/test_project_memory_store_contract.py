"""Small durable-memory contracts; all writes are local acceptance records."""
import pytest

from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.admission_journal import JournalAdapterViolation, JournalAdapterFailureCode


def put(store, guard, *, payload=None):
    return store.put(kind="REQUESTED", job_key="a" * 64,
        payload={"task": "maintain-frame"} if payload is None else payload, guard=guard)


@pytest.mark.parametrize("committed", [False, True])
def test_interrupted_event_recovers_only_valid_visibility_and_never_repeats_a_committed_write(tmp_path, monkeypatch, committed):
    store = M.ProjectMemoryStore(tmp_path)
    commit = M.commit_snapshot_transaction

    def interrupted(*args, **kwargs):
        if committed:
            commit(*args, **kwargs)
        raise RuntimeError("acceptance interruption at the event commit boundary")

    monkeypatch.setattr(M, "commit_snapshot_transaction", interrupted)
    with pytest.raises(JournalAdapterViolation) as failure:
        with store.session() as guard:
            put(store, guard)
    assert failure.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    assert isinstance(failure.value.__cause__, RuntimeError)
    assert "event commit boundary" in str(failure.value.__cause__)
    assert store.fence.current_epoch() % 2 == 1
    visible = M.ProjectMemoryStore(tmp_path, read_only=True).inventory()
    assert len(visible) == int(committed)
    original = {path: path.read_bytes() for path in store.events.rglob("*") if path.is_file()}
    monkeypatch.setattr(M, "commit_snapshot_transaction", commit)
    with store.session() as guard:
        recovered = put(store, guard)
        assert put(store, guard) == recovered
    assert store.fence.current_epoch() % 2 == 0
    assert len(store.inventory()) == 1
    assert {path: path.read_bytes() for path in original} == original
    if committed:
        assert recovered == visible[0][1]
    assert store.read(recovered)["payload"] == {"task": "maintain-frame"}


def test_a_completed_logical_transition_keeps_its_original_payload(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = put(store, guard)
        with pytest.raises(ValueError, match="cannot be rewritten"):
            put(store, guard, payload={"task": "different-frame"})
    assert len(store.inventory()) == 1
    assert store.read(receipt)["payload"] == {"task": "maintain-frame"}


def test_read_only_memory_has_no_write_operation(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = put(store, guard)
        reader = M.ProjectMemoryStore(tmp_path, read_only=True)
        assert reader.read(receipt) == store.read(receipt)
        with pytest.raises(TypeError, match="reader cannot"):
            put(reader, guard)


def test_event_budget_refuses_new_work_before_writing_and_keeps_idempotent_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "MAX_MEMORY_EVENTS", 1)
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = put(store, guard)
        assert put(store, guard) == receipt
        with pytest.raises(ValueError, match="event budget"):
            store.put(kind="STARTED", job_key="a" * 64, payload={"request_ref": receipt}, guard=guard)
    assert len(store.inventory()) == 1
