"""P0.6.25 production AS2GateController skeleton tests."""
from __future__ import annotations

import pytest

from synapse.runtime.as2_audit_sink import AS2AuditEvent, AS2AuditSink
from synapse.runtime.as2_gate_controller import (
    AS2GateControllerSkeleton,
    AS2GateDecisionKind,
    AS2GateReasonCode,
    AS2ProviderFailureReasonCode,
)
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    AS2WiringReasonCode,
    WiringAgentQuarantineRequest,
    WiringBridgeDisabled,
    WiringSystemicDisableRequest,
)


class CaptureSink:
    def __init__(self) -> None:
        self.events: list[AS2AuditEvent] = []

    def record(self, event: AS2AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
def capture_sink() -> CaptureSink:
    return CaptureSink()


@pytest.fixture
def controller(capture_sink: AS2AuditSink) -> AS2GateControllerSkeleton:
    return AS2GateControllerSkeleton(audit_sink=capture_sink)


def test_p0625_controller_default_state_is_disabled_by_default(controller: AS2GateControllerSkeleton) -> None:
    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT


def test_p0625_systemic_wiring_outcome_transitions_to_disabled_systemic(
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    outcome = WiringSystemicDisableRequest(
        correlation_id="corr-systemic",
        reason="provider unavailable",
        reason_code=AS2WiringReasonCode.PROVIDER_UNAVAILABLE,
        failure_context={"provider": "memory"},
    )

    decision = controller.handle_wiring_outcome(outcome, request_id="req-systemic")

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert decision.reason_code is AS2GateReasonCode.WIRING_SYSTEMIC_DISABLE_REQUESTED
    assert decision.gate_state is AS2WiringGateState.DISABLED_SYSTEMIC
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC
    assert capture_sink.events[-1].event_type == AS2GateDecisionKind.SYSTEMIC_DISABLE.value
    assert capture_sink.events[-1].previous_state_hash is None


def test_p0625_agent_quarantine_wiring_outcome_transitions_to_agent_quarantine(
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    outcome = WiringAgentQuarantineRequest(
        correlation_id="corr-agent",
        agent_id="agent-007",
        reason="bad identity",
        reason_code=AS2WiringReasonCode.MISSING_IDENTITY_CONTEXT,
    )

    decision = controller.handle_wiring_outcome(outcome)

    assert decision.kind is AS2GateDecisionKind.AGENT_QUARANTINE
    assert decision.reason_code is AS2GateReasonCode.WIRING_AGENT_QUARANTINE_REQUESTED
    assert decision.agent_id == "agent-007"
    assert controller.state is AS2WiringGateState.DISABLED_AGENT_QUARANTINE
    assert capture_sink.events[-1].agent_id == "agent-007"


def test_p0625_wiring_bridge_disabled_is_config_event_without_gate_transition(
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    outcome = WiringBridgeDisabled(correlation_id="corr-bridge")

    decision = controller.handle_wiring_outcome(outcome)

    assert decision.kind is AS2GateDecisionKind.OPERATOR_REVIEW
    assert decision.reason_code is AS2GateReasonCode.WIRING_BRIDGE_DISABLED
    assert decision.gate_state is AS2WiringGateState.DISABLED_BY_DEFAULT
    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT
    assert capture_sink.events[-1].from_state == AS2WiringGateState.DISABLED_BY_DEFAULT.value
    assert capture_sink.events[-1].to_state == AS2WiringGateState.DISABLED_BY_DEFAULT.value


def test_p0625_provider_timeout_observes_until_threshold(capture_sink: CaptureSink) -> None:
    controller = AS2GateControllerSkeleton(audit_sink=capture_sink, provider_failure_threshold=2)

    first = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr-timeout-1",
        provider_name="memory",
    )
    second = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr-timeout-2",
        provider_name="memory",
    )

    assert first.kind is AS2GateDecisionKind.OBSERVE
    assert first.reason_code is AS2GateReasonCode.PROVIDER_TIMEOUT_OBSERVED
    assert second.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert second.reason_code is AS2GateReasonCode.PROVIDER_TIMEOUT_THRESHOLD
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC
    assert capture_sink.events[1].previous_state_hash == capture_sink.events[0].record_hash()


def test_p0625_missing_request_context_is_systemic_contract_violation(
    controller: AS2GateControllerSkeleton,
) -> None:
    decision = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT,
        correlation_id="corr-context",
        provider_name="identity",
    )

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert decision.reason_code is AS2GateReasonCode.MISSING_REQUEST_CONTEXT
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0625_schema_mismatch_is_systemic_contract_failure(
    controller: AS2GateControllerSkeleton,
) -> None:
    decision = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.SCHEMA_MISMATCH,
        correlation_id="corr-schema",
        provider_name="model-registry",
    )

    assert decision.kind is AS2GateDecisionKind.SYSTEMIC_DISABLE
    assert decision.reason_code is AS2GateReasonCode.SCHEMA_MISMATCH


def test_p0625_unauthorized_routes_to_operator_review_without_transition(
    controller: AS2GateControllerSkeleton,
) -> None:
    decision = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.UNAUTHORIZED,
        correlation_id="corr-auth",
        provider_name="capability-grants",
    )

    assert decision.kind is AS2GateDecisionKind.OPERATOR_REVIEW
    assert decision.reason_code is AS2GateReasonCode.UNAUTHORIZED
    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT


def test_p0625_backpressure_is_observed_not_transitioned(controller: AS2GateControllerSkeleton) -> None:
    decision = controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.BACKPRESSURE_REJECTED,
        correlation_id="corr-backpressure",
        provider_name="memory",
    )

    assert decision.kind is AS2GateDecisionKind.OBSERVE
    assert decision.reason_code is AS2GateReasonCode.BACKPRESSURE_REJECTED
    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT


def test_p0625_audit_chain_is_deterministic_and_linked(capture_sink: CaptureSink) -> None:
    controller = AS2GateControllerSkeleton(audit_sink=capture_sink)

    controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr-a",
        provider_name="memory",
    )
    controller.handle_provider_failure(
        AS2ProviderFailureReasonCode.UNAUTHORIZED,
        correlation_id="corr-b",
        provider_name="memory",
    )

    assert capture_sink.events[1].previous_state_hash == capture_sink.events[0].record_hash()
    assert capture_sink.events[0].record_hash() == capture_sink.events[0].record_hash()


def test_p0625_controller_fixture_scope_is_function_scoped(controller: AS2GateControllerSkeleton) -> None:
    """Guardrail: this fixture must be fresh per test to avoid Poison Pill leakage."""

    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT
    assert not hasattr(controller, "_dedup_log")


def test_p0625_runtime_skeleton_does_not_expose_test_fake_methods(controller: AS2GateControllerSkeleton) -> None:
    assert not hasattr(controller, "simulate_restart")
