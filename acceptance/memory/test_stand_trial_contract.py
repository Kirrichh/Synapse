"""Stand trials as a verified comparison of competitors, over plain data (review R5 §4–5).

* each decided trial of the two competitors inside its transfer scope is one
  comparable situation; the same situation tried again counts once, an
  undecided trial (arms from two states, an undecided arm) counts nothing;
* one procedure wins only when it did better in the declared number of
  situations and never worse; contradicting trials and equal results name no
  winner;
* a trial whose scope does not reproduce every value the competitors'
  trigger admits says nothing about it;
* an arm's outcome is the environment's: a body refused something its
  contracts forbid has failed whatever the anchor says; an undecided segment
  leaves the arm undecided;
* on the ladder a trial winner is a verified step-2 result of its own basis:
  it lifts the slow-only ban and the loser yields to it.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _court_data as data
from synapse.memory_consolidation.court.comparison import compare_trials
from synapse.memory_consolidation.court.trials import arm_outcome

SCOPE = {"fields": {"route_kind": ["intl"]}}
WHEN = [[{"field": "route_kind", "op": "==", "value": "intl"}]] * 2


def _trial(left, right, outcomes, situation, *, decided=True, scope=SCOPE, stand="stand-1", run=0):
    return {"id": f"trl_{situation}_{outcomes}_{run}", "stand": stand, "scope": scope, "situation": situation,
            "decided": decided, "arms": {left: {"outcome": outcomes[0]}, right: {"outcome": outcomes[1]}}}


@pytest.mark.parametrize("trials, winner, reason", [
    ([("success", "failure", "s1"), ("success", "failure", "s2")], "A", "better_in_stand_trials"),
    # One situation tried twice, two trial records: it counts once.
    ([("success", "failure", "s1"), ("success", "failure", "s1")], None, "too_few_trials"),
    ([("success", "failure", "s1"), ("failure", "success", "s2")], None, "contradicting_trials"),
    ([("success", "success", "s1"), ("failure", "failure", "s2")], None, "no_difference_established"),
    # Better in one situation of the two declared, never worse: not enough for either.
    ([("success", "failure", "s1"), ("success", "success", "s2")], None, "no_difference_established"),
    ([("failure", "failure", "s1"), ("failure", "success", "s2")], None, "no_difference_established"),
])
def test_decided_trials_compare_like_comparable_situations(trials, winner, reason):
    found = compare_trials("A", "B", [_trial("A", "B", (a, b), situation, run=run)
                                      for run, (a, b, situation) in enumerate(trials)], 2, WHEN)
    assert (found["winner"], found["reason"], found["basis"]) == (winner, reason, "stand_trial")


@pytest.mark.parametrize("fire, outcome", [
    ({"refusals": ["gw-7"], "status": "counted", "signal": 1.0, "why": None}, ("failure", "contract_violation")),
    ({"refusals": ["gw-7"], "status": "pending", "signal": None, "why": "segment_uncertain"},
     ("failure", "contract_violation")),
    ({"refusals": [], "status": "counted", "signal": 1.0, "why": None}, ("success", "anchored")),
    ({"refusals": [], "status": "counted", "signal": -1.0, "why": None}, ("failure", "anchored")),
    ({"refusals": [], "status": "pending", "signal": None, "why": "segment_uncertain"},
     ("undecided", "segment_uncertain")),
])
def test_the_environment_decides_an_arm(fire, outcome):
    assert arm_outcome(fire) == outcome


def test_an_undecided_trial_counts_nothing():
    trials = [_trial("A", "B", ("success", "failure"), "s1"),
              _trial("A", "B", ("success", "failure"), "s2", decided=False)]
    assert compare_trials("A", "B", trials, 2, WHEN)["pairs"] == 1


def test_a_scope_that_does_not_cover_the_trigger_proves_nothing():
    narrow = {"fields": {"route_kind": ["dom"]}}
    trials = [_trial("A", "B", ("success", "failure"), name, scope=narrow) for name in ("s1", "s2")]
    found = compare_trials("A", "B", trials, 2, WHEN)
    assert (found["winner"], found["reason"], found["outside_scope"]) == (None, "outside_transfer_scope", 2)
    wider = [[*WHEN[0], {"field": "tool", "op": "==", "value": "flights"}]] * 2  # A field the stand never names.
    trials = [_trial("A", "B", ("success", "failure"), name) for name in ("s1", "s2")]
    assert compare_trials("A", "B", trials, 2, wider)["reason"] == "outside_transfer_scope"


@pytest.mark.parametrize("label", ["q", "c"])
def test_the_trial_decides_for_either_competitor(label):
    config, state, ids = data.world(labels=("q", "c"), state_name="active")
    winner, loser = ids[label], ids["c" if label == "q" else "q"]
    state["slow_only"] = [data.CONDITION]
    left, right = sorted(ids.values())
    advice = {f"{left}|{right}": {
        "basis": "insufficient_comparable_outcomes", "asked": False, "answer": None,
        "comparison": {"basis": "recorded_outcomes_same_situation", "winner": None, "reason": "x", "pairs": 0},
        "trial": {"basis": "stand_trial", "winner": winner, "reason": "better_in_stand_trials", "pairs": 3,
                  "trials": ["trl_1"], "outside_scope": 0, "stands": ["stand-1"]}, "attributed": {}}}
    _, decision = data.window(config, state, advice=advice)
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == 2 and conflict["slow_only_lifted"] is True and decision["slow_only"] == []
    assert conflict["advice"]["trial"]["basis"] == "stand_trial"
    assert decision["habits"][loser]["yields_to"] == [winner]
