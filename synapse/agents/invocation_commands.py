"""Durable cancellation acceptance and reconciliation owned by agent invocations.

The receipt and cooperative cancellation flag share the invocation transaction.
Reading receipts never initializes a store or starts an execution world.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from synapse.commands.binding import CommandBinding, CommandRefused, CommandScope
from synapse.commands.jcs import InvalidCommandValue, canonical_bytes as command_bytes, decode as command_value
from synapse.commands.reconciliation import status_query

from .invocation_observation import invocation_revision, publish_change

if TYPE_CHECKING:
    from .retention import InvocationStore


def initialize_commands(db) -> None:
    """Called only by the writable invocation owner during store creation."""
    db.execute("""CREATE TABLE IF NOT EXISTS cancellation_commands (
        command_id TEXT PRIMARY KEY, binding BLOB NOT NULL,
        result BLOB NOT NULL, result_sha256 TEXT NOT NULL)""")


class InvocationCommands:
    def __init__(self, store: InvocationStore):
        self.store = store

    @staticmethod
    def _cancellation_result(row, binding: CommandBinding, *, replayed: bool) -> dict:
        if type(row[1]) is not bytes or hashlib.sha256(row[1]).hexdigest() != row[2]:
            raise CommandRefused("RECORD_INVALID")
        try:
            result = command_value(row[1])
        except InvalidCommandValue as exc:
            raise CommandRefused("RECORD_INVALID") from exc
        document = binding.document()
        if type(result) is not dict or result.get("outcome") not in ("CANCEL_REQUESTED", "ALREADY_TERMINAL"):
            raise CommandRefused("RECORD_INVALID")
        expected = {"schema_version": "synapse.invocation.cancel-result/v1",
                    "command_id": binding.command_id, "binding_sha256": binding.digest,
                    "params_hash": document["params_hash"], "state": "COMPLETED",
                    "outcome": result["outcome"], "invocation_id": document["recipient"]}
        if result != expected:
            raise CommandRefused("RECORD_INVALID")
        return {**result, "replayed": replayed}

    def cancel(self, scope: CommandScope, command: dict) -> dict:
        """Atomically retain this owner's command outcome and cancellation flag.

        Acceptance requests cooperative cancellation only. It does not establish
        that a process, a whole task, or a remote effect has stopped. Receipts are
        retained without TTL; an old command ID never becomes a new command.
        """
        if self.store.read_only:
            raise CommandRefused("SCOPE_REFUSED")
        binding = CommandBinding.accept(scope, command)
        document = binding.document()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT binding,result,result_sha256 FROM cancellation_commands WHERE command_id=?",
                                  (binding.command_id,)).fetchone()
            if existing is not None:
                if existing[0] != binding.body:
                    raise CommandRefused("BINDING_CONFLICT")
                return self._cancellation_result(existing, binding, replayed=True)
            row = db.execute("SELECT binding,state,result,result_sha256,cancelled FROM invocations WHERE id=?",
                             (document["recipient"],)).fetchone()
            if row is None:
                raise CommandRefused("OBJECT_UNAVAILABLE")
            if invocation_revision(row) != document["expected_revision"]:
                raise CommandRefused("REVISION_CONFLICT")
            if row[1] not in ("CLAIMED", "TERMINAL"):
                raise CommandRefused("RECORD_INVALID")
            requested = row[1] == "CLAIMED"
            if requested:
                db.execute("UPDATE invocations SET cancelled=1 WHERE id=? AND state='CLAIMED'",
                           (document["recipient"],))
                if not row[4]:
                    publish_change(db, document["recipient"])
            result = {"schema_version": "synapse.invocation.cancel-result/v1",
                      "command_id": binding.command_id, "binding_sha256": binding.digest,
                      "params_hash": document["params_hash"], "state": "COMPLETED",
                      "outcome": "CANCEL_REQUESTED" if requested else "ALREADY_TERMINAL",
                      "invocation_id": document["recipient"]}
            raw = command_bytes(result)
            db.execute("INSERT INTO cancellation_commands VALUES(?,?,?,?)",
                       (binding.command_id, binding.body, raw, hashlib.sha256(raw).hexdigest()))
        # The transaction has committed before an acceptance/result is returned.
        return {**result, "replayed": False}

    def status(self, scope: CommandScope, command_id: str, recipient: str) -> dict:
        """Read-only reconciliation within the caller's already authorized scope."""
        if type(scope) is not CommandScope:
            raise CommandRefused("SCOPE_REFUSED")
        status_query(command_id, recipient)
        with self.store.connect() as db:
            # A legacy invocation store has no UI command receipts. Do not
            # initialize it during a read merely to answer this lookup.
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cancellation_commands'").fetchone()
            row = (db.execute("SELECT binding,result,result_sha256 FROM cancellation_commands WHERE command_id=?",
                              (command_id,)).fetchone() if table else None)
        if row is None:
            return {"schema_version": "synapse.command.status/v1", "state": "ABSENT",
                    "command_id": command_id, "owner": "agent.invocations",
                    "principal": scope.principal, "workspace": scope.workspace,
                    "recipient": recipient}
        try:
            document = command_value(row[0])
            retained_scope = CommandScope(document["principal"], document["workspace"])
            command = {key: document[key] for key in ("method", "method_version", "recipient", "expected_revision", "params")}
            binding = CommandBinding.accept(retained_scope, {**command, "command_id": command_id})
            if binding.body != row[0]:
                raise CommandRefused("RECORD_INVALID")
        except (InvalidCommandValue, CommandRefused, KeyError, TypeError) as exc:
            raise CommandRefused("RECORD_INVALID") from exc
        if (retained_scope, document["recipient"]) != (scope, recipient):
            raise CommandRefused("SCOPE_REFUSED")
        return self._cancellation_result(row, binding, replayed=True)
