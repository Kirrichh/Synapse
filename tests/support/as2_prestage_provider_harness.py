"""P0.6.30 test-only Host Pre-Stage provider aggregation harness.

This harness assembles Host Pre-Stage payload fragments from injected provider
fakes. It is deliberately test-only: it does not call runtime wiring, does not
retry, and does not perform real I/O.

Architecture Note:
    Test-only harness intentionally performs sequential fail-fast aggregation.

    Future production provider aggregation may use concurrent polling of
    independent ports, but must preserve:
    - deterministic failure classification;
    - explicit ProviderFailure routing to Control Plane;
    - fail-fast semantics (first failure stops aggregation).

    See: docs/AS2-HOST-PROVIDER-PORTS-RFC-P0621.md
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Union

from synapse.runtime.as2_provider_ports import (
    CapabilityGrantProviderPort,
    HostDefinitionProviderPort,
    HostIdentityProviderPort,
    HostProviderRequestContext,
    MemoryReferenceProviderPort,
    ModelSelectionProviderPort,
    ProviderFailure,
    StaticModelRegistryProviderPort,
    is_provider_failure,
)
HostPreStageHarnessResult = Union[Mapping[str, Any], ProviderFailure]


@dataclass(frozen=True)
class HostPreStageProviderHarness:
    """Test-only aggregator for all six AS2 provider ports.

    Invocation order is deterministic and fail-fast:
    Identity -> ModelSelection -> Definition -> StaticModelRegistry ->
    MemoryReference -> CapabilityGrant.
    """

    identity_provider: HostIdentityProviderPort
    model_selection_provider: ModelSelectionProviderPort
    definition_provider: HostDefinitionProviderPort
    registry_provider: StaticModelRegistryProviderPort
    memory_reference_provider: MemoryReferenceProviderPort
    capability_grant_provider: CapabilityGrantProviderPort

    def build_prestage_payload(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> HostPreStageHarnessResult:
        """Assemble Host Pre-Stage payload or return first ProviderFailure."""

        identity = self.identity_provider.get_adapter_identity_context(context, agent_id=agent_id)
        if is_provider_failure(identity):
            return identity

        model_selection = self.model_selection_provider.get_model_selection_source(
            context, agent_id=agent_id
        )
        if is_provider_failure(model_selection):
            return model_selection

        definition = self.definition_provider.get_adapter_definition_source(
            context, agent_id=agent_id
        )
        if is_provider_failure(definition):
            return definition

        registry = self.registry_provider.get_static_model_registry(context)
        if is_provider_failure(registry):
            return registry

        memory = self.memory_reference_provider.get_memory_ref_source(context, agent_id=agent_id)
        if is_provider_failure(memory):
            return memory

        capability = self.capability_grant_provider.get_capability_grant_source(
            context, agent_id=agent_id
        )
        if is_provider_failure(capability):
            return capability

        return {
            "adapter_identity_context": identity.data,
            "model_selection_source": model_selection.data,
            "adapter_definition_source": definition.data,
            "static_model_registry": registry.data,
            "memory_ref_source": memory.data,
            "capability_grant_source": capability.data,
        }


__all__ = ["HostPreStageHarnessResult", "HostPreStageProviderHarness"]
