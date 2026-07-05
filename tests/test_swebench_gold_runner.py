from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

import synapse.experiments.swebench.gold_runner as gold_runner_module
from synapse.change.outcomes import APPLIED
from synapse.change.runner import ControlledChangeResult
from synapse.experiments.swebench.baseline import run_baseline_task
from synapse.experiments.swebench.contract import BaselineTask, ExperimentArm, OracleResult
from synapse.experiments.swebench.gold_attempt_writer import (
    GOLD_ATTEMPT_WRITE_FAILED,
    GOLD_DUPLICATE_ATTEMPT_KEY,
    GOLD_EVIDENCE_REJECTED,
    GoldAttemptWriter,
)
from synapse.experiments.swebench.gold_evidence import (
    GOLD_EVIDENCE_REPORT_UNREADABLE,
    GoldEvidenceValidationResult,
    validate_gold_evidence,
)
from synapse.experiments.swebench.gold_runner import (
    BASELINE_PREEXISTING_FAILURE_REASON,
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_NO_CANDIDATE,
    GOLD_ORACLE_UNRESOLVED,
    GoldRunnerCommandExpectation,
    GoldRunnerCommandPolicy,
    GoldRunnerError,
    run_gold_attempt,
    validate_attempt_id,
    validate_gold_run_id,
    validate_gold_runner_payload,
    _status_for_controlled_change_failure,
)
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
)


OLD_SOURCE = "def add(a, b):\n    return a - b  # bug\n"
NEW_SOURCE = "def add(a, b):\n    return a + b\n"
BROKEN_SOURCE = "def add(a, b):\n    return a - b\n"
ASSERT_ADD_COMMAND = "from src.calc import add; assert add(2, 3) == 5"


def run(cmd: list[str], cwd: Path, **kwargs):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, **kwargs)


def git(repo: Path, *args: str) -> str:
    completed = run(["git", *args], repo, check=True)
    return completed.stdout.strip()


def ref_snapshot(repo: Path) -> dict[str, str]:
    output = run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], repo, check=True).stdout
    refs: dict[str, str] = {}
    for line in output.splitlines():
        refname, objectname = line.split(" ", 1)
        refs[refname] = objectname
    return refs


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")


