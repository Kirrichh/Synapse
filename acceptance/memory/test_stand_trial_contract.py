"""Stand trials as a verified comparison of competitors, over plain data (review R5 §4–5).

* each decided trial of the two competitors inside its transfer scope is one
  comparable situation; the same situation tried again with the same results
  counts once, with other results the trials contradict each other and name no
  winner, whichever came first; an undecided trial (arms from two states, an
  undecided arm) counts nothing;
* one procedure wins only when it did better in the declared number of
  situations and never worse; contradicting trials and equal results name no
  winner;
* a trial says something about a trigger only when everything the trigger
  admits lies inside the scope: every field the trigger names is reproduced,
  and every field the stand bounded the trigger bounds to values tried — a
  trigger open on the weather is wider than a stand tried in clear weather;
* an arm's outcome is the environment's: a body refused something its
  contracts forbid has failed whatever the anchor says; an undecided segment
  leaves the arm undecided;
* on the ladder a trial winner is a verified step-2 result of its own basis:
  it lifts the slow-only ban and the loser yields to it;
* a resolution found in trials stands only while the trials recorded so far
  name the same winner: a later trial that contradicts them takes it away in an
  ordinary window — the loser no longer yields, both go on probation, the
  trigger is slow-only again — whatever the order of the records; the same
  result tried again keeps it; a resolution found in compared outcomes rests on
  them, not on the trials, and a resolution whose basis is not known does not
  stand (review N1).
"""
from __future__ import annotations

import pytest

from acceptance.memory import _court_data as data
from synapse.memory_consolidation.court.advice import conflict_advice
from synapse.memory_consolidation.court.comparison import COMPARISON_BASIS, TRIAL_BASIS, compare_trials
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
    # The same situation tried again with other results contradicts itself, in either order of the records.
    ([("success", "failure", "s1"), ("success", "failure", "s2"), ("failure", "success", "s1")], None,
     "contradicting_trials"),
    ([("success", "failure", "s1"), ("success", "failure", "s2"), ("success", "success", "s1")], None,
     "contradicting_trials"),
    ([("success", "success", "s1"), ("success", "failure", "s2"), ("success", "failure", "s1")], None,
     "contradicting_trials"),
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


CLEAR = {"fields": {"route_kind": ["intl"], "weather": ["clear"]}}
INTL = {"field": "route_kind", "op": "==", "value": "intl"}


@pytest.mark.parametrize("when, covered", [
    ([INTL], False),  # Open on the weather: wider than a stand tried in clear weather.
    ([], False),
    ([INTL, {"field": "weather", "op": "in", "value": ["clear", "rain"]}], False),
    ([INTL, {"field": "weather", "op": "!=", "value": "rain"}], False),  # A negation never bounds to a set.
    ([INTL, {"field": "weather", "op": "==", "value": "clear"}], True),
    ([INTL, {"field": "weather", "op": "in", "value": ["clear"]}], True),
    ([INTL, {"field": "weather", "op": "in", "value": ["clear", "rain"]},
      {"field": "weather", "op": "==", "value": "clear"}], True),  # Bounds on one field intersect,
    ([INTL, {"field": "weather", "op": "==", "value": "clear"},
      {"field": "weather", "op": "in", "value": ["clear", "rain"]}], True),  # in either order;
    ([INTL, {"field": "weather", "op": "==", "value": "clear"},
      {"field": "weather", "op": "!=", "value": "rain"}], True),  # a negation narrows a bound further.
])
def test_a_trial_covers_a_trigger_only_inside_what_the_stand_tried(when, covered):
    trials = [_trial("A", "B", ("success", "failure"), name, scope=CLEAR) for name in ("s1", "s2")]
    found = compare_trials("A", "B", trials, 2, [when, [INTL, {"field": "weather", "op": "==", "value": "clear"}]])
    assert (found["winner"], found["reason"]) == (("A", "better_in_stand_trials") if covered
                                                  else (None, "outside_transfer_scope"))


@pytest.mark.parametrize("scope, lifted", [(CLEAR, False), (SCOPE, True)])
def test_the_court_lifts_a_ban_only_for_a_trigger_inside_the_stand(scope, lifted):
    config, state, ids = data.world(labels=("q", "c"), trust=0.7, state_name="active")
    state["slow_only"] = [data.CONDITION]  # The competitors' trigger bounds the route kind alone.
    trials = [_trial(ids["q"], ids["c"], ("success", "failure"), name, scope=scope) for name in "123"]
    after, decision = data.window(config, state, advice=conflict_advice(None, state, config.parameters, [], [], [],
                                                                        trials))
    conflict, = decision["sections"]["conflicts"]
    assert conflict["step"] == (2 if lifted else 3) and (after["slow_only"] == []) is lifted
    assert conflict["advice"]["trial"]["reason"] == ("better_in_stand_trials" if lifted else "outside_transfer_scope")


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


