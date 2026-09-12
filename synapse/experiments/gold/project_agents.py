"""Permanent project element owners and their bounded maintenance lifecycle.

Owners consume durable task/source requests, build the eight memory views and
return to IDLE. Completed run evidence becomes an episode for their next job.
Recovery repeats pure maintenance only; neither owner jobs nor memory labels
can execute a repository command, admit knowledge or close a verification gap.
"""
import hashlib
import json
from pathlib import Path

from .admission_journal import FileSnapshotFence
from .persistence import read_regular_bytes
from .project_memory_store import ProjectMemoryStore, memory_job_identity
from .project_memory_selection import require_run_memory_selection, selected_episode
from .project_model import build_active_memory_frame, build_project_model
from .source_verification import canonical, source_ref
from .stage10.task_contract import GoverningTaskContract
from .task_targets import read_task_targets
from .runner.records import RunRecordStore
from .runner.state_machine import load_run_state

OWNER_LIFECYCLE_V1 = "synapse.stage4.gold.element-owner-lifecycle/v1"
MAX_PRIOR_EPISODES = 128


def _run_episode(payload, project_identity):
    if type(payload) is not dict or set(payload) != {"run_root", "frozen_ref", "result_ref"}:
        raise ValueError("owner outcome needs its physical run origin")
    root = Path(payload["run_root"])
    if not root.is_absolute():
        raise ValueError("owner outcome needs an absolute run location")
    raw = read_regular_bytes(root / "experiment.json", maximum_bytes=16 * 1024 * 1024)
    frozen = json.loads(raw)
    if (raw != canonical(frozen) or source_ref(raw, frozen["schema_version"]).to_dict() != payload["frozen_ref"]
            or frozen["project_record_sha256"] != project_identity or frozen["run_root"] != str(root)):
        raise ValueError("owner outcome belongs to another project or frozen run")
    records = RunRecordStore(root, mutation_fence=FileSnapshotFence(root / "run-coordinator", read_only=True), read_only=True)
    state = load_run_state(records)
    result = state.final_result
    if (result is None or result.manifest_sha256 != state.manifest.manifest_sha256
            or state.manifest.inputs_sha256 != hashlib.sha256(raw).hexdigest()):
        raise ValueError("owner outcome lacks a completed domain result")
    result.validate_identity()
    if source_ref(canonical(result.stored_dict()), result.payload()["schema_version"]).to_dict() != payload["result_ref"]:
        raise ValueError("owner outcome differs from its original result")
    task = GoverningTaskContract.from_dict(frozen["declaration"]["task_contract"])
    return {"run_id": frozen["declaration"]["run_id"], "paths": sorted({item.subject_path for item in task.effects
                if item.subject_path is not None}), "repository_revision": task.repository_revision_sha256,
        "status": result.structured_outcome["payload"]["status"], "final_status": result.final_status.value,
        "result_ref": payload["result_ref"], "outcome": result.structured_outcome["payload"]}


def _frame_payload(request, request_ref, started_ref, store):
    task = GoverningTaskContract.from_dict(request["source_snapshot"]["task_contract"])
    read_task_targets(canonical(request["target_resolution"]), task=task, repository_root=Path(request["repository_root"]))
    episodes = []
    if len(request["prior_outcomes"]) > MAX_PRIOR_EPISODES:
        raise ValueError("active memory exceeds its explicit episode budget")
    for receipt in request["prior_outcomes"]:
        event = store.read(receipt)
        if event["kind"] != "OUTCOME_RECORDED":
            raise ValueError("active memory episode lacks its completed owner transition")
        episode = _run_episode(event["payload"], request["project_identity"])
        if selected_episode(episode["status"], request["run_memory_selection"]):
            episodes.append(episode)
    model = build_project_model(project_identity=request["project_identity"],
        resolution=request["target_resolution"], source_snapshot=request["source_snapshot"])
    frame = build_active_memory_frame(model=model, task=task,
        source_snapshot=request["source_snapshot"], prior_episodes=episodes)
    return {"profile": OWNER_LIFECYCLE_V1, "request_ref": request_ref, "started_ref": started_ref,
        "project_model": model, "active_memory_frame": frame,
        "owners": [{"owner_id": item["owner_id"], "element_id": item["element_id"], "state": "IDLE",
                    "completed_job": request_ref, "memory_routes": list(frame["memory_kinds"])}
                   for item in frame["elements"]]}


