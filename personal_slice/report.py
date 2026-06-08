"""JSON report creation for Personal Slice."""

from __future__ import annotations

from pathlib import Path
import json
import uuid

from .application import ApplicationResult
from .llm_gateway import PreparedPatchMetadata
from .task_contract import TaskContract
from .verification import PhaseResult

SCHEMA = "personal_slice.report/v0.3"


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
    *,
    report_dir: str | Path,
    task_path: str | None = None,
    task_contract_sha256: str | None = None,
    patch_sha256: str | None = None,
    reproduction_sha256: str | None = None,
    base_commit: str | None = None,
    base_tree: str | None = None,
    verified_tree: str | None = None,
    evidence_ref: str | None = None,
    evidence_ref_sha: str | None = None,
    environment_kind: str = "UNSPECIFIED",
    target_ref: str | None = None,
) -> Path:
    reports_dir = Path(report_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.task_id if task else "unknown-task"
    path = reports_dir / f"{task_id}-{run_id}.json"
    payload = {
        "schema": SCHEMA,
        "run_id": run_id,
        "task_id": task.task_id if task else None,
        "task_class": task.task_class if task else None,
        "task_path": task_path,
        "task_contract_sha256": task_contract_sha256,
        "patch_sha256": patch_sha256,
        "reproduction_sha256": reproduction_sha256,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "base_revision": base_commit,
        "target_ref": target_ref or (task.target_ref if task else None),
        "outcome": outcome,
        "prepared_patch": prepared_patch.to_json(),
        "phases": [phase.to_json() for phase in phases],
        "verified_commit": verified_commit,
        "verified_tree": verified_tree,
        "evidence_ref": evidence_ref,
        "evidence_ref_sha": evidence_ref_sha,
        "environment_kind": environment_kind,
        "worktree_durability": cleanup_status,
        "application_scope": "LOCAL_REF_ONLY",
        "remote_updated": False,
        "application": application.to_json() if application else None,
        "worktree_path": worktree_path,
        "cleanup_status": cleanup_status,
        "diagnostics": diagnostics,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
