"""Real competitors meet the conflict ladder; an unresolved conflict keeps its trigger slow-only.

Two recoveries of the same refused search — through the quota, or through the
capacity and a reserved slot — are verified in different tasks. The first is
born while a session opened on the older snapshot is still in its slow path;
that session's recovery completes the second candidate, which is born as a
competitor of the first (one applicability, one expected outcome, another
action). With equal trust and no recorded counterfactual basis the court
reaches step 3: both go to probation and the trigger becomes slow-only, so
the following sessions recover on the slow path again.
"""
from __future__ import annotations

import time

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = travel.PROGRAM.replace('''    let quota = tool("quota_status", {"route": route})
''', '''    if recovery == "quota" {
      let quota = tool("quota_status", {"route": route})
    } else {
      let capacity = tool("capacity_status", {"route": route})
      let slot = tool("reserve_slot", {"route": route})
    }
''')


def _tools():
    slow = {"payload": {"ok": True, "capacity": "open"}, "delay": 20}
    capacity = tool("capacity_status", "capacity:ops", [
        answer({"ok": True, "capacity": "open"}, when={"route": "EVN"}, sequence=[slow]),
        answer({"ok": True, "capacity": "open"})], server="ops", contract={"idempotent": True})
    slot = tool("reserve_slot", "slots:ops", [answer({"ok": True, "slot": "held"})], server="ops")
    return [*travel.tools(), capacity, slot]


def _inputs(task, route, recovery):
    return {**travel.inputs(task, route), "recovery": recovery}


def test_competitors_climb_the_ladder_and_an_unresolved_conflict_stays_slow_only(tmp_path):
    provenance = {**travel.independent_provenance(), "capacity:ops": {"ancestors": []},
                  "slots:ops": {"ancestors": []}}
    world = MemoryWorld(tmp_path, _tools(), provenance=provenance)
    for run_id, route, recovery in (("quota-1", "BUS", "quota"), ("quota-2", "YVR", "quota"),
                                    ("capacity-1", "MSQ", "capacity"), ("capacity-2", "TBS", "capacity")):
        world.run(PROGRAM, run_id, _inputs(run_id, route, recovery))

    # A session opened on the older snapshot waits in its slow path while the first recovery is born.
    waiting = world.start(PROGRAM, "capacity-3", _inputs("capacity-3", "EVN", "capacity"))
    deadline = time.monotonic() + 120
    while {"route": "EVN"} not in world.calls("capacity_status"):
        assert time.monotonic() < deadline and waiting.poll() is None
        time.sleep(0.2)
    world.run(PROGRAM, "quota-3", _inputs("quota-3", "RIX", "quota"))
    first, = world.reports()[-1]["births"]
    stdout, stderr = waiting.communicate(timeout=600)
    assert waiting.returncode == 0, (stdout, stderr)
    assert world.opening("capacity-3")["learned"] == []
    second, = world.reports()[-1]["births"]
    assert second["typed_check"] != "successor" and second["habit_id"] != first["habit_id"]

    # Both are loaded; equal trust announces the conflict at the fire.
    world.run(PROGRAM, "both", _inputs("both", "VNO", "quota"))
    assert sorted(item["habit_id"] for item in world.opening("both")["learned"]) == sorted(
        [first["habit_id"], second["habit_id"]])
    fired, = world.events("both", "habit_activated")
    assert fired["conflict_warning"] is True and fired["runner_up_habit_id"] in {first["habit_id"],
                                                                                   second["habit_id"]}

    # The ladder: no trust gap, no counterfactual basis — step 3, both on probation, the trigger slow-only.
    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 3 and conflict["resolution"] == "both_probation_trigger_slow_only"
    assert sorted(conflict["habits"].values()) == sorted([first["habit_id"], second["habit_id"]])
    assert {(item["habit_id"], item["rule"], item["to"]) for item in report["transitions"]} == {
        (first["habit_id"], "TC", "probation"), (second["habit_id"], "TC", "probation")}
    assert report["slow_only_triggers"] == [conflict["trigger"]]

    # The unresolved conflict persists: the next session recovers on the slow path.
    world.run(PROGRAM, "after", _inputs("after", "TLL", "quota"))
    assert world.opening("after")["learned"] == [] and world.events("after", "habit_activated") == []
    assert len(world.events("after", "slow_path_used")) == 1
    assert world.reports()[-1]["slow_only_triggers"] == [conflict["trigger"]]
