"""Stage 4 of the court: the conflict ladder among learned competitors.

Competitors share one applicability and one expected outcome and differ in
action. Step 1: an established trust gap selects the senior habit; the fact is
reported. Step 2: agreed recorded counterfactual advice keeps the better one
and puts the other on probation (TC). Step 3: without sufficient basis, both go
to probation and the trigger becomes slow-only; only a later verified step-2
verdict lifts it. Equal trust never yields an arbitrary winner: identity order
breaks the tie.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..learning.behavior import step_similarity
from ..learning.triggers import condition_key
from ..records import canonical
from .habit_state import EFFECTIVE

_ADVICE_FIELDS = ("basis", "asked", "calls", "answer", "agreed", "reasons", "refs", "component")


def competitors(state, parameters) -> list[tuple[str, str]]:
    """Pairs of live learned habits with one applicability, one expected outcome, other actions."""
    live = sorted(habit_id for habit_id, metadata in state["habits"].items() if metadata["state"] in EFFECTIVE)
    pairs = []
    for index, left in enumerate(live):
        for right in live[index + 1:]:
            a, b = state["frozen"][left], state["frozen"][right]
            if (canonical(condition_key(a["trigger"])) == canonical(condition_key(b["trigger"]))
                    and a["habit"]["expected_outcome"] == b["habit"]["expected_outcome"]
                    and step_similarity(a["habit"]["action_pattern"], b["habit"]["action_pattern"])
                    < parameters["action_same"]):
                pairs.append((left, right))
    return pairs


def _advice_view(advice: Mapping[str, Any]) -> dict[str, Any]:
    return {key: advice.get(key) for key in _ADVICE_FIELDS}


def _entry(habits, left, right, condition) -> dict[str, Any]:
    a, b = habits[left], habits[right]
    senior, junior = (left, right) if (-a["trust"], left) <= (-b["trust"], right) else (right, left)
    return {"trigger": condition, "habits": {"A": senior, "B": junior},
            "trust": {"A": habits[senior]["trust"], "B": habits[junior]["trust"]},
            "gap": abs(a["trust"] - b["trust"])}


def _step_two(entry, pair, advice, blocked, slow_only, forced) -> dict[str, Any]:
    """The question asks whether B, the second in identity order, would have been better."""
    left, right = pair
    better = right if advice["answer"] == "yes" else left
    loser = left if better == right else right
    forced.setdefault(loser, ("TC", f"lost counterfactual step 2 on {entry['trigger']['event_types']}"))
    if blocked:
        slow_only[:] = [item for item in slow_only if item != entry["trigger"]]
    return {**entry, "step": 2, "advice": _advice_view(advice), "resolution": f"{better}_stays_{loser}_probation",
            "slow_only_lifted": blocked}


def conflict_stage(parameters, state, habits, draft, report, forced) -> list[dict[str, Any]]:
    """Resolve every competitor pair; returns the slow-only triggers after this window."""
    slow_only = [dict(item) for item in state["slow_only"]]
    for left, right in competitors({"habits": habits, "frozen": state["frozen"]}, parameters):
        entry = _entry(habits, left, right, condition_key(state["frozen"][left]["trigger"]))
        blocked = entry["trigger"] in slow_only
        if entry["gap"] >= parameters["conflict_gap"] and not blocked:
            report["conflicts"].append({**entry, "step": 1, "resolution": "A_selected_by_trust_gap"})
            continue
        advice = draft["conflict_advice"].get(f"{left}|{right}", {"basis": "not_asked", "answer": None})
        if advice.get("answer") is not None:
            report["conflicts"].append(_step_two(entry, (left, right), advice, blocked, slow_only, forced))
            continue
        for habit_id in (left, right):
            forced.setdefault(habit_id, ("TC", "unresolved conflict, step 3"))
        if not blocked:
            slow_only.append(entry["trigger"])
        report["conflicts"].append({**entry, "step": 3, "advice": _advice_view(advice),
                                    "resolution": "both_probation_trigger_slow_only"})
    return sorted(slow_only, key=canonical)
