"""A composition answers for its parts (refinement §14).

* With automation off (exam mode C on the composition's snapshot) the learned
  procedures stay material for slow planning: the planner recovers the
  deployment from them on the slow path, and no fast path runs.
* When a part is withdrawn in Gold, the composition does not stay
  unconditionally active: at run time the withdrawn part is not called, the
  planner finds no other part, and the court reviews the composite — probation,
  with the part and the reason.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.learned_habit_lifecycle import withdraw_learned_habit


def _composite(world):
    drain = jobs.learn(world)
    cancel = jobs.learn_cancel(world, "pause")
    for index, service in enumerate(("svc-busy-1", "svc-busy-2", "svc-busy-3")):
        world.run(jobs.COMPOSE, f"composed-{index}", jobs.compose_inputs(f"composed-{index}", service))
    composite, = world.reports()[-1]["births"]
    return drain, cancel, composite


def test_parts_stay_slow_material_and_a_withdrawn_part_retracts_the_composition(tmp_path):
    world = jobs.world(tmp_path)
    _, cancel, composite = _composite(world)
    snapshot = world.reports()[-1]["snapshot_boundary_after"]

    # Automation off: every learned procedure slow-only, yet the planner composes them on the slow path.
    world.run(jobs.COMPOSE, "exam-C", jobs.compose_inputs("exam-C", "svc-busy-4"), exam=("C", snapshot))
    assert world.events("exam-C", "habit_activated") == []
    assert all(item["slow_only"] for item in world.opening("exam-C")["learned"])
    executed, = world.events("exam-C", "composition_executed")
    assert executed["recovered"] is True and executed["used"] == composite["habit_id"]
    assert [(part["habit_id"], part["recovered"]) for part in executed["parts"]] == [(cancel["habit_id"], True)]

    # The part is withdrawn: the composite's join is refused at run time, and nothing else joins.
    withdraw_learned_habit(open_gold_project(world.state), cancel["gates"]["publication"],
                           operator=ActorIdentity("acceptance.operator"), reason="acceptance withdrawal")
    world.run(jobs.COMPOSE, "withdrawn", jobs.compose_inputs("withdrawn", "svc-busy-5"))
    loaded = {item["habit_id"] for item in world.opening("withdrawn")["learned"]}
    assert composite["habit_id"] in loaded and cancel["habit_id"] not in loaded
    activated, = world.events("withdrawn", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "failure"
    assert activated["detail"]["contingency"] == {"reason": "component_unavailable", "part": cancel["habit_id"]}
    executed, = world.events("withdrawn", "composition_executed")
    assert executed["recovered"] is False
    job, = [job for job, item in jobs.jobs(world).items() if item["service"] == "svc-busy-5"]
    assert jobs.jobs(world)[job]["state"] == "running"

    # The court reviews the composite: probation, naming the part and why.
    report = world.reports()[-1]
    review, = report["composition_reviews"]
    assert review == {"habit_id": composite["habit_id"], "part": cancel["habit_id"], "at": 2, "reason": "not_admitted"}
    move, = [item for item in report["transitions"] if item["habit_id"] == composite["habit_id"]]
    assert (move["from"], move["to"], move["rule"]) == ("born", "probation", "TC")
