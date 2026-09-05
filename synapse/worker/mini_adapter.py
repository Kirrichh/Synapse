"""mini-swe-agent subprocess adapter for Gold-arm candidate generation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence

from synapse.experiments.gold.stage10.repository_scope import (
    RepositoryScope,
    normalize_repository_path,
)
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateReport,
    WorkerCandidateResult,
    WorkerCandidateStatus,
    WorkerCandidateUsage,
    WorkerDeliveryEvidence,
    WorkerDeliveryStatus,
    WorkerInvocation,
    WorkerTokenStatus,
)

from .contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
    WorkerReport,
)


RunCallable = Callable[..., subprocess.CompletedProcess[str]]
_MAX_PORTABLE_COMMAND_LINE_UTF16_UNITS = 32_767


class _MiniDispatchRefusal(OSError):
    def __init__(self, failure_reason: str) -> None:
        self.failure_reason = failure_reason
        super().__init__(failure_reason)


class _MiniRepositoryObservationFailure(OSError):
    pass


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
    task_statement = _build_task_statement(task, allowed_scope, resolved_config.max_steps)
    return _run_mini_worker_core(
        worktree_path,
        task_statement,
        allowed_scope,
        config=resolved_config,
        runner=runner,
        platform_name=platform_name,
    )


def run_mini_worker_invocation(
    worktree_path: str | Path,
    invocation: WorkerInvocation,
    *,
    config: MiniAdapterConfig | None = None,
    runner: RunCallable = subprocess.run,
    platform_name: str | None = None,
) -> WorkerCandidateResult:
    """Dispatch an exact pre-rendered Stage 10 invocation without rewriting it."""

    if type(invocation) is not WorkerInvocation:
        raise TypeError("invocation must be an exact WorkerInvocation")
    resolved_config = config or MiniAdapterConfig.from_env()
    try:
        result = _run_mini_worker_core(
            worktree_path,
            invocation.payload_text,
            invocation.allowed_scope,
            config=resolved_config,
            runner=runner,
            platform_name=platform_name,
        )
    except _MiniDispatchRefusal as exc:
        return _not_dispatched_candidate(invocation, failure_reason=exc.failure_reason)
    except OSError:
        return _not_dispatched_candidate(
            invocation,
            failure_reason="worker_process_not_started",
        )
    return _candidate_result(
        result,
        evidence=_delivery_evidence(
            invocation,
            status=WorkerDeliveryStatus.PROCESS_STARTED,
        ),
    )


def _delivery_evidence(
    invocation: WorkerInvocation,
    *,
    status: WorkerDeliveryStatus,
) -> WorkerDeliveryEvidence:
    return WorkerDeliveryEvidence(
        invocation_id=invocation.invocation_id,
        context_id=invocation.context_id,
        payload_sha256=invocation.payload_sha256,
        payload_byte_length=invocation.payload_byte_length,
        envelope_sha256=invocation.envelope_sha256,
        status=status,
        transport_name="mini-swe-agent-subprocess/v1",
    )


def _not_dispatched_candidate(
    invocation: WorkerInvocation,
    *,
    failure_reason: str,
) -> WorkerCandidateResult:
    return WorkerCandidateResult(
        status=WorkerCandidateStatus.ERROR,
        diff_text=None,
        touched_files=(),
        usage=WorkerCandidateUsage(
            token_status=WorkerTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            thinking_tokens=None,
            total_tokens=None,
            thinking_included=False,
        ),
        diagnostics={
            "scope_violations": (),
            "delivery_failure": failure_reason,
        },
        report=WorkerCandidateReport(failure_reason=failure_reason),
        delivery_evidence=_delivery_evidence(
            invocation,
            status=WorkerDeliveryStatus.NOT_DISPATCHED,
        ),
    )


def _candidate_result(
    result: ExternalCodingWorkerResult,
    *,
    evidence: WorkerDeliveryEvidence,
) -> WorkerCandidateResult:
    usage = result.usage
    return WorkerCandidateResult(
        status=WorkerCandidateStatus(result.worker_status.value),
        diff_text=result.diff_text,
        touched_files=result.touched_files,
        usage=WorkerCandidateUsage(
            token_status=WorkerTokenStatus(usage.token_status.value),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thinking_tokens=usage.thinking_tokens,
            total_tokens=usage.total_tokens,
            thinking_included=usage.thinking_included,
            diagnostics=usage.diagnostics,
        ),
        diagnostics=result.diagnostics,
        report=WorkerCandidateReport(
            summary=result.worker_report.summary,
            failure_reason=result.worker_report.failure_reason,
        ),
        delivery_evidence=evidence,
    )


class MiniWorkerTransport:
    """Narrow transport object suitable for the Stage 10 adapter port."""

    def __init__(self, *, config: MiniAdapterConfig | None = None) -> None:
        self._config = config

    @property
    def config(self) -> MiniAdapterConfig | None:
        return self._config

    def run(
        self,
        worktree_path: str | Path,
        invocation: WorkerInvocation,
    ) -> WorkerCandidateResult:
        return run_mini_worker_invocation(
            worktree_path,
            invocation,
            config=self._config,
        )


@dataclass(frozen=True)
class _MiniDispatchPlan:
    worktree: Path
    repository_scope: RepositoryScope
    trajectory_path: Path
    command: list[str]
    command_summary: dict[str, Any]
    run_kwargs: dict[str, Any]
    stdio_mode: str


@dataclass(frozen=True)
class _MiniProcessOutcome:
    completed: subprocess.CompletedProcess[str]
    stdout: str
    stderr: str
    usage: ExternalWorkerUsage


@dataclass(frozen=True)
class _MiniRepositoryObservation:
    diff_text: str
    tracked_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    touched_files: tuple[str, ...]
    scope_violations: tuple[str, ...]


def _run_mini_worker_core(
    worktree_path: str | Path,
    task_statement: str,
    allowed_scope: Sequence[str],
    *,
    config: MiniAdapterConfig,
    runner: RunCallable,
    platform_name: str | None,
) -> ExternalCodingWorkerResult:
    """Shared subprocess implementation for legacy and exact typed callers."""

    plan = _prepare_mini_dispatch(
        worktree_path,
        task_statement,
        allowed_scope,
        config=config,
        runner=runner,
        platform_name=platform_name,
    )
    process = _execute_mini_process(plan, runner=runner)
    if process is None:
        return _timeout_worker_result(plan)
    try:
        observation = _observe_worker_repository(plan, runner=runner)
    except _MiniRepositoryObservationFailure:
        return _repository_observation_failure_result(plan, process)
    return _normalize_worker_process_result(plan, process, observation)


def _prepare_mini_dispatch(
    worktree_path: str | Path,
    task_statement: str,
    allowed_scope: Sequence[str],
    *,
    config: MiniAdapterConfig,
    runner: RunCallable,
    platform_name: str | None,
) -> _MiniDispatchPlan:
    worktree = Path(worktree_path)
    if not worktree.is_dir():
        raise _MiniDispatchRefusal("worker_worktree_not_git_repository")
    repository_scope = RepositoryScope(tuple(allowed_scope))
    trajectory_path = _new_trajectory_path(worktree)
    command = _build_mini_command(
        task_statement,
        config=config,
        trajectory_path=trajectory_path,
    )
    try:
        _require_portable_command_line(command)
        _require_git_worktree(worktree, runner=runner)
    except _MiniDispatchRefusal:
        _cleanup_trajectory(trajectory_path)
        raise
    child_env = dict(os.environ)
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    child_env.setdefault("PYTHONUTF8", "1")
    stdio_mode = _stdio_mode(platform_name)
    task_bytes = task_statement.encode("utf-8")
    summary_command = list(command)
    summary_command[summary_command.index("-t") + 1] = (
        f"<typed-task sha256={hashlib.sha256(task_bytes).hexdigest()} bytes={len(task_bytes)}>"
    )
    command_summary = {
        "command": tuple(_redact_command_part(part) for part in summary_command),
        "cwd": str(worktree),
        "timeout_seconds": config.timeout_seconds,
        "stdio_mode": stdio_mode,
        "stdin_mode": "devnull",
    }
    run_kwargs: dict[str, Any] = {
        "cwd": str(worktree),
        "text": True,
        "timeout": config.timeout_seconds,
        "env": child_env,
        "stdin": subprocess.DEVNULL,
    }
    if stdio_mode == "inherit_console":
        run_kwargs.update({"stdout": None, "stderr": None})
    else:
        run_kwargs["capture_output"] = True
    return _MiniDispatchPlan(
        worktree=worktree,
        repository_scope=repository_scope,
        trajectory_path=trajectory_path,
        command=command,
        command_summary=command_summary,
        run_kwargs=run_kwargs,
        stdio_mode=stdio_mode,
    )


def _build_mini_command(
    task_statement: str,
    *,
    config: MiniAdapterConfig,
    trajectory_path: Path,
) -> list[str]:
    command = [*config.command, "-t", task_statement]
    if config.model:
        command.extend(("-m", config.model))
    command.extend(
        (
            "-y",
            "-l",
            _format_cost_limit(config.cost_limit),
            "--exit-immediately",
            "-c",
            "mini.yaml",
            "-c",
            f"agent.step_limit={config.max_steps}",
            "-o",
            str(trajectory_path),
        )
    )
    return command


def _execute_mini_process(
    plan: _MiniDispatchPlan,
    *,
    runner: RunCallable,
) -> _MiniProcessOutcome | None:
    try:
        completed = runner(plan.command, **plan.run_kwargs)
    except subprocess.TimeoutExpired:
        _cleanup_trajectory(plan.trajectory_path)
        return None
    except BaseException:
        _cleanup_trajectory(plan.trajectory_path)
        raise
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    try:
        usage = parse_worker_usage(
            stdout,
            stderr,
            trajectory_path=plan.trajectory_path,
        )
    finally:
        _cleanup_trajectory(plan.trajectory_path)
    return _MiniProcessOutcome(completed, stdout, stderr, usage)


def _timeout_worker_result(plan: _MiniDispatchPlan) -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus.TIMEOUT,
        diff_text=None,
        touched_files=(),
        usage=_unavailable_usage(),
        diagnostics={
            "scope_violations": (),
            "command_ledger_summary": plan.command_summary,
            "stdio_mode": plan.stdio_mode,
            "stdin_mode": "devnull",
        },
        worker_report=WorkerReport(failure_reason="worker_timeout"),
    )


def _observe_worker_repository(
    plan: _MiniDispatchPlan,
    *,
    runner: RunCallable,
) -> _MiniRepositoryObservation:
    diff_text = _run_git_probe(plan.worktree, ("diff",), runner=runner)
    tracked_files = _git_diff_name_only(plan.worktree, runner=runner)
    untracked_files = _git_untracked_files(plan.worktree, runner=runner)
    touched_files = _merge_repo_paths(tracked_files, untracked_files)
    return _MiniRepositoryObservation(
        diff_text=diff_text,
        tracked_files=tracked_files,
        untracked_files=untracked_files,
        touched_files=touched_files,
        scope_violations=_scope_violations(
            touched_files,
            plan.repository_scope,
        ),
    )


def _repository_observation_failure_result(
    plan: _MiniDispatchPlan,
    process: _MiniProcessOutcome,
) -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus.ERROR,
        diff_text=None,
        touched_files=(),
        usage=process.usage,
        diagnostics={
            "scope_violations": (),
            "command_ledger_summary": {
                **plan.command_summary,
                "returncode": process.completed.returncode,
            },
            "repository_observation": "FAILED",
            "stdio_mode": plan.stdio_mode,
            "stdin_mode": "devnull",
            "tracked_files": (),
            "untracked_files": (),
        },
        worker_report=WorkerReport(
            failure_reason="worker_repository_observation_failed",
        ),
    )


def _normalize_worker_process_result(
    plan: _MiniDispatchPlan,
    process: _MiniProcessOutcome,
    observation: _MiniRepositoryObservation,
) -> ExternalCodingWorkerResult:
    diagnostics: dict[str, Any] = {
        "scope_violations": observation.scope_violations,
        "command_ledger_summary": {
            **plan.command_summary,
            "returncode": process.completed.returncode,
        },
        "raw_usage_ref": process.usage.diagnostics.get("raw_usage_ref"),
        "stdio_mode": plan.stdio_mode,
        "stdin_mode": "devnull",
        "tracked_files": observation.tracked_files,
        "untracked_files": observation.untracked_files,
    }
    if observation.untracked_files:
        diagnostics["untracked_files_not_in_diff_text"] = observation.untracked_files
    if process.completed.returncode != 0:
        return ExternalCodingWorkerResult(
            worker_status=ExternalWorkerStatus.ERROR,
            diff_text=observation.diff_text or None,
            touched_files=observation.touched_files,
            usage=process.usage,
            diagnostics=diagnostics,
            worker_report=WorkerReport(
                summary=_first_line(process.stdout),
                failure_reason=(
                    _first_line(process.stderr)
                    or f"worker_exit_{process.completed.returncode}"
                ),
            ),
        )
    status = (
        ExternalWorkerStatus.PROPOSED_PATCH
        if observation.diff_text or observation.untracked_files
        else ExternalWorkerStatus.NO_PATCH
    )
    return ExternalCodingWorkerResult(
        worker_status=status,
        diff_text=observation.diff_text or None,
        touched_files=observation.touched_files,
        usage=process.usage,
        diagnostics=diagnostics,
        worker_report=WorkerReport(summary=_first_line(process.stdout)),
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


def _require_portable_command_line(command: Sequence[str]) -> None:
    if any(type(part) is not str or "\x00" in part for part in command):
        raise _MiniDispatchRefusal("worker_command_not_portable")
    rendered = subprocess.list2cmdline(command)
    utf16_units_with_terminator = len(rendered.encode("utf-16-le")) // 2 + 1
    if utf16_units_with_terminator > _MAX_PORTABLE_COMMAND_LINE_UTF16_UNITS:
        raise _MiniDispatchRefusal("worker_payload_exceeds_transport_limit")


def _require_git_worktree(worktree: Path, *, runner: RunCallable) -> None:
    try:
        completed = runner(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(worktree),
            text=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _MiniDispatchRefusal("worker_worktree_not_git_repository") from exc
    if (
        completed.returncode != 0
        or type(completed.stdout) is not str
        or completed.stdout.strip() != "true"
    ):
        raise _MiniDispatchRefusal("worker_worktree_not_git_repository")


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


def _git_diff_name_only(worktree: Path, *, runner: RunCallable) -> tuple[str, ...]:
    stdout = _run_git_probe(worktree, ("diff", "--name-only"), runner=runner)
    return tuple(_normalize_repo_path(line) for line in stdout.splitlines() if line.strip())


def _git_untracked_files(worktree: Path, *, runner: RunCallable) -> tuple[str, ...]:
    stdout = _run_git_probe(
        worktree,
        ("ls-files", "--others", "--exclude-standard"),
        runner=runner,
    )
    return tuple(_normalize_repo_path(line) for line in stdout.splitlines() if line.strip())


def _run_git_probe(
    worktree: Path,
    arguments: Sequence[str],
    *,
    runner: RunCallable,
) -> str:
    try:
        completed = runner(
            ["git", *arguments],
            cwd=str(worktree),
            text=True,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _MiniRepositoryObservationFailure from exc
    if completed.returncode != 0 or type(completed.stdout) is not str:
        raise _MiniRepositoryObservationFailure
    return completed.stdout


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
    if type(path) is not str:
        raise TypeError("git repository path must be an exact string")
    return normalize_repository_path(
        path.replace("\\", "/"),
        field_name="git repository path",
    )


def _scope_violations(
    touched_files: Sequence[str],
    allowed_scope: RepositoryScope,
) -> tuple[str, ...]:
    violations: list[str] = []
    for touched in touched_files:
        if not allowed_scope.covers(touched):
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
    if not isinstance(payload, Mapping):
        return None
    info = payload.get("info", {})
    stats = info.get("model_stats", {}) if isinstance(info, Mapping) else {}
    if not isinstance(stats, Mapping):
        return _unavailable_usage()
    # Only a declared run aggregate can bypass per-call reconciliation. A
    # recursive search used to report the first model call as the whole run.
    aggregate = _find_usage_payload(stats) or _find_usage_payload(payload)
    if aggregate is not None:
        return _usage_from_mapping(
            aggregate,
            raw_usage_ref=f"trajectory:{path.name}:aggregate",
            token_status=ExternalWorkerTokenStatus.TOOL_REPORTED,
        )
    messages, calls = payload.get("messages"), stats.get("api_calls")
    if type(messages) is not list or type(calls) is not int or calls < 0:
        return _unavailable_usage()
    responses = [item for item in messages if isinstance(item, Mapping) and item.get("role") == "assistant"]
    if len(responses) != calls:
        return _unavailable_usage()
    usages = []
    for message in responses:
        extra = message.get("extra")
        response = extra.get("response") if isinstance(extra, Mapping) else None
        raw = _find_usage_payload(response) if isinstance(response, Mapping) else None
        if raw is None:
            return _unavailable_usage()
        usage = _usage_from_mapping(raw, raw_usage_ref="trajectory:call", token_status=ExternalWorkerTokenStatus.TOOL_REPORTED)
        if usage.total_tokens is None:
            return _unavailable_usage()
        usages.append(usage)
    totals = {
        field: sum(getattr(item, field) for item in usages)
        if all(getattr(item, field) is not None for item in usages) else None
        for field in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens")
    }
    return ExternalWorkerUsage(
        token_status=ExternalWorkerTokenStatus.TOOL_REPORTED,
        **totals,
        thinking_included=all(item.thinking_included for item in usages),
        diagnostics={"raw_usage_ref": f"trajectory:{path.name}:calls", "reported_api_calls": calls},
    )


def _find_usage_payload(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in ("usage", "token_usage", "llm_usage", "usageMetadata"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return value
    if any(key in payload for key in ("total_tokens", "totalTokenCount", "prompt_tokens", "promptTokenCount")):
        return payload
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
