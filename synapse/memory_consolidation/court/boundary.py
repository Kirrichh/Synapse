"""The knowledge snapshot boundary: the runtime's slice of learned memory.

After an applied consolidation the boundary lists exactly the learned habits
that are both admitted by Gold and effective (born, active, probation), minus
slow-only triggers, with the observed trust of declared habits and the
statuses of hypotheses a later session may reuse for the same source version
(with the window they were decided in). The runtime
reads only a complete boundary; it names the consolidation it was built from,
so a lagging boundary is detected against the chain head and rebuilt
idempotently from the same report.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .. import records
from ..learning.triggers import condition_key
from ..records import canonical
from .habit_state import EFFECTIVE
from .hypotheses import boundary_view

BOUNDARY_V2 = "synapse.memory.snapshot-boundary/v2"


def _entry(habit_id, metadata, frozen) -> dict[str, Any]:
    return {"habit_id": habit_id, "trigger": frozen["trigger"], "habit": frozen["habit"],
            "state": metadata["state"], "priority": metadata["priority"], "context_trust": metadata["trust"],
            "energy_cost": metadata["energy_cost"], "publication": metadata["publication"]}


def boundary_record(state_after: Mapping[str, Any], legitimacy: Mapping[str, Any],
                    consolidation_id: str) -> dict[str, Any]:
    """The content-addressed boundary record built from one applied state."""
    slow = {canonical(item) for item in state_after["slow_only"]}
    habits = []
    for habit_id in sorted(state_after["habits"]):
        metadata = state_after["habits"][habit_id]
        frozen = state_after["frozen"][habit_id]
        verdict = legitimacy.get(habit_id, {})
        if (metadata["state"] in EFFECTIVE and verdict.get("admitted")
                and canonical(condition_key(frozen["trigger"])) not in slow):
            habits.append(_entry(habit_id, metadata, frozen))
    declared = {habit_id: metadata["context_trust"] for habit_id, metadata in sorted(state_after["declared"].items())}
    hypotheses, claims = boundary_view(state_after["hypotheses"])
    return records.make("snapshot_boundary", boundary={
        "schema_version": BOUNDARY_V2, "consolidation_id": consolidation_id, "window": state_after["window"],
        "habits": habits, "slow_only": copy.deepcopy(state_after["slow_only"]), "declared": declared,
        "hypotheses": hypotheses, "claims": claims})
