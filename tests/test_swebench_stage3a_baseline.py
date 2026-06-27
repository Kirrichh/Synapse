"""Stage 3A baseline runner product-path tests."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from synapse.experiments.swebench.baseline import run_baseline_task
from synapse.experiments.swebench.contract import AttemptVerdict, BaselineTask, ExperimentArm
from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.experiments.swebench.oracle import CommandOracleRunner
from synapse.worker import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
    WorkerReport,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "allowed.py").write_text("value = 'base'\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _worker_result(status: ExternalWorkerStatus, *, diff_text: str | None = "diff", summary: str | None = "worker"):
    return ExternalCodingWorkerResult(
        worker_status=status,
        diff_text=diff_text,
        touched_files=("allowed.py",) if diff_text else (),
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus.PROVIDER_REPORTED,
            input_tokens=10,
            output_tokens=5,
            thinking_tokens=5,
            total_tokens=20,
            thinking_included=True,
            diagnostics={"raw_usage_ref": "fake"},
        ),
        diagnostics={"scope_violations": ()},
        worker_report=WorkerReport(summary=summary),
    )


def test_proposed_patch_alone_does_not_resolve_and_second_attempt_uses_raw_carry(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    prompts: list[str] = []

    def fake_worker(worktree_path, task, allowed_scope, *, config):
        prompts.append(task["task"])
        value = "pass" if "oracle_stdout" in task["task"] else "bad"
        Path(worktree_path, "allowed.py").write_text(f"value = '{value}'\n", encoding="utf-8")
        return _worker_result(ExternalWorkerStatus.PROPOSED_PATCH, diff_text=f"allowed.py -> {value}")

    monkeypatch.setattr("synapse.experiments.swebench.baseline.run_mini_worker", fake_worker)
    oracle = CommandOracleRunner(
        (
            sys.executable,
            "-c",
            "from pathlib import Path; data=Path('allowed.py').read_text(); "
            "print('oracle saw pass' if 'pass' in data else 'oracle saw bad'); "
            "raise SystemExit(0 if 'pass' in data else 1)",
        )
    )
    task = BaselineTask("task", "instance", "make allowed pass", ("allowed.py",))

    run = run_baseline_task(
        task,
        repo_root=repo,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=3,
        mini=MiniInvocationConfig(),
        oracle=oracle,
        run_root=tmp_path / "runs",
    )

    assert run.resolved is True
    assert [attempt.verdict for attempt in run.attempts] == [
        AttemptVerdict.ORACLE_UNRESOLVED,
        AttemptVerdict.ORACLE_RESOLVED,
    ]
    assert "RAW BASELINE RETRY CONTEXT" in prompts[1]
    assert "oracle saw bad" in prompts[1]
    assert all(attempt.arm is ExperimentArm.BASELINE for attempt in run.attempts)
    assert run.arm is ExperimentArm.BASELINE


def test_max_attempt_budget_k3_is_enforced_for_unresolved_candidates(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    def fake_worker(worktree_path, task, allowed_scope, *, config):
        Path(worktree_path, "allowed.py").write_text("value = 'bad'\n", encoding="utf-8")
        return _worker_result(ExternalWorkerStatus.PROPOSED_PATCH)

    monkeypatch.setattr("synapse.experiments.swebench.baseline.run_mini_worker", fake_worker)
    oracle = CommandOracleRunner((sys.executable, "-c", "print('still failing'); raise SystemExit(1)"))

    run = run_baseline_task(
        BaselineTask("task", "instance", "statement", ("allowed.py",)),
        repo_root=repo,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=3,
        mini=MiniInvocationConfig(),
        oracle=oracle,
        run_root=tmp_path / "runs",
    )

    assert run.resolved is False
    assert len(run.attempts) == 3
    assert all(attempt.verdict is AttemptVerdict.ORACLE_UNRESOLVED for attempt in run.attempts)


def test_no_patch_error_and_timeout_do_not_call_oracle_or_become_success(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    statuses = iter((ExternalWorkerStatus.NO_PATCH, ExternalWorkerStatus.ERROR, ExternalWorkerStatus.TIMEOUT))
    oracle_calls = 0

    def fake_worker(worktree_path, task, allowed_scope, *, config):
        status = next(statuses)
        return _worker_result(status, diff_text=None, summary=status.value)

    class CountingOracle:
        def verify(self, worktree_path, task):
            nonlocal oracle_calls
            oracle_calls += 1
            raise AssertionError("oracle should not run without proposed patch")

    monkeypatch.setattr("synapse.experiments.swebench.baseline.run_mini_worker", fake_worker)

    run = run_baseline_task(
        BaselineTask("task", "instance", "statement", ("allowed.py",)),
        repo_root=repo,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=3,
        mini=MiniInvocationConfig(),
        oracle=CountingOracle(),
        run_root=tmp_path / "runs",
    )

    assert oracle_calls == 0
    assert [attempt.verdict for attempt in run.attempts] == [
        AttemptVerdict.NO_CANDIDATE,
        AttemptVerdict.INFRA_ERROR,
        AttemptVerdict.INFRA_ERROR,
    ]
    assert "SUCCESS" not in {attempt.verdict.value for attempt in run.attempts}


def test_gold_arm_rejected_in_baseline_runner(tmp_path):
    with pytest.raises(ValueError, match="stage3a: unsupported_arm"):
        run_baseline_task(
            BaselineTask("task", "instance", "statement", ("allowed.py",)),
            repo_root=tmp_path,
            base_revision="HEAD",
            replicate_id=1,
            mini=MiniInvocationConfig(),
            oracle=CommandOracleRunner((sys.executable, "-c", "raise SystemExit(0)")),
            run_root=tmp_path / "runs",
            arm=ExperimentArm.GOLD,
        )
