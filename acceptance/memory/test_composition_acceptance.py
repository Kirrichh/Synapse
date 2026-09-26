"""A new procedure composed from two verified parts (refinement §14).

Memory holds two learned procedures, each verified in its own task family:
the drain of a refused deployment (create a drain job, read it, cancel it,
repeat the deployment, confirm the release) and the recovery of a refused
cancel of a running job (pause it, repeat the cancel, confirm it through an
audit). No procedure for a deployment whose drain job starts running exists.

On a busy service the drain's fast path stops at the refused cancel. The
program's slow path only asks the memory to recover the failure; the slow
planner continues the stopped drain from its recorded answers, joins the
cancel recovery at the refused step (its trigger applies to that failure, its
inputs bind, it ends by repeating the cancel, nothing conflicts) and resumes
the drain. The environment is the checker's truth: the created job is
revoked, the deployment done, no job created twice, and the repeated cancel
is a declared retry of the same operation.

Three such composed recoveries in three tasks are verified as a whole; the
composition is born, supersedes the drain, and then runs on the fast path —
calling the cancel recovery only where the cancel is refused.
"""
from __future__ import annotations

import json

from acceptance.memory import _jobs as jobs

BUSY = ("svc-busy-1", "svc-busy-2", "svc-busy-3")


def _journal(world):
    return [json.loads(line) for line in (world.owner().gateway_root / "journal.jsonl").read_text().splitlines()]


def _created(world, service):
    return [job for job, item in jobs.jobs(world).items() if item["service"] == service]


def test_two_verified_parts_compose_a_procedure_neither_knows(tmp_path):
    world = jobs.world(tmp_path)
    drain = jobs.learn(world)
    cancel = jobs.learn_cancel(world, "pause")
    assert drain["composition"] is None and cancel["composition"] is None

    # No procedure recovers a deployment whose drain job runs: the drain stops at the refused cancel.
    for index, service in enumerate(BUSY):
        run_id = f"composed-{index}"
        world.run(jobs.COMPOSE, run_id, jobs.compose_inputs(run_id, service))
        activated, = world.events(run_id, "habit_activated")
        assert activated["habit_id"] == drain["habit_id"] and activated["outcome"] == "failure"
        assert activated["detail"]["reason"] == "diverged_from_basis" and activated["detail"]["observed"] == "JOB_RUNNING"
        planned, = world.events(run_id, "composition_planned")
        assert planned["join"] == {"at": 2, "on": "JOB_RUNNING", "part": cancel["habit_id"]}
        executed, = world.events(run_id, "composition_executed")
        assert executed["recovered"] is True and executed["base"] == drain["habit_id"] and executed["prefix"] == 3
        assert [(part["habit_id"], part["recovered"]) for part in executed["parts"]] == [(cancel["habit_id"], True)]
        # The environment: the one job created for the service was paused and revoked; the deployment happened.
        job, = _created(world, service)
        assert jobs.jobs(world)[job]["state"] == "revoked"
        assert {"service": service} in [item["args"] for item in world.world()["effects"] if item["effect"] == "deployed"]
        # The repeated cancel is a declared retry of the refused operation, admitted by the gateway.
        cancels = [record for record in _journal(world) if record["kind"] == "STARTED"
                   and record["body"]["tool"] == "jobs_cancel" and record["body"]["run_id"] == run_id]
        assert len(cancels) == 2 and cancels[1]["body"]["retry_of"] == cancels[0]["body"]["op_seq"]
        assert cancels[1]["body"]["op_seq"] == cancels[0]["body"]["op_seq"] and cancels[1]["body"]["admitted"]

    # Verified as a whole in three tasks, the composition is born and replaces the drain.
    report = world.reports()[-1]
    composite, = report["births"]
    assert composite["composition"]["base"] == drain["habit_id"]
    assert composite["composition"]["joins"] == [{"at": 2, "on": "JOB_RUNNING", "part": cancel["habit_id"]}]
    assert composite["criteria"]["episodes"] == 3 and composite["criteria"]["tasks"] == 3
    assert composite["criteria"]["parts"] == [cancel["habit_id"]]
    supersession, = report["supersessions"]
    assert supersession == {**supersession, "predecessor": drain["habit_id"], "successor": composite["habit_id"],
                            "kind": "composition"}
    assert report["legitimacy"][drain["habit_id"]]["admitted"] is False
    assert all(item["support"] == "success" and item["verified"] for item in report["compositions"])

    # The composite on the fast path: it calls the cancel recovery exactly where the cancel is refused.
    world.run(jobs.COMPOSE, "fast-busy", jobs.compose_inputs("fast-busy", "svc-busy-4"))
    activated, = world.events("fast-busy", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert world.events("fast-busy", "slow_path_used") == []
    job, = _created(world, "svc-busy-4")
    assert jobs.jobs(world)[job]["state"] == "revoked"
    world.run(jobs.COMPOSE, "fast-plain", jobs.compose_inputs("fast-plain", "svc-plain"))
    activated, = world.events("fast-plain", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert world.calls("jobs_pause")[-1] == {"job_id": job}  # No pause for the plain service's pending job.
    recent = {item["run_id"]: item["outcome"] for item in
              world.owner().state()["habits"][composite["habit_id"]]["recent"]}
    assert recent == {"fast-busy": "success", "fast-plain": "success"}
