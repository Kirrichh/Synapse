"""The drain scenario of the result-dependency acceptance files (refinement §13, D2).

A deployment is refused with ``DRAIN_REQUIRED`` until the service's intake was
drained: a drain job created for the service and then cancelled. The job
service returns a new, unpredictable identifier for every job it creates; its
status answer echoes the identifier, and cancelling acts on exactly the job the
identifier names. The recovery the agent writes on the slow path creates a
drain job, reads its status, cancels the job it created, repeats the
deployment and confirms the release through an independent registry.

The composition scenarios (refinement §14) add drain jobs that start running
on busy services: cancelling a running job is refused with ``JOB_RUNNING``,
pausing it makes it cancellable, killing it compensates its creation. A
separate task family teaches the recovery of a refused cancel; an independent
audit service confirms which job was revoked.

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


#: The same deployment whose slow path asks the memory to recover the failure.
COMPOSE = PROGRAM[:PROGRAM.index("    if careful == true {")] + '''    let recovery = recover(refusal)
    if recovery.recovered == false {
      print("unrecovered")
    }
  }
}
print("deployed")
'''

#: The same deployment whose slow path spells out the whole chain, the running job included.
SPELLED = program().replace('''      let cancelled = tool("jobs_cancel"''', '''      let paused = tool("jobs_pause", {"job_id": job.payload.job_id})
      let cancelled = tool("jobs_cancel"''')

#: A task that cancels one job; a refused cancel is recovered by pausing, or by killing, the job.
#: Its segment (and so its context label) is the task's own: cleanup, rotation, retirement.
CANCEL = '''
memory palace "ops" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "jobs_cancel", "admissible_err": [], "allowed_alternatives": []}
let anchor = {"tool": "job_audit", "fields": {"revoked": true}}
let seg = {"segment": "SEGMENT", "intent": "cancel the job", "element_part": "job_cancel", "requirement": req, "anchor": anchor}
let plan = task_plan({"task_id": task_name, "goal": "cancel the job", "segments": [seg]})
context "SEGMENT" {
  try {
    let done = tool("jobs_cancel", {"job_id": job})
  } catch (ACTION_FAILED as refusal) {
    if way == "pause" {
      let stopped = tool("jobs_pause", {"job_id": job})
    } else {
      let stopped = tool("jobs_kill", {"job_id": job})
    }
    let again = tool("jobs_cancel", {"job_id": job}, {"retry_of": refusal.op})
    let audited = tool("job_audit", {"job_id": job})
  }
}
print("cancelled")
'''

#: Services whose drain jobs start running, and the queue each runs on; svc-stuck is never released.
RUNNING = {**{f"svc-busy-{index}": "stream" for index in range(1, 9)}, "svc-batch": "batch", "svc-stuck": "stream"}

#: Services whose job creation fails, or answers without a usable identifier.
REFUSING = {"svc-quota": {"ok": False, "err": "QUOTA"}, "svc-missing": {"ok": True, "queued": True},
            "svc-typed": {"ok": True, "job_id": 4242}, "svc-empty": {"ok": True, "job_id": ""}}


def tools(*, ids: str = "random"):
    refused = {"ok": False, "err": "DRAIN_REQUIRED", "tier": "web"}
    deploy = tool("deploy", "deploy:acct", [
        answer(refused, when={"service": "svc-stuck"}),
        stateful({"ok": True, "deployed": True}, act={"consume": "jobs", "match": {"service": {"arg": "service"},
                                                                                   "state": "revoked"}},
                 otherwise=refused, effect="deployed")],
        contract={"effect_on_err": {"DRAIN_REQUIRED": "none"}}, event_fields=["tier"])
    create = tool("jobs_create", "jobs:ops", [
        *(answer(payload, when={"service": service}) for service, payload in REFUSING.items()),
        *(stateful({"ok": True}, act={"create": "jobs", "key": "job_id", "ids": ids, "keep": ["service", "kind"],
                                      "state": "running", "set": {"queue": queue}}, effect="job_created",
                   when={"service": service}) for service, queue in RUNNING.items()),
        stateful({"ok": True}, act={"create": "jobs", "key": "job_id", "ids": ids, "keep": ["service", "kind"],
                                    "state": "pending"}, effect="job_created")],
        server="jobs", contract={"effect_on_err": {"QUOTA": "none"}})
    status = tool("jobs_status", "jobs:ops", [stateful({"ok": True}, act={"read": "jobs", "key": "job_id"},
                                                       otherwise={"ok": False, "err": "UNKNOWN_JOB"})],
                  server="jobs", contract={"effect_on_err": {"UNKNOWN_JOB": "none"}})
    cancel = tool("jobs_cancel", "jobs:ops", [stateful(
        {"ok": True}, act={"transition": "jobs", "key": "job_id", "from": ["pending", "killed"], "to": "revoked",
                           "refuse": {"running": {"ok": False, "err": "JOB_RUNNING", "job_state": "running"}},
                           "echo": ["queue"]},
        otherwise={"ok": False, "err": "UNKNOWN_JOB"}, effect="job_revoked")],
        server="jobs", contract={"effect_on_err": {"UNKNOWN_JOB": "none", "JOB_RUNNING": "none"}},
        event_fields=["job_state", "queue"])
    pause = tool("jobs_pause", "jobs:ops", [stateful(
        {"ok": True}, act={"transition": "jobs", "key": "job_id", "from": ["running"], "to": "pending"},
        otherwise={"ok": False, "err": "NOT_RUNNING"}, effect="job_paused")],
        server="jobs", contract={"effect_on_err": {"NOT_RUNNING": "none"}})
    kill = tool("jobs_kill", "jobs:ops", [stateful(
        {"ok": True}, act={"transition": "jobs", "key": "job_id", "from": ["running"], "to": "killed"},
        otherwise={"ok": False, "err": "NOT_RUNNING"}, effect="job_killed")],
        server="jobs", contract={"effect_on_err": {"NOT_RUNNING": "none"}, "compensates": "jobs_create"})
    audit = tool("job_audit", "audit:ops", [stateful({"ok": True}, act={"read": "jobs", "key": "job_id",
                                                                        "flags": {"revoked": "revoked"}},
                                                     otherwise={"ok": False, "err": "UNKNOWN_JOB"})],
                 server="audit", contract={"state_check_for": "jobs_cancel", "resolve_state": {"revoked": "applied"},
                                           "effect_on_err": {"UNKNOWN_JOB": "none"}})
    release = tool("release_state", "cmdb:release", [
        answer({"ok": True, "released": False}, when={"service": "svc-stuck"}),
        answer({"ok": True, "released": True})], server="cmdb",
        contract={"state_check_for": "deploy", "resolve_state": {"released": "applied"}})
    return [deploy, create, status, cancel, pause, kill, audit, release]


def provenance():
    return {source: {"ancestors": []} for source in ("deploy:acct", "jobs:ops", "cmdb:release", "audit:ops")}


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


CANCELS = (("cancel-a", "run-a", "cleanup"), ("cancel-b", "run-b", "rotation"), ("cancel-c", "run-c", "retirement"))


def learn_cancel(world: MemoryWorld, way: str, runs=CANCELS) -> dict:
    """Three verified recoveries of a refused cancel of a running job, by pausing (or killing) it, observed
    in three different contexts: the recovery is not bound to one."""
    for run_id, job, segment in runs:
        seed_job(world, job, service="svc-ops", kind="batch", state="running", queue="stream")
        world.run(CANCEL.replace("SEGMENT", segment), run_id, {"task_name": run_id, "job": job, "way": way})
    birth, = world.reports()[-1]["births"]
    return birth


def compose_inputs(task: str, service: str) -> dict:
    return {"task_name": task, "service": service, "careful": False}
