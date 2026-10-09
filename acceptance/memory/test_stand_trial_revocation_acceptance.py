"""A resolution found in stand trials stands only while the trials still find it (review N1).

Two learned recoveries of one refused search stand in an unresolved conflict;
three stand trials on full routes, each from a restored copy of one initial
state, find the capacity recovery better: the next consolidation lifts the
slow-only ban and the quota recovery yields.

* The operator tries one full route again, from a properly restored copy: the
  same results. The next ordinary session keeps the resolution and recovers a
  full route on the fast path.
* The operator tries that route once more on a stand restored with a slot left
  held for the route — the stand observes the same start, but the quota
  recovery now works too. The trials contradict each other. The next ordinary
  session — no reassessment — takes the resolution away: step 3, the quota
  recovery no longer yields, both are on probation and the trigger is slow-only
  again.
* The decision is the memory's state, read again by a fresh owner from its
  verified snapshot and tail; the session after it opens on that boundary and
  recovers the full route on the slow path, with no habit acting.

The environment, restored before every arm, is the checker's truth.
"""
from __future__ import annotations

import copy
import json

from acceptance.memory import _competitors as competitors
from synapse.memory_consolidation.court.projection import fold
from synapse.memory_consolidation.owner import MemoryOwner

STAND = "acceptance-stand"
SCOPE = {"fields": {"route_kind": ["intl"], "tool": ["flights"], "transport": ["ok"], "op_result": ["op_error"],
                    "op_err": ["BUSY"], "effect": ["none"]}}


def _arm(world, initial, run_id, route, habit_id, snapshot):
    world.restore(initial)
    world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, "quota"), exam=("B", snapshot, habit_id))
    assert world.events(run_id, "habit_activated")[0]["habit_id"] == habit_id


def _trial(world, initial, name, route, habits, snapshot):
    _arm(world, initial, f"q-{name}", route, habits["quota"], snapshot)
    _arm(world, initial, f"c-{name}", route, habits["capacity"], snapshot)
    code, recorded, stderr = world.memory("trial", "--arm", world.runs / f"q-{name}.json",
                                          "--arm", world.runs / f"c-{name}.json",
                                          "--stand", STAND, "--scope", json.dumps(SCOPE))
    assert code == 0 and recorded["trial"]["decided"] is True, (recorded, stderr)
    return {recovery: recorded["trial"]["arms"][habit_id]["outcome"] for recovery, habit_id in habits.items()}


def _session(world, initial, run_id, route="VNO"):
    world.restore(initial)
    world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, "quota"))
    conflict, = world.reports()[-1]["conflicts"]
    return conflict


def test_a_later_contradicting_trial_takes_the_resolution_away(tmp_path):
    world = competitors.world(tmp_path)
    first, second = competitors.learn_competitors(world)
    habits = {competitors.recovery_of(world, item["habit_id"]): item["habit_id"] for item in (first, second)}
    snapshot = world.reports()[-1]["snapshot_boundary_after"]
    trigger = world.reports()[-1]["slow_only_triggers"]
    initial = world.world()
    for route in competitors.FULL:
        assert _trial(world, initial, route, route, habits, snapshot) == {"quota": "failure", "capacity": "success"}
    conflict = _session(world, initial, "decided")
    assert conflict["step"] == 2 and conflict["advice"]["trial"]["winner"] == habits["capacity"]
    assert world.reports()[-1]["slow_only_triggers"] == []

    # The same route tried again from a properly restored copy: the same results keep the resolution.
    route = competitors.FULL[0]
    assert _trial(world, initial, "again", route, habits, snapshot) == {"quota": "failure", "capacity": "success"}
    conflict = _session(world, initial, "kept")
    assert conflict["step"] == 2 and world.reports()[-1]["slow_only_triggers"] == []
    world.restore(initial)
    world.run(competitors.PROGRAM, "fast", competitors.inputs("fast", route, "quota"))
    assert [item["habit_id"] for item in world.events("fast", "habit_activated")] == [habits["capacity"]]

    # A stand restored with a slot left held for the route: the same observed start, other results.
    leftover = copy.deepcopy(initial)
    leftover.setdefault("objects", {}).setdefault("slots", {})[route] = {"route": route, "state": "held"}
    assert _trial(world, leftover, "leftover", route, habits, snapshot) == {"quota": "success", "capacity": "success"}
    conflict = _session(world, initial, "contradicted")
    assert (conflict["advice"]["trial"]["winner"], conflict["advice"]["trial"]["reason"]) == (
        None, "contradicting_trials")
    assert conflict["step"] == 3 and world.reports()[-1]["slow_only_triggers"] == trigger
    assert world.reports()[-1].get("reassessment") is None  # An ordinary session, not a reassessment.

    # The state a fresh owner reads from snapshot and tail; the next session opens on it.
    owner = MemoryOwner(world.state, read_only=True)
    state = owner.state()
    assert state == fold(item["report"] for item in owner.applied() if item["report"] is not None)
    assert {habit_id: (state["habits"][habit_id]["state"], state["habits"][habit_id]["yields_to"])
            for habit_id in habits.values()} == {habit_id: ("probation", []) for habit_id in habits.values()}
    world.restore(initial)
    world.run(competitors.PROGRAM, "slow", competitors.inputs("slow", route, "quota"))
    assert world.events("slow", "habit_activated") == []
