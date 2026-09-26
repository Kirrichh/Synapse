"""Physical journal and authority refusals; reference adapters are acceptance only."""

from dataclasses import replace
import hashlib

import pytest

from synapse.agents.codec import canonical_bytes, decode_json, digest
from synapse.agents.contracts import (
    AgentArtifactInput, AgentDeliveryEvidence, AgentExecutionRequest, AgentExecutionResult,
    AgentExecutionStatus, AgentProfile, AgentReport, AgentRuntimeContext, AgentTokenStatus,
    AgentTransportKind, AgentUsage, LocalInformationPolicy,
)
from synapse.agents.execution import AgentExecutionPort
from synapse.agents.outputs import OutputRegistry, SchemaOutputCodec, output_envelope
from synapse.agents.policy import AgentExecutionError, AgentFailureCode
from synapse.agents.registry import AgentRegistry, AgentSelectionError, CapabilityAdmission
from synapse.agents.retention import InvocationStore


MEASUREMENT = "reference.byte-measurement/v1"


class ReferenceAdapter:
    """Small local reference algorithm, with no task-success authority."""

    def __init__(self, name="reference", *, interrupted=False):
        self.profile = AgentProfile(name, name, "1", "reference", "1", AgentTransportKind.STDIO,
            ("bytes.measure",), ("application/octet-stream",), (MEASUREMENT,),
            LocalInformationPolicy.NOT_SUPPORTED, ("READ_ONLY",), "reference")
        self.calls = 0
        self.interrupted = interrupted

    def execute(self, request, runtime):
        from pathlib import Path
        self.calls += 1
        if self.interrupted:
            raise AgentExecutionError(AgentFailureCode.EXECUTION_STATE_UNKNOWN, "dispatch acknowledgement lost")
        assert all(Path(item.path).parent == runtime.invocation_root for item in request.artifacts)
        value = {"bytes": sum(len(Path(item.path).read_bytes()) for item in request.artifacts)}
        return AgentExecutionResult(request.invocation_id, self.profile.profile_id, AgentExecutionStatus.COMPLETED,
            (output_envelope(MEASUREMENT, value),), AgentUsage(AgentTokenStatus.UNAVAILABLE, None, None, None, None, False),
            {}, AgentReport(), AgentDeliveryEvidence(request.invocation_id, request.context_id, request.task_sha256,
                request.task_byte_length, request.envelope_sha256, "acceptance-reference", False))


def execution(tmp_path, adapters):
    root = tmp_path / "workspace"
    root.mkdir()
    schema = {"type": "object", "properties": {"bytes": {"type": "integer", "minimum": 0}},
              "required": ["bytes"], "additionalProperties": False}
    registry = AgentRegistry(adapters, admissions=tuple(CapabilityAdmission(digest(a.profile), a.profile.capabilities,
        digest({"authority": "acceptance only", "profile": a.profile.profile_id})) for a in adapters))
    port = AgentExecutionPort(registry, evidence_root=tmp_path / "evidence",
                             outputs=OutputRegistry((SchemaOutputCodec(MEASUREMENT, canonical_bytes(schema)),)))
    task = "measure bytes"
    request = AgentExecutionRequest("invocation", "attempt", "context", task, hashlib.sha256(task.encode()).hexdigest(),
        len(task), digest(task), ("bytes.measure",), MEASUREMENT, ("READ_ONLY",), (), LocalInformationPolicy.NOT_SUPPORTED,
        allowed_effects=("READ_ONLY",))
    return port, request, AgentRuntimeContext(root)


def test_restore_reopens_retained_inputs_and_never_reexecutes(tmp_path):
    adapter = ReferenceAdapter()
    port, request, runtime = execution(tmp_path, (adapter,))
    artifact = tmp_path / "source.bin"
    raw = "Данные".encode()
    artifact.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    request = replace(request, artifacts=(AgentArtifactInput("source", "application/octet-stream", sha, len(raw), str(artifact)),))
    result = port.execute(request=request, runtime=runtime)
    assert result.status is AgentExecutionStatus.COMPLETED
    assert decode_json(result.outputs[0].payload) == {"bytes": len(raw)}
    artifact.unlink()
    assert InvocationStore(port.evidence_root).read(sha) == raw
    assert port.restore(request.invocation_id)[1] == result
    assert port.execute(request=request, runtime=runtime) == result
    assert adapter.calls == 1
    assert port.cancel(request.invocation_id) is False
    with pytest.raises(AgentExecutionError) as failure:
        port.execute(request=replace(request, attempt_id="different-attempt"), runtime=runtime)
    assert failure.value.code is AgentFailureCode.INPUT_INVALID


def test_unknown_dispatch_never_retries_or_selects_another_adapter(tmp_path):
    chosen, alternative = ReferenceAdapter("a", interrupted=True), ReferenceAdapter("b")
    port, request, runtime = execution(tmp_path, (chosen, alternative))
    for _ in range(2):
        with pytest.raises(AgentExecutionError) as failure:
            port.execute(request=request, runtime=runtime)
        assert failure.value.code is AgentFailureCode.EXECUTION_STATE_UNKNOWN
    assert chosen.calls == 1
    assert alternative.calls == 0


def test_profile_preference_cannot_override_capabilities_or_expose_memory(tmp_path):
    adapter = ReferenceAdapter()
    port, request, runtime = execution(tmp_path, (adapter,))
    with pytest.raises(AgentSelectionError):
        port.execute(request=replace(request, required_capabilities=("repository.edit",)), runtime=runtime)
    secret = "local knowledge"
    with pytest.raises(AgentSelectionError):
        port.execute(request=replace(request, information_policy=LocalInformationPolicy.LOCAL_ONLY,
            information_text=secret, information_sha256=hashlib.sha256(secret.encode()).hexdigest(),
            information_byte_length=len(secret)), runtime=runtime)
    with pytest.raises(ValueError):
        AgentExecutionStatus("FULL")
    assert adapter.calls == 0


def test_concurrent_owner_cannot_dispatch_the_same_invocation(tmp_path):
    adapter = ReferenceAdapter()
    port, request, runtime = execution(tmp_path, (adapter,))
    with InvocationStore(port.evidence_root).executing(request.invocation_id):
        with pytest.raises(AgentExecutionError) as failure:
            port.execute(request=request, runtime=runtime)
        assert failure.value.code is AgentFailureCode.EXECUTION_STATE_UNKNOWN
    assert adapter.calls == 0
