"""Actual invocation owner acceptance, lost replies and read-only reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import sqlite3
from uuid import uuid4

import pytest

from acceptance.agents.test_execution_retention_acceptance import ReferenceAdapter, execution
from synapse.agents.policy import AgentExecutionError, AgentFailureCode
from synapse.agents.retention import InvocationStore
from synapse.agents.invocation_commands import InvocationCommands
from synapse.agents.invocation_observation import InvocationObservation
from synapse.commands.binding import CommandBinding, CommandRefused, CommandScope
from synapse.commands.jcs import parameter_hash

SCOPE = CommandScope("operator", "workspace-one")


def test_binding_matches_the_independently_serialized_native_recovery_vector():
    command = {"command_id": "11111111-1111-4111-8111-111111111111",
               "method": "invocation.cancel", "method_version": "1", "recipient": "invocation-one",
               "expected_revision": "a" * 64, "params": {"invocation_id": "invocation-one"}}
    assert CommandBinding.accept(SCOPE, command).digest == "2cff17b5eae19adc6869554c528581e186a4fc908357c16a58a1bc4f9387bdf0"


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", "\u2028", "\0"])
def test_binding_rejects_identifier_and_digest_suffixes(suffix):
    command = {"command_id": str(uuid4()), "method": "invocation.cancel", "method_version": "1",
               "recipient": "invocation-one", "expected_revision": "a" * 64,
               "params": {"invocation_id": "invocation-one"}}
    for changes in [{"recipient": "invocation-one" + suffix,
                     "params": {"invocation_id": "invocation-one" + suffix}},
                    {"expected_revision": "a" * 64 + suffix}]:
        with pytest.raises(CommandRefused, match="^COMMAND_INVALID$"):
            CommandBinding.accept(SCOPE, {**command, **changes})


def pending(tmp_path):
    adapter = ReferenceAdapter(interrupted=True)
    port, request, runtime = execution(tmp_path, (adapter,))
    with pytest.raises(AgentExecutionError) as error:
        port.execute(request=request, runtime=runtime)
    assert error.value.code is AgentFailureCode.EXECUTION_STATE_UNKNOWN
    store = InvocationStore(port.evidence_root)
    command = {"command_id": str(uuid4()), "method": "invocation.cancel", "method_version": "1",
               "recipient": request.invocation_id,
               "expected_revision": InvocationObservation(store).inspect(request.invocation_id)["revision"],
               "params": {"invocation_id": request.invocation_id}}
    return adapter, store, command, request, runtime


def test_lost_reply_reopens_the_owner_and_preserves_terminal_history(tmp_path):
    adapter, store, command, request, runtime = pending(tmp_path)
    receipt = InvocationCommands(store).cancel(SCOPE, command)
    assert receipt["outcome"] == "CANCEL_REQUESTED"
    assert receipt["params_hash"] == parameter_hash(command["params"])
    assert receipt["replayed"] is False
    assert store.is_cancelled(request.invocation_id)
    reader = InvocationStore.open_existing(store.root)
    assert InvocationObservation(reader).inspect(request.invocation_id)["state"] == "EXECUTION_STATE_UNKNOWN"
    assert InvocationCommands(reader).status(SCOPE, command["command_id"], request.invocation_id) == {**receipt, "replayed": True}
    adapter.interrupted = False
    terminal = adapter.execute(request, runtime)
    store.finish(request.invocation_id, terminal)
    with store.connect() as db:
        original = db.execute("SELECT result FROM invocations WHERE id=?", (request.invocation_id,)).fetchone()[0]
    restored = InvocationObservation(reader).inspect(request.invocation_id)
    assert restored["state"] == "TERMINAL"
    assert restored["record"].encode() == original
    assert restored["record_sha256"] == hashlib.sha256(original).hexdigest()
    # A late retry returns the old cancellation result, not current execution
    # state and not a request to execute the invocation again.
    assert InvocationCommands(InvocationStore(store.root)).cancel(SCOPE, command) == {**receipt, "replayed": True}
    assert adapter.calls == 2  # One interrupted dispatch, one explicit owner completion above.
    assert reader.restore(request.invocation_id)[1] == terminal


def test_binding_covers_scope_content_recipient_revision_and_semantic_version(tmp_path):
    _, store, command, _, _ = pending(tmp_path)
    original = CommandBinding.accept(SCOPE, command)
    changed = deepcopy(command)
    changed["params"]["invocation_id"] = changed["recipient"] = "other-invocation"
    assert CommandBinding.accept(SCOPE, changed).digest != original.digest
    assert CommandBinding.accept(CommandScope("operator", "other-workspace"), command).digest != original.digest
    changed["params"]["invocation_id"] = "substitution"
    with pytest.raises(CommandRefused, match="BINDING_CONFLICT"):
        CommandBinding.accept(SCOPE, changed)
    receipt = InvocationCommands(store).cancel(SCOPE, command)
    for scope, changes in [(CommandScope("another-operator", SCOPE.workspace), {}),
                           (CommandScope(SCOPE.principal, "other-workspace"), {}),
                           (SCOPE, {"expected_revision": "f" * 64}),
                           (SCOPE, {"recipient": "other", "params": {"invocation_id": "other"}})]:
        with pytest.raises(CommandRefused, match="BINDING_CONFLICT"):
            InvocationCommands(store).cancel(scope, {**command, **changes})
    for changes in [{"method": "invocation.execute"}, {"method_version": "2"},
                    {"params_hash": "f" * 64}, {"principal": "operator"},
                    {"params": {**command["params"], "secret": "synthetic-secret"}}]:
        with pytest.raises(CommandRefused, match="COMMAND_INVALID"):
            InvocationCommands(store).cancel(SCOPE, {**command, **changes})
    assert InvocationCommands(store).cancel(SCOPE, command) == {**receipt, "replayed": True}


def test_expected_revision_and_unknown_identity_do_not_claim_or_cancel(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    reader = InvocationStore.open_existing(store.root)
    assert InvocationCommands(reader).status(SCOPE, command["command_id"], request.invocation_id) == {
        "schema_version": "synapse.command.status/v1", "state": "ABSENT",
        "command_id": command["command_id"], "principal": SCOPE.principal,
        "workspace": SCOPE.workspace, "owner": "agent.invocations", "recipient": request.invocation_id}
    assert not store.is_cancelled(request.invocation_id)
    assert InvocationObservation(reader).inspect("missing")["state"] == "ABSENT"
    with pytest.raises(CommandRefused, match="REVISION_CONFLICT"):
        InvocationCommands(store).cancel(SCOPE, {**command, "expected_revision": "f" * 64})
    assert InvocationCommands(reader).status(SCOPE, command["command_id"], request.invocation_id)["state"] == "ABSENT"
    assert not store.is_cancelled(request.invocation_id)
    with pytest.raises(CommandRefused, match="OBJECT_UNAVAILABLE"):
        InvocationCommands(store).cancel(SCOPE, {**command, "recipient": "missing", "params": {"invocation_id": "missing"}})
    assert InvocationObservation(reader).inspect("missing")["state"] == "ABSENT"
    with pytest.raises(CommandRefused, match="SCOPE_REFUSED"):
        InvocationCommands(reader).cancel(SCOPE, command)


def test_concurrent_retries_have_one_durable_outcome(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: InvocationCommands(InvocationStore(store.root)).cancel(SCOPE, command), range(8)))
    assert sum(not result["replayed"] for result in results) == 1
    assert len({result["binding_sha256"] for result in results}) == 1
    assert all(result["outcome"] == "CANCEL_REQUESTED" for result in results)
    assert store.is_cancelled(request.invocation_id)


def test_failure_before_receipt_commit_rolls_back_the_effect(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    with store.connect() as db:
        db.executescript("""CREATE TRIGGER reject_receipt BEFORE INSERT ON cancellation_commands
            BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END;""")
    with pytest.raises(sqlite3.DatabaseError):
        InvocationCommands(store).cancel(SCOPE, command)
    assert not store.is_cancelled(request.invocation_id)
    assert InvocationCommands(store).status(SCOPE, command["command_id"], request.invocation_id)["state"] == "ABSENT"
    with store.connect() as db:
        db.execute("DROP TRIGGER reject_receipt")
    assert InvocationCommands(store).cancel(SCOPE, command)["outcome"] == "CANCEL_REQUESTED"


def test_status_cannot_cross_scope_or_trust_a_corrupt_receipt(tmp_path):
    _, store, command, request, _ = pending(tmp_path)
    InvocationCommands(store).cancel(SCOPE, command)
    reader = InvocationStore.open_existing(store.root)
    for scope, target in [(CommandScope("foreign", SCOPE.workspace), request.invocation_id),
                          (CommandScope(SCOPE.principal, "foreign"), request.invocation_id), (SCOPE, "foreign")]:
        with pytest.raises(CommandRefused, match="SCOPE_REFUSED"):
            InvocationCommands(reader).status(scope, command["command_id"], target)
    with store.connect() as db:
        db.execute("UPDATE cancellation_commands SET result=?", (b'{}',))
    with pytest.raises(CommandRefused, match="RECORD_INVALID"):
        InvocationCommands(reader).status(SCOPE, command["command_id"], request.invocation_id)
    with pytest.raises(CommandRefused, match="RECORD_INVALID"):
        InvocationCommands(store).cancel(SCOPE, command)


def test_reading_never_initializes_missing_or_legacy_stores(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(CommandRefused, match="OBJECT_UNAVAILABLE"):
        InvocationStore.open_existing(missing)
    assert not missing.exists()
    root = tmp_path / "legacy"
    root.mkdir()
    database = root / "executions.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE invocations (id TEXT PRIMARY KEY, binding BLOB, state TEXT, result BLOB, result_sha256 TEXT, cancelled INTEGER)")
    before = database.read_bytes()
    reader = InvocationStore.open_existing(root)
    assert InvocationCommands(reader).status(SCOPE, str(uuid4()), "missing")["state"] == "ABSENT"
    assert InvocationObservation(reader).inspect("missing")["state"] == "ABSENT"
    assert database.read_bytes() == before
    assert {p.name for p in root.iterdir()} == {"executions.sqlite3"}


@pytest.mark.parametrize("command_id,recipient", [("invalid", "invocation"),
    (str(uuid4()), ""), (str(uuid4()), "x" * 193), ([], "invocation"), (str(uuid4()), {}),
    (str(uuid4()), "invocation\n"), (str(uuid4()), "invocation\r"), (str(uuid4()), "invocation\u2028")])
def test_invalid_reconciliation_queries_are_refused_before_reading(tmp_path, command_id, recipient):
    store = InvocationStore(tmp_path / "owner")
    # Removing the journal makes an attempted SQL lookup fail. Invalid queries
    # must instead be rejected by the public status contract without recreating it.
    reader = InvocationStore.open_existing(store.root)
    store.database.unlink()
    with pytest.raises(CommandRefused, match="^COMMAND_INVALID$"):
        InvocationCommands(reader).status(SCOPE, command_id, recipient)
    assert not store.database.exists()
