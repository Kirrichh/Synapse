"""Step 1 of the conflict ladder: an established trust gap selects the senior habit (refinement §6).

The same pair of competitors, under a declared policy that updates trust on
every counted fire and learns fast while born. The first shared session fires
one of them at equal trust; its verified success opens a gap at least the
declared ``conflict_gap``, so the court selects it by trust alone: nobody goes
to probation and no trigger loses its fast path. The next session executes
the senior habit with no conflict warning.
"""
from __future__ import annotations

from acceptance.memory import _competitors as competitors

POLICY = {"lr": {"born": 0.8}, "min_evidence": 1}


def test_an_established_trust_gap_selects_the_senior_competitor(tmp_path):
    world = competitors.world(tmp_path, parameters=POLICY)
    first, second = competitors.learn_competitors(world)

    world.run(competitors.PROGRAM, "both", competitors.inputs("both", "VNO", "quota"))
    fired, = world.events("both", "habit_activated")
    assert fired["conflict_warning"] is True  # Equal trust when the session opened.
    senior = fired["habit_id"]
    junior = next(item for item in (first["habit_id"], second["habit_id"]) if item != senior)

    report = world.reports()[-1]
    update, = [item for item in report["trust_decisions"] if item["habit_id"] == senior]
    assert update["trust_new"] - update["trust_old"] >= 0.30
    conflict, = report["conflicts"]
    assert conflict["step"] == 1 and conflict["resolution"] == "A_selected_by_trust_gap"
    assert conflict["habits"] == {"A": senior, "B": junior} and conflict["gap"] >= 0.30
    assert [item for item in report["transitions"] if item["rule"] == "TC"] == []
    assert report["slow_only_triggers"] == []

    # The senior habit acts on the fast path, without a warning; the environment shows its recovery.
    recovery = competitors.recovery_of(world, senior)
    probe = "quota_status" if recovery == "quota" else "capacity_status"
    before = len(world.calls(probe))
    world.run(competitors.PROGRAM, "after", competitors.inputs("after", "TLL", "quota"))
    fired, = world.events("after", "habit_activated")
    assert fired["habit_id"] == senior and fired["conflict_warning"] is False
    assert world.events("after", "slow_path_used") == []
    assert len(world.calls(probe)) == before + 1 and {"route": "TLL"} in world.calls(probe)
