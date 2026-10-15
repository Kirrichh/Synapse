"""The court port Gold's project runs reach.

Gold's element-owner jobs judge completed project runs at their two moments —
before a new task pins the decision its memory frame reads, and after a run's
outcome is recorded — under Gold's own owner session. Gold does not import the
memory subsystem; the canonical composition hands it this port, so every
task stream, Synapse sessions and Gold project runs alike, reaches one court
and one decision chain. Without a bound memory configuration the court decides
the exact-subject section alone.
"""
from __future__ import annotations

from typing import Any

from .court.consolidation import consolidate, consolidate_exact
from .factory import MemoryFactory
from .owner import MemoryOwner, MemoryOwnerViolation


class ProjectMemoryCourt:
    """``consolidate(store, guard, project_identity=)`` for Gold's project memory jobs."""

    def consolidate(self, store, guard, *, project_identity: str) -> dict[str, Any]:
        owner = MemoryOwner(store.root.parent, store=store)
        if owner.identity != project_identity:
            raise MemoryOwnerViolation("a project run names another memory owner")
        configuration = owner.bound_configuration(guard=guard)
        if configuration is None:
            return consolidate_exact(owner, guard)
        factory = MemoryFactory(owner.state_root, configuration, owner=owner)
        return consolidate(owner, configuration, factory.ports(), guard, mode="summary")
