"""Independent before/after change oracle for Baseline and Gold attempts.

The oracle judges one candidate patch against an operator-declared check. It
takes a fresh detached checkout of the base revision, optionally applies the
evaluator's own patch (checks the task does not show), and runs the
fail-to-pass commands: every one must fail there, or the check cannot tell a
fix from no change and the verdict is an infrastructure error. A second fresh
checkout receives the evaluator patch and the candidate patch, so nothing the
first run left behind (caches, outputs) reaches it, and the fail-to-pass and
pass-to-pass commands run there; the task is resolved only when all of them
pass. A check that cannot start, or exits 126 or 127 (the shell's "cannot
execute" and "command not found"), gave no verdict: that is an infrastructure
error in either checkout. The verdict is written as an instance-level report
that C1 can reopen without running anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import hashlib
import json
import platform
import re
import subprocess
import time
import uuid

from synapse.change.workspace import cleanup_worktree, create_detached_worktree, git_binary

from .contract import BaselineTask, OracleResult
from .patch_extract import extract_model_patch_from_worktree


ADAPTER_VERSION = "synapse.applied_verification.change_oracle/v1"
REPORT_SCHEMA = "synapse.applied_verification.change_oracle_report/v1"
REPORT_KIND = "oracle_report"
CHECK_LOG_KIND = "oracle_check_log"
FAIL_TO_PASS = "fail_to_pass"
PASS_TO_PASS = "pass_to_pass"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Exit statuses with which a shell reports that the command never ran.
_UNAVAILABLE_EXIT_CODES = frozenset({126, 127})


def _commands(value: object, field_name: str, *, allow_empty: bool) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"change oracle: {field_name} must be a sequence of argv")
    commands = []
    for command in value:
        if (not isinstance(command, (list, tuple)) or not command
                or any(type(part) is not str or not part for part in command)):
            raise ValueError(f"change oracle: every {field_name} command must be a non-empty argv")
        commands.append(tuple(command))
    if not commands and not allow_empty:
        raise ValueError(f"change oracle: {field_name} must not be empty")
    return tuple(commands)


@dataclass(frozen=True)
class ChangeOracleConfig:
    work_dir: Path
    fail_to_pass: tuple[tuple[str, ...], ...]
    pass_to_pass: tuple[tuple[str, ...], ...] = ()
    evaluator_patch: Path | None = None
    evaluator_patch_sha256: str | None = None
    command_timeout_seconds: int = 600
    max_summary_bytes: int = 4000
    max_summary_lines: int = 80

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_dir", Path(self.work_dir))
        object.__setattr__(self, "fail_to_pass", _commands(self.fail_to_pass, FAIL_TO_PASS, allow_empty=False))
        object.__setattr__(self, "pass_to_pass", _commands(self.pass_to_pass, PASS_TO_PASS, allow_empty=True))
        if (self.evaluator_patch is None) != (self.evaluator_patch_sha256 is None):
            raise ValueError("change oracle: an evaluator patch is declared together with its sha256")
        if self.evaluator_patch is not None:
            object.__setattr__(self, "evaluator_patch", Path(self.evaluator_patch))
            if type(self.evaluator_patch_sha256) is not str or not _SHA256_RE.fullmatch(self.evaluator_patch_sha256):
                raise ValueError("change oracle: evaluator_patch_sha256 must be a lowercase sha256")
        if type(self.command_timeout_seconds) is not int or self.command_timeout_seconds <= 0:
            raise ValueError("change oracle: command_timeout_seconds must be positive")
        if self.max_summary_bytes <= 0 or self.max_summary_lines <= 0:
            raise ValueError("change oracle: summary limits must be positive")


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_oracle_config_fingerprint_payload(config: ChangeOracleConfig) -> Mapping[str, object]:
    return {
        "adapter": ADAPTER_VERSION,
        "fail_to_pass": [list(command) for command in config.fail_to_pass],
        "pass_to_pass": [list(command) for command in config.pass_to_pass],
        "evaluator_patch_sha256": config.evaluator_patch_sha256,
        "command_timeout_seconds": config.command_timeout_seconds,
    }


def compute_oracle_config_fingerprint(payload: Mapping[str, object]) -> str:
    return _sha256_text(_canonical_json(payload))


def build_oracle_environment_fingerprint_payload(config: ChangeOracleConfig) -> Mapping[str, object]:
    # Where an arm keeps its invocations is not part of what judges it; C1
    # compares the retained location (``cwd``) to its boundary separately.
    return {
        "adapter": ADAPTER_VERSION,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def compute_oracle_environment_fingerprint(payload: Mapping[str, object]) -> str:
    return _sha256_text(_canonical_json(payload))


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (slug or "task")[:60]


def generate_oracle_run_id(task: BaselineTask) -> str:
    prefix = _safe_slug(task.task_id or task.instance_id)
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def oracle_base_diagnostics(config: ChangeOracleConfig, task: BaselineTask, *, adapter: str,
                            invocation_root: str) -> dict[str, object]:
    """The identity of one invocation, shared by the Baseline and Gold front ends."""
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config_payload = build_oracle_config_fingerprint_payload(config)
    env_payload = build_oracle_environment_fingerprint_payload(config)
    run_id = generate_oracle_run_id(task)
    invocation_dir = config.work_dir / invocation_root / run_id
    invocation_dir.mkdir(parents=True, exist_ok=True)
    return {
        "adapter": adapter,
        "task_id": task.task_id,
        "instance_id": task.instance_id,
        "oracle_config_fingerprint": compute_oracle_config_fingerprint(config_payload),
        "oracle_config_fingerprint_payload": dict(config_payload),
        "oracle_environment_fingerprint": compute_oracle_environment_fingerprint(env_payload),
        "oracle_environment_fingerprint_payload": dict(env_payload),
        "run_id": run_id,
        "invocation_dir": str(invocation_dir.resolve()),
        "cwd": str(config.work_dir),
    }


def _artifact_summary(path: Path, *, kind: str) -> dict[str, object] | None:
    if not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    return {
        "kind": kind,
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(data),
        "bytes": len(data),
    }


def summarize_text_for_carry(
    text: str,
    *,
    max_bytes: int,
    max_lines: int,
) -> tuple[str, Mapping[str, object]]:
    lines = text.splitlines()
    line_truncated = len(lines) > max_lines
    if line_truncated:
        head_count = max(1, max_lines // 2)
        tail_count = max(1, max_lines - head_count - 1)
        omitted = len(lines) - head_count - tail_count
        selected = [*lines[:head_count], f"... truncated {omitted} lines ...", *lines[-tail_count:]]
    else:
        selected = lines
    summary = "\n".join(selected)
    data = summary.encode("utf-8")
    byte_truncated = len(data) > max_bytes
    if byte_truncated:
        summary = data[:max_bytes].decode("utf-8", errors="ignore") + "\n... truncated by byte limit ..."
    return summary, {
        "line_count": len(lines),
        "byte_count": len(text.encode("utf-8")),
        "line_truncated": line_truncated,
        "byte_truncated": byte_truncated,
        "truncated": line_truncated or byte_truncated,
    }


def _coerce_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _stream_summary(
    *,
    stream_name: str,
    text: str,
    config: ChangeOracleConfig,
    diagnostics: Mapping[str, object],
) -> tuple[str, Mapping[str, object]]:
    excerpt, summary_diagnostics = summarize_text_for_carry(
        text,
        max_bytes=config.max_summary_bytes,
        max_lines=config.max_summary_lines,
    )
    lines = [
        f"Change oracle {stream_name} summary",
        f"failure_reason: {diagnostics.get('failure_reason')}",
        f"instance_id: {diagnostics.get('instance_id')}",
        f"resolved: {diagnostics.get('resolved')}",
        f"infra_error: {diagnostics.get('infra_error')}",
        f"candidate_invalid: {diagnostics.get('candidate_invalid')}",
        "excerpt:",
        excerpt,
    ]
    return "\n".join(lines), summary_diagnostics


def oracle_result(
    *,
    started: float,
    config: ChangeOracleConfig,
    diagnostics: dict[str, object],
    resolved: bool,
    returncode: int | None = None,
    stdout_text: str = "",
    stderr_text: str = "",
) -> OracleResult:
    diagnostics = {**diagnostics, "resolved": resolved, "returncode": returncode}
    diagnostics.setdefault("oracle_managed_artifacts", [])
    stdout_summary, stdout_diagnostics = _stream_summary(
        stream_name="stdout", text=stdout_text, config=config, diagnostics=diagnostics)
    stderr_summary, stderr_diagnostics = _stream_summary(
        stream_name="stderr", text=stderr_text, config=config, diagnostics=diagnostics)
    diagnostics = {
        **diagnostics,
        "stdout_summary_truncated": bool(stdout_diagnostics.get("truncated")),
        "stderr_summary_truncated": bool(stderr_diagnostics.get("truncated")),
        "stdout_summary_diagnostics": dict(stdout_diagnostics),
        "stderr_summary_diagnostics": dict(stderr_diagnostics),
    }
    return OracleResult(
        resolved=resolved,
        returncode=returncode,
        stdout=stdout_summary,
        stderr=stderr_summary,
        duration_seconds=time.perf_counter() - started,
        diagnostics=diagnostics,
    )


def failure_result(
    *,
    started: float,
    config: ChangeOracleConfig,
    diagnostics: dict[str, object],
    reason: str,
    infra_error: bool,
    candidate_invalid: bool,
    returncode: int | None = None,
    stdout_text: str = "",
    stderr_text: str = "",
) -> OracleResult:
    diagnostics = {
        **diagnostics,
        "infra_error": infra_error,
        "candidate_invalid": candidate_invalid,
        "failure_reason": reason,
    }
    return oracle_result(started=started, config=config, diagnostics=diagnostics, resolved=False,
                         returncode=returncode, stdout_text=stdout_text, stderr_text=stderr_text)


@dataclass(frozen=True)
class ChangeOracleReport:
    target_instance_found: bool
    resolved: bool
    diagnostics: Mapping[str, object]


def _report_failure(reason: str, **extra: object) -> ChangeOracleReport:
    return ChangeOracleReport(
        target_instance_found=False,
        resolved=False,
        diagnostics={"infra_error": True, "failure_reason": reason, **extra},
    )


def parse_change_oracle_report(report_path: Path, instance_id: str) -> ChangeOracleReport:
    report_path = Path(report_path)
    if not report_path.exists():
        return _report_failure("oracle_report_missing", report_path=str(report_path))
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _report_failure("oracle_report_malformed", report_path=str(report_path),
                               exception_type=type(exc).__name__)
    if type(payload) is not dict or payload.get("schema") != REPORT_SCHEMA:
        return _report_failure("oracle_report_malformed", report_path=str(report_path))
    if payload.get("instance_id") != instance_id:
        return _report_failure("oracle_report_target_missing", report_path=str(report_path), instance_id=instance_id)
    if type(payload.get("resolved")) is not bool:
        return _report_failure("oracle_report_resolved_not_bool", report_path=str(report_path), instance_id=instance_id)
    if payload.get("infra_error") is not False:
        return _report_failure("oracle_report_infra_error", report_path=str(report_path), instance_id=instance_id)
    return ChangeOracleReport(
        target_instance_found=True,
        resolved=payload["resolved"],
        diagnostics={"infra_error": False, "failure_reason": None,
                     "report_path": str(report_path), "instance_id": instance_id},
    )


class _CheckInfraError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _run_check(command: tuple[str, ...], *, cwd: Path, config: ChangeOracleConfig,
               group: str, phase: str, checks: list, stdout_parts: list, stderr_parts: list) -> int:
    header = f"=== {phase} {group}: {' '.join(command)}"
    try:
        completed = subprocess.run(list(command), cwd=str(cwd), text=True, capture_output=True,
                                   timeout=config.command_timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        stdout_parts.append(f"{header}\n{_coerce_stream(exc.stdout)}")
        stderr_parts.append(f"{header}\n{_coerce_stream(exc.stderr)}")
        checks.append({"group": group, "phase": phase, "command": list(command), "returncode": None})
        raise _CheckInfraError("oracle_timeout") from None
    except OSError as exc:
        stderr_parts.append(f"{header}\n{exc}")
        checks.append({"group": group, "phase": phase, "command": list(command), "returncode": None})
        raise _CheckInfraError("oracle_command_error") from None
    stdout_parts.append(f"{header}\n{completed.stdout or ''}")
    stderr_parts.append(f"{header}\n{completed.stderr or ''}")
    checks.append({"group": group, "phase": phase, "command": list(command), "returncode": completed.returncode})
    if completed.returncode in _UNAVAILABLE_EXIT_CODES:
        raise _CheckInfraError("oracle_check_unavailable")
    return completed.returncode


def _apply(checkout: Path, patch: bytes) -> bool:
    for args in (["apply", "--check", "--binary", "-"], ["apply", "--binary", "-"]):
        if git_binary(args, checkout, check=False, input_bytes=patch).returncode != 0:
            return False
    return True


def _evaluator_patch(config: ChangeOracleConfig) -> bytes | None:
    if config.evaluator_patch is None:
        return None
    data = config.evaluator_patch.read_bytes()
    if _sha256_bytes(data) != config.evaluator_patch_sha256:
        raise _CheckInfraError("evaluator_patch_digest_mismatch")
    return data


def evaluate_change(
    config: ChangeOracleConfig,
    *,
    repo: Path,
    base_sha: str,
    model_patch: str,
    task: BaselineTask,
    diagnostics: dict[str, object],
    started: float,
) -> OracleResult:
    """Compare the base revision with the candidate patch under the declared check."""
    invocation_dir = Path(str(diagnostics["invocation_dir"]))
    patch_bytes = model_patch.encode("utf-8")
    checks: list[dict[str, object]] = []
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    resolved = False
    infra_reason: str | None = None
    candidate_reason: str | None = None
    checkouts = []
    cleanup_diagnostics: list[str] = []

    def checkout_with(*patches: bytes | None) -> Path | None:
        try:
            checkout = create_detached_worktree(repo, base_sha)
        except Exception as exc:
            stderr_parts.append(f"=== checkout\n{type(exc).__name__}: {exc}")
            raise _CheckInfraError("oracle_checkout_failed") from None
        checkouts.append(checkout)
        for index, patch in enumerate(patches):
            if patch is not None and not _apply(checkout.path, patch):
                if index == 0:
                    raise _CheckInfraError("evaluator_patch_apply_failed")
                return None
        return checkout.path

    try:
        evaluator = _evaluator_patch(config)
        before = checkout_with(evaluator)
        for command in config.fail_to_pass:
            if _run_check(command, cwd=before, config=config, group=FAIL_TO_PASS, phase="before",
                          checks=checks, stdout_parts=stdout_parts, stderr_parts=stderr_parts) == 0:
                raise _CheckInfraError("oracle_check_passes_before_change")
        after = checkout_with(evaluator, patch_bytes)
        if after is None:
            candidate_reason = "candidate_patch_apply_failed"
        else:
            returncodes = [
                _run_check(command, cwd=after, config=config, group=group, phase="after",
                           checks=checks, stdout_parts=stdout_parts, stderr_parts=stderr_parts)
                for group, commands in ((FAIL_TO_PASS, config.fail_to_pass), (PASS_TO_PASS, config.pass_to_pass))
                for command in commands
            ]
            resolved = all(code == 0 for code in returncodes)
    except _CheckInfraError as exc:
        infra_reason = exc.reason
    except OSError as exc:
        stderr_parts.append(f"=== evaluator patch\n{type(exc).__name__}: {exc}")
        infra_reason = "evaluator_patch_unreadable"
    finally:
        for checkout in checkouts:
            try:
                cleanup_worktree(checkout, keep=False)
            except Exception as exc:
                cleanup_diagnostics.append(f"oracle checkout cleanup failed: {type(exc).__name__}: {exc}")

    stdout_text = "\n".join(stdout_parts)
    stderr_text = "\n".join(stderr_parts)
    report_path = invocation_dir / "report.json"
    report = {
        "schema": REPORT_SCHEMA,
        "task_id": task.task_id,
        "instance_id": task.instance_id,
        "base_sha": base_sha,
        "model_patch_sha256": _sha256_bytes(patch_bytes),
        "evaluator_patch_sha256": config.evaluator_patch_sha256,
        "checks": checks,
        "resolved": resolved,
        "infra_error": infra_reason is not None,
        "failure_reason": infra_reason or candidate_reason,
    }
    report_path.write_text(_canonical_json(report) + "\n", encoding="utf-8")
    log_path = invocation_dir / "checks.log"
    log_path.write_text(stdout_text + ("\n" if stdout_text else "") + stderr_text, encoding="utf-8")
    artifacts = [item for item in (_artifact_summary(report_path, kind=REPORT_KIND),
                                   _artifact_summary(log_path, kind=CHECK_LOG_KIND)) if item]
    executed = [check["returncode"] for check in checks]
    diagnostics = {
        **diagnostics,
        "base_sha": base_sha,
        "report_path": str(report_path),
        "oracle_managed_artifacts": artifacts,
        "checks": checks,
        "target_instance_found": True,
    }
    if cleanup_diagnostics:
        diagnostics["oracle_cleanup_diagnostics"] = cleanup_diagnostics
    returncode = executed[-1] if executed else None
    if infra_reason is not None or candidate_reason is not None:
        return failure_result(started=started, config=config, diagnostics=diagnostics,
                              reason=infra_reason or candidate_reason, infra_error=infra_reason is not None,
                              candidate_invalid=candidate_reason is not None, returncode=returncode,
                              stdout_text=stdout_text, stderr_text=stderr_text)
    diagnostics.update({"infra_error": False, "candidate_invalid": False, "failure_reason": None})
    return oracle_result(started=started, config=config, diagnostics=diagnostics, resolved=resolved,
                         returncode=returncode, stdout_text=stdout_text, stderr_text=stderr_text)


@dataclass(frozen=True)
class ChangeOracleRunner:
    """Baseline front end: the candidate is the worker's uncommitted worktree."""

    config: ChangeOracleConfig

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        started = time.perf_counter()
        worktree = Path(worktree_path)
        diagnostics = oracle_base_diagnostics(self.config, task, adapter=ADAPTER_VERSION,
                                              invocation_root=".synapse_baseline_oracle")
        extraction = extract_model_patch_from_worktree(worktree)
        diagnostics.update(extraction.diagnostics)
        if extraction.model_patch is None:
            return oracle_result(started=started, config=self.config, diagnostics=diagnostics, resolved=False,
                                 stdout_text="The change oracle did not run: candidate patch extraction failed.")
        head = git_binary(["rev-parse", "HEAD"], worktree, check=False)
        if head.returncode != 0:
            return failure_result(started=started, config=self.config, diagnostics=diagnostics,
                                  reason="base_revision_resolve_failed", infra_error=True, candidate_invalid=False)
        return evaluate_change(self.config, repo=worktree, base_sha=head.stdout.decode("ascii").strip(),
                               model_patch=extraction.model_patch, task=task, diagnostics=diagnostics,
                               started=started)
