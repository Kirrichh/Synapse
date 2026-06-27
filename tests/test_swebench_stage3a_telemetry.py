"""Stage 3A telemetry and token accounting tests."""

from __future__ import annotations

import json

from synapse.experiments.swebench.contract import (
    BaselineRunRecord,
    ExperimentArm,
    PrimaryMetricStatus,
    UsageConsistencyStatus,
    UsageSource,
)
from synapse.experiments.swebench.telemetry import TelemetryWriter, token_accounting_from_worker_usage
from synapse.worker import ExternalWorkerTokenStatus, ExternalWorkerUsage


def _usage(total=10, input_tokens=4, output_tokens=3, thinking_tokens=3):
    return ExternalWorkerUsage(
        token_status=ExternalWorkerTokenStatus.PROVIDER_REPORTED if total is not None else ExternalWorkerTokenStatus.UNAVAILABLE,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        total_tokens=total,
        thinking_included=True,
        diagnostics={"raw_usage_ref": "test"},
    )


def test_token_accounting_primary_statuses():
    usable = token_accounting_from_worker_usage(_usage(total=10), usage_source=UsageSource.PROVIDER_REPORTED_DIRECT)
    missing = token_accounting_from_worker_usage(_usage(total=None), usage_source=UsageSource.UNAVAILABLE)
    inconsistent = token_accounting_from_worker_usage(_usage(total=5), usage_source=UsageSource.PROVIDER_REPORTED_DIRECT)

    assert usable.primary_metric_status is PrimaryMetricStatus.PRIMARY_USABLE
    assert usable.usage_consistency is UsageConsistencyStatus.CONSISTENT
    assert missing.primary_metric_status is PrimaryMetricStatus.UNAVAILABLE
    assert missing.total_tokens is None
    assert inconsistent.primary_metric_status is PrimaryMetricStatus.PROVIDER_USAGE_INCONSISTENT
    assert inconsistent.usage_consistency is UsageConsistencyStatus.COMPONENT_SUM_EXCEEDS_TOTAL
    assert inconsistent.total_tokens == 5


def test_telemetry_writer_creates_manifest_jsonl_and_artifacts_dir(tmp_path):
    run = BaselineRunRecord(
        run_id="run-1",
        task_id="task",
        instance_id="instance",
        arm=ExperimentArm.BASELINE,
        base_revision="abc123",
        replicate_id=1,
        max_attempts=3,
        resolved=False,
        attempts=(),
        total_provider_tokens=None,
        primary_metric_usable=False,
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:01Z",
        diagnostics={},
    )
    writer = TelemetryWriter(tmp_path, run.run_id)

    writer.write_manifest(
        run=run,
        provider="local Ollama",
        model="ollama_chat/qwen3-coder:30b",
        api_base_present=True,
        created_at_utc=run.started_at_utc,
    )
    writer.write_records(run)

    manifest = json.loads((tmp_path / "run-1" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["arm"] == "BASELINE"
    assert (tmp_path / "run-1" / "attempts.jsonl").exists()
    assert (tmp_path / "run-1" / "tokens.jsonl").exists()
    assert (tmp_path / "run-1" / "oracle.jsonl").exists()
    assert (tmp_path / "run-1" / "artifacts").is_dir()
