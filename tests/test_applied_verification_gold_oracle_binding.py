from __future__ import annotations

from pathlib import Path
import hashlib
import inspect
import json
import subprocess
import sys

import pytest

from synapse.experiments.applied_verification import gold_oracle_binding as binding_module
from synapse.experiments.applied_verification import gold_runner as gold_runner_module
from synapse.experiments.applied_verification.change_oracle import ChangeOracleConfig
from synapse.experiments.applied_verification.contract import BaselineTask
from synapse.experiments.applied_verification.gold_attempt_writer import GoldAttemptWriter
from synapse.experiments.applied_verification.gold_oracle_binding import GoldChangeOracleBinding
from synapse.experiments.applied_verification.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_ORACLE_UNRESOLVED,
    GoldRunnerCommandExpectation,
    GoldRunnerCommandPolicy,
    run_gold_attempt,
)
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
)


OLD_SOURCE = "def add(a, b):\n    return a - b\n"
NEW_SOURCE = "def add(a, b):\n    return a + b\n"
WRONG_SOURCE = "def add(a, b):\n    return a * b\n"
ASSERT_ADD_COMMAND = "from src.calc import add; assert add(2, 3) == 5"
ADD_CHECK = (sys.executable, "-B", "-c", ASSERT_ADD_COMMAND)


def run(cmd: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def git_raw(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def parent_count(repo: Path, commit: str = "HEAD") -> int:
    line = git(repo, "rev-list", "--parents", "-n", "1", commit)
    parts = line.split()
    assert parts
    return len(parts) - 1


def task() -> BaselineTask:
    return BaselineTask("task-1", "repo__issue-1", "fix it", ("src",))


def config(tmp_path: Path, *checks: tuple[str, ...], timeout: int = 30) -> ChangeOracleConfig:
    return ChangeOracleConfig(work_dir=tmp_path / "oracle-work", fail_to_pass=checks or (ADD_CHECK,),
                              command_timeout_seconds=timeout)


def seed_base_and_verified(repo: Path, verified_source: str = NEW_SOURCE) -> tuple[str, str]:
    init_repo(repo)
    write(repo / "src" / "__init__.py", "")
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    write(repo / "src" / "calc.py", verified_source)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "verified")
    verified = git(repo, "rev-parse", "HEAD")
    assert parent_count(repo, verified) == 1
    return base, verified


def detached_worktree(repo: Path, path: Path, commit: str) -> Path:
    git(repo, "worktree", "add", "--detach", str(path), commit)
    assert git(path, "rev-parse", "HEAD") == commit
    assert git(path, "status", "--short") == ""
    return path


def arm_forbidden_extractor_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from synapse.experiments.applied_verification import patch_extract

    def forbidden(*args, **kwargs):
        raise AssertionError("extract_model_patch_from_worktree must not be called")

    monkeypatch.setattr(patch_extract, "extract_model_patch_from_worktree", forbidden)
    source = inspect.getsource(binding_module)
    assert "extract_model_patch_from_worktree" not in source
    assert not hasattr(binding_module, "extract_model_patch_from_worktree")


def verified_binding(tmp_path: Path, *, verified_source: str = NEW_SOURCE, checks=()):
    repo = tmp_path / "repo"
    base, verified = seed_base_and_verified(repo, verified_source)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    expected_patch = git_raw(repo, "diff", "--binary", "--find-renames", f"{base}..{verified}")
    result = GoldChangeOracleBinding(config(tmp_path, *checks)).verify(worktree, task())
    return repo, worktree, base, verified, expected_patch, result


def report(result) -> dict[str, object]:
    return json.loads(Path(result.diagnostics["report_path"]).read_text(encoding="utf-8"))


def worker_result(diff_text: str | None) -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus.PROPOSED_PATCH if diff_text else ExternalWorkerStatus.NO_PATCH,
        diff_text=diff_text,
        touched_files=("src/calc.py",) if diff_text else (),
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            thinking_tokens=None,
            total_tokens=None,
            thinking_included=False,
            diagnostics={},
        ),
        diagnostics={},
    )


def c1_policy() -> GoldRunnerCommandPolicy:
    return GoldRunnerCommandPolicy(
        task_id="calc-fix",
        instance_id="repo__issue-1",
        statement="Fix add(a, b).",
        allowed_scope=("src",),
        reproduction_command=(sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),
        reproduction_committed_inputs=("src/calc.py", "tests_local/test_calc.py"),
        reproduction_before=GoldRunnerCommandExpectation(expected_exit_codes=(1,), timeout_seconds=10),
        reproduction_after=GoldRunnerCommandExpectation(expected_exit_codes=(0,), timeout_seconds=10),
        baseline_commands=((sys.executable, "-B", "-c", "pass"),),
        acceptance_commands=((sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),),
        full_suite_commands=((sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),),
        commit_message="Apply GOLD candidate",
        required_scaffold_paths=("src/calc.py", "tests_local/test_calc.py"),
        task_class="TEST",
    )


