"""Content-addressed recorded results (store D): the first write of an address wins.

A repeated write of the same address never replaces the content and reports
that the evidence was already there, so a substitution cannot be "healed" by a
later live call and become invisible.

Store D also holds the owner's raw traces and replay data (spec part 3 §2.2).
Retention may remove a body only through ``discard``: a durable marker naming
why it is gone (``compacted`` — provably reconstructible — or ``forgotten``
with its tombstone) is written before the body is unlinked, so a missing body
is always an explained end, never a dangling reference. Writing the same
content again restores the body and clears the marker.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from ..records import canonical
from .journal import GatewayIntegrityError

EVIDENCE_V1 = "synapse.memory.evidence/v1"
EVIDENCE_GONE_V1 = "synapse.memory.evidence-gone/v1"
GONE_REASONS = ("compacted", "forgotten")


class EvidenceStore:
    """Content-addressed recorded results (store D); the first write wins."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, content: Mapping[str, Any]) -> tuple[str, bool]:
        raw = canonical({"schema": EVIDENCE_V1, **dict(content)})
        ref = hashlib.sha256(raw).hexdigest()
        path = self.root / f"{ref}.json"
        if path.exists():
            if self.get(ref) is None:
                raise GatewayIntegrityError("evidence address holds other content")
            self._clear_marker(ref)
            return ref, True
        temp = self.root / f".{ref}.{os.getpid()}.tmp"
        self._write(temp, raw)
        try:
            os.link(temp, path)
            preexisting = False
        except FileExistsError:
            preexisting = True
        finally:
            temp.unlink()
        self._clear_marker(ref)
        return ref, preexisting

    def discard(self, ref: str, reason: str, *, tombstone: str | None = None) -> None:
        """Remove one body behind a durable marker (idempotent)."""
        if reason not in GONE_REASONS or (reason == "forgotten") != (tombstone is not None):
            raise ValueError("a removed body is compacted, or forgotten with its tombstone")
        marker = self.root / f"{ref}.gone.json"
        if not marker.exists():
            temp = self.root / f".{ref}.gone.{os.getpid()}.tmp"
            self._write(temp, canonical({"schema": EVIDENCE_GONE_V1, "ref": ref, "reason": reason,
                                         "tombstone": tombstone}))
            os.replace(temp, marker)
            self._sync_directory()
        try:
            (self.root / f"{ref}.json").unlink()
        except FileNotFoundError:
            pass

    def gone(self, ref: str) -> dict[str, Any] | None:
        """Why a body is absent, if retention removed it."""
        try:
            value = json.loads((self.root / f"{ref}.gone.json").read_bytes())
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or value.get("schema") != EVIDENCE_GONE_V1 or value.get("ref") != ref:
            raise GatewayIntegrityError("an evidence removal marker is unreadable")
        return value

    def _clear_marker(self, ref: str) -> None:
        marker = self.root / f"{ref}.gone.json"
        if marker.exists():
            marker.unlink()
            self._sync_directory()

    @staticmethod
    def _write(path: Path, raw: bytes) -> None:
        with path.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())

    def _sync_directory(self) -> None:
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def get(self, ref: str) -> dict[str, Any] | None:
        """The recorded content, only if its address still matches its bytes."""
        path = self.root / f"{ref}.json"
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if hashlib.sha256(raw).hexdigest() != ref:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) and value.get("schema") == EVIDENCE_V1 else None
