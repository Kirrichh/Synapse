"""SWE-bench report path resolution and parsing for Stage 3B."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import json


AGGREGATE_REPORT_KEYS = {
    "total_instances",
    "completed_instances",
    "resolved_instances",
    "unresolved_instances",
    "error_instances",
    "schema_version",
}


@dataclass(frozen=True)
class SWEbenchReportParseResult:
    target_instance_found: bool
    resolved: bool
    diagnostics: Mapping[str, object]


def swebench_model_log_dir_name(model_name_or_path: str) -> str:
    return model_name_or_path.replace("/", "__")


def resolve_swebench_instance_report_path(
    harness_work_dir: Path,
    *,
    run_id: str,
    model_name_or_path: str,
    instance_id: str,
) -> Path:
    return (
        Path(harness_work_dir)
        / "logs"
        / "run_evaluation"
        / run_id
        / swebench_model_log_dir_name(model_name_or_path)
        / instance_id
        / "report.json"
    )


def _parse_failure(reason: str, **extra: object) -> SWEbenchReportParseResult:
    return SWEbenchReportParseResult(
        target_instance_found=False,
        resolved=False,
        diagnostics={
            "infra_error": True,
            "failure_reason": reason,
            **extra,
        },
    )


def parse_swebench_report(report_path: Path, instance_id: str) -> SWEbenchReportParseResult:
    report_path = Path(report_path)
    if not report_path.exists():
        return _parse_failure("swebench_report_missing", report_path=str(report_path))
    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _parse_failure(
            "swebench_report_malformed",
            report_path=str(report_path),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
    if raw.strip() == "":
        return _parse_failure("swebench_report_empty", report_path=str(report_path))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _parse_failure(
            "swebench_report_malformed",
            report_path=str(report_path),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
    if not isinstance(payload, dict):
        return _parse_failure("swebench_report_malformed", report_path=str(report_path))
    if instance_id not in payload:
        if AGGREGATE_REPORT_KEYS.intersection(payload):
            return _parse_failure(
                "swebench_report_not_instance_level",
                report_path=str(report_path),
            )
        return _parse_failure(
            "swebench_report_target_missing",
            report_path=str(report_path),
            instance_id=instance_id,
        )
    target = payload[instance_id]
    if not isinstance(target, dict):
        return _parse_failure(
            "swebench_report_malformed",
            report_path=str(report_path),
            instance_id=instance_id,
        )
    if "resolved" not in target:
        return _parse_failure(
            "swebench_report_resolved_missing",
            report_path=str(report_path),
            instance_id=instance_id,
        )
    resolved = target["resolved"]
    if not isinstance(resolved, bool):
        return _parse_failure(
            "swebench_report_resolved_not_bool",
            report_path=str(report_path),
            instance_id=instance_id,
        )
    return SWEbenchReportParseResult(
        target_instance_found=True,
        resolved=resolved,
        diagnostics={
            "infra_error": False,
            "failure_reason": None,
            "report_path": str(report_path),
            "instance_id": instance_id,
        },
    )
