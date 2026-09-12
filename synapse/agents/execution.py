"""Single neutral execution owner for admitted external-agent adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import AgentExecutionRequest, AgentExecutionResult, AgentRuntimeContext
from .registry import AgentRegistry


def _sha256_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _verify_artifacts(request: AgentExecutionRequest) -> None:
    for artifact in request.artifacts:
        path = Path(artifact.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("agent artifact must remain an existing regular non-symlink file")
        size, digest = _sha256_file(path)
        if size != artifact.byte_length or digest != artifact.sha256:
            raise ValueError("agent artifact bytes differ from their frozen identity")


class AgentExecutionPort:
    """Select one admitted adapter and validate one bound execution result."""

    def __init__(self, registry: AgentRegistry) -> None:
        if type(registry) is not AgentRegistry:
            raise TypeError("agent execution port requires an exact AgentRegistry")
        self._registry = registry

    @property
    def registry(self) -> AgentRegistry:
        return self._registry

    def execute(
        self,
        *,
        request: AgentExecutionRequest,
        runtime: AgentRuntimeContext,
    ) -> AgentExecutionResult:
        if type(request) is not AgentExecutionRequest:
            raise TypeError("agent execution requires an exact AgentExecutionRequest")
        if type(runtime) is not AgentRuntimeContext:
            raise TypeError("agent execution requires an exact AgentRuntimeContext")
        request.__post_init__()
        runtime.__post_init__()
        if not runtime.execution_root.is_dir():
            raise ValueError("agent execution root must exist and be a directory")
        adapter = self._registry.select(request)
        _verify_artifacts(request)
        try:
            result = adapter.execute(request, runtime)
        finally:
            # READ_ONLY v1 artifacts are evidence inputs. A subprocess/container
            # adapter may provide stronger prevention, but the owner always
            # detects changed final bytes before accepting any result.
            _verify_artifacts(request)
        if type(result) is not AgentExecutionResult:
            raise TypeError("agent adapter returned a foreign result")
        result.__post_init__()
        evidence = result.delivery_evidence
        if (
            result.invocation_id != request.invocation_id
            or evidence.invocation_id != request.invocation_id
            or evidence.context_id != request.context_id
            or evidence.task_sha256 != request.task_sha256
            or evidence.task_byte_length != request.task_byte_length
            or evidence.envelope_sha256 != request.envelope_sha256
            or evidence.information_sha256 != request.information_sha256
            or evidence.information_byte_length != request.information_byte_length
            or result.agent_profile_id != adapter.profile.profile_id
        ):
            raise ValueError("agent execution result differs from its frozen request or selected profile")
        if result.outputs and any(item.output_schema not in adapter.profile.output_profiles for item in result.outputs):
            raise ValueError("agent result contains an output schema outside the admitted profile")
        return result


__all__ = ["AgentExecutionPort"]
