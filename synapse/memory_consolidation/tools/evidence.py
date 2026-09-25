"""Content-addressed recorded results (store D): the first write of an address wins.

A repeated write of the same address never replaces the content and reports
that the evidence was already there, so a substitution cannot be "healed" by a
later live call and become invisible.
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
            return ref, True
        temp = self.root / f".{ref}.{os.getpid()}.tmp"
        with temp.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            preexisting = False
        except FileExistsError:
            preexisting = True
        finally:
            temp.unlink()
        return ref, preexisting

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
