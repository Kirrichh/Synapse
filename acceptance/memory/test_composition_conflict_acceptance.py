"""A part whose effect undoes the procedure's own is never joined (refinement §14).

The refused cancel of a running job can also be recovered by killing the job
— verified in its own task family, where nothing depends on the job. Inside
the drain it would undo the drain job the procedure just created: the kill's
contract says it compensates the creation. The planner refuses the join for
that conflict, before any effect; the running drain job is left as it was.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs


def test_a_part_that_compensates_the_procedures_effect_is_refused(tmp_path):
    world = jobs.world(tmp_path)
    drain = jobs.learn(world)
    kill = jobs.learn_cancel(world, "kill")
    assert kill["composition"] is None

    kills = len(world.calls("jobs_kill"))
    world.run(jobs.COMPOSE, "busy", jobs.compose_inputs("busy", "svc-busy-1"))
    activated, = world.events("busy", "habit_activated")
    assert activated["habit_id"] == drain["habit_id"] and activated["detail"]["observed"] == "JOB_RUNNING"
    planned, = world.events("busy", "composition_planned")
    assert planned["join"] is None
    assert planned["considered"] == [{"part": kill["habit_id"], "joined": False, "reasons": [
        "effect_conflict: jobs_kill compensates jobs_create, which the procedure already did"]}]
    executed, = world.events("busy", "composition_executed")
    assert executed["recovered"] is False and executed["parts"] == []
    job, = [job for job, item in jobs.jobs(world).items() if item["service"] == "svc-busy-1"]
    assert jobs.jobs(world)[job]["state"] == "running" and len(world.calls("jobs_kill")) == kills
