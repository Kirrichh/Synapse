"""The drain scenario of the result-dependency acceptance files (refinement §13, D2).

A deployment is refused with ``DRAIN_REQUIRED`` until the service's intake was
drained: a drain job created for the service and then cancelled. The job
service returns a new, unpredictable identifier for every job it creates; its
status answer echoes the identifier, and cancelling acts on exactly the job the
identifier names. The recovery the agent writes on the slow path creates a
drain job, reads its status, cancels the job it created, repeats the
deployment and confirms the release through an independent registry.

The tool server keeps the jobs; the checker reads which job was cancelled from
the server's own world, never from the agent's account.
"""
from __future__ import annotations

import fcntl
import json

from acceptance.memory._world import MemoryWorld, answer, stateful, tool

PROGRAM = '''
memory palace "ops" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "deploy", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "release_state", "fields": {"released": true}}
let seg = {"segment": "deploy", "intent": "deploy the service", "element_part": "service_deploy", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "deploy the service", "segments": [seg]})
context "deploy" {
  try {
    let done = tool("deploy", {"service": service})
  } catch (ACTION_FAILED as refusal) {
    if careful == true {
      print("abstained")
    } else {
      let job = tool("jobs_create", {"service": service, "kind": "drain"})
      let seen = tool("jobs_status", {"job_id": JOB})
      let cancelled = tool("jobs_cancel", {"job_id": JOB})
      let repeated = tool("deploy", {"service": service}, {"retry_of": refusal.op})
      let released = tool("release_state", {"service": service})
    }
  }
}
print("deployed")
'''


def program(*, literal: bool = False) -> str:
    """The recovery reads the created job from its answer, or — the driver's literal — from an input."""
    return PROGRAM.replace("JOB", "planned_job" if literal else "job.payload.job_id")


#: Services whose job creation fails, or answers without a usable identifier.
REFUSING = {"svc-quota": {"ok": False, "err": "QUOTA"}, "svc-missing": {"ok": True, "queued": True},
            "svc-typed": {"ok": True, "job_id": 4242}, "svc-empty": {"ok": True, "job_id": ""}}


def tools(*, ids: str = "random"):
    deploy = tool("deploy", "deploy:acct", [stateful(
        {"ok": True, "deployed": True}, act={"consume": "jobs", "match": {"service": {"arg": "service"},
                                                                         "state": "revoked"}},
        otherwise={"ok": False, "err": "DRAIN_REQUIRED", "tier": "web"}, effect="deployed")],
        contract={"effect_on_err": {"DRAIN_REQUIRED": "none"}}, event_fields=["tier"])
    create = tool("jobs_create", "jobs:ops", [
        *(answer(payload, when={"service": service}) for service, payload in REFUSING.items()),
        stateful({"ok": True}, act={"create": "jobs", "key": "job_id", "ids": ids, "keep": ["service", "kind"],
                                    "state": "pending"}, effect="job_created")],
        server="jobs", contract={"effect_on_err": {"QUOTA": "none"}})
    status = tool("jobs_status", "jobs:ops", [stateful({"ok": True}, act={"read": "jobs", "key": "job_id"},
                                                       otherwise={"ok": False, "err": "UNKNOWN_JOB"})],
                  server="jobs", contract={"effect_on_err": {"UNKNOWN_JOB": "none"}})
    cancel = tool("jobs_cancel", "jobs:ops", [stateful(
        {"ok": True}, act={"transition": "jobs", "key": "job_id", "from": "pending", "to": "revoked"},
        otherwise={"ok": False, "err": "UNKNOWN_JOB"}, effect="job_revoked")],
        server="jobs", contract={"effect_on_err": {"UNKNOWN_JOB": "none"}})
    release = tool("release_state", "cmdb:release", [answer({"ok": True, "released": True})], server="cmdb",
                   contract={"state_check_for": "deploy", "resolve_state": {"released": "applied"}})
    return [deploy, create, status, cancel, release]


def provenance():
    return {source: {"ancestors": []} for source in ("deploy:acct", "jobs:ops", "cmdb:release")}


def world(root, *, ids: str = "random") -> MemoryWorld:
    return MemoryWorld(root, tools(ids=ids), provenance=provenance())


def inputs(task: str, service: str, *, careful: bool = False, planned_job: str | None = None) -> dict:
    values = {"task_name": task, "service": service, "careful": careful}
    if planned_job is not None:
        values["planned_job"] = planned_job
    return values


LEARNING = (("learn-api", "svc-api"), ("learn-web", "svc-web"), ("learn-worker", "svc-worker"))


def learn(world: MemoryWorld) -> dict:
    """Three verified drains in three tasks; the third births the learned procedure."""
    for run_id, service in LEARNING:
        world.run(program(), run_id, inputs(run_id, service))
    birth, = world.reports()[-1]["births"]
    return birth


def jobs(world: MemoryWorld) -> dict:
    """The job service's own record: identifier -> job."""
    return world.world().get("objects", {}).get("jobs", {})


def seed_job(world: MemoryWorld, job_id: str, **fields) -> None:
    """A job that already exists in the service before a session (another team's, say)."""
    path = world.world_path
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {"calls": [], "effects": []}
        state.setdefault("objects", {}).setdefault("jobs", {})[job_id] = {"job_id": job_id, **fields}
        path.write_text(json.dumps(state, sort_keys=True))
