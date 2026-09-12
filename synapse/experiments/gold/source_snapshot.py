"""Freeze and physically reopen task-selected source experience.

The snapshot names immutable prefixes of the existing journal. It neither
executes recipes nor admits behaviors. The inventory fence precedes operation
fences, which precede the project fence; source writers use the same order.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path

from .admission_journal import FileSnapshotFence
from .canonicalization import HashBoundRef
from .persistence import read_regular_bytes
from .source_operation_journal import (
    capture_source_operation, read_captured_source_operation, source_operation_directories,
)
from .source_experience import SOURCE_RECALL_QUERY_V1, recall_source_experience, source_publications, export_source_knowledge
from .source_verification import canonical, source_ref
from .stage10.task_contract import GoverningTaskContract
from .stage13.publication_store import PublicationResult, PUBLICATION_RESULT_V3
from .stage13.publication import reference


SOURCE_SNAPSHOT_V1 = "synapse.stage4.gold.source-experience-snapshot/v1"


def task_source_query(task, limit):
    return {"schema_version": SOURCE_RECALL_QUERY_V1, "statement": task.task_statement,
        "revision": task.repository_revision_sha256, "scope": list(task.allowed_scope.entries), "limit": limit}


def capture_project_source_snapshot(*, project, task, limit):
    state = project.declaration.state_root
    with ExitStack() as held:
        held.enter_context(FileSnapshotFence(state / "source-operations-coordinator").exclusive())
        directories = source_operation_directories(state)
        guards = [held.enter_context(FileSnapshotFence(path / "fence").exclusive()) for path in directories]
        held.enter_context(project.fence.exclusive())
        if project.fence.current_epoch() % 2:
            raise ValueError("project source snapshot cannot cross an unfinished publication")
        captures = [capture_source_operation(path, guard=guard) for path, guard in zip(directories, guards)]
        operations = [read_captured_source_operation(state, item) for item in captures]
        publications, origins = {}, []
        for result, request, _ in source_publications(state):
            operation_id = request["domain"]["operation_id"]
            publications[operation_id] = result
            origins.append({"operation_id": operation_id, "transaction_id": result["transaction_id"],
                "result_ref": reference(result, PUBLICATION_RESULT_V3).to_dict()})
        record = read_regular_bytes(state / "project.json", maximum_bytes=16 * 1024 * 1024)
        grant = None if project.declaration.entitlements is None else project.declaration.entitlements.to_dict()
        query = task_source_query(task, limit)
        recall = recall_source_experience(state_root=state, query=query, entitlements=grant,
                                          operations=operations, publications=publications)
        snapshot = {"schema_version": SOURCE_SNAPSHOT_V1, "project_state_root": str(state),
            "project_record_sha256": hashlib.sha256(record).hexdigest(), "task_contract": task.to_dict(),
            "operations": captures, "publications": origins, "recall": recall}
        knowledge = export_source_knowledge(state)
        heads = {"lifecycle": project.lifecycle_store.current_anchor().to_dict(),
                 "provenance": project.attestation_store.current_anchor().to_dict(),
                 "taint": project.taint_store.current_anchor().to_dict()}
    return snapshot, knowledge, heads


def read_source_snapshot(value, *, task=None):
    """Validate original physical history, including after later learning."""
    fields = {"schema_version", "project_state_root", "project_record_sha256", "task_contract",
              "operations", "publications", "recall"}
    if type(value) is not dict or set(value) != fields or value["schema_version"] != SOURCE_SNAPSHOT_V1:
        raise ValueError("source experience snapshot has an unknown contract")
    recorded_task = GoverningTaskContract.from_dict(value["task_contract"])
    if task is not None and task != recorded_task:
        raise ValueError("source experience snapshot belongs to another task")
    state = Path(value["project_state_root"])
    if not state.is_absolute():
        raise ValueError("source experience snapshot needs an absolute project location")
    raw = read_regular_bytes(state / "project.json", maximum_bytes=16 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != value["project_record_sha256"]:
        raise ValueError("source experience snapshot belongs to a changed project")
    if type(value["operations"]) is not list or type(value["publications"]) is not list:
        raise ValueError("source snapshot inventories must be exact lists")
    keys = [item["operation_key"] for item in value["operations"]]
    if keys != sorted(set(keys)):
        raise ValueError("source snapshot operation inventory is repeated or unordered")
    operations = [read_captured_source_operation(state, item) for item in value["operations"]]
    publications = {}
    for item in value["publications"]:
        if type(item) is not dict or set(item) != {"operation_id", "transaction_id", "result_ref"}:
            raise ValueError("source snapshot publication has an unknown identity")
        result = PublicationResult(state / "publications", item["transaction_id"]).retained_payload()
        if reference(result, PUBLICATION_RESULT_V3) != HashBoundRef.from_dict(item["result_ref"]) or item["operation_id"] in publications:
            raise ValueError("source snapshot publication changed or is repeated")
        publications[item["operation_id"]] = result
    query = value["recall"]["query"]
    if query != task_source_query(recorded_task, query["limit"]):
        raise ValueError("source snapshot query differs from the governing task")
    actual = recall_source_experience(state_root=state, query=query,
        entitlements=json.loads(raw)["entitlements"], operations=operations, publications=publications)
    if actual != value["recall"]:
        raise ValueError("selected source experience differs from its original physical history")
    return actual


def source_snapshot_reference(value):
    return source_ref(canonical(value), SOURCE_SNAPSHOT_V1)


def source_experience_delivery(value):
    """Pure data projection; Gold references remain outside Mini information.

    A nonzero command exit is an observation, never a rejected-method guard.
    Runtime/admission proofs are retained by Gold and do not become worker data.
    """
    import base64
    def encode(raw):
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    items = []
    recall = value["recall"]
    for selected in recall["selected"]:
        experience = selected["experience"]
        claim = experience["claim"]
        retained = {item["ref"]["sha256"]: item["content_base64url"] for item in experience["retained"]}
        content = {"repository_revision": claim["revision"],
            "status": experience["status"], "verification": experience["verification"],
            "execution": experience["execution"], "applicability": "UNASSESSED",
            "checkpoint_complete": experience["checkpoint_complete"],
            "matched_paths": selected["match"]["paths"],
            "sources": [{"path": item["path"], "content_base64url": retained[item["ref"]["sha256"]]}
                        for item in experience["sources"]],
            "symbols": [{key: item[key] for key in ("path", "qualname", "symbol_kind") if key in item}
                        for item in experience["bindings"]],
            "uninterpreted": experience["uninterpreted"],
            "uncaptured_sources": experience["uncaptured_sources"],
            "declared_recipe": claim["recipe"],
            "command_result": None if experience["command_result"] is None else
                {key: item for key, item in experience["command_result"].items()
                 if key in {"command", "argv", "returncode", "stdout", "stderr", "status", "diagnostics", "duration_ms"}},
            "worktree_clean": experience["worktree_clean"], "runtime_unchanged": experience["runtime_unchanged"]}
        items.append({"role": "REFERENCE", "media_type": "application/json", "content_base64url": encode(canonical(content))})
    # Explicit absence and limits are useful local facts, not hidden selection.
    items.append({"role": "REFERENCE", "media_type": "application/json", "content_base64url": encode(canonical({
        "selection_state": recall["state"], "eligible_count": recall["eligible_count"],
        "omitted_by_limit": recall["omitted_by_limit"], "applicability": "UNASSESSED"}))})
    return {"snapshot_ref": source_snapshot_reference(value).to_dict(),
            "information": {"schema_version": "synapse.worker.local-information-input/v1", "items": items}}


def read_frozen_source_experience(origin, *, run_id, intent=None):
    """Reopen the exact run declaration and its original physical experience."""
    if type(origin) is not dict or set(origin) != {"path", "ref", "snapshot_ref"}:
        raise ValueError("source experience lacks its frozen run origin")
    path = Path(origin["path"])
    if not path.is_absolute():
        raise ValueError("frozen run origin needs an absolute location")
    raw = read_regular_bytes(path, maximum_bytes=16 * 1024 * 1024)
    data = json.loads(raw)
    if (raw != canonical(data) or source_ref(raw, data["schema_version"]).to_dict() != origin["ref"]
            or data["schema_version"] != "synapse.stage4.gold.frozen-input/v4"
            or data["declaration"]["run_id"] != run_id
            or path != Path(data["run_root"]) / "experiment.json"):
        raise ValueError("source experience belongs to another frozen run")
    task = GoverningTaskContract.from_dict(data["declaration"]["task_contract"])
    if intent is not None:
        task.validate_intent(intent)
    snapshot = data["source_snapshot"]
    if (source_snapshot_reference(snapshot).to_dict() != origin["snapshot_ref"]
            or snapshot["project_state_root"] != data["project_state_root"]
            or snapshot["project_record_sha256"] != data["project_record_sha256"]):
        raise ValueError("source experience differs from frozen project inputs")
    read_source_snapshot(snapshot, task=task)
    return snapshot
