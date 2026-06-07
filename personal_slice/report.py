"""JSON report creation for Personal Slice."""

from __future__ import annotations

from pathlib import Path
import json
import uuid

from .application import ApplicationResult
from .llm_gateway import PreparedPatchMetadata
from .task_contract import TaskContract
from .verification import PhaseResult

SCHEMA = "personal_slice.report/v0.2"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def write_report(
    repo_root: str | Path,
    task: TaskContract | None,
    run_id: str,
    outcome: str,
    prepared_patch: PreparedPatchMetadata,
    phases: list[PhaseResult],
    verified_commit: str | None,
    application: ApplicationResult | None,
    worktree_path: str | None,
    cleanup_status: str,
    diagnostics: list[str],
    base_revision: str | None = None,
    target_ref: str | None = None,
) -> Path:
    root = Path(repo_root)
    reports_dir = root / "personal_slice" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.task_id if task else "unknown-task"
    path = reports_dir / f"{task_id}-{run_id}.json"
    payload = {
        "schema": SCHEMA,
        "run_id": run_id,
        "task_id": task.task_id if task else None,
        "task_class": task.task_class if task else None,
        "base_revision": base_revision or (task.base_revision if task else None),
        "target_ref": target_ref or (task.target_ref if task else None),
        "outcome": outcome,
        "prepared_patch": prepared_patch.to_json(),
        "phases": [phase.to_json() for phase in phases],
        "verified_commit": verified_commit,
        "application": application.to_json() if application else None,
        "worktree_path": worktree_path,
        "cleanup_status": cleanup_status,
        "diagnostics": diagnostics,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
