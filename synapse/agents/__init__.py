"""Universal external-agent execution boundary for Synapse."""

from .contracts import (
    AGENT_EXECUTION_REQUEST_V1,
    AGENT_EXECUTION_RESULT_V1,
    AGENT_PROFILE_V1,
    AgentArtifactInput,
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
from .execution import AgentExecutionPort
from .registry import AgentAdapter, AgentRegistry, AgentSelectionError, discover_adapter_factories

__all__ = [
    "AGENT_EXECUTION_REQUEST_V1",
    "AGENT_EXECUTION_RESULT_V1",
    "AGENT_PROFILE_V1",
    "AgentAdapter",
    "AgentArtifactInput",
    "AgentDeliveryEvidence",
    "AgentExecutionPort",
    "AgentExecutionRequest",
    "AgentExecutionResult",
    "AgentExecutionStatus",
    "AgentOutputEnvelope",
    "AgentProfile",
    "AgentRegistry",
    "AgentReport",
    "AgentRuntimeContext",
    "AgentSelectionError",
    "AgentTokenStatus",
    "AgentTransportKind",
    "AgentUsage",
    "LocalInformationPolicy",
    "discover_adapter_factories",
]
