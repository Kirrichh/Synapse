"""A composition tries the admissible alternatives in order (refinement §14).

Memory holds the drain of a refused deployment and two verified recoveries of
a refused cancel: pausing the running job (learned on jobs of the stream
queue) and preempting it (learned on sticky jobs of other queues, so its
trigger does not name the queue). Both apply to a cancel refused on the stream
queue; the pause, the more specific, is tried first.

A sticky job refuses the pause, and the cancel's refusal does not say that
the job is sticky. On a busy sticky service the planner joins the pause, sees
it fail without an effect, and tries the preemption against the same failure:
it recovers the cancel, and the drain resumes. The environment is the
checker's truth: the job was never paused, it was preempted and revoked, the
deployment happened. The refused pause declared the cancel it served, so
once that cancel settled it leaves nothing open (an aborted alternative that
changed nothing). Three such recoveries are verified as a whole; the
composite keeps both alternatives in their order and, on the fast path, calls
the preemption only where the pause did not recover the cancel.
"""
from __future__ import annotations

import json

from acceptance.memory import _jobs as jobs

STICKY = ("svc-sticky-1", "svc-sticky-2", "svc-sticky-3")
PREEMPTS = (("preempt-a", "hold-a", "cleanup"), ("preempt-b", "hold-b", "rotation"),
            ("preempt-c", "hold-c", "retirement"))


def _journal(world):
    return [json.loads(line) for line in (world.owner().gateway_root / "journal.jsonl").read_text().splitlines()]


def _created(world, service):
    job, = [job for job, item in jobs.jobs(world).items() if item["service"] == service]
    return job


def _effects(world, service):
    """What the environment did for one service: its drain job's life and the deployment."""
    job = _created(world, service)
    return [item["effect"] for item in world.world()["effects"]
            if item["args"].get("job_id") == job or item["args"].get("service") == service]


def test_the_next_alternative_recovers_what_the_first_did_not(tmp_path):
    world = jobs.world(tmp_path)
    # The parts first: the drain, learned last, is fresh when the composed tasks start failing it.
    pause = jobs.learn_cancel(world, "pause")
    preempt = jobs.learn_cancel(world, "preempt", PREEMPTS, state="sticky", queues=("bulk", "video", "media"))
    drain = jobs.learn(world)
    fields = {name: {item["field"] for item in birth["condition"]["when"]} for name, birth in
              (("pause", pause), ("preempt", preempt))}
    assert fields["preempt"] < fields["pause"] and "queue" in fields["pause"]

    for index, service in enumerate(STICKY):
        run_id = f"sticky-{index}"
        world.run(jobs.COMPOSE, run_id, jobs.compose_inputs(run_id, service))
        planned = world.events(run_id, "composition_planned")
        # Inside the pause nothing recovers the refused pause; at the drain the preemption is tried next.
        assert [(entry["path"], (entry["join"] or {}).get("part")) for entry in planned] == [
            ([], pause["habit_id"]), ([{"at": 2, "part": pause["habit_id"]}], None), ([], preempt["habit_id"])]
        assert planned[2]["considered"] == [{"part": preempt["habit_id"], "joined": True, "reasons": []}]
        executed, = world.events(run_id, "composition_executed")
        assert executed["recovered"] is True and executed["base"] == drain["habit_id"]
        assert [(part["habit_id"], part["outcome"], part["recovered"], part["applied"])
                for part in executed["parts"]] == [
            (pause["habit_id"], "failure", False, []),
            (preempt["habit_id"], "success", True, ["jobs_preempt", "jobs_cancel", "job_audit"])]
        assert jobs.jobs(world)[_created(world, service)]["state"] == "revoked"
        # The refused pause declared the cancel it served; that cancel settled, so the pause leaves nothing open.
        pauses = [record["body"] for record in _journal(world) if record["kind"] == "STARTED"
                  and record["body"]["tool"] == "jobs_pause" and record["body"]["run_id"] == run_id]
        cancels = [record["body"] for record in _journal(world) if record["kind"] == "STARTED"
                   and record["body"]["tool"] == "jobs_cancel" and record["body"]["run_id"] == run_id]
        assert len(pauses) == 1 and pauses[0]["serves"] == cancels[0]["op_seq"]
        assert _effects(world, service) == ["job_created", "job_preempted", "job_revoked", "deployed"]  # No pause.

    report = world.reports()[-1]
    composite, = report["births"]
    assert composite["composition"]["joins"] == [{"at": 2, "on": "JOB_RUNNING", "alternatives": [
        {"part": pause["habit_id"], "joins": []}, {"part": preempt["habit_id"], "joins": []}]}]
    assert composite["criteria"]["parts"] == sorted([pause["habit_id"], preempt["habit_id"]])
    assert all(item["support"] == "success" and item["verified"] for item in report["compositions"])

    # The composite on the fast path: the preemption only where the pause did not recover the cancel.
    world.run(jobs.COMPOSE, "fast-sticky", jobs.compose_inputs("fast-sticky", "svc-sticky-4"))
    activated, = world.events("fast-sticky", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert _effects(world, "svc-sticky-4") == ["job_created", "job_preempted", "job_revoked", "deployed"]
    preempted = len(world.calls("jobs_preempt"))
    world.run(jobs.COMPOSE, "fast-busy", jobs.compose_inputs("fast-busy", "svc-busy-1"))
    activated, = world.events("fast-busy", "habit_activated")
    assert activated["habit_id"] == composite["habit_id"] and activated["outcome"] == "success"
    assert _effects(world, "svc-busy-1") == ["job_created", "job_paused", "job_revoked", "deployed"]
    assert len(world.calls("jobs_preempt")) == preempted
