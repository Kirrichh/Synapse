"""Read-only typed capture adapters for governed snapshot sources."""

from __future__ import annotations

from typing import Protocol

from .coordination import CoordinatedFenceLease
from .knowledge_contracts import SnapshotStoreSequence


class SnapshotSourceHeadReader(Protocol):
    def read_snapshot_heads(
        self,
        *,
        fence_lease: CoordinatedFenceLease,
    ) -> tuple[SnapshotStoreSequence, ...]:
        """Read exact governed source heads while the shared lease is active."""
