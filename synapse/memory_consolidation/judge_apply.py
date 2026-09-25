"""The court's application (``integrate``, stage 7 and substage 7a).

Assembles the one legal act of memory change: the consolidation report. Its
decision sections explain every change down to events, cases and recorded
results (И4); its ``apply`` section carries the exact values the projection
folds. Order inside the transaction follows spec part 2 §4.7: verdicts, trust
and pending evidence, births with their Gold gate decisions, supersessions,
automaton transitions, quanta and element roots with the retention plan, the
digest; the registry entry is written last by the court, after this report.

A birth whose behavior Gold does not admit is cancelled here and its candidate
stays in the pool with the refusal as its reason. Legitimacy is never argued
with: a behavior Gold no longer admits leaves the next boundary whatever its
effectiveness.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import records
from .judge_decide import EFFECTIVE
from .operations import canonical
from .quanta import (
    element_record,
    part_record,
    retention_plan,
    tier,
    weight,
)
from .triggers import condition_key

REPORT_V1 = "synapse.memory.consolidation-report/v1"
BOUNDARY_V1 = "synapse.memory.snapshot-boundary/v1"
DIGEST_V1 = "synapse.memory.session-digest/v1"


def _quanta(state, draft, parameters, configuration, decision, admitted, window, provisional):
    """Substage 7a: one quantum per case, roots of touched elements, the retention plan."""
    basis = {qid for birth in admitted for qid in birth["habit"]["born_from"]["episodes"]}
    event_qid = {reaction["event_id"]: reaction["qid"] for reaction in draft["reactions"]}
    trust_basis = {event_qid.get(event) for item in decision["sections"]["trust_decisions"]
                   for event in item["events"]} - {None}
    pooled = {episode["qid"] for entry in decision["pool"].values() if entry is not None
              for episode in entry["episodes"]}
    quanta: dict[str, dict[str, Any]] = {}
    parts: dict[str, dict[str, list[str]]] = {}
    plan, tiers, measured, unmeasured = [], {"high": 0, "medium": 0, "low": 0}, 0, 0
    for case in draft["cases"]:
        qid = case["quantum"]["id"]
        element = case["element"] or configuration.element
        part = case["element_part"] or "off_plan"
        previous = copy.deepcopy(state["quanta"].get(qid) or quanta.get(qid))
        occurrences = 1 if previous is None else previous["stats"]["occurrences"] + 1
        outcomes = {} if previous is None else dict(previous["stats"]["outcomes"])
        outcomes[case["outcome"]] = outcomes.get(case["outcome"], 0) + 1
        measure = weight(parameters, steps=len(case["steps"]), external_calls=case["cost"]["external_calls"],
                         branches=case["reactions"], retries=case["cost"]["retries"], tokens=case["cost"]["tokens"],
                         seconds=case["seconds"], gas=case["cost"]["gas"])
        if measure["measured_time"]:
            measured += 1
        else:
            unmeasured += 1
        name, rule = tier(parameters, replay_status=case["replay"] or "replay_unavailable",
                          basis=qid in basis, anchored_confirmed=case["anchored_confirmed"],
                          weight_value=measure["W"], verdict=case["verdict"], in_pool=qid in pooled,
                          trust_basis=qid in trust_basis)
        if previous is not None and previous["tier"] == "high" and name != "high":
            name, rule = "high", previous["tier_rule"]
        tiers[name] += 1
        entry = {"qid": qid, "canonical": case["quantum"]["canonical"], "element_id": element,
                 "part_refs": [f"{element}/{part}"],
                 "syn_form": None if name == "low" else {
                     "memory_class": "Episodic", "goal_ref": case["marker_id"], "context": case["context"],
                     "actions": case["steps"] + [{"step": "habit", "habit_id": habit_id} for habit_id in case["habits"]],
                     "result_class": case["outcome"], "decisions": [], "defects": case["defects"]},
                 "replay_ref": None if name == "low" else {"run_id": case["run_id"], "op_scope": case["op_scope"],
                                                           "recorded_results": case["recorded_results"],
                                                           "event_budget": parameters["replay_event_budget"]},
                 "evidence_refs": case["evidence_refs"],
                 "born_in": draft["consolidation_id"] if previous is None else previous["born_in"],
                 "stats": {"occurrences": occurrences, "outcomes": outcomes,
                           "cost": {**case["cost"], "seconds": case["seconds"]},
                           "complexity_inputs": {"steps": len(case["steps"]),
                                                 "external_calls": case["cost"]["external_calls"],
                                                 "branches": case["reactions"], "retries": case["cost"]["retries"]}},
                 "weight": measure["W"], "weight_parts": measure, "tier": name, "tier_rule": rule,
                 "replay_status": case["replay"] or "replay_unavailable", "retention_state": "full",
                 "provisional": provisional}
        quanta[qid] = entry
        parts.setdefault(element, {}).setdefault(f"{element}/{part}", []).append(qid)
        retention = retention_plan(parameters, qid=qid, tier_name=name, window=window, provisional=provisional,
                                   replay_status=entry["replay_status"])
        if retention is not None:
            plan.append(retention)
    roots = {}
    for element in sorted(parts):
        cumulative = {**{key: list(value) for key, value in state["parts"].get(element, {}).items()}}
        for part, qids in parts[element].items():
            cumulative[part] = sorted(set(cumulative.get(part, [])) | set(qids))
        part_roots = [part_record(element, part, qids)["root"] for part, qids in sorted(cumulative.items())]
        roots[element] = element_record(element, part_roots)["root"]
    coverage = measured / (measured + unmeasured) if measured + unmeasured else 1.0
    return quanta, {element: {part: sorted(set(qids)) for part, qids in value.items()}
                    for element, value in parts.items()}, {
        "quanta_added": sum(1 for case in draft["cases"] if case["quantum"]["id"] not in state["quanta"]),
        "quanta": sorted(quanta), "tiers": tiers, "element_roots": roots, "retention_plan": plan,
        "provisional": provisional}, {
        "episodes_with_time": measured, "episodes_without_time": unmeasured, "coverage": coverage,
        "sufficiently_covered": coverage >= parameters["time_coverage"], "silence_forbidden": True}


def boundary_content(state_after: Mapping[str, Any], legitimacy: Mapping[str, Any], consolidation_id: str) -> dict:
    """The runtime slice: admitted, effective learned habits minus slow-only triggers."""
    habits = []
    slow = [canonical(item) for item in state_after["slow_only"]]
    for habit_id in sorted(state_after["habits"]):
        metadata = state_after["habits"][habit_id]
        frozen = state_after["frozen"][habit_id]
        verdict = legitimacy.get(habit_id, {})
        if metadata["state"] not in EFFECTIVE or not verdict.get("admitted"):
            continue
        if canonical(condition_key(frozen["trigger"])) in slow:
            continue
        habits.append({"habit_id": habit_id, "trigger": frozen["trigger"], "habit": frozen["habit"],
                       "state": metadata["state"], "priority": metadata["priority"],
                       "context_trust": metadata["trust"], "energy_cost": metadata["energy_cost"],
                       "publication": verdict.get("publication")})
    declared = {habit_id: metadata["context_trust"] for habit_id, metadata in sorted(state_after["declared"].items())}
    return {"schema_version": BOUNDARY_V1, "consolidation_id": consolidation_id, "habits": habits,
            "slow_only": copy.deepcopy(state_after["slow_only"]), "declared": declared}


def _digest(draft, quanta, boundary_id, slow_only, consolidation_id) -> dict[str, Any]:
    """Structure, not retelling: open markers, quanta as indices, key decisions."""
    by_marker: dict[str, list] = {}
    for case in draft["cases"]:
        if case["marker_id"] is not None:
            by_marker.setdefault(case["marker_id"], []).append(case)
    tasks: dict[str, dict[str, Any]] = {}
    for verdict in draft["verdicts"]:
        cases = by_marker.get(verdict["marker_id"], [])
        last = cases[-1] if cases else None
        task_id = last["task_id"] if last else None
        if task_id is None:
            task_id = next((reaction["task_id"] for reaction in draft["reactions"]
                            if reaction["segment_marker_id"] == verdict["marker_id"]), None)
        task = tasks.setdefault(task_id or "off_plan", {"task_id": task_id, "completed_segments": [],
                                                         "open_segments": []})
        qid = last["quantum"]["id"] if last else None
        if verdict["verdict"] == "confirmed":
            task["completed_segments"].append({"marker_id": verdict["marker_id"], "verdict": "confirmed", "qid": qid})
            continue
        entry = {"marker_id": verdict["marker_id"], "verdict": verdict["verdict"], "qid": qid}
        if "environmental_failure" in verdict["flags"]:
            entry.update(status="blocked", blocked_by="environmental_failure", criterion=verdict["criterion"])
        elif verdict["verdict"] == "uncertain":
            entry["status"] = "rejudge_next_window"
        elif verdict["verdict"] == "failed":
            entry["status"] = "needs_replanning"
        elif verdict["verdict"] == "skipped":
            entry["status"] = "not_reached"
        else:
            entry["status"] = "in_progress"
        if last is not None:
            entry["resume_from"] = {"qid": qid, "step": len(last["steps"])}
        task["open_segments"].append(entry)
    for task in tasks.values():
        task["status"] = "in_progress" if task["open_segments"] else "completed"
    return {"schema_version": DIGEST_V1, "based_on": consolidation_id,
            "task_state": [tasks[key] for key in sorted(tasks)], "snapshot_boundary": boundary_id,
            "slow_only_triggers": copy.deepcopy(slow_only)}


def assemble(*, state, draft, decision, configuration, inputs_hash, window_sessions, legitimacy,
             gates: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """7a, digest and the report for a decision whose every birth Gold admitted.

    ``gates`` holds the Gold decision of each admitted birth by habit id.
    Returns the report, the snapshot boundary and the digest records.
    """
    parameters = configuration.parameters
    mode = draft["mode"]
    window = state["window"] + 1
    sections = copy.deepcopy(decision["sections"])
    pool = copy.deepcopy(decision["pool"])
    admitted = []
    for birth in decision["births"]:
        verdict = gates.get(birth["habit"]["id"])
        if verdict is None or not verdict["admitted"]:
            raise ValueError("a birth reached the report without its admitting Gold gates")
        admitted.append({**birth, "gates": dict(verdict)})
    habits = copy.deepcopy(decision["habits"])
    frozen = {}
    births_view = []
    legitimacy_after = copy.deepcopy(dict(legitimacy))
    for birth in admitted:
        habit_id = birth["habit"]["id"]
        frozen[habit_id] = {"habit": birth["habit"], "trigger": birth["trigger"]}
        metadata = copy.deepcopy(birth["metadata"])
        habits[habit_id] = metadata
        legitimacy_after[habit_id] = {"admitted": True, "compatible": True, "publication": birth["gates"]["publication"]}
        predecessor = (birth.get("boundary") or {}).get("predecessor")
        if predecessor is not None and predecessor in habits:
            habits[predecessor]["superseded_by"] = habit_id
            legitimacy_after[predecessor] = {**legitimacy_after.get(predecessor, {}), "admitted": False,
                                             "superseded_by": habit_id}
        births_view.append({"habit_id": habit_id, "trigger_id": birth["trigger"]["id"],
                            "criteria": birth.get("criteria"), "typed_check": birth.get("typed_check", "successor"),
                            "arbitration": birth.get("arbitration"), "independence": birth.get("independence"),
                            "boundary": birth.get("boundary"), "episodes": birth["basis"],
                            "gates": birth["gates"], "state": metadata["state"], "trust": metadata["trust"]})
    sections["births"] = births_view
    quanta, parts, quantization, side_time = _quanta(state, draft, parameters, configuration, decision, admitted,
                                                     window, mode == "emergency")
    cursors = {item["run_id"]: {"to": item["to"], "head": item["head"]} for item in window_sessions}
    state_after_core = {**copy.deepcopy(dict(state)), "habits": {**state["habits"], **habits},
                        "frozen": {**state["frozen"], **frozen}, "slow_only": decision["slow_only"]}
    boundary = None
    digest_record = None
    if mode != "emergency":
        boundary_body = boundary_content(state_after_core, legitimacy_after, draft["consolidation_id"])
        boundary = records.make("snapshot_boundary", boundary=boundary_body)
        digest_record = records.make("session_digest", digest=_digest(draft, quanta, boundary["id"],
                                                                      decision["slow_only"], draft["consolidation_id"]))
    counsel = draft["counsel"]
    verdicts = draft["verdicts"]
    anchored = sum(1 for item in verdicts if item["evidence"] and item["stage"] in {"1", "1b"})
    birth_episodes = [episode for birth in admitted for episode in birth["basis"] if "qid" in episode]
    report = {
        "schema_version": REPORT_V1, "consolidation_id": draft["consolidation_id"], "mode": mode,
        "window": {"index": window, "sessions": window_sessions}, "inputs_hash": inputs_hash,
        "policy": configuration.policy, "components": configuration.components,
        "configuration_sha256": configuration.configuration_sha256,
        "integrity": draft["integrity"], "evidence_problems": draft["evidence_problems"], "replay": draft["replay"],
        "window_stats": {**draft["stats"], "anchored_segment_share": anchored / len(verdicts) if verdicts else None,
                         "birth_episodes_verified_share": 1.0 if birth_episodes else None,
                         "advice_questions": counsel.questions, "advice_agreed": counsel.agreed},
        "marker_verdicts": verdicts, **sections,
        "slow_only_triggers": decision["slow_only"],
        "quantization": quantization, "side_time_accounting": side_time,
        "legitimacy": {habit_id: {key: value for key, value in verdict.items() if key != "publication"}
                       for habit_id, verdict in sorted(legitimacy_after.items())},
        "digest_id": None if digest_record is None else digest_record["id"],
        "snapshot_boundary_after": None if boundary is None else boundary["id"],
        "apply": {"habits": habits, "frozen": frozen, "declared": decision["declared"],
                  "slow_only": decision["slow_only"], "pool": pool, "quanta": quanta, "parts": parts,
                  "cursors": cursors, "digest": digest_record},
    }
    return {"report": records.make("consolidation_report", report=report), "boundary": boundary,
            "digest": digest_record}
