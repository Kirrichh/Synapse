"""A part joins inside another part (refinement §14).

Memory holds three verified procedures, each from its own task family: the
drain of a refused deployment, the recovery of a refused cancel by pausing
the running job, and the recovery of a refused pause by unlocking the job. A
locked job refuses the pause; nothing recovers a deployment whose drain job
is running and locked.

On a busy locked service the drain stops at the refused cancel; the planner
joins the pause recovery, whose own pause is refused. Inside that part it
joins the unlock recovery: the job is unlocked, the pause repeated, the cancel
repeated, and the drain resumes. The composition is hierarchical — the unlock
belongs to the pause, and a part never joins inside itself. Verified three
times as a whole, the composite is born with the nested join and runs on the
fast path; when the nested part is withdrawn, the court reviews the composite.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.learned_habit_lifecycle import withdraw_learned_habit

LOCKED = ("svc-locked-1", "svc-locked-2", "svc-locked-3")


def _created(world, service):
    job, = [job for job, item in jobs.jobs(world).items() if item["service"] == service]
    return job


def _effects(world, service):
    """What the environment did for one service: its drain job's life and the deployment."""
    job = _created(world, service)
    return [item["effect"] for item in world.world()["effects"]
            if item["args"].get("job_id") == job or item["args"].get("service") == service]


def test_a_part_recovers_its_own_step_with_a_part_joined_inside_it(tmp_path):
    world = jobs.world(tmp_path)
    # The parts first: the drain, learned last, is fresh when the composed tasks start failing it.
    pause = jobs.learn_cancel(world, "pause")
    unlock = jobs.learn_unlock(world)
    drain = jobs.learn(world)

    for index, service in enumerate(LOCKED):
        run_id = f"locked-{index}"
        world.run(jobs.COMPOSE, run_id, jobs.compose_inputs(run_id, service))
        planned = world.events(run_id, "composition_planned")
        assert [(entry["path"], entry["join"]) for entry in planned] == [
            ([], {"at": 2, "on": "JOB_RUNNING", "part": pause["habit_id"]}),
            ([{"at": 2, "part": pause["habit_id"]}], {"at": 0, "on": "JOB_LOCKED", "part": unlock["habit_id"]})]
        # Inside the pause, neither the drain nor the pause itself is a candidate.
        assert {item["part"] for item in planned[1]["considered"]} == {unlock["habit_id"]}
        executed, = world.events(run_id, "composition_executed")
        assert executed["recovered"] is True
        part, = executed["parts"]
        assert (part["habit_id"], part["recovered"]) == (pause["habit_id"], True)
        assert [(item["habit_id"], item["at"], item["on"], item["recovered"]) for item in part["parts"]] == [
            (unlock["habit_id"], 0, "JOB_LOCKED", True)]
        assert _effects(world, service) == ["job_created", "job_unlocked", "job_paused", "job_revoked", "deployed"]

    report = world.reports()[-1]
    composite, = report["births"]
    assert composite["composition"] == {**composite["composition"], "base": drain["habit_id"], "joins": [
        {"at": 2, "on": "JOB_RUNNING", "alternatives": [{"part": pause["habit_id"], "joins": [
            {"at": 0, "on": "JOB_LOCKED", "alternatives": [{"part": unlock["habit_id"], "joins": []}]}]}]}]}
    assert composite["criteria"]["parts"] == sorted([pause["habit_id"], unlock["habit_id"]])

    world.run(jobs.COMPOSE, "fast-locked", jobs.compose_inputs("fast-locked", "svc-locked-4"))
    activated, = world.events("fast-locked", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert _effects(world, "svc-locked-4") == ["job_created", "job_unlocked", "job_paused", "job_revoked", "deployed"]

    # The nested part is withdrawn: the composite does not stay unconditionally active.
    withdraw_learned_habit(open_gold_project(world.state), unlock["gates"]["publication"],
                           operator=ActorIdentity("acceptance.operator"), reason="acceptance withdrawal")
    world.run(jobs.COMPOSE, "withdrawn", jobs.compose_inputs("withdrawn", "svc-locked-5"))
    activated, = world.events("withdrawn", "habit_activated")
    assert activated["outcome"] == "failure"
    review, = world.reports()[-1]["composition_reviews"]
    assert review == {"habit_id": composite["habit_id"], "part": unlock["habit_id"], "at": 2, "reason": "not_admitted"}
    assert jobs.jobs(world)[_created(world, "svc-locked-5")]["state"] == "locked"
