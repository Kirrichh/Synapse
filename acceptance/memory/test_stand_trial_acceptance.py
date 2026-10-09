"""Competitors compared in independent copies of one initial state (review R5 §4–5).

Two learned recoveries of one refused search stand in an unresolved conflict:
their shared trigger is slow-only, and their histories cannot compare them.
The operator runs trials on a stand: each competitor alone (an exam in mode B
naming it, ``--exam-trial``), with the stand restored to the same initial
state before each arm, and records each pair of arms with
``synapse memory trial`` and the transfer scope the stand reproduces:

* arms that started from different states (two routes) are recorded but
  compare nothing; decided trials on a stand whose scope reproduces the route
  kind but not the rest of the competitors' trigger prove nothing about it; arms
  from one state on a stand that reproduces only domestic routes decide
  nothing, the situation lies outside its scope; an
  arm whose habit never acted is no arm — the act is refused. The conflict
  stays unresolved and the trigger slow-only;
* three trials on full routes, where only the capacity recovery works, decide
  the conflict at the next consolidation: a verified comparison whose basis
  is the stand trial, inside its transfer scope. The capacity recovery stays,
  the quota recovery yields and goes on probation, the slow-only ban is
  lifted — and the next session recovers a full route on the fast path.

The environment, restored before every arm, is the checker's truth.
"""
from __future__ import annotations

import json

from acceptance.memory import _competitors as competitors

STAND = "acceptance-stand"
#: What the stand reproduces: every typed field of the refused search the competitors' trigger reads.
SCOPE = {"fields": {"route_kind": ["intl"], "tool": ["flights"], "transport": ["ok"], "op_result": ["op_error"],
                    "op_err": ["BUSY"], "effect": ["none"]}}
#: A stand that reproduces the route kind alone cannot speak for the rest of the trigger.
PARTIAL = {"fields": {"route_kind": ["intl"]}}


def _arm(world, initial, run_id, route, habit_id, snapshot):
    world.restore(initial)  # A fresh copy of the one initial state: the arms share nothing mutable.
    world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, "quota"), exam=("B", snapshot, habit_id))
    fired = world.events(run_id, "habit_activated")[0]
    assert fired["habit_id"] == habit_id and world.opening(run_id)["trial"] == habit_id


def _trial(world, left, right, scope=SCOPE):
    return world.memory("trial", "--arm", world.runs / f"{left}.json", "--arm", world.runs / f"{right}.json",
                        "--stand", STAND, "--scope", json.dumps(scope))


def _conflict(world):
    conflict, = world.reports()[-1]["conflicts"]
    return conflict


def test_stand_trials_inside_their_scope_decide_what_history_cannot(tmp_path):
    world = competitors.world(tmp_path)
    first, second = competitors.learn_competitors(world)
    habits = {competitors.recovery_of(world, item["habit_id"]): item["habit_id"] for item in (first, second)}
    snapshot = world.reports()[-1]["snapshot_boundary_after"]
    trigger = world.reports()[-1]["slow_only_triggers"]
    initial = world.world()

    # Arms from different initial states, a scope that does not cover the trigger, an arm whose habit never acted.
    _arm(world, initial, "apart-quota", "OSL", habits["quota"], snapshot)
    _arm(world, initial, "apart-capacity", "ARN", habits["capacity"], snapshot)
    code, apart, stderr = _trial(world, "apart-quota", "apart-capacity")
    assert code == 0, (apart, stderr)
    assert apart["trial"]["same_initial_state"] is False and apart["trial"]["decided"] is False
    code, elsewhere, _ = _trial(world, "apart-quota", "apart-capacity", {"fields": {"route_kind": ["dom"]}})
    assert elsewhere["trial"]["reason"] == "arms_started_from_different_states"
    world.restore(initial)
    world.run(competitors.PROGRAM, "no-arm", competitors.inputs("no-arm", "LED", "quota"),
              exam=("B", snapshot, habits["capacity"]))  # A domestic route: the habit does not apply.
    assert world.events("no-arm", "habit_activated") == []
    code, refused, _ = _trial(world, "apart-quota", "no-arm")
    assert code == 2 and refused["status"] == "REFUSED"
    for route in competitors.FULL:  # Decided trials on a stand whose scope does not cover the trigger.
        _arm(world, initial, f"pq-{route}", route, habits["quota"], snapshot)
        _arm(world, initial, f"pc-{route}", route, habits["capacity"], snapshot)
        code, partial, _ = _trial(world, f"pq-{route}", f"pc-{route}", PARTIAL)
        assert code == 0 and partial["trial"]["decided"] is True
    # Arms from one initial state on a stand that reproduces only domestic routes: the situation lies outside.
    code, domestic, _ = _trial(world, f"pq-{competitors.FULL[0]}", f"pc-{competitors.FULL[0]}",
                               {"fields": {"route_kind": ["dom"]}})
    assert code == 0 and domestic["trial"]["same_initial_state"] is True
    assert (domestic["trial"]["decided"], domestic["trial"]["reason"]) == (False, "situation_outside_transfer_scope")
    world.restore(initial)
    world.run(competitors.PROGRAM, "still", competitors.inputs("still", "VNO", "quota"))
    conflict = _conflict(world)
    assert conflict["step"] == 3 and world.reports()[-1]["slow_only_triggers"] == trigger
    verdict = conflict["advice"]["trial"]
    # Nothing decided inside the scope: the arms from two states compare nothing; the partial and the domestic
    # scopes cover nothing of the trigger.
    assert verdict["winner"] is None and verdict["pairs"] == 0 and verdict["outside_scope"] == 5

    # Three trials on full routes, each from a restored copy of the same state.
    recorded = []
    for route in competitors.FULL:
        _arm(world, initial, f"q-{route}", route, habits["quota"], snapshot)
        _arm(world, initial, f"c-{route}", route, habits["capacity"], snapshot)
        code, trial, stderr = _trial(world, f"q-{route}", f"c-{route}")
        assert code == 0 and trial["trial"]["decided"] is True, (trial, stderr)
        assert trial["trial"]["arms"][habits["capacity"]]["outcome"] == "success"
        assert trial["trial"]["arms"][habits["quota"]]["outcome"] == "failure"
        recorded.append(trial["trial"]["id"])
    world.restore(initial)
    world.run(competitors.PROGRAM, "after", competitors.inputs("after", "VNO", "quota"))
    conflict = _conflict(world)
    assert conflict["step"] == 2 and conflict["slow_only_lifted"] is True
    assert conflict["resolution"] == f"{habits['capacity']}_stays_{habits['quota']}_probation"
    verdict = conflict["advice"]["trial"]
    assert verdict["basis"] == "stand_trial" and verdict["stands"] == [STAND] and verdict["pairs"] == 3
    assert set(recorded) <= set(verdict["trials"]) and verdict["outside_scope"] == 5  # Partial and domestic.
    assert world.reports()[-1]["slow_only_triggers"] == []

    # The next session recovers a full route on the fast path, by the recovery the trials found better.
    world.run(competitors.PROGRAM, "fast", competitors.inputs("fast", "OSL", "quota"))
    fired = world.events("fast", "habit_activated")
    assert [item["habit_id"] for item in fired] == [habits["capacity"]] and fired[0]["outcome"] == "success"
