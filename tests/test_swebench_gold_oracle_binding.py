from __future__ import annotations

from pathlib import Path
import inspect
import json
import subprocess
import sys

import pytest

from synapse.experiments.swebench import gold_oracle_binding as binding_module
from synapse.experiments.swebench import gold_runner as gold_runner_module
from synapse.experiments.swebench.contract import BaselineTask
from synapse.experiments.swebench.gold_attempt_writer import GoldAttemptWriter
from synapse.experiments.swebench.gold_oracle_binding import GoldSWEbenchOracleBinding
from synapse.experiments.swebench.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_ORACLE_UNRESOLVED,
    GoldRunnerCommandExpectation,
    GoldRunnerCommandPolicy,
    run_gold_attempt,
)
from synapse.experiments.swebench.swebench_harness_oracle import SWEbenchHarnessOracleConfig
from synapse.experiments.swebench.swebench_reports import resolve_swebench_instance_report_path
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
)


OLD_SOURCE = "def add(a, b):\n    return a - b\n"
NEW_SOURCE = "def add(a, b):\n    return a + b\n"
ASSERT_ADD_COMMAND = "from src.calc import add; assert add(2, 3) == 5"


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


def config(tmp_path: Path) -> SWEbenchHarnessOracleConfig:
    return SWEbenchHarnessOracleConfig(
        python_executable=Path(sys.executable),
        swebench_work_dir=tmp_path / "swebench-work",
        dataset_name="princeton-nlp/SWE-bench_Lite",
        split="test",
        instance_timeout_seconds=120,
        max_workers=1,
        process_timeout_seconds=5,
    )


def seed_base_and_verified(repo: Path) -> tuple[str, str]:
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    write(repo / "src" / "calc.py", NEW_SOURCE)
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


def write_report(
    work_dir: Path,
    *,
    command: tuple[str, ...],
    config: SWEbenchHarnessOracleConfig,
    resolved: bool,
) -> Path:
    run_id = command[command.index("--run_id") + 1]
    instance_id = command[command.index("--instance_ids") + 1]
    report_path = resolve_swebench_instance_report_path(
        work_dir,
        run_id=run_id,
        model_name_or_path=config.model_name_or_path,
        instance_id=instance_id,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({instance_id: {"resolved": resolved}}), encoding="utf-8")
    return report_path


def arm_forbidden_extractor_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from synapse.experiments.swebench import patch_extract

    def forbidden(*args, **kwargs):
        raise AssertionError("extract_model_patch_from_worktree must not be called")

    monkeypatch.setattr(patch_extract, "extract_model_patch_from_worktree", forbidden)
    source = inspect.getsource(binding_module)
    assert "extract_model_patch_from_worktree" not in source
    assert not hasattr(binding_module, "extract_model_patch_from_worktree")


def successful_verified_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved: bool,
    returncode: int = 0,
) -> tuple[Path, Path, str, str, dict[str, object], object]:
    repo = tmp_path / "repo"
    base, verified = seed_base_and_verified(repo)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    expected_patch = git_raw(repo, "diff", "--binary", "--find-renames", f"{base}..{verified}")
    calls: list[tuple[str, ...]] = []
    assert calls == []

    def fake_run(command: tuple[str, ...], *, config: SWEbenchHarnessOracleConfig):
        calls.append(command)
        write_report(config.swebench_work_dir, command=command, config=config, resolved=resolved)
        return subprocess.CompletedProcess(command, returncode, stdout="harness stdout", stderr="")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(worktree, task())
    assert len(calls) == 1
    return repo, worktree, base, verified, {"expected_patch": expected_patch, "calls": calls}, result


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
    repo, worktree, base, verified, observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)
    diagnostics = result.diagnostics
    predictions_path = Path(diagnostics["predictions_artifact"]["path"])
    prediction = json.loads(predictions_path.read_text(encoding="utf-8"))

    assert git(worktree, "rev-parse", "HEAD") == verified
    assert parent_count(worktree) == 1
    assert git(worktree, "status", "--short") == ""
    assert diagnostics["base_sha"] == base
    assert diagnostics["verified_commit"] == verified
    assert diagnostics["base_source"] == "head_parent_single_parent_assumed"
    assert diagnostics["verified_commit_parent_count"] == 1
    assert diagnostics["patch_source"] == "verified_commit_pair_git_diff"
    assert prediction["model_patch"] == observed["expected_patch"]
    assert git_raw(repo, "diff", "--binary", "--find-renames", f"{base}..{verified}") == prediction["model_patch"]
    assert "return a + b" in prediction["model_patch"]


