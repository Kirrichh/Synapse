"""Single neutral execution owner for admitted external-agent adapters."""

from __future__ import annotations

from .contracts import AgentExecutionRequest, AgentExecutionResult, AgentRuntimeContext
from .registry import AgentRegistry


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
        adapter = self._registry.select(request)
        result = adapter.execute(request, runtime)
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
        return result


__all__ = ["AgentExecutionPort"]
