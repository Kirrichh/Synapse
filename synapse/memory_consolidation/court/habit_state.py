"""Metadata of learned and declared habits, as the court's decisions produce it.

Only the court writes these values, inside one consolidation; the projection
folds them from reports. Learned habits carry their effectiveness state, trust
per trigger, pending evidence and the counters the automaton reads. Declared
(layer 1) habits carry observed trust and statistics only: the court never
moves them between states.
"""
from __future__ import annotations

from typing import Any

EFFECTIVE = ("born", "active", "probation")
ARCHIVED = ("dormant", "extinct")


def new_metadata(parameters, *, habit_id: str, trigger_id: str, state: str, trust: float, window: int,
                 consolidation_id: str, energy_cost: float, supersedes: str | None) -> dict[str, Any]:
    return {"habit_id": habit_id, "trigger_id": trigger_id, "state": state, "trust": trust,
            "context_trust": {trigger_id: trust}, "counted": 0, "pending": [], "state_since": window,
            "fires_in_state": 0, "signals_in_state": 0, "signal_sum_in_state": 0.0, "participations_in_state": 0,
            "fires_since_birth": 0, "tasks_since_birth": [], "idle_windows": 0, "cold_windows": 0,
            "sprt_llr": 0.0, "key_hold_until": None, "votes": 0,
            "exec_summary": {"fires_total": 0, "successes": 0, "failures": 0, "uncertain": 0},
            "energy_cost": energy_cost, "priority": parameters["learned_priority_class"],
            "born_in": consolidation_id, "supersedes": supersedes, "superseded_by": None, "recent": []}


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
                    signal_sum_in_state=0.0, participations_in_state=0, idle_windows=0, cold_windows=0,
                    sprt_llr=0.0, key_hold_until=None)


def mean(values) -> float | None:
    return sum(values) / len(values) if values else None
