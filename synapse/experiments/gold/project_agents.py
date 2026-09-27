"""Permanent project element owners and their bounded maintenance lifecycle.

Owners consume durable task/source requests, build the eight memory views and
return to IDLE. A new job reads completed runs only through the court decision
it pinned when it was requested, so a retry or resume keeps its original cut
and later outcomes enter the next job. Recovery repeats pure maintenance only;
neither owner jobs nor memory labels can execute a repository command, admit
knowledge or close a verification gap.
"""
from pathlib import Path

from .project_court import outcome_judgement, read_court, task_subjects
from .project_episode_outcome import reopen_completed_run
from .project_memory_store import ProjectMemoryStore, memory_job_identity
from .project_memory_selection import require_run_memory_selection, selected_episode, selected_episode_outcome
from .project_model import build_active_memory_frame, build_project_model
from .project_learning import consolidate_episode, build_memory_layers
from .source_verification import canonical, source_ref
from .stage10.task_contract import GoverningTaskContract
from .task_targets import read_task_targets

OWNER_LIFECYCLE_V1 = "synapse.stage4.gold.element-owner-lifecycle/v1"
OWNER_LIFECYCLE_V2 = "synapse.stage4.gold.element-owner-lifecycle/v2"
# V3 jobs read completed runs through their pinned court decision. Earlier
# profiles keep the original interpretation of every job they recorded.
OWNER_LIFECYCLE_V3 = "synapse.stage4.gold.element-owner-lifecycle/v3"
OWNER_LIFECYCLES = (OWNER_LIFECYCLE_V1, OWNER_LIFECYCLE_V2, OWNER_LIFECYCLE_V3)
_FRAME_SCHEMAS = {OWNER_LIFECYCLE_V2: "synapse.stage4.gold.active-memory-frame/v2",
                  OWNER_LIFECYCLE_V3: "synapse.stage4.gold.active-memory-frame/v3"}
MAX_PRIOR_EPISODES = 128
# Selections whose candidate universe keeps verified positive experience.
_AUTOMATIC_SELECTIONS = {"ALL", "SUCCESS_ONLY"}


def _episode(frozen, state, payload):
    result = state.final_result
    task = GoverningTaskContract.from_dict(frozen["declaration"]["task_contract"])
    return {"run_id": frozen["declaration"]["run_id"], "paths": sorted({item.subject_path for item in task.effects
                if item.subject_path is not None}), "repository_revision": task.repository_revision_sha256,
        "status": result.structured_outcome["payload"]["status"], "final_status": result.final_status.value,
        "result_ref": payload["result_ref"], "outcome": result.structured_outcome["payload"]}


def _historical_history(request, store, profile, selection):
    """V1/V2 jobs reopen their original history cut under their original rules."""
    if profile == OWNER_LIFECYCLE_V2 and len(request["prior_learning"]) != len(request["prior_outcomes"]):
        raise ValueError("memory history lost its consolidation cut")
    if len(request["prior_outcomes"]) > MAX_PRIOR_EPISODES:
        raise ValueError("active memory exceeds its explicit episode budget")
    episodes, learning = [], []
    for index, receipt in enumerate(request["prior_outcomes"]):
        event = store.read(receipt)
        if event["kind"] != "OUTCOME_RECORDED":
            raise ValueError("active memory episode lacks its completed owner transition")
        frozen, state, records = reopen_completed_run(event["payload"], request["project_identity"])
        episode = _episode(frozen, state, event["payload"])
        if profile == OWNER_LIFECYCLE_V2:
            consolidated = store.read(request["prior_learning"][index])
            actual = consolidate_episode(frozen=frozen, state=state, records=records, outcome_ref=receipt)
            if (consolidated["kind"] != "CONSOLIDATED" or consolidated["job_key"] != event["job_key"]
                    or consolidated["payload"] != actual):
                raise ValueError("learning differs from its physical episode and publication")
        if selected_episode(episode["status"], selection):
            episodes.append(episode)
            if profile == OWNER_LIFECYCLE_V2:
                learning.append(actual)
    return episodes, learning


def _court_history(request, store, task, selection):
    """V3 jobs see the judged history and subject states of their pinned decision."""
    if "court" not in request:
        raise ValueError("memory request lost its court decision")
    court = read_court(store, project_identity=request["project_identity"], decision=request["court"])
    shown = court["judged"][-MAX_PRIOR_EPISODES:]
    episodes, learning = [], []
    for item in shown:
        observation = item["observation"]
        requirement = observation["requirement"]
        # Assertions keep their own verified status and are selected one by one;
        # an uncertain run does not erase an attempt that was independently verified.
        learning.append(item["learning"])
        if selected_episode_outcome(requirement["outcome"], selection):
            episodes.append({"run_id": observation["run_id"], "paths": requirement["paths"],
                             "repository_revision": requirement["repository_revision"],
                             "status": requirement["run_status"], "final_status": requirement["controller_status"],
                             "result_ref": item["result_ref"], "observation": observation})
    subjects = task_subjects(court, task)
    section = {"decision": court["decision"], "mode": court["mode"], "pending": court["pending"],
               "judged_episodes": len(court["judged"]), "omitted_episodes": len(court["judged"]) - len(shown),
               "subjects": subjects,
               # Only the court admits automation; selection only narrows the candidate universe.
               "automatic_patches": [item["patch_sha256"] for item in subjects if item["state"] == "ADMITTED"]
                                    if selection in _AUTOMATIC_SELECTIONS else []}
    return episodes, learning, section


