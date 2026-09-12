"""Bridge the historical Stage 10 coding-worker contract to AgentExecutionPort.

The historical WorkerInvocation/WorkerCandidateResult schemas keep their exact
meaning. They are translated at one boundary; Mini itself is selected and run
only through the universal agent port.
"""

from __future__ import annotations

import json
from pathlib import Path

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

from .contracts import (
    AgentExecutionRequest,
    AgentExecutionStatus,
    AgentRuntimeContext,
)
from .execution import AgentExecutionPort
from .mini_adapter import PATCH_CANDIDATE_OUTPUT_V1


class AgentBackedWorkerTransport:
    """Historical Stage 10 worker port backed by universal agent selection."""

    def __init__(self, execution_port: AgentExecutionPort) -> None:
        if type(execution_port) is not AgentExecutionPort:
            raise TypeError("worker bridge requires an exact AgentExecutionPort")
        self._execution_port = execution_port

    @property
    def execution_port(self) -> AgentExecutionPort:
        return self._execution_port

    def run(
        self,
        worktree_path: str | Path,
        invocation: WorkerInvocation,
    ) -> WorkerCandidateResult:
        if type(invocation) is not WorkerInvocation:
            raise TypeError("worker bridge requires an exact WorkerInvocation")
        invocation.__post_init__()
        request = AgentExecutionRequest(
            invocation_id=invocation.invocation_id,
            attempt_id=invocation.attempt_id,
            context_id=invocation.context_id,
            task_text=invocation.payload_text,
            task_sha256=invocation.payload_sha256,
            task_byte_length=invocation.payload_byte_length,
            envelope_sha256=invocation.envelope_sha256,
            required_capabilities=invocation.capabilities,
            required_output_profile=PATCH_CANDIDATE_OUTPUT_V1,
            allowed_scope=invocation.allowed_scope,
            information_text=invocation.information_text,
            information_sha256=invocation.information_sha256,
            information_byte_length=invocation.information_byte_length,
        )
        result = self._execution_port.execute(
            request=request,
            runtime=AgentRuntimeContext(worktree_path=Path(worktree_path)),
        )
        if len(result.outputs) != 1 or result.outputs[0].output_schema != PATCH_CANDIDATE_OUTPUT_V1:
            raise ValueError("coding worker requires exactly one patch-candidate output")
        try:
            payload = json.loads(result.outputs[0].payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("patch-candidate output is not valid UTF-8 JSON") from exc
        if type(payload) is not dict or set(payload) != {
            "status", "diff_text", "touched_files", "diagnostics", "report"
        }:
            raise ValueError("patch-candidate output has an unknown shape")
        status = WorkerCandidateStatus(payload["status"])
        if result.status is AgentExecutionStatus.TIMEOUT and status is not WorkerCandidateStatus.TIMEOUT:
            raise ValueError("agent and coding-worker timeout statuses differ")
        if result.status in (AgentExecutionStatus.ERROR, AgentExecutionStatus.REFUSED, AgentExecutionStatus.CANCELLED) \
                and status not in (WorkerCandidateStatus.ERROR, WorkerCandidateStatus.TIMEOUT):
            raise ValueError("agent failure cannot translate to a successful coding candidate")
        touched = payload["touched_files"]
        report = payload["report"]
        diagnostics = payload["diagnostics"]
        if type(touched) is not list or any(type(item) is not str for item in touched):
            raise ValueError("patch-candidate touched files are malformed")
        if type(report) is not dict or set(report) != {"summary", "failure_reason"}:
            raise ValueError("patch-candidate report is malformed")
        if type(diagnostics) is not dict:
            raise ValueError("patch-candidate diagnostics are malformed")
        usage = result.usage
        evidence = result.delivery_evidence
        return WorkerCandidateResult(
            status=status,
            diff_text=payload["diff_text"],
            touched_files=tuple(touched),
            usage=WorkerCandidateUsage(
                token_status=WorkerTokenStatus(usage.token_status.value),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                thinking_tokens=usage.thinking_tokens,
                total_tokens=usage.total_tokens,
                thinking_included=usage.thinking_included,
                diagnostics=usage.diagnostics,
            ),
            diagnostics=diagnostics,
            report=WorkerCandidateReport(
                summary=report["summary"],
                failure_reason=report["failure_reason"],
            ),
            delivery_evidence=WorkerDeliveryEvidence(
                invocation_id=invocation.invocation_id,
                context_id=invocation.context_id,
                payload_sha256=invocation.payload_sha256,
                payload_byte_length=invocation.payload_byte_length,
                envelope_sha256=invocation.envelope_sha256,
                status=(WorkerDeliveryStatus.PROCESS_STARTED
                        if evidence.process_started else WorkerDeliveryStatus.NOT_DISPATCHED),
                transport_name=evidence.transport_name,
                input_schema_version=invocation.schema_version,
                information_sha256=invocation.information_sha256,
                information_byte_length=invocation.information_byte_length,
            ),
        )


__all__ = ["AgentBackedWorkerTransport"]
