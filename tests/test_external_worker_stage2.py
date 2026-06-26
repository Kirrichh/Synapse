"""Stage 2 external coding-worker adapter contract tests."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from synapse.worker import (
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    MiniAdapterConfig,
    run_mini_worker,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "allowed.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "outside.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _runner(
    *,
    mutate: str | None = "allowed.py",
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    timeout: bool = False,
    trajectory: dict | None = None,
    seen_commands: list[list[str]] | None = None,
    seen_kwargs: list[dict] | None = None,
):
    def run(command, **kwargs):
        if command[0] == "mini":
            if seen_commands is not None:
                seen_commands.append(list(command))
            if seen_kwargs is not None:
                seen_kwargs.append(dict(kwargs))
            if timeout:
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))
            if trajectory is not None:
                output_path = Path(command[command.index("-o") + 1])
                output_path.write_text(json.dumps(trajectory), encoding="utf-8")
            if mutate:
                path = Path(kwargs["cwd"]) / mutate
                path.write_text("value = 2\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)
        return subprocess.run(command, **kwargs)

    return run


def _usage_line(**overrides):
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "thinking_tokens": 3,
        "total_tokens": 18,
    }
    usage.update(overrides)
    return json.dumps({"usage": usage, "summary": "candidate generated"})


def _trajectory_usage(**overrides):
    usage = {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "thinking_tokens": 5,
        "total_tokens": 23,
    }
    usage.update(overrides)
    return {"info": {"model_stats": {"usage": usage}}}


def test_adapter_returns_proposed_patch_diff_usage_and_touched_files(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        {"task_id": "stage2"},
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(stdout=_usage_line()),
    )

    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert result.diff_text is not None
    assert "allowed.py" in result.diff_text
    assert result.touched_files == ("allowed.py",)
    assert result.usage.token_status is ExternalWorkerTokenStatus.PROVIDER_REPORTED
    assert result.usage.total_tokens == 18
    assert result.usage.thinking_included is True
    assert result.diagnostics["scope_violations"] == ()


def _assert_mini_v242_required_flags(command: list[str], kwargs: dict, *, max_steps: int = 3):
    assert "--max-steps" not in command
    assert "--agent-class" not in command
    assert "agent.agent_class=yolo" not in command
    assert command[:1] == ["mini"]
    assert "-t" in command
    assert "-y" in command
    assert "--exit-immediately" in command
    assert "-l" in command
    config_indexes = [index for index, value in enumerate(command) if value == "-c"]
    assert len(config_indexes) == 2
    assert command[config_indexes[0] + 1] == "mini.yaml"
    assert command[config_indexes[1] + 1] == f"agent.step_limit={max_steps}"
    assert config_indexes[0] < config_indexes[1]
    assert command[command.index("-o") + 1].endswith(".trajectory.json")
    assert kwargs.get("shell") is not True


def test_adapter_default_invocation_omits_model_override(tmp_path):
    repo = _repo(tmp_path)
    commands: list[list[str]] = []
    kwargs_seen: list[dict] = []

    result = run_mini_worker(
        repo,
        {"task_id": "stage2"},
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3, cost_limit=0.25),
        runner=_runner(stdout=_usage_line(), seen_commands=commands, seen_kwargs=kwargs_seen),
    )

    command = commands[0]
    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert "stage2" in command[command.index("-t") + 1]
    assert "-m" not in command
    assert "gemini/gemini-3.1-flash-lite" not in command
    assert command[command.index("-l") + 1] == "0.25"
    _assert_mini_v242_required_flags(command, kwargs_seen[0], max_steps=3)


def test_adapter_explicit_env_model_adds_model_override(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    commands: list[list[str]] = []
    kwargs_seen: list[dict] = []
    monkeypatch.setenv("SYNAPSE_MINI_WORKER_MODEL", "gemini/gemini-2.5-flash-lite")

    result = run_mini_worker(
        repo,
        {"task_id": "stage2"},
        ("allowed.py",),
        runner=_runner(stdout=_usage_line(), seen_commands=commands, seen_kwargs=kwargs_seen),
    )

    command = commands[0]
    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert command[command.index("-m") + 1] == "gemini/gemini-2.5-flash-lite"
    _assert_mini_v242_required_flags(command, kwargs_seen[0], max_steps=50)


def test_adapter_blank_env_model_omits_model_override(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    commands: list[list[str]] = []
    kwargs_seen: list[dict] = []
    monkeypatch.setenv("SYNAPSE_MINI_WORKER_MODEL", "   ")

    result = run_mini_worker(
        repo,
        {"task_id": "stage2"},
        ("allowed.py",),
        runner=_runner(stdout=_usage_line(), seen_commands=commands, seen_kwargs=kwargs_seen),
    )

    command = commands[0]
    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert "-m" not in command
    _assert_mini_v242_required_flags(command, kwargs_seen[0], max_steps=50)


def test_adapter_reads_tool_usage_from_trajectory_file(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "trajectory usage task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(stdout="stdout has no usage", trajectory=_trajectory_usage()),
    )

    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert result.usage.token_status is ExternalWorkerTokenStatus.TOOL_REPORTED
    assert result.usage.input_tokens == 11
    assert result.usage.output_tokens == 7
    assert result.usage.thinking_tokens == 5
    assert result.usage.total_tokens == 23
    assert result.usage.thinking_included is True
    assert result.diagnostics["raw_usage_ref"].startswith("trajectory:")


def test_adapter_returns_no_patch_and_unavailable_usage_when_worker_changes_nothing(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "no-op task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(mutate=None, stdout="no usage here"),
    )

    assert result.worker_status is ExternalWorkerStatus.NO_PATCH
    assert result.diff_text is None
    assert result.touched_files == ()
    assert result.usage.token_status is ExternalWorkerTokenStatus.UNAVAILABLE
    assert result.usage.total_tokens is None
    assert result.usage.thinking_included is False


def test_adapter_maps_nonzero_exit_to_error_without_verification_claim(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "failing task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(mutate=None, stderr="mini failed", returncode=2),
    )

    assert result.worker_status is ExternalWorkerStatus.ERROR
    assert result.worker_report.failure_reason == "mini failed"
    assert "SUCCESS" not in result.to_dict()["worker_status"]
    assert "COMPLETE" not in result.to_dict()["worker_status"]


def test_adapter_maps_timeout_without_hanging_or_inventing_usage(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "timeout task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=1, max_steps=3),
        runner=_runner(timeout=True),
    )

    assert result.worker_status is ExternalWorkerStatus.TIMEOUT
    assert result.diff_text is None
    assert result.usage.token_status is ExternalWorkerTokenStatus.UNAVAILABLE
    assert result.usage.total_tokens is None


def test_adapter_thinking_guard_marks_reported_undercount_false(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "usage task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(stdout=_usage_line(total_tokens=12)),
    )

    assert result.usage.token_status is ExternalWorkerTokenStatus.PROVIDER_REPORTED
    assert result.usage.total_tokens == 12
    assert result.usage.thinking_included is False


def test_adapter_reports_scope_violations_as_diagnostics_not_success(tmp_path):
    repo = _repo(tmp_path)

    result = run_mini_worker(
        repo,
        "scope task",
        ("allowed.py",),
        config=MiniAdapterConfig(command=("mini",), timeout_seconds=30, max_steps=3),
        runner=_runner(mutate="outside.py", stdout=_usage_line()),
    )

    assert result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH
    assert result.touched_files == ("outside.py",)
    assert result.diagnostics["scope_violations"] == ("outside.py",)


def test_worker_status_model_has_no_complete_or_success():
    status_values = {status.value for status in ExternalWorkerStatus}

    assert status_values == {"PROPOSED_PATCH", "NO_PATCH", "ERROR", "TIMEOUT"}
    assert "COMPLETE" not in status_values
    assert "SUCCESS" not in status_values
