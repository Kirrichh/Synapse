"""Metadata of learned and declared habits, as the court's decisions produce it.

Only the court writes these values, inside one consolidation; the projection
folds them from reports. Learned habits carry their effectiveness state, trust
per trigger, pending evidence and the counters the automaton reads. Declared
(layer 1) habits carry observed trust and statistics only: the court never
moves them between states.
"""
from __future__ import annotations

import copy
from typing import Any

EFFECTIVE = ("born", "active", "probation")
ARCHIVED = ("dormant", "extinct")
#: Fields a later policy reads, with the value that claims nothing: no verified resolution, no compared
#: outcome, no evidence of errors. Metadata recorded before them is completed with these values, never with
#: a positive one, and the completion is reported.
NEUTRAL_FIELDS: dict[str, Any] = {"yields_to": [], "compared": [], "tail": [],
                                  # Confirmed experience since birth (review R6): none is claimed.
                                  "counted_since_birth": 0, "counted_tasks_since_birth": []}


def new_metadata(parameters, *, habit_id: str, trigger_id: str, state: str, trust: float, window: int,
                 consolidation_id: str, energy_cost: float, supersedes: str | None) -> dict[str, Any]:
    return {"habit_id": habit_id, "trigger_id": trigger_id, "state": state, "trust": trust,
            "context_trust": {trigger_id: trust}, "counted": 0, "pending": [], "state_since": window,
            "fires_in_state": 0, "signals_in_state": 0, "signal_sum_in_state": 0.0, "tail": [],
            "fires_since_birth": 0, "tasks_since_birth": [], "counted_since_birth": 0,
            "counted_tasks_since_birth": [], "idle_windows": 0, "cold_windows": 0,
            "sprt_llr": 0.0, "key_hold_until": None, "votes": 0, "cs_tasks": [], "cs_sum": 0.0,
            "exec_summary": {"fires_total": 0, "successes": 0, "failures": 0, "uncertain": 0},
            "energy_cost": energy_cost, "priority": parameters["learned_priority_class"],
            "born_in": consolidation_id, "supersedes": supersedes, "superseded_by": None, "recent": [],
            "publication": None, "yields_to": [], "compared": []}


def complete_metadata(habits: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Add the neutral value of every field a habit's recorded metadata lacks; what was added."""
    completed = []
    for habit_id, metadata in sorted(habits.items()):
        missing = sorted(name for name in NEUTRAL_FIELDS if name not in metadata)
        for name in missing:
            metadata[name] = copy.deepcopy(NEUTRAL_FIELDS[name])
        if missing:
            completed.append({"habit_id": habit_id, "fields": missing})
    return completed


def declared_metadata(habit_id: str) -> dict[str, Any]:
    return {"habit_id": habit_id, "context_trust": {}, "counted": {}, "pending": [], "recent": [],
            "exec_summary": {"fires_total": 0, "successes": 0, "failures": 0, "uncertain": 0}}


def count_fire(summary: dict[str, int], outcome: str | None) -> None:
    summary["fires_total"] += 1
    key = {"success": "successes", "failure": "failures"}.get(outcome, "uncertain")
    summary[key] += 1


def enter_state(metadata: dict[str, Any], state: str, window: int) -> None:
    """A new effectiveness state restarts every counter measured within a state."""
    metadata.update(state=state, state_since=window, fires_in_state=0, signals_in_state=0,
                    signal_sum_in_state=0.0, tail=[], idle_windows=0, cold_windows=0,
                    sprt_llr=0.0, key_hold_until=None, cs_tasks=[], cs_sum=0.0)


def mean(values) -> float | None:
    return sum(values) / len(values) if values else None
