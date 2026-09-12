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
    LocalInformationPolicy,
)
from .execution import AgentExecutionPort
from .outputs import PATCH_CANDIDATE_OUTPUT_V1


class AgentBackedWorkerTransport:
    """Historical Stage 10 worker port backed by universal agent selection."""

    def __init__(self, execution_port: AgentExecutionPort) -> None:
        if type(execution_port) is not AgentExecutionPort:
            raise TypeError("worker bridge requires an exact AgentExecutionPort")
        self._execution_port = execution_port

    @property
    def execution_port(self) -> AgentExecutionPort:
        return self._execution_port

    @property
    def accounting(self):
        return self._execution_port.accounting

    def run(
        self,
        worktree_path: str | Path,
        invocation: WorkerInvocation,
        *, context, persistence, plan_persistence, authorization,
    ) -> WorkerCandidateResult:
        if type(invocation) is not WorkerInvocation:
            raise TypeError("worker bridge requires an exact WorkerInvocation")
        invocation.__post_init__()
        from synapse.experiments.gold.stage10.worker_context_adapter import create_worker_invocation
        expected = create_worker_invocation(context=context, persistence=persistence,
            plan_persistence=plan_persistence, authorization=authorization)
        if expected != invocation:
            raise ValueError("agent invocation differs from Gold's persisted authorized task")
        from .codec import canonical_bytes
        provenance = (context.canonical_bytes(), authorization.canonical_bytes(), canonical_bytes({
            "intent": plan_persistence.intent_store_ref.to_dict(),
            "plan": plan_persistence.plan_store_ref.to_dict(),
            "decision": plan_persistence.decision_store_ref.to_dict(),
            "accepted_plan": plan_persistence.accepted_plan_store_ref.to_dict(),
            "bundle_sha256": plan_persistence.bundle_sha256,
        }))
        profile = self._execution_port.registry.adapters[0].profile
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
            required_effect_classes=("PATH_MODIFIED",),
            allowed_effects=("PATH_MODIFIED",),
            resource_budget=profile.resource_limits,
            selected_profile_id=profile.profile_id,
            allowed_networks=(profile.runtime_policy.network,),
            task_contract_ref=None if context.intent.task_contract_ref is None else context.intent.task_contract_ref.sha256,
            plan_ref=plan_persistence.bundle_sha256,
            information_policy=(LocalInformationPolicy.LOCAL_ONLY if invocation.information_text is not None
                                else LocalInformationPolicy.NOT_SUPPORTED),
            allowed_scope=invocation.allowed_scope,
            information_text=invocation.information_text,
            information_sha256=invocation.information_sha256,
            information_byte_length=invocation.information_byte_length,
        )
        result = self._execution_port.execute(
            request=request,
            runtime=AgentRuntimeContext(execution_root=Path(worktree_path).absolute(), provenance=provenance),
        )
        if not result.outputs and result.status in (AgentExecutionStatus.ERROR, AgentExecutionStatus.REFUSED,
                                                    AgentExecutionStatus.TIMEOUT, AgentExecutionStatus.CANCELLED):
            payload = {
                "status": "TIMEOUT" if result.status is AgentExecutionStatus.TIMEOUT else "ERROR",
                "diff_text": None, "touched_files": [], "diagnostics": dict(result.diagnostics),
                "report": {"summary": result.report.summary, "failure_reason": result.report.failure_reason},
            }
        else:
            if len(result.outputs) != 1 or result.outputs[0].output_schema != PATCH_CANDIDATE_OUTPUT_V1:
                raise ValueError("coding worker requires exactly one patch-candidate output")
            from .codec import decode_json
            payload = decode_json(result.outputs[0].payload)
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
        for key in ("tracked_files", "untracked_files", "scope_violations"):
            if key in diagnostics:
                if type(diagnostics[key]) is not list:
                    raise ValueError("historical path diagnostics must be exact arrays")
                diagnostics[key] = tuple(diagnostics[key])
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
