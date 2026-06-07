"""CLI orchestration for the Personal Slice Variant A runner."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .application import ApplicationResult, apply_verified_commit
from .git_workspace import (
    Worktree,
    changed_files,
    cleanup_worktree,
    create_detached_worktree,
    find_repo_root,
    git,
    resolve_revision,
    verify_scaffold_committed,
)
from .llm_gateway import prepared_patch_metadata
from .report import new_run_id, write_report
from .task_contract import TaskContract, TaskContractError, load_task_contract
from .verification import PhaseResult, run_command, run_expected_command

EXIT_CODES = {
    "APPLIED": 0,
    "PATCH_REJECTED": 11,
    "VERIFICATION_FAILED": 12,
    "APPLICATION_STALE_BASE": 20,
    "INTERNAL_ERROR": 30,
}


def _phase_from_status(name: str, status: str, diagnostics: list[str]) -> PhaseResult:
    return PhaseResult(name=name, status=status, command=None, returncode=None, stdout="", stderr="", duration_ms=0, diagnostics=diagnostics)


def _commit_verified_change(worktree_path: Path, commit_message: str) -> tuple[PhaseResult, str | None]:
    git(["add", "-A"], worktree_path)
    result = run_command(["git", "commit", "-m", commit_message], worktree_path, "verified_commit")
    if result.status != "PASS":
        return result, None
    verified = git(["rev-parse", "HEAD"], worktree_path).stdout.strip()
    return result, verified


def _run_task(task: TaskContract, repo_root: Path, keep_worktree: bool, run_id: str) -> tuple[str, list[PhaseResult], str | None, ApplicationResult | None, Worktree | None, list[str]]:
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None

    base_sha = resolve_revision(repo_root, task.base_revision)
    missing = verify_scaffold_committed(repo_root, base_sha, task.required_scaffold_paths)
    if missing:
        diagnostics.extend(["SCAFFOLD_NOT_COMMITTED", *missing])
        return "INTERNAL_ERROR", phases, verified_commit, application, worktree, diagnostics

    worktree = create_detached_worktree(repo_root, base_sha)
    patch_path = worktree.path / task.patch_path

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.before, "reproduction_before")
    phases.append(phase)
    if phase.status != "PASS":
        return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    phase = run_command(["git", "apply", "--check", "--recount", str(patch_path)], worktree.path, "apply_patch_check")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, application, worktree, diagnostics

    phase = run_command(["git", "apply", "--recount", str(patch_path)], worktree.path, "apply_patch")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, application, worktree, diagnostics

    files = changed_files(worktree.path)
    out_of_scope = [path for path in files if path not in task.allowed_scope]
    scope_status = "FAIL" if out_of_scope else "PASS"
    scope_diagnostics = [f"out-of-scope change: {path}" for path in out_of_scope]
    phases.append(_phase_from_status("scope_check", scope_status, scope_diagnostics))
    if out_of_scope:
        return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.after, "reproduction_after")
    phases.append(phase)
    if phase.status != "PASS":
        return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    for idx, command in enumerate(task.acceptance_commands, start=1):
        phase = run_command(command, worktree.path, f"acceptance_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    for idx, command in enumerate(task.full_suite_commands, start=1):
        phase = run_command(command, worktree.path, f"full_suite_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    phase, verified_commit = _commit_verified_change(worktree.path, task.commit_message)
    phases.append(phase)
    if phase.status != "PASS" or verified_commit is None:
        return "VERIFICATION_FAILED", phases, verified_commit, application, worktree, diagnostics

    application = apply_verified_commit(repo_root, task.target_ref, base_sha, verified_commit)
    app_phase = _phase_from_status("application", application.status, application.diagnostics)
    phases.append(app_phase)
    if application.status == "APPLIED":
        return "APPLIED", phases, verified_commit, application, worktree, diagnostics
    if application.status == "APPLICATION_STALE_BASE":
        return "APPLICATION_STALE_BASE", phases, verified_commit, application, worktree, diagnostics
    diagnostics.extend(application.diagnostics)
    return "INTERNAL_ERROR", phases, verified_commit, application, worktree, diagnostics


def run_personal_slice(task_path: str, keep_worktree: bool) -> int:
    repo_root = find_repo_root()
    run_id = new_run_id()
    prepared_patch = prepared_patch_metadata()
    task: TaskContract | None = None
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None
    cleanup_status = "NO_WORKTREE_CREATED"
    outcome = "INTERNAL_ERROR"
    base_sha: str | None = None
    target_ref: str | None = None

    try:
        task = load_task_contract(task_path)
        base_sha = resolve_revision(repo_root, task.base_revision)
        target_ref = task.target_ref
        outcome, phases, verified_commit, application, worktree, diagnostics = _run_task(task, repo_root, keep_worktree, run_id)
    except TaskContractError as exc:
        diagnostics.append(f"TASK_CONTRACT_ERROR: {exc}")
        outcome = "INTERNAL_ERROR"
    except Exception as exc:  # report internal failures instead of bare tracebacks
        diagnostics.append(f"INTERNAL_ERROR: {type(exc).__name__}: {exc}")
        outcome = "INTERNAL_ERROR"
    finally:
        try:
            cleanup_status = cleanup_worktree(worktree, keep_worktree)
        except Exception as exc:
            cleanup_status = "CLEANUP_FAILED"
            diagnostics.append(f"cleanup failed: {type(exc).__name__}: {exc}")
            if outcome == "APPLIED":
                outcome = "INTERNAL_ERROR"
        report_path = write_report(
            repo_root=repo_root,
            task=task,
            run_id=run_id,
            outcome=outcome,
            prepared_patch=prepared_patch,
            phases=phases,
            verified_commit=verified_commit,
            application=application,
            worktree_path=str(worktree.path) if worktree else None,
            cleanup_status=cleanup_status,
            diagnostics=diagnostics,
            base_revision=base_sha,
            target_ref=target_ref,
        )
        print(f"Personal Slice outcome: {outcome}")
        print(f"Report: {report_path}")
        if worktree:
            print(f"Worktree: {worktree.path}")

    return EXIT_CODES.get(outcome, EXIT_CODES["INTERNAL_ERROR"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Synapse Personal Slice Variant A")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="run a Personal Slice task contract")
    run_parser.add_argument("task_json", help="path to task.json")
    run_parser.add_argument("--keep-worktree", action="store_true", help="preserve isolated worktree for inspection")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return run_personal_slice(args.task_json, args.keep_worktree)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
