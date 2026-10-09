"""Durable external experiment scheduler; product runs retain their ownership.

A committed dispatch start precedes every effect. The OS releases the lock on
process death. Reopening resumes the original Gold run, while an interrupted
legacy Baseline remains indeterminate. Completed arms are never re-executed.
"""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from synapse.experiments.gold.persistence import ExclusiveStoreLock

from .execution import execute_baseline, execute_gold
from .protocol import Protocol, canonical, code_identity, digest, read_source, environment_identity


TERMINAL = {"FINISHED", "FAILED", "INTERRUPTED"}


class Experiment:
    def __init__(self, root, *, repository, protocol=None):
        self.root, self.repository = Path(root).resolve(), Path(repository).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "experiment.sqlite3"
        if self.database.is_symlink():
            raise ValueError("experiment database must not be a link")
        with ExclusiveStoreLock(self.root / "experiment.lock"), closing(self._connect()) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS registration (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity TEXT NOT NULL, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, payload BLOB NOT NULL);
            """)
            row = connection.execute("SELECT identity,payload FROM registration WHERE singleton=1").fetchone()
            if row is None:
                if not isinstance(protocol, Protocol):
                    raise ValueError("a new experiment needs a frozen protocol")
                protocol.validate_inputs()
                if code_identity(self.repository) != protocol.payload()["code"]:
                    raise ValueError("experiment code differs from preregistration")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO registration VALUES(1,?,?)", (protocol.identity, protocol.raw))
                connection.commit()
                row = protocol.identity, protocol.raw
            self.protocol = Protocol(bytes(row[1]))
            if self.protocol.identity != row[0] or (protocol is not None and protocol.raw != self.protocol.raw):
                raise ValueError("preregistration changed")
        self.history()

    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        connection.execute("PRAGMA synchronous=FULL")
        if mode != "wal" or connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
            connection.close()
            raise RuntimeError("external experiment durability profile is unavailable")
        return connection

    def history(self):
        with closing(self._connect()) as connection:
            registration = connection.execute("SELECT identity,payload FROM registration WHERE singleton=1").fetchone()
            if registration != (self.protocol.identity, self.protocol.raw):
                raise ValueError("stored preregistration changed")
            rows = connection.execute("SELECT sequence,identity,payload FROM events ORDER BY sequence").fetchall()
        schedule = {slot["slot_id"]: slot for slot in self.protocol.schedule()}
        events, latest, previous = [], {}, self.protocol.identity
        for sequence, identity, raw in rows:
            event = json.loads(raw)
            slot = event["slot_id"]
            if (sequence != len(events) + 1 or canonical(event) != raw or digest(event) != identity
                    or event["sequence"] != sequence or event["previous"] != previous
                    or event["protocol"] != self.protocol.identity or slot not in schedule):
                raise ValueError("experiment event history is incomplete or changed")
            before = latest.get(slot)
            kind = event["kind"]
            if kind == "STARTED":
                if before is not None:
                    raise ValueError("arm was started more than once")
                unfinished = next((s for s in self.protocol.schedule()
                    if latest.get(s["slot_id"]) not in TERMINAL), None)
                if unfinished is None or unfinished["slot_id"] != slot:
                    raise ValueError("execution order differs from preregistration")
            elif kind == "RESUMING":
                if schedule[slot]["arm"] != "GOLD" or before not in {"STARTED", "RESUMING", "APPROVAL_REQUIRED"}:
                    raise ValueError("only the original unfinished Gold run can resume")
            elif kind in TERMINAL | {"APPROVAL_REQUIRED"}:
                if before not in {"STARTED", "RESUMING"}:
                    raise ValueError("completion has no preceding dispatch")
                if kind == "APPROVAL_REQUIRED" and schedule[slot]["arm"] != "GOLD":
                    raise ValueError("Baseline cannot acquire Gold approval semantics")
            else:
                raise ValueError("unknown experiment event")
            latest[slot] = kind
            previous = identity
            events.append(event)
        return events

    def _append(self, slot, kind, payload):
        events = self.history()
        event = {"sequence": len(events) + 1, "protocol": self.protocol.identity,
            "previous": self.protocol.identity if not events else digest(events[-1]),
            "slot_id": slot["slot_id"], "kind": kind, "payload": payload}
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO events VALUES(?,?,?)", (event["sequence"], digest(event), canonical(event)))
            connection.commit()
        self.history()

    def allocations(self):
        events = self.history()
        result = []
        for slot in self.protocol.schedule():
            history = [event for event in events if event["slot_id"] == slot["slot_id"]]
            latest = None if not history else history[-1]
            result.append({**slot, "state": "PLANNED" if latest is None else latest["kind"],
                "receipt": None if latest is None or latest["kind"] not in TERMINAL | {"APPROVAL_REQUIRED"} else latest["payload"],
                "events": history})
        return result

    def run_next(self, *, approve_pending=False):
        with ExclusiveStoreLock(self.root / "experiment.lock"):
            if code_identity(self.repository) != self.protocol.payload()["code"]:
                raise ValueError("run code differs from frozen preregistration")
            if environment_identity() != self.protocol.payload()["environment"]:
                raise ValueError("execution host profile differs from preregistration")
            self.protocol.validate_inputs()
            slot = next((slot for slot in self.allocations() if slot["state"] not in TERMINAL), None)
            if slot is None:
                return None
            state = slot["state"]
            if state == "APPROVAL_REQUIRED" and not approve_pending:
                return slot
            if state in {"STARTED", "RESUMING"} and slot["arm"] == "BASELINE":
                self._append(slot, "INTERRUPTED", {"reason": "legacy_baseline_has_no_durable_resume",
                    "usage": None, "result": None, "effects_repeated": False})
                return self.allocations()[slot["ordinal"]]
            definition = read_source(slot["input_ref"])
            if state == "PLANNED":
                self.protocol.validate_initial(slot)
            self._append(slot, "STARTED" if state == "PLANNED" else "RESUMING",
                {"input_ref": slot["input_ref"], "environment": environment_identity()})
            started = time.monotonic_ns()
            try:
                if slot["arm"] == "BASELINE":
                    receipt = execute_baseline(slot, definition)
                else:
                    receipt = execute_gold(slot, definition, repository=self.repository,
                        resume=state != "PLANNED",
                        approval=slot["receipt"]["pending"] if state == "APPROVAL_REQUIRED" else None)
                receipt["external_action_duration_ns"] = str(time.monotonic_ns() - started)
            except Exception as exc:
                # The process may have performed effects. Its unknown usage is
                # retained and the arm is not silently replaced by a fresh run.
                self._append(slot, "FAILED", {"error_class": type(exc).__name__, "detail": str(exc)[:500],
                    "external_action_duration_ns": str(time.monotonic_ns() - started), "usage": None})
            else:
                # Publication errors are not execution failures. A committed
                # terminal receipt must never acquire a second terminal event.
                self._append(slot, "FINISHED" if receipt["terminal"] else "APPROVAL_REQUIRED", receipt)
            return self.allocations()[slot["ordinal"]]

    def run_all(self, *, approve_pending=False):
        while True:
            result = self.run_next(approve_pending=approve_pending)
            if result is None or (result["state"] == "APPROVAL_REQUIRED" and not approve_pending):
                return self.allocations()