def build_candidate_repo(repo: Path) -> tuple[str, str]:
    init_repo(repo)
    write(repo / "README.md", "gold runner c1 fixture\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "seed")

    write(repo / "src" / "calc.py", OLD_SOURCE)
    write(
        repo / "tests_local" / "test_calc.py",
        "from src.calc import add\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
    )
    git(repo, "add", "src/calc.py", "tests_local/test_calc.py")
    git(repo, "commit", "-m", "candidate base")
    base = git(repo, "rev-parse", "HEAD")

    write(repo / "src" / "calc.py", NEW_SOURCE)
    patch_text = run(["git", "diff", "--binary", "--", "src/calc.py"], repo, check=True).stdout
    assert patch_text
    write(repo / "src" / "calc.py", OLD_SOURCE)
    assert git(repo, "status", "--short") == ""
    return base, patch_text


def worker_result(diff_text: str | None, *, touched_files: tuple[str, ...] = ("src/calc.py",)) -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus.PROPOSED_PATCH if diff_text else ExternalWorkerStatus.NO_PATCH,
        diff_text=diff_text,
        touched_files=touched_files if diff_text else (),
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


def policy(
    *,
    reproduction_before: GoldRunnerCommandExpectation | None = None,
    reproduction_after: GoldRunnerCommandExpectation | None = None,
    baseline_commands: tuple[tuple[str, ...], ...] = ((sys.executable, "-B", "-c", "pass"),),
    acceptance_commands: tuple[tuple[str, ...], ...] = ((sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),),
    full_suite_commands: tuple[tuple[str, ...], ...] = ((sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),),
) -> GoldRunnerCommandPolicy:
    return GoldRunnerCommandPolicy(
        task_id="calc-fix",
        instance_id="calc-1",
        statement="Fix add(a, b).",
        allowed_scope=("src",),
        reproduction_command=(sys.executable, "-B", "-c", ASSERT_ADD_COMMAND),
        reproduction_committed_inputs=("src/calc.py", "tests_local/test_calc.py"),
        reproduction_before=reproduction_before or GoldRunnerCommandExpectation(expected_exit_codes=(1,), timeout_seconds=10),
        reproduction_after=reproduction_after or GoldRunnerCommandExpectation(expected_exit_codes=(0,), timeout_seconds=10),
        baseline_commands=baseline_commands,
        acceptance_commands=acceptance_commands,
        full_suite_commands=full_suite_commands,
        commit_message="Apply GOLD C1 candidate",
        required_scaffold_paths=("src/calc.py", "tests_local/test_calc.py"),
        task_class="TEST",
    )


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def committed_text(repo: Path, commit: str, path: str) -> str:
    return run(["git", "show", f"{commit}:{path}"], repo, check=True).stdout


@dataclass
class RecordingOracle:
    resolved: bool = True
    infra_error: bool = False
    calls: int = 0
    observed_heads: list[str] | None = None

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        assert (worktree_path / "src" / "calc.py").is_file()
        head = git(worktree_path, "rev-parse", "HEAD")
        if self.observed_heads is None:
            self.observed_heads = []
        self.observed_heads.append(head)
        self.calls += 1
        return OracleResult(
            resolved=self.resolved,
            returncode=0 if self.resolved and not self.infra_error else 1,
            stdout="oracle stdout",
            stderr="oracle stderr",
            duration_seconds=0.01,
            diagnostics={"infra_error": self.infra_error, "task_id": task.task_id},
        )


def make_writer(run_root: Path, repo: Path) -> GoldAttemptWriter:
    return GoldAttemptWriter(
        run_root,
        repo_root=repo,
        report_root=run_root / "controlled-change-reports",
    )


def run_successful_attempt(tmp_path: Path, *, attempt_id: str = "1", oracle: RecordingOracle | None = None):
    repo = tmp_path / f"repo-{attempt_id}"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / f"run-{attempt_id}"
    writer = make_writer(run_root, repo)
    oracle = oracle or RecordingOracle()
    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runA",
        attempt_id=attempt_id,
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=oracle,
        writer=writer,
        run_root=run_root,
    )
    return repo, run_root, oracle, result


def test_bridge_commit_contains_committed_task_patch_scaffold_and_reproduction_inputs(tmp_path: Path) -> None:
    repo, run_root, oracle, result = run_successful_attempt(tmp_path)

    assert result.status == GOLD_APPLIED_WITH_EVIDENCE
    assert result.write_result.ok is True
    assert result.write_result.status == "GOLD_ATTEMPT_WRITTEN"
    assert result.bridge_commit is not None
    assert result.task_path is not None
    assert result.patch_path is not None
    assert result.controlled_change_result is not None
    assert result.controlled_change_result.outcome == APPLIED
    assert result.payload["controlled_change_run_id"] == result.controlled_change_result.run_id
    assert oracle.calls == 1
    assert oracle.observed_heads == [result.controlled_change_result.verified_commit]

    task_json = json.loads(committed_text(repo, result.bridge_commit, result.task_path))
    patch_text = committed_text(repo, result.bridge_commit, result.patch_path)
    assert task_json["base_revision"] == "HEAD"
    assert task_json["target_ref"] == "refs/heads/synapse/gold/runA/1"
    assert task_json["patch_path"] == result.patch_path
    assert task_json["reproduction"]["before"]["expected_exit_codes"] == [1]
    assert task_json["reproduction"]["after"]["expected_exit_codes"] == [0]
    assert task_json["baseline_commands"] == [[sys.executable, "-B", "-c", "pass"]]
    assert task_json["acceptance_commands"] == [[sys.executable, "-B", "-c", ASSERT_ADD_COMMAND]]
    assert task_json["full_suite_commands"] == [[sys.executable, "-B", "-c", ASSERT_ADD_COMMAND]]
    assert result.task_path in task_json["required_scaffold_paths"]
    assert result.patch_path in task_json["required_scaffold_paths"]
    assert "src/calc.py" in task_json["required_scaffold_paths"]
    assert "tests_local/test_calc.py" in task_json["required_scaffold_paths"]
    assert "src/calc.py" in task_json["reproduction"]["committed_inputs"]
    assert "tests_local/test_calc.py" in task_json["reproduction"]["committed_inputs"]
    assert "return a + b" in patch_text
    assert not (repo / result.task_path).exists()
    assert not (repo / result.patch_path).exists()
    assert git(repo, "rev-parse", "refs/heads/synapse/gold/runA/1") == result.controlled_change_result.verified_commit

    assert result.gold_evidence is not None
    assert not Path(result.gold_evidence.report_path).is_absolute()
    validation = validate_gold_evidence(
        result.gold_evidence,
        repo_root=repo,
        report_root=run_root / "controlled-change-reports",
    )
    assert validation.ok is True

    records = read_jsonl(run_root / "gold_attempts.jsonl")
    assert len(records) == 1
    assert records[0]["status"] == GOLD_APPLIED_WITH_EVIDENCE
    assert records[0]["requested_status"] == GOLD_APPLIED_WITH_EVIDENCE
    assert records[0]["gold_evidence"] is not None
    assert records[0]["payload"]["controlled_change_run_id"] == result.controlled_change_result.run_id
    assert records[0]["status"] != "GOLD_FULL_VERIFIED"


