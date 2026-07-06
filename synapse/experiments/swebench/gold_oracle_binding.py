"""Stage 4 C2-S1 GOLD SWE-bench oracle binding.

This adapter receives the clean detached verified worktree created by the C1
GOLD runner and derives the SWE-bench ``model_patch`` from the verified commit
pair. It does not run workers, controlled-change, telemetry, paired
measurement, carry admission, or FULL verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import hashlib
import os
import re
import subprocess
import time
import uuid

from synapse.experiments.swebench.contract import BaselineTask, OracleResult
from synapse.experiments.swebench.swebench_harness_oracle import (
    SWEbenchHarnessOracleConfig,
    build_oracle_config_fingerprint_payload,
    build_oracle_environment_fingerprint_payload,
    build_swebench_harness_command,
    compute_oracle_config_fingerprint,
    compute_oracle_environment_fingerprint,
    detect_swebench_version,
    summarize_text_for_carry,
    write_swebench_predictions_jsonl,
)
from synapse.experiments.swebench.swebench_reports import (
    parse_swebench_report,
    resolve_swebench_instance_report_path,
)


ADAPTER_VERSION = "synapse.stage4.c2s1.gold_swebench_oracle_binding/v1"
PATCH_SOURCE = "verified_commit_pair_git_diff"
BASE_SOURCE = "head_parent_single_parent_assumed"


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (slug or "task")[:60]


def _run_git(worktree_path: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=str(worktree_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _decode_strict(data: bytes) -> str:
    return data.decode("utf-8", errors="strict")


def _decode_replace(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _normalize_repo_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or "//" in normalized
    ):
        raise ValueError("candidate_patch_path_malformed")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("candidate_patch_path_malformed")
    return normalized


def _decode_z_paths(data: bytes) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        paths.append(_normalize_repo_relative_path(_decode_strict(raw)))
    return tuple(dict.fromkeys(paths))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def _write_text_artifact(path: Path, text: str, *, kind: str) -> dict[str, object] | None:
    if text == "":
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return _artifact_summary(path, kind=kind)


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
    config: SWEbenchHarnessOracleConfig,
    diagnostics: Mapping[str, object],
) -> tuple[str, Mapping[str, object]]:
    excerpt, summary_diagnostics = summarize_text_for_carry(
        text,
        max_bytes=config.max_summary_bytes,
        max_lines=config.max_summary_lines,
    )
    lines = [
        f"GOLD SWE-bench oracle {stream_name} summary",
        f"failure_reason: {diagnostics.get('failure_reason')}",
        f"instance_id: {diagnostics.get('instance_id')}",
        f"resolved: {diagnostics.get('resolved')}",
        f"infra_error: {diagnostics.get('infra_error')}",
        f"candidate_invalid: {diagnostics.get('candidate_invalid')}",
        "excerpt:",
        excerpt,
    ]
    return "\n".join(lines), summary_diagnostics


def _run_harness_command(
    command: tuple[str, ...],
    *,
    config: SWEbenchHarnessOracleConfig,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(config.swebench_work_dir),
        text=True,
        capture_output=True,
        timeout=config.process_timeout_seconds,
        check=False,
    )


def _failure_result(
    *,
    started: float,
    config: SWEbenchHarnessOracleConfig,
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
        "resolved": False,
        "returncode": returncode,
        "infra_error": infra_error,
        "candidate_invalid": candidate_invalid,
        "failure_reason": reason,
    }
    if "oracle_managed_artifacts" not in diagnostics:
        diagnostics["oracle_managed_artifacts"] = []
    return _oracle_result(
        resolved=False,
        returncode=returncode,
        duration_seconds=time.perf_counter() - started,
        diagnostics=diagnostics,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        config=config,
    )


def _oracle_result(
    *,
    resolved: bool,
    returncode: int | None,
    duration_seconds: float,
    diagnostics: dict[str, object],
    stdout_text: str,
    stderr_text: str,
    config: SWEbenchHarnessOracleConfig,
) -> OracleResult:
    stdout_summary, stdout_diagnostics = _stream_summary(
        stream_name="stdout",
        text=stdout_text,
        config=config,
        diagnostics=diagnostics,
    )
    stderr_summary, stderr_diagnostics = _stream_summary(
        stream_name="stderr",
        text=stderr_text,
        config=config,
        diagnostics=diagnostics,
    )
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
        duration_seconds=duration_seconds,
        diagnostics=diagnostics,
    )


def _generate_run_id(task: BaselineTask) -> str:
    prefix = _safe_slug(task.task_id or task.instance_id)
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class _CommitPair:
    verified_commit: str
    base_sha: str
    parent_count: int


@dataclass(frozen=True)
class GoldSWEbenchOracleBinding:
    config: SWEbenchHarnessOracleConfig

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        started = time.perf_counter()
        worktree = Path(worktree_path)
        config = self.config
        config.swebench_work_dir.mkdir(parents=True, exist_ok=True)
        swebench_version = detect_swebench_version()
        config_payload = build_oracle_config_fingerprint_payload(config, swebench_version=swebench_version)
        env_payload = build_oracle_environment_fingerprint_payload(config, swebench_version=swebench_version)
        run_id = _generate_run_id(task)
        invocation_dir = config.swebench_work_dir / ".synapse_stage4_gold_oracle" / run_id
        invocation_dir.mkdir(parents=True, exist_ok=True)
        diagnostics: dict[str, object] = {
            "adapter": ADAPTER_VERSION,
            "task_id": task.task_id,
            "instance_id": task.instance_id,
            "model_name_or_path": config.model_name_or_path,
            "instance_image_tag": config.instance_image_tag,
            "env_image_tag": config.env_image_tag,
            "oracle_config_fingerprint": compute_oracle_config_fingerprint(config_payload),
            "oracle_config_fingerprint_payload": dict(config_payload),
            "oracle_environment_fingerprint": compute_oracle_environment_fingerprint(env_payload),
            "oracle_environment_fingerprint_payload": dict(env_payload),
            "run_id": run_id,
            "invocation_dir": str(invocation_dir.resolve()),
            "patch_source": PATCH_SOURCE,
        }

        pair = self._resolve_commit_pair(worktree, diagnostics=diagnostics)
        if isinstance(pair, OracleResult):
            return pair

        diagnostics.update(
            {
                "base_sha": pair.base_sha,
                "base_source": BASE_SOURCE,
                "verified_commit": pair.verified_commit,
                "verified_commit_parent_count": pair.parent_count,
            }
        )

        extraction = self._model_patch_from_pair(worktree, pair, diagnostics=diagnostics)
        if isinstance(extraction, OracleResult):
            return extraction
        model_patch, changed_paths = extraction
        patch_bytes = model_patch.encode("utf-8")
        diagnostics.update(
            {
                "model_patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
                "model_patch_bytes": len(patch_bytes),
                "changed_paths": list(changed_paths),
                "scope_observation_source": "verified_commit_pair_patch_paths",
                "scope_observation_only": True,
                "infra_error": False,
                "candidate_invalid": False,
            }
        )

        predictions_path = invocation_dir / "predictions.jsonl"
        prediction_summary = dict(
            write_swebench_predictions_jsonl(
                predictions_path,
                instance_id=task.instance_id,
                model_name_or_path=config.model_name_or_path,
                model_patch=model_patch,
            )
        )
        command = build_swebench_harness_command(
            config,
            predictions_path=predictions_path,
            instance_id=task.instance_id,
            run_id=run_id,
        )
        report_path = resolve_swebench_instance_report_path(
            config.swebench_work_dir,
            run_id=run_id,
            model_name_or_path=config.model_name_or_path,
            instance_id=task.instance_id,
        )
        diagnostics.update(
            {
                "command": list(command),
                "shell": False,
                "cwd": str(config.swebench_work_dir),
                "predictions_artifact": prediction_summary,
                "report_path": str(report_path),
            }
        )
        stdout_text = ""
        stderr_text = ""
        returncode: int | None = None
        artifacts = [prediction_summary]
        process_failure_reason: str | None = None
        try:
            completed = _run_harness_command(command, config=config)
            stdout_text = completed.stdout or ""
            stderr_text = completed.stderr or ""
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout_text = _coerce_stream(exc.stdout)
            stderr_text = _coerce_stream(exc.stderr)
            process_failure_reason = "oracle_timeout"
        except OSError as exc:
            stderr_text = str(exc)
            process_failure_reason = "oracle_command_error"

        stdout_summary = _write_text_artifact(invocation_dir / "harness-stdout.txt", stdout_text, kind="swebench_stdout")
        stderr_summary = _write_text_artifact(invocation_dir / "harness-stderr.txt", stderr_text, kind="swebench_stderr")
        if stdout_summary:
            artifacts.append(stdout_summary)
        if stderr_summary:
            artifacts.append(stderr_summary)
        for kind, path in (
            ("swebench_report", report_path),
            ("swebench_run_instance_log", report_path.parent / "run_instance.log"),
            ("swebench_test_output", report_path.parent / "test_output.txt"),
        ):
            summary = _artifact_summary(path, kind=kind)
            if summary:
                artifacts.append(summary)
        diagnostics["oracle_managed_artifacts"] = artifacts
        diagnostics["returncode"] = returncode
        if stdout_summary:
            diagnostics["stdout_artifact"] = stdout_summary
        if stderr_summary:
            diagnostics["stderr_artifact"] = stderr_summary

        if process_failure_reason is not None:
            return _failure_result(
                started=started,
                config=config,
                diagnostics=diagnostics,
                reason=process_failure_reason,
                infra_error=True,
                candidate_invalid=False,
                returncode=returncode,
                stdout_text=stdout_text,
                stderr_text=stderr_text,
            )

        parse_result = parse_swebench_report(report_path, task.instance_id)
        parse_diagnostics = dict(parse_result.diagnostics)
        infra_error = bool(parse_diagnostics.get("infra_error"))
        failure_reason = parse_diagnostics.get("failure_reason")
        diagnostics.update(
            {
                "resolved": parse_result.resolved,
                "target_instance_found": parse_result.target_instance_found,
                "infra_error": infra_error,
                "candidate_invalid": False,
                "failure_reason": failure_reason,
                "report_parse_diagnostics": parse_diagnostics,
            }
        )
        return _oracle_result(
            resolved=parse_result.resolved,
            returncode=returncode,
            duration_seconds=time.perf_counter() - started,
            diagnostics=diagnostics,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            config=config,
        )

    def _resolve_commit_pair(self, worktree: Path, *, diagnostics: dict[str, object]) -> _CommitPair | OracleResult:
        started = time.perf_counter()
        head = _run_git(worktree, ("rev-parse", "HEAD"))
        if head.returncode != 0:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            verified_commit = _decode_strict(head.stdout).strip()
        except UnicodeDecodeError:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )

        parents = _run_git(worktree, ("rev-list", "--parents", "-n", "1", "HEAD"))
        if parents.returncode != 0:
            diagnostics["verified_commit"] = verified_commit
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            parent_line = _decode_strict(parents.stdout).strip()
        except UnicodeDecodeError:
            diagnostics["verified_commit"] = verified_commit
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        parts = parent_line.split()
        if not parts or parts[0] != verified_commit:
            diagnostics["verified_commit"] = verified_commit
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_parse_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        parent_count = len(parts) - 1
        diagnostics["verified_commit"] = verified_commit
        diagnostics["verified_commit_parent_count"] = parent_count
        if parent_count == 0:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_missing",
                infra_error=True,
                candidate_invalid=False,
            )
        if parent_count >= 2:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_merge_unsupported",
                infra_error=True,
                candidate_invalid=False,
            )
        parent = parts[1]
        parent_resolve = _run_git(worktree, ("rev-parse", "--verify", f"{parent}^{{commit}}"))
        if parent_resolve.returncode != 0:
            diagnostics["base_sha"] = parent
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="verified_commit_parent_resolve_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        return _CommitPair(verified_commit=verified_commit, base_sha=parent, parent_count=parent_count)

    def _model_patch_from_pair(
        self,
        worktree: Path,
        pair: _CommitPair,
        *,
        diagnostics: dict[str, object],
    ) -> tuple[str, tuple[str, ...]] | OracleResult:
        started = time.perf_counter()
        range_spec = f"{pair.base_sha}..{pair.verified_commit}"
        diff = _run_git(worktree, ("diff", "--binary", "--find-renames", range_spec))
        if diff.returncode != 0:
            diagnostics.update(
                {
                    "git_command": f"git diff --binary --find-renames {range_spec}",
                    "git_returncode": diff.returncode,
                    "git_stderr": _decode_replace(diff.stderr),
                }
            )
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_git_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            model_patch = _decode_strict(diff.stdout)
        except UnicodeDecodeError:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_decode_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        names = _run_git(worktree, ("diff", "--name-only", "-z", "--find-renames", range_spec))
        if names.returncode != 0:
            diagnostics.update(
                {
                    "git_command": f"git diff --name-only -z --find-renames {range_spec}",
                    "git_returncode": names.returncode,
                    "git_stderr": _decode_replace(names.stderr),
                }
            )
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_git_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        try:
            changed_paths = _decode_z_paths(names.stdout)
        except UnicodeDecodeError:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_decode_failed",
                infra_error=True,
                candidate_invalid=False,
            )
        except ValueError:
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_path_malformed",
                infra_error=False,
                candidate_invalid=True,
            )
        if not model_patch.strip():
            diagnostics["changed_paths"] = list(changed_paths)
            return _failure_result(
                started=started,
                config=self.config,
                diagnostics=diagnostics,
                reason="candidate_patch_empty",
                infra_error=False,
                candidate_invalid=True,
            )
        return model_patch, changed_paths
