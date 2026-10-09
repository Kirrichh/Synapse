"""Learning after a forget: a forgotten example leaves what it was part of, and fresh examples learn (findings R6
and R7 on 556624d).

A flight search refused while busy is recovered on the slow path; three verified recoveries in two or more tasks
teach the procedure (``_travel``).

* R6 — two recoveries (BUS, YVR) make a candidate, and the operator forgets the BUS case: its recorded results leave
  store D and the example leaves the candidate. BUS searched again in another task gives the same answers — a new
  observation now, an example of its own — and still two examples stand: nothing is born. MSQ adds a third and the
  procedure is born from YVR, BUS searched again and MSQ; the forgotten case stays forgotten, the habit acts in the
  next task, a further consolidation changes nothing and the state restored from the journal is the fold of its
  reports. Without the forget (control) BUS searched again only copies recorded answers, and the procedure is born
  from BUS, YVR and MSQ.
* R7 — a learned procedure loses one of its three basis cases to a forget and the court archives it (TR). Two
  matching events of one task then reach it: demand is no proof of the basis it lost, so it is not woken
  (``basis_not_retained``) and nothing acts in the next task. Its candidate learns the procedure again from fresh
  examples only: with that next task it is born as a habit of its own, on fresh cases alone, and acts; the archived
  one stays archived with the basis it was born from. A habit archived for disuse (control) still wakes on demand
  and acts.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld
from synapse.memory_consolidation.court.projection import fold

MAINTENANCE = '''memory palace "clerk" { rooms { episodic procedural } consolidate during dream }
consolidate palace
print("maintained")'''
_SEARCH = travel.PROGRAM[travel.PROGRAM.index('context "search"'):travel.PROGRAM.index('print("searched")')]
#: Two searches in one task: two matching events of one task.
TWO = travel.PROGRAM.replace('print("searched")',
                             _SEARCH.replace('{"route": route}', '{"route": second}') + 'print("searched")')


def _world(root, *, busy=(), **parameters):
    return MemoryWorld(root, travel.tools(busy_every_time=busy), provenance=travel.independent_provenance(),
                       parameters={"n_medium_windows": 100, "n_low_windows": 100, **parameters})


def _forget(world, qid):
    code, payload, stderr = world.memory("forget", "--quantum", qid, "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)


def _births(world):
    return [birth for report in world.reports() for birth in report["births"]]


def _learn(world, *routes):
    for index, route in enumerate(routes):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"learn-{index}", route))
    birth, = _births(world)
    return birth, world.owner().state()["frozen"][birth["habit_id"]]["habit"]["born_from"]["episodes"]


@pytest.mark.parametrize("forget", [True, False], ids=["forgotten", "kept"])
def test_a_forgotten_example_leaves_its_candidate(tmp_path, forget):
    world = _world(tmp_path, busy=("BUS",))
    for run_id, route in (("bus", "BUS"), ("yvr", "YVR")):
        world.run(travel.PROGRAM, run_id, travel.inputs(run_id, route))
    candidate, = world.owner().state()["pool"].values()
    bus, = [item for item in candidate["episodes"] if item["run_id"] == "bus"]
    if forget:
        _forget(world, bus["qid"])
        assert all(world.evidence().get(ref) is None for ref in bus["evidence"])

    world.run(travel.PROGRAM, "bus-again", travel.inputs("bus-again", "BUS"))
    assert _births(world) == []  # Two examples stand: a forgotten one is none, and a copy counts once.
    episodes = world.owner().state()["pool"][candidate["candidate_key"]]["episodes"]
    again, = [item for item in episodes if item["run_id"] == "bus-again"]
    assert again["evidence"] == bus["evidence"] and again["copy"] is not forget
    assert ("bus" in {item["run_id"] for item in episodes}) is not forget

    world.run(travel.PROGRAM, "msq", travel.inputs("msq", "MSQ"))
    birth, = _births(world)
    assert {item["run_id"] for item in birth["episodes"]} == ({"yvr", "bus-again", "msq"} if forget
                                                               else {"bus", "yvr", "msq"})
    state = world.owner().state()
    basis = state["frozen"][birth["habit_id"]]["habit"]["born_from"]["episodes"]
    assert (bus["qid"] in basis) is not forget
    assert state["quanta"][bus["qid"]]["retention_state"] == ("forgotten" if forget else "full")
    assert all(state["quanta"][qid]["retention_state"] == "full" and all(
        world.evidence().get(ref) is not None for ref in state["quanta"][qid]["evidence_refs"]) for qid in basis)

    world.run(travel.PROGRAM, "next", travel.inputs("next", "TBS"))
    fired, = world.events("next", "habit_activated")
    assert fired["habit_id"] == birth["habit_id"] and fired["outcome"] == "success"
    world.run(MAINTENANCE, "maintenance", {})
    after = world.owner().state()
    assert _births(world) == [birth]
    assert after["frozen"][birth["habit_id"]] == state["frozen"][birth["habit_id"]]
    assert after["quanta"][bus["qid"]]["retention_state"] == ("forgotten" if forget else "full")
    assert after == fold(item["report"] for item in world.owner().applied() if item["report"] is not None)


def test_demand_never_wakes_a_habit_whose_basis_was_forgotten(tmp_path):
    world = _world(tmp_path)
    old, basis = _learn(world, "BUS", "YVR", "MSQ")
    _forget(world, basis[0])
    world.run(MAINTENANCE, "maintenance", {})
    assert [(item["habit_id"], item["rule"], item["to"]) for item in world.reports()[-1]["transitions"]] == [
        (old["habit_id"], "TR", "dormant")]

    # Two matching events of one task reach it: demand, never a proof of the basis it lost.
    world.run(TWO, "demand", {**travel.inputs("demand", "TBS"), "second": "EVN"})
    report = world.reports()[-1]
    check, = report["cold_checks"]["dormant_matches"]
    assert (check["habit_id"], check["refused"]) == (old["habit_id"], "basis_not_retained")
    assert [item["run_id"] for item in check["matches"]] == ["demand", "demand"]
    assert [item for item in report["transitions"] if item["habit_id"] == old["habit_id"]] == []
    assert report["births"] == []
    world.run(travel.PROGRAM, "next", travel.inputs("next", "RIX"))
    assert world.events("next", "habit_activated") == []

    # Fresh examples of a second task: the procedure is learned again, as a habit of its own on fresh cases alone.
    new, = world.reports()[-1]["births"]
    assert new["habit_id"] != old["habit_id"] and new["typed_check"] == "archived_unfounded"
    assert sorted(item["run_id"] for item in new["episodes"]) == ["demand", "demand", "next"]
    state = world.owner().state()
    assert not set(state["frozen"][new["habit_id"]]["habit"]["born_from"]["episodes"]) & set(basis)
    assert state["habits"][old["habit_id"]]["state"] == "dormant"
    assert state["frozen"][old["habit_id"]]["habit"]["born_from"]["episodes"] == basis
    world.run(travel.PROGRAM, "after", travel.inputs("after", "VNO"))
    fired, = world.events("after", "habit_activated")
    assert fired["habit_id"] == new["habit_id"] and fired["outcome"] == "success"


def test_a_habit_archived_for_disuse_still_wakes_on_demand(tmp_path):
    world = _world(tmp_path, m_idle=1)
    habit, _ = _learn(world, "BUS", "YVR", "MSQ")
    for run_id, route in (("unused-0", "YVR"), ("unused-1", "MSQ")):  # Searched before: no refusal, no fire.
        world.run(travel.PROGRAM, run_id, travel.inputs(run_id, route))
    assert world.owner().state()["habits"][habit["habit_id"]]["state"] == "dormant"
    world.run(TWO, "demand", {**travel.inputs("demand", "TBS"), "second": "EVN"})
    report = world.reports()[-1]
    assert [(item["rule"], item["to"]) for item in report["transitions"] if item["habit_id"] == habit["habit_id"]] == [
        ("T6", "probation")]
    assert report["births"] == []  # The events that woke it feed no candidate.
    candidate, = world.owner().state()["pool"].values()  # Its basis is retained: its candidate stays born.
    assert (candidate["status"], candidate["born_habit"]) == ("born", habit["habit_id"])
    world.run(travel.PROGRAM, "next", travel.inputs("next", "RIX"))
    fired, = world.events("next", "habit_activated")
    assert fired["habit_id"] == habit["habit_id"]
