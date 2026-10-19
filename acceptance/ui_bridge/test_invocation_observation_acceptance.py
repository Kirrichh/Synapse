"""The invocation owner publishes complete, consistent StateSync bases."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
from threading import Event

import pytest

from synapse.agents.retention import InvocationStore
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.commands.binding import CommandRefused
from acceptance.ui_bridge.test_invocation_commands_acceptance import SCOPE, pending


def test_subscribe_basis_snapshot_and_filtered_progress_share_owner_order(tmp_path):
    owner = InvocationStore(tmp_path / "retained")
    reader = InvocationStore.open_existing(owner.root)
    initial = InvocationObservation(reader).snapshot(("one", "two"))
    assert initial["position"] == 0
    assert all(item["state"] == "ABSENT" for item in initial["items"])
    owner.claim("one", b'{"request":"one"}')
    owner.claim("other", b'{"request":"other"}')
    snapshot = InvocationObservation(reader).snapshot(("one", "two"))
    assert snapshot["position"] == 2
    assert snapshot["generation"] == initial["generation"]
    assert snapshot["items"][0]["state"] == "EXECUTION_STATE_UNKNOWN"
    assert snapshot["items"][1]["state"] == "ABSENT"
    prefix = InvocationObservation(reader).after(initial["generation"], 0, ("one", "two"))
    assert (prefix["from"], prefix["to"]) == (0, 2)
    assert [value["position"] for value in prefix["changes"]] == [1]
    assert prefix["changes"][0]["item"] == snapshot["items"][0]
    quiet = InvocationObservation(reader).after(initial["generation"], 2, ("one",))
    assert quiet["from"] == quiet["to"] == 2
    assert quiet["changes"] == []
    owner.cancel("one")
    delta = InvocationObservation(reader).after(initial["generation"], 2, ("one",))
    assert delta["to"] == 3
    assert delta["changes"][0]["item"]["cancel_requested"]
    owner.cancel("one")
    assert InvocationObservation(reader).snapshot(("one",))["position"] == 3


def test_command_and_projection_commit_or_rollback_together(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    reader = InvocationStore.open_existing(store.root)
    before = InvocationObservation(reader).snapshot((request.invocation_id,))
    with store.connect() as db:
        db.executescript("""CREATE TRIGGER fail_receipt BEFORE INSERT ON cancellation_commands
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;""")
    with pytest.raises(sqlite3.DatabaseError):
        InvocationCommands(store).cancel(SCOPE, command)
    assert InvocationObservation(reader).snapshot((request.invocation_id,)) == before
    with store.connect() as db:
        db.execute("DROP TRIGGER fail_receipt")
    InvocationCommands(store).cancel(SCOPE, command)
    after = InvocationObservation(reader).snapshot((request.invocation_id,))
    assert after["position"] == before["position"] + 1
    InvocationCommands(store).cancel(SCOPE, command)
    assert InvocationObservation(reader).snapshot((request.invocation_id,)) == after


def test_snapshot_position_and_items_are_one_sqlite_read_transaction(tmp_path, monkeypatch):
    owner = InvocationStore(tmp_path / "retained")
    reader = InvocationStore.open_existing(owner.root)
    at_items, committed = Event(), Event()
    connect = reader.connect

    @contextmanager
    def pause_before_items():
        with connect() as db:
            def observe_sql(sql):
                if sql.startswith("SELECT binding,state,result,result_sha256,cancelled"):
                    at_items.set()
                    committed.wait(5)
            db.set_trace_callback(observe_sql)
            yield db

    def write():
        assert at_items.wait(5), "snapshot never reached the read barrier"
        owner.claim("one", b'{}')
        committed.set()

    monkeypatch.setattr(reader, "connect", pause_before_items)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(write)
        snapshot = InvocationObservation(reader).snapshot(("one",))
        running.result()
    assert committed.is_set(), "the competing commit did not occur inside the read"
    assert snapshot["position"] == 0
    assert snapshot["items"] == [{"invocation_id": "one", "state": "ABSENT"}]
    fresh = InvocationObservation(reader).snapshot(("one",))
    assert fresh["position"] == 1
    assert fresh["items"][0]["state"] == "EXECUTION_STATE_UNKNOWN"


def test_missing_coverage_and_foreign_generation_never_advance_progress(tmp_path):
    owner = InvocationStore(tmp_path / "retained")
    owner.claim("one", b'{}')
    snapshot = InvocationObservation(owner).snapshot(("one",))
    for generation, position in [("foreign", 0), (snapshot["generation"], 2), (snapshot["generation"], -1)]:
        with pytest.raises(CommandRefused, match="CURSOR_UNAVAILABLE"):
            InvocationObservation(owner).after(generation, position, ("one",))
    with owner.connect() as db:
        db.execute("DELETE FROM observation_changes WHERE revision=1")
    with pytest.raises(CommandRefused, match="RECORD_INVALID"):
        InvocationObservation(owner).after(snapshot["generation"], 0, ("one",))
    for identities in [(), ("one", "one"), tuple(str(index) for index in range(33))]:
        with pytest.raises(CommandRefused, match="COMMAND_INVALID"):
            InvocationObservation(owner).snapshot(identities)
