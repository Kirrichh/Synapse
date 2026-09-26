"""Births from the candidate pool (stage 5): typed check, arbitration, records.

A candidate that meets every criterion is checked against every known habit,
live and archived: a duplicate or an absorbing trigger with the same action is
a vote for the existing habit and no birth (an archived duplicate is woken by
its cold anchor instead); a competitor is born and meets the conflict ladder;
an action in the gray band or a close template needs the recorded arbitration
(variation is a vote, different is a birth, anything else keeps waiting). A
birth creates the trigger and the frozen habit together; its behavior still
has to pass Gold's gates, and a refused birth leaves its candidate waiting
with the refusal as its reason.
"""
from __future__ import annotations

from typing import Any

from .. import records
from ..learning.behavior import SAME, check_binding, step_similarity
from ..learning.triggers import condition_key, context_template, covers, make_trigger
from ..quanta import weight
from ..records import canonical
from .habit_state import EFFECTIVE, mean, new_metadata
from .pool import assess, merge_pool

_DECISIVE = {"duplicate", "absorbed", "archived_duplicate", "archived_absorbed"}
_ARBITRATION_FIELDS = ("similarity", "calls", "answer", "agreed", "refs")


def _relation(same, covered, similarity, archived, parameters) -> str:
    if same and similarity >= parameters["action_same"]:
        return "archived_duplicate" if archived else "duplicate"
    if covered and similarity >= parameters["action_same"]:
        return "archived_absorbed" if archived else "absorbed"
    if same and similarity < parameters["action_different"]:
        return "competitor"
    return "arbitration_action" if same else "distinct"


def typed_check(candidate_steps, condition, state, parameters) -> list[dict[str, Any]]:
    """Relations of a candidate to every known habit (live and archived), in identity order."""
    relations = []
    for habit_id in sorted(state["frozen"]):
        frozen = state["frozen"][habit_id]
        archived = state["habits"].get(habit_id, {"state": "extinct"})["state"] not in EFFECTIVE
        existing = condition_key(frozen["trigger"])
        similarity = step_similarity(candidate_steps, frozen["habit"]["action_pattern"])
        relation = _relation(canonical(existing) == canonical(condition), covers(existing, condition), similarity,
                             archived, parameters)
        relations.append({"habit_id": habit_id, "relation": relation, "action_similarity": similarity,
                          "archived": archived, "template": frozen["trigger"]["context_template"]})
    return relations


def _needs_arbitration(item, answer, parameters) -> bool:
    if item["relation"] == "arbitration_action":
        return True
    if item["relation"] != "distinct" or item["archived"]:
        return False
    similarity = None if answer is None else answer.get("similarity")
    return similarity is not None and similarity >= parameters["arbitration_similarity"]


def typed_relation(key, candidate, assessment, state, parameters, arbitration, report):
    """The deciding relation of a candidate that met every criterion, and why it waits (if it does)."""
    relations = typed_check(candidate["steps"], assessment["condition"], state, parameters)
    decisive = next((item for item in relations if item["relation"] in _DECISIVE), None)
    if decisive is not None:
        if decisive["relation"] in {"duplicate", "absorbed"}:
            report["votes"].append({"candidate_key": key, "habit_id": decisive["habit_id"],
                                    "relation": decisive["relation"]})
        return decisive, [f"typed_{decisive['relation']}"]
    relation = None
    for item in relations:
        answer = arbitration.get(f"{key}|{item['habit_id']}")
        if not _needs_arbitration(item, answer, parameters):
            continue
        verdict = None if answer is None else answer.get("answer")
        relation = {**item, "arbitration": None if answer is None else {k: answer.get(k) for k in _ARBITRATION_FIELDS}}
        if verdict == "variation":
            report["votes"].append({"candidate_key": key, "habit_id": item["habit_id"], "relation": "variation"})
            return relation, ["arbitrated_variation"]
        if verdict != "different":
            return relation, ["arbitration_unresolved"]
    return relation, []


def expected_outcome(steps) -> str:
    return "failed_operation_recovered" if any(item.get("tool") == SAME for item in steps) else "reaction_completed"


def energy_cost(parameters, episodes) -> float:
    """``R`` of the basis executions times ``energy_per_R``; unmeasured time uses the declared default."""
    values = []
    for item in episodes:
        if item.get("seconds") is not None:
            values.append(weight(parameters, steps=len(item["calls"]), external_calls=len(item["calls"]), branches=0,
                                 retries=0, tokens=item.get("tokens", 0), seconds=item["seconds"], gas=0)["R"])
    return mean(values) * parameters["energy_per_R"] if values else float(parameters["unmeasured_energy_cost"])