def _trials_window(config, state, trials, comparison=None):
    advice = conflict_advice(None, state, config.parameters, [], [], [], trials)
    if comparison is not None:
        next(iter(advice.values()))["comparison"].update(winner=comparison, reason="better_in_same_situation")
    after, decision = data.window(config, state, advice=advice)
    conflict, = decision["sections"]["conflicts"]
    return after, decision, conflict


def _found_in_trials():
    config, state, ids = data.world(labels=("q", "c"), trust=0.7, state_name="active")
    state["slow_only"] = [data.CONDITION]
    winner, loser = ids["q"], ids["c"]
    trials = [_trial(winner, loser, ("success", "failure"), name) for name in ("s1", "s2", "s3")]
    after, _, conflict = _trials_window(config, state, trials)
    assert conflict["step"] == 2 and after["slow_only"] == [] and after["habits"][loser]["yields_to"] == [winner]
    assert after["habits"][loser]["resolved_by"] == {winner: TRIAL_BASIS}
    return config, after, winner, loser, trials


@pytest.mark.parametrize("contradiction", [
    ("failure", "success", "s1", 1),  # The same situation tried again with other results.
    ("success", "success", "s2", 1),
    ("failure", "success", "s4", 1),  # The loser did better in a situation of its own.
])
@pytest.mark.parametrize("later_first", [False, True])
def test_a_later_contradicting_trial_takes_away_the_resolution_trials_gave(contradiction, later_first):
    config, won, winner, loser, trials = _found_in_trials()
    *outcomes, situation, run = contradiction
    later = _trial(winner, loser, tuple(outcomes), situation, run=run)
    after, decision, conflict = _trials_window(config, won, [later, *trials] if later_first else [*trials, later])
    assert conflict["advice"]["trial"]["reason"] == "contradicting_trials"
    assert conflict["step"] == 3 and after["slow_only"] == [data.CONDITION]
    assert after["habits"][loser]["yields_to"] == [] and after["habits"][loser]["resolved_by"] == {}
    assert {decision["habits"][habit_id]["state"] for habit_id in (winner, loser)} == {"probation"}


def test_the_same_result_tried_again_keeps_the_winner():
    config, won, winner, loser, trials = _found_in_trials()
    after, _, conflict = _trials_window(config, won, [*trials, _trial(winner, loser, ("success", "failure"), "s1",
                                                                      run=1)])
    assert conflict["step"] == 2 and after["slow_only"] == [] and after["habits"][loser]["yields_to"] == [winner]


def test_a_resolution_compared_outcomes_found_since_rests_on_them():
    config, won, winner, loser, trials = _found_in_trials()
    compared, _, conflict = _trials_window(config, won, trials, comparison=winner)
    assert conflict["step"] == 2 and compared["habits"][loser]["resolved_by"] == {winner: COMPARISON_BASIS}
    after, _, conflict = _trials_window(config, compared, [*trials, _trial(winner, loser, ("failure", "success"),
                                                                           "s1", run=1)])
    assert (conflict["step"], conflict.get("standing")) == (2, True) and after["slow_only"] == []


def test_a_reversed_resolution_rests_on_what_reversed_it():
    config, won, winner, loser, trials = _found_in_trials()
    after, _, conflict = _trials_window(config, won, trials, comparison=loser)
    assert conflict["step"] == 2 and after["habits"][winner]["yields_to"] == [loser]
    assert (after["habits"][winner]["resolved_by"], after["habits"][loser]["resolved_by"]) == (
        {loser: COMPARISON_BASIS}, {})


@pytest.mark.parametrize("basis, stands", [(COMPARISON_BASIS, True), (None, False)])
def test_a_resolution_whose_basis_is_not_known_does_not_stand(basis, stands):
    config, won, winner, loser, _ = _found_in_trials()
    won["habits"][loser]["resolved_by"] = {} if basis is None else {winner: basis}
    after, _, conflict = _trials_window(config, won, [])  # Neither history nor trials decide in this window.
    assert (conflict["step"] == 2) is stands and (after["slow_only"] == []) is stands
