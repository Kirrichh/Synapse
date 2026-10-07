"""Which authorities depend on which bases (review §8.2): the court's dependency projection of memory state.

The projection is read from memory state alone, in the vocabulary of W3C
PROV-DM. Each edge says that its first node depends on its second:

* a case ``hadMember`` the recorded results it carries;
* a hypothesis status ``wasDerivedFrom`` the recorded answer it was read from
  and the recorded answer of the check that decided it, and
  ``wasGeneratedBy`` the case that holds that check;
* a version of semantic knowledge ``wasDerivedFrom`` the recorded answer it was
  read from; a correction ``wasRevisionOf`` the version it corrects;
* a learned habit ``wasDerivedFrom`` its basis cases and ``used`` the versions
  its basis sessions admitted.

``dependents`` answers the question the review asks — which authorities
depended on a changed basis — by following support (every relation except
``wasRevisionOf``: a revision is history, not support) backwards. The answer
is complete only relative to what memory state records: every link the state
cannot name (a status decided without a recorded check, a basis case no longer
retained, a tombstone that does not name its results) is listed as unknown,
never read as absent. The graph describes derivation; it proves neither the
truth of a source nor the independence of two sources.

When the operator forgets a case, three separate things happen and are
reported separately: the data leaves store D (the retention act, already
recorded), the versions read from the forgotten results leave the search index
(``withdrawn``: they stay in the timeline as history, and no session is offered
them as candidates), and the authorities that depended on them are revoked — a
hypothesis status decided before the forget returns to ``provisional`` (a
revoked confirmation does not make the content false; a check made afterwards
stands), a learned habit whose retained basis no longer
suffices is archived (TR), and a habit whose basis admitted a withdrawn
version goes to probation (TC), as for a correction. The stage reads state
only, so a window that could not act (an emergency) is caught up by the next.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Mapping

from .habit_state import EFFECTIVE

DEPENDENCIES_V1 = "synapse.memory.dependencies/v1"
SUPPORT = ("hadMember", "wasDerivedFrom", "wasGeneratedBy", "used")
_UNRETAINED = {"forgotten", "rolled_up"}
#: Forced transitions that already archive a habit; a lost basis archives over a conflict's probation.
_ARCHIVING = {"TS", "TR", "TV"}


def _basis_runs(state, habit_id) -> set[str]:
    runs = set()
    for qid in state["frozen"][habit_id]["habit"]["born_from"]["episodes"]:
        quantum = state["quanta"].get(qid)
        if quantum is not None and quantum.get("replay_ref") is not None:
            runs.add(quantum["replay_ref"]["run_id"])
    return runs


def graph(state: Mapping[str, Any]) -> dict[str, Any]:
    """The dependency edges memory state records, and the links it cannot name."""
    edges: set[tuple[str, str, str]] = set()
    unknown: set[tuple[str, str]] = set()
    for qid, quantum in state["quanta"].items():
        for ref in quantum.get("evidence_refs") or []:
            edges.add((f"case:{qid}", "hadMember", f"observation:{ref}"))
    for hypothesis_id, entry in state["hypotheses"].items():
        node = f"hypothesis:{hypothesis_id}"
        if entry.get("source_ref") is not None:
            edges.add((node, "wasDerivedFrom", f"observation:{entry['source_ref']}"))
        check = (entry.get("check_ref") or {}).get("evidence")
        if check is not None:
            edges.add((node, "wasDerivedFrom", f"observation:{check}"))
        elif entry["status"] != "provisional":
            unknown.add((node, "check_observation"))
        if entry.get("basis") is not None:
            edges.add((node, "wasGeneratedBy", f"case:{entry['basis']}"))
    knowledge = state.get("knowledge") or {"versions": {}, "uses": {}}
    for statement_id, entry in knowledge["versions"].items():
        edges.add((f"statement:{statement_id}", "wasDerivedFrom", f"observation:{entry['record']['source']['ref']}"))
        for corrected_by in [entry.get("corrected_by"), *(item["corrected_by"] for item in entry.get(
                "earlier_known", []))]:
            if corrected_by is not None:
                edges.add((f"statement:{corrected_by}", "wasRevisionOf", f"statement:{statement_id}"))
    for habit_id, frozen in state["frozen"].items():
        node = f"habit:{habit_id}"
        for qid in frozen["habit"]["born_from"]["episodes"]:
            edges.add((node, "wasDerivedFrom", f"case:{qid}"))
            if qid not in state["quanta"]:
                unknown.add((node, f"basis_case:{qid}"))
        runs = _basis_runs(state, habit_id)
        for statement_id, used_by in knowledge["uses"].items():
            if runs & set(used_by):
                edges.add((node, "used", f"statement:{statement_id}"))
    for qid, tombstone in state["retention"]["tombstones"].items():
        if "refs" not in tombstone:
            unknown.add((f"case:{qid}", "forgotten_results"))
    return {"schema_version": DEPENDENCIES_V1, "edges": sorted(edges), "unknown": sorted(unknown)}


def dependents(projection: Mapping[str, Any], changed: Iterable[str]) -> dict[str, list[str]]:
    """Every node that depends, through support, on a changed one; by kind, without the changed nodes."""
    supported: dict[str, set[str]] = {}
    for source, relation, target in projection["edges"]:
        if relation in SUPPORT:
            supported.setdefault(target, set()).add(source)
    start = set(changed)
    found, frontier = set(), list(start)
    while frontier:
        node = frontier.pop()
        for dependent in supported.get(node, ()):
            if dependent not in found and dependent not in start:
                found.add(dependent)
                frontier.append(dependent)
    grouped: dict[str, list[str]] = {}
    for node in sorted(found):
        kind, _, identity = node.partition(":")
        grouped.setdefault(kind, []).append(identity)
    return grouped


def forgotten_dependents(state: Mapping[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, list[str]]]]:
    """Per tombstone, in the order they were applied: the case, its tombstone and what depended on it."""
    projection = graph(state)
    found = []
    for qid, tombstone in sorted(state["retention"]["tombstones"].items(),
                                 key=lambda item: (item[1]["window"], item[0])):
        changed = [f"case:{qid}", *(f"observation:{ref}" for ref in tombstone.get("refs", []))]
        found.append((qid, tombstone, dependents(projection, changed)))
    return found


def _retained_basis(state, parameters, habit_id) -> tuple[int, int]:
    episodes = state["frozen"][habit_id]["habit"]["born_from"]["episodes"]
    retained = sum(1 for qid in episodes
                   if qid in state["quanta"] and state["quanta"][qid]["retention_state"] not in _UNRETAINED)
    return retained, min(parameters["birth_episodes"], len(episodes))


def forgotten_stage(context, habits, forced, knowledge) -> None:
    """Withdraw from search and revoke what depended on forgotten results; ``knowledge`` is this window's
    knowledge decision, extended in place."""
    state = context.state
    tombstones = state["retention"]["tombstones"]
    if not tombstones:
        return
    projection = graph(state)
    supports: dict[str, set[str]] = {}
    for source, relation, target in projection["edges"]:
        if source.startswith("habit:") and relation in SUPPORT:
            supports.setdefault(source.partition(":")[2], set()).add(target)
    versions = {**(state.get("knowledge") or {"versions": {}})["versions"], **knowledge["versions"]}
    hypotheses = {**state["hypotheses"], **knowledge["hypotheses"]}
    for qid, tombstone, found in forgotten_dependents(state):
        mark = {"tombstone": tombstone["tombstone"], "window": context.window}
        # A version observed again after the forget is a new observation and stays in the index.
        withdrawn = [statement_id for statement_id in found.get("statement", [])
                     if versions[statement_id].get("withdrawn") is None
                     and versions[statement_id]["known_from"] <= tombstone["window"]]
        for statement_id in withdrawn:
            versions[statement_id] = knowledge["versions"][statement_id] = {
                **copy.deepcopy(versions[statement_id]), "withdrawn": mark}
        # A status decided after the forget was checked afresh and stands.
        revoked = [hypothesis_id for hypothesis_id in found.get("hypothesis", [])
                   if hypotheses[hypothesis_id]["status"] != "provisional"
                   and hypotheses[hypothesis_id]["window"] <= tombstone["window"]]
        for hypothesis_id in revoked:
            hypotheses[hypothesis_id] = knowledge["hypotheses"][hypothesis_id] = {
                **copy.deepcopy(hypotheses[hypothesis_id]), "status": "provisional",
                "reason": f"basis_forgotten:{tombstone['tombstone']}", "window": context.window}
        archived, probation, kept = [], [], []
        for habit_id in found.get("habit", []):
            metadata = habits.get(habit_id)
            if metadata is None or metadata["state"] not in EFFECTIVE or forced.get(habit_id, ("",))[0] in _ARCHIVING:
                continue
            support = supports.get(habit_id, set())
            retained, required = _retained_basis(state, context.parameters, habit_id)
            if f"case:{qid}" in support and retained < required:
                forced[habit_id] = ("TR", f"{retained} of {required} basis cases retained after forgetting")
                archived.append(habit_id)
            elif metadata["state"] in {"born", "active"} and any(
                    f"statement:{statement_id}" in support for statement_id in withdrawn):
                forced.setdefault(habit_id, ("TC", f"a version its basis admitted was forgotten "
                                                   f"({tombstone['tombstone']})"))
                probation.append(habit_id)
            elif f"case:{qid}" in support:
                kept.append(habit_id)
        # Reported once: when the tombstone is applied, or when it changes anything later.
        if withdrawn or revoked or archived or probation or tombstone["window"] == state["window"]:
            context.report["dependencies"].append({
                "qid": qid, "tombstone": tombstone["tombstone"], "index_withdrawn": withdrawn,
                "authority_revoked": {"hypotheses": revoked, "habits_archived": archived,
                                      "habits_on_probation": probation},
                "basis_still_holds": kept, "graph_unknown": [list(item) for item in projection["unknown"]]})
