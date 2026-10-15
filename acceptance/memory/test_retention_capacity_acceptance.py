"""A declared capacity is published with the experience it cost; a habit's basis is never paid (refinement §9).

The owner may hold at most two raw traces. A learned habit is born from three
verified recoveries: its basis stays in custody even though it exceeds the
limit, and the pass publishes that it is over the limit. Later cases are
eligible: retention compacts them early — only after an exact reproduction —
and publishes which detail was lost to the limit.
"""
from __future__ import annotations

from acceptance.memory import _stock as stock
from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld


def test_capacity_compacts_eligible_cases_early_and_never_a_decision_basis(tmp_path):
    world = MemoryWorld(tmp_path, [*travel.tools(), *stock.tools()],
                        provenance={**travel.independent_provenance(), "stock:main": {"ancestors": []},
                                    "log:ops": {"ancestors": []}},
                        parameters={"raw_capacity": 2})
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = world.reports()[-1]["births"]
    basis = {item["qid"] for item in birth["episodes"]}
    evidence = world.evidence()
    capacity = [act for item in world.owner().retention_passes() for act in item["acts"] if act["act"] == "capacity"]
    assert capacity[-1]["held"] == 3 and capacity[-1]["compacted_early"] == [] and capacity[-1]["over_limit"] == 1

    stock.count(world, "count")
    counted = stock.cases(world, "count")
    last = [act for act in world.owner().retention_passes()[-1]["acts"] if act["act"] == "capacity"][-1]
    assert last["limit"] == 2 and last["held"] == 5
    assert sorted(last["compacted_early"]) == sorted([counted["medium"]["qid"], counted["low"]["qid"]])
    assert last["over_limit"] == 1  # Only the habit's basis remains over the limit.
    early = {act["qid"]: act for act in world.owner().retention_passes()[-1]["acts"] if act.get("qid")}
    assert early[counted["medium"]["qid"]]["act"] == "raw_deleted" and early[counted["medium"]["qid"]]["reason"] == \
        "capacity"
    assert evidence.get(counted["medium"]["raw_ref"]) is None

    # The basis of the live habit never left custody.
    state = world.owner().state()
    for qid in basis:
        assert state["quanta"][qid]["retention_state"] == "full" and state["quanta"][qid]["tier"] == "high"
        assert evidence.get(state["quanta"][qid]["raw_ref"]) is not None
