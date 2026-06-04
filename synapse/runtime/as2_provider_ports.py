"""AS2 Host Provider Ports skeleton interfaces (Alpha3g P0.6.28).

This module materializes the P0.6.21 Host Provider Ports RFC as
production-facing contracts. It defines the six AS2 provider ports, a required
request context, and result-style provider outcomes.

Explicit non-goals for P0.6.28:
- no concrete provider adapters;
- no network/file/database/CAS I/O;
- no AgentRuntime or Environment coupling;
- no projection calls or AgentSnapshot construction;
- no retries or provider-side orchestration.

Provider failures are values, not control-flow exceptions. Callers route
``ProviderFailure.reason_code`` to the AS2 control plane; provider ports do not
mutate gate state and do not retry implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Mapping, Protocol, TypeGuard, TypeVar, Union, runtime_checkable

T = TypeVar("T")


class AS2ProviderName(str, Enum):
    """Canonical AS2 provider names for deterministic aggregation decisions."""

    IDENTITY = "identity"
    MODEL_SELECTION = "model_selection"
    DEFINITION = "definition"
    STATIC_MODEL_REGISTRY = "static_model_registry"
    MEMORY_REFERENCE = "memory_reference"
    CAPABILITY_GRANT = "capability_grant"


@dataclass(frozen=True)
class HostProviderRequestContext:
    """Explicit request context required by every AS2 Host Provider Port.

    ``correlation_id`` and ``request_id`` are required. P0.6.28 does not allow
    fallback generation inside provider ports. ``timestamp`` is accepted only as
    caller-supplied context; provider ports must not depend on wall-clock time.
    """

    correlation_id: str
    request_id: str
    timestamp: str | None = None
    security_context: Mapping[str, Any] | None = None

    def is_valid(self) -> bool:
        """Return whether required context fields are present."""

        return bool(self.correlation_id and self.request_id)


class ProviderReasonCode(str, Enum):
    """Stable ProviderFailure taxonomy from the AS2 Provider Ports RFC."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BACKPRESSURE_REJECTED = "backpressure_rejected"
    MISSING_REQUEST_CONTEXT = "missing_request_context"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    SCHEMA_MISMATCH = "schema_mismatch"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProviderSuccess(Generic[T]):
    """Successful provider outcome with required tracing and timing metadata."""

    data: T
    correlation_id: str
    latency_ms: int

    @property
    def is_success(self) -> bool:
        return True


@dataclass(frozen=True)
class ProviderFailure:
    """Expected provider failure represented as a value, not an exception."""

    reason_code: ProviderReasonCode
    detail: str
    correlation_id: str
    latency_ms: int
    provider_name: AS2ProviderName | str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return False


ProviderOutcome = Union[ProviderSuccess[T], ProviderFailure]


def is_provider_success(outcome: ProviderOutcome[T]) -> TypeGuard[ProviderSuccess[T]]:
    """Return whether ``outcome`` is a ProviderSuccess, narrowing its type."""

    return outcome.is_success


def is_provider_failure(outcome: ProviderOutcome[T]) -> TypeGuard[ProviderFailure]:
    """Return whether ``outcome`` is a ProviderFailure, narrowing its type."""

    return not outcome.is_success



@runtime_checkable
class HostIdentityProviderPort(Protocol):
    """Provides ``adapter_identity_context`` for Host Pre-Stage assembly."""

    def get_adapter_identity_context(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return canonical identity context or a typed provider failure."""


@runtime_checkable
class HostDefinitionProviderPort(Protocol):
    """Provides ``adapter_definition_source`` for Host Pre-Stage assembly."""

    def get_adapter_definition_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return adapter definition source or a typed provider failure."""


@runtime_checkable
class StaticModelRegistryProviderPort(Protocol):
    """Provides ``static_model_registry`` for Host Pre-Stage assembly."""

    def get_static_model_registry(
        self,
        context: HostProviderRequestContext,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return static model registry or a typed provider failure."""


@runtime_checkable
class MemoryReferenceProviderPort(Protocol):
    """Provides ``memory_ref_source`` for Host Pre-Stage assembly."""

    def get_memory_ref_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return externalized memory references or a typed provider failure."""


@runtime_checkable
class CapabilityGrantProviderPort(Protocol):
    """Provides ``capability_grant_source`` for Host Pre-Stage assembly."""

    def get_capability_grant_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return capability grants or a typed provider failure."""


@runtime_checkable
class ModelSelectionProviderPort(Protocol):
    """Provides ``model_selection_source`` for Host Pre-Stage assembly."""

    def get_model_selection_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        """Return canonical model selection source or typed provider failure."""


__all__ = [
    "AS2ProviderName",
    "CapabilityGrantProviderPort",
    "HostDefinitionProviderPort",
    "HostIdentityProviderPort",
    "HostProviderRequestContext",
    "MemoryReferenceProviderPort",
    "ModelSelectionProviderPort",
    "ProviderFailure",
    "ProviderOutcome",
    "is_provider_failure",
    "is_provider_success",
    "ProviderReasonCode",
    "ProviderSuccess",
    "StaticModelRegistryProviderPort",
]
