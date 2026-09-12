"""Mini coding-agent adapter for the neutral Synapse agent execution port."""

from __future__ import annotations

import hashlib
import json

from synapse.experiments.gold.stage10.worker_transport import (
    WORKER_INVOCATION_SCHEMA_V1,
    WORKER_INVOCATION_SCHEMA_V2,
    WorkerCandidateResult,
    WorkerCandidateStatus,
    WorkerDeliveryStatus,
    WorkerInvocation,
)
from synapse.worker.mini_adapter import MiniAdapterConfig, MiniWorkerTransport
from synapse.worker.provider_transport import WorkerAccountingPort

from .contracts import (
    AgentDeliveryEvidence,
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentExecutionStatus,
    AgentOutputEnvelope,
    AgentProfile,
    AgentReport,
    AgentRuntimeContext,
    AgentTokenStatus,
    AgentTransportKind,
    AgentUsage,
    LocalInformationPolicy,
)


PATCH_CANDIDATE_OUTPUT_V1 = "synapse.agent.output.patch-candidate/v1"
_MINI_PROFILE_ID = "mini-coding/v1"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _status(value: WorkerCandidateStatus) -> AgentExecutionStatus:
    if value in (WorkerCandidateStatus.PROPOSED_PATCH, WorkerCandidateStatus.NO_PATCH):
        return AgentExecutionStatus.COMPLETED
    if value is WorkerCandidateStatus.TIMEOUT:
        return AgentExecutionStatus.TIMEOUT
    return AgentExecutionStatus.ERROR


class MiniAgentAdapter:
    """Translate neutral agent requests to the existing exact Mini transport."""

    def __init__(
        self,
        *,
        config: MiniAdapterConfig,
        accounting: WorkerAccountingPort | None = None,
    ) -> None:
        if type(config) is not MiniAdapterConfig:
            raise TypeError("Mini agent adapter requires an exact MiniAdapterConfig")
        self._transport = MiniWorkerTransport(config=config, accounting=accounting)
        self._profile = AgentProfile(
            profile_id=_MINI_PROFILE_ID,
            agent_id="mini-swe-agent",
            agent_version="2.4.6",
            adapter_id="synapse-mini",
            adapter_version="1",
            transport=AgentTransportKind.NATIVE_SUBPROCESS,
            capabilities=("repository.edit",),
            accepted_media_types=(),
            output_profiles=(PATCH_CANDIDATE_OUTPUT_V1,),
            local_information_policy=LocalInformationPolicy.LOCAL_ONLY,
            effect_classes=("PATH_MODIFIED",),
            provider_name="mini",
            model_name=config.model,
        )

    @property
    def profile(self) -> AgentProfile:
        return self._profile

    @property
    def mini_transport(self) -> MiniWorkerTransport:
        return self._transport

    def execute(
        self,
        request: AgentExecutionRequest,
        runtime: AgentRuntimeContext,
    ) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest or type(runtime) is not AgentRuntimeContext:
            raise TypeError("Mini adapter requires exact neutral request and runtime records")
        request.__post_init__()
        runtime.__post_init__()
        if request.required_output_profile != PATCH_CANDIDATE_OUTPUT_V1:
            raise ValueError("Mini adapter supports only the patch-candidate output profile")
        if request.artifacts:
            raise ValueError("Mini coding profile does not accept generic artifact inputs")
        if request.information_text is not None and request.information_policy is not LocalInformationPolicy.LOCAL_ONLY:
            raise ValueError("Mini local information requires the LOCAL_ONLY request policy")
        schema = WORKER_INVOCATION_SCHEMA_V2 if request.information_text is not None else WORKER_INVOCATION_SCHEMA_V1
        invocation = WorkerInvocation(
            invocation_id=request.invocation_id,
            attempt_id=request.attempt_id,
            context_id=request.context_id,
            payload_text=request.task_text,
            payload_sha256=request.task_sha256,
            payload_byte_length=request.task_byte_length,
            envelope_sha256=request.envelope_sha256,
            allowed_scope=request.allowed_scope,
            capabilities=request.required_capabilities,
            schema_version=schema,
            information_text=request.information_text,
            information_sha256=request.information_sha256,
            information_byte_length=request.information_byte_length,
        )
        candidate = self._transport.run(runtime.execution_root, invocation)
        if type(candidate) is not WorkerCandidateResult:
            raise TypeError("Mini transport returned a foreign worker candidate")
        payload = _canonical_json({
            "status": candidate.status.value,
            "diff_text": candidate.diff_text,
            "touched_files": list(candidate.touched_files),
            "diagnostics": dict(candidate.diagnostics),
            "report": {
                "summary": candidate.report.summary,
                "failure_reason": candidate.report.failure_reason,
            },
        })
        usage = candidate.usage
        generic_usage = AgentUsage(
            token_status=AgentTokenStatus(usage.token_status.value),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thinking_tokens=usage.thinking_tokens,
            total_tokens=usage.total_tokens,
            thinking_included=usage.thinking_included,
            diagnostics=usage.diagnostics,
        )
        evidence = candidate.delivery_evidence
        return AgentExecutionResult(
            invocation_id=request.invocation_id,
            agent_profile_id=self.profile.profile_id,
            status=_status(candidate.status),
            outputs=(AgentOutputEnvelope(
                output_schema=PATCH_CANDIDATE_OUTPUT_V1,
                media_type="application/vnd.synapse.patch-candidate+json",
                payload=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_length=len(payload),
            ),),
            usage=generic_usage,
            diagnostics=candidate.diagnostics,
            report=AgentReport(
                summary=candidate.report.summary,
                failure_reason=candidate.report.failure_reason,
            ),
            delivery_evidence=AgentDeliveryEvidence(
                invocation_id=request.invocation_id,
                context_id=request.context_id,
                task_sha256=request.task_sha256,
                task_byte_length=request.task_byte_length,
                envelope_sha256=request.envelope_sha256,
                transport_name=evidence.transport_name,
                process_started=evidence.status is WorkerDeliveryStatus.PROCESS_STARTED,
                information_sha256=request.information_sha256,
                information_byte_length=request.information_byte_length,
            ),
        )


__all__ = ["MiniAgentAdapter", "PATCH_CANDIDATE_OUTPUT_V1"]
