"""Machine-readable telemetry for Stage 3A baseline runs."""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

from synapse.worker import ExternalWorkerTokenStatus, ExternalWorkerUsage

from .contract import (
    BaselineRunRecord,
    ExperimentArm,
    PrimaryMetricStatus,
    TokenAccountingRecord,
    UsageConsistencyStatus,
    UsageSource,
)


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def token_accounting_from_worker_usage(
    usage: ExternalWorkerUsage,
    *,
    usage_source: UsageSource,
    arm: ExperimentArm = ExperimentArm.BASELINE,
) -> TokenAccountingRecord:
    total = usage.total_tokens
    components = (usage.input_tokens, usage.output_tokens, usage.thinking_tokens)
    if total is None:
        primary_status = PrimaryMetricStatus.UNAVAILABLE
        consistency = UsageConsistencyStatus.MISSING_TOTAL
    elif None not in components and sum(component for component in components if component is not None) > total:
        primary_status = PrimaryMetricStatus.PROVIDER_USAGE_INCONSISTENT
        consistency = UsageConsistencyStatus.COMPONENT_SUM_EXCEEDS_TOTAL
    elif None in components:
        primary_status = PrimaryMetricStatus.PRIMARY_USABLE
        consistency = UsageConsistencyStatus.COMPONENTS_INCOMPLETE
    else:
        primary_status = PrimaryMetricStatus.PRIMARY_USABLE
        consistency = UsageConsistencyStatus.CONSISTENT
    return TokenAccountingRecord(
        arm=arm,
        usage_source=usage_source,
        primary_metric_status=primary_status,
        usage_consistency=consistency,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        thinking_tokens=usage.thinking_tokens,
        total_tokens=total,
        thinking_included=usage.thinking_included,
        diagnostics=dict(usage.diagnostics),
    )


def usage_source_from_worker_status(token_status: ExternalWorkerTokenStatus) -> UsageSource:
    if token_status is ExternalWorkerTokenStatus.PROVIDER_REPORTED:
        return UsageSource.PROVIDER_REPORTED_DIRECT
    if token_status is ExternalWorkerTokenStatus.TOOL_REPORTED:
        return UsageSource.PROVIDER_REPORTED_VIA_TOOL_TRAJECTORY
    return UsageSource.UNAVAILABLE


class TelemetryWriter:
    def __init__(self, run_root: str | Path, run_id: str) -> None:
        self.run_dir = Path(run_root) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "artifacts").mkdir(exist_ok=True)

    def write_manifest(
        self,
        *,
        run: BaselineRunRecord,
        provider: str,
        model: str,
        api_base_present: bool,
        created_at_utc: str,
        experiment_doc_path: str = "docs/experiments/1-swebench-token-economy-preregistration.md",
        experiment_doc_status: str = "pre-Section 6.4 provider alignment",
    ) -> None:
        payload = {
            "schema": "synapse.experiments.swebench.stage3a.manifest/v1",
            "run_id": run.run_id,
            "arm": run.arm.value,
            "provider": provider,
            "model": model,
            "api_base_present": api_base_present,
            "base_revision": run.base_revision,
            "created_at_utc": created_at_utc,
            "experiment_doc_path": experiment_doc_path,
            "experiment_doc_status": experiment_doc_status,
            "max_attempts": run.max_attempts,
            "replicate_id": run.replicate_id,
        }
        (self.run_dir / "manifest.json").write_text(json_dumps(payload) + "\n", encoding="utf-8")

    def write_records(self, run: BaselineRunRecord) -> None:
        self._write_jsonl("attempts.jsonl", [attempt.to_dict() for attempt in run.attempts])
        self._write_jsonl("tokens.jsonl", [attempt.token_accounting.to_dict() for attempt in run.attempts])
        oracle_records = []
        for attempt in run.attempts:
            if attempt.oracle_result is not None:
                payload = attempt.oracle_result.to_dict()
                payload["arm"] = attempt.arm.value
                payload["attempt_id"] = attempt.attempt_id
                oracle_records.append(payload)
        self._write_jsonl("oracle.jsonl", oracle_records)

    def _write_jsonl(self, name: str, records: list[dict[str, Any]]) -> None:
        path = self.run_dir / name
        path.write_text("".join(json_dumps(record) + "\n" for record in records), encoding="utf-8")
