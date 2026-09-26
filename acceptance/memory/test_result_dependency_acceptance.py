"""A learned procedure passes a result from one step to the next (refinement §13, D2).

Three verified drains in three tasks teach the court the recovery of a refused
deployment: create a drain job, read its status, cancel the job, repeat the
deployment, confirm the release. The job identifier is new and unpredictable
every time, so the court can explain the status and the cancel argument only
as the identifier the creation answered — not the status answer that echoes
it, which the agent already knew — and the birth binds both to step 0.

In a new session another team's pending drain job for the same service
already exists. The learned procedure runs on the fast path: it cancels
exactly the job it created, the other job stays pending, the deployment
succeeds. The environment is the checker's truth. Resuming the completed
session and the court's verified re-execution read the recorded identifier and
create no new job.
"""
from __future__ import annotations

from acceptance.memory import _jobs as jobs


def test_the_consumer_uses_the_producers_answer_and_touches_nothing_else(tmp_path):
    world = jobs.world(tmp_path)
    birth = jobs.learn(world)

    # The birth binds the status and the cancel to the created identifier (step 0), not to the echo (step 1).
    assert birth["criteria"]["dependencies"] == 2
    binding = {item["step"]: item.get("args") for item in birth["binding"]}
    reference = {"result": {"step": 0, "path": ["job_id"], "kind": "string"}}
    assert binding[1] == {"job_id": reference} and binding[2] == {"job_id": reference}
    assert binding[0] == {"kind": {"const": "drain"}, "service": {"failed_arg": "service"}}
    learned = {item["job_id"] for item in world.calls("jobs_cancel")}
    assert len(learned) == 3 and all(jobs.jobs(world)[job]["state"] == "revoked" for job in learned)

    # Another team's pending drain job for the service exists before the session.
    jobs.seed_job(world, "job-theirs", service="svc-new", kind="drain", state="pending")
    creations = len(world.calls("jobs_create"))
    world.run(jobs.program(), "fast-new", jobs.inputs("fast-new", "svc-new"))
    activated, = world.events("fast-new", "habit_activated")
    assert activated["habit_id"] == birth["habit_id"] and activated["outcome"] == "success"
    assert activated["detail"] is None and world.events("fast-new", "slow_path_used") == []
    created, = [job for job, item in jobs.jobs(world).items() if item["service"] == "svc-new" and job != "job-theirs"]
    assert world.calls("jobs_status")[-1] == {"job_id": created} == world.calls("jobs_cancel")[-1]
    assert jobs.jobs(world)[created]["state"] == "revoked" and jobs.jobs(world)[created]["consumed"] is True
    assert jobs.jobs(world)["job-theirs"]["state"] == "pending"
    assert [item["args"] for item in world.world()["effects"] if item["effect"] == "deployed"][-1] == {
        "service": "svc-new"}
    assert len(world.calls("jobs_create")) == creations + 1

    # The court re-derived the fast path from the journal: verified re-execution, a counted success.
    assert world.reports()[-1]["replay"]["fast-new"]["status"] == "replay_verified"
    recent, = [item for item in world.owner().state()["habits"][birth["habit_id"]]["recent"]
               if item["run_id"] == "fast-new"]
    assert recent["outcome"] == "success" and recent["segment_verdict"] == "confirmed"

    # Re-entering the completed session reads the recorded identifier: no new job, no new effect.
    environment, journal = world.world(), world.journal()
    code, _, _ = world.resume("fast-new")
    assert world.world() == environment and world.journal() == journal, code