def seed_c1_candidate_repo(repo: Path) -> str:
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    write(
        repo / "tests_local" / "test_calc.py",
        "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )
    git(repo, "add", "src/calc.py", "tests_local/test_calc.py")
    git(repo, "commit", "-m", "base")
    write(repo / "src" / "calc.py", NEW_SOURCE)
    patch_text = git_raw(repo, "diff", "--binary", "--", "src/calc.py")
    assert patch_text
    write(repo / "src" / "calc.py", OLD_SOURCE)
    assert git(repo, "status", "--short") == ""
    return patch_text


def test_patch_comes_from_verified_commit_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arm_forbidden_extractor_guard(monkeypatch)
    repo, worktree, base, verified, expected_patch, result = verified_binding(tmp_path)
    diagnostics = result.diagnostics

    assert git(worktree, "rev-parse", "HEAD") == verified
    assert parent_count(worktree) == 1
    assert git(worktree, "status", "--short") == ""
    assert diagnostics["base_sha"] == base
    assert diagnostics["verified_commit"] == verified
    assert diagnostics["base_source"] == "head_parent_single_parent_assumed"
    assert diagnostics["verified_commit_parent_count"] == 1
    assert diagnostics["patch_source"] == "verified_commit_pair_git_diff"
    assert diagnostics["model_patch_sha256"] == hashlib.sha256(expected_patch.encode("utf-8")).hexdigest()
    assert report(result)["model_patch_sha256"] == diagnostics["model_patch_sha256"]
    assert report(result)["base_sha"] == base
    assert "return a + b" in expected_patch


def test_clean_verified_worktree_is_judged_before_and_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arm_forbidden_extractor_guard(monkeypatch)
    _repo, worktree, _base, _verified, _patch, result = verified_binding(tmp_path)

    assert git(worktree, "status", "--short") == ""
    assert result.resolved is True
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["failure_reason"] is None
    assert [(check["phase"], check["returncode"] == 0) for check in result.diagnostics["checks"]] == [
        ("before", False), ("after", True)]
    assert git(worktree, "worktree", "list").count("\n") == 1


def test_empty_commit_pair_diff_fails_before_checks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "base")
    git(repo, "commit", "--allow-empty", "-m", "empty verified")
    verified = git(repo, "rev-parse", "HEAD")
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    assert git_raw(worktree, "diff", "--binary", "--find-renames", "HEAD^..HEAD") == ""
    result = GoldChangeOracleBinding(config(tmp_path)).verify(worktree, task())

    assert "checks" not in result.diagnostics
    assert result.resolved is False
    assert result.diagnostics["failure_reason"] == "candidate_patch_empty"
    assert result.diagnostics["candidate_invalid"] is True
    assert result.diagnostics["infra_error"] is False


def test_root_commit_rejected_as_infra(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "root")
    root = git(repo, "rev-parse", "HEAD")
    assert parent_count(repo, root) == 0
    result = GoldChangeOracleBinding(config(tmp_path)).verify(repo, task())

    assert "checks" not in result.diagnostics
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "verified_commit_parent_missing"
    assert result.diagnostics["verified_commit_parent_count"] == 0


def test_merge_commit_rejected_as_infra(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", "base\n")
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "base")
    default_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    git(repo, "checkout", "-b", "feature")
    write(repo / "src" / "feature.py", "feature\n")
    git(repo, "add", "src/feature.py")
    git(repo, "commit", "-m", "feature")
    git(repo, "checkout", default_branch)
    write(repo / "src" / "main.py", "main\n")
    git(repo, "add", "src/main.py")
    git(repo, "commit", "-m", "main")
    git(repo, "merge", "--no-ff", "feature", "-m", "merge")
    merge = git(repo, "rev-parse", "HEAD")
    assert parent_count(repo, merge) >= 2
    result = GoldChangeOracleBinding(config(tmp_path)).verify(repo, task())

    assert "checks" not in result.diagnostics
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "verified_commit_merge_unsupported"
    assert result.diagnostics["verified_commit_parent_count"] >= 2


