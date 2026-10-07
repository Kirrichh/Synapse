"""The court's record of semantic knowledge (refinement §15): the one writer of its timeline.

A session only declares what it read (``knowledge_declared``); the court folds
every declaration of a window into memory, in the order the world was read (the
gateway's sequence of each statement's source observation), and gives each
version its transaction time — the window it became known in:

* the very same statement (same source version) is a copy and adds nothing;
  the same source read again stating the same thing is a copy too: repetition
  never raises confidence;
* the same source stating something else about the same slot and start is a
  **correction**: the earlier version's transaction time closes
  (``known_until``, ``corrected_by``) and it stays in history, answerable as
  of any earlier window;
* another source stating something else about the same slot and start is a
  **conflict**: both versions stay current and admission lets neither win by
  similarity, confidence or insertion time;
* any other statement is a new version; its valid time places it on the
  timeline (a late report about the past never overrides a later start);
* a version memory held before — closed by a correction, or withdrawn with
  forgotten results — observed again is held again from this window: it
  corrects what holds its slot now (a correction back to an earlier answer,
  20 → 25 → 20), and the periods it was held before stay in its history
  (``earlier_known``), so every earlier window still answers as it did
  (review AUD-3).

A correction revises what depended on the corrected answer, as a truth
maintenance system withdraws beliefs with their premises (Doyle 1979), without
deleting anything: hypotheses read from that answer, or checked against the
corrected source, return to ``provisional`` with the reason; learned habits
whose basis episodes admitted the corrected version go to probation (TC).
The runs that admitted each version are recorded as its uses.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from ..knowledge.statements import slot, source_identity
from ..records import canonical, digest, verify
from .habit_state import EFFECTIVE

EVENTS = ("knowledge_declared", "memory_admission")


def knowledge_events(found: Mapping[str, list], run_id: str, observed: Mapping[int, int]) -> dict[str, list]:
    """A session's statement declarations, each with the gateway sequence of the observation it was read from
    (``observed``, by history position; a declaration with none is the session's integrity problem and is not
    folded), and admissions of statements, in history order."""
    declared = [{"run_id": run_id, "position": position, "statement": event["statement"],
                 "vector": event.get("vector"), "embedded_by": event.get("embedded_by"),
                 "observed": observed[position]}
                for position, event in found.get("knowledge_declared", []) if position in observed]
    uses = [{"run_id": run_id, "statement": event["fact"]} for _, event in found.get("memory_admission", [])
            if event.get("decision") == "admitted" and str(event.get("fact") or "").startswith("stm_")]
    return {"declared": declared, "uses": uses}


def _content(record) -> str:
    return canonical({"value": record["value"], "polarity": record["polarity"], "valid": record["valid"]})


def _revise_hypotheses(state, corrected, window, revised) -> list[str]:
    """Hypotheses read from the corrected answer, or checked against its source, return to provisional."""
    source = source_identity(corrected["record"])
    found = []
    for hypothesis_id, entry in sorted({**state["hypotheses"], **revised}.items()):
        record = entry["record"]
        from_answer = (digest({"tool": record["source"]["tool"], "args": record["source"]["args"]}) == source
                       and entry["source_ref"] == corrected["record"]["source"]["ref"])
        checked_by = digest({"tool": record["check"]["tool"], "args": record["check"]["args"]}) == source
        if entry["status"] == "provisional" or not (from_answer or checked_by):
            continue
        reason = "source_corrected" if from_answer else "check_source_corrected"
        revised[hypothesis_id] = {**copy.deepcopy(entry), "status": "provisional",
                                  "reason": f"{reason}:{corrected['record']['id']}", "window": window}
        found.append(hypothesis_id)
    return found


def _habit_runs(state, habit_id) -> set[str]:
    """The runs a learned habit's basis episodes came from."""
    frozen = state["frozen"].get(habit_id)
    if frozen is None:
        return set()
    runs = set()
    for qid in frozen["habit"]["born_from"]["episodes"]:
        quantum = state["quanta"].get(qid)
        if quantum is not None:
            runs.add(quantum["replay_ref"]["run_id"])
    return runs


def _revise_habits(state, habits, runs, statement_id, forced) -> list[str]:
    """Live habits whose basis admitted the corrected version go to probation."""
    found = []
    for habit_id in sorted(habits):
        metadata = habits[habit_id]
        if metadata["state"] not in EFFECTIVE or metadata.get("superseded_by") is not None:
            continue
        if _habit_runs(state, habit_id) & runs:
            forced.setdefault(habit_id, ("TC", f"a fact its basis admitted was corrected ({statement_id})"))
            found.append(habit_id)
    return found


