"""Test-only AS2 runtime wiring harness for Alpha3g P0.6.15.

This module defines executable Host Provider contracts for tests only. It does
not define production Host provider APIs, does not perform runtime wiring, does
not call AS2 projection, and does not construct canonical snapshots.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

import synapse.agent_snapshot_bridge as bridge


PRODUCTION_PAYLOAD_KEYS = frozenset(
    {
        "adapter_identity_context",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
        "model_selection_source",
    }
)

TEST_FAILURE_MODELING_KEYS = frozenset(
    {
        "host_stage_failure",
        "forbidden_runtime_reads",
        "inline_memory_payload",
    }
)

COMPATIBILITY_ALIAS_KEYS = frozenset()
DIAGNOSTIC_NOTE_KEYS = frozenset({"notes"})


class SystemicProviderUnavailableError(RuntimeError):
    """A shared Host provider failed before a per-agent AS2 payload existed."""


@runtime_checkable
class IdentityProviderProtocol(Protocol):
    def adapter_identity_context(self) -> Mapping[str, Any]:
        """Return explicit AS2 identity context prepared by Host."""


@runtime_checkable
class DefinitionProviderProtocol(Protocol):
    def adapter_definition_source(self) -> Mapping[str, Any]:
        """Return explicit AS2 definition source prepared by Host."""


@runtime_checkable
class ModelRegistryProviderProtocol(Protocol):
    def static_model_registry(self) -> Mapping[str, Any]:
        """Return a pinned static model registry snapshot."""


@runtime_checkable
class MemoryExternalizationProtocol(Protocol):
    def memory_ref_source(self) -> Mapping[str, Any]:
        """Return externalized memory references, not inline legacy memory."""


@runtime_checkable
class CapabilityGrantManifestProtocol(Protocol):
    def capability_grant_source(self) -> Mapping[str, Any]:
        """Return declarative capability grants, not live callables."""


@runtime_checkable
class ModelSelectionProtocol(Protocol):
    def model_selection_source(self) -> Mapping[str, Any]:
        """Return explicit model selection prepared by Host policy."""


@dataclass(frozen=True)
class MockHostIdentityProvider:
    payload: Mapping[str, Any]

    def adapter_identity_context(self) -> Mapping[str, Any]:
        return copy.deepcopy(dict(self.payload))


@dataclass(frozen=True)
class MockHostDefinitionProvider:
    payload: Mapping[str, Any]

    def adapter_definition_source(self) -> Mapping[str, Any]:
        return copy.deepcopy(dict(self.payload))


@dataclass(frozen=True)
class MockStaticModelRegistryProvider:
    payload: Mapping[str, Any]

    def static_model_registry(self) -> Mapping[str, Any]:
        return copy.deepcopy(dict(self.payload))


@dataclass(frozen=True)
class MockMemoryExternalizationProvider:
    payload: Mapping[str, Any] | None = None
    fail_systemically: bool = False

    def memory_ref_source(self) -> Mapping[str, Any]:
        if self.fail_systemically:
            raise SystemicProviderUnavailableError("memory externalization provider unavailable")
        return copy.deepcopy(dict(self.payload or {}))


@dataclass(frozen=True)
class MockCapabilityGrantManifestProvider:
    payload: Mapping[str, Any]

    def capability_grant_source(self) -> Mapping[str, Any]:
        return copy.deepcopy(dict(self.payload))


@dataclass(frozen=True)
class MockModelSelectionProvider:
    payload: Mapping[str, Any]

    def model_selection_source(self) -> Mapping[str, Any]:
        return copy.deepcopy(dict(self.payload))


@dataclass(frozen=True)
class MockHostPrestagePayloadBuilder:
    identity_provider: IdentityProviderProtocol
    definition_provider: DefinitionProviderProtocol
    model_registry_provider: ModelRegistryProviderProtocol
    memory_provider: MemoryExternalizationProtocol
    capability_provider: CapabilityGrantManifestProtocol
    model_selection_provider: ModelSelectionProtocol

    def assemble_payload(self) -> dict[str, Any]:
        """Assemble a production Host Pre-Stage payload from provider ports only."""

        payload = {
            "adapter_identity_context": self.identity_provider.adapter_identity_context(),
            "adapter_definition_source": self.definition_provider.adapter_definition_source(),
            "static_model_registry": self.model_registry_provider.static_model_registry(),
            "memory_ref_source": self.memory_provider.memory_ref_source(),
            "capability_grant_source": self.capability_provider.capability_grant_source(),
            "model_selection_source": self.model_selection_provider.model_selection_source(),
        }
        unexpected = set(payload) - PRODUCTION_PAYLOAD_KEYS
        if unexpected:
            raise AssertionError(f"builder emitted non-production payload keys: {sorted(unexpected)}")
        return payload


class HarnessOutcome(str, Enum):
    PREPARED = "prepared"
    QUARANTINE_AGENT = "quarantine_agent"
    DISABLE_WIRING_GLOBALLY = "disable_wiring_globally"


@dataclass(frozen=True)
class HarnessAgentResult:
    agent_id: str
    outcome: HarnessOutcome
    prepared: bridge.PreparedAS2Inputs | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class HarnessBatchResult:
    global_outcome: HarnessOutcome
    agent_results: Mapping[str, HarnessAgentResult]
    systemic_error_type: str | None = None


PrepareFunction = Callable[[Mapping[str, Any]], bridge.PreparedAS2Inputs]


def run_p0615_harness_batch(
    builders_by_agent: Mapping[str, MockHostPrestagePayloadBuilder],
    *,
    prepare: PrepareFunction,
) -> HarnessBatchResult:
    """Simulate approved P0.6.15 failure policy without mutating runtime state."""

    agent_results: dict[str, HarnessAgentResult] = {}
    for agent_id, builder in builders_by_agent.items():
        try:
            payload = builder.assemble_payload()
            prepared = prepare(payload)
        except SystemicProviderUnavailableError as exc:
            return HarnessBatchResult(
                global_outcome=HarnessOutcome.DISABLE_WIRING_GLOBALLY,
                agent_results={},
                systemic_error_type=exc.__class__.__name__,
            )
        except bridge.AS2BridgeInputError as exc:
            agent_results[agent_id] = HarnessAgentResult(
                agent_id=agent_id,
                outcome=HarnessOutcome.QUARANTINE_AGENT,
                error_type=exc.__class__.__name__,
            )
        else:
            agent_results[agent_id] = HarnessAgentResult(
                agent_id=agent_id,
                outcome=HarnessOutcome.PREPARED,
                prepared=prepared,
            )
    return HarnessBatchResult(global_outcome=HarnessOutcome.PREPARED, agent_results=agent_results)