def test_wrong_fix_is_unresolved_not_infra(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(tmp_path, verified_source=WRONG_SOURCE)

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["failure_reason"] is None
    assert report(result)["resolved"] is False
    assert result.returncode not in (None, 0)


def test_check_that_passes_without_the_change_is_infra(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(
        tmp_path, checks=((sys.executable, "-B", "-c", "pass"),))

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_check_passes_before_change"
    assert [check["phase"] for check in result.diagnostics["checks"]] == ["before"]


def test_timeout_is_infra(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _base, verified = seed_base_and_verified(repo)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    sleeper = (sys.executable, "-B", "-c", "import time; time.sleep(30)")
    result = GoldChangeOracleBinding(config(tmp_path, sleeper, timeout=1)).verify(worktree, task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_timeout"


def test_oserror_is_infra(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(
        tmp_path, checks=(("/nonexistent/oracle-check",),))

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_command_error"


def test_shell_free_commands(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(tmp_path)

    assert all(check["command"] == list(ADD_CHECK) for check in result.diagnostics["checks"])
    assert "shell=True" not in inspect.getsource(binding_module)


def test_fingerprints_preserved(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(tmp_path)

    assert result.diagnostics["oracle_config_fingerprint"]
    assert result.diagnostics["oracle_config_fingerprint_payload"]["fail_to_pass"] == [list(ADD_CHECK)]
    assert result.diagnostics["oracle_environment_fingerprint"]
    assert result.diagnostics["cwd"] == str(tmp_path / "oracle-work")
    assert "work_dir" not in result.diagnostics["oracle_environment_fingerprint_payload"]


def run_c1_with_binding(
    tmp_path: Path,
    *,
    check: tuple[str, ...],
    attempt_id: str = "1",
):
    repo = tmp_path / f"repo-{attempt_id}"
    patch_text = seed_c1_candidate_repo(repo)
    run_root = tmp_path / f"run-{attempt_id}"
    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id=f"run{attempt_id}",
        attempt_id=attempt_id,
        worker_result=worker_result(patch_text),
        command_policy=c1_policy(),
        oracle=GoldChangeOracleBinding(config(tmp_path / f"oracle-{attempt_id}", check)),
        writer=GoldAttemptWriter(run_root, repo_root=repo, report_root=run_root / "controlled-change-reports"),
        run_root=run_root,
    )
    return run_root, result


def test_c1_integration_smoke_uses_injected_gold_binding(tmp_path: Path) -> None:
    source = inspect.getsource(gold_runner_module)
    assert "gold_oracle_binding" not in source
    stricter = (sys.executable, "-B", "-c", "from src.calc import add; assert add(2, 3) == 6")
    resolved_run, resolved = run_c1_with_binding(tmp_path / "resolved", check=ADD_CHECK, attempt_id="1")
    unresolved_run, unresolved = run_c1_with_binding(tmp_path / "unresolved", check=stricter, attempt_id="2")
    infra_run, infra = run_c1_with_binding(tmp_path / "infra", check=("/nonexistent/oracle-check",), attempt_id="3")

    assert resolved.status == GOLD_APPLIED_WITH_EVIDENCE
    assert resolved.gold_evidence is not None
    assert resolved.oracle_result is not None
    assert resolved.oracle_result.diagnostics["infra_error"] is False
    assert resolved.payload["oracle_invoked"] is True
    assert resolved.payload["oracle_resolved"] is True
    assert unresolved.status == GOLD_ORACLE_UNRESOLVED
    assert unresolved.gold_evidence is not None
    assert unresolved.payload["oracle_invoked"] is True
    assert unresolved.payload["oracle_resolved"] is False
    assert unresolved.payload["oracle_infra_error"] is False
    assert infra.status == GOLD_INFRA_ERROR
    assert infra.gold_evidence is not None
    assert infra.payload["oracle_invoked"] is True
    assert infra.payload["oracle_infra_error"] is True
    assert infra.payload["oracle_diagnostics"]["failure_reason"] == "oracle_command_error"
    for run_root in (resolved_run, unresolved_run, infra_run):
        records = [
            json.loads(line)
            for line in (run_root / "gold_attempts.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(records) == 1
        assert records[0]["status"] != "GOLD_FULL_VERIFIED"


def test_no_full_status_in_gold_oracle_binding_code(tmp_path: Path) -> None:
    _repo, _worktree, _base, _verified, _patch, result = verified_binding(tmp_path)

    assert result.resolved is True
    assert "GOLD_FULL_VERIFIED" not in inspect.getsource(binding_module)
    assert result.diagnostics.get("status") != "GOLD_FULL_VERIFIED"


def test_success_diagnostics_include_evidence_binding_fields(tmp_path: Path) -> None:
    _repo, _worktree, base, verified, _patch, result = verified_binding(tmp_path)
    diagnostics = result.diagnostics

    assert diagnostics["base_sha"] == base
    assert diagnostics["base_source"] == "head_parent_single_parent_assumed"
    assert diagnostics["verified_commit"] == verified
    assert diagnostics["verified_commit_parent_count"] == 1
    assert diagnostics["patch_source"] == "verified_commit_pair_git_diff"
    assert diagnostics["model_patch_sha256"]
    assert diagnostics["model_patch_bytes"] > 0
    assert diagnostics["changed_paths"] == ["src/calc.py"]
    assert diagnostics["oracle_config_fingerprint"]
    assert diagnostics["oracle_environment_fingerprint"]