def test_wrong_cwd_does_not_route_controlled_change_to_wrong_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wrong_repo = tmp_path / "wrong"
    init_repo(wrong_repo)
    write(wrong_repo / "README.md", "wrong repo\n")
    git(wrong_repo, "add", "README.md")
    git(wrong_repo, "commit", "-m", "wrong seed")
    monkeypatch.chdir(wrong_repo)

    repo, _run_root, _oracle, result = run_successful_attempt(tmp_path)

    assert Path.cwd() == wrong_repo
    assert result.controlled_change_result is not None
    assert git(repo, "rev-parse", result.target_ref) == result.controlled_change_result.verified_commit
    missing = run(["git", "rev-parse", "--verify", result.target_ref], wrong_repo)
    assert missing.returncode != 0


def test_per_attempt_target_refs_prevent_stale_collisions_and_next_attempt_appends(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / "run"
    writer = make_writer(run_root, repo)

    first = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runB",
        attempt_id="1",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )
    second = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runB",
        attempt_id="2",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )

    records = read_jsonl(run_root / "gold_attempts.jsonl")
    assert first.status == GOLD_APPLIED_WITH_EVIDENCE
    assert second.status == GOLD_APPLIED_WITH_EVIDENCE
    assert first.target_ref == "refs/heads/synapse/gold/runB/1"
    assert second.target_ref == "refs/heads/synapse/gold/runB/2"
    assert first.target_ref != second.target_ref
    assert len(records) == 2
    assert {record["attempt_id"] for record in records} == {"1", "2"}


def controlled_failure(*, failure_phase: str) -> ControlledChangeResult:
    result = ControlledChangeResult(
        outcome="INTERNAL_ERROR",
        exit_code=1,
        report_path=None,
        verified_commit=None,
        verified_tree=None,
        evidence_ref=None,
        application=None,
        cleanup_status="NO_WORKTREE_CREATED",
        worktree_path=None,
        failure_phase=failure_phase,
        failure_code="NEUTRAL_FAILURE_CODE",
    )
    assert result.failure_phase == failure_phase
    assert result.failure_code == "NEUTRAL_FAILURE_CODE"
    assert result.failure_code != "OUT_OF_SCOPE_PATH"
    assert result.failure_code != "VERIFICATION_MUTATED_CANDIDATE"
    return result


def test_controlled_change_unknown_failure_phases_default_to_infra_error() -> None:
    task_contract = controlled_failure(failure_phase="task_contract")
    verified_commit = controlled_failure(failure_phase="verified_commit")
    evidence_ref = controlled_failure(failure_phase="evidence_ref")
    report = controlled_failure(failure_phase="report")
    cleanup = controlled_failure(failure_phase="cleanup")
    internal = controlled_failure(failure_phase="internal")
    future_phase = controlled_failure(failure_phase="future_phase_x")

    assert _status_for_controlled_change_failure(task_contract) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(verified_commit) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(evidence_ref) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(report) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(cleanup) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(internal) == GOLD_INFRA_ERROR
    assert _status_for_controlled_change_failure(future_phase) == GOLD_INFRA_ERROR