def make_birth(parameters, consolidation_id, window, *, condition, applicability, steps, binding, template,
               source_episodes, basis_qids, energy, trust, state_name, supersedes=None, basis=None) -> dict[str, Any]:
    """The trigger, frozen habit and initial metadata of one birth."""
    check_binding(steps, binding)
    trigger = make_trigger(condition, template=template, born_from=consolidation_id, source_episodes=source_episodes,
                           applicability=applicability)
    habit = records.make("learned_habit", origin="learned", layer=2, trigger=trigger["id"],
                         action_pattern=[dict(item) for item in steps], binding=binding,
                         expected_outcome=expected_outcome(steps),
                         born_from={"consolidation": consolidation_id, "episodes": sorted(set(basis_qids))},
                         supersedes=supersedes)
    metadata = new_metadata(parameters, habit_id=habit["id"], trigger_id=trigger["id"], state=state_name,
                            trust=trust, window=window, consolidation_id=consolidation_id, energy_cost=energy,
                            supersedes=supersedes)
    return {"habit": habit, "trigger": trigger, "metadata": metadata, "basis": basis or []}


def _candidate_birth(parameters, consolidation_id, window, key, entry, assessment, relation) -> dict[str, Any]:
    success = assessment["success"]
    birth = make_birth(parameters, consolidation_id, window, condition=assessment["condition"],
                       applicability=assessment["applicability"], steps=entry["steps"], binding=assessment["binding"],
                       template=context_template(assessment["condition"], [item["context"] for item in success]),
                       source_episodes=[{"qid": item["qid"], "steps": item["steps"]} for item in success],
                       basis_qids=[item["qid"] for item in success], energy=energy_cost(parameters, success),
                       trust=parameters["resurrection_trust"], state_name="born",
                       basis=[{"qid": item["qid"], "steps": item["steps"], "run_id": item["run_id"],
                               "event_id": item["event_id"]}
                              for item in success])
    birth.update(candidate_key=key, criteria=assessment["criteria"], independence=assessment["independence"],
                 evidence=sorted({ref for item in success for ref in item["evidence"]}),
                 typed_check="distinct" if relation is None else relation["relation"],
                 arbitration=None if relation is None else relation.get("arbitration"))
    return birth


def _waiting(entry, reasons, assessment, report, key) -> dict[str, Any]:
    entry["reasons"] = reasons
    entry["status"] = "arbitration_pending" if "arbitration_unresolved" in reasons else "accumulating"
    unavailable = {name: value["unavailable"] for name, value in (("binding", assessment["binding"]),
                                                                  ("boundary", assessment["applicability"]))
                   if isinstance(value, dict) and "unavailable" in value}
    report["pool_updates"].append({"candidate_key": key, "status": entry["status"],
                                   "episodes_total": len(entry["episodes"]), "reasons": reasons,
                                   "criteria": assessment["criteria"], "independence": assessment["independence"],
                                   "unavailable": unavailable})
    return entry


def _expired(pool, touched, window, parameters, report) -> dict[str, Any]:
    updates = {}
    for key, entry in sorted(pool.items()):
        if key not in touched and entry["status"] != "born" and \
                window - entry["last_seen"] >= parameters["candidate_max_windows"]:
            updates[key] = None
            report["pool_updates"].append({"candidate_key": key, "status": "expired",
                                           "episodes_total": len(entry["episodes"])})
    return updates


def pool_stage(context, births, consumed, refused) -> dict[str, Any]:
    """Pool updates of this window; births are appended to ``births``."""
    state, draft, parameters, report = context.state, context.draft, context.parameters, context.report
    pool, touched = merge_pool(state, draft, parameters, context.window)
    updates = _expired(pool, touched, context.window, parameters, report)
    born_conditions = {canonical(condition_key(item["trigger"])) for item in births}
    for key in touched:
        entry = pool[key]
        entry["episodes"] = [item for item in entry["episodes"] if item["event_id"] not in consumed]
        assessment = assess(entry, parameters, context.configuration, draft["requests"])
        reasons, relation = list(assessment["reasons"]), None
        if not reasons:
            relation, waiting = typed_relation(key, entry, assessment, state, parameters, draft["arbitration"], report)
            reasons += waiting + (["successor_owns_trigger"]
                                  if canonical(assessment["condition"]) in born_conditions else [])
        birth = None if reasons else _candidate_birth(parameters, draft["consolidation_id"], context.window, key,
                                                      entry, assessment, relation)
        if birth is not None and birth["habit"]["id"] in refused:
            reasons = [f"gate_refused:{refused[birth['habit']['id']]}"]
            report["refused_births"].append({"habit_id": birth["habit"]["id"], "candidate_key": key,
                                             "reason": refused[birth["habit"]["id"]]})
        if reasons:
            updates[key] = _waiting(entry, reasons, assessment, report, key)
            continue
        births.append(birth)
        born_conditions.add(canonical(assessment["condition"]))
        updates[key] = {**entry, "status": "born", "born_habit": birth["habit"]["id"], "reasons": []}
    return updates
