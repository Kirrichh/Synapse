"""Single execution owner: select, bind, retain, dispatch and validate results."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path

from .codec import canonical_bytes, digest
from .contracts import (AgentExecutionRequest, AgentExecutionResult, AgentRuntimeContext,
    AgentExecutionStatus, AgentDeliveryEvidence, AgentUsage, AgentTokenStatus, AgentReport)
from .outputs import OutputRegistry, DocumentObservationSet
from .policy import AgentExecutionError, AgentFailureCode
from .registry import AgentRegistry
from .retention import InvocationStore


def _artifact_bytes(artifact):
    path = Path(artifact.path)
    if path.is_symlink() or not path.is_file():
        raise AgentExecutionError(AgentFailureCode.ARTIFACT_UNAVAILABLE, "agent artifact is not a regular non-symlink file")
    with path.open('rb') as stream:
        raw = stream.read(artifact.byte_length + 1)
    if len(raw) != artifact.byte_length or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise AgentExecutionError(AgentFailureCode.ARTIFACT_UNAVAILABLE, "agent artifact differs from frozen bytes")
    return raw


def failure_result(request, profile, code, *, started=False, evidence_refs=()):
    status = {AgentFailureCode.TIMEOUT: AgentExecutionStatus.TIMEOUT,
              AgentFailureCode.CANCELLED: AgentExecutionStatus.CANCELLED}.get(code, AgentExecutionStatus.ERROR)
    if code in {AgentFailureCode.UNSUPPORTED_CAPABILITY, AgentFailureCode.LOCAL_INFORMATION_POLICY_VIOLATION,
                AgentFailureCode.NETWORK_DENIED}:
        status = AgentExecutionStatus.REFUSED
    return AgentExecutionResult(
        invocation_id=request.invocation_id, agent_profile_id=profile.profile_id,
        status=status, outputs=(), usage=AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False),
        diagnostics={"failure_code": code.value}, report=AgentReport(failure_reason=code.value),
        delivery_evidence=AgentDeliveryEvidence(request.invocation_id, request.context_id, request.task_sha256,
            request.task_byte_length, request.envelope_sha256, profile.transport.value, started,
            request.information_sha256, request.information_byte_length), evidence_refs=evidence_refs)


class AgentExecutionPort:
    def __init__(self, registry: AgentRegistry, *, evidence_root: Path | None = None,
                 outputs: OutputRegistry | None = None):
        if type(registry) is not AgentRegistry:
            raise TypeError("agent execution port requires an exact AgentRegistry")
        self._registry = registry
        self._evidence_root = evidence_root
        self.outputs = outputs or OutputRegistry()
        for adapter in registry.adapters:
            if not all(self.outputs.supports(s) for s in adapter.profile.output_profiles):
                raise ValueError("admitted agent requires a registered output codec")

    @property
    def registry(self):
        return self._registry

    @property
    def evidence_root(self):
        return self._evidence_root

    @property
    def accounting(self):
        # Optional physical-call accounting belongs to the selected adapter.
        if len(self._registry.adapters) != 1:
            raise ValueError("accounting lookup requires a frozen selected profile")
        return getattr(self._registry.adapters[0], "accounting", None)

    def execute(self, *, request: AgentExecutionRequest, runtime: AgentRuntimeContext) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest or type(runtime) is not AgentRuntimeContext:
            raise TypeError("agent execution requires exact request/runtime contracts")
        request.__post_init__()
        runtime.__post_init__()
        if not runtime.execution_root.is_dir():
            raise ValueError("agent execution root must be an existing directory")
        adapter = self._registry.select(request)
        root = self._evidence_root or runtime.evidence_root
        if root is None:
            root = runtime.execution_root.parent / (".synapse-agents-" + digest(str(runtime.execution_root))[:16])
        root = root.absolute()
        if root.resolve().is_relative_to(runtime.execution_root.resolve()):
            raise ValueError("retained agent evidence must be outside the agent workspace")
        store = InvocationStore(root)
        with store.executing(request.invocation_id):
            return self._execute_bound(request, runtime, adapter, root, store)

    def _execute_bound(self, request, runtime, adapter, root, store):
        profile = adapter.profile
        binding = canonical_bytes({"request": request, "profile": profile,
                                   "admission": self._registry.admission(adapter), "execution_root": runtime.execution_root,
                                   "provenance_sha256": [hashlib.sha256(raw).hexdigest() for raw in runtime.provenance]})
        resuming = False
        try:
            existing = store.claim(request.invocation_id, binding)
        except AgentExecutionError as exc:
            if exc.code is not AgentFailureCode.EXECUTION_STATE_UNKNOWN or not callable(getattr(adapter, "resume", None)):
                raise
            existing = None
            resuming = True
        if existing is not None:
            # Historical reads use retained bytes and never redispatch the agent.
            _, existing = store.restore(request.invocation_id)
            self._validate(existing, request, profile)
            return existing
        refs = [store.retain(binding), *(store.retain(raw) for raw in (*runtime.provenance, *self._registry.retained_evidence))]
        invocation_root = root / 'processes' / digest(request.invocation_id)
        invocation_root.mkdir(parents=True, exist_ok=resuming, mode=0o700)
        bound_runtime = replace(runtime, evidence_root=root, invocation_root=invocation_root)
        try:
            retained_artifacts = []
            for artifact in request.artifacts:
                raw = _artifact_bytes(artifact)
                refs.append(store.retain(raw))
                source = invocation_root / ("input-" + digest(artifact.artifact_id))
                if source.exists():
                    if source.is_symlink() or source.read_bytes() != raw:
                        raise AgentExecutionError(AgentFailureCode.ARTIFACT_UNAVAILABLE, "retained invocation input changed")
                else:
                    with source.open("xb") as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    source.chmod(0o400)
                retained_artifacts.append(replace(artifact, path=str(source)))
            delivered_request = replace(request, artifacts=tuple(retained_artifacts))
            refs.append(store.retain(canonical_bytes(delivered_request)))
            if store.is_cancelled(request.invocation_id):
                raise AgentExecutionError(AgentFailureCode.CANCELLED, "agent invocation was cancelled before dispatch")
            result = adapter.resume(delivered_request, bound_runtime) if resuming else adapter.execute(delivered_request, bound_runtime)
            self._registry.admission(adapter)
            for artifact in request.artifacts:
                _artifact_bytes(artifact)
            self._validate(result, request, profile)
        except AgentExecutionError as exc:
            if exc.code is AgentFailureCode.EXECUTION_STATE_UNKNOWN:
                raise
            result = failure_result(request, profile, exc.code, started=store.latest_event(request.invocation_id, "PROCESS_STARTED") is not None)
        except OSError:
            result = failure_result(request, profile, AgentFailureCode.PROCESS_NOT_STARTED, started=False)
        except Exception as exc:
            refs.append(store.event(request.invocation_id, {"kind": "ADAPTER_FAILURE", "exception_type": type(exc).__name__, "detail": str(exc)[:512]}))
            # Adapter/library exceptions never escape into Gold as native authority.
            result = failure_result(request, profile, AgentFailureCode.OUTPUT_INVALID, started=store.latest_event(request.invocation_id, "PROCESS_STARTED") is not None)
        for artifact in request.artifacts:
            try:
                _artifact_bytes(artifact)
            except AgentExecutionError:
                result = failure_result(request, profile, AgentFailureCode.ARTIFACT_UNAVAILABLE,
                    started=result.delivery_evidence.process_started, evidence_refs=result.evidence_refs)
        result = replace(result, evidence_refs=tuple(sorted(set((*refs, *store.event_refs(request.invocation_id), *result.evidence_refs)))))
        store.finish(request.invocation_id, result)
        return result

    def _validate(self, result, request, profile):
        if type(result) is not AgentExecutionResult:
            raise TypeError("agent adapter returned a foreign result")
        result.__post_init__()
        evidence = result.delivery_evidence
        if (result.invocation_id != request.invocation_id or result.agent_profile_id != profile.profile_id
                or evidence.context_id != request.context_id or evidence.task_sha256 != request.task_sha256
                or evidence.task_byte_length != request.task_byte_length or evidence.envelope_sha256 != request.envelope_sha256
                or evidence.information_sha256 != request.information_sha256
                or evidence.information_byte_length != request.information_byte_length):
            raise ValueError("agent result differs from the frozen request/profile")
        if sum(o.byte_length for o in result.outputs) > request.resource_budget.output_bytes:
            raise AgentExecutionError(AgentFailureCode.RESOURCE_LIMIT, "agent output budget exceeded")
        for item in result.outputs:
            if item.output_schema not in profile.output_profiles or item.output_schema != request.required_output_profile:
                raise ValueError("agent returned an unrequested output profile")
            decoded = self.outputs.decode(item)
            if isinstance(decoded, DocumentObservationSet):
                expected = {a.artifact_id: a.sha256 for a in request.artifacts}
                observed = {s.artifact_id: s.source_sha256 for s in decoded.sources}
                if observed != expected:
                    raise ValueError("document observations do not cover the exact supplied sources")
                if result.status is AgentExecutionStatus.COMPLETED and any(s.status != "PARSED" for s in decoded.sources):
                    raise ValueError("incomplete extraction cannot claim completed execution")
        if result.status is AgentExecutionStatus.COMPLETED and not result.outputs:
            raise ValueError("completed execution lacks the requested typed output")

    def restore(self, invocation_id: str):
        if self._evidence_root is None:
            raise ValueError("restore requires an explicit retained evidence root")
        return InvocationStore(self._evidence_root).restore(invocation_id)

    def cancel(self, invocation_id: str) -> bool:
        if self._evidence_root is None:
            raise ValueError("cancellation requires an explicit retained evidence root")
        return InvocationStore(self._evidence_root).cancel(invocation_id)
