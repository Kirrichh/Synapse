"""Neutral typed contracts for external-agent execution.

This layer owns transport facts only. It never grants verification, task success,
admission, publication, replay, or memory authority to an external agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


AGENT_PROFILE_V1 = "synapse.agent.profile/v1"
AGENT_EXECUTION_REQUEST_V1 = "synapse.agent.execution-request/v1"
AGENT_EXECUTION_RESULT_V1 = "synapse.agent.execution-result/v1"
AGENT_OUTPUT_V1 = "synapse.agent.output/v1"

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class AgentTransportKind(str, Enum):
    NATIVE_SUBPROCESS = "NATIVE_SUBPROCESS"
    STDIO = "STDIO"
    A2A = "A2A"
    OCI_STDIO = "OCI_STDIO"


class LocalInformationPolicy(str, Enum):
    NOT_SUPPORTED = "NOT_SUPPORTED"
    LOCAL_ONLY = "LOCAL_ONLY"
    PROVIDER_VISIBLE_EXPLICIT = "PROVIDER_VISIBLE_EXPLICIT"


class AgentExecutionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    REFUSED = "REFUSED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class AgentTokenStatus(str, Enum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    TOOL_REPORTED = "TOOL_REPORTED"
    UNAVAILABLE = "UNAVAILABLE"


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded safe identifier")
    return value


def _digest(value: object, field_name: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _sorted_strings(value: object, field_name: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(value) is not tuple or (nonempty and not value):
        raise TypeError(f"{field_name} must be an exact tuple")
    if any(type(item) is not str or not item for item in value):
        raise TypeError(f"{field_name} must contain non-empty strings")
    if value != tuple(sorted(set(value))):
        raise ValueError(f"{field_name} must be sorted and unique")
    return value


@dataclass(frozen=True)
class AgentProfile:
    profile_id: str
    agent_id: str
    agent_version: str
    adapter_id: str
    adapter_version: str
    transport: AgentTransportKind
    capabilities: tuple[str, ...]
    accepted_media_types: tuple[str, ...]
    output_profiles: tuple[str, ...]
    local_information_policy: LocalInformationPolicy
    effect_classes: tuple[str, ...]
    provider_name: str
    model_name: str | None = None
    schema_version: str = AGENT_PROFILE_V1

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_PROFILE_V1:
            raise ValueError("agent profile schema is unknown")
        for name in ("profile_id", "agent_id", "adapter_id", "provider_name"):
            _identifier(getattr(self, name), name)
        for name in ("agent_version", "adapter_version"):
            value = getattr(self, name)
            if type(value) is not str or not value or len(value) > 128:
                raise ValueError(f"{name} must be bounded and non-empty")
        if type(self.transport) is not AgentTransportKind:
            raise TypeError("agent transport kind must be exact")
        if type(self.local_information_policy) is not LocalInformationPolicy:
            raise TypeError("local-information policy must be exact")
        _sorted_strings(self.capabilities, "capabilities", nonempty=True)
        _sorted_strings(self.accepted_media_types, "accepted_media_types")
        _sorted_strings(self.output_profiles, "output_profiles", nonempty=True)
        _sorted_strings(self.effect_classes, "effect_classes")
        if self.model_name is not None and (type(self.model_name) is not str or not self.model_name):
            raise ValueError("model_name must be absent or non-empty")


@dataclass(frozen=True)
class AgentArtifactInput:
    artifact_id: str
    media_type: str
    sha256: str
    byte_length: int
    path: str
    access_mode: str = "READ_ONLY"

    def __post_init__(self) -> None:
        _identifier(self.artifact_id, "artifact_id")
        if type(self.media_type) is not str or not self.media_type:
            raise ValueError("artifact media type must be non-empty")
        _digest(self.sha256, "artifact sha256")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("artifact byte length is invalid")
        if type(self.path) is not str or not self.path or "\x00" in self.path:
            raise ValueError("artifact path is invalid")
        if self.access_mode != "READ_ONLY":
            raise ValueError("v1 artifact inputs are read-only")


@dataclass(frozen=True)
class AgentExecutionRequest:
    invocation_id: str
    attempt_id: str
    context_id: str
    task_text: str
    task_sha256: str
    task_byte_length: int
    envelope_sha256: str
    required_capabilities: tuple[str, ...]
    required_output_profile: str
    allowed_scope: tuple[str, ...]
    information_text: str | None = None
    information_sha256: str | None = None
    information_byte_length: int | None = None
    artifacts: tuple[AgentArtifactInput, ...] = ()
    schema_version: str = AGENT_EXECUTION_REQUEST_V1

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_EXECUTION_REQUEST_V1:
            raise ValueError("agent execution request schema is unknown")
        for name in ("invocation_id", "attempt_id", "context_id", "required_output_profile"):
            _identifier(getattr(self, name), name)
        if type(self.task_text) is not str or not self.task_text:
            raise ValueError("agent task text must be non-empty")
        task = self.task_text.encode("utf-8")
        if self.task_byte_length != len(task) or self.task_sha256 != hashlib.sha256(task).hexdigest():
            raise ValueError("agent task binding differs from exact task bytes")
        _digest(self.envelope_sha256, "envelope_sha256")
        _sorted_strings(self.required_capabilities, "required_capabilities", nonempty=True)
        _sorted_strings(self.allowed_scope, "allowed_scope", nonempty=True)
        if self.information_text is None:
            if self.information_sha256 is not None or self.information_byte_length is not None:
                raise ValueError("absent local information cannot carry a digest or length")
        else:
            raw = self.information_text.encode("utf-8")
            if self.information_byte_length != len(raw) or self.information_sha256 != hashlib.sha256(raw).hexdigest():
                raise ValueError("local information binding differs from exact bytes")
        if type(self.artifacts) is not tuple or any(type(item) is not AgentArtifactInput for item in self.artifacts):
            raise TypeError("agent artifacts must be exact AgentArtifactInput records")
        artifact_ids = tuple(item.artifact_id for item in self.artifacts)
        if artifact_ids != tuple(sorted(set(artifact_ids))):
            raise ValueError("agent artifacts must be sorted by unique identity")


@dataclass(frozen=True)
class AgentRuntimeContext:
    worktree_path: Path

    def __post_init__(self) -> None:
        if type(self.worktree_path) is not type(Path()):
            raise TypeError("agent runtime worktree must be an exact platform Path")


@dataclass(frozen=True)
class AgentOutputEnvelope:
    output_schema: str
    media_type: str
    payload: bytes
    sha256: str
    byte_length: int
    schema_version: str = AGENT_OUTPUT_V1

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_OUTPUT_V1:
            raise ValueError("agent output schema is unknown")
        _identifier(self.output_schema, "output_schema")
        if type(self.media_type) is not str or not self.media_type:
            raise ValueError("agent output media type must be non-empty")
        if type(self.payload) is not bytes:
            raise TypeError("agent output payload must be exact bytes")
        if self.byte_length != len(self.payload) or self.sha256 != hashlib.sha256(self.payload).hexdigest():
            raise ValueError("agent output binding differs from exact payload bytes")


@dataclass(frozen=True)
class AgentUsage:
    token_status: AgentTokenStatus
    input_tokens: int | None
    output_tokens: int | None
    thinking_tokens: int | None
    total_tokens: int | None
    thinking_included: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.token_status) is not AgentTokenStatus or type(self.thinking_included) is not bool:
            raise TypeError("agent usage fields must be exact")
        for value in (self.input_tokens, self.output_tokens, self.thinking_tokens, self.total_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("agent token counts must be absent or non-negative exact integers")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))


@dataclass(frozen=True)
class AgentReport:
    summary: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        for value in (self.summary, self.failure_reason):
            if value is not None and type(value) is not str:
                raise TypeError("agent report text must be absent or exact strings")


@dataclass(frozen=True)
class AgentDeliveryEvidence:
    invocation_id: str
    context_id: str
    task_sha256: str
    task_byte_length: int
    envelope_sha256: str
    transport_name: str
    process_started: bool
    information_sha256: str | None = None
    information_byte_length: int | None = None

    def __post_init__(self) -> None:
        for name in ("invocation_id", "context_id"):
            _identifier(getattr(self, name), name)
        _digest(self.task_sha256, "task_sha256")
        _digest(self.envelope_sha256, "envelope_sha256")
        if type(self.task_byte_length) is not int or self.task_byte_length <= 0:
            raise ValueError("task byte length must be positive")
        if type(self.transport_name) is not str or not self.transport_name or len(self.transport_name) > 128:
            raise ValueError("transport_name must be bounded and non-empty")
        if type(self.process_started) is not bool:
            raise TypeError("process_started must be an exact bool")
        if self.information_sha256 is None:
            if self.information_byte_length is not None:
                raise ValueError("absent information digest cannot have a byte length")
        else:
            _digest(self.information_sha256, "information_sha256")
            if type(self.information_byte_length) is not int or self.information_byte_length <= 0:
                raise ValueError("information byte length must be positive when present")


@dataclass(frozen=True)
class AgentExecutionResult:
    invocation_id: str
    agent_profile_id: str
    status: AgentExecutionStatus
    outputs: tuple[AgentOutputEnvelope, ...]
    usage: AgentUsage
    diagnostics: Mapping[str, Any]
    report: AgentReport
    delivery_evidence: AgentDeliveryEvidence
    schema_version: str = AGENT_EXECUTION_RESULT_V1

    def __post_init__(self) -> None:
        if self.schema_version != AGENT_EXECUTION_RESULT_V1:
            raise ValueError("agent execution result schema is unknown")
        _identifier(self.invocation_id, "invocation_id")
        _identifier(self.agent_profile_id, "agent_profile_id")
        if type(self.status) is not AgentExecutionStatus:
            raise TypeError("agent execution status must be exact")
        if type(self.outputs) is not tuple or any(type(item) is not AgentOutputEnvelope for item in self.outputs):
            raise TypeError("agent outputs must be exact typed envelopes")
        if type(self.usage) is not AgentUsage or type(self.report) is not AgentReport:
            raise TypeError("agent result nested records must be exact")
        if type(self.delivery_evidence) is not AgentDeliveryEvidence:
            raise TypeError("agent result requires delivery evidence")
        if self.delivery_evidence.invocation_id != self.invocation_id:
            raise ValueError("agent result and delivery evidence differ in invocation identity")
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
