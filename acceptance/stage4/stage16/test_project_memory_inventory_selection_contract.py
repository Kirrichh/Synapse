"""A selected journal view preserves the memory owner's custody contract."""
from copy import deepcopy

import pytest

from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.admission_journal import JournalAdapterViolation
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.memory_consolidation import records
from synapse.memory_consolidation.owner import MemoryOwner


def _put(store, guard, *, kind="REQUESTED", job="a", payload=None):
    return store.put(kind=kind, job_key=job * 64, guard=guard,
                     payload={"items": [{"value": "original"}]} if payload is None else payload)


def _view(store, guard, mode, kind):
    if mode == "read_only":
        return M.ProjectMemoryStore(store.root.parent, read_only=True).inventory(kind=kind)
    return store.inventory(kind=kind, guard=guard if mode == "guarded" else None)


@pytest.mark.parametrize("mode", ["guarded", "unguarded", "read_only"])
def test_selection_equals_full_inventory_filtered_after_read(tmp_path, mode):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        for kind in sorted(M.EVENT_KINDS, reverse=True):
            _put(store, guard, kind=kind, payload={"kind": kind, "nested": [{"value": 7}]})
        full = store.inventory(guard=guard)
        assert _view(store, guard, mode, None) == full
        assert [receipt["transaction_id"] for _, receipt in full] == sorted(
            receipt["transaction_id"] for _, receipt in full)
        for kind in sorted(M.EVENT_KINDS):
            assert _view(store, guard, mode, kind) == [pair for pair in full if pair[0]["kind"] == kind]


@pytest.mark.parametrize("mode", ["guarded", "unguarded", "read_only"])
def test_empty_selection_does_not_change_the_complete_inventory(tmp_path, mode):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        _put(store, guard)
        before = store.inventory(guard=guard)
        assert _view(store, guard, mode, "STARTED") == []
        assert _view(store, guard, mode, None) == before


@pytest.mark.parametrize("kind", ["", "requested", "*", 1, True, [], {}, {"REQUESTED"}])
def test_kind_requires_one_exact_known_event_name(tmp_path, kind):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        for mode in ("guarded", "unguarded", "read_only"):
            with pytest.raises(ValueError):
                _view(store, guard, mode, kind)


@pytest.mark.parametrize("mode", ["guarded", "unguarded", "read_only"])
def test_selected_records_remain_detached_from_every_callers_data(tmp_path, mode):
    store = M.ProjectMemoryStore(tmp_path)
    payload = {"items": [{"value": "original"}]}
    with store.session() as guard:
        receipt = _put(store, guard, payload=payload)
        _put(store, guard, kind="MEMORY_STATE")
        expected = store.inventory(guard=guard)
        saved_receipt = deepcopy(receipt)
        payload["items"][0]["value"] = "changed input"
        receipt["ref"].clear()
        selected = _view(store, guard, mode, "REQUESTED")
        selected[0][0]["payload"]["items"][0]["value"] = "changed result"
        selected[0][1]["ref"].clear()
        selected.clear()
        assert store.inventory(guard=guard) == expected
        assert _view(store, guard, mode, "REQUESTED") == [
            pair for pair in expected if pair[0]["kind"] == "REQUESTED"]
        assert store.read(saved_receipt)["payload"] == {"items": [{"value": "original"}]}


def test_selection_observes_new_commits_without_changing_other_kinds(tmp_path):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        _put(store, guard, kind="MEMORY_STATE")
        unrelated = store.inventory(kind="MEMORY_STATE", guard=guard)
        assert store.inventory(kind="REQUESTED", guard=guard) == []
        first = _put(store, guard)
        first_view = store.inventory(kind="REQUESTED", guard=guard)
        assert [receipt for _, receipt in first_view] == [first]
        second = _put(store, guard, job="b")
        _put(store, guard, kind="MEMORY_TRIAL")
        selected = store.inventory(kind="REQUESTED", guard=guard)
        assert [receipt["transaction_id"] for _, receipt in selected] == sorted(
            receipt["transaction_id"] for receipt in [first, second])
        assert len(first_view) == 1
        assert store.inventory(kind="MEMORY_STATE", guard=guard) == unrelated


def test_even_an_empty_selection_requires_a_live_guard_of_this_owner_session(tmp_path):
    (tmp_path / "owner").mkdir()
    (tmp_path / "other").mkdir()
    store = M.ProjectMemoryStore(tmp_path / "owner")
    other = M.ProjectMemoryStore(tmp_path / "other")
    with store.session() as retired:
        _put(store, retired)
    with pytest.raises(JournalAdapterViolation):
        store.inventory(kind="STARTED", guard=retired)
    with store.session() as guard, other.session() as foreign:
        with pytest.raises(JournalAdapterViolation):
            store.inventory(kind="STARTED", guard=retired)
        with pytest.raises(JournalAdapterViolation):
            store.inventory(kind="STARTED", guard=foreign)
        same_coordinator = M.ProjectMemoryStore(tmp_path / "owner")
        with pytest.raises(ValueError, match="uninterrupted owner session"):
            same_coordinator.inventory(kind="STARTED", guard=guard)
        assert store.inventory(kind="STARTED", guard=guard) == []


@pytest.mark.parametrize("failure_point", ["before_commit", "after_commit", "readback"])
def test_failed_mutation_refuses_even_a_selection_without_matches(tmp_path, monkeypatch, failure_point):
    store = M.ProjectMemoryStore(tmp_path)
    commit = M.commit_snapshot_transaction

    def interrupted_commit(*args, **kwargs):
        if failure_point == "after_commit":
            commit(*args, **kwargs)
        raise RuntimeError("acceptance interruption at event commit")

    def unavailable_readback(transaction):
        raise OSError("acceptance readback unavailable")

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
            store.inventory(kind="STARTED", guard=guard)
    with store.session() as guard:
        assert store.inventory(kind="STARTED", guard=guard) == []


