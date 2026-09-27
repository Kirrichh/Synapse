"""Stage 4 of the court: the conflict ladder among learned competitors.

Rivals share one expected outcome and differ in action (``behavior.rivals``).
Competitors are rivals that apply together: they share one applicability, or
the runtime found both applicable to one event and held both back
(``met_at_runtime``: triggers whose conditions overlap only partly).

Step 1: an established trust gap selects the senior habit; the fact is
reported. Step 2: the verified comparison of their recorded outcomes
(``comparison.py``) finds one better in comparable situations: it stays and the
other yields to it (``yields_to``, which the snapshot boundary carries to the
runtime); on one shared applicability the other also goes on probation (TC). A
model's answer is a recorded proposal and never resolves a conflict (review
R5). Slow-path outcomes attributed to a competitor are kept (``compared``), so
comparable evidence accumulates across windows. A verified result stands in later windows while the recorded outcomes do
not contradict it. Step 3: without a sufficient verified result, both go to
probation and a shared trigger becomes slow-only; only a later verified step-2
result lifts it. Partly overlapping rivals keep their own triggers: the runtime
holds both back on the overlap only. Equal trust never yields an arbitrary
winner: identity order breaks the tie.

A habit born in this window meets its competitors at once, before any session
could fire either: an established trust gap selects the senior habit, anything
else makes the shared trigger slow-only — an unresolved conflict is blocked
before it can act, never announced as a warning after the fact.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..learning.behavior import rivals
from ..learning.triggers import condition_key
from ..records import canonical
from .habit_state import EFFECTIVE

_ADVICE_FIELDS = ("basis", "asked", "calls", "answer", "agreed", "reasons", "refs", "component", "comparison")


def met_at_runtime(suppressed: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """Pairs the runtime found applicable to one event and held back as an unresolved conflict."""
    return {tuple(sorted((item["habit_id"], item["competitor"]))) for item in suppressed
            if item.get("reason") == "unresolved_conflict" and item.get("competitor")}


def _shared(a_frozen, b_frozen) -> bool:
    return canonical(condition_key(a_frozen["trigger"])) == canonical(condition_key(b_frozen["trigger"]))


def competitors(state, parameters, met: set[tuple[str, str]] = frozenset()) -> list[tuple[str, str]]:
    """Pairs of live learned rivals that share one applicability or met on one event."""
    live = sorted(habit_id for habit_id, metadata in state["habits"].items() if metadata["state"] in EFFECTIVE)
    pairs = []
    for index, left in enumerate(live):
        for right in live[index + 1:]:
            a, b = state["frozen"][left], state["frozen"][right]
            if rivals(a["habit"], b["habit"], parameters["action_same"]) and (_shared(a, b) or (left, right) in met):
                pairs.append((left, right))
    return pairs


def _advice_view(advice: Mapping[str, Any]) -> dict[str, Any]:
    return {key: advice.get(key) for key in _ADVICE_FIELDS}


def _entry(habits, left, right, frozen) -> dict[str, Any]:
    a, b = habits[left], habits[right]
    senior, junior = (left, right) if (-a["trust"], left) <= (-b["trust"], right) else (right, left)
    entry = {"habits": {"A": senior, "B": junior},
             "trust": {"A": habits[senior]["trust"], "B": habits[junior]["trust"]},
             "gap": abs(a["trust"] - b["trust"])}
    if _shared(frozen[left], frozen[right]):
        return {"trigger": condition_key(frozen[left]["trigger"]), **entry}
    return {"trigger": None, "overlap": {"A": condition_key(frozen[senior]["trigger"]),
                                         "B": condition_key(frozen[junior]["trigger"])}, **entry}


def _yield(habits, winner, loser) -> None:
    habits[loser]["yields_to"] = sorted(set(habits[loser].get("yields_to", [])) | {winner})
    habits[winner]["yields_to"] = [item for item in habits[winner].get("yields_to", []) if item != loser]


def _unyield(habits, pair) -> None:
    left, right = pair
    for one, other in ((left, right), (right, left)):
        habits[one]["yields_to"] = [item for item in habits[one].get("yields_to", []) if item != other]


def _step_two(entry, habits, winner, advice, blocked, slow_only, forced, *, standing=False) -> dict[str, Any]:
    """The verified comparison found ``winner`` better: it stays, the other yields to it."""
    loser = entry["habits"]["B"] if winner == entry["habits"]["A"] else entry["habits"]["A"]
    _yield(habits, winner, loser)
    if entry["trigger"] is not None:
        forced.setdefault(loser, ("TC", f"lost the verified comparison on {entry['trigger']['event_types']}"))
    if blocked:
        slow_only[:] = [item for item in slow_only if item != entry["trigger"]]
    resolution = f"{winner}_stays_{loser}_probation" if entry["trigger"] is not None else f"{loser}_yields_to_{winner}"
    return {**entry, "step": 2, "advice": _advice_view(advice), "resolution": resolution,
            "slow_only_lifted": blocked, **({"standing": True} if standing else {})}


def _standing(habits, pair, comparison) -> str | None:
    """The winner of an earlier verified comparison, while the recorded outcomes do not contradict it."""
    left, right = pair
    earlier = (right if right in habits[left].get("yields_to", []) else
               left if left in habits[right].get("yields_to", []) else None)
    if earlier is None or comparison.get("reason") == "contradicting_situations" \
            or comparison.get("winner") not in (None, earlier):
        return None
    return earlier


def _keep_compared(parameters, metadata, attributed) -> None:
    """Slow-path outcomes attributed to a competitor stay comparable in later windows, each episode once."""
    known = {item["ref"] for item in metadata.get("compared", [])}
    added = [item for item in attributed if item["ref"] is None or item["ref"] not in known]
    metadata["compared"] = [*metadata.get("compared", []), *added][-parameters["recent_fires"]:]


def conflict_stage(parameters, state, habits, draft, report, forced) -> list[dict[str, Any]]:
    """Resolve every competitor pair; returns the slow-only triggers after this window."""
    slow_only = [dict(item) for item in state["slow_only"]]
    met = met_at_runtime(draft["suppressed"])
    for left, right in competitors({"habits": habits, "frozen": state["frozen"]}, parameters, met):
        entry = _entry(habits, left, right, state["frozen"])
        advice = draft["conflict_advice"].get(f"{left}|{right}", {"basis": "not_asked", "answer": None})
        for habit_id in (left, right):
            _keep_compared(parameters, habits[habit_id], (advice.get("attributed") or {}).get(habit_id, []))
        blocked = entry["trigger"] is not None and entry["trigger"] in slow_only
        if entry["gap"] >= parameters["conflict_gap"] and not blocked:
            report["conflicts"].append({**entry, "step": 1, "resolution": "A_selected_by_trust_gap"})
            continue
        comparison = advice.get("comparison") or {}
        if comparison.get("winner") is not None:
            report["conflicts"].append(_step_two(entry, habits, comparison["winner"], advice, blocked, slow_only,
                                                 forced))
            continue
        standing = _standing(habits, (left, right), comparison)
        if standing is not None:
            report["conflicts"].append(_step_two(entry, habits, standing, advice, blocked, slow_only, forced,
                                                 standing=True))
            continue
        _unyield(habits, (left, right))
        _unresolved(report, forced, slow_only, entry, advice, (left, right), blocked)
    return sorted(slow_only, key=canonical)


def _unresolved(report, forced, slow_only, entry, advice, pair, blocked, *, at_birth=False) -> None:
    if entry["trigger"] is None:
        resolution = "held_back_on_the_overlap_until_compared"
    else:
        for habit_id in pair:
            forced.setdefault(habit_id, ("TC", "unresolved conflict, step 3"))
        if not blocked:
            slow_only.append(entry["trigger"])
        resolution = "trigger_slow_only_until_compared" if at_birth else "both_probation_trigger_slow_only"
    report["conflicts"].append({**entry, "step": 3, "advice": _advice_view(advice), "resolution": resolution,
                                **({"at_birth": True} if at_birth else {})})


def birth_conflicts(parameters, state, habits, births, slow_only, report, forced) -> list[dict[str, Any]]:
    """Judge every pair a birth of this window forms on one applicability with a live habit or another birth.

    Returns the slow-only triggers after the judgement. A birth that supersedes a habit does not compete
    with it. Rivals whose triggers overlap only partly are held back by the runtime on the overlap."""
    slow_only = sorted([dict(item) for item in slow_only], key=canonical)
    live = {habit_id: {"frozen": state["frozen"][habit_id], "trust": metadata["trust"]}
            for habit_id, metadata in habits.items() if metadata["state"] in EFFECTIVE and habit_id in state["frozen"]}
    born = {birth["habit"]["id"]: {"frozen": {"habit": birth["habit"], "trigger": birth["trigger"]},
                                   "trust": birth["metadata"]["trust"], "supersedes": birth["metadata"]["supersedes"]}
            for birth in births}
    seen = set()
    for new_id in sorted(born):
        for other_id in sorted({**live, **born}):
            pair = tuple(sorted((new_id, other_id)))
            if other_id == new_id or pair in seen or born[new_id]["supersedes"] == other_id \
                    or (other_id in born and born[other_id]["supersedes"] == new_id):
                continue
            seen.add(pair)
            other = live.get(other_id) or born[other_id]
            if not (_shared(born[new_id]["frozen"], other["frozen"])
                    and rivals(born[new_id]["frozen"]["habit"], other["frozen"]["habit"], parameters["action_same"])):
                continue
            trust = {new_id: born[new_id]["trust"], other_id: other["trust"]}
            senior, junior = sorted(pair, key=lambda item: (-trust[item], item))
            entry = {"trigger": condition_key(born[new_id]["frozen"]["trigger"]),
                     "habits": {"A": senior, "B": junior}, "trust": {"A": trust[senior], "B": trust[junior]},
                     "gap": abs(trust[senior] - trust[junior])}
            blocked = entry["trigger"] in slow_only
            if entry["gap"] >= parameters["conflict_gap"] and not blocked:
                report["conflicts"].append({**entry, "step": 1, "resolution": "A_selected_by_trust_gap",
                                            "at_birth": True})
                continue
            # A birth has no recorded outcomes yet: nothing can be compared, so the trigger waits on the slow path.
            _unresolved(report, forced, slow_only, entry, {"basis": "born_this_window", "answer": None},
                        (other_id,) if other_id in live else (), blocked, at_birth=True)
    return sorted(slow_only, key=canonical)