def test_clean_verified_worktree_still_invokes_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arm_forbidden_extractor_guard(monkeypatch)
    _repo, worktree, _base, _verified, observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)

    assert git(worktree, "status", "--short") == ""
    assert len(observed["calls"]) == 1
    assert result.resolved is True
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["failure_reason"] is None
    assert result.diagnostics["failure_reason"] != "candidate_patch_empty"


def test_empty_commit_pair_diff_fails_before_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "base")
    git(repo, "commit", "--allow-empty", "-m", "empty verified")
    verified = git(repo, "rev-parse", "HEAD")
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    calls = 0

    def fake_run(command, *, config):
        nonlocal calls
        calls += 1
        raise AssertionError("harness must not run for empty pair diff")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    assert git_raw(worktree, "diff", "--binary", "--find-renames", "HEAD^..HEAD") == ""
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(worktree, task())

    assert calls == 0
    assert result.resolved is False
    assert result.diagnostics["failure_reason"] == "candidate_patch_empty"
    assert result.diagnostics["candidate_invalid"] is True
    assert result.diagnostics["infra_error"] is False


def test_root_commit_rejected_as_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", OLD_SOURCE)
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "root")
    root = git(repo, "rev-parse", "HEAD")
    assert parent_count(repo, root) == 0
    calls = 0

    def fake_run(command, *, config):
        nonlocal calls
        calls += 1
        raise AssertionError("harness must not run for root commit")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(repo, task())

    assert calls == 0
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "verified_commit_parent_missing"
    assert result.diagnostics["verified_commit_parent_count"] == 0


def test_merge_commit_rejected_as_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    init_repo(repo)
    write(repo / "src" / "calc.py", "base\n")
    git(repo, "add", "src/calc.py")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature")
    write(repo / "src" / "feature.py", "feature\n")
    git(repo, "add", "src/feature.py")
    git(repo, "commit", "-m", "feature")
    git(repo, "checkout", "master")
    write(repo / "src" / "main.py", "main\n")
    git(repo, "add", "src/main.py")
    git(repo, "commit", "-m", "main")
    git(repo, "merge", "--no-ff", "feature", "-m", "merge")
    merge = git(repo, "rev-parse", "HEAD")
    assert parent_count(repo, merge) >= 2
    assert git(repo, "rev-parse", "HEAD^")
    calls = 0

    def fake_run(command, *, config):
        nonlocal calls
        calls += 1
        raise AssertionError("harness must not run for merge commit")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(repo, task())

    assert calls == 0
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "verified_commit_merge_unsupported"
    assert result.diagnostics["verified_commit_parent_count"] >= 2


def test_report_authority_beats_returncode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, _base, _verified, observed, result = successful_verified_binding(
        tmp_path,
        monkeypatch,
        resolved=True,
        returncode=17,
    )

    assert len(observed["calls"]) == 1
    assert result.resolved is True
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["returncode"] == 17
    assert result.returncode == 17
    assert result.diagnostics["report_parse_diagnostics"]["infra_error"] is False


def test_resolved_false_report_is_unresolved_not_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, _base, _verified, observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=False)

    assert len(observed["calls"]) == 1
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["failure_reason"] is None


def test_missing_report_is_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _base, verified = seed_base_and_verified(repo)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)
    calls: list[tuple[str, ...]] = []
    assert calls == []

    def fake_run(command, *, config):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(worktree, task())

    assert len(calls) == 1
    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "swebench_report_missing"


