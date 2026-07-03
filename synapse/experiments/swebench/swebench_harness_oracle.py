"""SWE-bench harness oracle adapter for Stage 3B measurement."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Mapping
import hashlib
import json
import platform
import re
import subprocess
import sys
import time
import uuid

from .contract import BaselineTask, OracleResult
from .patch_extract import extract_model_patch_from_worktree
from .swebench_reports import (
    parse_swebench_report,
    resolve_swebench_instance_report_path,
    swebench_model_log_dir_name,
)


ADAPTER_VERSION = "synapse.stage3b.swebench_harness_oracle/v1"


@dataclass(frozen=True)
class SWEbenchHarnessOracleConfig:
    python_executable: Path
    swebench_work_dir: Path
    dataset_name: str
    split: str
    instance_timeout_seconds: int
    max_workers: int
    open_file_limit: int = 4096
    cache_level: str = "env"
    force_rebuild: bool = False
    clean: bool = False
    namespace: str | None = "swebench"
    model_name_or_path: str = "synapse-stage3b"
    instance_image_tag: str = "latest"
    env_image_tag: str = "latest"
    process_timeout_seconds: int | None = None
    max_summary_bytes: int = 4000
    max_summary_lines: int = 80

    def __post_init__(self) -> None:
        if self.force_rebuild and self.namespace is not None:
            raise ValueError("stage3b: force_rebuild cannot be used with a non-None namespace")
        if not self.model_name_or_path.strip() or len(self.model_name_or_path) > 200:
            raise ValueError("stage3b: invalid model_name_or_path")
        if "\\" in self.model_name_or_path or any(ord(ch) < 32 for ch in self.model_name_or_path):
            raise ValueError("stage3b: invalid model_name_or_path")
        if self.instance_timeout_seconds <= 0:
            raise ValueError("stage3b: instance_timeout_seconds must be positive")
        if self.max_workers <= 0:
            raise ValueError("stage3b: max_workers must be positive")
        if self.open_file_limit <= 0:
            raise ValueError("stage3b: open_file_limit must be positive")
        if self.max_summary_bytes <= 0 or self.max_summary_lines <= 0:
            raise ValueError("stage3b: summary limits must be positive")
        object.__setattr__(self, "python_executable", Path(self.python_executable))
        object.__setattr__(self, "swebench_work_dir", Path(self.swebench_work_dir))


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_swebench_version() -> str:
    try:
        return metadata.version("swebench")
    except metadata.PackageNotFoundError:
        return "unknown"


def _namespace_value(config: SWEbenchHarnessOracleConfig) -> str:
    return "none" if config.namespace is None else config.namespace


def build_oracle_config_fingerprint_payload(
    config: SWEbenchHarnessOracleConfig,
    *,
    swebench_version: str,
) -> Mapping[str, object]:
    return {
        "adapter": ADAPTER_VERSION,
        "dataset_name": config.dataset_name,
        "split": config.split,
        "instance_timeout_seconds": config.instance_timeout_seconds,
        "max_workers": config.max_workers,
        "open_file_limit": config.open_file_limit,
        "cache_level": config.cache_level,
        "force_rebuild": config.force_rebuild,
        "clean": config.clean,
        "namespace": _namespace_value(config),
        "model_name_or_path": config.model_name_or_path,
        "instance_image_tag": config.instance_image_tag,
        "env_image_tag": config.env_image_tag,
        "swebench_version": swebench_version,
    }


def compute_oracle_config_fingerprint(payload: Mapping[str, object]) -> str:
    return _sha256_text(_canonical_json(payload))


def build_oracle_environment_fingerprint_payload(
    config: SWEbenchHarnessOracleConfig,
    *,
    swebench_version: str,
) -> Mapping[str, object]:
    return {
        "swebench_version": swebench_version,
        "python_executable": str(config.python_executable),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "instance_image_tag": config.instance_image_tag,
        "env_image_tag": config.env_image_tag,
        "docker_probe": "not_performed",
    }


def compute_oracle_environment_fingerprint(payload: Mapping[str, object]) -> str:
    return _sha256_text(_canonical_json(payload))


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (slug or "task")[:60]


def generate_swebench_run_id(task: BaselineTask) -> str:
    prefix = _safe_slug(task.task_id or task.instance_id)
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


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


def write_swebench_predictions_jsonl(
    path: Path,
    *,
    instance_id: str,
    model_name_or_path: str,
    model_patch: str,
) -> Mapping[str, object]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "instance_id": instance_id,
        "model_name_or_path": model_name_or_path,
        "model_patch": model_patch,
    }
    data = _canonical_json(payload) + "\n"
    path.write_text(data, encoding="utf-8")
    summary = _artifact_summary(path, kind="swebench_prediction")
    if summary is None:
        raise RuntimeError("stage3b: failed to write predictions JSONL")
    return summary


def _bool_arg(value: bool) -> str:
    return "true" if value else "false"


def build_swebench_harness_command(
    config: SWEbenchHarnessOracleConfig,
    *,
    predictions_path: Path,
    instance_id: str,
    run_id: str,
) -> tuple[str, ...]:
    predictions_path = Path(predictions_path)
    absolute_predictions_path = predictions_path if predictions_path.is_absolute() else predictions_path.resolve()
    return (
        str(config.python_executable),
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        config.dataset_name,
        "--split",
        config.split,
        "--instance_ids",
        instance_id,
        "--predictions_path",
        str(absolute_predictions_path),
        "--run_id",
        run_id,
        "--max_workers",
        str(config.max_workers),
        "--open_file_limit",
        str(config.open_file_limit),
        "--timeout",
        str(config.instance_timeout_seconds),
        "--cache_level",
        config.cache_level,
        "--force_rebuild",
        _bool_arg(config.force_rebuild),
        "--clean",
        _bool_arg(config.clean),
        "--namespace",
        _namespace_value(config),
        "--instance_image_tag",
        config.instance_image_tag,
        "--env_image_tag",
        config.env_image_tag,
    )


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


def _artifact_by_kind(artifacts: list[dict[str, object]], kind: str) -> dict[str, object] | None:
    for artifact in artifacts:
        if artifact.get("kind") == kind:
            return artifact
    return None


def _stream_summary(
    *,
    stream_name: str,
    text: str,
    config: SWEbenchHarnessOracleConfig,
    diagnostics: Mapping[str, object],
    artifacts: list[dict[str, object]],
) -> tuple[str, Mapping[str, object]]:
    excerpt, summary_diagnostics = summarize_text_for_carry(
        text,
        max_bytes=config.max_summary_bytes,
        max_lines=config.max_summary_lines,
    )
    lines = [
        f"SWE-bench harness oracle {stream_name} summary",
        f"failure_reason: {diagnostics.get('failure_reason')}",
        f"instance_id: {diagnostics.get('instance_id')}",
        f"resolved: {diagnostics.get('resolved')}",
        f"infra_error: {diagnostics.get('infra_error')}",
        f"candidate_invalid: {diagnostics.get('candidate_invalid')}",
        f"report_artifact: {_artifact_by_kind(artifacts, 'swebench_report')}",
        f"{stream_name}_artifact: {_artifact_by_kind(artifacts, f'swebench_{stream_name}')}",
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


@dataclass(frozen=True)
class SWEbenchHarnessOracleRunner:
    config: SWEbenchHarnessOracleConfig

    def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
        started = time.perf_counter()
        config = self.config
        config.swebench_work_dir.mkdir(parents=True, exist_ok=True)
        swebench_version = detect_swebench_version()
        config_payload = build_oracle_config_fingerprint_payload(config, swebench_version=swebench_version)
        env_payload = build_oracle_environment_fingerprint_payload(config, swebench_version=swebench_version)
        base_diagnostics: dict[str, object] = {
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
        }
        run_id = generate_swebench_run_id(task)
        invocation_dir = config.swebench_work_dir / ".synapse_stage3b_oracle" / run_id
        invocation_dir.mkdir(parents=True, exist_ok=True)
        base_diagnostics.update(
            {
                "run_id": run_id,
                "invocation_dir": str(invocation_dir.resolve()),
            }
        )

        extraction = extract_model_patch_from_worktree(Path(worktree_path))
        extraction_diagnostics = dict(extraction.diagnostics)
        if extraction.model_patch is None:
            diagnostics = {
                **base_diagnostics,
                **extraction_diagnostics,
                "resolved": False,
                "oracle_managed_artifacts": [],
            }
            return self._result(
                resolved=False,
                returncode=None,
                duration_seconds=time.perf_counter() - started,
                diagnostics=diagnostics,
                stdout_text="SWE-bench harness was not invoked because candidate patch extraction failed.",
                stderr_text="",
                artifacts=[],
            )

        predictions_path = invocation_dir / "predictions.jsonl"
        prediction_summary = dict(
            write_swebench_predictions_jsonl(
                predictions_path,
                instance_id=task.instance_id,
                model_name_or_path=config.model_name_or_path,
                model_patch=extraction.model_patch,
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
        stdout_text = ""
        stderr_text = ""
        returncode: int | None = None
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

        artifacts = [prediction_summary]
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

        parse_result = parse_swebench_report(report_path, task.instance_id)
        parse_diagnostics = dict(parse_result.diagnostics)
        infra_error = bool(parse_diagnostics.get("infra_error"))
        failure_reason = parse_diagnostics.get("failure_reason")
        if infra_error and process_failure_reason is not None:
            failure_reason = process_failure_reason
        if infra_error and returncode not in (None, 0) and failure_reason is None:
            failure_reason = "swebench_harness_failed"

        diagnostics = {
            **base_diagnostics,
            **extraction_diagnostics,
            "run_id": run_id,
            "command": list(command),
            "shell": False,
            "cwd": str(config.swebench_work_dir),
            "predictions_artifact": prediction_summary,
            "report_path": str(report_path),
            "oracle_managed_artifacts": artifacts,
            "returncode": returncode,
            "resolved": parse_result.resolved,
            "target_instance_found": parse_result.target_instance_found,
            "infra_error": infra_error,
            "candidate_invalid": False,
            "failure_reason": failure_reason,
            "report_parse_diagnostics": parse_diagnostics,
        }
        if stdout_summary:
            diagnostics["stdout_artifact"] = stdout_summary
        if stderr_summary:
            diagnostics["stderr_artifact"] = stderr_summary

        return self._result(
            resolved=parse_result.resolved,
            returncode=returncode,
            duration_seconds=time.perf_counter() - started,
            diagnostics=diagnostics,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            artifacts=artifacts,
        )

    def _result(
        self,
        *,
        resolved: bool,
        returncode: int | None,
        duration_seconds: float,
        diagnostics: dict[str, object],
        stdout_text: str,
        stderr_text: str,
        artifacts: list[dict[str, object]],
    ) -> OracleResult:
        stdout_summary, stdout_diagnostics = _stream_summary(
            stream_name="stdout",
            text=stdout_text,
            config=self.config,
            diagnostics=diagnostics,
            artifacts=artifacts,
        )
        stderr_summary, stderr_diagnostics = _stream_summary(
            stream_name="stderr",
            text=stderr_text,
            config=self.config,
            diagnostics=diagnostics,
            artifacts=artifacts,
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
