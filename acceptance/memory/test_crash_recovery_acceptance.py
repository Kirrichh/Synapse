"""A crash in the middle of a session: no partial credit, one application per input.

The process running a durable session is killed while its slow path waits for
an answer. Nothing of the unfinished session reaches memory. Resuming the same
run first records its tail in emergency mode and applies nothing — no births,
no trust, no cursor (review R11) — then the gateway recovers the interrupted
idempotent call and the session completes; its full consolidation judges the
whole session once and counts its episode once.
"""
from __future__ import annotations

import os
import signal
import time

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def _await(condition, seconds=120):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the scenario did not reach its crash point"
        time.sleep(0.2)


def test_killed_session_is_recovered_without_partial_credit(tmp_path):
    world = MemoryWorld(tmp_path, travel.tools(slow_quota=("RIX",)), provenance=travel.independent_provenance())
    for index, route in enumerate(["BUS", "YVR"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    before = world.reports()

    process = world.start(travel.PROGRAM, "crashed", travel.inputs("task-crashed", "RIX"))
    _await(lambda: {"route": "RIX"} in world.calls("quota_status"))
    os.killpg(process.pid, signal.SIGKILL)  # The session and its tool servers die mid slow path.
    process.wait(60)

    # No partial credit: the unfinished session changed nothing in memory.
    assert world.reports() == before
    assert all(entry["run_id"] != "crashed" for report in before for entry in report["window"]["sessions"])

    code, payload, stderr = world.resume("crashed")
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    after = world.reports()[len(before):]
    assert [report["mode"] for report in after] == ["emergency", "full"]
    emergency, full = after
    assert emergency["births"] == [] and emergency["trust_decisions"] == []
    assert emergency["snapshot_boundary_after"] is None and emergency["pool_updates"] == []
    # It saw the tail and deferred it: no cursor moved, no case was applied.
    assert emergency["window"]["sessions"] == [] and emergency["apply"]["cursors"] == {}
    assert [(item["run_id"], item["from"]) for item in emergency["window"]["deferred"]] == [("crashed", 0)]
    assert emergency["apply"]["quanta"] == {}
    assert emergency["marker_verdicts"] == []  # The interrupted segment was still executing: no verdict yet.
    # The interrupted idempotent read was answered again; the search was never repeated blindly.
    assert world.calls("quota_status").count({"route": "RIX"}) == 2
    assert world.calls("flights").count({"route": "RIX"}) == 2
    # One application per input: the recovered episode is counted once and completes the birth.
    birth, = full["births"]
    assert birth["criteria"]["episodes"] == 3 and birth["criteria"]["tasks"] == 3
    assert sorted(item["run_id"] for item in birth["episodes"]) == ["crashed", "learn-0", "learn-1"]
    crashed, = [entry for report in world.reports() for entry in report["window"]["sessions"]
                if entry["run_id"] == "crashed"]
    assert crashed["from"] == 0  # The full window judged the whole session, its tail included, once.

    # Re-entering the completed run applies nothing again.
    journal = world.journal()
    world.resume("crashed")
    assert world.journal() == journal
