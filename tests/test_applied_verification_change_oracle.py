"""Change oracle tests: the Baseline front end, the before/after verdict and its report."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import subprocess
import sys

import pytest

from synapse.experiments.applied_verification.baseline import _attempt_verdict
from synapse.experiments.applied_verification.carry import RawCarryEntry, RawTranscriptCarry
from synapse.experiments.applied_verification.change_oracle import (
    ChangeOracleConfig,
    ChangeOracleRunner,
    build_oracle_config_fingerprint_payload,
    build_oracle_environment_fingerprint_payload,
    compute_oracle_config_fingerprint,
    compute_oracle_environment_fingerprint,
    parse_change_oracle_report,
    summarize_text_for_carry,
)
from synapse.experiments.applied_verification.contract import (
    ArtifactRef,
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
    OracleResult,
    UsageSource,
)
from synapse.experiments.applied_verification.telemetry import TelemetryWriter, token_accounting_from_worker_usage
from synapse.worker import ExternalCodingWorkerResult, ExternalWorkerStatus, ExternalWorkerTokenStatus, ExternalWorkerUsage, WorkerReport


# The dispatch owner is replaced in these product-path tests; only the
# admitted profile identity written to the Baseline manifest is read.
FAKE_AGENT = SimpleNamespace(registry=SimpleNamespace(adapters=(SimpleNamespace(
    profile=SimpleNamespace(provider_name="fake-agent", model_name="fake-model")),)))
FIXED_CHECK = (sys.executable, "-B", "-c", "import allowed, sys; sys.exit(0 if allowed.value == 'fixed' else 1)")
BASE_CHECK = (sys.executable, "-B", "-c", "import allowed, sys; sys.exit(0 if allowed.value == 'base' else 1)")


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


def _config(tmp_path: Path, **overrides) -> ChangeOracleConfig:
    values = {"work_dir": tmp_path / "oracle-work", "fail_to_pass": (FIXED_CHECK,), "command_timeout_seconds": 30}
    values.update(overrides)
    return ChangeOracleConfig(**values)


def _task(allowed_scope=("allowed.py",)) -> BaselineTask:
    return BaselineTask("task-1", "repo__issue-1", "fix the issue", tuple(allowed_scope))


def _fix(repo: Path, value: str = "fixed") -> None:
    (repo / "allowed.py").write_text(f"value = {value!r}\n", encoding="utf-8")


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


def test_config_validation_and_fingerprints_are_deterministic_and_semantic(tmp_path):
    with pytest.raises(ValueError, match="fail_to_pass must not be empty"):
        _config(tmp_path, fail_to_pass=())
    with pytest.raises(ValueError, match="non-empty argv"):
        _config(tmp_path, fail_to_pass=([],))
    with pytest.raises(ValueError, match="together with its sha256"):
        _config(tmp_path, evaluator_patch=tmp_path / "evaluator.patch")
    with pytest.raises(ValueError, match="lowercase sha256"):
        _config(tmp_path, evaluator_patch=tmp_path / "evaluator.patch", evaluator_patch_sha256="ABC")
    with pytest.raises(ValueError, match="command_timeout_seconds"):
        _config(tmp_path, command_timeout_seconds=0)

    from_json = _config(tmp_path, fail_to_pass=[list(FIXED_CHECK)], work_dir=str(tmp_path / "oracle-work"))
    config = _config(tmp_path)
    assert from_json == config
    payload = build_oracle_config_fingerprint_payload(config)
    same_payload = build_oracle_config_fingerprint_payload(from_json)
    changed_payload = build_oracle_config_fingerprint_payload(_config(tmp_path, pass_to_pass=(BASE_CHECK,)))

    assert compute_oracle_config_fingerprint(payload) == compute_oracle_config_fingerprint(same_payload)
    assert compute_oracle_config_fingerprint(payload) != compute_oracle_config_fingerprint(changed_payload)
    assert payload["fail_to_pass"] == [list(FIXED_CHECK)]
    assert payload["pass_to_pass"] == []
    assert payload["evaluator_patch_sha256"] is None
    assert payload["command_timeout_seconds"] == 30
    assert "work_dir" not in payload
    assert "run_id" not in payload

    env_payload = build_oracle_environment_fingerprint_payload(config)
    elsewhere = build_oracle_environment_fingerprint_payload(_config(tmp_path / "other-arm"))
    assert env_payload == elsewhere and "work_dir" not in env_payload
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


def test_runner_uses_worktree_patch_records_artifacts_and_does_not_scope_gate(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo, "physical change")
    raw_marker = "RAW_LONG_LOG_BODY_SHOULD_NOT_ENTER_CARRY"
    script = tmp_path / "noisy_check.py"
    script.write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import allowed\n"
        "print('\\n'.join([f'line {i}' for i in range(40)] + "
        f"[{raw_marker!r}] + [f'tail {{i}}' for i in range(40)]))\n"
        "sys.exit(0 if allowed.value == 'fixed' else 2)\n", encoding="utf-8")
    noisy = (sys.executable, "-B", str(script))
    config = _config(tmp_path, fail_to_pass=(noisy,), max_summary_bytes=800, max_summary_lines=8)

    result = ChangeOracleRunner(config).verify(repo, _task(allowed_scope=("other.py",)))

    assert result.resolved is False
    assert result.returncode == 2
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["patch_source"] == "worktree_git_diff"
    assert result.diagnostics["changed_paths"] == ["allowed.py"]
    assert result.diagnostics["scope_observation_only"] is True
    assert result.diagnostics["failure_reason"] is None
    assert "candidate_patch_scope_violation" not in json.dumps(result.diagnostics)
    kinds = {artifact["kind"] for artifact in result.diagnostics["oracle_managed_artifacts"]}
    assert kinds == {"oracle_report", "oracle_check_log"}
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
    log, = [item for item in result.diagnostics["oracle_managed_artifacts"] if item["kind"] == "oracle_check_log"]
    assert raw_marker in Path(log["path"]).read_text(encoding="utf-8")
    assert result.diagnostics["oracle_config_fingerprint"]
    assert result.diagnostics["oracle_environment_fingerprint"]
    assert (repo / "allowed.py").read_text(encoding="utf-8") == "value = 'physical change'\n"


def test_fix_is_resolved_only_when_the_check_fails_before_and_passes_after(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo)
    result = ChangeOracleRunner(_config(tmp_path)).verify(repo, _task())

    assert result.resolved is True
    assert result.returncode == 0
    assert result.diagnostics["infra_error"] is False
    assert [(check["phase"], check["group"], check["returncode"]) for check in result.diagnostics["checks"]] == [
        ("before", "fail_to_pass", 1), ("after", "fail_to_pass", 0)]
    report = parse_change_oracle_report(Path(result.diagnostics["report_path"]), "repo__issue-1")
    assert report.target_instance_found is True and report.resolved is True
    assert _git(repo, "worktree", "list").stdout.count("\n") == 1


def test_regression_in_pass_to_pass_is_unresolved(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo)
    still_base = (sys.executable, "-B", "-c", "import allowed, sys; sys.exit(0 if allowed.value == 'base' else 3)")
    result = ChangeOracleRunner(_config(tmp_path, pass_to_pass=(still_base,))).verify(repo, _task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is False
    assert [check["returncode"] for check in result.diagnostics["checks"]] == [1, 0, 3]


def test_check_that_cannot_tell_the_fix_from_no_change_is_infra(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo)
    result = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(BASE_CHECK,))).verify(repo, _task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "oracle_check_passes_before_change"
    assert parse_change_oracle_report(Path(result.diagnostics["report_path"]), "repo__issue-1").diagnostics[
        "failure_reason"] == "oracle_report_infra_error"


@pytest.mark.parametrize("code", [126, 127])
def test_check_that_never_ran_is_infra_in_either_checkout(tmp_path, code):
    repo = _repo(tmp_path)
    _fix(repo)
    unavailable = (sys.executable, "-B", "-c", f"import sys; sys.exit({code})")
    before = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(unavailable,))).verify(repo, _task())
    assert before.diagnostics["infra_error"] is True
    assert before.diagnostics["failure_reason"] == "oracle_check_unavailable"
    assert [check["phase"] for check in before.diagnostics["checks"]] == ["before"]

    after_only = (sys.executable, "-B", "-c",
                  f"import allowed, sys; sys.exit({code} if allowed.value == 'fixed' else 1)")
    after = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(after_only,))).verify(repo, _task())
    assert after.resolved is False
    assert after.diagnostics["infra_error"] is True
    assert after.diagnostics["failure_reason"] == "oracle_check_unavailable"
    assert [check["phase"] for check in after.diagnostics["checks"]] == ["before", "after"]


def test_before_run_leaves_nothing_for_the_after_run(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo)
    leaves_marker = (sys.executable, "-B", "-c",
                     "import allowed, pathlib, sys\n"
                     "if pathlib.Path('marker').exists(): sys.exit(9)\n"
                     "pathlib.Path('marker').write_text('x')\n"
                     "sys.exit(0 if allowed.value == 'fixed' else 1)")
    result = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(leaves_marker,))).verify(repo, _task())

    assert result.resolved is True
    assert [check["returncode"] for check in result.diagnostics["checks"]] == [1, 0]


def test_evaluator_patch_reaches_both_checkouts_and_is_pinned(tmp_path):
    repo = _repo(tmp_path)
    hidden_check = (sys.executable, "-B", "-c", "import evaluator_check")
    (tmp_path / "evaluator").mkdir()
    patch_path = tmp_path / "evaluator" / "hidden.patch"
    check_file = repo / "evaluator_check.py"
    check_file.write_text("import allowed, sys\nsys.exit(0 if allowed.value == 'fixed' else 1)\n", encoding="utf-8")
    _git(repo, "add", "-N", "evaluator_check.py")
    patch_path.write_bytes(subprocess.run(["git", "diff", "--binary", "--", "evaluator_check.py"], cwd=repo,
                                          capture_output=True, check=True).stdout)
    _git(repo, "rm", "-q", "--cached", "evaluator_check.py")
    check_file.unlink()
    digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    _fix(repo)
    config = _config(tmp_path, fail_to_pass=(hidden_check,), evaluator_patch=patch_path, evaluator_patch_sha256=digest)

    result = ChangeOracleRunner(config).verify(repo, _task())
    assert result.resolved is True, result.stderr
    assert json.loads(Path(result.diagnostics["report_path"]).read_text())["evaluator_patch_sha256"] == digest
    assert not (repo / "evaluator_check.py").exists()

    tampered = _config(tmp_path, fail_to_pass=(hidden_check,), evaluator_patch=patch_path,
                       evaluator_patch_sha256="0" * 64)
    result = ChangeOracleRunner(tampered).verify(repo, _task())
    assert result.diagnostics["infra_error"] is True
    assert result.diagnostics["failure_reason"] == "evaluator_patch_digest_mismatch"


def test_candidate_that_conflicts_with_the_evaluator_patch_is_unresolved(tmp_path):
    repo = _repo(tmp_path)
    (repo / "allowed.py").write_text("value = 'evaluator'\n", encoding="utf-8")
    patch_path = tmp_path / "evaluator.patch"
    patch_path.write_bytes(subprocess.run(["git", "diff", "--binary"], cwd=repo, capture_output=True, check=True).stdout)
    _fix(repo)
    digest = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    result = ChangeOracleRunner(_config(tmp_path, evaluator_patch=patch_path, evaluator_patch_sha256=digest)).verify(
        repo, _task())

    assert result.resolved is False
    assert result.diagnostics["infra_error"] is False
    assert result.diagnostics["candidate_invalid"] is True
    assert result.diagnostics["failure_reason"] == "candidate_patch_apply_failed"
    assert parse_change_oracle_report(Path(result.diagnostics["report_path"]), "repo__issue-1").resolved is False


def test_runner_timeout_oserror_and_invalid_candidate_mapping(tmp_path):
    repo = _repo(tmp_path)
    _fix(repo, "changed")
    sleeper = (sys.executable, "-B", "-c", "import time; time.sleep(30)")
    timeout_result = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(sleeper,), command_timeout_seconds=1)).verify(
        repo, _task())
    assert timeout_result.diagnostics["infra_error"] is True
    assert timeout_result.diagnostics["failure_reason"] == "oracle_timeout"

    oserror_result = ChangeOracleRunner(_config(tmp_path, fail_to_pass=(("/nonexistent/check",),))).verify(
        repo, _task())
    assert oserror_result.diagnostics["infra_error"] is True
    assert oserror_result.diagnostics["failure_reason"] == "oracle_command_error"

    clean_parent = tmp_path / "clean"
    clean_parent.mkdir()
    clean_repo = _repo(clean_parent)
    invalid_result = ChangeOracleRunner(_config(tmp_path)).verify(clean_repo, _task())
    assert "checks" not in invalid_result.diagnostics
    assert invalid_result.diagnostics["infra_error"] is False
    assert invalid_result.diagnostics["candidate_invalid"] is True
    assert invalid_result.diagnostics["failure_reason"] == "candidate_patch_empty"
    assert invalid_result.diagnostics["oracle_config_fingerprint"]
    assert invalid_result.diagnostics["oracle_environment_fingerprint"]


def _write_report(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_report_parse_accepts_only_its_own_instance_level_verdict(tmp_path):
    valid = {"schema": "synapse.applied_verification.change_oracle_report/v1", "instance_id": "repo__issue-1",
             "resolved": False, "infra_error": False}
    parsed = parse_change_oracle_report(_write_report(tmp_path / "ok.json", valid), "repo__issue-1")
    assert parsed.target_instance_found is True and parsed.resolved is False
    cases = {
        "oracle_report_missing": tmp_path / "missing.json",
        "oracle_report_malformed": _write_report(tmp_path / "bad.json", "{"),
        "oracle_report_target_missing": _write_report(tmp_path / "other.json", {**valid, "instance_id": "other"}),
        "oracle_report_resolved_not_bool": _write_report(tmp_path / "nonbool.json", {**valid, "resolved": "yes"}),
        "oracle_report_infra_error": _write_report(tmp_path / "infra.json", {**valid, "infra_error": True}),
    }
    for reason, path in cases.items():
        parsed = parse_change_oracle_report(path, "repo__issue-1")
        assert parsed.target_instance_found is False and parsed.resolved is False
        assert parsed.diagnostics["infra_error"] is True
        assert parsed.diagnostics["failure_reason"] == reason
    schema_less = _write_report(tmp_path / "schema.json", {k: v for k, v in valid.items() if k != "schema"})
    assert parse_change_oracle_report(schema_less, "repo__issue-1").diagnostics["failure_reason"] == "oracle_report_malformed"


def test_summarize_text_for_carry_bounds_long_text():
    marker = "RAW_BODY_SHOULD_BE_TRUNCATED"
    text = "\n".join([f"head {i}" for i in range(20)] + [marker] + [f"tail {i}" for i in range(20)])

    summary, diagnostics = summarize_text_for_carry(text, max_bytes=200, max_lines=6)

    assert diagnostics["truncated"] is True
    assert marker not in summary


def test_baseline_worker_diff_model_patch_diagnostics(tmp_path, monkeypatch):
    from synapse.experiments.applied_verification.baseline import run_baseline_task
    
    repo = _repo(tmp_path)

    def fake_worker(agent, *, worktree, prompt, **_):
        Path(worktree, "allowed.py").write_text("value = 'physical'\n", encoding="utf-8")
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
            from synapse.experiments.applied_verification.patch_extract import extract_model_patch_from_worktree

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

    monkeypatch.setattr("synapse.experiments.applied_verification.baseline._agent_candidate", fake_worker)

    run = run_baseline_task(
        _task(),
        repo_root=repo,
        base_revision="HEAD",
        replicate_id=1,
        max_attempts=1,
        agent=FAKE_AGENT,
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
    raw_check_stdout = "RAW_FULL_CHECK_STDOUT_SHOULD_NOT_BE_IN_JSONL"
    raw_check_stderr = "RAW_FULL_CHECK_STDERR_SHOULD_NOT_BE_IN_JSONL"
    raw_report_body = "RAW_REPORT_BODY_SHOULD_NOT_BE_IN_JSONL"
    raw_artifact_root = tmp_path / "oracle-raw"
    raw_artifact_root.mkdir()
    raw_files = {
        "oracle_check_log": ("checks.log", raw_check_stdout + "\n" + raw_check_stderr),
        "oracle_report": ("report.json", raw_report_body),
    }

    def raw_summary(kind: str):
        name, body = raw_files[kind]
        path = raw_artifact_root / name
        path.write_text(body, encoding="utf-8")
        data = body.encode("utf-8")
        return {
            "kind": kind,
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    oracle_managed_artifacts = [raw_summary("oracle_check_log"), raw_summary("oracle_report")]
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
                "failure_reason": "oracle_report_target_missing",
                "resolved": False,
                "returncode": 1,
                "model_patch_sha256": "sha",
                "model_patch_bytes": len(raw_model_patch),
                "changed_paths": ["allowed.py"],
                "oracle_config_fingerprint": "config-fp",
                "oracle_environment_fingerprint": "env-fp",
                "oracle_managed_artifacts": oracle_managed_artifacts,
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

    attempts_text = (tmp_path / "run" / "attempts.jsonl").read_text(encoding="utf-8")
    oracle_text = (tmp_path / "run" / "oracle.jsonl").read_text(encoding="utf-8")
    for text in (attempts_text, oracle_text):
        assert raw_model_patch not in text
        assert raw_check_stdout not in text
        assert raw_check_stderr not in text
        assert raw_report_body not in text
    telemetry = attempts_text + oracle_text
    assert oracle_managed_artifacts[1]["sha256"] in telemetry
    assert "config-fp" in telemetry
    assert "env-fp" in telemetry
    assert "oracle_report_target_missing" in telemetry
    assert "allowed.py" in telemetry
    assert "sha256" in telemetry
    assert "bytes" in telemetry
