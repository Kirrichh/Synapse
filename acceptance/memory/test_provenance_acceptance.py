"""Independent provenance of witnesses: near negatives of the birth gate (refinement §7).

Each case trains the same verified recovery in three tasks; only the declared
provenance graph or the observed claim changes. The positive control — an
independent observation of the same flights — births in the learning scenario.
Here no birth may happen, and the pool names why.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

INDEPENDENT = travel.independent_provenance()
CASES = {
    # 1. The booking view is a copy of the flight service's own record.
    "copy_of_one_record": ({**INDEPENDENT, "gds:ops": {"ancestors": ["flights:acct1"]}}, True, "dependent"),
    # 3. The only other outside answer (the quota) attests another claim.
    "other_claim": (INDEPENDENT, False, "not_established"),
    # 4. A mirror and its original share one ancestor.
    "mirror_and_original": ({**INDEPENDENT, "flights:acct1": {"ancestors": ["gds:origin"]},
                             "gds:ops": {"ancestors": ["gds:origin"]}, "gds:origin": {"ancestors": []}},
                            True, "dependent"),
    # 5. A node of the graph is missing: independence is not established.
    "missing_node": ({key: value for key, value in INDEPENDENT.items() if key != "gds:ops"}, True,
                     "not_established"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_dependent_or_unestablished_witnesses_never_birth(tmp_path, case):
    provenance, verify_state, verdict = CASES[case]
    world = MemoryWorld(tmp_path, travel.tools(), provenance=provenance)
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route, verify_state=verify_state))
    reports = world.reports()
    assert [len(report["births"]) for report in reports] == [0, 0, 0]
    update, = reports[-1]["pool_updates"]
    # Every other criterion holds: provenance alone keeps the candidate in the pool.
    assert update["reasons"] == ["sources_dependent" if verdict == "dependent" else "independence_not_established"]
    assert update["criteria"]["tasks"] == 3 and update["criteria"]["all_verifiable"] is True
    assert update["criteria"]["independence"] == verdict
    assert "quota:ops" not in update["independence"]["witnesses"]
    # The environment did recover every search; memory declined to learn from it.
    assert world.calls("flights") == [{"route": route} for route in ("BUS", "BUS", "YVR", "YVR", "MSQ", "MSQ")]
    world.run(travel.PROGRAM, "after", travel.inputs("task-after", "TBS", verify_state=verify_state))
    assert world.opening("after")["learned"] == [] and len(world.events("after", "slow_path_used")) == 1
