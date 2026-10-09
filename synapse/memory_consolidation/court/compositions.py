"""Compositions in the court (refinement §14): verification, birth and review.

A recovery the slow planner composed is a hypothesis. The court re-derives
each such episode with the executor over the recorded answers (reactions)
and weighs it only when the record holds exactly the calls the composition
makes. Episodes of one composition — its base and its joins — accumulate as a
candidate; the goal is judged for the whole: the failed operation settled by
the journal and the segment not failed. A part that recovered its step while
the goal failed is a partial success, reported separately and never support.

A composition is born only as a whole, under the same criteria as any birth
(repeated, several tasks, all verified, all inside succeeded, independent
witnesses), from the episodes memory still holds (a forgotten one leaves the
candidate, as in the pool) and only while every part is live. The composite is
the base procedure with its joins; it supersedes the live procedure of that
base (the base itself or an earlier composite of it), and Gold admits it like
any learned behavior.

Every live composite is reviewed each window: a part that is no longer live
— archived, superseded or not admitted by Gold — anywhere in it (an
alternative, or a part nested inside another) sends the composite to
probation with the reason, as a truth-maintenance retraction would, and its
runtime will not call that part.
"""
from __future__ import annotations

from typing import Any

from ..learning.applicability import explanation_of
from ..learning.behavior import contracts_of
from ..learning.composition import COMPOSITION_V1, composition_key, parts_of
from ..learning.triggers import condition_key
from .births import energy_cost, make_birth
from .habit_state import EFFECTIVE
from .pool import distinct_episodes, independence_of, retained


def _support(reaction) -> str | None:
    """How one composed recovery bears on its composition, or ``None`` when it is no material."""
    composition, slow = reaction["composition"], reaction["slow"]
    if not composition["verified"] or slow is None or not slow["completed"]:
        return None
    if "environmental_failure" in reaction["segment_flags"]:
        return None
    if (composition["outcome"] == "success" and reaction["failed_settled"] is True
            and reaction["segment_verdict"] not in {"failed", "uncertain"}):
        return "success"
    if any(part["recovered"] for part in composition["parts"]):
        return "partial"
    return "failure"


def _episode(reaction, kind, window) -> dict[str, Any]:
    slow = reaction["slow"]
    return {"qid": reaction["qid"], "steps": reaction["steps_range"], "run_id": reaction["run_id"],
            "event_id": reaction["event_id"], "task": reaction["task_id"] or f"run:{reaction['run_id']}",
            "witnesses": slow["witnesses"], "evidence": slow["evidence"], "copy": slow["evidence_preexisting"],
            "support": kind, "window": window, "seconds": slow.get("seconds"), "tokens": slow.get("tokens", 0),
            "calls": slow["calls"], "verified": reaction["replay"] == "replay_verified" or reaction["anchored_evidence"],
            "parts": reaction["composition"]["parts"]}


def _live(state, legitimacy, habit_id) -> str | None:
    """Why a habit is not a live part now, or ``None``."""
    metadata = state["habits"].get(habit_id)
    if metadata is None or habit_id not in state["frozen"]:
        return "unknown"
    if metadata["superseded_by"] is not None:
        return f"superseded_by:{metadata['superseded_by']}"
    if metadata["state"] not in EFFECTIVE:
        return f"state:{metadata['state']}"
    if legitimacy.get(habit_id, {}).get("admitted") is False:
        return "not_admitted"
    return None


def review(context, habits, forced, legitimacy) -> None:
    """Live composites whose parts are no longer live go to probation, with the reason."""
    for habit_id, frozen in sorted(context.state["frozen"].items()):
        composition = frozen["habit"].get("composition")
        metadata = habits.get(habit_id)
        if composition is None or metadata is None or metadata["state"] not in EFFECTIVE \
                or metadata["superseded_by"] is not None:
            continue
        for item in parts_of(composition["joins"]):
            reason = _live(context.state, legitimacy, item["part"])
            if reason is None:
                continue
            context.report["composition_reviews"].append({"habit_id": habit_id, "part": item["part"],
                                                          "at": item["at"], "reason": reason})
            forced.setdefault(habit_id, ("TC", f"part {item['part']} is no longer live ({reason})"))


def _predecessor(state, base) -> str | None:
    """The live procedure of this base a new composite replaces: the base or an earlier composite of it."""
    for habit_id, frozen in sorted(state["frozen"].items()):
        metadata = state["habits"].get(habit_id) or {}
        composition = frozen["habit"].get("composition")
        if (habit_id == base or (composition is not None and composition["base"] == base)) \
                and metadata.get("state") in EFFECTIVE and metadata.get("superseded_by") is None:
            return habit_id
    return None


