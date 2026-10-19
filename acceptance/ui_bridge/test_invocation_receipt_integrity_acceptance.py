"""Retained command replay validates both record integrity and the lookup identity."""
import hashlib
from uuid import uuid4

import pytest

from acceptance.ui_bridge.test_invocation_commands_acceptance import SCOPE, pending
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.agents.retention import InvocationStore
from synapse.commands.binding import CommandRefused
from synapse.commands.jcs import canonical_bytes, decode


@pytest.mark.parametrize("replacement", [
    {"command_id": str(uuid4())}, {"invocation_id": "another-invocation"},
    {"params_hash": "f" * 64}, {"binding_sha256": "f" * 64},
    {"schema_version": "unsupported"}, {"state": "ACCEPTED"},
    {"outcome": "TASK_STOPPED"}, {"unexpected": "synthetic-private-value"},
])
def test_valid_digest_cannot_replay_a_result_for_other_content(tmp_path, replacement):
    _, store, command, request, _ = pending(tmp_path)
    commands = InvocationCommands(store)
    receipt = commands.cancel(SCOPE, command)
    altered = {key: value for key, value in receipt.items() if key != "replayed"}
    altered.update(replacement)
    raw = canonical_bytes(altered)
    with store.connect() as db:
        db.execute("UPDATE cancellation_commands SET result=?,result_sha256=?",
                   (raw, hashlib.sha256(raw).hexdigest()))
    reader = InvocationCommands(InvocationStore.open_existing(store.root))
    for read in (lambda: reader.status(SCOPE, command["command_id"], request.invocation_id),
                 lambda: commands.cancel(SCOPE, command)):
        with pytest.raises(CommandRefused, match="^RECORD_INVALID$"):
            read()
    assert store.is_cancelled(request.invocation_id)


@pytest.mark.parametrize("raw", [b'[]', b'null', b'{"secret":"synthetic-private-value"}', b'{', "not-a-blob"])
def test_malformed_retained_receipt_has_a_declared_nonsecret_refusal(tmp_path, raw):
    _, store, command, request, _ = pending(tmp_path)
    InvocationCommands(store).cancel(SCOPE, command)
    with store.connect() as db:
        db.execute("UPDATE cancellation_commands SET result=?,result_sha256=?",
                   (raw, hashlib.sha256(raw if type(raw) is bytes else raw.encode()).hexdigest()))
    with pytest.raises(CommandRefused, match="^RECORD_INVALID$"):
        InvocationCommands(InvocationStore.open_existing(store.root)).status(SCOPE, command["command_id"], request.invocation_id)


@pytest.mark.parametrize("replacement", [{"owner": "other-owner"}, {"params_hash": "f" * 64},
                                         {"params": {"invocation_id": "other"}}, {"unknown": True}])
def test_status_revalidates_retained_binding_instead_of_trusting_its_fields(tmp_path, replacement):
    _, store, command, request, _ = pending(tmp_path)
    InvocationCommands(store).cancel(SCOPE, command)
    with store.connect() as db:
        raw = db.execute("SELECT binding FROM cancellation_commands").fetchone()[0]
        db.execute("UPDATE cancellation_commands SET binding=?", (canonical_bytes({**decode(raw), **replacement}),))
    with pytest.raises(CommandRefused, match="^RECORD_INVALID$"):
        InvocationCommands(InvocationStore.open_existing(store.root)).status(SCOPE, command["command_id"], request.invocation_id)


def test_changed_but_well_formed_historical_outcome_still_requires_its_retained_digest(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    owner = InvocationCommands(store)
    owner.cancel(SCOPE, command)
    with store.connect() as db:
        raw = db.execute("SELECT result FROM cancellation_commands").fetchone()[0]
        changed = {**decode(raw), "outcome": "ALREADY_TERMINAL"}
        db.execute("UPDATE cancellation_commands SET result=?", (canonical_bytes(changed),))
    reader = InvocationCommands(InvocationStore.open_existing(store.root))
    for read in (lambda: reader.status(SCOPE, command["command_id"], request.invocation_id),
                 lambda: owner.cancel(SCOPE, command)):
        with pytest.raises(CommandRefused, match="^RECORD_INVALID$"):
            read()
    assert store.is_cancelled(request.invocation_id)


def test_terminal_read_reopens_the_owners_evidence_before_returning_original_record(tmp_path):
    adapter, store, _, request, runtime = pending(tmp_path)
    adapter.interrupted = False
    result = adapter.execute(request, runtime)
    store.finish(request.invocation_id, result)
    reader = InvocationObservation(InvocationStore.open_existing(store.root))
    retained = reader.inspect(request.invocation_id)
    assert retained["state"] == "TERMINAL"
    with store.connect() as db:
        original = db.execute("SELECT payload FROM artifacts WHERE sha256=?", (result.outputs[0].sha256,)).fetchone()[0]
        db.execute("DELETE FROM artifacts WHERE sha256=?", (result.outputs[0].sha256,))
    with pytest.raises(CommandRefused, match="^RECORD_INVALID$"):
        reader.inspect(request.invocation_id)
    with store.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?)", (result.outputs[0].sha256, original))
    assert reader.inspect(request.invocation_id) == retained
    assert adapter.calls == 2