def _frame_payload(request, request_ref, started_ref, store):
    task = GoverningTaskContract.from_dict(request["source_snapshot"]["task_contract"])
    read_task_targets(canonical(request["target_resolution"]), task=task, repository_root=Path(request["repository_root"]))
    profile = request.get("profile", OWNER_LIFECYCLE_V1)
    if profile not in OWNER_LIFECYCLES:
        raise ValueError("memory request has an unknown lifecycle")
    selection = request["run_memory_selection"]
    court = None
    if profile == OWNER_LIFECYCLE_V3:
        episodes, learning, court = _court_history(request, store, task, selection)
    else:
        episodes, learning = _historical_history(request, store, profile, selection)
    model = build_project_model(project_identity=request["project_identity"],
        resolution=request["target_resolution"], source_snapshot=request["source_snapshot"])
    frame = build_active_memory_frame(model=model, task=task,
        source_snapshot=request["source_snapshot"], prior_episodes=episodes)
    if profile != OWNER_LIFECYCLE_V1:
        frame = {**frame, "schema_version": _FRAME_SCHEMAS[profile],
                 "layers": build_memory_layers(task=task, source_snapshot=request["source_snapshot"],
                                               episodes=episodes, learning=learning,
                                               run_memory_selection=selection)}
    if court is not None:
        frame["court"] = court
    return {"profile": profile, "request_ref": request_ref, "started_ref": started_ref,
        "project_model": model, "active_memory_frame": frame,
        "owners": [{"owner_id": item["owner_id"], "element_id": item["element_id"], "state": "IDLE",
                    "completed_job": request_ref, "memory_routes": list(frame["memory_kinds"])}
                   for item in frame["elements"]]}


def capture_active_memory(*, project, run_root, run_id, target_resolution, source_snapshot, run_memory_selection,
                          court):
    """Pin the court decision a new job reads; ``court`` is the memory court's port."""
    store = ProjectMemoryStore(project.declaration.state_root)
    identity = source_snapshot["project_record_sha256"]
    job_key = memory_job_identity(identity, run_root, run_id)
    with store.session() as guard:
        request = {"project_identity": identity, "run_root": str(run_root), "run_id": run_id,
            "repository_root": str(project.declaration.repo_root), "source_snapshot": source_snapshot,
            "target_resolution": target_resolution,
            "run_memory_selection": require_run_memory_selection(run_memory_selection)}
        existing = [event for event, _ in store.inventory(guard=guard)
                    if event["kind"] == "REQUESTED" and event["job_key"] == job_key]
        if existing:
            # A retry consumes its original history cut and lifecycle, not outcomes added later.
            original = existing[0]["payload"]
            for field in ("profile", "prior_outcomes", "prior_learning", "court"):
                if field in original:
                    request[field] = original[field]
        else:
            # The unjudged tail is judged before the job pins the decision it reads.
            decision = court.consolidate(store, guard, project_identity=identity)
            request.update(profile=OWNER_LIFECYCLE_V3, court=decision["decision"])
        requested = store.put(kind="REQUESTED", job_key=job_key, payload=request, guard=guard)
        started = store.put(kind="STARTED", job_key=job_key, payload={"request_ref": requested}, guard=guard)
        completed = store.put(kind="FRAME_COMPLETED", job_key=job_key,
            payload=_frame_payload(request, requested, started, store), guard=guard)
    return {"profile": request.get("profile", OWNER_LIFECYCLE_V1), "job_key": job_key, "frame_event": completed}


def read_active_memory(binding, *, source_snapshot, run_memory_selection):
    require_run_memory_selection(run_memory_selection)
    if (type(binding) is not dict or set(binding) != {"profile", "job_key", "frame_event"}
            or binding["profile"] not in OWNER_LIFECYCLES):
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
            or payload["profile"] != binding["profile"]
            or request["payload"]["run_memory_selection"] != run_memory_selection):
        raise ValueError("active memory lacks its original maintenance lifecycle")
    actual = _frame_payload(request["payload"], payload["request_ref"], payload["started_ref"], store)
    if actual != payload:
        raise ValueError("active memory differs from its retained project evidence")
    return payload


def record_project_outcome(*, inputs, result, court):
    data = inputs.data
    binding = data.get("source_snapshot", {}).get("project_memory")
    if binding is None:
        return {"status": "NOT_IN_PROFILE"}
    if binding["profile"] not in OWNER_LIFECYCLES:
        raise ValueError("owner outcome has an unknown lifecycle")
    store = ProjectMemoryStore(Path(data["project_state_root"]))
    payload = {"run_root": data["run_root"],
        "frozen_ref": source_ref(inputs.canonical_bytes, data["schema_version"]).to_dict(),
        "result_ref": source_ref(canonical(result.stored_dict()), result.payload()["schema_version"]).to_dict()}
    reopen_completed_run(payload, data["project_record_sha256"])
    with store.session() as guard:
        frame = store.read(binding["frame_event"])
        if frame["kind"] != "FRAME_COMPLETED" or frame["job_key"] != binding["job_key"]:
            raise ValueError("owner outcome has no completed task frame")
        receipt = store.put(kind="OUTCOME_RECORDED", job_key=binding["job_key"], payload=payload, guard=guard)
        # Every completed run reaches memory through the court, whatever the
        # lifecycle of the job that recorded it; that job's frame is unchanged.
        identity = data["project_record_sha256"]
        head = court.consolidate(store, guard, project_identity=identity)["decision"]
        court = read_court(store, project_identity=identity, decision=head)
    # The judgement of this outcome, not the moving court head, keeps resume output stable.
    return {"status": "RECORDED", "event": receipt, "court": outcome_judgement(court, receipt)}
