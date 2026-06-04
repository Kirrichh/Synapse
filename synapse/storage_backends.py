"""Synapse v1.9 cognitive storage backends.

The production contract is Postgres/Redis-friendly, but this module keeps the
reference implementation dependency-free. SQLite is the durable fallback used by
unit tests; PostgreSQL/Redis classes expose the same boundary and can be wired to
real drivers later without touching language semantics.
"""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class CognitiveStorageError(RuntimeError):
    pass


class CognitiveStorageBackend:
    def put_memory(self, palace: str, room: str, record: Dict[str, Any]) -> str:
        raise NotImplementedError

    def query_memory(self, palace: str, room: str, query: str = "", threshold: float = 0.0, limit: int = 10) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def save_intention(self, cascade: Dict[str, Any]) -> str:
        raise NotImplementedError

    def list_intentions(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def save_habit(self, habit: Dict[str, Any]) -> str:
        raise NotImplementedError

    def list_habits(self) -> List[Dict[str, Any]]:
        raise NotImplementedError


class InMemoryCognitiveStorage(CognitiveStorageBackend):
    def __init__(self):
        self.memories: List[Dict[str, Any]] = []
        self.intentions: List[Dict[str, Any]] = []
        self.habits: List[Dict[str, Any]] = []

    def put_memory(self, palace: str, room: str, record: Dict[str, Any]) -> str:
        rid = record.get("id") or f"mem-{uuid.uuid4().hex[:12]}"
        payload = dict(record, id=rid, palace=palace, room=room, created_at=record.get("created_at", time.time()))
        self.memories.append(payload)
        return rid

    def query_memory(self, palace: str, room: str, query: str = "", threshold: float = 0.0, limit: int = 10) -> List[Dict[str, Any]]:
        q = (query or "").lower()
        candidates = []
        for m in self.memories:
            if m.get("palace") != palace or m.get("room") != room:
                continue
            text = json.dumps(m, ensure_ascii=False, default=str).lower()
            overlap = 1.0 if not q else sum(1 for token in q.split() if token in text) / max(1, len(q.split()))
            confidence = float(m.get("confidence", 1.0) or 0.0)
            score = round((overlap + confidence) / 2.0, 3)
            if score >= threshold:
                row = dict(m)
                row["score"] = score
                candidates.append(row)
        candidates.sort(key=lambda x: (x.get("score", 0), x.get("created_at", 0)), reverse=True)
        return candidates[: int(limit or 10)]

    def save_intention(self, cascade: Dict[str, Any]) -> str:
        cid = cascade.get("id") or f"intent-{uuid.uuid4().hex[:12]}"
        self.intentions.append(dict(cascade, id=cid, created_at=cascade.get("created_at", time.time())))
        return cid

    def list_intentions(self) -> List[Dict[str, Any]]:
        return [dict(x) for x in self.intentions]

    def save_habit(self, habit: Dict[str, Any]) -> str:
        hid = habit.get("id") or f"habit-{uuid.uuid4().hex[:12]}"
        self.habits.append(dict(habit, id=hid, created_at=habit.get("created_at", time.time())))
        return hid

    def list_habits(self) -> List[Dict[str, Any]]:
        return [dict(x) for x in self.habits]


class SQLiteCognitiveStorage(InMemoryCognitiveStorage):
    def __init__(self, path: str = ":memory:"):
        super().__init__()
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, palace TEXT, room TEXT, payload TEXT, created_at REAL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS intentions (id TEXT PRIMARY KEY, payload TEXT, created_at REAL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS habits (id TEXT PRIMARY KEY, payload TEXT, created_at REAL)")
        self.conn.commit()

    def put_memory(self, palace: str, room: str, record: Dict[str, Any]) -> str:
        rid = record.get("id") or f"mem-{uuid.uuid4().hex[:12]}"
        payload = dict(record, id=rid, palace=palace, room=room, created_at=record.get("created_at", time.time()))
        self.conn.execute("INSERT OR REPLACE INTO memories VALUES (?, ?, ?, ?, ?)", (rid, palace, room, json.dumps(payload, ensure_ascii=False, default=str), payload["created_at"]))
        self.conn.commit()
        return rid

    def query_memory(self, palace: str, room: str, query: str = "", threshold: float = 0.0, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT payload FROM memories WHERE palace=? AND room=? ORDER BY created_at DESC", (palace, room)).fetchall()
        self.memories = [json.loads(r[0]) for r in rows]
        return super().query_memory(palace, room, query, threshold, limit)

    def save_intention(self, cascade: Dict[str, Any]) -> str:
        cid = cascade.get("id") or f"intent-{uuid.uuid4().hex[:12]}"
        payload = dict(cascade, id=cid, created_at=cascade.get("created_at", time.time()))
        self.conn.execute("INSERT OR REPLACE INTO intentions VALUES (?, ?, ?)", (cid, json.dumps(payload, ensure_ascii=False, default=str), payload["created_at"]))
        self.conn.commit()
        return cid

    def save_habit(self, habit: Dict[str, Any]) -> str:
        hid = habit.get("id") or f"habit-{uuid.uuid4().hex[:12]}"
        payload = dict(habit, id=hid, created_at=habit.get("created_at", time.time()))
        self.conn.execute("INSERT OR REPLACE INTO habits VALUES (?, ?, ?)", (hid, json.dumps(payload, ensure_ascii=False, default=str), payload["created_at"]))
        self.conn.commit()
        return hid


class PostgreSQLCognitiveStorage(InMemoryCognitiveStorage):
    """Production boundary placeholder.

    In this dependency-free distribution it behaves like in-memory storage while
    preserving backend metadata. Real deployments can replace it with psycopg and
    SERIALIZABLE transactions without changing Synapse language semantics.
    """
    backend_name = "postgresql"


class RedisSpine:
    """Dependency-free Redis time-series/cache boundary used by habit analysis."""
    def __init__(self):
        self.series: Dict[str, List[Dict[str, Any]]] = {}

    def add_metric(self, name: str, value: float, tags: Optional[Dict[str, Any]] = None):
        self.series.setdefault(name, []).append({"ts": time.time(), "value": value, "tags": tags or {}})

    def window(self, name: str, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self.series.get(name, []))[-limit:]