def test_timeout_is_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _base, verified = seed_base_and_verified(repo)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)

    def fake_run(command, *, config):
        raise subprocess.TimeoutExpired(command, timeout=1, output="partial", stderr="err")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(worktree, task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_timeout"


def test_oserror_is_infra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _base, verified = seed_base_and_verified(repo)
    worktree = detached_worktree(repo, tmp_path / "verified", verified)

    def fake_run(command, *, config):
        raise OSError("cannot launch")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = GoldSWEbenchOracleBinding(config(tmp_path)).verify(worktree, task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_command_error"


def test_shell_free_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, _base, _verified, observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)
    command = result.diagnostics["command"]

    assert len(observed["calls"]) == 1
    assert result.diagnostics["shell"] is False
    assert isinstance(command, list)
    assert command == list(observed["calls"][0])
    assert "--timeout" in command
    assert command[command.index("--timeout") + 1] == "120"
    assert "shell=True" not in inspect.getsource(binding_module)


def test_fingerprints_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, _base, _verified, _observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)

    assert result.diagnostics["oracle_config_fingerprint"]
    assert result.diagnostics["oracle_config_fingerprint_payload"]
    assert result.diagnostics["oracle_environment_fingerprint"]
    assert result.diagnostics["oracle_environment_fingerprint_payload"]


def run_c1_with_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolved: bool | None,
    infra: bool = False,
    attempt_id: str = "1",
):
    repo = tmp_path / f"repo-{attempt_id}"
    patch_text = seed_c1_candidate_repo(repo)
    run_root = tmp_path / f"run-{attempt_id}"
    oracle_config = config(tmp_path / f"oracle-{attempt_id}")
    calls: list[tuple[str, ...]] = []
    assert calls == []

    def fake_run(command, *, config):
        calls.append(command)
        if infra:
            raise OSError("oracle infra")
        assert resolved is not None
        write_report(config.swebench_work_dir, command=command, config=config, resolved=resolved)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(binding_module, "_run_harness_command", fake_run)
    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id=f"run{attempt_id}",
        attempt_id=attempt_id,
        worker_result=worker_result(patch_text),
        command_policy=c1_policy(),
        oracle=GoldSWEbenchOracleBinding(oracle_config),
        writer=GoldAttemptWriter(run_root, repo_root=repo, report_root=run_root / "controlled-change-reports"),
        run_root=run_root,
    )
    return run_root, calls, result


def test_c1_integration_smoke_uses_injected_gold_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = inspect.getsource(gold_runner_module)
    assert "gold_oracle_binding" not in source
    resolved_run, resolved_calls, resolved = run_c1_with_binding(tmp_path / "resolved", monkeypatch, resolved=True, attempt_id="1")
    unresolved_run, unresolved_calls, unresolved = run_c1_with_binding(tmp_path / "unresolved", monkeypatch, resolved=False, attempt_id="2")
    infra_run, infra_calls, infra = run_c1_with_binding(tmp_path / "infra", monkeypatch, resolved=None, infra=True, attempt_id="3")

    assert len(resolved_calls) == 1
    assert resolved.status == GOLD_APPLIED_WITH_EVIDENCE
    assert resolved.gold_evidence is not None
    assert resolved.oracle_result is not None
    assert resolved.oracle_result.diagnostics["infra_error"] is False
    assert resolved.payload["oracle_invoked"] is True
    assert resolved.payload["oracle_resolved"] is True
    assert len(unresolved_calls) == 1
    assert unresolved.status == GOLD_ORACLE_UNRESOLVED
    assert unresolved.gold_evidence is not None
    assert unresolved.payload["oracle_invoked"] is True
    assert unresolved.payload["oracle_resolved"] is False
    assert unresolved.payload["oracle_infra_error"] is False
    assert len(infra_calls) == 1
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


def test_no_full_status_in_gold_oracle_binding_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, _base, _verified, _observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)

    assert result.resolved is True
    assert "GOLD_FULL_VERIFIED" not in inspect.getsource(binding_module)
    assert result.diagnostics.get("status") != "GOLD_FULL_VERIFIED"


def test_success_diagnostics_include_evidence_binding_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, _worktree, base, verified, _observed, result = successful_verified_binding(tmp_path, monkeypatch, resolved=True)
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
