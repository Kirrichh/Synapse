"""Rivals whose triggers overlap only partly are held back where they meet (review R5).

In a tiered world the capacity recovery is learned only on routes the service
reports as gold, so its trigger requires the tier; the quota recovery's does
not. Their triggers differ, so no consolidation pairs them at birth. They meet
for the first time at run time, on a gold route both apply to: with no
verified resolution neither acts, before any effect — the slow path recovers.
The court then records the conflict on the overlap from what the runtime held
back; each habit keeps its own trigger and outside the overlap the quota
recovery acts alone.
"""
from __future__ import annotations

from acceptance.memory import _competitors as competitors


def test_partly_overlapping_rivals_are_held_back_on_the_overlap_only(tmp_path):
    world = competitors.world(tmp_path, tiered=True)
    first, second = competitors.learn_competitors(world)
    habits = {competitors.recovery_of(world, item["habit_id"]): item["habit_id"] for item in (first, second)}
    report = world.reports()[-1]
    assert report["conflicts"] == [] and report["slow_only_triggers"] == []  # Other triggers: not paired at birth.

    # The first meeting, on a gold route: both apply, neither acts, the slow path recovers.
    world.run(competitors.PROGRAM, "gold", competitors.inputs("gold", "VNO", "quota"))
    learned = {item["habit_id"]: item for item in world.opening("gold")["learned"]}
    assert learned[habits["quota"]]["unresolved_with"] == [habits["capacity"]]
    held = {(item["habit_id"], item["reason"], item["competitor"]) for item in world.events("gold", "habit_suppressed")}
    assert held == {(habits["quota"], "unresolved_conflict", habits["capacity"]),
                    (habits["capacity"], "unresolved_conflict", habits["quota"])}
    assert world.events("gold", "habit_activated") == [] and len(world.events("gold", "slow_path_used")) == 1
    assert {"route": "VNO"} not in world.calls("capacity_status")  # The capacity recovery never ran.

    # The court records the conflict on the overlap and keeps both triggers.
    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 3 and conflict["resolution"] == "held_back_on_the_overlap_until_compared"
    assert conflict["trigger"] is None and sorted(conflict["habits"].values()) == sorted(habits.values())
    assert report["slow_only_triggers"] == []
    assert [item for item in report["transitions"] if item["rule"] == "TC"] == []

    # Outside the overlap only the quota recovery applies, and it acts.
    world.run(competitors.PROGRAM, "plain", competitors.inputs("plain", "TLL", "quota"))
    fired, = world.events("plain", "habit_activated")
    assert fired["habit_id"] == habits["quota"] and world.events("plain", "habit_suppressed") == []
    assert world.events("plain", "slow_path_used") == []
