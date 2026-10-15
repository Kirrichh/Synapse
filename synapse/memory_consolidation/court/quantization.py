"""Substage 7a: one quantum per case, element roots, tiers and the retention plan.

A repeated case keeps its ``qid`` and increases its statistics. The tier is
recomputed every time from the first matching rule (spec part 3 §8.1), and a
quantum that was high keeps its basis. A known quantum that becomes the basis
of a live habit rises to high at once; one whose basis lost its force returns
to medium after ``grace_windows`` (§8.5). Each new case names its raw trace and
replay data in the owner's custody; the plan says when retention may act on
it, never what retention did — those facts arrive from the retention passes.
Element roots are recomputed over the cumulative membership of every touched
element. An emergency window marks its quanta provisional and plans no
deletion. Time comes only from the side log; the coverage of measured cases is
published and silence is forbidden.
"""
from __future__ import annotations

import copy
from typing import Any

from ..quanta import element_record, part_record, retention_plan, tier, weight
from .habit_state import EFFECTIVE


def _bases(draft, decision, admitted) -> dict[str, set]:
    event_qid = {(reaction["run_id"], reaction["event_id"]): reaction["qid"] for reaction in draft["reactions"]}
    return {"birth": {qid for birth in admitted for qid in birth["habit"]["born_from"]["episodes"]},
            "trust": {event_qid.get((event["run_id"], event["event_id"]))
                      for item in decision["sections"]["trust_decisions"] for event in item["events"]} - {None},
            "pool": {episode["qid"] for entry in decision["pool"].values() if entry is not None
                     for episode in entry["episodes"]}}


def _stats(previous, case) -> dict[str, Any]:
    outcomes = {} if previous is None else dict(previous["stats"]["outcomes"])
    outcomes[case["outcome"]] = outcomes.get(case["outcome"], 0) + 1
    return {"occurrences": 1 if previous is None else previous["stats"]["occurrences"] + 1, "outcomes": outcomes,
            "cost": {**case["cost"], "seconds": case["seconds"]},
            "complexity_inputs": {"steps": len(case["steps"]), "external_calls": case["cost"]["external_calls"],
                                  "branches": case["reactions"], "retries": case["cost"]["retries"]}}


def _entry(case, previous, measure, tier_name, rule, element, part, parameters, consolidation_id, provisional,
           held):
    """The quantum of one case; ``held`` names its raw trace and replay data in the owner's custody."""
    return {"qid": case["quantum"]["id"], "canonical": case["quantum"]["canonical"], "element_id": element,
            "part_refs": [f"{element}/{part}"],
            "syn_form": {
                "memory_class": "Episodic", "goal_ref": case["marker_id"], "context": case["context"],
                "actions": case["steps"] + [{"step": "habit", "habit_id": habit_id} for habit_id in case["habits"]],
                "result_class": case["outcome"], "decisions": [], "defects": case["defects"]},
            "replay_ref": {"run_id": case["run_id"], "op_scope": case["op_scope"], "data_ref": held["data_ref"],
                           "positions": held["positions"], "recorded_results": case["recorded_results"],
                           "event_budget": parameters["replay_event_budget"]},
            "raw_ref": held["raw_ref"], "executor": held["executor"], "evidence_refs": case["evidence_refs"],
            "born_in": consolidation_id if previous is None else previous["born_in"],
            "stats": _stats(previous, case), "weight": measure["W"], "weight_parts": measure, "tier": tier_name,
            "tier_rule": rule, "replay_status": case["replay"] or "replay_unavailable", "retention_state": "full",
            "retention": None, "basis_lost": None, "provisional": provisional}


def _quantum(state, quanta, case, bases, parameters, configuration, consolidation_id, provisional, held):
    qid = case["quantum"]["id"]
    previous = copy.deepcopy(state["quanta"].get(qid) or quanta.get(qid))
    measure = weight(parameters, steps=len(case["steps"]), external_calls=case["cost"]["external_calls"],
                     branches=case["reactions"], retries=case["cost"]["retries"], tokens=case["cost"]["tokens"],
                     seconds=case["seconds"], gas=case["cost"]["gas"])
    name, rule = tier(parameters, replay_status=case["replay"] or "replay_unavailable", basis=qid in bases["birth"],
                      anchored_confirmed=case["anchored_confirmed"], weight_value=measure["W"],
                      verdict=case["verdict"], in_pool=qid in bases["pool"], trust_basis=qid in bases["trust"])
    if previous is not None and previous["tier"] == "high" and name != "high":
        name, rule = "high", previous["tier_rule"]
    element = case["element"] or configuration.element
    return _entry(case, previous, measure, name, rule, element, case["element_part"] or "off_plan", parameters,
                  consolidation_id, provisional, held)


