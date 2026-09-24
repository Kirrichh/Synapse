"""Stage 3A baseline SWE-bench experiment runner.

The Baseline arm runs the same admitted agent as Gold through the universal
agent execution port, without Synapse context, memory or local information.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import uuid

from synapse.agents.codec import decode_json
from synapse.agents.contracts import (AgentExecutionRequest, AgentExecutionStatus, AgentRuntimeContext,
                                      LocalInformationPolicy)
from synapse.agents.execution import AgentExecutionPort
from synapse.agents.outputs import PATCH_CANDIDATE_OUTPUT_V1
from synapse.change.workspace import cleanup_worktree, create_detached_worktree
from synapse.worker import ExternalWorkerStatus
from synapse.worker.contract import (ExternalCodingWorkerResult, ExternalWorkerTokenStatus, ExternalWorkerUsage,
                                     WorkerReport)

from .artifacts import ArtifactStore
from .carry import RawCarryEntry, RawTranscriptCarry
from .contract import (
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
    OracleResult,
)
from .oracle import OracleRunner
from .telemetry import TelemetryWriter, token_accounting_from_worker_usage, usage_source_from_worker_status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _attempt_verdict(status: ExternalWorkerStatus, oracle_result: OracleResult | None) -> AttemptVerdict:
    if status is ExternalWorkerStatus.NO_PATCH:
        return AttemptVerdict.NO_CANDIDATE
    if status in {ExternalWorkerStatus.ERROR, ExternalWorkerStatus.TIMEOUT}:
        return AttemptVerdict.INFRA_ERROR
    if oracle_result is not None and oracle_result.diagnostics.get("infra_error") is True:
        return AttemptVerdict.INFRA_ERROR
    if oracle_result is not None and oracle_result.resolved is True:
        return AttemptVerdict.ORACLE_RESOLVED
    return AttemptVerdict.ORACLE_UNRESOLVED


def _worker_model_patch_diagnostics(
    *,
    worker_diff_text: str | None,
    worker_diff_artifact_present: bool,
    oracle_result: OracleResult | None,
) -> dict[str, object]:
    worker_diff_sha256 = (
        hashlib.sha256(worker_diff_text.encode("utf-8")).hexdigest()
        if worker_diff_text is not None
        else None
    )
    model_patch_sha256 = (
        oracle_result.diagnostics.get("model_patch_sha256")
        if oracle_result is not None
        else None
    )
    if isinstance(model_patch_sha256, str) and worker_diff_sha256 is not None:
        matches: bool | str = worker_diff_sha256 == model_patch_sha256
    else:
        matches = "unknown"
    return {
        "worker_diff_artifact_present": worker_diff_artifact_present,
        "worker_diff_sha256": worker_diff_sha256,
        "model_patch_sha256": model_patch_sha256,
        "worker_diff_matches_model_patch": matches,
        "worker_diff_model_patch_mismatch": matches is False,
        "model_patch_source": (
            oracle_result.diagnostics.get("patch_source")
            if oracle_result is not None
            else None
        ),
    }


def _build_prompt(task: BaselineTask, carry: RawTranscriptCarry) -> str:
    allowed = "\n".join(f"- {path}" for path in task.allowed_scope)
    return (
        f"{task.statement}\n\n"
        "Allowed scope:\n"
        f"{allowed}\n"
        f"{carry.render_prompt_suffix()}"
    )


def _agent_candidate(port: AgentExecutionPort, *, worktree: Path, evidence_root: Path, prompt: str,
                     invocation_id: str, attempt_id: str, allowed_scope) -> ExternalCodingWorkerResult:
    """One agent invocation over the Baseline worktree, as the arm's worker result."""
    profile = port.registry.adapters[0].profile
    raw = prompt.encode("utf-8")
    request = AgentExecutionRequest(
        invocation_id=invocation_id, attempt_id=attempt_id, context_id="baseline-" + attempt_id,
        task_text=prompt, task_sha256=hashlib.sha256(raw).hexdigest(), task_byte_length=len(raw),
        envelope_sha256=hashlib.sha256(b"synapse.baseline.envelope/v1\0" + raw).hexdigest(),
        required_capabilities=("repository.edit",), required_output_profile=PATCH_CANDIDATE_OUTPUT_V1,
        required_effect_classes=("PATH_MODIFIED",), allowed_effects=("PATH_MODIFIED",),
        allowed_scope=tuple(sorted(allowed_scope)), information_policy=LocalInformationPolicy.NOT_SUPPORTED,
        resource_budget=profile.resource_limits, selected_profile_id=profile.profile_id,
        allowed_networks=(profile.runtime_policy.network,))
    result = port.execute(request=request, runtime=AgentRuntimeContext(
        execution_root=Path(worktree).absolute(), evidence_root=evidence_root))
    usage = result.usage
    worker_usage = ExternalWorkerUsage(token_status=ExternalWorkerTokenStatus(usage.token_status.value),
        input_tokens=usage.input_tokens, output_tokens=usage.output_tokens, thinking_tokens=usage.thinking_tokens,
        total_tokens=usage.total_tokens, thinking_included=usage.thinking_included, diagnostics=dict(usage.diagnostics))
    if result.status is not AgentExecutionStatus.COMPLETED or len(result.outputs) != 1:
        status = ExternalWorkerStatus.TIMEOUT if result.status is AgentExecutionStatus.TIMEOUT else ExternalWorkerStatus.ERROR
        return ExternalCodingWorkerResult(worker_status=status, diff_text=None, touched_files=(), usage=worker_usage,
            diagnostics=dict(result.diagnostics), worker_report=WorkerReport(summary=result.report.summary,
                failure_reason=result.report.failure_reason or "agent_" + result.status.value.lower()))
    payload = decode_json(result.outputs[0].payload)
    if payload["status"] not in {"PROPOSED_PATCH", "NO_PATCH"}:
        raise ValueError("a completed agent returned a failed patch candidate")
    report = payload["report"]
    return ExternalCodingWorkerResult(worker_status=ExternalWorkerStatus(payload["status"]),
        diff_text=payload["diff_text"], touched_files=tuple(payload["touched_files"]), usage=worker_usage,
        diagnostics=dict(payload["diagnostics"]),
        worker_report=WorkerReport(summary=report["summary"], failure_reason=report["failure_reason"]))


def run_baseline_task(
    task: BaselineTask,
    *,
    repo_root: str | Path,
    base_revision: str,
    replicate_id: int,
    max_attempts: int = 3,
    agent: AgentExecutionPort,
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
            worker_result = _agent_candidate(agent, worktree=Path(worktree.path),
                evidence_root=run_dir / "agent-executions", prompt=prompt,
                invocation_id=f"{run_id}:attempt:{attempt_id}", attempt_id=str(attempt_id),
                allowed_scope=task.allowed_scope)
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
                oracle_result,
            )
            worker_diff_artifact_present = any(artifact.kind == "worker_diff" for artifact in artifacts)
            attempt_diagnostics = {
                "worktree_cleanup_policy": "remove",
                "candidate_is_not_success": True,
                **_worker_model_patch_diagnostics(
                    worker_diff_text=worker_result.diff_text,
                    worker_diff_artifact_present=worker_diff_artifact_present,
                    oracle_result=oracle_result,
                ),
            }
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
                diagnostics=attempt_diagnostics,
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
    profile = agent.registry.adapters[0].profile
    writer.write_manifest(
        run=run,
        provider=profile.provider_name,
        model=profile.model_name,
        api_base_present=False,
        created_at_utc=run_started,
    )
    writer.write_records(run)
    return run
