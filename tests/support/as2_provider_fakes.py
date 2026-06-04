"""P0.6.28 Stage 1 fake providers for AS2 Host Provider Ports.

These fakes implement the Stage 1 provider ports only:
- HostIdentityProviderPort
- ModelSelectionProviderPort

They are deterministic, in-memory, and side-effect free. They intentionally do
not perform network, file, database, or CAS I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from synapse.runtime.as2_provider_ports import (
    HostProviderRequestContext,
    ProviderFailure,
    ProviderOutcome,
    ProviderReasonCode,
    ProviderSuccess,
)


def _missing_context_failure(
    context: HostProviderRequestContext,
    *,
    provider_name: str,
) -> ProviderFailure:
    return ProviderFailure(
        reason_code=ProviderReasonCode.MISSING_REQUEST_CONTEXT,
        detail="HostProviderRequestContext requires correlation_id and request_id",
        correlation_id=context.correlation_id or "<missing>",
        latency_ms=0,
        provider_name=provider_name,
    )


@dataclass(frozen=True)
class FakeHostIdentityProvider:
    """Stage 1 fake for HostIdentityProviderPort."""

    identity_by_agent: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    latency_ms: int = 1

    def get_adapter_identity_context(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="host_identity")
        data = self.identity_by_agent.get(
            agent_id,
            {
                "agent_id": agent_id,
                "tenant_id": "tenant-test",
                "subject": f"subject:{agent_id}",
            },
        )
        return ProviderSuccess(
            data=dict(data),
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class FakeModelSelectionProvider:
    """Stage 1 fake for ModelSelectionProviderPort."""

    model_by_agent: Mapping[str, str] = field(default_factory=dict)
    default_model: str = "gpt-test-model"
    latency_ms: int = 1

    def get_model_selection_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="model_selection")
        model = self.model_by_agent.get(agent_id, self.default_model)
        return ProviderSuccess(
            data={"model": model, "selection_source": "p0628_fake"},
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class FakeHostDefinitionProvider:
    """Stage 2 full fake for HostDefinitionProviderPort."""

    definitions_by_agent: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    schema_mismatch_agents: frozenset[str] = frozenset()
    not_found_agents: frozenset[str] = frozenset()
    latency_ms: int = 1

    def get_adapter_definition_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="host_definition")
        if agent_id in self.schema_mismatch_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.SCHEMA_MISMATCH,
                detail="adapter definition source schema mismatch",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="host_definition",
                context={"agent_id": agent_id},
            )
        if agent_id in self.not_found_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.NOT_FOUND,
                detail="adapter definition source not found",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="host_definition",
                context={"agent_id": agent_id},
            )
        data = self.definitions_by_agent.get(
            agent_id,
            {
                "type": "adapter_definition_source",
                "schema_version": "alpha3g.adapter_definition_source.v1",
                "profile": "stable-canonical.v1",
                "definition_ref": {
                    "type": "agent_definition_ref",
                    "schema_version": "alpha3g.agent_definition_ref.v1",
                    "profile": "stable-canonical.v1",
                    "namespace": "alpha3g.fixture",
                    "class_name": "FixtureAgent",
                    "declared_version": "v1.0.0",
                    "manifest_hash": "sha256:" + "2" * 64,
                    "interface_schema_hash": "sha256:" + "4" * 64,
                    "config_schema_hash": "sha256:" + "5" * 64,
                    "capability_schema_hash": "sha256:" + "6" * 64,
                },
                "config": {"max_steps": 1, "temperature": 0},
                "canonical_fields": {
                    "legacy_name_alias": agent_id,
                    "projection_mode": "as2.provider.stage2",
                },
            },
        )
        return ProviderSuccess(
            data=dict(data),
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class FakeStaticModelRegistryProvider:
    """Stage 2 full fake for StaticModelRegistryProviderPort."""

    registry: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    schema_mismatch: bool = False
    not_found_models: frozenset[str] = frozenset()
    latency_ms: int = 1

    def get_static_model_registry(
        self,
        context: HostProviderRequestContext,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="static_model_registry")
        if self.schema_mismatch:
            return ProviderFailure(
                reason_code=ProviderReasonCode.SCHEMA_MISMATCH,
                detail="static model registry schema mismatch",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="static_model_registry",
            )
        entries: list[Mapping[str, Any]] = []
        source = self.registry or {
            "gpt-stage2": {
                "provider_namespace": "fake",
                "model_version": "v1",
            }
        }
        for model_id, model_config in source.items():
            if model_id in self.not_found_models:
                return ProviderFailure(
                    reason_code=ProviderReasonCode.NOT_FOUND,
                    detail=f"static model registry does not contain requested model: {model_id}",
                    correlation_id=context.correlation_id,
                    latency_ms=self.latency_ms,
                    provider_name="static_model_registry",
                    context={"model": model_id},
                )
            entries.append(
                {
                    "legacy_model": model_id,
                    "model_ref": {
                        "type": "model_ref",
                        "schema_version": "alpha3g.model_ref.v1",
                        "profile": "stable-canonical.v1",
                        "model_id": model_id,
                        "model_version": str(model_config.get("model_version", "v1")),
                        "provider_namespace": str(model_config.get("provider_namespace", "fake")),
                        "capability_profile_hash": str(
                            model_config.get("capability_profile_hash", "sha256:" + "e" * 64)
                        ),
                    },
                }
            )
        return ProviderSuccess(
            data={
                "type": "static_model_registry",
                "schema_version": "alpha3g.static_model_registry.v1",
                "profile": "stable-canonical.v1",
                "registry_snapshot_hash": "sha256:" + "7" * 64,
                "entries": entries,
            },
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class FakeMemoryReferenceProvider:
    """Stage 3 full fake for MemoryReferenceProviderPort.

    Empty memory refs are a valid success state. ``NOT_FOUND`` means the
    provider cannot locate the required provider-side source, not that an
    agent simply has no refs.
    """

    memory_by_agent: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    invalid_input_agents: frozenset[str] = frozenset()
    not_found_agents: frozenset[str] = frozenset()
    cancelled_agents: frozenset[str] = frozenset()
    latency_ms: int = 1

    def get_memory_ref_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="memory_reference")
        if agent_id in self.cancelled_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.CANCELLED,
                detail="memory reference request cancelled",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="memory_reference",
                context={"agent_id": agent_id},
            )
        if agent_id in self.invalid_input_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.INVALID_INPUT,
                detail="memory reference source has invalid structure",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="memory_reference",
                context={"agent_id": agent_id},
            )
        if agent_id in self.not_found_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.NOT_FOUND,
                detail="memory reference source not found",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="memory_reference",
                context={"agent_id": agent_id},
            )
        data = self.memory_by_agent.get(
            agent_id,
            {
                "type": "memory_ref_source",
                "schema_version": "alpha3g.memory_ref_source.v1",
                "profile": "stable-canonical.v1",
                "expected_memory_space_id": "sha256:" + "3" * 64,
                "memory_space_policy_version": "alpha3g.memory_space_policy.v1",
                "refs": [],
            },
        )
        return ProviderSuccess(
            data=dict(data),
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


@dataclass(frozen=True)
class FakeCapabilityGrantProvider:
    """Stage 3 full fake for CapabilityGrantProviderPort.

    Empty grants are a valid success state. ``NOT_FOUND`` means the provider
    cannot locate a required provider-side source, not that an agent has no
    grants.
    """

    grants_by_agent: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    invalid_input_agents: frozenset[str] = frozenset()
    not_found_agents: frozenset[str] = frozenset()
    cancelled_agents: frozenset[str] = frozenset()
    latency_ms: int = 1

    def get_capability_grant_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        if not context.is_valid():
            return _missing_context_failure(context, provider_name="capability_grant")
        if agent_id in self.cancelled_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.CANCELLED,
                detail="capability grant request cancelled",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="capability_grant",
                context={"agent_id": agent_id},
            )
        if agent_id in self.invalid_input_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.INVALID_INPUT,
                detail="capability grant source has invalid structure",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="capability_grant",
                context={"agent_id": agent_id},
            )
        if agent_id in self.not_found_agents:
            return ProviderFailure(
                reason_code=ProviderReasonCode.NOT_FOUND,
                detail="capability grant source not found",
                correlation_id=context.correlation_id,
                latency_ms=self.latency_ms,
                provider_name="capability_grant",
                context={"agent_id": agent_id},
            )
        data = self.grants_by_agent.get(
            agent_id,
            {
                "type": "capability_grant_source",
                "schema_version": "alpha3g.capability_grant_source.v1",
                "profile": "stable-canonical.v1",
                "grants": [],
            },
        )
        return ProviderSuccess(
            data=dict(data),
            correlation_id=context.correlation_id,
            latency_ms=self.latency_ms,
        )


__all__ = [
    "FakeCapabilityGrantProvider",
    "FakeHostDefinitionProvider",
    "FakeHostIdentityProvider",
    "FakeMemoryReferenceProvider",
    "FakeModelSelectionProvider",
    "FakeStaticModelRegistryProvider",
]
