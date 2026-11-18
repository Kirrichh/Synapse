"""Read-only invocation projections and their transaction-bound change journal.

The journal describes owner state; it neither accepts commands nor redispatches
executions. Its bounded history may require a fresh snapshot, while retained
invocation results and command receipts remain durable.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from uuid import uuid4

from synapse.commands.binding import CommandRefused
from synapse.commands.jcs import canonical_bytes as command_bytes, decode as command_value

from .policy import AgentExecutionError

if TYPE_CHECKING:
    from .retention import InvocationStore


def initialize_observation(db) -> None:
    """Called only by the writable invocation owner during store creation."""
    db.execute("""CREATE TABLE IF NOT EXISTS observation_meta (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT NOT NULL,
        revision INTEGER NOT NULL, floor INTEGER NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS observation_changes (
        revision INTEGER PRIMARY KEY, payload BLOB NOT NULL)""")
    db.execute("INSERT OR IGNORE INTO observation_meta VALUES(1,?,0,0)", (str(uuid4()),))


def invocation_revision(row) -> str:
    # The revision binds the owner's actual state, including a cancellation
    # requested by another caller. It is not an execution identity or lease.
    return hashlib.sha256(command_bytes({"binding_sha256": hashlib.sha256(row[0]).hexdigest(),
        "state": row[1], "result_sha256": row[3], "cancel_requested": bool(row[4])})).hexdigest()


def _observed_item(invocation_id, row) -> dict:
    if row[1] not in ("CLAIMED", "TERMINAL"):
        raise CommandRefused("RECORD_INVALID")
    if row[1] == "TERMINAL" and hashlib.sha256(row[2]).hexdigest() != row[3]:
        raise CommandRefused("RECORD_INVALID")
    return {"invocation_id": invocation_id, "revision": invocation_revision(row),
            "state": "TERMINAL" if row[1] == "TERMINAL" else "EXECUTION_STATE_UNKNOWN",
            "cancel_requested": bool(row[4]), "result_sha256": row[3]}


def publish_change(db, invocation_id: str) -> None:
    """Publish a projection in the same owner transaction as its state change."""
    revision = db.execute("SELECT revision FROM observation_meta WHERE singleton=1").fetchone()[0] + 1
    if revision > 9007199254740991:
        raise CommandRefused("RECORD_INVALID")
    row = db.execute("SELECT binding,state,result,result_sha256,cancelled FROM invocations WHERE id=?",
                     (invocation_id,)).fetchone()
    value = _observed_item(invocation_id, row)
    db.execute("INSERT INTO observation_changes VALUES(?,?)", (revision, command_bytes(value)))
    floor = max(0, revision - 2048)
    db.execute("UPDATE observation_meta SET revision=?,floor=? WHERE singleton=1", (revision, floor))
    db.execute("DELETE FROM observation_changes WHERE revision<=?", (floor,))


def _basis(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observation_meta'").fetchone() is None:
        raise CommandRefused("OBJECT_UNAVAILABLE")
    row = db.execute("SELECT generation,revision,floor FROM observation_meta WHERE singleton=1").fetchone()
    if row is None or not 0 <= row[2] <= row[1] <= 9007199254740991:
        raise CommandRefused("RECORD_INVALID")
    return row


def _filter(invocation_ids) -> tuple[str, ...]:
    if (type(invocation_ids) is not tuple or not 1 <= len(invocation_ids) <= 32
            or any(type(value) is not str or not 1 <= len(value) <= 192 for value in invocation_ids)
            or len(set(invocation_ids)) != len(invocation_ids)):
        raise CommandRefused("COMMAND_INVALID")
    return invocation_ids


class InvocationObservation:
    def __init__(self, store: InvocationStore):
        self.store = store

    def inspect(self, invocation_id: str) -> dict:
        """Report confirmed owner facts; never claim, redispatch or guess liveness."""
        with self.store.connect() as db:
            row = db.execute("SELECT binding,state,result,result_sha256,cancelled FROM invocations WHERE id=?",
                             (invocation_id,)).fetchone()
        if row is None:
            return {"state": "ABSENT", "invocation_id": invocation_id}
        result = {"state": "EXECUTION_STATE_UNKNOWN", "invocation_id": invocation_id,
                  "revision": invocation_revision(row), "cancel_requested": bool(row[4])}
        if row[1] == "TERMINAL":
            # Reopen outputs and evidence using the existing owner's validator.
            # Carry the original retained bytes inside the new envelope. They
            # are not re-canonicalized with JCS or assigned a replacement ID.
            try:
                self.store.restore(invocation_id)
            except (ValueError, AgentExecutionError) as exc:
                raise CommandRefused("RECORD_INVALID") from exc
            if hashlib.sha256(row[2]).hexdigest() != row[3]:
                raise CommandRefused("RECORD_INVALID")
            result.update(state="TERMINAL", record=row[2].decode("utf-8"), record_sha256=row[3])
        elif row[1] != "CLAIMED":
            raise CommandRefused("RECORD_INVALID")
        return result

    def snapshot(self, invocation_ids: tuple[str, ...]) -> dict:
        """One consistent read transaction, with its inclusive owner position."""
        identities = _filter(invocation_ids)
        with self.store.connect() as db:
            db.execute("BEGIN")
            generation, revision, _ = _basis(db)
            items = []
            for invocation_id in identities:
                row = db.execute("SELECT binding,state,result,result_sha256,cancelled FROM invocations WHERE id=?",
                                 (invocation_id,)).fetchone()
                items.append(_observed_item(invocation_id, row) if row is not None else
                             {"invocation_id": invocation_id, "state": "ABSENT"})
        return {"generation": generation, "position": revision, "items": items}

    def after(self, generation: str, position: int, invocation_ids: tuple[str, ...], *, limit: int = 32) -> dict:
        """Complete (from, to] coverage, including progress past filtered changes."""
        identities = _filter(invocation_ids)
        if type(position) is not int or type(limit) is not int or not 1 <= limit <= 32:
            raise CommandRefused("COMMAND_INVALID")
        with self.store.connect() as db:
            db.execute("BEGIN")
            actual, current, floor = _basis(db)
            if generation != actual or not floor <= position <= current:
                raise CommandRefused("CURSOR_UNAVAILABLE")
            end = min(current, position + limit)
            rows = db.execute("SELECT revision,payload FROM observation_changes WHERE revision>? AND revision<=? ORDER BY revision",
                              (position, end)).fetchall()
            if [row[0] for row in rows] != list(range(position + 1, end + 1)):
                raise CommandRefused("RECORD_INVALID")
            changes = []
            for ordinal, raw in rows:
                item = command_value(raw)
                if item["invocation_id"] in identities:
                    changes.append({"position": ordinal, "item": item})
        return {"generation": actual, "from": position, "to": end, "changes": changes}
