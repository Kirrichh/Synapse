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

from .. import records
from .knowledge import placed_in

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
    # applied retention passes, rollup aggregates of tail quanta and tombstones of forgotten ones
    "retention": {"cursor": 0, "rollups": [], "tombstones": {}},
    "hypotheses": {},      # hypothesis id -> the status its latest recorded check gave it
    # statement id -> its version (record, embedding, transaction time); statement id -> runs that admitted it;
    # one source's statements about one slot and start -> the world's order they were stated in
    "knowledge": {"versions": {}, "uses": {}, "stated": {}},
    "consolidations": [],  # applied consolidation ids, in order
}
APPLY_FIELDS = frozenset({"habits", "frozen", "declared", "slow_only", "pool", "quanta", "parts", "cursors",
                          "digest", "retention", "hypotheses", "knowledge"})
#: Reports applied before semantic knowledge existed carry no knowledge section.
_APPLY_FIELDS_BEFORE_KNOWLEDGE = APPLY_FIELDS - {"knowledge"}


#: The fold's own version: a state snapshot records it, and a snapshot of another version is never used.
#: Any change to how ``apply_report`` folds a report changes this version. v2 (recheck of 556624d): the
#: knowledge section folds the world's order of statements (``stated``) too, each report adding to it.
PROJECTION_V2 = "synapse.memory.state-projection/v2"


def empty_state() -> dict[str, Any]:
    return copy.deepcopy(EMPTY_STATE)


def apply_report(state: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    """The state after one applied report (pure)."""
    section = report["apply"]
    if type(section) is not dict or set(section) not in (APPLY_FIELDS, _APPLY_FIELDS_BEFORE_KNOWLEDGE):
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
    result["retention"] = copy.deepcopy(section["retention"])
    for hypothesis_id, entry in section["hypotheses"].items():
        result["hypotheses"][hypothesis_id] = copy.deepcopy(entry)
    knowledge = section.get("knowledge") or {"versions": {}, "uses": {}}
    for statement_id, entry in knowledge["versions"].items():
        records.verify(entry["record"], "statement")
        if entry["record"]["id"] != statement_id:
            raise ValueError("a knowledge version names another statement")
        result["knowledge"]["versions"][statement_id] = copy.deepcopy(entry)
    for statement_id, runs in knowledge["uses"].items():
        result["knowledge"]["uses"][statement_id] = sorted(runs)
    # A report applied before the order of statements was kept carries none, a state folded then holds none.
    # A report carries what its decision added to each order, joined as the court joined it.
    stated = result["knowledge"].setdefault("stated", {})
    for key, group in knowledge.get("stated", {}).items():
        stated[key] = {"source": copy.deepcopy(group["source"]),
                       "statements": placed_in(stated.get(key) or {"statements": []}, group["statements"])}
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
