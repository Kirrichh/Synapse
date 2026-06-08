"""CLI orchestration for the Personal Slice runner."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

from .application import ApplicationResult, ZERO_OID, apply_verified_commit
from .git_workspace import (
    GitWorkspaceError,
    Worktree,
    changed_files,
    cleanup_worktree,
    committed_path_exists,
    create_detached_worktree,
    find_repo_root,
    git,
    git_dir,
    read_committed_bytes,
    read_committed_text,
    resolve_revision,
    resolve_tree,
    validate_repo_relative_path,
    verify_scaffold_committed,
)
from .llm_gateway import prepared_patch_metadata
from .report import new_run_id, write_report
from .task_contract import TaskContract, TaskContractError, parse_task_contract_text
from .verification import PhaseResult, run_command, run_expected_command

EXIT_CODES = {
    "APPLIED": 0,
    "PATCH_REJECTED": 11,
    "VERIFICATION_FAILED": 12,
    "BASELINE_PREEXISTING_FAILURE": 13,
    "APPLICATION_STALE_BASE": 20,
    "INTERNAL_ERROR": 30,
}


def _phase_from_status(name: str, status: str, diagnostics: list[str]) -> PhaseResult:
    return PhaseResult(name=name, status=status, command=None, returncode=None, stdout="", stderr="", duration_ms=0, diagnostics=diagnostics)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _committed_file_sha(repo_root: Path, base_sha: str, relative_path: str) -> str:
    return _sha256_bytes(read_committed_bytes(repo_root, base_sha, relative_path))


def _reproduction_sha(repo_root: Path, base_sha: str, task: TaskContract) -> str | None:
    parts: list[bytes] = []
    for arg in task.reproduction.command:
        try:
            rel = validate_repo_relative_path(arg, field="reproduction.command[]")
        except GitWorkspaceError:
            continue
        if committed_path_exists(repo_root, base_sha, rel):
            parts.append(rel.encode("utf-8") + b"\0" + read_committed_bytes(repo_root, base_sha, rel) + b"\0")
    if not parts:
        return None
    return _sha256_bytes(b"".join(parts))


def _commit_verified_change(worktree_path: Path, commit_message: str) -> tuple[PhaseResult, str | None]:
    git(["add", "-A"], worktree_path)
    result = run_command(
        [
            "git",
            "-c",
            "user.name=Synapse Personal Slice",
            "-c",
            "user.email=personal-slice@local.invalid",
            "commit",
            "-m",
            commit_message,
        ],
        worktree_path,
        "verified_commit",
    )
    if result.status != "PASS":
        return result, None
    verified = git(["rev-parse", "HEAD"], worktree_path).stdout.strip()
    return result, verified


def _create_evidence_ref(repo_root: Path, run_id: str, verified_commit: str) -> tuple[PhaseResult, str | None]:
    evidence_ref = f"refs/personal-slice/verified/{run_id}"
    result = git(["update-ref", evidence_ref, verified_commit, ZERO_OID], repo_root, check=False)
    if result.returncode != 0:
        return _phase_from_status(
            "evidence_ref",
            "FAIL",
            [result.stderr.strip() or result.stdout.strip() or "git update-ref evidence CAS failed"],
        ), None
    return _phase_from_status("evidence_ref", "PASS", []), evidence_ref


def _scope_phase(worktree_path: Path, task: TaskContract, phase_name: str, base_sha: str) -> tuple[PhaseResult, list[str]]:
    files = changed_files(worktree_path, base_sha)
    out_of_scope = [path for path in files if not task.allowed_scope.contains(path)]
    status = "FAIL" if out_of_scope else "PASS"
    diagnostics = [f"out-of-scope change: {path}" for path in out_of_scope]
    return _phase_from_status(phase_name, status, diagnostics), out_of_scope


def _run_task(
    task: TaskContract,
    repo_root: Path,
    base_sha: str,
    run_id: str,
) -> tuple[str, list[PhaseResult], str | None, str | None, ApplicationResult | None, Worktree | None, list[str], str | None]:
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    verified_tree: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None
    evidence_ref: str | None = None

    scaffold = tuple(dict.fromkeys((task.patch_path, *task.required_scaffold_paths)))
    missing = verify_scaffold_committed(repo_root, base_sha, scaffold)
    if missing:
        diagnostics.extend(["SCAFFOLD_NOT_COMMITTED", *missing])
        return "INTERNAL_ERROR", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    worktree = create_detached_worktree(repo_root, base_sha)
    patch_path = worktree.path / task.patch_path

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.before, "reproduction_before")
    phases.append(phase)
    if phase.status != "PASS":
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    for idx, command in enumerate(task.baseline_commands, start=1):
        phase = run_command(command, worktree.path, f"baseline_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "BASELINE_PREEXISTING_FAILURE", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase = run_command(["git", "apply", "--check", "--recount", str(patch_path)], worktree.path, "apply_patch_check")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase = run_command(["git", "apply", "--recount", str(patch_path)], worktree.path, "apply_patch")
    phases.append(phase)
    if phase.status != "PASS":
        return "PATCH_REJECTED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase, out_of_scope = _scope_phase(worktree.path, task, "scope_after_patch", base_sha)
    phases.append(phase)
    if out_of_scope:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.after, "reproduction_after")
    phases.append(phase)
    if phase.status != "PASS":
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    for idx, command in enumerate(task.acceptance_commands, start=1):
        phase = run_command(command, worktree.path, f"acceptance_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    for idx, command in enumerate(task.full_suite_commands, start=1):
        phase = run_command(command, worktree.path, f"full_suite_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase, out_of_scope = _scope_phase(worktree.path, task, "scope_before_commit", base_sha)
    phases.append(phase)
    if out_of_scope:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    phase, verified_commit = _commit_verified_change(worktree.path, task.commit_message)
    phases.append(phase)
    if phase.status != "PASS" or verified_commit is None:
        return "VERIFICATION_FAILED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref
    verified_tree = resolve_tree(worktree.path, verified_commit)

    phase, evidence_ref = _create_evidence_ref(repo_root, run_id, verified_commit)
    phases.append(phase)
    if phase.status != "PASS" or evidence_ref is None:
        diagnostics.extend(phase.diagnostics)
        return "INTERNAL_ERROR", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref

    application = apply_verified_commit(repo_root, task.target_ref, base_sha, verified_commit)
    app_phase = _phase_from_status("application", application.status, application.diagnostics)
    phases.append(app_phase)
    if application.status == "APPLIED":
        return "APPLIED", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref
    if application.status == "APPLICATION_STALE_BASE":
        return "APPLICATION_STALE_BASE", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref
    diagnostics.extend(application.diagnostics)
    return "INTERNAL_ERROR", phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref


def _default_report_dir(repo_root: Path) -> Path:
    return git_dir(repo_root) / "personal-slice" / "reports"


def run_personal_slice(
    *,
    base: str,
    task_path: str,
    keep_worktree: bool,
    report_dir: str | None = None,
    environment_kind: str = "UNSPECIFIED",
) -> int:
    repo_root = find_repo_root()
    run_id = new_run_id()
    prepared_patch = prepared_patch_metadata()
    task: TaskContract | None = None
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    verified_tree: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None
    cleanup_status = "NO_WORKTREE_CREATED"
    outcome = "INTERNAL_ERROR"
    base_sha: str | None = None
    base_tree: str | None = None
    target_ref: str | None = None
    evidence_ref: str | None = None
    task_contract_sha256: str | None = None
    patch_sha256: str | None = None
    reproduction_sha256: str | None = None
    normalized_task_path: str | None = None
    reports_dir = Path(report_dir) if report_dir else _default_report_dir(repo_root)

    try:
        normalized_task_path = validate_repo_relative_path(task_path, field="task")
        base_sha = resolve_revision(repo_root, base)
        base_tree = resolve_tree(repo_root, base_sha)
        task_bytes = read_committed_bytes(repo_root, base_sha, normalized_task_path)
        task_contract_sha256 = _sha256_bytes(task_bytes)
        task = parse_task_contract_text(task_bytes.decode("utf-8"))
        target_ref = task.target_ref
        if task.deprecated_base_revision is not None:
            deprecated_sha = resolve_revision(repo_root, task.deprecated_base_revision)
            if deprecated_sha != base_sha:
                raise TaskContractError("deprecated base_revision does not match CLI --base")
        patch_sha256 = _committed_file_sha(repo_root, base_sha, task.patch_path)
        reproduction_sha256 = _reproduction_sha(repo_root, base_sha, task)
        outcome, phases, verified_commit, verified_tree, application, worktree, diagnostics, evidence_ref = _run_task(
            task, repo_root, base_sha, run_id
        )
    except (TaskContractError, GitWorkspaceError, UnicodeDecodeError) as exc:
        diagnostics.append(str(exc))
        outcome = "INTERNAL_ERROR"
    except Exception as exc:  # Defensive report-on-any-outcome path.
        diagnostics.append(f"unexpected error: {exc}")
        outcome = "INTERNAL_ERROR"
    finally:
        worktree_path = str(worktree.path) if worktree else None
        cleanup_status = cleanup_worktree(worktree, keep_worktree)
        report_path = write_report(
            repo_root,
            task,
            run_id,
            outcome,
            prepared_patch,
            phases,
            verified_commit,
            application,
            worktree_path,
            cleanup_status,
            diagnostics,
            report_dir=reports_dir,
            task_path=normalized_task_path,
            task_contract_sha256=task_contract_sha256,
            patch_sha256=patch_sha256,
            reproduction_sha256=reproduction_sha256,
            base_commit=base_sha,
            base_tree=base_tree,
            verified_tree=verified_tree,
            evidence_ref=evidence_ref,
            evidence_ref_sha=verified_commit if evidence_ref else None,
            environment_kind=environment_kind,
            target_ref=target_ref,
        )
        print(f"Personal Slice outcome: {outcome}")
        print(f"Report: {report_path}")
        if verified_commit:
            print(f"Verified commit: {verified_commit}")
        if evidence_ref:
            print(f"Evidence ref: {evidence_ref}")
        if application:
            print(f"Application: {application.status}")
    return EXIT_CODES.get(outcome, EXIT_CODES["INTERNAL_ERROR"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m personal_slice")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run a committed Personal Slice task")
    run.add_argument("--base", required=True, help="trusted base commit or ref")
    run.add_argument("--task", required=True, help="repo-relative committed task JSON path")
    run.add_argument("--keep-worktree", action="store_true", help="preserve the detached worktree for inspection")
    run.add_argument("--report-dir", help="directory for JSON runtime reports; defaults under .git/personal-slice/reports")
    run.add_argument("--environment-kind", default="UNSPECIFIED", choices=["UNSPECIFIED", "LOCAL"], help="operator-declared environment kind")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run_personal_slice(
            base=args.base,
            task_path=args.task,
            keep_worktree=args.keep_worktree,
            report_dir=args.report_dir,
            environment_kind=args.environment_kind,
        )
    return 30


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