def test_bridge_creation_does_not_move_existing_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    marker_before = git(repo, "rev-parse", "HEAD")
    current_branch = git(repo, "branch", "--show-current")
    assert current_branch
    git(repo, "branch", "user-marker", marker_before)
    refs_before = ref_snapshot(repo)
    assert refs_before
    assert refs_before[f"refs/heads/{current_branch}"] == marker_before
    assert refs_before["refs/heads/user-marker"] == marker_before
    assert len(refs_before) == 2
    run_root = tmp_path / "run"

    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runC",
        attempt_id="1",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=make_writer(run_root, repo),
        run_root=run_root,
    )

    assert result.bridge_commit is not None
    assert result.controlled_change_result is not None
    assert result.controlled_change_result.verified_commit is not None
    assert result.controlled_change_result.evidence_ref is not None
    expected_target_ref = "refs/heads/synapse/gold/runC/1"
    assert result.target_ref == expected_target_ref
    refs_after = ref_snapshot(repo)
    expected_refs = {
        **refs_before,
        expected_target_ref: result.controlled_change_result.verified_commit,
        result.controlled_change_result.evidence_ref: result.controlled_change_result.verified_commit,
    }
    assert refs_after == expected_refs
    assert refs_after[expected_target_ref] == result.controlled_change_result.verified_commit
    assert refs_after[result.controlled_change_result.evidence_ref] == result.controlled_change_result.verified_commit
    assert git(repo, "rev-parse", "HEAD") == marker_before


def test_canonical_and_noncanonical_ids_are_enforced() -> None:
    assert validate_gold_run_id("Run_1-OK") == "Run_1-OK"
    assert validate_attempt_id("1") == "1"
    assert validate_attempt_id("23") == "23"
    with pytest.raises(GoldRunnerError):
        validate_gold_run_id("")
    with pytest.raises(GoldRunnerError):
        validate_gold_run_id("run.1")
    with pytest.raises(GoldRunnerError):
        validate_gold_run_id("run/1")
    with pytest.raises(GoldRunnerError):
        validate_gold_run_id("run 1")
    with pytest.raises(GoldRunnerError):
        validate_attempt_id("")
    with pytest.raises(GoldRunnerError):
        validate_attempt_id("01")
    with pytest.raises(GoldRunnerError):
        validate_attempt_id("a1")
    with pytest.raises(GoldRunnerError):
        validate_attempt_id("attempt-1")


def test_oracle_infra_unresolved_and_resolved_status_mapping(tmp_path: Path) -> None:
    _repo1, _run1, infra_oracle, infra = run_successful_attempt(
        tmp_path / "infra",
        oracle=RecordingOracle(resolved=False, infra_error=True),
    )
    _repo2, _run2, unresolved_oracle, unresolved = run_successful_attempt(
        tmp_path / "unresolved",
        oracle=RecordingOracle(resolved=False, infra_error=False),
    )
    _repo3, _run3, resolved_oracle, resolved = run_successful_attempt(
        tmp_path / "resolved",
        oracle=RecordingOracle(resolved=True, infra_error=False),
    )

    assert infra_oracle.calls == 1
    assert infra.status == GOLD_INFRA_ERROR
    assert infra.payload["oracle_infra_error"] is True
    assert unresolved_oracle.calls == 1
    assert unresolved.status == GOLD_ORACLE_UNRESOLVED
    assert unresolved.payload["oracle_resolved"] is False
    assert unresolved.payload["oracle_infra_error"] is False
    assert resolved_oracle.calls == 1
    assert resolved.status == GOLD_APPLIED_WITH_EVIDENCE
    assert resolved.payload["oracle_resolved"] is True
    assert resolved.payload["oracle_infra_error"] is False


def test_cleanup_failure_after_oracle_result_is_payload_diagnostic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_cleanup = gold_runner_module.cleanup_worktree

    def cleanup_then_fail(worktree, keep):
        status = original_cleanup(worktree, keep)
        assert status == "REMOVED"
        raise RuntimeError("cleanup diagnostic")

    monkeypatch.setattr(gold_runner_module, "cleanup_worktree", cleanup_then_fail)

    _repo, _run_root, _oracle, result = run_successful_attempt(tmp_path)

    assert result.status == GOLD_APPLIED_WITH_EVIDENCE
    assert result.payload["oracle_cleanup_diagnostic"]
    assert "cleanup diagnostic" in result.payload["oracle_cleanup_diagnostic"]


