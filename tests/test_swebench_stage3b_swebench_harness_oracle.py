"""Stage 3B SWE-bench harness oracle adapter tests."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

from synapse.experiments.swebench import swebench_harness_oracle as harness
from synapse.experiments.swebench.baseline import _attempt_verdict
from synapse.experiments.swebench.carry import RawCarryEntry, RawTranscriptCarry
from synapse.experiments.swebench.contract import (
    ArtifactRef,
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
    OracleResult,
    UsageSource,
)
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig,
    SWEbenchHarnessOracleRunner,
    build_oracle_config_fingerprint_payload,
    build_oracle_environment_fingerprint_payload,
    build_swebench_harness_command,
    compute_oracle_config_fingerprint,
    compute_oracle_environment_fingerprint,
    detect_swebench_version,
    summarize_text_for_carry,
    write_swebench_predictions_jsonl,
)
from synapse.experiments.swebench.swebench_reports import resolve_swebench_instance_report_path
from synapse.experiments.swebench.telemetry import TelemetryWriter, token_accounting_from_worker_usage
from synapse.worker import ExternalCodingWorkerResult, ExternalWorkerStatus, ExternalWorkerTokenStatus, ExternalWorkerUsage, WorkerReport


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


def _config(tmp_path: Path, **overrides) -> SWEbenchHarnessOracleConfig:
    values = {
        "python_executable": Path(sys.executable),
        "swebench_work_dir": tmp_path / "swebench-work",
        "dataset_name": "princeton-nlp/SWE-bench_Lite",
        "split": "test",
        "instance_timeout_seconds": 120,
        "max_workers": 1,
    }
    values.update(overrides)
    return SWEbenchHarnessOracleConfig(**values)


def _task(allowed_scope=("allowed.py",)) -> BaselineTask:
    return BaselineTask("task-1", "repo__issue-1", "fix the issue", tuple(allowed_scope))


def _oracle_result(*, resolved=False, infra_error=False, candidate_invalid=False) -> OracleResult:
    return OracleResult(
        resolved=resolved,
        returncode=0 if resolved else 1,
        stdout="summary",
        stderr="",
        duration_seconds=0.1,
        diagnostics={
            "infra_error": infra_error,
            "candidate_invalid": candidate_invalid,
            "failure_reason": "test",
        },
    )


def test_config_validation_and_fingerprints_are_deterministic_and_semantic(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="force_rebuild"):
        _config(tmp_path, force_rebuild=True, namespace="namespace")
    _config(tmp_path, force_rebuild=True, namespace=None)

    config = _config(tmp_path, namespace=None, instance_image_tag="inst", env_image_tag="env")
    payload = build_oracle_config_fingerprint_payload(config, swebench_version="4.1.0")
    same_payload = build_oracle_config_fingerprint_payload(config, swebench_version="4.1.0")
    changed_payload = build_oracle_config_fingerprint_payload(
        _config(tmp_path, split="dev"),
        swebench_version="4.1.0",
    )

    assert compute_oracle_config_fingerprint(payload) == compute_oracle_config_fingerprint(same_payload)
    assert compute_oracle_config_fingerprint(payload) != compute_oracle_config_fingerprint(changed_payload)
    assert payload["dataset_name"] == "princeton-nlp/SWE-bench_Lite"
    assert payload["split"] == "test"
    assert payload["instance_timeout_seconds"] == 120
    assert payload["namespace"] == "none"
    assert payload["instance_image_tag"] == "inst"
    assert payload["env_image_tag"] == "env"
    assert "swebench_work_dir" not in payload
    assert "run_id" not in payload

    def missing_version(name):
        raise harness.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(harness.metadata, "version", missing_version)
    assert detect_swebench_version() == "unknown"

    env_payload = build_oracle_environment_fingerprint_payload(config, swebench_version="4.1.0")
    assert env_payload["swebench_version"] == "4.1.0"
    assert env_payload["python_executable"] == str(config.python_executable)
    assert env_payload["instance_image_tag"] == "inst"
    assert env_payload["env_image_tag"] == "env"
    assert env_payload["docker_probe"] == "not_performed"
    assert compute_oracle_environment_fingerprint(env_payload) == compute_oracle_environment_fingerprint(dict(env_payload))


def test_attempt_verdict_uses_oracle_infra_error_without_treating_candidate_invalid_as_infra():
    assert _attempt_verdict(ExternalWorkerStatus.PROPOSED_PATCH, _oracle_result(resolved=True)) is AttemptVerdict.ORACLE_RESOLVED
    assert _attempt_verdict(ExternalWorkerStatus.PROPOSED_PATCH, _oracle_result(resolved=False, infra_error=False)) is AttemptVerdict.ORACLE_UNRESOLVED
    for reason in ("missing report", "malformed report", "timeout", "oserror"):
        result = _oracle_result(resolved=False, infra_error=True)
        assert result.diagnostics["infra_error"] is True
        assert _attempt_verdict(ExternalWorkerStatus.PROPOSED_PATCH, result) is AttemptVerdict.INFRA_ERROR, reason
    assert _attempt_verdict(
        ExternalWorkerStatus.PROPOSED_PATCH,
        _oracle_result(resolved=False, infra_error=False, candidate_invalid=True),
    ) is AttemptVerdict.ORACLE_UNRESOLVED
    assert _attempt_verdict(ExternalWorkerStatus.NO_PATCH, None) is AttemptVerdict.NO_CANDIDATE
    assert _attempt_verdict(ExternalWorkerStatus.ERROR, None) is AttemptVerdict.INFRA_ERROR
    assert _attempt_verdict(ExternalWorkerStatus.TIMEOUT, None) is AttemptVerdict.INFRA_ERROR


def test_prediction_jsonl_and_command_shape(tmp_path):
    config = _config(tmp_path, namespace=None, force_rebuild=True, clean=True)
    predictions = tmp_path / "predictions.jsonl"
    patch = "diff --git a/a.py b/a.py\n"

    summary = write_swebench_predictions_jsonl(
        predictions,
        instance_id="repo__issue-1",
        model_name_or_path="synapse-stage3b",
        model_patch=patch,
    )
    payload = json.loads(predictions.read_text(encoding="utf-8"))
    command = build_swebench_harness_command(
        config,
        predictions_path=predictions,
        instance_id="repo__issue-1",
        run_id="run-1",
    )

    assert payload == {
        "instance_id": "repo__issue-1",
        "model_name_or_path": "synapse-stage3b",
        "model_patch": patch,
    }
    assert summary["kind"] == "swebench_prediction"
    assert command[:3] == (str(config.python_executable), "-m", "swebench.harness.run_evaluation")
    assert "--dataset_name" in command
    assert "--split" in command
    assert "--instance_ids" in command
    assert "--predictions_path" in command
    assert Path(command[command.index("--predictions_path") + 1]).is_absolute()
    assert command[command.index("--force_rebuild") + 1] == "true"
    assert command[command.index("--clean") + 1] == "true"
    assert command[command.index("--namespace") + 1] == "none"
    assert command[command.index("--instance_image_tag") + 1] == "latest"
    assert command[command.index("--env_image_tag") + 1] == "latest"
    assert "docker" not in command

    namespaced = build_swebench_harness_command(
        _config(tmp_path, namespace="custom", model_name_or_path="org/model"),
        predictions_path=predictions,
        instance_id="repo__issue-1",
        run_id="run-2",
    )
    assert namespaced[namespaced.index("--namespace") + 1] == "custom"


def test_runner_uses_worktree_patch_records_artifacts_and_does_not_scope_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "allowed.py").write_text("value = 'physical change'\n", encoding="utf-8")
    config = _config(tmp_path, max_summary_bytes=800, max_summary_lines=8)
    raw_marker = "RAW_LONG_LOG_BODY_SHOULD_NOT_ENTER_CARRY"
    calls = []

    def fake_run(command, *, config):
        calls.append(command)
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        predictions_path = Path(command[command.index("--predictions_path") + 1])
        prediction = json.loads(predictions_path.read_text(encoding="utf-8"))
        assert "physical change" in prediction["model_patch"]
        report_path = resolve_swebench_instance_report_path(
            config.swebench_work_dir,
            run_id=run_id,
            model_name_or_path=config.model_name_or_path,
            instance_id=instance_id,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({instance_id: {"resolved": False}}), encoding="utf-8")
        (report_path.parent / "run_instance.log").write_text("full run instance log", encoding="utf-8")
        (report_path.parent / "test_output.txt").write_text("full test output", encoding="utf-8")
        long_stdout = "\n".join([f"line {i}" for i in range(40)] + [raw_marker] + [f"tail {i}" for i in range(40)])
        return subprocess.CompletedProcess(command, 2, stdout=long_stdout, stderr="stderr summary source")

    monkeypatch.setattr(harness, "_run_harness_command", fake_run)

    result = SWEbenchHarnessOracleRunner(config).verify(repo, _task(allowed_scope=("other.py",)))

    assert calls
    assert result.resolved is False
    assert result.returncode == 2
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["patch_source"] == "worktree_git_diff"
    assert result.diagnostics["changed_paths"] == ["allowed.py"]
    assert result.diagnostics["scope_observation_only"] is True
    assert result.diagnostics["failure_reason"] is None
    assert "candidate_patch_scope_violation" not in json.dumps(result.diagnostics)
    kinds = {artifact["kind"] for artifact in result.diagnostics["oracle_managed_artifacts"]}
    assert {
        "swebench_prediction",
        "swebench_report",
        "swebench_stdout",
        "swebench_stderr",
        "swebench_run_instance_log",
        "swebench_test_output",
    }.issubset(kinds)
    assert raw_marker not in result.stdout
    carry = RawTranscriptCarry().append(
        RawCarryEntry(
            attempt_id=1,
            worker_summary="worker",
            oracle_stdout=result.stdout,
            oracle_stderr=result.stderr,
            diagnostics=(),
        )
    )
    assert raw_marker not in carry.render_prompt_suffix()
    stdout_artifact = result.diagnostics["stdout_artifact"]
    assert raw_marker in Path(stdout_artifact["path"]).read_text(encoding="utf-8")
    assert result.diagnostics["oracle_config_fingerprint"]
    assert result.diagnostics["oracle_environment_fingerprint"]


def test_runner_valid_report_takes_precedence_over_returncode(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "allowed.py").write_text("value = 'fixed'\n", encoding="utf-8")
    config = _config(tmp_path)

    def fake_run(command, *, config):
        run_id = command[command.index("--run_id") + 1]
        instance_id = command[command.index("--instance_ids") + 1]
        report_path = resolve_swebench_instance_report_path(
            config.swebench_work_dir,
            run_id=run_id,
            model_name_or_path=config.model_name_or_path,
            instance_id=instance_id,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({instance_id: {"resolved": True}}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(harness, "_run_harness_command", fake_run)

    result = SWEbenchHarnessOracleRunner(config).verify(repo, _task())

    assert result.resolved is True
    assert result.returncode == 1
    assert result.diagnostics["infra_error"] is False


def test_runner_nonzero_without_valid_report_is_infra_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "allowed.py").write_text("value = 'changed'\n", encoding="utf-8")
    config = _config(tmp_path)

    def fake_run(command, *, config):
        return subprocess.CompletedProcess(command, 2, stdout="failed", stderr="no report")

    monkeypatch.setattr(harness, "_run_harness_command", fake_run)

    result = SWEbenchHarnessOracleRunner(config).verify(repo, _task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "swebench_report_missing"


def test_runner_timeout_oserror_and_invalid_candidate_mapping(tmp_path, monkeypatch):
    config = _config(tmp_path, process_timeout_seconds=1)
    repo = _repo(tmp_path)
    (repo / "allowed.py").write_text("value = 'changed'\n", encoding="utf-8")

    def timeout_run(command, *, config):
        raise subprocess.TimeoutExpired(command, timeout=1, output="partial out", stderr="partial err")

    monkeypatch.setattr(harness, "_run_harness_command", timeout_run)
    timeout_result = SWEbenchHarnessOracleRunner(config).verify(repo, _task())
    assert timeout_result.diagnostics["infra_error"] is True
    assert timeout_result.diagnostics["failure_reason"] == "oracle_timeout"

    def oserror_run(command, *, config):
        raise OSError("cannot launch")

    monkeypatch.setattr(harness, "_run_harness_command", oserror_run)
    oserror_result = SWEbenchHarnessOracleRunner(config).verify(repo, _task())
    assert oserror_result.diagnostics["infra_error"] is True
    assert oserror_result.diagnostics["failure_reason"] == "oracle_command_error"

    clean_parent = tmp_path / "clean"
    clean_parent.mkdir()
    clean_repo = _repo(clean_parent)
    calls = 0

    def should_not_run(command, *, config):
        nonlocal calls
        calls += 1
        raise AssertionError("harness should not run for invalid candidate")

    monkeypatch.setattr(harness, "_run_harness_command", should_not_run)
    invalid_result = SWEbenchHarnessOracleRunner(config).verify(clean_repo, _task())
    assert calls == 0
    assert invalid_result.diagnostics["infra_error"] is False
    assert invalid_result.diagnostics["candidate_invalid"] is True
    assert invalid_result.diagnostics["failure_reason"] == "candidate_patch_empty"
    assert invalid_result.diagnostics["oracle_config_fingerprint"]
    assert invalid_result.diagnostics["oracle_environment_fingerprint"]


def test_summarize_text_for_carry_bounds_long_text():
    marker = "RAW_BODY_SHOULD_BE_TRUNCATED"
    text = "\n".join([f"head {i}" for i in range(20)] + [marker] + [f"tail {i}" for i in range(20)])

    summary, diagnostics = summarize_text_for_carry(text, max_bytes=200, max_lines=6)

    assert diagnostics["truncated"] is True
    assert marker not in summary


def test_baseline_worker_diff_model_patch_diagnostics(tmp_path, monkeypatch):
    from synapse.experiments.swebench.baseline import run_baseline_task
    from synapse.experiments.swebench.mini_config import MiniInvocationConfig

    repo = _repo(tmp_path)

    def fake_worker(worktree_path, task, allowed_scope, *, config):
        Path(worktree_path, "allowed.py").write_text("value = 'physical'\n", encoding="utf-8")
        return ExternalCodingWorkerResult(
            worker_status=ExternalWorkerStatus.PROPOSED_PATCH,
            diff_text="worker reported something else",
            touched_files=("allowed.py",),
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
            worker_report=WorkerReport(summary="candidate"),
        )

    class HashOracle:
        def verify(self, worktree_path, task):
            from synapse.experiments.swebench.patch_extract import extract_model_patch_from_worktree

            extraction = extract_model_patch_from_worktree(worktree_path)
            return OracleResult(
                resolved=False,
                returncode=1,
                stdout="bounded",
                stderr="",
                duration_seconds=0.1,
                diagnostics={
                    "infra_error": False,
                    "patch_source": extraction.diagnostics["patch_source"],
                    "model_patch_sha256": extraction.diagnostics["model_patch_sha256"],
                    "model_patch_bytes": extraction.diagnostics["model_patch_bytes"],
                },
            )

    monkeypatch.setattr("synapse.experiments.swebench.baseline.run_mini_worker", fake_worker)

    run = run_baseline_task(
        _task(),
        repo_root=repo,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=1,
        mini=MiniInvocationConfig(),
        oracle=HashOracle(),
        run_root=tmp_path / "runs",
    )

    diagnostics = run.attempts[0].diagnostics
    assert diagnostics["worker_diff_artifact_present"] is True
    assert diagnostics["model_patch_source"] == "worktree_git_diff"
    assert diagnostics["worker_diff_matches_model_patch"] is False
    assert diagnostics["worker_diff_model_patch_mismatch"] is True
    assert run.attempts[0].verdict is AttemptVerdict.ORACLE_UNRESOLVED


def test_telemetry_jsonl_does_not_embed_raw_model_patch(tmp_path):
    raw_model_patch = "RAW_MODEL_PATCH_SHOULD_ONLY_BE_IN_PREDICTION_ARTIFACT"
    token = token_accounting_from_worker_usage(
        ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            thinking_tokens=None,
            total_tokens=None,
            thinking_included=False,
            diagnostics={},
        ),
        usage_source=UsageSource.UNAVAILABLE,
    )
    attempt = BaselineAttemptRecord(
        attempt_id=1,
        arm=ExperimentArm.BASELINE,
        verdict=AttemptVerdict.ORACLE_UNRESOLVED,
        worker_result=ExternalCodingWorkerResult(
            worker_status=ExternalWorkerStatus.PROPOSED_PATCH,
            diff_text="worker diff",
            touched_files=("allowed.py",),
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
            worker_report=WorkerReport(summary="candidate"),
        ),
        token_accounting=token,
        oracle_result=OracleResult(
            resolved=False,
            returncode=1,
            stdout="bounded summary",
            stderr="",
            duration_seconds=0.1,
            diagnostics={
                "infra_error": False,
                "model_patch_sha256": "sha",
                "model_patch_bytes": len(raw_model_patch),
                "oracle_managed_artifacts": [
                    {
                        "kind": "swebench_prediction",
                        "path": "prediction.jsonl",
                        "sha256": "prediction-sha",
                        "bytes": 100,
                    }
                ],
            },
        ),
        artifacts=(
            ArtifactRef("oracle_stdout", "artifacts/stdout.txt", "stdout-sha", 7),
            ArtifactRef("oracle_stderr", "artifacts/stderr.txt", "stderr-sha", 7),
        ),
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:01Z",
        diagnostics={"model_patch_sha256": "sha"},
    )
    run = BaselineRunRecord(
        run_id="run",
        task_id="task",
        instance_id="repo__issue-1",
        arm=ExperimentArm.BASELINE,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=1,
        resolved=False,
        attempts=(attempt,),
        total_provider_tokens=None,
        primary_metric_usable=False,
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:01Z",
        diagnostics={},
    )

    TelemetryWriter(tmp_path, run.run_id).write_records(run)

    telemetry = (tmp_path / "run" / "attempts.jsonl").read_text(encoding="utf-8")
    telemetry += (tmp_path / "run" / "oracle.jsonl").read_text(encoding="utf-8")
    assert raw_model_patch not in telemetry
    assert "prediction-sha" in telemetry
