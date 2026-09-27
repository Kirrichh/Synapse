"""One fire per window is experience, not a failure (review R6), through the canonical launch.

A learned search recovery is born after three verified sessions. It then fires
once per session, and every session is its own consolidation window. Each
fire is a verified success. Too little experience is never a reason to demote:
the habit stays born while its fires accumulate across windows, and it is
promoted (T1) once it has the declared number of fires in the declared number
of tasks — exactly as one window with the same fires would have promoted it.
"""
from __future__ import annotations

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def test_rare_verified_fires_accumulate_to_a_promotion(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(), provenance=travel.independent_provenance())
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]

    moves = []
    for index, route in enumerate(["TBS", "EVN", "RIX", "VNO", "TLL"]):
        world.run(travel.PROGRAM, f"use-{index}", travel.inputs(f"use-{index}", route))
        fired, = world.events(f"use-{index}", "habit_activated")
        assert fired["habit_id"] == birth["habit_id"] and fired["outcome"] == "success"
        report = world.reports()[-1]
        assert [entry["run_id"] for entry in report["window"]["sessions"]] == [f"use-{index}"]
        moves.append([(item["rule"], item["to"], item["cause"]) for item in report["transitions"]
                      if item["habit_id"] == birth["habit_id"]])

    # Four windows with one success each change nothing; the fifth fire completes the declared experience.
    assert moves == [[], [], [], [], [("T1", "active", "sufficient_experience")]]
    habit = world.reports()[-1]["apply"]["habits"][birth["habit_id"]]
    assert habit["state"] == "active" and habit["fires_since_birth"] == 5
