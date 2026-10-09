"""Durable invocation claims, exact results and content-addressed evidence.

SQLite owns concurrency and atomic terminal publication. A started invocation
without a terminal record is never silently dispatched a second time.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sqlite3
import time

from .codec import canonical_bytes, decode_json, result_from_dict
from .policy import AgentExecutionError, AgentFailureCode


class InvocationStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ValueError("agent evidence root cannot be a symlink")
        self.database = root / "executions.sqlite3"
        if self.database.is_symlink():
            raise ValueError("agent journal cannot be a symlink")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS invocations (
                    id TEXT PRIMARY KEY, binding BLOB NOT NULL, state TEXT NOT NULL,
                    started REAL NOT NULL, finished REAL, result BLOB, result_sha256 TEXT,
                    cancelled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS artifacts (
                    sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    invocation_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                    payload BLOB NOT NULL, PRIMARY KEY(invocation_id, sequence));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def executing(self, invocation_id):
        from filelock import FileLock, Timeout
        lock = FileLock(self.root / (hashlib.sha256(invocation_id.encode()).hexdigest() + ".lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN,
                "another owner is already executing or reconciling this invocation") from exc
        try:
            yield
        finally:
            lock.release()

    def event_refs(self, invocation_id):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM events WHERE invocation_id=? ORDER BY sequence", (invocation_id,)).fetchall()
        return tuple(self.retain(row[0]) for row in rows)

    def claim(self, invocation_id: str, binding: bytes):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT binding,state,result,result_sha256 FROM invocations WHERE id=?", (invocation_id,)).fetchone()
            if row is not None:
                if row[0] != binding:
                    raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "invocation identity is already bound to other inputs or runtime")
                if row[1] != "TERMINAL":
                    raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN,
                        "invocation was claimed; reconcile its retained runtime state before any new attempt")
                if hashlib.sha256(row[2]).hexdigest() != row[3]:
                    raise ValueError("retained agent result digest differs")
                return result_from_dict(decode_json(row[2]))
            db.execute("INSERT INTO invocations(id,binding,state,started) VALUES(?,?,?,?)",
                       (invocation_id, binding, "CLAIMED", time.time()))
        return None

    def retain(self, payload: bytes) -> str:
        sha = hashlib.sha256(payload).hexdigest()
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (sha, payload))
            if db.execute("SELECT payload FROM artifacts WHERE sha256=?", (sha,)).fetchone()[0] != payload:
                raise ValueError("retained agent artifact identity collision")
        return sha

    def read(self, sha: str) -> bytes:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM artifacts WHERE sha256=?", (sha,)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != sha:
            raise ValueError("retained agent artifact is unavailable or changed")
        return row[0]

    def event(self, invocation_id: str, value) -> str:
        raw = canonical_bytes(value)
        ref = self.retain(raw)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            index = db.execute("SELECT coalesce(max(sequence),-1)+1 FROM events WHERE invocation_id=?", (invocation_id,)).fetchone()[0]
            db.execute("INSERT INTO events VALUES(?,?,?)", (invocation_id, index, raw))
        return ref

    def finish(self, invocation_id: str, result):
        raw = canonical_bytes(result)
        for output in result.outputs:
            self.retain(output.payload)
        for sha in result.evidence_refs:
            self.read(sha)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE invocations SET state='TERMINAL',finished=?,result=?,result_sha256=? WHERE id=? AND state='CLAIMED'",
                (time.time(), raw, hashlib.sha256(raw).hexdigest(), invocation_id)).rowcount
            if changed != 1:
                raise ValueError("agent terminal result cannot replace a different invocation state")

    def cancel(self, invocation_id: str) -> bool:
        with self.connect() as db:
            return db.execute("UPDATE invocations SET cancelled=1 WHERE id=? AND state='CLAIMED'", (invocation_id,)).rowcount == 1

    def is_cancelled(self, invocation_id: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT cancelled FROM invocations WHERE id=?", (invocation_id,)).fetchone()
        return row is not None and row[0] == 1

    def latest_event(self, invocation_id: str, kind: str):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM events WHERE invocation_id=? ORDER BY sequence DESC", (invocation_id,)).fetchall()
        for row in rows:
            value = decode_json(row[0])
            if value.get("kind") == kind:
                return value
        return None

    def restore(self, invocation_id: str):
        with self.connect() as db:
            row = db.execute("SELECT binding,state,result,result_sha256 FROM invocations WHERE id=?", (invocation_id,)).fetchone()
        if row is None or row[1] != "TERMINAL":
            raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "agent execution has no retained terminal result")
        if hashlib.sha256(row[2]).hexdigest() != row[3]:
            raise ValueError("retained agent result changed")
        result = result_from_dict(decode_json(row[2]))
        for item in result.outputs:
            if self.read(item.sha256) != item.payload:
                raise ValueError("retained output bytes differ")
        for sha in result.evidence_refs:
            self.read(sha)
        return decode_json(row[0]), result
