"""A paired A/B/C exam: the same tasks, each arm from the same initial state (review, package 5).

After a learned search recovery is born and admitted, one fixed snapshot is
examined in three arms on the very same tasks:

* A — accumulated experience switched off: nothing is loaded;
* B — the learned habits the snapshot admits act on the fast path;
* C — the same habits are loaded but every learned trigger is slow-only.

Before each arm the environment is restored to the state it had when the
snapshot was taken, so every arm meets exactly the same refusals and answers
and no arm's effects reach another. The tasks include one outside the habit's
scope (a domestic route), where all three arms must behave alike.

Per task the environment's own record is compared across arms: the same
answers, the same final result; only the path differs — B recovers the
international searches on the fast path, A and C on the slow path. No arm
teaches the memory.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

TASKS = ("TBS", "EVN", "LED")


def _arm(world, mode, snapshot, initial):
    world.restore(initial)
    record = {}
    for route in TASKS:
        run_id = f"{mode}-{route}"
        world.run(travel.PROGRAM, run_id, travel.inputs(run_id, route), exam=(mode, snapshot))
        record[route] = {
            "path": "fast" if world.events(run_id, "habit_activated") else "slow",
            "slow_path": len(world.events(run_id, "slow_path_used")),
            "suppressed": [item["reason"] for item in world.events(run_id, "habit_suppressed")],
            "loaded": [item["habit_id"] for item in world.opening(run_id)["learned"]],
            "flights": [call for call in world.calls("flights") if call == {"route": route}],
            "found": {"route": route} in world.calls("booking_state")}
    return record, world.world()["effects"]


def test_three_arms_on_the_same_tasks_from_the_same_state(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]
    snapshot = world.reports()[-1]["snapshot_boundary_after"]
    initial, journal = world.world(), world.journal()

    arms = {mode: _arm(world, mode, snapshot, initial) for mode in "ABC"}

    for route in TASKS:
        a, b, c = (arms[mode][0][route] for mode in "ABC")
        # The same environment met every arm: the refused search and its repeat, then the independent record.
        assert a["flights"] == b["flights"] == c["flights"] == [{"route": route}] * 2
        assert a["found"] and b["found"] and c["found"]
        assert a["loaded"] == [] and b["loaded"] == c["loaded"] == [birth["habit_id"]]
    # Inside the habit's scope only B takes the fast path; outside it every arm takes the slow path.
    assert [arms["B"][0][route]["path"] for route in TASKS] == ["fast", "fast", "slow"]
    assert [arms["A"][0][route]["path"] for route in TASKS] == ["slow"] * 3
    assert [arms["C"][0][route]["path"] for route in TASKS] == ["slow"] * 3
    assert [arms["C"][0][route]["suppressed"] for route in TASKS] == [["slow_only"], ["slow_only"], []]
    # Each arm started from the same state and left the same effects: none leaked into another.
    assert arms["A"][1] == arms["B"][1] == arms["C"][1]
    # No arm taught the memory.
    assert world.journal() == journal
