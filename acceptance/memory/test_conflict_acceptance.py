"""Real competitors meet the conflict ladder; an unresolved conflict keeps its trigger slow-only.

With equal trust and no recorded counterfactual basis the court reaches step 3:
both competitors go to probation and the trigger becomes slow-only, so the
following sessions recover on the slow path again.
"""
from __future__ import annotations

from acceptance.memory import _competitors as competitors


def test_competitors_climb_the_ladder_and_an_unresolved_conflict_stays_slow_only(tmp_path):
    world = competitors.world(tmp_path)
    first, second = competitors.learn_competitors(world)

    # Both are loaded; equal trust announces the conflict at the fire.
    world.run(competitors.PROGRAM, "both", competitors.inputs("both", "VNO", "quota"))
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
    world.run(competitors.PROGRAM, "after", competitors.inputs("after", "TLL", "quota"))
    assert world.opening("after")["learned"] == [] and world.events("after", "habit_activated") == []
    assert len(world.events("after", "slow_path_used")) == 1
    assert world.reports()[-1]["slow_only_triggers"] == [conflict["trigger"]]
