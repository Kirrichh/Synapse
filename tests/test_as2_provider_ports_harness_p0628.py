"""P0.6.28 Host Provider Ports Harness + Skeleton Interfaces tests."""
from __future__ import annotations

import pytest

from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecisionKind,
)
from synapse.runtime.as2_provider_ports import (
    CapabilityGrantProviderPort,
    HostDefinitionProviderPort,
    HostIdentityProviderPort,
    HostProviderRequestContext,
    MemoryReferenceProviderPort,
    ModelSelectionProviderPort,
    ProviderFailure,
    ProviderReasonCode,
    ProviderSuccess,
    StaticModelRegistryProviderPort,
)
from synapse.runtime.as2_projection_handoff import AS2ProjectionHandoffResultKind
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState
from tests.support.as2_provider_fakes import FakeHostIdentityProvider, FakeModelSelectionProvider
from tests.support.as2_provider_routing import route_provider_failure_to_controller


def _context() -> HostProviderRequestContext:
    return HostProviderRequestContext(
        correlation_id="corr.p0628.provider",
        request_id="req.p0628.provider",
        timestamp="2026-06-02T00:00:00Z",
    )



def test_p0628_all_six_provider_port_interfaces_are_materialized() -> None:
    """P0.6.28 materializes the full P0.6.21 RFC interface surface."""

    assert HostIdentityProviderPort is not None
    assert HostDefinitionProviderPort is not None
    assert StaticModelRegistryProviderPort is not None
    assert MemoryReferenceProviderPort is not None
    assert CapabilityGrantProviderPort is not None
    assert ModelSelectionProviderPort is not None


@pytest.mark.parametrize(
    "reason_code",
    [
        ProviderReasonCode.TIMEOUT,
        ProviderReasonCode.UNAVAILABLE,
        ProviderReasonCode.UNAUTHORIZED,
        ProviderReasonCode.FORBIDDEN,
        ProviderReasonCode.MISSING_REQUEST_CONTEXT,
        ProviderReasonCode.SCHEMA_MISMATCH,
        ProviderReasonCode.BACKPRESSURE_REJECTED,
        ProviderReasonCode.INVALID_INPUT,
        ProviderReasonCode.NOT_FOUND,
        ProviderReasonCode.CANCELLED,
    ],
)
def test_p0628_provider_reason_code_taxonomy_is_stable(reason_code: ProviderReasonCode) -> None:
    assert isinstance(reason_code.value, str)
    assert reason_code.value


def test_p0628_stage1_identity_provider_success() -> None:
    provider = FakeHostIdentityProvider(
        identity_by_agent={"agent-42": {"agent_id": "agent-42", "tenant_id": "tenant-a"}}
    )

    outcome = provider.get_adapter_identity_context(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.correlation_id == "corr.p0628.provider"
    assert outcome.latency_ms == 1
    assert outcome.data["agent_id"] == "agent-42"
    assert outcome.data["tenant_id"] == "tenant-a"


def test_p0628_stage1_model_selection_provider_success() -> None:
    provider = FakeModelSelectionProvider(model_by_agent={"agent-42": "gpt-stage1"})

    outcome = provider.get_model_selection_source(_context(), agent_id="agent-42")

    assert isinstance(outcome, ProviderSuccess)
    assert outcome.correlation_id == "corr.p0628.provider"
    assert outcome.data == {"model": "gpt-stage1", "selection_source": "p0628_fake"}


def test_p0628_missing_context_is_provider_failure_not_exception() -> None:
    provider = FakeHostIdentityProvider()
    missing = HostProviderRequestContext(correlation_id="", request_id="")

    outcome = provider.get_adapter_identity_context(missing, agent_id="agent-42")

    assert isinstance(outcome, ProviderFailure)
    assert outcome.reason_code is ProviderReasonCode.MISSING_REQUEST_CONTEXT
    assert outcome.provider_name == "host_identity"
    assert outcome.correlation_id == "<missing>"


def test_p0628_provider_failure_routes_to_control_plane_systemic_disable() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.MISSING_REQUEST_CONTEXT,
        detail="caller omitted context",
        correlation_id="corr.p0628.missing-context",
        latency_ms=0,
        provider_name="host_identity",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0628_provider_timeout_threshold_routes_through_control_plane() -> None:
    controller = AS2GateControllerSkeleton(
        initial_state=AS2WiringGateState.ENABLED_FOR_TEST,
        provider_failure_threshold=2,
    )
    first = ProviderFailure(
        reason_code=ProviderReasonCode.TIMEOUT,
        detail="timeout one",
        correlation_id="corr.p0628.timeout.1",
        latency_ms=100,
        provider_name="model_selection",
    )
    second = ProviderFailure(
        reason_code=ProviderReasonCode.TIMEOUT,
        detail="timeout two",
        correlation_id="corr.p0628.timeout.2",
        latency_ms=100,
        provider_name="model_selection",
    )

    first_decision = route_provider_failure_to_controller(controller, first)
    second_decision = route_provider_failure_to_controller(controller, second)

    assert first_decision.kind is AS2GateDecisionKind.OBSERVE
    assert second_decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE


def test_p0628_unauthorized_provider_failure_routes_to_operator_review() -> None:
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    failure = ProviderFailure(
        reason_code=ProviderReasonCode.UNAUTHORIZED,
        detail="token rejected",
        correlation_id="corr.p0628.unauthorized",
        latency_ms=3,
        provider_name="host_identity",
    )

    decision = route_provider_failure_to_controller(controller, failure)

    assert decision.kind is AS2GateDecisionKind.OPERATOR_REVIEW
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST


def test_p0628_provider_failure_path_is_distinct_from_projection_failure_path() -> None:
    """Provider failures route to Control Plane, projection failures stay handoff results."""

    provider_failure = ProviderFailure(
        reason_code=ProviderReasonCode.TIMEOUT,
        detail="provider timeout",
        correlation_id="corr.p0628.path-separation",
        latency_ms=50,
        provider_name="model_selection",
    )
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = route_provider_failure_to_controller(controller, provider_failure)

    assert decision.kind in {AS2GateDecisionKind.OBSERVE, AS2GateDecisionKind.SYSTEMIC_DISABLE}
    assert AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE.value == "projection_systemic_failure"
    assert provider_failure.reason_code is ProviderReasonCode.TIMEOUT


def test_p0628_stage1_fakes_are_runtime_checkable_ports() -> None:
    identity = FakeHostIdentityProvider()
    model_selection = FakeModelSelectionProvider()

    assert isinstance(identity, HostIdentityProviderPort)
    assert isinstance(model_selection, ModelSelectionProviderPort)