def test_payload_schema_requires_common_fields_scope_violations_and_oracle_on_applied() -> None:
    payload = {
        "gold_run_id": "run",
        "attempt_id": "1",
        "materialization_status": "MATERIALIZED",
        "materialization_diagnostics": {},
        "controlled_change_outcome": APPLIED,
        "failure_phase": None,
        "failure_code": None,
        "oracle_invoked": False,
        "skip_reason": "bad",
    }
    with pytest.raises(GoldRunnerError):
        validate_gold_runner_payload(status=GOLD_APPLIED_WITH_EVIDENCE, payload=payload, evidence_valid=True)

    payload["controlled_change_run_id"] = "abc123"
    with pytest.raises(GoldRunnerError):
        validate_gold_runner_payload(status=GOLD_APPLIED_WITH_EVIDENCE, payload=payload, evidence_valid=True)

    scope_payload = {
        "gold_run_id": "run",
        "attempt_id": "1",
        "controlled_change_run_id": None,
        "materialization_status": "REJECTED_SCOPE_VIOLATION",
        "materialization_diagnostics": {},
        "controlled_change_outcome": None,
        "failure_phase": "materialization",
        "failure_code": "REJECTED_SCOPE_VIOLATION",
        "oracle_invoked": False,
        "skip_reason": "materialization_not_materialized",
    }
    with pytest.raises(GoldRunnerError):
        validate_gold_runner_payload(status=GOLD_NO_CANDIDATE, payload=scope_payload)


def test_scope_violation_materialization_writes_no_candidate_with_scope_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    build_candidate_repo(repo)
    write(repo / "tests_local" / "test_calc.py", "def test_changed():\n    assert True\n")
    patch_text = run(["git", "diff", "--binary", "--", "tests_local/test_calc.py"], repo, check=True).stdout
    write(
        repo / "tests_local" / "test_calc.py",
        "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )
    assert patch_text
    run_root = tmp_path / "run"

    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runScope",
        attempt_id="1",
        worker_result=worker_result(patch_text, touched_files=("tests_local/test_calc.py",)),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=make_writer(run_root, repo),
        run_root=run_root,
    )

    assert result.status == GOLD_NO_CANDIDATE
    assert result.payload["scope_violations"] == ["tests_local/test_calc.py"]
    assert result.payload["oracle_invoked"] is False


def test_duplicate_attempt_key_refuses_and_next_attempt_appends(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    build_candidate_repo(repo)
    run_root = tmp_path / "run"
    writer = make_writer(run_root, repo)
    empty = worker_result(None, touched_files=())

    first = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runDup",
        attempt_id="1",
        worker_result=empty,
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )
    duplicate = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runDup",
        attempt_id="1",
        worker_result=empty,
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )
    retry = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runDup",
        attempt_id="2",
        worker_result=empty,
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )

    records = read_jsonl(run_root / "gold_attempts.jsonl")
    assert first.write_result.ok is True
    assert duplicate.write_result.ok is False
    assert duplicate.write_result.status == GOLD_ATTEMPT_WRITE_FAILED
    assert duplicate.write_result.failure_code == GOLD_DUPLICATE_ATTEMPT_KEY
    assert retry.write_result.ok is True
    assert len(records) == 2
    assert {record["attempt_id"] for record in records} == {"1", "2"}


def test_baseline_runner_remains_baseline_only() -> None:
    with pytest.raises(ValueError):
        run_baseline_task(
            BaselineTask("task", "inst", "statement", ("src",)),
            repo_root=".",
            base_revision="HEAD",
            replicate_id=1,
            mini=None,
            oracle=None,
            run_root=".",
            arm=ExperimentArm.GOLD,
        )


def test_report_root_mismatch_is_rejected_by_writer_level_validation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / "run"
    writer = GoldAttemptWriter(run_root, repo_root=repo, report_root=tmp_path / "wrong-report-root")

    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runMismatch",
        attempt_id="1",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=writer,
        run_root=run_root,
    )

    records = read_jsonl(run_root / "gold_attempts.jsonl")
    assert result.status == GOLD_APPLIED_WITH_EVIDENCE
    assert result.evidence_validation is not None
    assert result.evidence_validation.ok is True
    assert result.write_result.ok is False
    assert result.write_result.status == GOLD_EVIDENCE_REJECTED
    assert result.write_result.failure_code == GOLD_EVIDENCE_REPORT_UNREADABLE
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == GOLD_APPLIED_WITH_EVIDENCE
    assert records[0]["gold_evidence"] is None
    assert records[0]["failure_code"] == GOLD_EVIDENCE_REPORT_UNREADABLE


