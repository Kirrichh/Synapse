"""mini-swe-agent subprocess adapter for Gold-arm candidate generation."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from .contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
    WorkerReport,
)


RunCallable = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class MiniAdapterConfig:
    command: tuple[str, ...] = ("mini",)
    timeout_seconds: int = 600
    max_steps: int = 50
    cost_limit: float = 0.5
    model: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "MiniAdapterConfig":
        env = environ or os.environ
        raw_command = env.get("SYNAPSE_MINI_WORKER_COMMAND", "mini")
        command = tuple(shlex.split(raw_command)) or ("mini",)
        timeout_raw = env.get("SYNAPSE_MINI_WORKER_TIMEOUT_SECONDS", "600")
        max_steps_raw = env.get("SYNAPSE_MINI_WORKER_MAX_STEPS", "50")
        cost_limit_raw = env.get("SYNAPSE_MINI_WORKER_COST_LIMIT", "0.5")
        raw_model = env.get("SYNAPSE_MINI_WORKER_MODEL")
        model = raw_model.strip() if raw_model and raw_model.strip() else None
        return cls(
            command=command,
            timeout_seconds=_positive_int(timeout_raw, default=600),
            max_steps=_positive_int(max_steps_raw, default=50),
            cost_limit=_nonnegative_float(cost_limit_raw, default=0.5),
            model=model,
        )


def run_mini_worker(
    worktree_path: str | Path,
    task: Mapping[str, Any] | str,
    allowed_scope: Sequence[str],
    *,
    config: MiniAdapterConfig | None = None,
    runner: RunCallable = subprocess.run,
    platform_name: str | None = None,
) -> ExternalCodingWorkerResult:
    """Run mini as an external subprocess and return a typed candidate envelope.

    The adapter does not own worktree lifecycle, apply patches, run verification,
    or interpret a diff as accepted work.
    """

    resolved_config = config or MiniAdapterConfig.from_env()
    worktree = Path(worktree_path)
    task_statement = _build_task_statement(task, allowed_scope, resolved_config.max_steps)
    trajectory_path = _new_trajectory_path(worktree)
    command = [
        *resolved_config.command,
        "-t",
        task_statement,
    ]
    if resolved_config.model:
        command.extend(("-m", resolved_config.model))
    command.extend(
        (
            "-y",
            "-l",
            _format_cost_limit(resolved_config.cost_limit),
            "--exit-immediately",
            "-c",
            "mini.yaml",
            "-c",
            f"agent.step_limit={resolved_config.max_steps}",
            "-o",
            str(trajectory_path),
        )
    )
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")
    stdio_mode = _stdio_mode(platform_name)
    command_summary = {
        "command": tuple(_redact_command_part(part) for part in command),
        "cwd": str(worktree),
        "timeout_seconds": resolved_config.timeout_seconds,
        "stdio_mode": stdio_mode,
    }
    run_kwargs: dict[str, Any] = {
        "cwd": str(worktree),
        "text": True,
        "timeout": resolved_config.timeout_seconds,
        "env": child_env,
    }
    if stdio_mode == "inherit_console":
        run_kwargs.update({"stdout": None, "stderr": None})
    else:
        run_kwargs["capture_output"] = True

    try:
        completed = runner(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        _cleanup_trajectory(trajectory_path)
        return ExternalCodingWorkerResult(
            worker_status=ExternalWorkerStatus.TIMEOUT,
            diff_text=None,
            touched_files=(),
            usage=_unavailable_usage(),
            diagnostics={
                "scope_violations": (),
                "command_ledger_summary": command_summary,
                "stdio_mode": stdio_mode,
            },
            worker_report=WorkerReport(failure_reason="worker_timeout"),
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    usage = parse_worker_usage(stdout, stderr, trajectory_path=trajectory_path)
    _cleanup_trajectory(trajectory_path)
    diff_text = _git_diff(worktree, runner=runner)
    tracked_files = _git_diff_name_only(worktree, runner=runner)
    untracked_files = _git_untracked_files(worktree, runner=runner)
    touched_files = _merge_repo_paths(tracked_files, untracked_files)
    scope_violations = _scope_violations(touched_files, allowed_scope)
    diagnostics: dict[str, Any] = {
        "scope_violations": scope_violations,
        "command_ledger_summary": {
            **command_summary,
            "returncode": completed.returncode,
        },
        "raw_usage_ref": usage.diagnostics.get("raw_usage_ref"),
        "stdio_mode": stdio_mode,
        "tracked_files": tracked_files,
        "untracked_files": untracked_files,
    }
    if untracked_files:
        diagnostics["untracked_files_not_in_diff_text"] = untracked_files
    if completed.returncode != 0:
        return ExternalCodingWorkerResult(
            worker_status=ExternalWorkerStatus.ERROR,
            diff_text=diff_text or None,
            touched_files=touched_files,
            usage=usage,
            diagnostics=diagnostics,
            worker_report=WorkerReport(
                summary=_first_line(stdout),
                failure_reason=_first_line(stderr) or f"worker_exit_{completed.returncode}",
            ),
        )
    status = ExternalWorkerStatus.PROPOSED_PATCH if diff_text or untracked_files else ExternalWorkerStatus.NO_PATCH
    return ExternalCodingWorkerResult(
        worker_status=status,
        diff_text=diff_text or None,
        touched_files=touched_files,
        usage=usage,
        diagnostics=diagnostics,
        worker_report=WorkerReport(summary=_first_line(stdout)),
    )


def parse_worker_usage(
    stdout: str,
    stderr: str = "",
    *,
    trajectory_path: str | Path | None = None,
) -> ExternalWorkerUsage:
    if trajectory_path is not None:
        usage = _usage_from_trajectory(Path(trajectory_path))
        if usage is not None:
            return usage
    for source_name, text in (("stdout", stdout), ("stderr", stderr)):
        usage = _usage_from_json_lines(text, source_name)
        if usage is not None:
            return usage
        usage = _usage_from_key_value_text(text, source_name)
        if usage is not None:
            return usage
    return _unavailable_usage()


def _stdio_mode(platform_name: str | None = None) -> str:
    return "inherit_console" if (platform_name or os.name) == "nt" else "capture_output"


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_float(value: str, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _format_cost_limit(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _new_trajectory_path(worktree: Path) -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix=".synapse-mini-",
        suffix=".trajectory.json",
        dir=str(worktree),
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def _cleanup_trajectory(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _build_task_statement(task: Mapping[str, Any] | str, allowed_scope: Sequence[str], max_steps: int) -> str:
    if isinstance(task, str):
        task_text = task
    else:
        task_text = str(
            task.get("task")
            or task.get("issue")
            or task.get("statement")
            or task.get("description")
            or json.dumps(task, indent=2, sort_keys=True, ensure_ascii=False)
        )
    scope = "\n".join(f"- {path}" for path in allowed_scope)
    return (
        "You are an external coding worker. Produce a candidate diff only.\n"
        "Do not claim the task is verified or accepted.\n"
        f"Max steps: {max_steps}\n\n"
        "Allowed scope guidance:\n"
        f"{scope}\n\n"
        "Task:\n"
        f"{task_text}\n"
    )


def _git_diff(worktree: Path, *, runner: RunCallable) -> str:
    completed = runner(
        ["git", "diff"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
        timeout=60,
    )
    return completed.stdout or ""


def _git_diff_name_only(worktree: Path, *, runner: RunCallable) -> tuple[str, ...]:
    completed = runner(
        ["git", "diff", "--name-only"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
        timeout=60,
    )
    return tuple(_normalize_repo_path(line) for line in (completed.stdout or "").splitlines() if line.strip())


def _git_untracked_files(worktree: Path, *, runner: RunCallable) -> tuple[str, ...]:
    completed = runner(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=str(worktree),
        text=True,
        capture_output=True,
        timeout=60,
    )
    return tuple(_normalize_repo_path(line) for line in (completed.stdout or "").splitlines() if line.strip())


def _merge_repo_paths(*groups: Sequence[str]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for path in group:
            normalized = _normalize_repo_path(path)
            if normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return tuple(merged)


def _normalize_repo_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _scope_violations(touched_files: Sequence[str], allowed_scope: Sequence[str]) -> tuple[str, ...]:
    allowed = tuple(_normalize_repo_path(path) for path in allowed_scope)
    violations: list[str] = []
    for touched in touched_files:
        if not any(touched == item or (item.endswith("/") and touched.startswith(item)) for item in allowed):
            violations.append(touched)
    return tuple(sorted(violations))


def _usage_from_json_lines(text: str, source_name: str) -> ExternalWorkerUsage | None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        usage_payload = _find_usage_payload(payload)
        if usage_payload is None:
            continue
        return _usage_from_mapping(
            usage_payload,
            raw_usage_ref=f"{source_name}:json-line:{line_number}",
            token_status=ExternalWorkerTokenStatus.PROVIDER_REPORTED,
        )
    return None


def _usage_from_trajectory(path: Path) -> ExternalWorkerUsage | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    usage_payload = _find_usage_payload_recursive(payload)
    if usage_payload is None:
        return None
    return _usage_from_mapping(
        usage_payload,
        raw_usage_ref=f"trajectory:{path.name}",
        token_status=ExternalWorkerTokenStatus.TOOL_REPORTED,
    )


def _find_usage_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("usage", "token_usage", "llm_usage", "usageMetadata"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    if any(key in payload for key in ("total_tokens", "totalTokenCount", "prompt_tokens", "promptTokenCount")):
        return payload
    return None


def _find_usage_payload_recursive(payload: Any) -> Mapping[str, Any] | None:
    if isinstance(payload, Mapping):
        usage = _find_usage_payload(payload)
        if usage is not None:
            return usage
        for value in payload.values():
            usage = _find_usage_payload_recursive(value)
            if usage is not None:
                return usage
    elif isinstance(payload, list):
        for value in payload:
            usage = _find_usage_payload_recursive(value)
            if usage is not None:
                return usage
    return None


def _usage_from_key_value_text(text: str, source_name: str) -> ExternalWorkerUsage | None:
    total = _regex_int(text, r"(?i)\btotal[_ ]?tokens?\b\s*[:=]\s*(\d+)")
    if total is None:
        return None
    payload = {
        "prompt_tokens": _regex_int(text, r"(?i)\b(?:prompt|input)[_ ]?tokens?\b\s*[:=]\s*(\d+)"),
        "completion_tokens": _regex_int(text, r"(?i)\b(?:completion|output|candidate)[_ ]?tokens?\b\s*[:=]\s*(\d+)"),
        "thinking_tokens": _regex_int(text, r"(?i)\b(?:thinking|thoughts?)[_ ]?tokens?\b\s*[:=]\s*(\d+)"),
        "total_tokens": total,
    }
    return _usage_from_mapping(
        payload,
        raw_usage_ref=f"{source_name}:key-value",
        token_status=ExternalWorkerTokenStatus.PROVIDER_REPORTED,
    )


def _usage_from_mapping(
    payload: Mapping[str, Any],
    *,
    raw_usage_ref: str,
    token_status: ExternalWorkerTokenStatus,
) -> ExternalWorkerUsage:
    input_tokens = _int_from_keys(payload, "input_tokens", "prompt_tokens", "promptTokenCount")
    output_tokens = _int_from_keys(payload, "output_tokens", "completion_tokens", "candidatesTokenCount")
    thinking_tokens = _int_from_keys(payload, "thinking_tokens", "thoughtsTokenCount")
    total_tokens = _int_from_keys(payload, "total_tokens", "totalTokenCount")
    resolved_token_status = token_status if total_tokens is not None else ExternalWorkerTokenStatus.UNAVAILABLE
    thinking_included = False
    if total_tokens is not None and None not in (input_tokens, output_tokens, thinking_tokens):
        thinking_included = total_tokens >= input_tokens + output_tokens + thinking_tokens
    return ExternalWorkerUsage(
        token_status=resolved_token_status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total_tokens,
        thinking_included=thinking_included,
        diagnostics={
            "raw_usage_ref": raw_usage_ref,
            "reported_fields": tuple(sorted(str(key) for key in payload.keys())),
        },
    )


def _int_from_keys(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _regex_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return int(match.group(1))


def _unavailable_usage() -> ExternalWorkerUsage:
    return ExternalWorkerUsage(
        token_status=ExternalWorkerTokenStatus.UNAVAILABLE,
        input_tokens=None,
        output_tokens=None,
        thinking_tokens=None,
        total_tokens=None,
        thinking_included=False,
        diagnostics={},
    )


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:500]
    return None


def _redact_command_part(part: str) -> str:
    lowered = part.lower()
    if "key" in lowered or "token" in lowered or "secret" in lowered:
        return "<redacted>"
    return part
