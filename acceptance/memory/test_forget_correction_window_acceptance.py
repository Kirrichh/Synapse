"""A forget and a correction of knowledge consolidate in one window (finding R5 on 556624d).

Memory holds the basic plan's price, 20, read from the catalog, and a learned recovery of a refused flight search on
three recorded cases. The operator forgets one of those cases; the catalog then says 30 and a session reads it. That
one consolidation applies both: the forgotten case names no run any more — its trace left with its tombstone — and
the court decides without it: 30 corrects 20, the correction touches no habit, and the habit, two cases short of a
birth's basis, is archived (TR). Applying the forget in a window of its own first (control) ends in the same memory;
without a forget (control) the correction leaves the habit as it was.
"""
from __future__ import annotations

import pytest

from acceptance.memory import _catalog as catalog
from acceptance.memory import _travel as travel
from acceptance.memory._world import MemoryWorld

MAINTENANCE = '''memory palace "clerk" { rooms { episodic procedural } consolidate during dream }
consolidate palace
print("maintained")'''


def _world(root):
    return MemoryWorld(root, [*travel.tools(), *catalog.tools()],
                       provenance={**travel.independent_provenance(), **catalog.provenance()},
                       parameters={"n_medium_windows": 100, "n_low_windows": 100},
                       knowledge={"embedder": {"tool": "embed", "version": "concepts-v1"},
                                  "properties": catalog.PROPERTIES, "budget": 3})


def _price(world, value):
    catalog.publish(world, "catalog", "basic", "monthly_price", value, text=f"basic monthly price {value}",
                    start="2026-01-01")
    return catalog.learn(world, f"price-{value}", "basic")


@pytest.mark.parametrize("mode", ["same-window", "forget-first", "no-forget"])
def test_a_forget_and_a_correction_consolidate_in_one_window(tmp_path, mode):
    world = _world(tmp_path)
    _price(world, 20)
    for index, route in enumerate(["BUS", "YVR", "MSQ"]):
        world.run(travel.PROGRAM, f"learn-{index}", travel.inputs(f"task-{index}", route))
    birth, = [birth for report in world.reports() for birth in report["births"]]
    basis = world.owner().state()["frozen"][birth["habit_id"]]["habit"]["born_from"]["episodes"]
    if mode != "no-forget":
        code, payload, stderr = world.memory("forget", "--quantum", basis[0], "--reason", "data subject request",
                                             "--operator", "acceptance.operator")
        assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    if mode == "forget-first":
        world.run(MAINTENANCE, "maintenance", {})

    report = _price(world, 30)  # The session completes and its consolidation is applied.
    assert len(report["knowledge"]["corrections"]) == 1
    assert [(item["habits"], item["hypotheses"]) for item in report["knowledge"]["revisions"]] == [([], [])]
    state = world.owner().state()
    held = [version["record"]["value"] for version in state["knowledge"]["versions"].values()
            if version["known_until"] is None and version.get("withdrawn") is None]
    assert held == [30]
    archived = [(item["habit_id"], item["rule"], item["to"]) for item in report["transitions"]]
    if mode == "no-forget":
        assert archived == [] and state["habits"][birth["habit_id"]]["state"] == "born"
        return
    assert archived == ([(birth["habit_id"], "TR", "dormant")] if mode == "same-window" else [])
    assert state["habits"][birth["habit_id"]]["state"] == "dormant"
    assert state["quanta"][basis[0]]["retention_state"] == "forgotten"
    assert state["frozen"][birth["habit_id"]]["habit"]["born_from"]["episodes"] == basis