def test_runner_level_evidence_rejection_is_distinct_from_writer_level_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _base, patch_text = build_candidate_repo(repo)
    run_root = tmp_path / "run"

    monkeypatch.setattr(
        gold_runner_module,
        "validate_gold_evidence",
        lambda *args, **kwargs: GoldEvidenceValidationResult(
            ok=False,
            failure_code=GOLD_EVIDENCE_REPORT_UNREADABLE,
            detail="forced runner-level rejection",
        ),
    )

    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runEvidenceReject",
        attempt_id="1",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=make_writer(run_root, repo),
        run_root=run_root,
    )

    records = read_jsonl(run_root / "gold_attempts.jsonl")
    assert result.status == GOLD_EVIDENCE_REJECTED
    assert result.gold_evidence is None
    assert result.write_result.status == "GOLD_ATTEMPT_WRITTEN"
    assert records[0]["status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["requested_status"] == GOLD_EVIDENCE_REJECTED
    assert records[0]["writer"]["validation"] == "GOLD_EVIDENCE_NOT_REQUIRED"


def test_reproduction_before_and_baseline_preexisting_failures_map_to_infra_error(tmp_path: Path) -> None:
    repo1 = tmp_path / "repo-repro-before"
    _base1, patch_text1 = build_candidate_repo(repo1)
    repro_result = run_gold_attempt(
        repo_root=repo1,
        gold_run_id="runPreExisting",
        attempt_id="1",
        worker_result=worker_result(patch_text1),
        command_policy=policy(reproduction_before=GoldRunnerCommandExpectation(expected_exit_codes=(0,), timeout_seconds=10)),
        oracle=RecordingOracle(),
        writer=make_writer(tmp_path / "run-repro-before", repo1),
        run_root=tmp_path / "run-repro-before",
    )

    repo2 = tmp_path / "repo-baseline"
    _base2, patch_text2 = build_candidate_repo(repo2)
    baseline_result = run_gold_attempt(
        repo_root=repo2,
        gold_run_id="runBaseline",
        attempt_id="1",
        worker_result=worker_result(patch_text2),
        command_policy=policy(baseline_commands=((sys.executable, "-c", "import sys; sys.exit(1)"),)),
        oracle=RecordingOracle(),
        writer=make_writer(tmp_path / "run-baseline", repo2),
        run_root=tmp_path / "run-baseline",
    )

    assert repro_result.status == GOLD_INFRA_ERROR
    assert repro_result.payload["failure_phase"] == "reproduction_before"
    assert repro_result.payload["failure_reason"] == BASELINE_PREEXISTING_FAILURE_REASON
    assert baseline_result.status == GOLD_INFRA_ERROR
    assert baseline_result.payload["failure_phase"] == "baseline_1"
    assert baseline_result.payload["failure_reason"] == BASELINE_PREEXISTING_FAILURE_REASON


def test_reproduction_after_failure_maps_to_no_candidate(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    build_candidate_repo(repo)
    write(repo / "src" / "calc.py", BROKEN_SOURCE)
    patch_text = run(["git", "diff", "--binary", "--", "src/calc.py"], repo, check=True).stdout
    write(repo / "src" / "calc.py", OLD_SOURCE)
    assert patch_text
    assert git(repo, "status", "--short") == ""
    run_root = tmp_path / "run"

    result = run_gold_attempt(
        repo_root=repo,
        gold_run_id="runAfterFailure",
        attempt_id="1",
        worker_result=worker_result(patch_text),
        command_policy=policy(),
        oracle=RecordingOracle(),
        writer=make_writer(run_root, repo),
        run_root=run_root,
    )

    assert result.status == GOLD_NO_CANDIDATE
    assert result.payload["failure_phase"] == "reproduction_after"
    assert result.payload["controlled_change_outcome"] != APPLIED
    assert result.payload["oracle_invoked"] is False
