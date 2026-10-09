"""Cold anchors: archived habits woken by demand (T6, T8a), checked before any birth.

Near misses and misses of the window are matched against the preserved
triggers of dormant and extinct habits. Two or more matches wake a dormant
habit (T6); an extinct one wakes only when Gold's fresh compatibility check
admits it for the current binding (T8a). Demand is a reason to wake a habit,
never a proof of the basis it lost: one whose retained basis no longer suffices
for a birth (its basis episodes forgotten) stays archived (review R7), and the
events go on to its candidate, which learns the procedure again from fresh
examples only, as a habit of its own (``pool.py``, ``births.typed_check``).
Episodes that woke an archived habit do not feed a new candidate, so a
duplicate of an archived habit is not born.
"""
from __future__ import annotations

from ..learning.triggers import matches
from .dependencies import retained_basis
from .habit_state import ARCHIVED


def cold_stage(context, legitimacy) -> tuple[dict[str, tuple[str, str]], set[tuple[str, str]]]:
    """Wakes by habit id, and the reactive events they consumed as ``(run_id, event_id)``: an event id is unique
    within its run only, so a wake never consumes another run's example (review R8)."""
    state, report = context.state, context.report
    wakes: dict[str, tuple[str, str]] = {}
    consumed: set[tuple[str, str]] = set()
    demand = [reaction for reaction in context.draft["reactions"] if reaction["reaction"] in {"miss", "near_miss"}]
    for habit_id in sorted(state["habits"]):
        current = state["habits"][habit_id]["state"]
        if current not in ARCHIVED:
            continue
        trigger = state["frozen"][habit_id]["trigger"]
        matched = [(item["run_id"], item["event_id"]) for item in demand
                   if matches(trigger, item["context"])[0] == "applicable"]
        entry = {"habit_id": habit_id, "state": current,
                 "matches": [{"run_id": run_id, "event_id": event_id} for run_id, event_id in matched]}
        if len(matched) >= context.parameters["cold_matches"]:
            retained, required = retained_basis(state, context.parameters, habit_id)
            if retained < required:
                entry["refused"] = "basis_not_retained"
            elif current == "dormant":
                wakes[habit_id] = ("T6", f"{len(matched)} window events matched the archived trigger")
            elif legitimacy.get(habit_id, {}).get("compatible"):
                wakes[habit_id] = ("T8a", f"{len(matched)} window events matched; fresh compatibility admitted")
            else:
                entry["refused"] = "compatibility_not_admitted"
            if habit_id in wakes:
                consumed.update(matched)
        report["cold_checks"]["dormant_matches" if current == "dormant" else "extinct_matches"].append(entry)
    return wakes, consumed