def capture_active_memory(*, project, run_root, run_id, target_resolution, source_snapshot, run_memory_selection):
    store = ProjectMemoryStore(project.declaration.state_root)
    identity = source_snapshot["project_record_sha256"]
    job_key = memory_job_identity(identity, run_root, run_id)
    with store.session() as guard:
        events = store.inventory()
        prior = sorted([receipt for event, receipt in events if event["kind"] == "OUTCOME_RECORDED"
                        and event["job_key"] != job_key], key=canonical)
        request = {"project_identity": identity, "run_root": str(run_root), "run_id": run_id,
            "repository_root": str(project.declaration.repo_root), "source_snapshot": source_snapshot,
            "target_resolution": target_resolution, "prior_outcomes": prior,
            "run_memory_selection": require_run_memory_selection(run_memory_selection)}
        existing = [event for event, _ in events if event["kind"] == "REQUESTED" and event["job_key"] == job_key]
        if existing:
            # A retry consumes its original history cut, not outcomes added later.
            request["prior_outcomes"] = existing[0]["payload"]["prior_outcomes"]
        requested = store.put(kind="REQUESTED", job_key=job_key, payload=request, guard=guard)
        started = store.put(kind="STARTED", job_key=job_key, payload={"request_ref": requested}, guard=guard)
        completed = store.put(kind="FRAME_COMPLETED", job_key=job_key,
            payload=_frame_payload(request, requested, started, store), guard=guard)
    return {"profile": OWNER_LIFECYCLE_V1, "job_key": job_key, "frame_event": completed}


def read_active_memory(binding, *, source_snapshot, run_memory_selection):
    require_run_memory_selection(run_memory_selection)
    if (type(binding) is not dict or set(binding) != {"profile", "job_key", "frame_event"}
            or binding["profile"] != OWNER_LIFECYCLE_V1):
        raise ValueError("active memory has an unknown owner lifecycle")
    store = ProjectMemoryStore(Path(source_snapshot["project_state_root"]), read_only=True)
    complete = store.read(binding["frame_event"])
    payload = complete["payload"]
    request = store.read(payload["request_ref"])
    started = store.read(payload["started_ref"])
    if (complete["kind"] != "FRAME_COMPLETED" or request["kind"] != "REQUESTED" or started["kind"] != "STARTED"
            or {complete["job_key"], request["job_key"], started["job_key"]} != {binding["job_key"]}
            or started["payload"] != {"request_ref": payload["request_ref"]}
            or request["payload"]["source_snapshot"] != source_snapshot
            or request["payload"]["run_memory_selection"] != run_memory_selection):
        raise ValueError("active memory lacks its original maintenance lifecycle")
    actual = _frame_payload(request["payload"], payload["request_ref"], payload["started_ref"], store)
    if actual != payload:
        raise ValueError("active memory differs from its retained project evidence")
    return payload


def record_project_outcome(*, inputs, result):
    data = inputs.data
    binding = data.get("source_snapshot", {}).get("project_memory")
    if binding is None:
        return {"status": "NOT_IN_PROFILE"}
    store = ProjectMemoryStore(Path(data["project_state_root"]))
    payload = {"run_root": data["run_root"],
        "frozen_ref": source_ref(inputs.canonical_bytes, data["schema_version"]).to_dict(),
        "result_ref": source_ref(canonical(result.stored_dict()), result.payload()["schema_version"]).to_dict()}
    _run_episode(payload, data["project_record_sha256"])
    with store.session() as guard:
        frame = store.read(binding["frame_event"])
        if frame["kind"] != "FRAME_COMPLETED" or frame["job_key"] != binding["job_key"]:
            raise ValueError("owner outcome has no completed task frame")
        receipt = store.put(kind="OUTCOME_RECORDED", job_key=binding["job_key"], payload=payload, guard=guard)
    return {"status": "RECORDED", "event": receipt}
