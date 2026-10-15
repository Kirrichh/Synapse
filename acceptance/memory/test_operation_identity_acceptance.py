"""Operation identity and resources: which operation and which resource close a goal (refinement §8).

Each session is one task with one segment whose requirement was fixed before
execution. The tool server's world is the checker's truth: which jobs exist,
which were cancelled, how many checkpoints were applied. The court's verdict
must agree with it.

* A job is created and exactly the returned job is cancelled: the goal is met.
  A successful cancel of another, pre-existing job does not meet it.
* Two independent identical operations are two operations, not one retried.
* A refused operation followed by an identical but undeclared call stays
  refused: the second call is a new operation and does not settle the first.
* A declared retry that changes the essential arguments is refused before any
  effect; a declared retry with the same arguments settles the operation.
"""
from __future__ import annotations

import json

from acceptance.memory._world import MemoryWorld, answer, tool

PALACE = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
'''

JOBS = PALACE + '''
let resource = {"argument": "job_id", "from": {"tool": "create_job", "field": "job_id"}}
let req = {"kind": "execute", "tool": "cancel_job", "admissible_err": [], "allowed_alternatives": [], "resource": resource}
let anchor = {"tool": "cancel_job", "fields": {"cancelled": true}}
let seg = {"segment": "jobs", "intent": "create a job and cancel exactly that job", "element_part": "jobs", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "cancel the created job", "segments": [seg]})
context "jobs" {
  let created = tool("create_job", {"owner": task_name})
  if whose == "created" {
    let cancelled = tool("cancel_job", {"job_id": created.payload.job_id})
  } else {
    let cancelled = tool("cancel_job", {"job_id": "job-legacy"})
  }
}
print("done")
'''

CHECKPOINTS = PALACE + '''
let req = {"kind": "execute", "tool": "checkpoint", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "maintenance", "intent": "apply the checkpoint", "element_part": "checkpoints", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "apply the checkpoint", "segments": [seg]})
context "maintenance" {
  if mode == "twice" {
    let first = tool("checkpoint", {"db": db})
    let second = tool("checkpoint", {"db": db})
  } else {
    try {
      let first = tool("checkpoint", {"db": db})
    } catch (ACTION_FAILED as busy) {
      if mode == "again" {
        let second = tool("checkpoint", {"db": db})
      }
      if mode == "declared" {
        let second = tool("checkpoint", {"db": db}, {"retry_of": busy.op})
      }
      if mode == "changed" {
        try {
          let second = tool("checkpoint", {"db": db, "quick": true}, {"retry_of": busy.op})
        } catch (ACTION_FAILED as refused) {
          print("retry refused")
        }
      }
    }
  }
}
print("done")
'''

BUSY = {"payload": {"ok": False, "err": "BUSY"}, "effect": "partial"}


def _tools():
    create = tool("create_job", "jobs:acct1", [
        answer({"ok": True, "job_id": f"job-{owner}"}, when={"owner": owner}, effect="created")
        for owner in ("exact", "other")], server="jobs")
    cancel = tool("cancel_job", "jobs:acct1", [
        answer({"ok": True, "cancelled": True}, when={"job_id": job}, effect="cancelled")
        for job in ("job-exact", "job-other", "job-legacy")], server="jobs",
        contract={"effect_on_err": {"UNKNOWN_JOB": "none"}})
    checkpoint = tool("checkpoint", "db:primary", [
        answer({"ok": True, "applied": True}, when={"db": "twice"}, effect="applied"),
        *[answer({"ok": True, "applied": True}, when={"db": db}, sequence=[BUSY], effect="applied")
          for db in ("again", "declared", "changed")]],
        server="db", contract={"effect_on_err": {"BUSY": "partial"}, "repeatable_on_partial": True})
    return [create, cancel, checkpoint]


def _verdict(world, run_id):
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == run_id]
    return verdict


def _effects(world, name, **args):
    return [item["effect"] for item in world.world()["effects"]
            if item["tool"] == name and all(item["args"].get(key) == value for key, value in args.items())]


def _gateway(world, run_id):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record for record in map(json.loads, journal.read_text().splitlines())
            if record["body"].get("run_id") == run_id]


def test_only_the_named_operation_on_the_named_resource_closes_a_goal(tmp_path):
    world = MemoryWorld(tmp_path, _tools(), provenance={"jobs:acct1": {"ancestors": []},
                                                         "db:primary": {"ancestors": []}})

    # The job that was created is the job that was cancelled: the environment confirms the goal.
    world.run(JOBS, "exact", {"task_name": "exact", "whose": "created"})
    verdict = _verdict(world, "exact")
    assert verdict["verdict"] == "confirmed" and verdict["evidence"]
    assert _effects(world, "create_job") == ["created"] and _effects(world, "cancel_job") == ["cancelled"]
    assert world.calls("cancel_job") == [{"job_id": "job-exact"}]

    # A successful cancel of another job does not close the goal; the created job stays open.
    world.run(JOBS, "other", {"task_name": "other", "whose": "legacy"})
    verdict = _verdict(world, "other")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "required_operation_on_another_resource"
    assert _effects(world, "cancel_job", job_id="job-legacy") == ["cancelled"]
    assert _effects(world, "cancel_job", job_id="job-other") == []

    # Two independent identical operations: both applied, two operations in the journal.
    world.run(CHECKPOINTS, "twice", {"task_name": "twice", "mode": "twice", "db": "twice"})
    assert _verdict(world, "twice")["verdict"] != "failed"
    assert _effects(world, "checkpoint", db="twice") == ["applied", "applied"]
    started = [record["body"] for record in _gateway(world, "twice") if record["kind"] == "STARTED"]
    assert [(item["op_seq"], item["retry_of"]) for item in started] == [(1, None), (2, None)]

    # A refused operation, then an identical undeclared call: a new operation that settles nothing.
    world.run(CHECKPOINTS, "again", {"task_name": "again", "mode": "again", "db": "again"})
    verdict = _verdict(world, "again")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert _effects(world, "checkpoint", db="again") == ["partial", "applied"]
    started = [record["body"] for record in _gateway(world, "again") if record["kind"] == "STARTED"]
    assert [(item["op_seq"], item["retry_of"]) for item in started] == [(1, None), (2, None)]

    # A declared retry with changed essential arguments is refused before any effect.
    world.run(CHECKPOINTS, "changed", {"task_name": "changed", "mode": "changed", "db": "changed"})
    verdict = _verdict(world, "changed")
    assert verdict["verdict"] == "failed" and verdict["criterion"] == "requirement_unfulfilled"
    assert world.calls("checkpoint").count({"db": "changed"}) == 1 and {"db": "changed", "quick": True} not in \
        world.calls("checkpoint")
    rejected, = [record["body"] for record in _gateway(world, "changed") if record["kind"] == "REJECTED"]
    assert rejected["reason"] == "a declared retry changes the tool or its essential arguments"

    # The same retry, declared with the same arguments, settles the one operation.
    world.run(CHECKPOINTS, "declared", {"task_name": "declared", "mode": "declared", "db": "declared"})
    assert _verdict(world, "declared")["verdict"] != "failed"
    assert _effects(world, "checkpoint", db="declared") == ["partial", "applied"]
    started = [record["body"] for record in _gateway(world, "declared") if record["kind"] == "STARTED"]
    assert [(item["op_seq"], item["attempt"], item["retry_of"], item["admitted"]) for item in started] == [
        (1, 1, None, None), (1, 2, 1, True)]