@pytest.mark.parametrize("mode", ["unguarded", "read_only", "fresh_session"])
def test_excluded_corrupt_event_still_prevents_a_trusted_inventory(tmp_path, mode):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipt = _put(store, guard)
    member = store.events / receipt["transaction_id"] / "event.json"
    member.write_bytes(member.read_bytes().replace(b"original", b"modified"))
    with pytest.raises(PersistenceViolation):
        if mode == "fresh_session":
            with store.session() as guard:
                store.inventory(kind="STARTED", guard=guard)
        else:
            _view(store, None, mode, "STARTED")


def _commit_conflicting_event(store, guard):
    event = {"schema_version": M.MEMORY_EVENT_V1, "kind": "REQUESTED",
             "job_key": "a" * 64, "payload": {"conflicting": "original"}}
    raw, transaction = records.canonical(event), M.new_operation_id()
    with M.store_transaction(store.fence, guard=guard) as ticket:
        members = M.stage_snapshot_transaction(store.events, transaction_id=transaction,
            members={"event.json": raw}, maximum_bytes=len(raw), ticket=ticket)
        M.commit_snapshot_transaction(store.events, transaction_id=transaction, members=members,
            boundary_id=event["job_key"], marker_sha256=M.hashlib.sha256(raw).hexdigest(), ticket=ticket)


@pytest.mark.parametrize("mode", ["unguarded", "read_only", "fresh_session"])
def test_selection_preserves_global_inventory_conflict_checks(tmp_path, mode):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        _put(store, guard)
        _commit_conflicting_event(store, guard)
    with pytest.raises(ValueError, match="conflicting immutable results"):
        if mode == "fresh_session":
            with store.session() as guard:
                store.inventory(kind="STARTED", guard=guard)
        else:
            _view(store, None, mode, "STARTED")


def _payload_copies(value):
    if type(value) is dict:
        if value.get("schema_version") == M.MEMORY_EVENT_V1:
            yield value["kind"], len(records.canonical(value["payload"]))
        else:
            for child in value.values():
                yield from _payload_copies(child)
    elif type(value) in (list, tuple):
        for child in value:
            yield from _payload_copies(child)


def _observe_payload_copies(monkeypatch, copied):
    original = M.deepcopy

    def measured(value, *args, **kwargs):
        copied.extend(_payload_copies(value))
        return original(value, *args, **kwargs)

    monkeypatch.setattr(M, "deepcopy", measured)


def test_held_selection_copies_only_the_requested_payloads(tmp_path, monkeypatch):
    store = M.ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        _put(store, guard, payload={"requested": [1, 2, 3]})
        _put(store, guard, kind="MEMORY_STATE", payload={"state": [{"value": i} for i in range(1024)]})
        expected = [pair for pair in store.inventory(guard=guard) if pair[0]["kind"] == "REQUESTED"]
        copied = []
        _observe_payload_copies(monkeypatch, copied)
        assert store.inventory(kind="REQUESTED", guard=guard) == expected
        assert {kind for kind, _ in copied} <= {"REQUESTED"}
        assert sum(size for _, size in copied) <= len(records.canonical(expected[0][0]["payload"]))
        copied.clear()
        assert store.inventory(kind="STARTED", guard=guard) == []
        assert copied == []


def _owner_payloads(identity):
    boundary = records.make("snapshot_boundary", boundary={"consolidation_id": "cons_" + "a" * 64})
    return {
        "MEMORY_BOUND": {"project_identity": identity, "configuration_sha256": "b" * 64},
        "SESSION_OPENED": {"project_identity": identity, "run_id": "run-selection"},
        "MEMORY_RETENTION": {"project_identity": identity, "sequence": 0, "acts": []},
        "MEMORY_TRIAL": {"project_identity": identity, "trial": {"id": "trial-selection"}},
        "SNAPSHOT_BOUNDARY": {"boundary": boundary},
    }


def test_owner_readers_do_not_copy_unrelated_journal_payloads(tmp_path, monkeypatch):
    (tmp_path / "project.json").write_text('{"project":"inventory-selection-acceptance"}', encoding="utf-8")
    owner = MemoryOwner(tmp_path)
    payloads = _owner_payloads(owner.identity)
    boundary = payloads["SNAPSHOT_BOUNDARY"]["boundary"]
    readers = [
        ("bound_digest", "MEMORY_BOUND", "b" * 64),
        ("sessions", "SESSION_OPENED", [payloads["SESSION_OPENED"]]),
        ("retention_passes", "MEMORY_RETENTION", [payloads["MEMORY_RETENTION"]]),
        ("trials", "MEMORY_TRIAL", [payloads["MEMORY_TRIAL"]["trial"]]),
        ("boundaries", "SNAPSHOT_BOUNDARY", {boundary["boundary"]["consolidation_id"]: boundary}),
    ]
    with owner.store.session() as guard:
        for kind, payload in payloads.items():
            _put(owner.store, guard, kind=kind, payload=payload)
        _put(owner.store, guard, kind="MEMORY_STATE", payload={"state": [{"value": i} for i in range(1024)]})
        copied = []
        _observe_payload_copies(monkeypatch, copied)
        for method, kind, expected in readers:
            copied.clear()
            assert getattr(owner, method)(guard=guard) == expected
            assert {event_kind for event_kind, _ in copied} <= {kind}
            assert sum(size for _, size in copied) <= len(records.canonical(payloads[kind]))
