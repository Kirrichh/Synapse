"""Canonical controlled-change runner and public API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import re

from .application import ApplicationResult, apply_verified_commit
from .contract import (
    INITIAL_WORKTREE_NOT_CLEAN,
    PATCH_PATH_NOT_REGULAR_FILE,
    TASK_CONTRACT_SCHEMA,
    TASK_PATH_NOT_REGULAR_FILE,
    TaskContract,
    TaskContractError,
    diagnostic_code,
    parse_task_contract_text,
    validate_repo_relative_path,
)
from .prepared_patch import PreparedPatchMetadata, prepared_patch_metadata
from .report import new_run_id, write_report
from .verification import PhaseResult, phase_from_status, run_command, run_expected_command
from .workspace import (
    REGULAR_BLOB_MODES,
    REPRODUCTION_INPUT_MODES,
    ZERO_OID,
    CandidateSnapshot,
    ChangedPath,
    GitWorkspaceError,
    Worktree,
    capture_candidate_snapshot,
    changed_paths,
    cleanup_worktree,
    commit_tree,
    create_detached_worktree,
    diff_candidate_snapshots,
    find_repo_root,
    git,
    load_committed_bytes,
    require_tree_mode,
    resolve_revision,
    sha256_bytes,
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
COMPLETED_LEGACY_SEMANTICS = "COMPLETED_LEGACY_SEMANTICS"
NOT_ATTEMPTED = "NOT_ATTEMPTED"


@dataclass(frozen=True)
class ControlledChangeRequest:
    base: str
    task_path: str
    keep_worktree: bool = False
    report_dir: str | None = None
    environment_kind: str = "UNSPECIFIED"


@dataclass(frozen=True)
class TrustedInputs:
    task_path: str
    task_contract_sha256: str
    patch_path: str
    patch_sha256: str
    patch_bytes: bytes
    reproduction_committed_inputs: tuple[str, ...]
    reproduction_sha256: str
    required_scaffold_paths: tuple[str, ...]
    base_tree: str

    def to_json(self) -> dict[str, object]:
        return {
            "task_contract_sha256": self.task_contract_sha256,
            "patch_path": self.patch_path,
            "patch_sha256": self.patch_sha256,
            "reproduction_committed_inputs": list(self.reproduction_committed_inputs),
            "reproduction_sha256": self.reproduction_sha256,
            "required_scaffold_paths": list(self.required_scaffold_paths),
            "base_tree": self.base_tree,
        }


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
    task_path: str | None = None
    task_schema: str | None = None
    trusted_inputs: TrustedInputs | None = None
    baseline: dict[str, object] = field(default_factory=dict)
    candidate: dict[str, object] = field(default_factory=dict)
    failure_phase: str | None = None
    failure_code: str | None = None


@dataclass
class LifecycleResult:
    outcome: str
    phases: list[PhaseResult]
    verified_commit: str | None
    verified_tree: str | None
    evidence_ref: str | None
    application: ApplicationResult | None
    worktree: Worktree | None
    diagnostics: list[str]
    baseline: dict[str, object]
    candidate: dict[str, object]
    failure_phase: str | None = None
    failure_code: str | None = None


def _validate_environment_kind(kind: str) -> str:
    if kind not in _ALLOWED_ENVIRONMENT_KINDS:
        raise TaskContractError("ENVIRONMENT_KIND_INVALID", f"environment_kind must be one of {sorted(_ALLOWED_ENVIRONMENT_KINDS)}")
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


DIRECT_REF_EXISTS = "DIRECT_REF_EXISTS"
SYMBOLIC_REF_EXISTS = "SYMBOLIC_REF_EXISTS"
REF_ABSENT_AFTER_FAILED_UPDATE = "REF_ABSENT_AFTER_FAILED_UPDATE"
REF_STATE_UNKNOWN = "REF_STATE_UNKNOWN"
_EVIDENCE_REF_OBSERVATION_FORMAT = "%(refname)%09%(objectname)%09%(symref)"
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class EvidenceRefError(RuntimeError):
    """Raised when the evidence ref cannot be created via zero-OID CAS."""

    def __init__(
        self,
        *,
        reason: str,
        attempted_ref: str,
        observed_existing_oid: str | None = None,
        observed_symbolic_target: str | None = None,
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.attempted_ref = attempted_ref
        self.observed_existing_oid = observed_existing_oid
        self.observed_symbolic_target = observed_symbolic_target


def _classify_failed_evidence_ref_observation(attempted_ref: str, stdout: str) -> tuple[str, str | None, str | None, str]:
    exact_rows: list[tuple[str, str, str]] = []
    ignored_rows = 0
    for line in stdout.splitlines():
        if line == "":
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            return REF_STATE_UNKNOWN, None, None, "observation parse failed: non-empty row did not contain exactly three tab-separated fields"
        refname, objectname, symref = fields
        if refname != attempted_ref:
            ignored_rows += 1
            continue
        if not _HEX40_RE.fullmatch(objectname):
            return REF_STATE_UNKNOWN, None, None, f"observation parse failed: exact ref {attempted_ref} had non-40-hex objectname"
        exact_rows.append((refname, objectname, symref))

    if len(exact_rows) > 1:
        return REF_STATE_UNKNOWN, None, None, f"observation parse failed: exact ref {attempted_ref} appeared more than once"
    if not exact_rows:
        suffix = f"; ignored {ignored_rows} non-exact row(s)" if ignored_rows else ""
        return REF_ABSENT_AFTER_FAILED_UPDATE, None, None, f"observation found no exact ref {attempted_ref}{suffix}"

    _refname, objectname, symref = exact_rows[0]
    if symref:
        return SYMBOLIC_REF_EXISTS, None, symref, f"observation found symbolic ref {attempted_ref} -> {symref}"
    return DIRECT_REF_EXISTS, objectname, None, f"observation found direct ref {attempted_ref} at {objectname}"


def _observe_failed_evidence_ref(repo_root: Path, attempted_ref: str) -> tuple[str, str | None, str | None, str]:
    observed = git(["for-each-ref", f"--format={_EVIDENCE_REF_OBSERVATION_FORMAT}", "--", attempted_ref], repo_root, check=False)
    if observed.returncode != 0:
        detail = observed.stderr.strip() or observed.stdout.strip() or "git for-each-ref observation failed without output"
        return REF_STATE_UNKNOWN, None, None, f"observation failed with rc={observed.returncode}: {detail}"
    return _classify_failed_evidence_ref_observation(attempted_ref, observed.stdout)


def _create_evidence_ref(repo_root: Path, run_id: str, verified_commit: str) -> str:
    safe_run_id = re.sub(r"[^a-zA-Z0-9._-]", "-", run_id)
    ref = f"refs/synapse/change/evidence/{safe_run_id}"
    created = git(["update-ref", "--no-deref", ref, verified_commit, ZERO_OID], repo_root, check=False)
    if created.returncode != 0:
        update_detail = created.stderr.strip() or created.stdout.strip() or "git update-ref CAS failed"
        reason, observed_oid, observed_symref, observation_detail = _observe_failed_evidence_ref(repo_root, ref)
        raise EvidenceRefError(
            reason=reason,
            attempted_ref=ref,
            observed_existing_oid=observed_oid,
            observed_symbolic_target=observed_symref,
            detail=(
                f"EVIDENCE_REF_CAS_FAILED: evidence ref {ref} already exists or "
                f"cannot be created via zero-OID CAS with --no-deref: "
                f"update-ref failed with rc={created.returncode}: {update_detail}; "
                f"{observation_detail}"
            ),
        )
    return ref


def _empty_lifecycle(outcome: str, diagnostics: list[str], *, failure_phase: str | None = None, failure_code: str | None = None) -> LifecycleResult:
    return LifecycleResult(
        outcome=outcome,
        phases=[],
        verified_commit=None,
        verified_tree=None,
        evidence_ref=None,
        application=None,
        worktree=None,
        diagnostics=diagnostics,
        baseline={"initial_integrity_status": NOT_ATTEMPTED, "commands": [], "status": NOT_ATTEMPTED, "mutation_paths": []},
        candidate={"prepared_snapshot_summary": None, "final_snapshot_summary": None, "integrity_status": NOT_ATTEMPTED, "mutation_diagnostics": []},
        failure_phase=failure_phase,
        failure_code=failure_code,
    )


def _scope_diagnostics(changes: tuple[ChangedPath, ...], task: TaskContract) -> list[str]:
    diagnostics: list[str] = []
    for change in changes:
        if change.kind.value == "RENAMED":
            if change.old_path and not task.allowed_scope.allows_path(change.old_path):
                diagnostics.append(f"OUT_OF_SCOPE_RENAME_SOURCE: {change.old_path}")
            if not task.allowed_scope.allows_path(change.new_path):
                diagnostics.append(f"OUT_OF_SCOPE_RENAME_DESTINATION: {change.new_path}")
        elif change.kind.value == "COPIED":
            if change.old_path and not task.allowed_scope.allows_path(change.old_path):
                diagnostics.append(f"OUT_OF_SCOPE_COPY_SOURCE: {change.old_path}")
            if not task.allowed_scope.allows_path(change.new_path):
                diagnostics.append(f"OUT_OF_SCOPE_COPY_DESTINATION: {change.new_path}")
        elif change.kind.value == "DELETED":
            if not task.allowed_scope.allows_path(change.new_path):
                diagnostics.append(f"OUT_OF_SCOPE_DELETED_PATH: {change.new_path}")
        else:
            for path in change.affected_paths():
                if not task.allowed_scope.allows_path(path):
                    diagnostics.append(f"OUT_OF_SCOPE_PATH: {change.kind.value}: {path}")
    return diagnostics


def _mutation_paths(changes: tuple[ChangedPath, ...]) -> list[str]:
    paths: set[str] = set()
    for change in changes:
        paths.update(path for path in change.affected_paths() if path is not None)
    return sorted(paths)


def _reproduction_hash(repo_root: Path, base_sha: str, committed_inputs: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    for rel_path in committed_inputs:
        require_tree_mode(repo_root, base_sha, rel_path, REPRODUCTION_INPUT_MODES, "REPRODUCTION_INPUTS_INVALID")
        h.update(rel_path.encode("utf-8") + b"\0")
        h.update(load_committed_bytes(repo_root, base_sha, rel_path) + b"\0")
    return h.hexdigest()


def _trusted_inputs(repo_root: Path, base_sha: str, task_path: str, task_bytes: bytes, task: TaskContract) -> TrustedInputs:
    require_tree_mode(repo_root, base_sha, task_path, REGULAR_BLOB_MODES, TASK_PATH_NOT_REGULAR_FILE)
    require_tree_mode(repo_root, base_sha, task.patch_path, REGULAR_BLOB_MODES, PATCH_PATH_NOT_REGULAR_FILE)
    missing = verify_scaffold_committed(repo_root, base_sha, task.required_scaffold_paths)
    if missing:
        raise TaskContractError("SCAFFOLD_NOT_COMMITTED", ", ".join(missing))
    patch_bytes = load_committed_bytes(repo_root, base_sha, task.patch_path)
    reproduction_sha256 = _reproduction_hash(repo_root, base_sha, task.reproduction.committed_inputs)
    return TrustedInputs(
        task_path=task_path,
        task_contract_sha256=sha256_bytes(task_bytes),
        patch_path=task.patch_path,
        patch_sha256=sha256_bytes(patch_bytes),
        patch_bytes=patch_bytes,
        reproduction_committed_inputs=task.reproduction.committed_inputs,
        reproduction_sha256=reproduction_sha256,
        required_scaffold_paths=task.required_scaffold_paths,
        base_tree=commit_tree(repo_root, base_sha),
    )


def _execute_task_lifecycle(task: TaskContract, trusted: TrustedInputs, repo_root: Path, base_sha: str, run_id: str) -> LifecycleResult:
    phases: list[PhaseResult] = []
    diagnostics: list[str] = []
    verified_commit: str | None = None
    verified_tree: str | None = None
    evidence_ref: str | None = None
    application: ApplicationResult | None = None
    worktree: Worktree | None = None
    baseline: dict[str, object] = {"initial_integrity_status": NOT_ATTEMPTED, "commands": [], "status": NOT_ATTEMPTED, "mutation_paths": []}
    candidate: dict[str, object] = {"prepared_snapshot_summary": None, "final_snapshot_summary": None, "integrity_status": NOT_ATTEMPTED, "mutation_diagnostics": []}

    base_parent_diagnostics = _base_parent_diagnostics(repo_root, base_sha)
    if base_parent_diagnostics:
        diagnostics.extend(base_parent_diagnostics)
        return LifecycleResult("INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "base_parent_integrity", "BASE_PARENT_UNSUPPORTED")

    worktree = create_detached_worktree(repo_root, base_sha)

    initial_changes = changed_paths(worktree.path)
    if initial_changes:
        mutation_paths = _mutation_paths(initial_changes)
        baseline.update({"initial_integrity_status": "FAIL", "status": NOT_ATTEMPTED, "mutation_paths": mutation_paths})
        phases.append(phase_from_status("initial_worktree_integrity", "FAIL", [INITIAL_WORKTREE_NOT_CLEAN, *mutation_paths]))
        return LifecycleResult("INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "initial_worktree_integrity", INITIAL_WORKTREE_NOT_CLEAN)
    baseline["initial_integrity_status"] = "PASS"
    phases.append(phase_from_status("initial_worktree_integrity", "PASS", []))

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.before, "reproduction_before")
    phases.append(phase)
    if phase.status != "PASS":
        return LifecycleResult("BASELINE_PREEXISTING_FAILURE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "reproduction_before", "BASELINE_PREEXISTING_FAILURE")

    baseline_commands: list[dict[str, object]] = []
    for idx, command in enumerate(task.baseline_commands, start=1):
        phase = run_command(command, worktree.path, f"baseline_{idx}")
        phases.append(phase)
        baseline_commands.append({"name": phase.name, "command": list(command), "status": phase.status, "diagnostics": phase.diagnostics})
        baseline["commands"] = baseline_commands
        if phase.status != "PASS":
            return LifecycleResult("BASELINE_PREEXISTING_FAILURE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, phase.name, "BASELINE_PREEXISTING_FAILURE")
    baseline["commands"] = baseline_commands

    baseline_changes = changed_paths(worktree.path)
    if baseline_changes:
        mutation_paths = _mutation_paths(baseline_changes)
        baseline.update({"status": "FAIL", "mutation_paths": mutation_paths})
        phases.append(phase_from_status("baseline_integrity", "FAIL", [f"baseline mutation: {path}" for path in mutation_paths]))
        return LifecycleResult("BASELINE_MUTATED_WORKTREE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "baseline_integrity", "BASELINE_MUTATED_WORKTREE")
    baseline["status"] = "PASS"
    phases.append(phase_from_status("baseline_integrity", "PASS", []))

    phase = run_command(["git", "apply", "--check", "--recount", "-"], worktree.path, "apply_patch_check", input_bytes=trusted.patch_bytes)
    phases.append(phase)
    if phase.status != "PASS":
        return LifecycleResult("PATCH_REJECTED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "apply_patch_check", "PATCH_REJECTED")

    phase = run_command(["git", "apply", "--recount", "-"], worktree.path, "apply_patch", input_bytes=trusted.patch_bytes)
    phases.append(phase)
    if phase.status != "PASS":
        return LifecycleResult("PATCH_REJECTED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "apply_patch", "PATCH_REJECTED")

    after_patch_changes = changed_paths(worktree.path)
    scope_diagnostics = _scope_diagnostics(after_patch_changes, task)
    phases.append(phase_from_status("scope_check_after_patch", "FAIL" if scope_diagnostics else "PASS", scope_diagnostics))
    if scope_diagnostics:
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "scope_check_after_patch", "OUT_OF_SCOPE_PATH")

    prepared_candidate = capture_candidate_snapshot(worktree.path)
    candidate["prepared_snapshot_summary"] = prepared_candidate.summary()

    phase = run_expected_command(task.reproduction.command, worktree.path, task.reproduction.after, "reproduction_after")
    phases.append(phase)
    if phase.status != "PASS":
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "reproduction_after", "VERIFICATION_FAILED")

    for idx, command in enumerate(task.acceptance_commands, start=1):
        phase = run_command(command, worktree.path, f"acceptance_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, phase.name, "VERIFICATION_FAILED")

    for idx, command in enumerate(task.full_suite_commands, start=1):
        phase = run_command(command, worktree.path, f"full_suite_{idx}")
        phases.append(phase)
        if phase.status != "PASS":
            return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, phase.name, "VERIFICATION_FAILED")

    final_changes = changed_paths(worktree.path)
    final_scope_diagnostics = _scope_diagnostics(final_changes, task)
    phases.append(phase_from_status("scope_check_before_commit", "FAIL" if final_scope_diagnostics else "PASS", final_scope_diagnostics))
    if final_scope_diagnostics:
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "scope_check_before_commit", "OUT_OF_SCOPE_PATH")

    final_candidate = capture_candidate_snapshot(worktree.path)
    candidate["final_snapshot_summary"] = final_candidate.summary()
    diff = diff_candidate_snapshots(prepared_candidate, final_candidate)
    if diff.has_changes():
        mutation_diagnostics = ["verification mutated prepared candidate delta", *diff.diagnostics()]
        candidate.update({"integrity_status": "FAIL", "mutation_diagnostics": mutation_diagnostics})
        phases.append(phase_from_status("candidate_integrity", "FAIL", mutation_diagnostics))
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "candidate_integrity", "VERIFICATION_MUTATED_CANDIDATE")
    candidate.update({"integrity_status": "PASS", "mutation_diagnostics": []})
    phases.append(phase_from_status("candidate_integrity", "PASS", []))

    phase, verified_commit, verified_tree = _commit_verified_change(repo_root, worktree.path, base_sha, task.commit_message)
    phases.append(phase)
    if phase.status != "PASS" or verified_commit is None or verified_tree is None:
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "verified_commit", "VERIFIED_COMMIT_FAILED")

    commit_diagnostics = _validate_verified_commit(repo_root, verified_commit, base_sha, verified_tree)
    phases.append(phase_from_status("verified_commit_integrity", "FAIL" if commit_diagnostics else "PASS", commit_diagnostics))
    if commit_diagnostics:
        return LifecycleResult("VERIFICATION_FAILED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "verified_commit_integrity", "VERIFIED_COMMIT_INTEGRITY_FAILED")

    try:
        evidence_ref = _create_evidence_ref(repo_root, run_id, verified_commit)
    except EvidenceRefError as exc:
        evidence_diagnostics = [str(exc), f"reason={exc.reason}"]
        phases.append(phase_from_status("evidence_ref", "FAIL", evidence_diagnostics))
        return LifecycleResult("INTERNAL_ERROR", phases, verified_commit, verified_tree, None, application, worktree, [*diagnostics, *evidence_diagnostics], baseline, candidate, "evidence_ref", "EVIDENCE_REF_CAS_FAILED")
    phases.append(phase_from_status("evidence_ref", "PASS", []))

    application = apply_verified_commit(repo_root, task.target_ref, base_sha, verified_commit, evidence_ref=evidence_ref)
    app_phase = phase_from_status("application", application.status, application.diagnostics)
    phases.append(app_phase)
    if application.status == "APPLIED":
        return LifecycleResult("APPLIED", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate)
    if application.status == "APPLICATION_STALE_BASE":
        return LifecycleResult("APPLICATION_STALE_BASE", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "application", "APPLICATION_STALE_BASE")
    diagnostics.extend(application.diagnostics)
    return LifecycleResult("INTERNAL_ERROR", phases, verified_commit, verified_tree, evidence_ref, application, worktree, diagnostics, baseline, candidate, "application", application.status)


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
    task_path: str | None = None
    trusted: TrustedInputs | None = None
    baseline: dict[str, object] = {"initial_integrity_status": NOT_ATTEMPTED, "commands": [], "status": NOT_ATTEMPTED, "mutation_paths": []}
    candidate: dict[str, object] = {"prepared_snapshot_summary": None, "final_snapshot_summary": None, "integrity_status": NOT_ATTEMPTED, "mutation_diagnostics": []}
    failure_phase: str | None = None
    failure_code: str | None = None
    report_path: Path | None = None

    try:
        environment_kind = _validate_environment_kind(request.environment_kind)
        task_path = validate_repo_relative_path(request.task_path, "task_path")
        base_sha = resolve_revision(repo_root, request.base)
        require_tree_mode(repo_root, base_sha, task_path, REGULAR_BLOB_MODES, TASK_PATH_NOT_REGULAR_FILE)
        task_bytes = load_committed_bytes(repo_root, base_sha, task_path)
        task = parse_task_contract_text(task_bytes.decode("utf-8"), base_revision=base_sha)
        target_ref = task.target_ref
        trusted = _trusted_inputs(repo_root, base_sha, task_path, task_bytes, task)
        lifecycle = _execute_task_lifecycle(task, trusted, repo_root, base_sha, run_id)
        outcome = lifecycle.outcome
        phases = lifecycle.phases
        verified_commit = lifecycle.verified_commit
        verified_tree = lifecycle.verified_tree
        evidence_ref = lifecycle.evidence_ref
        application = lifecycle.application
        worktree = lifecycle.worktree
        diagnostics = lifecycle.diagnostics
        baseline = lifecycle.baseline
        candidate = lifecycle.candidate
        failure_phase = lifecycle.failure_phase
        failure_code = lifecycle.failure_code
    except TaskContractError as exc:
        environment_kind = request.environment_kind
        failure_code = diagnostic_code(exc)
        failure_phase = "task_contract"
        diagnostics.append(f"TASK_CONTRACT_ERROR: {exc}")
        outcome = "INTERNAL_ERROR"
    except Exception as exc:  # report internal failures instead of bare tracebacks
        environment_kind = request.environment_kind
        failure_code = type(exc).__name__
        failure_phase = "internal"
        diagnostics.append(f"INTERNAL_ERROR: {type(exc).__name__}: {exc}")
        outcome = "INTERNAL_ERROR"
    finally:
        try:
            cleanup_status = cleanup_worktree(worktree, request.keep_worktree)
        except Exception as exc:
            cleanup_status = "CLEANUP_FAILED"
            diagnostics.append(f"cleanup failed: {type(exc).__name__}: {exc}")
            outcome = "INTERNAL_ERROR"
            failure_phase = "cleanup"
            failure_code = "CLEANUP_FAILED"
        try:
            report_path = write_report(
                repo_root=repo_root,
                report_dir=request.report_dir,
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
                worktree_path=str(worktree.path) if worktree else None,
                cleanup_status=cleanup_status,
                diagnostics=diagnostics,
                base_revision=base_sha,
                target_ref=target_ref,
                environment_kind=environment_kind,
                trusted_inputs=trusted,
                baseline=baseline,
                candidate=candidate,
                failure_phase=failure_phase,
                failure_code=failure_code,
            )
        except Exception as exc:
            diagnostics.append(f"report failed: {type(exc).__name__}: {exc}")
            outcome = "INTERNAL_ERROR"
            failure_phase = "report"
            failure_code = "REPORT_WRITE_FAILED"

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
        task_path=task_path,
        task_schema=task.schema if task else None,
        trusted_inputs=trusted,
        baseline=baseline,
        candidate=candidate,
        failure_phase=failure_phase,
        failure_code=failure_code,
    )
