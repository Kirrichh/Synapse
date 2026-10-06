"""A verified comparison of learned competitors from recorded outcomes (review R5).

The conflict ladder's second step needs to know whether one competitor's
procedure does better than the other's. A model's answer to that question is a
proposal of what to compare, never the verdict: two agreeing answers over
outcomes nobody compared prove nothing. The verdict is this comparison's, and
it rests only on recorded outcomes that can be compared:

* an outcome counts only when the environment decided it — the episode's own
  result is a success or a failure *and* its segment verdict agrees (confirmed,
  or failed); an uncertain or unclear episode, an undecided segment and an
  episode whose evidence already existed elsewhere (a copy) are excluded, and
  one recorded episode counts once however often it is listed;
* outcomes are comparable only under the same situation — the same recorded
  context fields — so results of different conditions never make a pair; a
  situation in which one procedure has both succeeded and failed says nothing
  about it and is set aside;
* one procedure is better only when it did better in at least the declared
  number of comparable situations and never worse in any; equal results,
  contradicting situations or too few pairs keep the conflict unresolved —
  nothing is attributed to either procedure.

The histories remain observational data: they are not a counterfactual
experiment in independent copies of one initial state, and the comparison says
so (``basis``). Stand trials (``trials.py``) are such experiments: each decided
trial of the two competitors is one comparable situation, each situation once
(tried again with the same results it counts once; with other results the
trials contradict each other and decide nothing), judged by the same rule
under its own basis — and only for a trigger whose whole admitted region lies
inside the transfer scope its stand declares; a trigger that leaves open a
field the stand bounded is wider than the stand, and a trial proves nothing
about it. The court lifts a slow-only ban only on a winner found here.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..records import canonical

COMPARISON_BASIS = "recorded_outcomes_same_situation"


def decided(entry: Mapping[str, Any]) -> str | None:
    """The environment's result of one recorded episode, or ``None`` when it did not decide one."""
    if entry.get("copy"):
        return None
    outcome, verdict = entry.get("outcome"), entry.get("segment_verdict")
    if outcome == "success" and verdict == "confirmed":
        return "success"
    if outcome in {"success", "failure"} and verdict == "failed":
        return "failure"
    if outcome == "failure" and verdict in {"failed", None}:
        return "failure"
    return None


def _situations(history: Iterable[Mapping[str, Any]]) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Decided results by situation, each recorded episode once; and what was excluded, by reason."""
    seen: set[str] = set()
    found: dict[str, set[str]] = {}
    excluded = {"undecided": 0, "repeated": 0}
    for entry in history:
        ref = entry.get("ref")
        if ref is not None and ref in seen:
            excluded["repeated"] += 1
            continue
        if ref is not None:
            seen.add(ref)
        result = decided(entry)
        if result is None:
            excluded["undecided"] += 1
            continue
        found.setdefault(canonical(entry.get("fields") or {}).decode(), set()).add(result)
    return found, excluded


def compare(left: str, right: str, histories: Mapping[str, list], minimum: int) -> dict[str, Any]:
    """Whether ``left`` or ``right`` does better on comparable recorded situations, with every reason."""
    a, excluded_a = _situations(histories.get(left, []))
    b, excluded_b = _situations(histories.get(right, []))
    pairs, mixed = [], []
    for situation in sorted(set(a) & set(b)):
        if len(a[situation]) != 1 or len(b[situation]) != 1:
            mixed.append(situation)
            continue
        pairs.append({"situation": situation, left: next(iter(a[situation])), right: next(iter(b[situation]))})
    better = {left: 0, right: 0}
    for pair in pairs:
        if pair[left] != pair[right]:
            better[left if pair[left] == "success" else right] += 1
    winner, reason = None, "too_few_comparable_situations"
    if len(pairs) >= minimum:
        if better[left] and better[right]:
            reason = "contradicting_situations"
        elif better[left] >= minimum:
            winner, reason = left, "better_in_comparable_situations"
        elif better[right] >= minimum:
            winner, reason = right, "better_in_comparable_situations"
        else:
            reason = "no_difference_established"
    return {"basis": COMPARISON_BASIS, "winner": winner, "reason": reason, "pairs": len(pairs),
            "better": better, "mixed_situations": len(mixed),
            "excluded": {left: excluded_a, right: excluded_b}}


def _admitted(conditions) -> dict[str, set[bytes] | None]:
    """Per field a trigger names, the finite set of values its conditions admit (``None``: not finite)."""
    found: dict[str, set[bytes] | None] = {}
    for condition in conditions:
        name = condition["field"]
        values = ({canonical(condition["value"])} if condition["op"] == "==" else
                  {canonical(item) for item in condition["value"]} if condition["op"] == "in" else None)
        if values is None:
            found.setdefault(name, None)  # A bound, a negation or a type test narrows; it never makes finite.
        else:
            found[name] = values if found.get(name) is None else found[name] & values
    return found


def _covered(scope, condition_sets) -> bool:
    """Whether everything a trigger admits lies inside what the stand reproduced: every field the trigger
    names is one the stand reproduces, and on every field the stand reproduces the trigger admits only values
    the stand tried. A field the trigger leaves open admits every value — wider than any stand — so a trigger
    that does not bound a field of the stand is never covered by it."""
    reproduced = {name: {canonical(item) for item in values} for name, values in scope["fields"].items()}
    for conditions in condition_sets:
        admitted = _admitted(conditions)
        if set(admitted) - set(reproduced):
            return False
        for name, values in reproduced.items():
            allowed = admitted.get(name)
            if allowed is None or not allowed <= values:
                return False
    return True


def compare_trials(left: str, right: str, trials: Iterable[Mapping[str, Any]], minimum: int,
                   conditions: Iterable[Iterable[Mapping[str, Any]]]) -> dict[str, Any]:
    """Whether ``left`` or ``right`` did better in stand trials of the two, inside the trials' transfer scope."""
    relevant = [item for item in trials if set(item["arms"]) == {left, right}]
    conditions = [list(group) for group in conditions]
    inside = [item for item in relevant if _covered(item["scope"], conditions)]
    # One situation tried again with the same results counts once; with other results it contradicts itself.
    tried: dict[str, set[tuple[str, str]]] = {}
    for item in inside:
        if item["decided"]:
            tried.setdefault(f"{item['stand']}|{item['situation']}", set()).add(
                (item["arms"][left]["outcome"], item["arms"][right]["outcome"]))
    mixed = sorted(key for key, results in tried.items() if len(results) > 1)
    situations = {key: dict(zip((left, right), next(iter(results))))
                  for key, results in tried.items() if len(results) == 1}
    better = {left: 0, right: 0}
    for outcomes in situations.values():
        if outcomes[left] != outcomes[right]:
            better[left if outcomes[left] == "success" else right] += 1
    winner, reason = None, "too_few_trials"
    if relevant and not inside:
        reason = "outside_transfer_scope"
    elif mixed:
        reason = "contradicting_trials"  # A copy of one initial state that gave two results decides nothing.
    elif len(situations) >= minimum:
        if better[left] and better[right]:
            reason = "contradicting_trials"
        elif better[left] >= minimum:
            winner, reason = left, "better_in_stand_trials"
        elif better[right] >= minimum:
            winner, reason = right, "better_in_stand_trials"
        else:
            reason = "no_difference_established"
    return {"basis": "stand_trial", "winner": winner, "reason": reason, "pairs": len(situations),
            "better": better, "mixed_situations": len(mixed), "trials": sorted(item["id"] for item in relevant),
            "outside_scope": len(relevant) - len(inside),
            "stands": sorted({item["stand"] for item in inside})}
