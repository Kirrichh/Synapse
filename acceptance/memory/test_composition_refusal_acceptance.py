"""What a composition must not claim (refinement §14).

* A part whose conditions do not hold for the failure is not joined: the
  cancel recovery was learned on jobs of the stream queue, and a busy batch
  service's running job is on another queue. The planner records why, and
  nothing reaches the job.
* A part that recovered its step while the goal still failed is a partial
  success: counted for the part, never as the composition's success.
* A chain the driver spells out step by step is learned as an ordinary
  procedure: no composition is credited for it.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs


def _created(world, service):
    return [job for job, item in jobs.jobs(world).items() if item["service"] == service]


def test_incompatible_parts_do_not_join_and_partial_success_is_not_the_goal(tmp_path):
    world = jobs.world(tmp_path / "composed")
    drain = jobs.learn(world)
    cancel = jobs.learn_cancel(world, "pause")

    # Another queue: the part's conditions do not hold for this failure; nothing is joined or called.
    pauses = len(world.calls("jobs_pause"))
    world.run(jobs.COMPOSE, "batch", jobs.compose_inputs("batch", "svc-batch"))
    planned, = world.events("batch", "composition_planned")
    assert planned["join"] is None and planned["failure"]["fields"]["queue"] == "batch"
    assert planned["considered"] == [{"part": cancel["habit_id"], "joined": False,
                                      "reasons": ["conditions_not_met: queue"]}]
    executed, = world.events("batch", "composition_executed")
    assert executed["recovered"] is False and executed["parts"] == [] and executed["joins"] == []
    job, = _created(world, "svc-batch")
    assert jobs.jobs(world)[job]["state"] == "running" and len(world.calls("jobs_pause")) == pauses
    assert world.reports()[-1]["compositions"][0]["support"] == "failure"

    # The part recovers its step; the deployment is still refused: partial, not the goal.
    world.run(jobs.COMPOSE, "stuck", jobs.compose_inputs("stuck", "svc-stuck"))
    executed, = world.events("stuck", "composition_executed")
    assert executed["recovered"] is False and executed["outcome"] == "failure"
    assert [(part["habit_id"], part["recovered"]) for part in executed["parts"]] == [(cancel["habit_id"], True)]
    job, = _created(world, "svc-stuck")
    assert jobs.jobs(world)[job]["state"] == "revoked"  # The part did its work in the environment.
    entry, = world.reports()[-1]["compositions"]
    assert entry["support"] == "partial" and entry["goal"] == "failure"
    assert entry["parts"] == [{"habit_id": cancel["habit_id"], "at": 2, "outcome": "success", "recovered": True}]
    update = next(item for item in world.reports()[-1]["pool_updates"] if item["candidate_key"] == entry["candidate_key"])
    assert update["criteria"]["partial"] == 1 and update["criteria"]["episodes"] == 0
    assert drain["habit_id"] in world.owner().state()["frozen"]


def test_a_chain_the_driver_spells_out_is_not_a_composition(tmp_path):
    world = jobs.world(tmp_path / "spelled")
    for index in (5, 6, 7):
        run_id = f"spelled-{index}"
        world.run(jobs.SPELLED, run_id, jobs.compose_inputs(run_id, f"svc-busy-{index}"))
        job, = _created(world, f"svc-busy-{index}")
        assert jobs.jobs(world)[job]["state"] == "revoked"
    reports = world.reports()
    assert all(report["compositions"] == [] for report in reports)
    assert all(world.events(f"spelled-{index}", "composition_planned") == [] for index in (5, 6, 7))
    birth, = reports[-1]["births"]
    assert birth["composition"] is None and birth["typed_check"] != "composition"
