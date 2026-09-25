"""Current memory state as the fold of applied consolidation reports.

Trust, effectiveness states, pending evidence, the candidate pool, quanta
statistics and session cursors are never edited in place: each applied report
carries the exact values its decisions produced (its ``apply`` section) and the
state is the ordered fold of those sections (spec part 3 §2.2). The fold is the
only reader of that section, so re-reading the journal always rebuilds the same
state, and an audit can recompute a report from its inputs and compare.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from . import records

EMPTY_STATE: dict[str, Any] = {
    "window": 0,
    "habits": {},          # learned habit id -> metadata
    "frozen": {},          # learned habit id -> {"habit": record, "trigger": record}
    "declared": {},        # declared (layer 1) habit id -> observed metadata
    "slow_only": [],       # trigger condition keys without a fast path
    "pool": {},            # candidate key -> candidate entry
    "quanta": {},          # qid -> mutable quantum fields
    "parts": {},           # element -> part -> sorted qids
    "cursors": {},         # run id -> consolidated history position
    "digest": None,        # last session digest record
    "consolidations": [],  # applied consolidation ids, in order
}
APPLY_FIELDS = frozenset({"habits", "frozen", "declared", "slow_only", "pool", "quanta", "parts", "cursors",
                          "digest"})


def empty_state() -> dict[str, Any]:
    return copy.deepcopy(EMPTY_STATE)


def apply_report(state: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """The state after one applied report (pure)."""
    section = report["apply"]
    if type(section) is not dict or set(section) != APPLY_FIELDS:
        raise ValueError("report apply section has an unknown shape")
    result = copy.deepcopy(dict(state))
    for habit_id, entry in section["frozen"].items():
        if habit_id in result["frozen"]:
            raise ValueError("a learned habit is born once")
        records.verify(entry["habit"], "learned_habit")
        records.verify(entry["trigger"], "habit_trigger")
        if entry["habit"]["id"] != habit_id or entry["habit"]["trigger"] != entry["trigger"]["id"]:
            raise ValueError("a learned habit names another trigger")
        result["frozen"][habit_id] = copy.deepcopy(entry)
    for habit_id, metadata in section["habits"].items():
        if habit_id not in result["frozen"]:
            raise ValueError("metadata of an unknown learned habit")
        result["habits"][habit_id] = copy.deepcopy(metadata)
    for habit_id, metadata in section["declared"].items():
        result["declared"][habit_id] = copy.deepcopy(metadata)
    result["slow_only"] = copy.deepcopy(section["slow_only"])
    for key, entry in section["pool"].items():
        if entry is None:
            result["pool"].pop(key, None)
        else:
            result["pool"][key] = copy.deepcopy(entry)
    for qid, entry in section["quanta"].items():
        result["quanta"][qid] = copy.deepcopy(entry)
    for element, parts in section["parts"].items():
        target = result["parts"].setdefault(element, {})
        for part, qids in parts.items():
            target[part] = sorted(set(target.get(part, [])) | set(qids))
    for run_id, cursor in section["cursors"].items():
        result["cursors"][run_id] = copy.deepcopy(cursor)
    if section["digest"] is not None:
        result["digest"] = copy.deepcopy(section["digest"])
    result["window"] += 1
    result["consolidations"].append(report["consolidation_id"])
    return result


def fold(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    state = empty_state()
    for report in reports:
        state = apply_report(state, report)
    return state


def live_habits(state: Mapping[str, Any]) -> list[str]:
    """Learned habits in an effective state (born, active, probation)."""
    return sorted(habit_id for habit_id, metadata in state["habits"].items()
                  if metadata["state"] in {"born", "active", "probation"})
