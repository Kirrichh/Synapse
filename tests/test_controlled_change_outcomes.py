from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import synapse.change.application as application_module
import synapse.change.outcomes as outcomes
import synapse.change.report as report_module
import synapse.change.runner as runner_module
from synapse.change import ControlledChangeRequest
from synapse.change.application import apply_verified_commit
from synapse.change.prepared_patch import PreparedPatchMetadata
from synapse.change.report import build_report_payload
from synapse.change.runner import EvidenceRefError, execute_controlled_change
from synapse.change.workspace import ZERO_OID

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_REF = "refs/heads/controlled/verified"


def run(cmd, cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def ref_sha(repo: Path, ref: str) -> str | None:
    completed = run(["git", "rev-parse", "--verify", ref], repo)
    return completed.stdout.strip() if completed.returncode == 0 else None


def init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")
    (repo / "app.txt").write_text("old\n", encoding="utf-8")
    (repo / "tasks").mkdir()
    (repo / "patches").mkdir()
    (repo / "patches" / "change.patch").write_text(
        "diff --git a/app.txt b/app.txt\n"
        "index 3367afd..3e75765 100644\n"
        "--- a/app.txt\n"
        "+++ b/app.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
        encoding="utf-8",
    )
    task = {
        "schema": "synapse.controlled-change.task/v1",
        "task_id": "outcome-change",
        "task_class": "TEST",
        "base_revision": "HEAD",
        "target_ref": TARGET_REF,
        "allowed_scope": ["app.txt"],
        "patch_path": "patches/change.patch",
        "required_scaffold_paths": ["tasks/task.json", "patches/change.patch"],
        "reproduction": {
            "command": [sys.executable, "-c", "raise SystemExit(0)"],
            "committed_inputs": [],
            "before": {"expected_exit_codes": [0], "timeout_seconds": 10},
            "after": {"expected_exit_codes": [0], "timeout_seconds": 10},
        },
        "acceptance_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "full_suite_commands": [[sys.executable, "-c", "raise SystemExit(0)"]],
        "commit_message": "Apply controlled change",
    }
    (repo / "tasks" / "task.json").write_text(json.dumps(task), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    return repo, git(repo, "rev-parse", "HEAD")


def make_commit(repo: Path, message: str, filename: str = "side.txt") -> str:
    path = repo / filename
    path.write_text(f"{message}\n", encoding="utf-8")
    git(repo, "add", filename)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def read_report(path: str | None) -> dict:
    assert path is not None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def assert_report_consistent(result) -> dict:
    payload = read_report(result.report_path)
    assert payload["schema"] == "personal_slice.report/v0.5.0"
    assert payload["outcome"] == payload["lifecycle"]["outcome"] == result.outcome
    assert payload["lifecycle"]["exit_code"] == outcomes.exit_code_for(result.outcome) == result.exit_code
    assert payload["failure"] == {"failure_phase": result.failure_phase, "failure_code": result.failure_code}
    assert payload["evidence"] == result.evidence.to_json()
    assert payload["evidence_ref"] == payload["evidence"]["evidence_ref"] == result.evidence_ref == result.evidence.evidence_ref
    if result.application is None:
        assert payload["application"]["status"] == outcomes.NOT_ATTEMPTED
        assert payload["application"]["result_status"] is None
        assert payload["application"]["failure_code"] is None
    else:
        assert payload["application"]["status"] == outcomes.COMPLETED_LEGACY_SEMANTICS
        assert payload["application"]["result_status"] == result.application.status
        assert payload["application"]["failure_code"] == result.application.failure_code
    return payload


def report_path_from_output(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("Report: "):
            return line.split(": ", 1)[1]
    raise AssertionError(output)


def outcome_from_output(output: str) -> str:
    for line in output.splitlines():
        if " outcome: " in line:
            return line.rsplit(" outcome: ", 1)[1]
    raise AssertionError(output)


def test_canonical_outcomes_owner_contract():
    assert outcomes.EXIT_CODES == {
        outcomes.APPLIED: 0,
        outcomes.PATCH_REJECTED: 11,
        outcomes.VERIFICATION_FAILED: 12,
        outcomes.BASELINE_PREEXISTING_FAILURE: 13,
        outcomes.BASELINE_MUTATED_WORKTREE: 14,
        outcomes.APPLICATION_STALE_BASE: 20,
        outcomes.POLICY_REFUSED: 21,
        outcomes.SAFETY_STATE_UNKNOWN: 22,
        outcomes.EVIDENCE_CONFLICT: 23,
        outcomes.INTERNAL_ERROR: 30,
    }
    assert len(set(outcomes.EXIT_CODES.values())) == len(outcomes.EXIT_CODES)
    assert outcomes.exit_code_for("UNKNOWN_VALUE") == 30
    assert runner_module.EXIT_CODES is outcomes.EXIT_CODES
    assert outcomes.NOT_ATTEMPTED == "NOT_ATTEMPTED"
    assert outcomes.COMPLETED_LEGACY_SEMANTICS == "COMPLETED_LEGACY_SEMANTICS"
    assert outcomes.EVIDENCE_CREATED == "EVIDENCE_CREATED"
    for name in (
        "UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT",
        "WORKTREE_DISCOVERY_FAILED",
        "WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH",
        "EVIDENCE_REF_ALREADY_EXISTS",
        "EVIDENCE_REF_SYMBOLIC_CONFLICT",
        "EVIDENCE_REF_UPDATE_FAILED",
        "EVIDENCE_REF_STATE_UNKNOWN",
    ):
        assert getattr(outcomes, name)
    runner_source = Path(runner_module.__file__).read_text(encoding="utf-8")
    report_source = Path(report_module.__file__).read_text(encoding="utf-8")
    outcomes_source = Path(outcomes.__file__).read_text(encoding="utf-8")
    assert "EXIT_CODES = {" not in runner_source
    assert "NOT_ATTEMPTED =" not in runner_source
    assert "COMPLETED_LEGACY_SEMANTICS =" not in runner_source
    assert "NOT_ATTEMPTED =" not in report_source
    assert "COMPLETED_LEGACY_SEMANTICS =" not in report_source
    outcomes_imports = [node.module for node in ast.walk(ast.parse(outcomes_source)) if isinstance(node, ast.ImportFrom)]
    assert not {"application", "runner", "report"} & {module or "" for module in outcomes_imports}
    assert "from .runner" not in report_source and "import runner" not in report_source


def test_policy_refusal_main_worktree_result_and_report(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    git(repo, "reset", "--hard", base)
    git(repo, "switch", "-c", "controlled/verified")
    app = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert app.status == outcomes.POLICY_REFUSED
    assert app.failure_code == outcomes.UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT
    assert app.application_attempted is False
    assert app.actual_old_sha is None
    assert app.remote_updated is False
    assert ref_sha(repo, TARGET_REF) == base

    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.POLICY_REFUSED
    assert result.exit_code == 21
    assert result.failure_phase == "application"
    assert result.failure_code == outcomes.UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT
    assert result.application.status == outcomes.POLICY_REFUSED
    assert result.application.failure_code == outcomes.UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT
    assert result.application.application_attempted is False
    assert result.application.actual_old_sha is None
    assert result.application.remote_updated is False
    assert any(str(repo) in diagnostic or "worktree" in diagnostic for diagnostic in result.application.diagnostics)
    assert ref_sha(repo, TARGET_REF) == base
    assert_report_consistent(result)


def test_policy_refusal_linked_worktree_preserves_branch_and_diagnostics(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-b", "controlled/verified", str(linked), base)
    assert git(repo, "symbolic-ref", "-q", "HEAD") != TARGET_REF
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.POLICY_REFUSED
    assert result.exit_code == 21
    assert result.failure_code == outcomes.UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT
    assert result.application.application_attempted is False
    assert result.application.actual_old_sha is None
    assert result.application.remote_updated is False
    diagnostics = "\n".join(result.application.diagnostics)
    assert str(linked).replace("\\", "/") in diagnostics.replace("\\", "/")
    assert ref_sha(repo, TARGET_REF) == base
    assert_report_consistent(result)


def test_discovery_command_failure_is_typed_safety_state_unknown(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    git(repo, "update-ref", TARGET_REF, base)

    def broken_git_binary(args, cwd, **kwargs):
        assert args[:3] == ["worktree", "list", "--porcelain"]
        return subprocess.CompletedProcess(args=["git", *args], returncode=128, stdout=b"", stderr=b"custom text")

    monkeypatch.setattr(application_module, "git_binary", broken_git_binary)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.SAFETY_STATE_UNKNOWN
    assert result.exit_code == 22
    assert result.failure_phase == "application"
    assert result.failure_code == outcomes.WORKTREE_DISCOVERY_FAILED
    assert result.application.status == outcomes.SAFETY_STATE_UNKNOWN
    assert result.application.failure_code == outcomes.WORKTREE_DISCOVERY_FAILED
    assert result.application.application_attempted is False
    assert result.application.actual_old_sha is None
    assert ref_sha(repo, TARGET_REF) == base
    assert_report_consistent(result)


def test_discovery_ambiguity_uses_typed_failure_code(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    git(repo, "update-ref", TARGET_REF, base)
    corrupted = (
        b"worktree /repo/a\x00branch " + TARGET_REF.encode() + b"\x00\x00"
        b"worktree /repo/b\x00branch " + TARGET_REF.encode() + b"\x00\x00"
    )

    def corrupted_git_binary(args, cwd, **kwargs):
        return subprocess.CompletedProcess(args=["git", *args], returncode=0, stdout=corrupted, stderr=b"")

    monkeypatch.setattr(application_module, "git_binary", corrupted_git_binary)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.SAFETY_STATE_UNKNOWN
    assert result.exit_code == 22
    assert result.failure_code == outcomes.WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH
    assert result.application.failure_code == outcomes.WORKTREE_DISCOVERY_AMBIGUOUS_BRANCH
    assert result.application.actual_old_sha is None
    assert ref_sha(repo, TARGET_REF) == base
    assert_report_consistent(result)


def test_direct_evidence_conflict_typed_state_and_report(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    blocker = make_commit(repo, "direct-blocker")
    git(repo, "reset", "--hard", base)
    fixed_run_id = "direct-conflict"
    attempted_ref = f"refs/synapse/change/evidence/{fixed_run_id}"
    git(repo, "update-ref", attempted_ref, blocker)
    monkeypatch.setattr(runner_module, "new_run_id", lambda: fixed_run_id)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.EVIDENCE_CONFLICT
    assert result.exit_code == 23
    assert result.failure_phase == "evidence_ref"
    assert result.failure_code == outcomes.EVIDENCE_REF_ALREADY_EXISTS
    assert result.application is None
    assert ref_sha(repo, TARGET_REF) is None
    assert ref_sha(repo, attempted_ref) == blocker
    assert run(["git", "cat-file", "-e", result.verified_commit], repo).returncode == 0
    assert result.evidence.to_json() == {
        "status": outcomes.COMPLETED_LEGACY_SEMANTICS,
        "result_status": outcomes.EVIDENCE_CONFLICT,
        "evidence_ref": None,
        "attempted_ref": attempted_ref,
        "observed_existing_oid": blocker,
        "observed_symbolic_target": None,
    }
    assert_report_consistent(result)


def test_symbolic_evidence_conflict_typed_state_and_report(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    blocker_target = make_commit(repo, "symbolic-target")
    git(repo, "reset", "--hard", base)
    fixed_run_id = "symbolic-conflict"
    attempted_ref = f"refs/synapse/change/evidence/{fixed_run_id}"
    target_branch = "refs/heads/evidence-target"
    git(repo, "update-ref", target_branch, blocker_target)
    git(repo, "symbolic-ref", attempted_ref, target_branch)
    before = ref_sha(repo, target_branch)
    monkeypatch.setattr(runner_module, "new_run_id", lambda: fixed_run_id)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.EVIDENCE_CONFLICT
    assert result.exit_code == 23
    assert result.failure_code == outcomes.EVIDENCE_REF_SYMBOLIC_CONFLICT
    assert result.evidence.evidence_ref is None
    assert result.evidence.attempted_ref == attempted_ref
    assert result.evidence.observed_existing_oid is None
    assert result.evidence.observed_symbolic_target == target_branch
    assert run(["git", "symbolic-ref", "-q", attempted_ref], repo).stdout.strip() == target_branch
    assert ref_sha(repo, target_branch) == before
    assert ref_sha(repo, TARGET_REF) is None
    assert run(["git", "cat-file", "-e", result.verified_commit], repo).returncode == 0
    assert_report_consistent(result)


@pytest.mark.parametrize(
    ("reason", "failure_code"),
    [
        (runner_module.REF_ABSENT_AFTER_FAILED_UPDATE, outcomes.EVIDENCE_REF_UPDATE_FAILED),
        (runner_module.REF_STATE_UNKNOWN, outcomes.EVIDENCE_REF_STATE_UNKNOWN),
        ("UNRECOGNIZED_REASON", outcomes.EVIDENCE_REF_STATE_UNKNOWN),
    ],
)
def test_evidence_infrastructure_failures_and_unknown_reason_fail_closed(tmp_path, monkeypatch, reason, failure_code):
    repo, base = init_repo(tmp_path)
    attempted_ref = "refs/synapse/change/evidence/injected"

    def failing_evidence(repo_root, run_id, verified_commit):
        raise EvidenceRefError(
            reason=reason,
            attempted_ref=attempted_ref,
            detail=f"synthetic evidence failure {reason}",
        )

    def forbidden_application(*args, **kwargs):
        raise AssertionError("application must not run after evidence failure")

    monkeypatch.setattr(runner_module, "_create_evidence_ref", failing_evidence)
    monkeypatch.setattr(runner_module, "apply_verified_commit", forbidden_application)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.INTERNAL_ERROR
    assert result.exit_code == 30
    assert result.failure_phase == "evidence_ref"
    assert result.failure_code == failure_code
    assert result.evidence.status == outcomes.COMPLETED_LEGACY_SEMANTICS
    assert result.evidence.result_status == outcomes.INTERNAL_ERROR
    assert result.evidence.evidence_ref is None
    assert result.evidence.attempted_ref == attempted_ref
    assert result.evidence.observed_existing_oid is None
    assert result.evidence.observed_symbolic_target is None
    assert result.application is None
    assert ref_sha(repo, TARGET_REF) is None
    if reason == "UNRECOGNIZED_REASON":
        assert any("UNRECOGNIZED_REASON" in diagnostic for diagnostic in result.diagnostics)
    assert_report_consistent(result)


def test_stale_before_cas_and_during_cas_keep_stale_outcome(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    verified = make_commit(repo, "verified")
    other = make_commit(repo, "other")
    git(repo, "reset", "--hard", base)
    git(repo, "update-ref", TARGET_REF, other)
    stale = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert stale.status == outcomes.APPLICATION_STALE_BASE
    assert stale.failure_code == outcomes.APPLICATION_STALE_BASE
    assert stale.application_attempted is False
    assert stale.actual_old_sha == other

    git(repo, "update-ref", TARGET_REF, base)
    attacker = make_commit(repo, "attacker", "attacker.txt")
    git(repo, "reset", "--hard", base)
    real_git = application_module.git

    def racing_git(args, cwd, **kwargs):
        if args[:2] == ["update-ref", TARGET_REF] and len(args) == 4:
            real_git(["update-ref", TARGET_REF, attacker], cwd, check=True)
        return real_git(args, cwd, **kwargs)

    monkeypatch.setattr(application_module, "git", racing_git)
    raced = apply_verified_commit(repo, TARGET_REF, base, verified)
    assert raced.status == outcomes.APPLICATION_STALE_BASE
    assert raced.failure_code == outcomes.APPLICATION_STALE_BASE
    assert raced.application_attempted is True
    assert ref_sha(repo, TARGET_REF) == attacker


def test_success_evidence_state_and_report_consistency(tmp_path, monkeypatch):
    repo, base = init_repo(tmp_path)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base=base, task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.APPLIED
    assert result.evidence.status == outcomes.COMPLETED_LEGACY_SEMANTICS
    assert result.evidence.result_status == outcomes.EVIDENCE_CREATED
    assert result.evidence.evidence_ref == result.evidence_ref
    assert result.evidence.attempted_ref == result.evidence_ref
    assert result.evidence.observed_existing_oid is None
    assert result.evidence.observed_symbolic_target is None
    assert_report_consistent(result)


def test_direct_report_helper_evidence_compatibility_fail_closed():
    payload = build_report_payload(
        task=None,
        task_path=None,
        run_id="run",
        outcome=outcomes.INTERNAL_ERROR,
        prepared_patch=PreparedPatchMetadata(),
        phases=[],
        verified_commit=None,
        verified_tree=None,
        evidence_ref=None,
        application=None,
        worktree_path=None,
        cleanup_status="NO_WORKTREE_CREATED",
        diagnostics=[],
    )
    assert payload["evidence"] == {
        "status": outcomes.NOT_ATTEMPTED,
        "result_status": None,
        "evidence_ref": None,
        "attempted_ref": None,
        "observed_existing_oid": None,
        "observed_symbolic_target": None,
    }

    class Evidence:
        def to_json(self):
            return {
                "status": outcomes.COMPLETED_LEGACY_SEMANTICS,
                "result_status": outcomes.EVIDENCE_CREATED,
                "evidence_ref": "refs/synapse/change/evidence/actual",
                "attempted_ref": "refs/synapse/change/evidence/actual",
                "observed_existing_oid": None,
                "observed_symbolic_target": None,
            }

    with pytest.raises(ValueError):
        build_report_payload(
            task=None,
            task_path=None,
            run_id="run",
            outcome=outcomes.APPLIED,
            prepared_patch=PreparedPatchMetadata(),
            phases=[],
            verified_commit="0" * 40,
            verified_tree="1" * 40,
            evidence_ref="refs/synapse/change/evidence/legacy",
            evidence=Evidence(),
            application=None,
            worktree_path=None,
            cleanup_status="NO_WORKTREE_CREATED",
            diagnostics=[],
        )


def test_cli_equivalence_for_policy_refusal(tmp_path):
    canonical_repo, canonical_base = init_repo(tmp_path / "canonical")
    compatibility_repo, compatibility_base = init_repo(tmp_path / "compatibility")
    git(canonical_repo, "switch", "-c", "controlled/verified")
    git(compatibility_repo, "switch", "-c", "controlled/verified")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    canonical = run(
        [sys.executable, "-m", "synapse", "change", "apply", "--base", canonical_base, "--task", "tasks/task.json", "--environment-kind", "TEST"],
        canonical_repo,
        env=env,
    )
    compatibility = run(
        [sys.executable, "-m", "personal_slice", "run", "--base", compatibility_base, "--task", "tasks/task.json", "--environment-kind", "TEST"],
        compatibility_repo,
        env=env,
    )
    assert canonical.returncode == compatibility.returncode == 21
    assert outcome_from_output(canonical.stdout) == outcome_from_output(compatibility.stdout) == outcomes.POLICY_REFUSED
    canonical_report = read_report(report_path_from_output(canonical.stdout))
    compatibility_report = read_report(report_path_from_output(compatibility.stdout))
    assert canonical_report["schema"] == compatibility_report["schema"] == "personal_slice.report/v0.5.0"
    assert canonical_report["outcome"] == compatibility_report["outcome"] == outcomes.POLICY_REFUSED
    assert canonical_report["failure"]["failure_code"] == compatibility_report["failure"]["failure_code"] == outcomes.UNSAFE_TARGET_REF_CURRENTLY_CHECKED_OUT


def test_unexpected_exception_remains_internal_error(tmp_path, monkeypatch):
    repo, _base = init_repo(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner_module, "resolve_revision", boom)
    monkeypatch.chdir(repo)
    result = execute_controlled_change(ControlledChangeRequest(base="HEAD", task_path="tasks/task.json", environment_kind="TEST"))
    assert result.outcome == outcomes.INTERNAL_ERROR
    assert result.exit_code == 30
    assert result.failure_phase == "internal"
    assert result.failure_code == "RuntimeError"
    payload = assert_report_consistent(result)
    assert payload["evidence"] == runner_module.DEFAULT_EVIDENCE_RESULT.to_json()
