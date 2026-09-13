"""Mini coding-agent adapter for the neutral Synapse agent execution port."""

from __future__ import annotations

import hashlib
import json
import subprocess

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
from .outputs import PATCH_CANDIDATE_OUTPUT_V1
from .codec import digest
from .policy import RuntimePolicy, ResourceBudget, IsolationKind, AgentExecutionError, AgentFailureCode
from .registry import CapabilityAdmission
from .runtime import run_process

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
            configuration_sha256=digest(config),
            runtime_policy=RuntimePolicy(isolation=IsolationKind.TRUSTED_PROCESS,
                network="LOCAL_BROKER", writable_workspace=True),
            resource_limits=ResourceBudget(timeout_seconds=config.timeout_seconds, cpu_seconds=config.timeout_seconds),
        )

    @property
    def profile(self) -> AgentProfile:
        return self._profile

    @property
    def mini_transport(self) -> MiniWorkerTransport:
        return self._transport

    @property
    def accounting(self):
        return self._transport.accounting

    def execute(
        self,
        request: AgentExecutionRequest,
        runtime: AgentRuntimeContext,
    ) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest or type(runtime) is not AgentRuntimeContext:
            raise TypeError("Mini adapter requires exact neutral request and runtime records")
        if getattr(self, "_requires_accounting", False) and self._transport.accounting is None:
            raise AgentExecutionError(AgentFailureCode.INPUT_INVALID, "Mini requires its bound accounting owner")
        request.__post_init__()
        runtime.__post_init__()
        if request.required_output_profile != PATCH_CANDIDATE_OUTPUT_V1:
            raise ValueError("Mini adapter supports only the patch-candidate output profile")
        if request.artifacts:
            raise ValueError("Mini coding profile does not accept generic artifact inputs")
        if request.information_text is not None and request.information_policy is not LocalInformationPolicy.LOCAL_ONLY:
            raise ValueError("Mini local information requires the LOCAL_ONLY request policy")
        schema = WORKER_INVOCATION_SCHEMA_V2 if request.information_text is not None else WORKER_INVOCATION_SCHEMA_V1
        capabilities = request.required_capabilities
        if schema == WORKER_INVOCATION_SCHEMA_V2:
            from synapse.worker.input_contract import WorkerTaskInput
            # Restore the historical task binding, including C1-owned obligations.
            # Agent eligibility and runtime authority use the separate request.
            task = WorkerTaskInput(request.task_text.encode("utf-8")).to_dict()
            capabilities = tuple(task["capabilities"])
            if not set(request.required_capabilities).issubset(capabilities):
                raise AgentExecutionError(AgentFailureCode.INPUT_INVALID,
                    "delegated capabilities exceed the historical task")
        invocation = WorkerInvocation(
            invocation_id=request.invocation_id,
            attempt_id=request.attempt_id,
            context_id=request.context_id,
            payload_text=request.task_text,
            payload_sha256=request.task_sha256,
            payload_byte_length=request.task_byte_length,
            envelope_sha256=request.envelope_sha256,
            allowed_scope=request.allowed_scope,
            capabilities=capabilities,
            schema_version=schema,
            information_text=request.information_text,
            information_sha256=request.information_sha256,
            information_byte_length=request.information_byte_length,
        )
        retained = []
        def supervised(command, **kwargs):
            if command[0] == "git":
                return subprocess.run(command, **kwargs)
            observation = run_process(request=request, runtime=runtime, profile=self.profile,
                argv=tuple(command), environment=tuple(sorted(kwargs.get("env", {}).items())))
            retained.extend(observation.evidence_refs)
            if observation.failure is AgentFailureCode.TIMEOUT:
                raise subprocess.TimeoutExpired(command, request.resource_budget.timeout_seconds)
            if observation.failure is not None:
                raise AgentExecutionError(observation.failure, "Mini runtime stopped by execution policy")
            return subprocess.CompletedProcess(command, observation.returncode,
                observation.stdout.decode("utf-8", errors="replace"), observation.stderr.decode("utf-8", errors="replace"))
        candidate = self._transport.run(runtime.execution_root, invocation, runner=supervised)
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
            evidence_refs=tuple(sorted(set(retained))),
        )


def historical_mini_admission(adapter: MiniAgentAdapter) -> CapabilityAdmission:
    """Preserve the explicit historical worker declaration; never admit plugins."""
    if type(adapter) is not MiniAgentAdapter:
        raise TypeError("historical worker admission applies only to its exact adapter")
    return CapabilityAdmission(digest(adapter.profile), adapter.profile.capabilities,
        digest({"source": "historical-stage10-worker-declaration/v1", "configuration": adapter.mini_transport.config}))


__all__ = ["MiniAgentAdapter", "PATCH_CANDIDATE_OUTPUT_V1"]


class MiniAdapterFactory:
    def create(self, configuration):
        from dataclasses import replace
        from pathlib import Path
        from .configuration import profile_from_dict
        from synapse.experiments.gold.stage10_composition import decode_worker_configuration
        from synapse.experiments.gold.stage15.worker_accounting import validate_accounting_declaration, WorkerAccounting
        from synapse.experiments.gold.stage15.capture_store import CaptureStore
        from synapse.experiments.gold.stage15.telemetry import reference
        from synapse.worker.provider_transport import MiniProviderConfiguration
        native = configuration['native']
        config = decode_worker_configuration(native)
        from synapse.worker.provider_transport import frozen_mini_runtime
        frozen_mini_runtime(list(config.command))
        captured = validate_accounting_declaration(native)
        if config.input_profile != 'mini-2.4.6-local-edit-proposals/v4':
            raise ValueError('new Mini profiles require the governed local-edit v4 boundary')
        admitted = profile_from_dict(configuration['profile'])
        accounting = None
        context = configuration.get('context')
        if context is not None:
            manifest = context['manifest']
            (Path(context['run_root']) / 'stage15').mkdir(exist_ok=True)
            accounting = WorkerAccounting(store=CaptureStore(
                root=Path(context['run_root']) / 'stage15' / 'capture', run_id=context['run_id'],
                manifest_ref=reference(manifest, manifest['payload']['schema_version'])),
                configuration=MiniProviderConfiguration(model=config.model,
                    endpoint=captured['endpoint'], credential_env=captured['credential_env'],
                    timeout_seconds=min(60, config.timeout_seconds)))
        adapter = MiniAgentAdapter(config=config, accounting=accounting)
        expected = replace(adapter.profile, profile_id=admitted.profile_id,
            runtime_identity=admitted.runtime_identity, configuration_sha256=admitted.configuration_sha256)
        if expected != admitted:
            raise ValueError('admitted Mini profile differs from the native implementation')
        adapter._profile = admitted
        adapter._requires_accounting = True
        return adapter
