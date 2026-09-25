"""Cold anchors: archived habits woken by demand (T6, T8a), checked before any birth.

Near misses and misses of the window are matched against the preserved
triggers of dormant and extinct habits. Two or more matches wake a dormant
habit (T6); an extinct one wakes only when Gold's fresh compatibility check
admits it for the current binding (T8a). Episodes that woke an archived habit
do not feed a new candidate, so a duplicate of an archived habit is not born.
"""
from __future__ import annotations

from ..learning.triggers import matches
from .habit_state import ARCHIVED


def cold_stage(context, legitimacy) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """Wakes by habit id, and the reactive events they consumed."""
    state, report = context.state, context.report
    wakes: dict[str, tuple[str, str]] = {}
    consumed: set[str] = set()
    demand = [reaction for reaction in context.draft["reactions"] if reaction["reaction"] in {"miss", "near_miss"}]
    for habit_id in sorted(state["habits"]):
        current = state["habits"][habit_id]["state"]
        if current not in ARCHIVED:
            continue
        trigger = state["frozen"][habit_id]["trigger"]
        matched = [item["event_id"] for item in demand if matches(trigger, item["context"])[0] == "applicable"]
        entry = {"habit_id": habit_id, "state": current, "matches": matched}
        if len(matched) >= context.parameters["cold_matches"]:
            if current == "dormant":
                wakes[habit_id] = ("T6", f"{len(matched)} window events matched the archived trigger")
            elif legitimacy.get(habit_id, {}).get("compatible"):
                wakes[habit_id] = ("T8a", f"{len(matched)} window events matched; fresh compatibility admitted")
            else:
                entry["refused"] = "compatibility_not_admitted"
            if habit_id in wakes:
                consumed.update(matched)
        report["cold_checks"]["dormant_matches" if current == "dormant" else "extinct_matches"].append(entry)
    return wakes, consumed
