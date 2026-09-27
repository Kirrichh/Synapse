"""A competitor born at equal trust is blocked before it can act (review R5).

The second recovery is born as a competitor of the first with no established
trust gap and no recorded outcome that could compare them. The court judges
the pair in the very consolidation that births it: step 3, the live
competitor goes to probation and the shared trigger becomes slow-only. No
session ever fires either habit on that trigger and no conflict is merely
announced after an effect: the following sessions recover on the slow path.
"""
from __future__ import annotations

from acceptance.memory import _competitors as competitors


def test_a_competitor_born_without_a_resolution_closes_the_fast_path_before_any_fire(tmp_path):
    world = competitors.world(tmp_path)
    first, second = competitors.learn_competitors(world)

    # The birth's own consolidation judged the pair: step 3, the trigger slow-only.
    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 3 and conflict["at_birth"] is True
    assert conflict["resolution"] == "trigger_slow_only_until_compared"
    assert sorted(conflict["habits"].values()) == sorted([first["habit_id"], second["habit_id"]])
    assert conflict["advice"]["basis"] == "born_this_window" and conflict["gap"] < 0.30
    assert (first["habit_id"], "TC", "probation") in {(item["habit_id"], item["rule"], item["to"])
                                                      for item in report["transitions"]}
    assert report["slow_only_triggers"] == [conflict["trigger"]]

    # Neither habit is loaded: the next sessions recover on the slow path, no habit ever fires.
    for run_id, route in (("both", "VNO"), ("after", "TLL")):
        world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, "quota"))
        assert world.opening(run_id)["learned"] == [] and world.events(run_id, "habit_activated") == []
        assert len(world.events(run_id, "slow_path_used")) == 1
    assert world.reports()[-1]["slow_only_triggers"] == [conflict["trigger"]]
