"""A segment completed before a crash still teaches, and counts, once (finding R11 on 556624d).

Two recoveries of a refused flight search (BUS, YVR) wait for a third. A third task recovers MSQ, has the search
confirmed independently and leaves its segment; it then reads the quota of EVN and is killed while the quota of RIX
is slow to answer. Resuming the run records its tail in emergency mode and applies nothing — no cursor, case,
signal or birth — the gateway answers the interrupted idempotent read again and the session completes. Its full
consolidation judges the whole session once, with its re-execution: the completed segment is the third example and
the procedure is born from BUS, YVR and MSQ. No search was repeated for it, and entering the completed run again
changes nothing.

Once the procedure is learned, a task in which it acts and then crashes the same way leaves one fire: counted once
in its execution summary, its recent fires and its evidence.

The same programs without the crash (control) teach and count the same.
"""
from __future__ import annotations

import os
import signal
import time

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

#: After its segment, the task reads two more quotas; the second is where it is killed.
TAIL = travel.PROGRAM.replace('print("searched")', 'let checkpoint_read = tool("quota_status", {"route": "EVN"})\n'
                                                   'let last_read = tool("quota_status", {"route": "RIX"})\n'
                                                   'print("searched")')
CRASH = pytest.mark.parametrize("crash", [True, False], ids=["killed-after-the-segment", "completed"])


def _world(root, crash):
    return MemoryWorld(root, travel.tools(slow_quota=("RIX",) if crash else ()),
                       provenance=travel.independent_provenance(),
                       parameters={"n_medium_windows": 100, "n_low_windows": 100})


def _tail(world, crash, run_id, route):
    """The task that leaves its segment and reads on; with ``crash`` it is killed at the last read and resumed."""
    if not crash:
        world.run(TAIL, run_id, travel.inputs(run_id, route))
        return
    reports = len(world.reports())
    process = world.start(TAIL, run_id, travel.inputs(run_id, route))
    deadline = time.monotonic() + 120
    while {"route": "RIX"} not in world.calls("quota_status"):
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the session did not reach its crash point"
        time.sleep(0.2)
    os.killpg(process.pid, signal.SIGKILL)
    process.wait(60)
    assert len(world.reports()) == reports
    assert any(event.get("type") == "context_exited" for event in world.history(run_id))
    code, payload, stderr = world.resume(run_id)
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)


@CRASH
def test_a_segment_completed_before_a_crash_teaches_once(tmp_path, crash):
    world = _world(tmp_path, crash)
    for index, route in enumerate(["BUS", "YVR"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    before = len(world.reports())
    _tail(world, crash, "third", "MSQ")

    reports = world.reports()[before:]
    assert [report["mode"] for report in reports] == (["emergency", "full"] if crash else ["full"])
    if crash:
        emergency = reports[0]
        # It saw the completed segment and applied nothing.
        assert "confirmed" in [item["verdict"] for item in emergency["marker_verdicts"]]
        assert emergency["births"] == [] and emergency["trust_decisions"] == [] and emergency["pool_updates"] == []
        assert emergency["apply"]["cursors"] == {} and emergency["apply"]["quanta"] == {}
    birth, = reports[-1]["births"]
    assert sorted(item["run_id"] for item in birth["episodes"]) == ["learn-0", "learn-1", "third"]
    assert birth["criteria"]["episodes"] == 3
    third, = [entry for report in world.reports() for entry in report["window"]["sessions"]
              if entry["run_id"] == "third"]
    assert third["from"] == 0
    # Nothing was repeated for it; only the interrupted idempotent read was answered again.
    assert world.calls("flights").count({"route": "MSQ"}) == 2
    assert world.calls("quota_status").count({"route": "RIX"}) == (2 if crash else 1)
    if crash:
        journal = world.journal()
        world.resume("third")
        assert world.journal() == journal


@CRASH
def test_a_fire_before_a_crash_counts_once(tmp_path, crash):
    world = _world(tmp_path, crash)
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = [birth for report in world.reports() for birth in report["births"]]
    _tail(world, crash, "fired", "TBS")

    fired, = world.events("fired", "habit_activated")
    assert fired["habit_id"] == birth["habit_id"] and fired["outcome"] == "success"
    habit = world.owner().state()["habits"][birth["habit_id"]]
    assert habit["exec_summary"]["fires_total"] == 1
    assert [item["run_id"] for item in habit["recent"]] == ["fired"]
    counted = [event for report in world.reports() for item in report["trust_decisions"]
               if item["habit_id"] == birth["habit_id"] for event in item["events"]]
    assert [item["run_id"] for item in counted + habit["pending"]] == ["fired"]
