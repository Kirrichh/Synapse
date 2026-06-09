"""JSON report state for canonical controlled changes."""

from __future__ import annotations

from pathlib import Path
import json
import uuid

from .application import ApplicationResult
from .contract import TaskContract
from .prepared_patch import PreparedPatchMetadata
from .verification import PhaseResult
from .workspace import git_dir

SCHEMA = "personal_slice.report/v0.3.2"


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def default_report_dir(repo_root: str | Path) -> Path:
    gd = git_dir(repo_root)
    if not gd.is_absolute():
        gd = Path(repo_root) / gd
    return gd / "synapse" / "change" / "reports"


def build_report_payload(
    *,
    task: TaskContract | None,
    run_id: str,
    outcome: str,
    prepared_patch: PreparedPatchMetadata,
    phases: list[PhaseResult],
    verified_commit: str | None,
    verified_tree: str | None,
    evidence_ref: str | None,
    application: ApplicationResult | None,
    worktree_path: str | None,
    cleanup_status: str,
    diagnostics: list[str],
    base_revision: str | None = None,
    target_ref: str | None = None,
    environment_kind: str = "UNSPECIFIED",
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "task_id": task.task_id if task else None,
        "task_class": task.task_class if task else None,
        "base_revision": base_revision or (task.base_revision if task else None),
        "target_ref": target_ref or (task.target_ref if task else None),
        "environment_kind": environment_kind,
        "outcome": outcome,
        "prepared_patch": prepared_patch.to_json(),
        "phases": [phase.to_json() for phase in phases],
        "verified_commit": verified_commit,
        "verified_tree": verified_tree,
        "evidence_ref": evidence_ref,
        "application": application.to_json() if application else None,
        "application_attempted": application.application_attempted if application else False,
        "worktree_path": worktree_path,
        "cleanup_status": cleanup_status,
        "diagnostics": diagnostics,
    }


def write_report(
    repo_root: str | Path,
    *,
    report_dir: str | None,
    task: TaskContract | None,
    run_id: str,
    outcome: str,
    prepared_patch: PreparedPatchMetadata,
    phases: list[PhaseResult],
    verified_commit: str | None,
    verified_tree: str | None,
    evidence_ref: str | None,
    application: ApplicationResult | None,
    worktree_path: str | None,
    cleanup_status: str,
    diagnostics: list[str],
    base_revision: str | None = None,
    target_ref: str | None = None,
    environment_kind: str = "UNSPECIFIED",
) -> Path:
    reports_dir = Path(report_dir) if report_dir else default_report_dir(repo_root)
    reports_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.task_id if task else "unknown-task"
    path = reports_dir / f"{task_id}-{run_id}.json"
    payload = build_report_payload(
        task=task,
        run_id=run_id,
        outcome=outcome,
        prepared_patch=prepared_patch,
        phases=phases,
        verified_commit=verified_commit,
        verified_tree=verified_tree,
        evidence_ref=evidence_ref,
        application=application,
        worktree_path=worktree_path,
        cleanup_status=cleanup_status,
        diagnostics=diagnostics,
        base_revision=base_revision,
        target_ref=target_ref,
        environment_kind=environment_kind,
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
