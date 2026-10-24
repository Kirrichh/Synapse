"""Owner state, command receipts and observation history share durable boundaries."""
import sqlite3

import pytest

from acceptance.ui_bridge.test_invocation_commands_acceptance import SCOPE, pending
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.agents.retention import InvocationStore


@pytest.mark.parametrize("operation", ["claim", "cancel", "command", "finish"])
def test_failed_projection_publication_rolls_back_the_owner_change(tmp_path, operation):
    adapter, store, command, request, runtime = pending(tmp_path)
    observer = InvocationObservation(InvocationStore.open_existing(store.root))
    identities = (request.invocation_id, "new-invocation")
    before = observer.snapshot(identities)
    with store.connect() as db:
        db.executescript("""CREATE TRIGGER reject_projection BEFORE INSERT ON observation_changes
            BEGIN SELECT RAISE(ABORT, 'synthetic publication failure'); END;""")
    adapter.interrupted = False
    result = adapter.execute(request, runtime)
    actions = {
        "claim": lambda: store.claim("new-invocation", b'{}'),
        "cancel": lambda: store.cancel(request.invocation_id),
        "command": lambda: InvocationCommands(store).cancel(SCOPE, command),
        "finish": lambda: store.finish(request.invocation_id, result),
    }
    with pytest.raises(sqlite3.DatabaseError):
        actions[operation]()
    assert observer.snapshot(identities) == before
    assert not store.is_cancelled(request.invocation_id)
    assert observer.inspect(request.invocation_id)["state"] == "EXECUTION_STATE_UNKNOWN"
    assert observer.inspect("new-invocation")["state"] == "ABSENT"
    assert InvocationCommands(store).status(SCOPE, command["command_id"], request.invocation_id)["state"] == "ABSENT"
    with store.connect() as db:
        db.execute("DROP TRIGGER reject_projection")
    actions[operation]()
    assert observer.snapshot(identities)["position"] == before["position"] + 1


def test_read_only_store_cannot_mutate_through_any_owner(tmp_path):
    adapter, store, command, request, runtime = pending(tmp_path)
    reader = InvocationStore.open_existing(store.root)
    observer = InvocationObservation(reader)
    before = observer.snapshot((request.invocation_id, "new-invocation"))
    adapter.interrupted = False
    result = adapter.execute(request, runtime)
    for action in (lambda: reader.claim("new-invocation", b'{}'),
                   lambda: reader.cancel(request.invocation_id),
                   lambda: reader.finish(request.invocation_id, result),
                   lambda: reader.retain(b"synthetic bytes"),
                   lambda: reader.event(request.invocation_id, {"kind": "synthetic"})):
        with pytest.raises(sqlite3.DatabaseError):
            action()
    assert observer.snapshot((request.invocation_id, "new-invocation")) == before
    assert InvocationCommands(reader).status(SCOPE, command["command_id"], request.invocation_id)["state"] == "ABSENT"
