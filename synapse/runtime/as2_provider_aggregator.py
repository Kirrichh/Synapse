"""AS2 Production Provider Aggregator skeleton (Alpha3g P0.6.37).

This module materializes the production-facing provider aggregation skeleton
under ENABLED_FOR_TEST. It is an Anti-Corruption Layer between provider-native
outputs and the bridge-valid Host Pre-Stage payload contract.

Explicit non-goals for P0.6.37:
- no concrete provider adapters;
- no network/file/database/CAS/queue I/O;
- no runtime wiring changes;
- no projection calls or canonical snapshot construction;
- no idempotency store coupling;
- no audit relay or audit storage writes;
- no concurrent execution implementation;
- no production ENABLED activation.

The runtime aggregation path is deterministic sequential fail-fast. The Failure
Priority Matrix is implemented as a pure selector for future concurrent
aggregation and is tested independently from ``aggregate``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Sequence, Union

from synapse.runtime.as2_provider_ports import (
    AS2ProviderName,
    CapabilityGrantProviderPort,
    HostDefinitionProviderPort,
    HostIdentityProviderPort,
    HostProviderRequestContext,
    MemoryReferenceProviderPort,
    ModelSelectionProviderPort,
    ProviderFailure,
    ProviderOutcome,
    ProviderReasonCode,
    StaticModelRegistryProviderPort,
    is_provider_failure,
)

FailureScope = Literal["agent", "systemic"]

BRIDGE_PAYLOAD_KEYS: tuple[str, ...] = (
    "adapter_identity_context",
    "model_selection_source",
    "adapter_definition_source",
    "static_model_registry",
    "memory_ref_source",
    "capability_grant_source",
)

PROVIDER_ORDER: tuple[AS2ProviderName, ...] = (
    AS2ProviderName.IDENTITY,
    AS2ProviderName.MODEL_SELECTION,
    AS2ProviderName.DEFINITION,
    AS2ProviderName.STATIC_MODEL_REGISTRY,
    AS2ProviderName.MEMORY_REFERENCE,
    AS2ProviderName.CAPABILITY_GRANT,
)

FAILURE_PRIORITY: Mapping[ProviderReasonCode, int] = {
    ProviderReasonCode.MISSING_REQUEST_CONTEXT: 1,
    ProviderReasonCode.UNAUTHORIZED: 2,
    ProviderReasonCode.FORBIDDEN: 2,
    ProviderReasonCode.SCHEMA_MISMATCH: 3,
    ProviderReasonCode.INVALID_INPUT: 3,
    ProviderReasonCode.NOT_FOUND: 4,
    ProviderReasonCode.TIMEOUT: 5,
    ProviderReasonCode.UNAVAILABLE: 5,
    ProviderReasonCode.BACKPRESSURE_REJECTED: 5,
    ProviderReasonCode.CANCELLED: 6,
}

_PROVIDER_OUTPUT_KEYS: Mapping[AS2ProviderName, str] = {
    AS2ProviderName.IDENTITY: "adapter_identity_context",
    AS2ProviderName.MODEL_SELECTION: "model_selection_source",
    AS2ProviderName.DEFINITION: "adapter_definition_source",
    AS2ProviderName.STATIC_MODEL_REGISTRY: "static_model_registry",
    AS2ProviderName.MEMORY_REFERENCE: "memory_ref_source",
    AS2ProviderName.CAPABILITY_GRANT: "capability_grant_source",
}

_ALLOWED_FRAGMENT_KEYS: Mapping[AS2ProviderName, frozenset[str]] = {
    AS2ProviderName.IDENTITY: frozenset({"identity_seed", "schema_version", "profile"}),
    AS2ProviderName.MODEL_SELECTION: frozenset({"model"}),
    AS2ProviderName.DEFINITION: frozenset(
        {"type", "schema_version", "profile", "definition_ref", "config", "canonical_fields"}
    ),
    AS2ProviderName.STATIC_MODEL_REGISTRY: frozenset(
        {"type", "schema_version", "profile", "registry_snapshot_hash", "entries"}
    ),
    AS2ProviderName.MEMORY_REFERENCE: frozenset(
        {
            "type",
            "schema_version",
            "profile",
            "expected_memory_space_id",
            "memory_space_policy_version",
            "refs",
        }
    ),
    AS2ProviderName.CAPABILITY_GRANT: frozenset(
        {"type", "schema_version", "profile", "grants"}
    ),
}

_REQUIRED_FRAGMENT_KEYS: Mapping[AS2ProviderName, frozenset[str]] = {
    AS2ProviderName.IDENTITY: frozenset({"schema_version"}),
    AS2ProviderName.MODEL_SELECTION: frozenset({"model"}),
    AS2ProviderName.DEFINITION: frozenset({"definition_ref"}),
    AS2ProviderName.STATIC_MODEL_REGISTRY: frozenset({"entries"}),
    AS2ProviderName.MEMORY_REFERENCE: frozenset({"refs"}),
    AS2ProviderName.CAPABILITY_GRANT: frozenset({"grants"}),
}

ProviderPort = Union[
    HostIdentityProviderPort,
    ModelSelectionProviderPort,
    HostDefinitionProviderPort,
    StaticModelRegistryProviderPort,
    MemoryReferenceProviderPort,
    CapabilityGrantProviderPort,
]


@dataclass(frozen=True)
class AggregatorSuccess:
    """Successful provider aggregation with a bridge-valid Host Pre-Stage payload."""

    payload: Mapping[str, Any]


@dataclass(frozen=True)
class AggregatorFailure:
    """Provider aggregation failure with explicit failure scope classification."""

    failure: ProviderFailure
    scope: FailureScope


AggregationResult = Union[AggregatorSuccess, AggregatorFailure]


def _provider_name_sort_value(provider_name: AS2ProviderName | str | None) -> str:
    if isinstance(provider_name, AS2ProviderName):
        return provider_name.value
    if provider_name is None:
        return ""
    return str(provider_name)


def select_representative_failure(failures: Sequence[ProviderFailure]) -> ProviderFailure:
    """Return the deterministic representative failure for future concurrent mode.

    Selection is independent of input order: lower Failure Priority Matrix rank
    wins, and equal-priority failures are resolved by lexicographic provider
    name ordering. Runtime ``aggregate`` remains sequential fail-fast in
    P0.6.37 and does not collect failures from remaining ports.
    """

    if not failures:
        raise ValueError("select_representative_failure requires at least one failure")
    return min(
        failures,
        key=lambda failure: (
            FAILURE_PRIORITY.get(failure.reason_code, 99),
            _provider_name_sort_value(failure.provider_name),
        ),
    )


def classify_failure_scope(failure: ProviderFailure) -> FailureScope:
    """Classify INVALID_INPUT with explicit agent-scoped evidence as agent-local."""

    if (
        failure.reason_code == ProviderReasonCode.INVALID_INPUT
        and bool(failure.context.get("agent_id"))
        and failure.context.get("agent_scoped") is True
    ):
        return "agent"
    return "systemic"


def _schema_failure(
    context: HostProviderRequestContext,
    *,
    provider_name: AS2ProviderName,
    detail: str,
    agent_id: str,
) -> ProviderFailure:
    return ProviderFailure(
        reason_code=ProviderReasonCode.SCHEMA_MISMATCH,
        detail=detail,
        correlation_id=context.correlation_id,
        latency_ms=0,
        provider_name=provider_name,
        context={"agent_id": agent_id, "provider_name": provider_name.value},
    )


def normalize_provider_fragment(
    provider_name: AS2ProviderName,
    data: Mapping[str, Any],
    *,
    context: HostProviderRequestContext,
    agent_id: str,
) -> Mapping[str, Any] | ProviderFailure:
    """Validate and strip one provider-native fragment to its bridge contract."""

    required = _REQUIRED_FRAGMENT_KEYS[provider_name]
    missing = sorted(key for key in required if key not in data)
    if missing:
        return _schema_failure(
            context,
            provider_name=provider_name,
            detail=f"{provider_name.value} output missing required fields: {missing}",
            agent_id=agent_id,
        )

    allowed = _ALLOWED_FRAGMENT_KEYS[provider_name]
    return {key: data[key] for key in data if key in allowed}


class AS2ProviderAggregatorSkeleton:
    """Sequential fail-fast production-facing provider aggregator skeleton.

    Providers are injected as AS2 provider port protocol implementations. The
    aggregator calls them in canonical order and stops on the first provider or
    normalization failure. It does not call runtime wiring, projection handoff,
    idempotency store, audit storage, or real I/O.
    """

    def __init__(self, providers: Mapping[AS2ProviderName, ProviderPort] | None = None) -> None:
        self._providers = dict(providers) if providers is not None else {}

    def aggregate(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> AggregationResult:
        """Build a bridge-valid Host Pre-Stage payload or return first failure."""

        payload: dict[str, Any] = {}
        for provider_name in PROVIDER_ORDER:
            provider = self._providers.get(provider_name)
            if provider is None:
                failure = _schema_failure(
                    context,
                    provider_name=provider_name,
                    detail=f"required provider is not configured: {provider_name.value}",
                    agent_id=agent_id,
                )
                return AggregatorFailure(failure=failure, scope=classify_failure_scope(failure))

            outcome = self._call_provider(provider_name, provider, context, agent_id=agent_id)
            if is_provider_failure(outcome):
                return AggregatorFailure(failure=outcome, scope=classify_failure_scope(outcome))

            normalized = normalize_provider_fragment(
                provider_name,
                outcome.data,
                context=context,
                agent_id=agent_id,
            )
            if isinstance(normalized, ProviderFailure):
                return AggregatorFailure(
                    failure=normalized,
                    scope=classify_failure_scope(normalized),
                )
            payload[_PROVIDER_OUTPUT_KEYS[provider_name]] = dict(normalized)

        return AggregatorSuccess(payload=payload)

    def _call_provider(
        self,
        provider_name: AS2ProviderName,
        provider: ProviderPort,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        calls: Mapping[
            AS2ProviderName,
            Callable[[ProviderPort, HostProviderRequestContext, str], ProviderOutcome[Mapping[str, Any]]],
        ] = {
            AS2ProviderName.IDENTITY: self._call_identity,
            AS2ProviderName.MODEL_SELECTION: self._call_model_selection,
            AS2ProviderName.DEFINITION: self._call_definition,
            AS2ProviderName.STATIC_MODEL_REGISTRY: self._call_registry,
            AS2ProviderName.MEMORY_REFERENCE: self._call_memory,
            AS2ProviderName.CAPABILITY_GRANT: self._call_capability,
        }
        return calls[provider_name](provider, context, agent_id)

    @staticmethod
    def _call_identity(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return provider.get_adapter_identity_context(context, agent_id=agent_id)  # type: ignore[union-attr]

    @staticmethod
    def _call_model_selection(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return provider.get_model_selection_source(context, agent_id=agent_id)  # type: ignore[union-attr]

    @staticmethod
    def _call_definition(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return provider.get_adapter_definition_source(context, agent_id=agent_id)  # type: ignore[union-attr]

    @staticmethod
    def _call_registry(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        del agent_id
        return provider.get_static_model_registry(context)  # type: ignore[union-attr]

    @staticmethod
    def _call_memory(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return provider.get_memory_ref_source(context, agent_id=agent_id)  # type: ignore[union-attr]

    @staticmethod
    def _call_capability(
        provider: ProviderPort,
        context: HostProviderRequestContext,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return provider.get_capability_grant_source(context, agent_id=agent_id)  # type: ignore[union-attr]


__all__ = [
    "AS2ProviderAggregatorSkeleton",
    "AggregationResult",
    "AggregatorFailure",
    "AggregatorSuccess",
    "BRIDGE_PAYLOAD_KEYS",
    "FAILURE_PRIORITY",
    "PROVIDER_ORDER",
    "classify_failure_scope",
    "normalize_provider_fragment",
    "select_representative_failure",
]