def _assess(entry, context, legitimacy) -> tuple[list[str], dict[str, Any], list, dict]:
    parameters, state = context.parameters, context.state
    distinct = distinct_episodes(entry["episodes"])
    success = [item for item in distinct if item["support"] == "success"]
    # Reported for every episode: a partial success is never support, so copies do not matter here.
    partial = [item for item in entry["episodes"] if item["support"] == "partial"]
    reasons = []
    if len(success) < parameters["birth_episodes"]:
        reasons.append("repeatability_below_threshold")
    if len({item["task"] for item in success}) < parameters["birth_tasks"]:
        reasons.append("single_task")
    if len(success) != len(distinct):
        reasons.append("not_all_episodes_succeeded")
    if any(not item["verified"] for item in success):
        reasons.append("episode_not_verified")
    independence = independence_of(context.configuration, success, parameters["birth_sources"])
    if independence["verdict"] != "independent":
        reasons.append("sources_dependent" if independence["verdict"] == "dependent"
                       else "independence_not_established")
    if entry["base"] not in state["frozen"]:
        reasons.append("base_unknown")
    parts = sorted({item["part"] for item in parts_of(entry["joins"])})
    reasons += [f"part_not_live:{part}:{why}" for part in parts
                for why in [_live(state, legitimacy, part)] if why is not None]
    criteria = {"episodes": len(success), "distinct_episodes": len(distinct),
                "copies": len(entry["episodes"]) - len(distinct), "tasks": len({item["task"] for item in success}),
                "contradictions": len(distinct) - len(success), "partial": len(partial),
                "all_success": len(success) == len(distinct) and bool(success),
                "all_verifiable": all(item["verified"] for item in success), "concrete": True,
                "independence": independence["verdict"], "request": None, "contrasts": 0, "dependencies": 0,
                "parts": parts}
    return reasons, criteria, success, independence


def _birth(context, entry, success, criteria, independence) -> dict[str, Any]:
    state, parameters = context.state, context.parameters
    base = state["frozen"][entry["base"]]
    predecessor = _predecessor(state, entry["base"])
    composition = {"schema_version": COMPOSITION_V1, "base": entry["base"], "joins": entry["joins"]}
    birth = make_birth(parameters, context.draft["consolidation_id"], context.window,
                       condition=condition_key(base["trigger"]), applicability=explanation_of(base["trigger"]),
                       steps=base["habit"]["action_pattern"], binding=base["habit"]["binding"],
                       template=base["trigger"]["context_template"],
                       source_episodes=[{"qid": item["qid"], "steps": item["steps"]} for item in success],
                       basis_qids=[item["qid"] for item in success], energy=energy_cost(parameters, success),
                       trust=parameters["resurrection_trust"], state_name="born", supersedes=predecessor,
                       basis=[{"qid": item["qid"], "steps": item["steps"], "run_id": item["run_id"],
                               "event_id": item["event_id"]} for item in success],
                       composition=composition,
                       contracts=contracts_of(base["habit"]["action_pattern"], context.configuration))
    birth.update(candidate_key=entry["candidate_key"], criteria=criteria, independence=independence,
                 evidence=sorted({ref for item in success for ref in item["evidence"]}),
                 typed_check="composition", arbitration=None)
    if predecessor is not None:
        birth["boundary"] = {"kind": "composition", "basis": f"{len(success)} verified composed recoveries",
                             "predecessor": predecessor}
    return birth


def composition_stage(context, habits, births, forced, refused, legitimacy) -> dict[str, Any]:
    """Review live composites, accumulate composed recoveries and birth verified compositions."""
    review(context, habits, forced, legitimacy)
    state, window, report = context.state, context.window, context.report
    updates: dict[str, Any] = {}
    for reaction in sorted(context.draft["reactions"], key=lambda item: (item["run_id"], item["event_id"])):
        composition = reaction["composition"]
        if composition is None:
            continue
        key = composition_key(composition["base"], composition["joins"])
        kind = _support(reaction)
        report["compositions"].append({"run_id": reaction["run_id"], "event_id": reaction["event_id"],
                                       "candidate_key": key, "base": composition["base"],
                                       "joins": composition["joins"], "verified": composition["verified"],
                                       "goal": composition["outcome"], "support": kind,
                                       "parts": composition["parts"]})
        if kind is None:
            continue
        entry = updates.get(key) or dict(state["pool"].get(key) or {
            "candidate_key": key, "kind": "composition", "base": composition["base"], "joins": composition["joins"],
            "episodes": [], "first_seen": window, "last_seen": window, "status": "accumulating", "reasons": [],
            "born_habit": None})
        if entry["status"] == "born" or any(item["run_id"] == reaction["run_id"]
                                            and item["event_id"] == reaction["event_id"] for item in entry["episodes"]):
            continue
        entry = {**entry, "episodes": [*entry["episodes"], _episode(reaction, kind, window)], "last_seen": window}
        updates[key] = entry
    for key, entry in sorted(updates.items()):
        entry = {**entry, "episodes": retained(state, entry["episodes"])}
        reasons, criteria, success, independence = _assess(entry, context, legitimacy)
        birth = None if reasons else _birth(context, entry, success, criteria, independence)
        if birth is not None and birth["habit"]["id"] in refused:
            reasons = [f"gate_refused:{refused[birth['habit']['id']]}"]
        report["pool_updates"].append({"candidate_key": key, "status": "born" if not reasons else "accumulating",
                                       "episodes_total": len(entry["episodes"]), "reasons": reasons,
                                       "criteria": criteria, "independence": independence, "unavailable": {}})
        if reasons:
            updates[key] = {**entry, "reasons": reasons}
            continue
        births.append(birth)
        updates[key] = {**entry, "status": "born", "born_habit": birth["habit"]["id"], "reasons": []}
        predecessor = (birth.get("boundary") or {}).get("predecessor")
        if predecessor is not None:
            forced[predecessor] = ("TS", "superseded by a verified composition")
            report["supersessions"].append({"predecessor": predecessor, "successor": birth["habit"]["id"],
                                            "kind": "composition", "basis": birth["boundary"]["basis"],
                                            "trigger": condition_key(birth["trigger"])})
    return updates
