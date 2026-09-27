"""Append-only JSON-lines logs whose records form one hash chain.

A record is acknowledged only when its line is complete and fsynced; a torn
final line was never acknowledged and is cut before the next append. Appends
run under a process-level file lock, so the runs of one memory owner share one
chain. A broken chain is an integrity failure, never silently repaired.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from filelock import FileLock

from ..records import canonical


class GatewayIntegrityError(RuntimeError):
    """A gateway journal, side log or evidence file fails its own integrity."""


class ChainedLog:
    """Append-only JSON lines whose records form one hash chain."""

    def __init__(self, path: Path, genesis: str, hasher) -> None:
        self.path = path
        self.genesis = genesis
        self.hasher = hasher
        self.lock = FileLock(str(path) + ".lock")

    def _scan(self) -> tuple[list[dict[str, Any]], int]:
        """Validated records and the byte length they occupy."""
        if not self.path.exists():
            return [], 0
        raw = self.path.read_bytes()
        records: list[dict[str, Any]] = []
        prev, offset = self.genesis, 0
        while offset < len(raw):
            end = raw.find(b"\n", offset)
            if end < 0:
                # A torn final line was never acknowledged; it is not a record.
                break
            try:
                record = json.loads(raw[offset:end])
            except ValueError as exc:
                raise GatewayIntegrityError(f"{self.path.name}: unreadable record {len(records)}") from exc
            if (type(record) is not dict or record.get("seq") != len(records) or record.get("prev") != prev
                    or record.get("hash") != self.hasher(record)):
                raise GatewayIntegrityError(f"{self.path.name}: chain broken at record {len(records)}")
            records.append(record)
            prev = record["hash"]
            offset = end + 1
        return records, offset

    def read(self) -> list[dict[str, Any]]:
        return self._scan()[0]

    def append(self, fill) -> dict[str, Any]:
        """Append the record ``fill(seq, prev)`` returns, under the owner lock."""
        with self.lock:
            records, valid = self._scan()
            prev = records[-1]["hash"] if records else self.genesis
            record = fill(len(records), prev)
            record["hash"] = self.hasher(record)
            with self.path.open("ab") as handle:
                handle.truncate(valid)
                handle.write(canonical(record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            return record
