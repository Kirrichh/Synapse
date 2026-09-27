"""One procedure's outcome is the next one's input (refinement §14).

Memory holds two verified procedures with different goals, each from its own
task family: the drain that lets a refused deployment through, and the schema
migration that lets through a deployment refused for an outdated schema (old
services, never drained). A legacy service needs both, one after the other;
no procedure for it exists.

The drain runs on the fast path and repeats the deployment, which is now
refused for the schema: the drain ends in that new failure. The planner
continues the drain and joins the migration at that repeat — the drain's
output, the typed failure event of its repeat, is the migration's input. The
migration repeats the same operation, which succeeds, and the drain resumes.
The environment is the checker's truth: the drain job revoked, the schema
migrated, the service deployed, and both repeats declare the one refused
operation. Verified three times, the sequence is born and runs on the fast
path.
"""
from __future__ import annotations

import json

from acceptance.memory import _jobs as jobs

LEGACY = ("svc-legacy-1", "svc-legacy-2", "svc-legacy-3")


def _journal(world):
    return [json.loads(line) for line in (world.owner().gateway_root / "journal.jsonl").read_text().splitlines()]


def _effects(world, service):
    job, = [job for job, item in jobs.jobs(world).items() if item["service"] == service]
    return [item["effect"] for item in world.world()["effects"]
            if item["args"].get("job_id") == job or item["args"].get("service") == service]


def test_a_procedure_ending_in_a_new_failure_is_followed_by_the_one_that_recovers_it(tmp_path):
    world = jobs.world(tmp_path)
    # The migration first: the drain, learned last, is fresh when the composed tasks start failing it.
    migrate = jobs.learn_migrate(world)
    drain = jobs.learn(world)
    assert {item["value"] for item in migrate["condition"]["when"] if item["field"] == "op_err"} == {"SCHEMA_OUTDATED"}

    for index, service in enumerate(LEGACY):
        run_id = f"legacy-{index}"
        world.run(jobs.COMPOSE, run_id, jobs.compose_inputs(run_id, service))
        activated, = world.events(run_id, "habit_activated")
        assert activated["habit_id"] == drain["habit_id"] and activated["outcome"] == "failure"
        assert activated["detail"]["observed"] == "SCHEMA_OUTDATED" and activated["detail"]["step"] == 3
        planned, = world.events(run_id, "composition_planned")
        assert planned["join"] == {"at": 3, "on": "SCHEMA_OUTDATED", "part": migrate["habit_id"]}
        assert planned["failure"]["fields"]["op_err"] == "SCHEMA_OUTDATED"
        executed, = world.events(run_id, "composition_executed")
        assert executed["recovered"] is True and executed["prefix"] == 4
        assert [(part["habit_id"], part["recovered"]) for part in executed["parts"]] == [(migrate["habit_id"], True)]
        assert _effects(world, service) == ["job_created", "job_revoked", "schema_migrated", "deployed"]
        deploys = [record["body"] for record in _journal(world) if record["kind"] == "STARTED"
                   and record["body"]["tool"] == "deploy" and record["body"]["run_id"] == run_id]
        assert len(deploys) == 3 and deploys[1]["retry_of"] == deploys[2]["retry_of"] == deploys[0]["op_seq"]

    report = world.reports()[-1]
    composite, = report["births"]
    assert composite["composition"]["joins"] == [{"at": 3, "on": "SCHEMA_OUTDATED", "alternatives": [
        {"part": migrate["habit_id"], "joins": []}]}]
    supersession, = report["supersessions"]
    assert (supersession["predecessor"], supersession["successor"]) == (drain["habit_id"], composite["habit_id"])

    world.run(jobs.COMPOSE, "fast-legacy", jobs.compose_inputs("fast-legacy", "svc-legacy-4"))
    activated, = world.events("fast-legacy", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert world.events("fast-legacy", "slow_path_used") == []
    assert _effects(world, "svc-legacy-4") == ["job_created", "job_revoked", "schema_migrated", "deployed"]
