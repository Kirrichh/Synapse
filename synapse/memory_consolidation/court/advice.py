"""Advice the decision stages consume, asked during evaluation.

Stage 4, step 2 — a recorded counterfactual for learned competitors: would the
other habit's outcome have been better? It rests only on recorded outcomes
(fires of both, and slow-path episodes on the shared trigger whose steps are
either habit's action) and is not asked below the declared minimum, because an
unsafe comparison must stay uncertain.

Stage 5 — arbitration of candidates that meet every birth criterion but sit in
a gray band against an existing habit: an action similarity between the
declared thresholds, or distinct triggers whose templates the scorer finds
close. The same pure pool merge and criteria the decision applies select them.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..learning.behavior import step_similarity
from ..learning.triggers import context_template, matches, render_template
from .births import typed_check
from .conflicts import competitors
from .counsel import Counsel
from .pool import assess, merge_pool

_DECISIVE = {"duplicate", "absorbed", "archived_duplicate", "archived_absorbed"}


def _histories(state, fires) -> dict[str, list]:
    history: dict[str, list] = {habit_id: list(metadata.get("recent", []))
                                for habit_id, metadata in state["habits"].items()}
    for fire in fires:
        history.setdefault(fire["habit_id"], []).append({"outcome": fire["outcome"], "path": "habit",
                                                         "segment_verdict": fire["segment_verdict"],
                                                         "fields": fire["context"]["fields"]})
    return history


def _attribute_slow(history, pair, state, reactions, parameters) -> None:
    """Slow-path outcomes on the shared trigger count for the habit whose action they repeat."""
    left, right = pair
    a = state["frozen"][left]
    for reaction in reactions:
        slow = reaction["slow"]
        if slow is None or matches(a["trigger"], reaction["context"])[0] != "applicable":
            continue
        for habit_id in pair:
            pattern = state["frozen"][habit_id]["habit"]["action_pattern"]
            if step_similarity(slow["steps"], pattern) >= parameters["action_same"]:
                history.setdefault(habit_id, []).append({"outcome": slow["outcome"], "path": "slow",
                                                         "segment_verdict": reaction["segment_verdict"],
                                                         "fields": reaction["context"]["fields"]})


def conflict_advice(counsel: Counsel, state, parameters, fires, reactions) -> dict[str, Any]:
    """Counterfactual answers for every competitor pair, keyed ``"left|right"``."""
    history = _histories(state, fires)
    advice: dict[str, Any] = {}
    for left, right in competitors(state, parameters):
        _attribute_slow(history, (left, right), state, reactions, parameters)
        key = f"{left}|{right}"
        if min(len(history.get(left, [])), len(history.get(right, []))) < parameters["counterfactual_min_pairs"]:
            advice[key] = {"basis": "insufficient_recorded_outcomes", "asked": False, "answer": None}
            continue
        variant = {"A": {"habit_id": left, "pattern": state["frozen"][left]["habit"]["action_pattern"],
                         "outcomes": history[left]},
                   "B": {"habit_id": right, "pattern": state["frozen"][right]["habit"]["action_pattern"],
                         "outcomes": history[right]}}
        answer = counsel.ask("would_B_outcome_be_better", ("yes", "no"),
                             (variant, {"B": variant["B"], "A": variant["A"]}))
        advice[key] = {"basis": "recorded_outcomes", **answer}
    return advice


def _arbitrate(counsel, state, parameters, key, candidate, assessment, item) -> dict[str, Any] | None:
    similarity = None
    if item["relation"] == "distinct":
        if item["archived"]:
            return None
        event = {"fields": assessment["success"][0]["fields"]}
        template = context_template(assessment["condition"], [entry["context"] for entry in assessment["success"]])
        similarity = counsel.similarity(render_template(template, event), render_template(item["template"], event))
        if similarity is None or similarity < parameters["arbitration_similarity"]:
            return {"similarity": similarity, "answer": None, "asked": False}
    elif item["relation"] != "arbitration_action":
        return None
    frozen = state["frozen"][item["habit_id"]]
    question = {"candidate": {"trigger": assessment["condition"], "steps": candidate["steps"]},
                "existing": {"trigger": frozen["trigger"]["when"], "steps": frozen["habit"]["action_pattern"]}}
    answer = counsel.ask("variation_or_different", ("variation", "different", "uncertain"),
                         (question, {"existing": question["existing"], "candidate": question["candidate"]}))
    if answer["answer"] == "uncertain":
        answer = {**answer, "answer": None}
    return {"similarity": similarity, **answer}


def arbitration(counsel: Counsel, state, configuration, partial: Mapping[str, Any]) -> dict[str, Any]:
    """Arbitration answers keyed ``"candidate|habit"`` for candidates in the gray band."""
    parameters = configuration.parameters
    pool, touched = merge_pool(state, partial, parameters, state["window"] + 1)
    result: dict[str, Any] = {}
    for key in touched:
        assessment = assess(pool[key], parameters, configuration, partial["requests"])
        if assessment["reasons"]:
            continue
        relations = typed_check(pool[key]["steps"], assessment["condition"], state, parameters)
        if any(item["relation"] in _DECISIVE for item in relations):
            continue
        for item in relations:
            answer = _arbitrate(counsel, state, parameters, key, pool[key], assessment, item)
            if answer is not None:
                result[f"{key}|{item['habit_id']}"] = answer
    return result
