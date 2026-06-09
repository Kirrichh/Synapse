"""Canonical controlled-change orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from .application import ApplicationResult, apply_verified_commit
from .contract import TaskContract, TaskContractError, parse_task_contract_text, validate_repo_relative_path
from .prepared_patch import PreparedPatchMetadata, prepared_patch_metadata
from .report import new_run_id, write_report
from .verification import PhaseResult, phase_from_status, run_command, run_expected_command
from .workspace import (
    Worktree,
    assert_clean_worktree,
    capture_candidate_snapshot,
    changed_files,
    cleanup_worktree,
    create_detached_worktree,
    find_repo_root,
    git,
    load_committed_text,
    resolve_revision,
    verify_scaffold_committed,
)

EXIT_CODES = {
    "APPLIED": 0,
    "PATCH_REJECTED": 11,
    "VERIFICATION_FAILED": 12,
    "BASELINE_PREEXISTING_FAILURE": 13,
    "BASELINE_MUTATED_WORKTREE": 14,
    "APPLICATION_STALE_BASE": 20,
    "INTERNAL_ERROR": 30,
}
_ALLOWED_ENVIRONMENT_KINDS = {"UNSPECIFIED", "LOCAL", "CI", "TEST"}


@dataclass(frozen=True)
class ControlledChangeRequest:
    base: str
    task_path: str
    keep_worktree: bool = False
    report_dir: str | None = None
    environment_kind: str = "UNSPECIFIED"


@dataclass
class ControlledChangeResult:
    outcome: str
    exit_code: int
    report_path: str | None
    verified_commit: str | None
    verified_tree: str | None
    evidence_ref: str | None
    application: ApplicationResult | None
    cleanup_status: str
    worktree_path: str | None
    phases: list[PhaseResult] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    base_revision: str | None = None
    target_ref: str | None = None
    run_id: str | None = None
    environment_kind: str = "UNSPECIFIED"
    prepared_patch: PreparedPatchMetadata | None = None


def _validate_environment_kind(kind: str) -> str:
    if kind not in _ALLOWED_ENVIRONMENT_KINDS:
        raise TaskContractError(f"environment_kind must be one of {sorted(_ALLOWED_ENVIRONMENT_KINDS)}")
    return kind


def _commit_verified_change(repo_root: Path, worktree_path: Path, base_sha: str, commit_message: str) -> tuple[PhaseResult, str | None, str | None]:
    git(["add", "-A"], worktree_path)
    tree = git(["write-tree"], worktree_path).stdout.strip()
    result = run_command(["git", "commit-tree", tree, "-p", base_sha, "-m", commit_message], repo_root, "verified_commit")
    if result.status != "PASS":
        return result, None, tree
    verified = result.stdout.strip()
    return result, verified, tree


def _validate_verified_commit(repo_root: Path, verified_commit: str, base_sha: str, expected_tree: str) -> list[str]:
    diagnostics: list[str] = []
    parents = git(["show", "-s", "--format=%P", verified_commit], repo_root).stdout.strip().split()
    if len(parents) != 1:
        diagnostics.append("verified commit must have exactly one parent")
    elif parents[0] != base_sha:
        diagnostics.append("verified commit parent does not match resolved base")
    tree = git(["show", "-s", "--format=%T", verified_commit], repo_root).stdout.strip()
    if tree != expected_tree:
        diagnostics.append("verified commit tree does not match candidate tree")
    return diagnostics


def _base_parent_diagnostics(repo_root: Path, base_sha: str) -> list[str]:
    parents = git(["show", "-s", "--format=%P", base_sha], repo_root).stdout.strip().split()
    if len(parents) == 0:
        return ["base commit must not be a root commit"]
    if len(parents) > 1:
        return ["base commit must not be a merge commit"]
    return []


def _create_evidence_ref(repo_root: Path, run_id: str, verified_commit: str) -> str:
    safe_run_id = re.sub(r"[^a-zA-Z0-9._-]", "-", run_id)
    ref = f"refs/synapse/change/evidence/{safe_run_id}"
    git(["update-ref", ref, verified_commit], repo_root)
    return ref


def _execute_task_lifecycle(task: TaskContract, repo_root: Path, base_sha: str, run_id: str) -> tuple[str, list[PhaseResult], str | None, str | None, str | None, ApplicationResult | None, Worktree | None, list[str]]:
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    verified_tree: str | None = None
    evidence_ref: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None

    base_parent_diagnostics = _base_parent_diagnostics(repo_root, base_sha)
    if base_parent_diagnostics:
        diagnostics.extend(base_parent_diagnostics)
        return "INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    missing = verify_scaffold_committed(repo_root, base_sha, task.required_scaffold_paths)
    if missing:
        diagnostics.extend(["SCAFFOLD_NOT_COMMITTED", *missing])
        return "INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    worktree = create_detached_worktree(repo_root, base_sha)
    patch_path = worktree.path / task.patch_path

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.before, "reproduction_before")
    phases.append(phase)
    if phase.status != "PASS":
        return "BASELINE_PREEXISTING_FAILURE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    baseline_snapshot = capture_candidate_snapshot(worktree.path)
    if baseline_snapshot.entries:
        phases.append(phase_from_status("baseline_integrity", "FAIL", [f"baseline mutation: {entry[0]}" for entry in baseline_snapshot.entries]))
        return "BASELINE_MUTATED_WORKTREE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics
    phases.append(phase_from_status("baseline_integrity", "PASS", []))

    phase = run_command(["git", "apply", "--check", "--recount", str(patch_path)], worktree.path, "apply_patch_check")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    phase = run_command(["git", "apply", "--recount", str(patch_path)], worktree.path, "apply_patch")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    files = changed_files(worktree.path)
    out_of_scope = [path for path in files if path not in task.allowed_scope]
    scope_status = "FAIL" if out_of_scope else "PASS"
    scope_diagnostics = [f"out-of-scope change: {path}" for path in out_of_scope]
    phases.append(phase_from_status("scope_check_after_patch", scope_status, scope_diagnostics))
    if out_of_scope:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    prepared_candidate = capture_candidate_snapshot(worktree.path)

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.after, "reproduction_after")
    phases.append(phase)
    if phase.status != "PASS":
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    for idx, command in enumerate(task.acceptance_commands, start=1):
        phase = run_command(command, worktree.path, f"acceptance_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    for idx, command in enumerate(task.full_suite_commands, start=1):
        phase = run_command(command, worktree.path, f"full_suite_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    final_files = changed_files(worktree.path)
    final_out_of_scope = [path for path in final_files if path not in task.allowed_scope]
    phases.append(phase_from_status("scope_check_before_commit", "FAIL" if final_out_of_scope else "PASS", [f"out-of-scope change: {path}" for path in final_out_of_scope]))
    if final_out_of_scope:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    final_candidate = capture_candidate_snapshot(worktree.path)
    if final_candidate != prepared_candidate:
        phases.append(phase_from_status("candidate_integrity", "FAIL", ["verification mutated prepared candidate delta"]))
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics
    phases.append(phase_from_status("candidate_integrity", "PASS", []))

    phase, verified_commit, verified_tree = _commit_verified_change(repo_root, worktree.path, base_sha, task.commit_message)
    phases.append(phase)
    if phase.status != "PASS" or verified_commit is None or verified_tree is None:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    commit_diagnostics = _validate_verified_commit(repo_root, verified_commit, base_sha, verified_tree)
    phases.append(phase_from_status("verified_commit_integrity", "FAIL" if commit_diagnostics else "PASS", commit_diagnostics))
    if commit_diagnostics:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics

    evidence_ref = _create_evidence_ref(repo_root, run_id, verified_commit)
    phases.append(phase_from_status("evidence_ref", "PASS", []))

    application = apply_verified_commit(repo_root, task.target_ref, base_sha, verified_commit, evidence_ref=evidence_ref)
    app_phase = phase_from_status("application", application.status, application.diagnostics)
    phases.append(app_phase)
    if application.status == "APPLIED":
        return "APPLIED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics
    if application.status == "APPLICATION_STALE_BASE":
        return "APPLICATION_STALE_BASE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics
    diagnostics.extend(application.diagnostics)
    return "INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics


def execute_controlled_change(request: ControlledChangeRequest) -> ControlledChangeResult:
    repo_root = find_repo_root()
    run_id = new_run_id()
    prepared_patch = prepared_patch_metadata()
    task: TaskContract | None = None
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    verified_tree: str | None = None
    evidence_ref: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None
    cleanup_status = "NO_WORKTREE_CREATED"
    outcome = "INTERNAL_ERROR"
    base_sha: str | None = None
    target_ref: str | None = None
    report_path: Path | None = None

    try:
        environment_kind = _validate_environment_kind(request.environment_kind)
        task_path = validate_repo_relative_path(request.task_path, "task_path")
        base_sha = resolve_revision(repo_root, request.base)
        task_text = load_committed_text(repo_root, base_sha, task_path)
        task = parse_task_contract_text(task_text, base_revision=base_sha)
        target_ref = task.target_ref
        outcome, phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics = _execute_task_lifecycle(task, repo_root, base_sha, run_id)
    except TaskContractError as exc:
        environment_kind = request.environment_kind
        diagnostics.append(f"TASK_CONTRACT_ERROR: {exc}")
        outcome = "INTERNAL_ERROR"
    except Exception as exc:  # report internal failures instead of bare tracebacks
        environment_kind = request.environment_kind
        diagnostics.append(f"INTERNAL_ERROR: {type(exc).__name__}: {exc}")
        outcome = "INTERNAL_ERROR"
    finally:
        try:
            cleanup_status = cleanup_worktree(worktree, request.keep_worktree)
        except Exception as exc:
            cleanup_status = "CLEANUP_FAILED"
            diagnostics.append(f"cleanup failed: {type(exc).__name__}: {exc}")
            outcome = "INTERNAL_ERROR"
        try:
            report_path = write_report(
                repo_root=repo_root,
                report_dir=request.report_dir,
                task=task,
                run_id=run_id,
                outcome=outcome,
                prepared_patch=prepared_patch,
                phases=phases,
                verified_commit=verified_commit,
                verified_tree=verified_tree,
                evidence_ref=evidence_ref,
                application=application,
                worktree_path=str(worktree.path) if worktree else None,
                cleanup_status=cleanup_status,
                diagnostics=diagnostics,
                base_revision=base_sha,
                target_ref=target_ref,
                environment_kind=environment_kind,
            )
        except Exception as exc:
            diagnostics.append(f"report failed: {type(exc).__name__}: {exc}")
            outcome = "INTERNAL_ERROR"

    return ControlledChangeResult(
        outcome=outcome,
        exit_code=EXIT_CODES.get(outcome, EXIT_CODES["INTERNAL_ERROR"]),
        report_path=str(report_path) if report_path else None,
        verified_commit=verified_commit,
        verified_tree=verified_tree,
        evidence_ref=evidence_ref,
        application=application,
        cleanup_status=cleanup_status,
        worktree_path=str(worktree.path) if worktree else None,
        phases=phases,
        diagnostics=diagnostics,
        base_revision=base_sha,
        target_ref=target_ref,
        run_id=run_id,
        environment_kind=request.environment_kind,
        prepared_patch=prepared_patch,
    )
