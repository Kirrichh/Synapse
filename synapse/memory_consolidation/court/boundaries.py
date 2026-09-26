"""Boundary decisions of live learned habits: narrowing and widening successors.

Verified failures of a habit (three or more) grouped in one typed subcontext,
while it succeeds elsewhere, narrow it: a successor with ``not_when`` of that
subcontext, whose failures become contrasts of that field. Near misses of one
failed condition recovered by the habit's own action (three or more) widen
it: a field condition admits the values they recovered at — it is never
removed, and it never extends over a value a recorded contrast failed at (a
refusal is reported); a failed context or forbidden subcontext is lifted. The
successor keeps the frozen action and binding, is born on probation with its
predecessor's trust, and supersedes it (TS); Gold records the supersession.
Self-assessment is never a basis.
"""
from __future__ import annotations

from typing import Any

from ..learning.applicability import BoundaryUnavailable, explanation_of, narrow_explanation, widen
from ..learning.behavior import step_similarity
from ..learning.triggers import condition_key, narrowed, widened
from ..records import canonical
from .births import make_birth
from .habit_state import EFFECTIVE
from .pool import support


def _narrowing(parameters, frozen, metadata):
    failures = [item for item in metadata["recent"] if item["outcome"] == "failure"
                and item["segment_verdict"] is not None]
    successes = [item for item in metadata["recent"] if item["outcome"] == "success"]
    if len(failures) < parameters["min_evidence"] or not successes:
        return None
    shared = set.intersection(*({(key, canonical(value)) for key, value in item["fields"].items()}
                                for item in failures))
    for name, value in sorted(shared):
        if any(canonical(item["fields"].get(name)) == value for item in successes):
            continue
        subcontext = {"field": name, "op": "==", "value": next(item["fields"][name] for item in failures)}
        try:
            condition = narrowed(frozen["trigger"], subcontext)
        except ValueError:
            continue
        return ("narrow", condition, f"{len(failures)} verified failures in {name}",
                narrow_explanation(frozen["trigger"], subcontext, failures))
    return None


def _widened(trigger, group):
    """The successor condition and explanation one group of recovered near misses supports."""
    failed = group[0]["failed_condition"]
    if failed.get("forbidden") or failed.get("field") == "context":
        return widened(trigger, failed), explanation_of(trigger)
    return widen(trigger, failed, [item["failed_condition"].get("actual") for item in group])


def _widening(parameters, frozen, habit_id, near_misses, refusals):
    groups: dict[bytes, list] = {}
    for reaction in near_misses:
        if (reaction["habit_id"] == habit_id and reaction["slow"] is not None and support(reaction) == "success"
                and step_similarity(reaction["slow"]["steps"], frozen["habit"]["action_pattern"])
                >= parameters["action_same"]):
            failed = reaction["failed_condition"]
            groups.setdefault(canonical({k: failed.get(k) for k in ("field", "op", "value", "forbidden")}),
                              []).append(reaction)
    for key in sorted(groups):
        group = groups[key]
        if len(group) < parameters["min_evidence"]:
            continue
        try:
            condition, explanation = _widened(frozen["trigger"], group)
        except (BoundaryUnavailable, ValueError) as exc:
            refusals.append({"habit_id": habit_id, "failed_condition": group[0]["failed_condition"],
                             "near_misses": len(group), "reason": str(exc)})
            continue
        return "widen", condition, f"{len(group)} near misses recovered by the same action", explanation
    return None


def _successor(context, habit_id, metadata, frozen, change) -> dict[str, Any]:
    kind, condition, basis, explanation = change
    birth = make_birth(context.parameters, context.draft["consolidation_id"], context.window, condition=condition,
                       applicability=explanation,
                       steps=frozen["habit"]["action_pattern"], binding=frozen["habit"]["binding"],
                       template=frozen["trigger"]["context_template"],
                       source_episodes=frozen["trigger"]["source_episodes"],
                       basis_qids=frozen["habit"]["born_from"]["episodes"], energy=metadata["energy_cost"],
                       trust=metadata["trust"], state_name="probation", supersedes=habit_id,
                       basis=[{"boundary": kind, "basis": basis}])
    birth["boundary"] = {"kind": kind, "basis": basis, "predecessor": habit_id}
    return birth


def boundary_stage(context, habits, births, forced, refused) -> None:
    """Successor births of live habits whose boundary the window evidence moves."""
    known = {canonical(condition_key(item["trigger"])) for item in context.state["frozen"].values()}
    for habit_id in sorted(habits):
        metadata = habits[habit_id]
        if metadata["state"] not in EFFECTIVE or metadata["superseded_by"] is not None or habit_id in forced:
            continue
        frozen = context.state["frozen"][habit_id]
        change = (_narrowing(context.parameters, frozen, metadata)
                  or _widening(context.parameters, frozen, habit_id, context.draft["near_misses"],
                               context.report["boundary_refusals"]))
        if change is None or canonical(change[1]) in known:
            continue
        birth = _successor(context, habit_id, metadata, frozen, change)
        if birth["habit"]["id"] in refused:
            context.report["refused_births"].append({"habit_id": birth["habit"]["id"], "predecessor": habit_id,
                                                     "reason": refused[birth["habit"]["id"]]})
            continue
        births.append(birth)
        forced[habit_id] = ("TS", f"superseded by a {change[0]}ed successor")
        context.report["supersessions"].append({"predecessor": habit_id, "successor": birth["habit"]["id"],
                                                "kind": change[0], "basis": change[2], "trigger": change[1]})
