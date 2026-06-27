"""Stage 3A baseline SWE-bench experiment runner."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from synapse.change.workspace import cleanup_worktree, create_detached_worktree
from synapse.worker import ExternalWorkerStatus
from synapse.worker.mini_adapter import run_mini_worker

from .artifacts import ArtifactStore
from .carry import RawCarryEntry, RawTranscriptCarry
from .contract import (
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
)
from .mini_config import MiniInvocationConfig
from .oracle import OracleRunner
from .telemetry import TelemetryWriter, token_accounting_from_worker_usage, usage_source_from_worker_status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _attempt_verdict(status: ExternalWorkerStatus, oracle_resolved: bool | None) -> AttemptVerdict:
    if status is ExternalWorkerStatus.NO_PATCH:
        return AttemptVerdict.NO_CANDIDATE
    if status in {ExternalWorkerStatus.ERROR, ExternalWorkerStatus.TIMEOUT}:
        return AttemptVerdict.INFRA_ERROR
    if oracle_resolved is True:
        return AttemptVerdict.ORACLE_RESOLVED
    return AttemptVerdict.ORACLE_UNRESOLVED


def _build_prompt(task: BaselineTask, carry: RawTranscriptCarry) -> str:
    allowed = "\n".join(f"- {path}" for path in task.allowed_scope)
    return (
        f"{task.statement}\n\n"
        "Allowed scope:\n"
        f"{allowed}\n"
        f"{carry.render_prompt_suffix()}"
    )


def run_baseline_task(
    task: BaselineTask,
    *,
    repo_root: str | Path,
    base_revision: str,
    replicate_id: int,
    max_attempts: int = 3,
    mini: MiniInvocationConfig,
    oracle: OracleRunner,
    run_root: str | Path,
    arm: ExperimentArm = ExperimentArm.BASELINE,
) -> BaselineRunRecord:
    if arm is not ExperimentArm.BASELINE:
        raise ValueError("stage3a: unsupported_arm - only BASELINE is executable in Stage 3A")

    run_id = f"stage3a-{task.task_id}-r{replicate_id}-{uuid.uuid4().hex[:12]}"
    run_started = _utc_now()
    run_dir = Path(run_root) / run_id
    artifact_store = ArtifactStore(run_dir)
    carry = RawTranscriptCarry()
    attempts: list[BaselineAttemptRecord] = []
    resolved = False

    for attempt_id in range(1, max_attempts + 1):
        attempt_started = _utc_now()
        worktree = create_detached_worktree(repo_root, base_revision)
        oracle_result = None
        artifacts = []
        try:
            prompt = _build_prompt(task, carry)
            worker_result = run_mini_worker(
                worktree.path,
                task.to_worker_payload(prompt),
                task.allowed_scope,
                config=mini.to_adapter_config(),
            )
            if worker_result.diff_text:
                artifact = artifact_store.write_text(f"attempt-{attempt_id}-worker.diff", "worker_diff", worker_result.diff_text)
                if artifact:
                    artifacts.append(artifact)
            if worker_result.worker_status is ExternalWorkerStatus.PROPOSED_PATCH:
                oracle_result = oracle.verify(worktree.path, task)
                for suffix, kind, text in (
                    ("stdout.txt", "oracle_stdout", oracle_result.stdout),
                    ("stderr.txt", "oracle_stderr", oracle_result.stderr),
                ):
                    artifact = artifact_store.write_text(f"attempt-{attempt_id}-{suffix}", kind, text)
                    if artifact:
                        artifacts.append(artifact)
            usage_source = usage_source_from_worker_status(worker_result.usage.token_status)
            token_record = token_accounting_from_worker_usage(worker_result.usage, usage_source=usage_source, arm=arm)
            verdict = _attempt_verdict(
                worker_result.worker_status,
                oracle_result.resolved if oracle_result is not None else None,
            )
            attempt = BaselineAttemptRecord(
                attempt_id=attempt_id,
                arm=arm,
                verdict=verdict,
                worker_result=worker_result,
                token_accounting=token_record,
                oracle_result=oracle_result,
                artifacts=tuple(artifacts),
                started_at_utc=attempt_started,
                finished_at_utc=_utc_now(),
                diagnostics={
                    "worktree_cleanup_policy": "remove",
                    "candidate_is_not_success": True,
                },
            )
            attempts.append(attempt)
            if verdict is AttemptVerdict.ORACLE_RESOLVED:
                resolved = True
                break
            carry = carry.append(
                RawCarryEntry(
                    attempt_id=attempt_id,
                    worker_summary=worker_result.worker_report.summary or worker_result.worker_report.failure_reason,
                    oracle_stdout=oracle_result.stdout if oracle_result else "",
                    oracle_stderr=oracle_result.stderr if oracle_result else "",
                    diagnostics=tuple(str(item) for item in worker_result.diagnostics.get("scope_violations", ())),
                )
            )
        finally:
            cleanup_worktree(worktree, keep=False)

    usable_tokens = [
        attempt.token_accounting.total_tokens
        for attempt in attempts
        if attempt.token_accounting.total_tokens is not None
        and attempt.token_accounting.primary_metric_status.value == "PRIMARY_USABLE"
    ]
    total_provider_tokens = sum(usable_tokens) if len(usable_tokens) == len(attempts) else None
    run = BaselineRunRecord(
        run_id=run_id,
        task_id=task.task_id,
        instance_id=task.instance_id,
        arm=arm,
        base_revision=base_revision,
        replicate_id=replicate_id,
        max_attempts=max_attempts,
        resolved=resolved,
        attempts=tuple(attempts),
        total_provider_tokens=total_provider_tokens,
        primary_metric_usable=total_provider_tokens is not None,
        started_at_utc=run_started,
        finished_at_utc=_utc_now(),
        diagnostics={
            "stage": "3A",
            "gold_execution_implemented": False,
            "auto_arm_selection": False,
        },
    )
    writer = TelemetryWriter(run_root, run_id)
    writer.write_manifest(
        run=run,
        provider="local Ollama",
        model=mini.model,
        api_base_present=bool(mini.api_base),
        created_at_utc=run_started,
    )
    writer.write_records(run)
    return run