def _reindex(versions, section, entry, item) -> None:
    """A copy adds no confidence, but its vector restores the search index (review DEEP-5, DEEP-6): a version
    with no vector, or one of another embedder, takes the vector this reading brings. The statement, its
    periods and its uses stay as they are."""
    if item["vector"] is None or item["embedded_by"] is None or (
            entry.get("vector") is not None and entry.get("embedded_by") == item["embedded_by"]):
        return
    identity = entry["record"]["id"]
    versions[identity] = {**copy.deepcopy(versions.get(identity, entry)), "vector": copy.deepcopy(item["vector"]),
                          "embedded_by": item["embedded_by"]}
    section["reindexed"].append({"run_id": item["run_id"], "statement": identity})


EMPTY = {"versions": {}, "uses": {}}


def known_of(state: Mapping[str, Any]) -> Mapping[str, Any]:
    """The knowledge a state holds (a state from before semantic knowledge holds none)."""
    return state.get("knowledge") or EMPTY


def knowledge_stage(context, habits, forced, configuration) -> dict[str, Any]:
    """Versions, uses and hypothesis revisions of one window; the report's ``knowledge`` section."""
    state, window = {**context.state, "knowledge": known_of(context.state)}, context.window
    section = {"declared": [], "copies": [], "corrections": [], "conflicts": [], "revisions": [], "reindexed": []}
    context.report["knowledge"] = section
    versions: dict[str, dict] = {}
    uses: dict[str, list] = {}
    revised: dict[str, dict] = {}
    knowledge = context.draft.get("knowledge") or {"declared": [], "uses": []}
    for item in knowledge["uses"]:
        uses.setdefault(item["statement"], sorted(set(state["knowledge"]["uses"].get(item["statement"], []))))
        uses[item["statement"]] = sorted(set(uses[item["statement"]]) | {item["run_id"]})
    # In the order the world was read — the gateway's sequence of each statement's source — never by the names
    # of the runs that read it: one consolidation of several sessions folds 20 then 25 as 25 (review DEEP-3).
    for item in sorted(knowledge["declared"], key=lambda value: (value["observed"], value["run_id"],
                                                                 value["position"])):
        record = verify(item["statement"], "statement")
        known = {**state["knowledge"]["versions"], **versions}
        earlier = known.get(record["id"])
        if earlier is not None and earlier.get("withdrawn") is None and earlier["known_until"] is None:
            section["copies"].append({"run_id": item["run_id"], "statement": record["id"], "of": record["id"]})
            _reindex(versions, section, earlier, item)
            continue
        own_slot, source = slot(record), source_identity(record)
        current = [entry for entry in known.values() if entry["slot"] == own_slot and entry["known_until"] is None
                   and entry.get("withdrawn") is None and entry["record"]["valid"]["from"] == record["valid"]["from"]]
        same = [entry for entry in current if entry["source_identity"] == source]
        repeated = next((entry for entry in same if _content(entry["record"]) == _content(record)), None)
        if repeated is not None:
            section["copies"].append({"run_id": item["run_id"], "statement": record["id"],
                                      "of": repeated["record"]["id"]})
            _reindex(versions, section, repeated, item)
            continue
        contract = configuration.tools.tools.get(record["source"]["tool"])
        entry = {"record": copy.deepcopy(record), "vector": copy.deepcopy(item["vector"]),
                 "embedded_by": item["embedded_by"] if item["vector"] is not None else None, "slot": own_slot,
                 "source_identity": source, "source": None if contract is None else contract.source,
                 "run_id": item["run_id"], "known_from": window, "known_until": None, "corrected_by": None}
        if earlier is not None:  # Held again: the periods it was held before stay in its history.
            entry["earlier_known"] = [*copy.deepcopy(earlier.get("earlier_known", [])), {
                "from": earlier["known_from"], "until": window if earlier["known_until"] is None
                else earlier["known_until"], "corrected_by": earlier.get("corrected_by"),
                "withdrawn": copy.deepcopy(earlier.get("withdrawn"))}]
        versions[record["id"]] = entry
        section["declared"].append({"run_id": item["run_id"], "statement": record["id"], "slot": own_slot,
                                    **({"held_again": True} if earlier is not None else {})})
        for old in same:
            closed = {**copy.deepcopy(old), "known_until": window, "corrected_by": record["id"]}
            versions[old["record"]["id"]] = closed
            runs = set(state["knowledge"]["uses"].get(old["record"]["id"], [])) | set(
                uses.get(old["record"]["id"], []))
            section["corrections"].append({"statement": old["record"]["id"], "corrected_by": record["id"],
                                           "slot": own_slot})
            section["revisions"].append({
                "statement": old["record"]["id"], "hypotheses": _revise_hypotheses(state, old, window, revised),
                "habits": _revise_habits(state, habits, runs, old["record"]["id"], forced)})
        others = [entry for entry in current if entry["source_identity"] != source
                  and _content(entry["record"]) != _content(record)]
        if others:
            section["conflicts"].append({"statement": record["id"], "slot": own_slot,
                                         "with": sorted(entry["record"]["id"] for entry in others)})
    return {"versions": versions, "uses": uses, "hypotheses": revised}
