"""P0.6.30 provider integration hardening tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.runtime.as2_provider_ports import HostProviderRequestContext, ProviderFailure, ProviderOutcome, ProviderReasonCode, ProviderSuccess
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState, WiringSuccess, process_host_prestage
from tests.support.as2_prestage_provider_harness import HostPreStageProviderHarness
from tests.support.as2_provider_fakes import (
    FakeCapabilityGrantProvider,
    FakeHostDefinitionProvider,
    FakeHostIdentityProvider,
    FakeMemoryReferenceProvider,
    FakeModelSelectionProvider,
    FakeStaticModelRegistryProvider,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _context() -> HostProviderRequestContext:
    return HostProviderRequestContext(
        correlation_id="corr.p0630.integration",
        request_id="req.p0630.integration",
        timestamp="2026-06-02T00:00:00Z",
    )


def _fixture_inputs() -> Mapping[str, Any]:
    return json.loads(
        (PROJECT_ROOT / "tests/fixtures/as2/positive_minimal_valid_projection_inputs.json").read_text(
            encoding="utf-8"
        )
    )["inputs"]


class FixtureModelSelectionProvider:
    """Test helper that returns bridge-valid model_selection_source shape."""

    def get_model_selection_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        return ProviderSuccess(
            data={"model": "mock-agent-model"},
            correlation_id=context.correlation_id,
            latency_ms=1,
        )


def _full_harness(*, memory_provider=None, capability_provider=None) -> HostPreStageProviderHarness:
    inputs = _fixture_inputs()
    return HostPreStageProviderHarness(
        identity_provider=FakeHostIdentityProvider(
            identity_by_agent={"fixture-agent": inputs["adapter_identity_context"]}
        ),
        model_selection_provider=FixtureModelSelectionProvider(),
        definition_provider=FakeHostDefinitionProvider(
            definitions_by_agent={"fixture-agent": inputs["adapter_definition_source"]}
        ),
        registry_provider=FakeStaticModelRegistryProvider(
            registry={
                "mock-agent-model": {
                    "provider_namespace": "mock",
                    "model_version": "v1",
                    "capability_profile_hash": "sha256:" + "e" * 64,
                }
            }
        ),
        memory_reference_provider=memory_provider
        or FakeMemoryReferenceProvider(memory_by_agent={"fixture-agent": inputs["memory_ref_source"]}),
        capability_grant_provider=capability_provider
        or FakeCapabilityGrantProvider(grants_by_agent={"fixture-agent": inputs["capability_grant_source"]}),
    )


def test_p0630_full_six_provider_harness_assembles_complete_payload() -> None:
    payload = _full_harness().build_prestage_payload(_context(), agent_id="fixture-agent")

    assert not isinstance(payload, ProviderFailure)
    assert set(payload) == {
        "adapter_identity_context",
        "model_selection_source",
        "adapter_definition_source",
        "static_model_registry",
        "memory_ref_source",
        "capability_grant_source",
    }
    assert payload["memory_ref_source"]["refs"] == []
    assert payload["capability_grant_source"]["grants"]


class CountingCapabilityProvider:
    """Test helper that records whether capability grants were requested."""

    def __init__(self) -> None:
        self.calls = 0

    def get_capability_grant_source(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
    ) -> ProviderOutcome[Mapping[str, Any]]:
        self.calls += 1
        return ProviderSuccess(
            data={
                "type": "capability_grant_source",
                "schema_version": "alpha3g.capability_grant_source.v1",
                "profile": "stable-canonical.v1",
                "grants": [],
            },
            correlation_id=context.correlation_id,
            latency_ms=1,
        )


def test_p0630_memory_failure_stops_before_capability_provider_call() -> None:
    capability = CountingCapabilityProvider()
    harness = _full_harness(
        memory_provider=FakeMemoryReferenceProvider(invalid_input_agents=frozenset({"fixture-agent"})),
        capability_provider=capability,
    )

    result = harness.build_prestage_payload(_context(), agent_id="fixture-agent")

    assert isinstance(result, ProviderFailure)
    assert result.reason_code is ProviderReasonCode.INVALID_INPUT
    assert result.provider_name == "memory_reference"
    assert capability.calls == 0


def test_p0630_capability_failure_returns_provider_failure_value() -> None:
    result = _full_harness(
        capability_provider=FakeCapabilityGrantProvider(cancelled_agents=frozenset({"fixture-agent"}))
    ).build_prestage_payload(_context(), agent_id="fixture-agent")

    assert isinstance(result, ProviderFailure)
    assert result.reason_code is ProviderReasonCode.CANCELLED
    assert result.provider_name == "capability_grant"


def test_p0630_full_provider_payload_is_accepted_by_runtime_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)
    payload = _full_harness().build_prestage_payload(_context(), agent_id="fixture-agent")
    assert not isinstance(payload, ProviderFailure)

    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.p0630.integration",
    )

    assert isinstance(outcome, WiringSuccess)
    assert outcome.correlation_id == "corr.p0630.integration"
