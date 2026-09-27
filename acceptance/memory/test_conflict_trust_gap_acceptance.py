"""Step 1 of the conflict ladder: an established trust gap selects the senior habit (refinement §6).

The same pair of competitors, under a declared policy that updates trust on
every counted fire and learns fast while born. The first recovery fires once,
verified, before its competitor is born: its trust is then at least the
declared ``conflict_gap`` above the newborn's. The court judges the pair at
the birth and selects the senior by trust alone: nobody goes to probation and
no trigger loses its fast path. At run time both are loaded and both apply;
the junior gives way to the senior before anything is done, and the senior
acts.
"""
from __future__ import annotations

from acceptance.memory import _competitors as competitors

POLICY = {"lr": {"born": 0.8}, "min_evidence": 1}


def test_an_established_trust_gap_selects_the_senior_competitor(tmp_path):
    world = competitors.world(tmp_path, delay=25, hold=25, parameters=POLICY)

    def fire_the_first():
        world.run(competitors.PROGRAM, "fire", competitors.inputs("fire", "VNO", "quota"))

    first, second = competitors.learn_competitors(world, fire_the_first)
    fired, = world.events("fire", "habit_activated")
    assert fired["habit_id"] == first["habit_id"] and fired["outcome"] == "success"

    report = world.reports()[-1]
    conflict, = report["conflicts"]
    assert conflict["step"] == 1 and conflict["at_birth"] is True
    assert conflict["resolution"] == "A_selected_by_trust_gap"
    assert conflict["habits"] == {"A": first["habit_id"], "B": second["habit_id"]} and conflict["gap"] >= 0.30
    assert [item for item in report["transitions"] if item["rule"] == "TC"] == []
    assert report["slow_only_triggers"] == []

    # Both are loaded and apply; the junior yields to the senior before any effect, the senior acts.
    recovery = competitors.recovery_of(world, first["habit_id"])
    probe = "quota_status" if recovery == "quota" else "capacity_status"
    before = len(world.calls(probe))
    world.run(competitors.PROGRAM, "after", competitors.inputs("after", "TLL", "quota"))
    learned = {item["habit_id"]: item for item in world.opening("after")["learned"]}
    assert learned[second["habit_id"]]["yields_to"] == [first["habit_id"]]
    held, = world.events("after", "habit_suppressed")
    assert (held["habit_id"], held["reason"], held["competitor"]) == (
        second["habit_id"], "yields_to_rival", first["habit_id"])
    fired, = world.events("after", "habit_activated")
    assert fired["habit_id"] == first["habit_id"] and fired["conflict_warning"] is False
    assert world.events("after", "slow_path_used") == []
    assert len(world.calls(probe)) == before + 1 and {"route": "TLL"} in world.calls(probe)
