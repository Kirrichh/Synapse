"""JSON report state for canonical controlled changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import uuid

from .application import ApplicationResult
from .contract import TaskContract
from .outcomes import COMPLETED_LEGACY_SEMANTICS, NOT_ATTEMPTED, exit_code_for
from .prepared_patch import PreparedPatchMetadata
from .verification import PhaseResult
from .workspace import git_dir

# legacy namespace preserved for report family compatibility
SCHEMA = "personal_slice.report/v0.5.0"
# COMPLETED_LEGACY_SEMANTICS means the phase executed and is represented in
# the report; it does not mean the phase had a successful business result.
# NOT_ATTEMPTED is only used for phases that did not run.


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def default_report_dir(repo_root: str | Path) -> Path:
    gd = git_dir(repo_root)
    if not gd.is_absolute():
        gd = Path(repo_root) / gd
    return gd / "synapse" / "change" / "reports"


def _application_json(application: ApplicationResult | None) -> dict[str, object]:
    if application is None:
        return {
            "status": NOT_ATTEMPTED,
            "result": None,
            "result_status": None,
            "failure_code": None,
        }
    data = application.to_json()
    data["status"] = COMPLETED_LEGACY_SEMANTICS
    data["result_status"] = application.status
    data["failure_code"] = application.failure_code
    return data


def _legacy_status(executed: bool) -> str:
    return COMPLETED_LEGACY_SEMANTICS if executed else NOT_ATTEMPTED


def _default_evidence_json() -> dict[str, object]:
    return {
        "status": NOT_ATTEMPTED,
        "result_status": None,
        "evidence_ref": None,
        "attempted_ref": None,
        "observed_existing_oid": None,
        "observed_symbolic_target": None,
    }


def _evidence_json(evidence: Any | None, evidence_ref: str | None) -> dict[str, object]:
    data = _default_evidence_json() if evidence is None else evidence.to_json()
    structured_ref = data.get("evidence_ref")
    if evidence_ref is not None and evidence_ref != structured_ref:
        raise ValueError(
            "legacy evidence_ref does not match structured evidence.evidence_ref"
        )
    return data


def build_report_payload(
    *,
    task: TaskContract | None,
    task_path: str | None,
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
    trusted_inputs: Any | None = None,
    baseline: dict[str, object] | None = None,
    candidate: dict[str, object] | None = None,
    failure_phase: str | None = None,
    failure_code: str | None = None,
    evidence: Any | None = None,
) -> dict[str, object]:
    trusted_payload = trusted_inputs.to_json() if trusted_inputs else {}
    trusted_section = {
        "base_commit": base_revision or (task.base_revision if task else None),
        "base_tree": trusted_payload.get("base_tree"),
        "task_contract_sha256": trusted_payload.get("task_contract_sha256"),
        "patch_path": trusted_payload.get("patch_path"),
        "patch_sha256": trusted_payload.get("patch_sha256"),
        "reproduction_committed_inputs": trusted_payload.get("reproduction_committed_inputs", []),
        "reproduction_sha256": trusted_payload.get("reproduction_sha256"),
        "required_scaffold_paths": trusted_payload.get("required_scaffold_paths", []),
    }
    evidence_payload = _evidence_json(evidence, evidence_ref)
    payload_evidence_ref = evidence_payload["evidence_ref"]
    lifecycle = {"outcome": outcome, "exit_code": exit_code_for(outcome)}
    payload: dict[str, object] = {
        "schema": SCHEMA,
        "run": {"run_id": run_id, "environment_kind": environment_kind},
        "task": {
            "task_id": task.task_id if task else None,
            "task_class": task.task_class if task else None,
            "task_path": task_path,
            "task_schema": task.schema if task else None,
            "target_ref": target_ref or (task.target_ref if task else None),
        },
        "trusted_inputs": trusted_section,
        "baseline": baseline or {"initial_integrity_status": NOT_ATTEMPTED, "commands": [], "status": NOT_ATTEMPTED, "mutation_paths": []},
        "candidate": candidate or {"prepared_snapshot_summary": None, "final_snapshot_summary": None, "integrity_status": NOT_ATTEMPTED, "mutation_diagnostics": []},
        "verified_result": {
            "status": _legacy_status(verified_commit is not None),
            "verified_commit": verified_commit,
            "verified_tree": verified_tree,
        },
        "evidence": evidence_payload,
        "application": _application_json(application),
        "cleanup": {"status": COMPLETED_LEGACY_SEMANTICS, "cleanup_status": cleanup_status, "worktree_path": worktree_path},
        "failure": {"failure_phase": failure_phase, "failure_code": failure_code},
        "lifecycle": lifecycle,
        "phases": [phase.to_json() for phase in phases],
        "diagnostics": diagnostics,
        # Deprecated compatibility projections retained for current Python/report consumers;
        # values are projected from the structured lifecycle sections above.
        "run_id": run_id,
        "task_id": task.task_id if task else None,
        "task_class": task.task_class if task else None,
        "base_revision": base_revision or (task.base_revision if task else None),
        "target_ref": target_ref or (task.target_ref if task else None),
        "environment_kind": environment_kind,
        "outcome": outcome,
        "prepared_patch": prepared_patch.to_json(),
        "verified_commit": verified_commit,
        "verified_tree": verified_tree,
        "evidence_ref": payload_evidence_ref,
        "application_attempted": application.application_attempted if application else False,
        "worktree_path": worktree_path,
        "cleanup_status": cleanup_status,
    }
    return payload


def write_report(
    repo_root: str | Path,
    *,
    report_dir: str | None,
    task: TaskContract | None,
    task_path: str | None,
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
    trusted_inputs: Any | None = None,
    baseline: dict[str, object] | None = None,
    candidate: dict[str, object] | None = None,
    failure_phase: str | None = None,
    failure_code: str | None = None,
    evidence: Any | None = None,
) -> Path:
    reports_dir = Path(report_dir) if report_dir else default_report_dir(repo_root)
    reports_dir.mkdir(parents=True, exist_ok=True)
    task_id = task.task_id if task else "unknown-task"
    path = reports_dir / f"{task_id}-{run_id}.json"
    payload = build_report_payload(
        task=task,
        task_path=task_path,
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
        trusted_inputs=trusted_inputs,
        baseline=baseline,
        candidate=candidate,
        failure_phase=failure_phase,
        failure_code=failure_code,
        evidence=evidence,
    )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
