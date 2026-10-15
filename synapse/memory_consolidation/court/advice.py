"""Advice the decision stages consume, asked during evaluation.

Stage 4, step 2 — learned competitors: the verified comparison of their
recorded outcomes (``comparison.py``: decided outcomes of fires of both and of
slow-path episodes where both apply whose steps are either habit's action,
paired only in the same situation) decides; the model is asked only once there
are enough comparable situations, and its answer is recorded as a proposal of
what the comparison should look at — it has no authority to resolve anything.

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
from .comparison import compare, compare_trials
from .conflicts import competitors, met_at_runtime
from .counsel import Counsel
from .pool import assess, merge_pool

_DECISIVE = {"duplicate", "absorbed", "archived_duplicate", "archived_absorbed"}


def _ref(run_id, event_id) -> str | None:
    return None if run_id is None or event_id is None else f"{run_id}|{event_id}"


def _histories(state, fires) -> dict[str, list]:
    history: dict[str, list] = {habit_id: [{**item, "ref": _ref(item.get("run_id"), item.get("event_id"))}
                                           for item in metadata.get("recent", [])]
                                for habit_id, metadata in state["habits"].items()}
    for fire in fires:
        history.setdefault(fire["habit_id"], []).append({
            "outcome": fire["outcome"], "path": "habit", "segment_verdict": fire["segment_verdict"],
            "fields": fire["context"]["fields"], "ref": _ref(fire.get("run_id"), fire.get("event_id")),
            "copy": fire.get("status") == "excluded"})
    return history


def _attributed(pair, state, reactions, parameters) -> dict[str, list]:
    """This window's slow-path outcomes where both apply, for the habit whose action they repeat."""
    found: dict[str, list] = {habit_id: [] for habit_id in pair}
    for reaction in reactions:
        slow = reaction["slow"]
        if slow is None or any(matches(state["frozen"][habit_id]["trigger"], reaction["context"])[0] != "applicable"
                               for habit_id in pair):
            continue
        for habit_id in pair:
            pattern = state["frozen"][habit_id]["habit"]["action_pattern"]
            if step_similarity(slow["steps"], pattern) >= parameters["action_same"]:
                found[habit_id].append({
                    "outcome": slow["outcome"], "path": "slow", "segment_verdict": reaction["segment_verdict"],
                    "fields": reaction["context"]["fields"], "ref": _ref(reaction["run_id"], reaction["event_id"]),
                    "copy": bool(slow.get("evidence_preexisting"))})
    return found


def conflict_advice(counsel: Counsel | None, state, parameters, fires, reactions, suppressed,
                    trials=()) -> dict[str, Any]:
    """The verified comparison of every competitor pair, keyed ``"left|right"``, with the model's proposal.

    The compared outcomes are the habits' recent fires, this window's fires, the slow-path outcomes
    attributed to them in earlier windows (``compared``) and this window's (``attributed``, which the
    decision keeps). Without ``counsel`` (a reassessment calls nothing) no proposal is asked."""
    fired = _histories(state, fires)
    advice: dict[str, Any] = {}
    for left, right in competitors(state, parameters, met_at_runtime(suppressed)):
        attributed = _attributed((left, right), state, reactions, parameters)
        history = {habit_id: [*fired.get(habit_id, []), *state["habits"][habit_id].get("compared", []),
                              *attributed[habit_id]] for habit_id in (left, right)}
        key = f"{left}|{right}"
        result = compare(left, right, history, parameters["counterfactual_min_pairs"])
        trial = compare_trials(left, right, trials, parameters["counterfactual_min_pairs"],
                               [state["frozen"][habit_id]["trigger"]["when"] for habit_id in (left, right)])
        if result["pairs"] < parameters["counterfactual_min_pairs"] or counsel is None:
            basis = "insufficient_comparable_outcomes" if counsel is not None else "not_asked_in_reassessment"
            advice[key] = {"basis": basis, "asked": False, "answer": None, "comparison": result, "trial": trial,
                           "attributed": attributed}
            continue
        variant = {"A": {"habit_id": left, "pattern": state["frozen"][left]["habit"]["action_pattern"],
                         "outcomes": history[left]},
                   "B": {"habit_id": right, "pattern": state["frozen"][right]["habit"]["action_pattern"],
                         "outcomes": history[right]}}
        answer = counsel.ask("would_B_outcome_be_better", ("yes", "no"),
                             (variant, {"B": variant["B"], "A": variant["A"]}))
        advice[key] = {"basis": "proposal", **answer, "comparison": result, "trial": trial, "attributed": attributed}
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