def _live_bases(state, decision, admitted) -> set[str]:
    """Quanta that are the basis of a live learned habit after this decision."""
    habits = {**state["habits"], **decision["habits"]}
    frozen = {**state["frozen"], **{birth["habit"]["id"]: {"habit": birth["habit"]} for birth in admitted}}
    live = {birth["habit"]["id"] for birth in admitted} | {
        habit_id for habit_id, metadata in habits.items() if metadata["state"] in EFFECTIVE}
    return {qid for habit_id in live if habit_id in frozen for qid in frozen[habit_id]["habit"]["born_from"]["episodes"]}


def _retier(state, quanta, live, parameters, window) -> None:
    """Known quanta whose decision basis appeared or lost its force (§8.5), updated in ``quanta``."""
    for qid, known in sorted(state["quanta"].items()):
        if qid in quanta or known["retention_state"] in {"forgotten", "rolled_up"}:
            continue
        entry = None
        if qid in live and (known["tier"] != "high" or known["basis_lost"] is not None):
            entry = {**copy.deepcopy(known), "tier": "high", "tier_rule": "decision_basis", "retention": None,
                     "basis_lost": None}
        elif qid not in live and known["tier"] == "high" and known["tier_rule"] == "decision_basis":
            if known["basis_lost"] is None:
                entry = {**copy.deepcopy(known), "basis_lost": window}
            elif window - known["basis_lost"] >= parameters["grace_windows"] and known["retention_state"] == "full":
                entry = {**copy.deepcopy(known), "tier": "medium", "tier_rule": "basis_lost", "basis_lost": None}
                entry["retention"] = _plan(parameters, entry, window, False)
        if entry is not None:
            quanta[qid] = entry


def _plan(parameters, entry, window, provisional) -> dict[str, Any] | None:
    plan = retention_plan(parameters, qid=entry["qid"], tier_name=entry["tier"], window=window,
                          provisional=provisional, replay_status=entry["replay_status"])
    return None if plan is None else {"action": plan["action"], "due": plan["not_before_window"]}


def _roots(state, parts) -> dict[str, str]:
    roots = {}
    for element in sorted(parts):
        cumulative = {key: list(value) for key, value in state["parts"].get(element, {}).items()}
        for part, qids in parts[element].items():
            cumulative[part] = sorted(set(cumulative.get(part, [])) | set(qids))
        part_roots = [part_record(element, part, qids)["root"] for part, qids in sorted(cumulative.items())]
        roots[element] = element_record(element, part_roots)["root"]
    return roots


def quantize(state, draft, decision, configuration, admitted, window, custody) -> dict[str, Any]:
    """Quanta, membership, the report section and the side-time accounting of one window.

    ``custody`` maps each case's ``qid`` to its raw trace, replay data and executor in store D.
    """
    parameters, provisional = configuration.parameters, draft["mode"] == "emergency"
    bases = _bases(draft, decision, admitted)
    quanta: dict[str, dict[str, Any]] = {}
    parts: dict[str, dict[str, set]] = {}
    plan, tiers = [], {"high": 0, "medium": 0, "low": 0}
    for case in draft["cases"]:
        entry = _quantum(state, quanta, case, bases, parameters, configuration, draft["consolidation_id"], provisional,
                         custody[case["quantum"]["id"]])
        entry["retention"] = _plan(parameters, entry, window, provisional)
        quanta[entry["qid"]] = entry
        tiers[entry["tier"]] += 1
        parts.setdefault(entry["element_id"], {}).setdefault(entry["part_refs"][0], set()).add(entry["qid"])
        retention = retention_plan(parameters, qid=entry["qid"], tier_name=entry["tier"], window=window,
                                   provisional=provisional, replay_status=entry["replay_status"])
        if retention is not None:
            plan.append(retention)
    if not provisional:
        _retier(state, quanta, _live_bases(state, decision, admitted), parameters, window)
    cases = [quanta[case["quantum"]["id"]] for case in draft["cases"]]
    measured = sum(1 for entry in cases if entry["weight_parts"]["measured_time"])
    coverage = measured / len(cases) if cases else 1.0
    membership = {element: {part: sorted(qids) for part, qids in value.items()} for element, value in parts.items()}
    return {"quanta": quanta, "parts": membership,
            "section": {"quanta_added": sum(1 for qid in quanta if qid not in state["quanta"]),
                        "quanta": sorted(quanta), "tiers": tiers, "element_roots": _roots(state, membership),
                        "retention_plan": plan, "provisional": provisional},
            "side_time": {"episodes_with_time": measured, "episodes_without_time": len(cases) - measured,
                          "coverage": coverage, "sufficiently_covered": coverage >= parameters["time_coverage"],
                          "silence_forbidden": True}}
