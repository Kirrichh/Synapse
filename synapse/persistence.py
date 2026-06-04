"""Persistent storage adapters for Synapse runtime state.

v1.7 Production Hardening adds a small storage boundary rather than binding the
runtime to a specific database. The interface stores JSON-safe snapshots and
append-only event batches; SQLite is included because it is part of the Python
standard library and works in tests without external services.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from contextlib import closing
from typing import Any, Dict, List, Optional


class StorageError(RuntimeError):
    pass


class StorageBackend(ABC):
    @abstractmethod
    def save_state(self, run_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def append_events(self, run_id: str, events: List[Dict[str, Any]]) -> int:
        raise NotImplementedError

    @abstractmethod
    def load_events(self, run_id: str, after_index: int = 0) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_runs(self) -> List[str]:
        raise NotImplementedError


class InMemoryStorage(StorageBackend):
    def __init__(self):
        self.states: Dict[str, Dict[str, Any]] = {}
        self.events: Dict[str, List[Dict[str, Any]]] = {}

    def save_state(self, run_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.loads(json.dumps(state, default=str))
        payload["stored_at"] = time.time()
        self.states[run_id] = payload
        return {"run_id": run_id, "status": "saved", "backend": "memory"}

    def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        state = self.states.get(run_id)
        return json.loads(json.dumps(state, default=str)) if state is not None else None

    def append_events(self, run_id: str, events: List[Dict[str, Any]]) -> int:
        self.events.setdefault(run_id, [])
        for event in events:
            self.events[run_id].append(json.loads(json.dumps(event, default=str)))
        return len(self.events[run_id])

    def load_events(self, run_id: str, after_index: int = 0) -> List[Dict[str, Any]]:
        return json.loads(json.dumps(self.events.get(run_id, [])[after_index:], default=str))

    def list_runs(self) -> List[str]:
        return sorted(set(self.states) | set(self.events))


class SQLiteStorage(StorageBackend):
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.path)

    def _init_db(self):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS synapse_states (
                        run_id TEXT PRIMARY KEY,
                        state_json TEXT NOT NULL,
                        history_hash TEXT,
                        stored_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS synapse_events (
                        run_id TEXT NOT NULL,
                        idx INTEGER NOT NULL,
                        event_json TEXT NOT NULL,
                        event_hash TEXT,
                        stored_at REAL NOT NULL,
                        PRIMARY KEY(run_id, idx)
                    )
                    """
                )

    def save_state(self, run_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps(state, sort_keys=True, default=str)
        history_hash = state.get("history_hash") or state.get("runtime", {}).get("history_hash")
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO synapse_states(run_id, state_json, history_hash, stored_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        state_json=excluded.state_json,
                        history_hash=excluded.history_hash,
                        stored_at=excluded.stored_at
                    """,
                    (run_id, payload, history_hash, time.time()),
                )
        return {"run_id": run_id, "status": "saved", "backend": "sqlite", "path": self.path}

    def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT state_json FROM synapse_states WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def append_events(self, run_id: str, events: List[Dict[str, Any]]) -> int:
        with closing(self._connect()) as conn:
            with conn:
                row = conn.execute("SELECT COALESCE(MAX(idx), -1) FROM synapse_events WHERE run_id=?", (run_id,)).fetchone()
                start = int(row[0]) + 1
                for offset, event in enumerate(events):
                    idx = start + offset
                    event_hash = event.get("event_hash") or event.get("hash")
                    conn.execute(
                        "INSERT OR REPLACE INTO synapse_events(run_id, idx, event_json, event_hash, stored_at) VALUES(?, ?, ?, ?, ?)",
                        (run_id, idx, json.dumps(event, sort_keys=True, default=str), event_hash, time.time()),
                    )
                row = conn.execute("SELECT COUNT(*) FROM synapse_events WHERE run_id=?", (run_id,)).fetchone()
        return int(row[0])

    def load_events(self, run_id: str, after_index: int = 0) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT event_json FROM synapse_events WHERE run_id=? AND idx>=? ORDER BY idx ASC",
                (run_id, after_index),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_runs(self) -> List[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT run_id FROM synapse_states UNION SELECT run_id FROM synapse_events ORDER BY run_id"
            ).fetchall()
        return [row[0] for row in rows]
