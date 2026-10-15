"""A reassessment judges a habit's basis from the owner's own custody, and republishes only what it verified
(findings R9 and R10 on 556624d).

A recovery of a refused flight search is learned (``_travel``); every basis case is held in store D — its raw trace,
its session's replay data and the recorded results.

* R10 — the run artifacts of the learning sessions are moved away; store D is untouched. ``synapse memory
  reassess`` calls no tool: each basis session is re-executed from its replay data and recorded results and carries
  its case's exact events, so all three cases are still verified and the habit keeps its basis — as it does with the
  artifacts in place (control). Replay data that no longer matches its address is no custody: that case is
  unavailable, never read as verified, and the habit, short of a birth's basis, is archived (TR).
* R9 — a habit learned on four cases loses one to a forget; three still suffice. The operator documents the quota
  read (another tool binding) and reassesses: the forgotten case is unavailable and carries no evidence, the three
  others are verified, and Gold admits the habit again with the evidence of those three alone. The configuration
  is adopted and the habit acts under it; reassessing again records nothing more and calls nothing. Without the
  forget (control) all four are verified and republished.
"""
from __future__ import annotations

import json

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

MAINTENANCE = '''memory palace "clerk" { rooms { episodic procedural } consolidate during dream }
consolidate palace
print("maintained")'''


def _world(root):
    return MemoryWorld(root, travel.tools(), provenance=travel.independent_provenance(),
                       parameters={"n_medium_windows": 100, "n_low_windows": 100})


def _learned(world, routes, *, deferred=False):
    """The habit born of ``routes`` and its basis cases; ``deferred`` consolidates them all at the last session."""
    quiet = travel.PROGRAM.replace("  consolidate during dream\n", "")
    for index, route in enumerate(routes):
        last = index == len(routes) - 1
        world.run(travel.PROGRAM if last or not deferred else quiet, f"learn-{index}",
                  travel.inputs(f"task-{index}", route))
    birth, = [birth for report in world.reports() for birth in report["births"]]
    return birth["habit_id"], world.owner().state()["frozen"][birth["habit_id"]]["habit"]["born_from"]["episodes"]


def _reassessed(world, habit_id, configuration=None):
    calls, reports = len(world.world()["calls"]), len(world.reports())
    code, result, stderr = world.memory("reassess", configuration=configuration)
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    assert len(world.world()["calls"]) == calls and len(world.reports()) == reports + 1
    habit, = [item for item in result["consolidation"]["reassessment"]["habits"] if item["habit_id"] == habit_id]
    return habit


@pytest.mark.parametrize("custody", ["artifacts-kept", "artifacts-moved", "replay-data-damaged"])
def test_a_reassessment_reads_the_basis_from_its_own_custody(tmp_path, custody):
    world = _world(tmp_path)
    habit_id, basis = _learned(world, ["BUS", "YVR", "MSQ"])
    state = world.owner().state()
    if custody == "artifacts-moved":
        moved = tmp_path / "moved"
        moved.mkdir()
        for index in range(3):
            (world.runs / f"learn-{index}.json").rename(moved / f"learn-{index}.json")
    damaged = state["quanta"][basis[0]]["replay_ref"]["data_ref"]
    if custody == "replay-data-damaged":
        body = world.evidence().root / f"{damaged}.json"
        body.write_bytes(body.read_bytes() + b" ")  # The same meaning in other bytes: no longer its address.
        assert world.evidence().get(damaged) is None

    habit = _reassessed(world, habit_id)
    episodes = {item["qid"]: item for item in habit["episodes"]}
    after = world.owner().state()["habits"][habit_id]["state"]
    if custody == "replay-data-damaged":
        assert (episodes[basis[0]]["now"], episodes[basis[0]]["reason"]) == ("unavailable", "trace_unavailable")
        assert episodes[basis[0]]["evidence"] == []
        assert (habit["verified"], habit["verdict"]) == (2, "basis_no_longer_verified") and after == "dormant"
    else:
        assert [episodes[qid]["now"] for qid in basis] == ["verified"] * 3
        assert (habit["verified"], habit["required"], habit["verdict"]) == (3, 3, "basis_holds")
        assert after == state["habits"][habit_id]["state"]


@pytest.mark.parametrize("forget", [True, False], ids=["one-forgotten", "all-kept"])
def test_a_reassessment_after_a_partial_forget_republishes_what_it_verified(tmp_path, forget):
    world = _world(tmp_path)
    habit_id, basis = _learned(world, ["BUS", "YVR", "MSQ", "TBS"], deferred=True)
    assert len(basis) == 4
    if forget:
        code, payload, stderr = world.memory("forget", "--quantum", basis[0], "--reason", "data subject request",
                                             "--operator", "acceptance.operator")
        assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
        world.run(MAINTENANCE, "maintenance", {})
    state = world.owner().state()
    assert state["habits"][habit_id]["state"] in {"born", "active", "probation"}
    configuration = json.loads(world.configuration_path.read_text())
    quota, = [item for item in configuration["tools"]["tools"] if item["name"] == "quota_status"]
    documented = world.reconfigured("documented", {"quota_status": {**quota["contract"], "doc": "a read of the quota"}})

    habit = _reassessed(world, habit_id, documented)
    episodes = {item["qid"]: item for item in habit["episodes"]}
    kept = basis[1:] if forget else basis
    assert all(episodes[qid]["now"] == "verified" and episodes[qid]["evidence"] for qid in kept)
    if forget:
        assert (episodes[basis[0]]["now"], episodes[basis[0]]["evidence"]) == ("unavailable", [])
    assert (habit["verified"], habit["required"], habit["verdict"]) == (len(kept), 3, "basis_holds")
    assert habit["republication"]["admitted"] is True and habit["republication"]["reason"] is None
    publication = world.owner().state()["habits"][habit_id]["publication"]
    assert publication == habit["republication"]["publication"]
    assert publication["tool_binding_sha256"] != state["habits"][habit_id]["publication"]["tool_binding_sha256"]

    # Adopted: the habit acts under the documented configuration; reassessing again records and calls nothing.
    code, payload, stderr = world.attempt(travel.PROGRAM, "after", travel.inputs("after", "EVN"),
                                          configuration=documented)
    assert code == 0 and payload["status"] == "COMPLETED", (payload, stderr)
    fired, = world.events("after", "habit_activated")
    assert fired["habit_id"] == habit_id and fired["outcome"] == "success"
    calls, reports = len(world.world()["calls"]), len(world.reports())
    code, result, stderr = world.memory("reassess", configuration=documented)
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    assert len(world.world()["calls"]) == calls and len(world.reports()) == reports
