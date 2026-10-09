"""The runtime acts on the court's latest resolution of a pair (finding R12 on 556624d).

Two learned recoveries of a refused search compete on one applicability (``_competitors``): one checks the quota,
the other reserves capacity. On full routes only the capacity recovery works, and the verified comparison selects it
(step 2): the quota habit yields to it. Then the capacity habit fails on a new route — no slot can be held there, the
reservation is refused without effect — and, under a declared policy that learns from every counted fire, its trust
falls below the quota habit's by more than the conflict gap: the court selects the quota habit by trust (step 1). The
earlier yielding no longer stands: the next session's boundary and its runtime name the one winner the court named,
and the quota habit acts. Without the failure (control) the verified comparison stands and the capacity habit acts.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _competitors as competitors
from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld, answer

#: A session whose own slow path recovers nothing: the learned habit's fire alone decides the refused search.
STOP = travel.PROGRAM[:travel.PROGRAM.index("    let quota =")] + '''    print("deferred")
  }
}
print("done")
'''
POLICY = {"counterfactual_min_pairs": 1, "min_evidence": 1, "lr": {"born": 0.8, "active": 0.8, "probation": 0.8},
          "m_idle": 100, "n_t9": 100, "n_medium_windows": 100, "n_low_windows": 100}


def _world(root):
    tools = competitors.tools(delay=20)
    slot = next(item for item in tools if item["name"] == "reserve_slot")
    slot["answers"] = [answer({"ok": False, "err": "NO_CAPACITY"}, when={"route": "VNO"}), *slot["answers"]]
    slot["contract"] = {"effect_on_err": {"NO_CAPACITY": "none"}}
    return MemoryWorld(root, tools, provenance=competitors.provenance(), parameters=POLICY)


@pytest.mark.parametrize("fails", [True, False], ids=["winner-fails", "winner-stands"])
def test_the_runtime_acts_on_the_latest_resolution_of_a_pair(tmp_path, fails):
    world = _world(tmp_path)
    births = competitors.learn_competitors(world)
    ids = {competitors.recovery_of(world, birth["habit_id"]): birth["habit_id"] for birth in births}
    for run_id, route, recovery in (("capacity-full", "OSL", "capacity"), ("quota-full", "ARN", "quota")):
        world.run(competitors.PROGRAM, run_id, competitors.inputs(run_id, route, recovery))
    resolved, = world.reports()[-1]["conflicts"]
    assert resolved["step"] == 2 and resolved["advice"]["comparison"]["winner"] == ids["capacity"]
    assert world.owner().state()["habits"][ids["quota"]]["yields_to"] == [ids["capacity"]]
    winner, loser = ids["capacity"], ids["quota"]
    if fails:
        world.run(STOP, "capacity-fails", travel.inputs("capacity-fails", "VNO"))
        fired, = world.events("capacity-fails", "habit_activated")
        assert (fired["habit_id"], fired["outcome"]) == (ids["capacity"], "failure")
        decided, = world.reports()[-1]["conflicts"]
        assert (decided["step"], decided["resolution"], decided["habits"]["A"]) == (
            1, "A_selected_by_trust_gap", ids["quota"])
        habits = world.owner().state()["habits"]
        assert habits[ids["quota"]]["yields_to"] == [] and habits[ids["capacity"]]["yields_to"] == []
        assert ids["capacity"] not in habits[ids["quota"]].get("resolved_by", {})
        winner, loser = ids["quota"], ids["capacity"]

    world.run(competitors.PROGRAM, "probe", competitors.inputs("probe", "HEL", "quota"))
    learned = {item["habit_id"]: item for item in world.opening("probe")["learned"]}
    assert learned[loser]["yields_to"] == [winner] and learned[winner]["yields_to"] == []
    held, = world.events("probe", "habit_suppressed")
    assert (held["habit_id"], held["reason"], held["competitor"]) == (loser, "yields_to_rival", winner)
    fired, = world.events("probe", "habit_activated")
    assert fired["habit_id"] == winner and fired["outcome"] == "success"
