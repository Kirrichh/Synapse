"""Three exam modes on the same memory (refinement §17).

After a learned recovery is born and admitted, three exam runs read the same
fixed snapshot through the ordinary launch: A without accumulated experience,
B with admitted learned habits, C with the same habits every one of them
slow-only. The environment shows which path each run took; the project
journal shows that no exam taught the memory.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def test_exam_modes_read_one_snapshot_and_never_teach(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]
    snapshot = world.reports()[-1]["snapshot_boundary_after"]
    journal = world.journal()

    for mode, route in (("A", "TBS"), ("B", "EVN"), ("C", "RIX")):
        world.run(travel.PROGRAM, f"exam-{mode}", travel.inputs(f"exam-{mode}", route), exam=(mode, snapshot))

    # A: accumulated experience is off; the slow path recovers.
    opening = world.opening("exam-A")
    assert opening["exam"] == "A" and opening["learned"] == [] and opening["boundary"] is None
    assert world.events("exam-A", "habit_activated") == [] and len(world.events("exam-A", "slow_path_used")) == 1

    # B: the admitted habit of the snapshot acts on the fast path.
    opening = world.opening("exam-B")
    assert opening["exam"] == "B" and opening["boundary"] == snapshot
    assert [item["habit_id"] for item in opening["learned"]] == [birth["habit_id"]]
    assert len(world.events("exam-B", "habit_activated")) == 1 and world.events("exam-B", "slow_path_used") == []

    # C: the same snapshot and read rights, every learned trigger slow-only.
    opening = world.opening("exam-C")
    assert opening["exam"] == "C" and opening["boundary"] == snapshot
    assert [(item["habit_id"], item["slow_only"]) for item in opening["learned"]] == [(birth["habit_id"], True)]
    suppressed, = world.events("exam-C", "habit_suppressed")
    assert suppressed["habit_id"] == birth["habit_id"] and suppressed["reason"] == "slow_only"
    assert world.events("exam-C", "habit_activated") == [] and len(world.events("exam-C", "slow_path_used")) == 1

    # Every exam recovered its search in the environment, by its own path.
    for route in ("TBS", "EVN", "RIX"):
        assert world.calls("flights").count({"route": route}) == 2 and {"route": route} in world.calls("booking_state")
    # No exam taught the memory: no session, report or decision was added.
    assert world.journal() == journal
    assert all(world.events(f"exam-{mode}", "session_consolidated") == [] for mode in "ABC")
