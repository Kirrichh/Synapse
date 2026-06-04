"""P0.6.24 AS2GateController harness executable contract tests."""
from __future__ import annotations

from tests.fixtures.as2_thresholds import DEFAULT_PROVIDER_FAILURE_THRESHOLD
from tests.support.as2_control_plane_fake import (
    ControlPlaneAction,
    HarnessReasonCode,
    InMemoryAS2GateController,
    ProviderFailureReasonCode,
)
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState


def test_p0624_control_plane_fake_uses_configurable_threshold_baseline() -> None:
    controller = InMemoryAS2GateController(
        initial_state=AS2WiringGateState.ENABLED_FOR_TEST,
        provider_failure_threshold=DEFAULT_PROVIDER_FAILURE_THRESHOLD,
    )

    first = controller.handle_provider_failure(
        ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr.timeout.1",
        provider_name="identity",
    )
    second = controller.handle_provider_failure(
        ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr.timeout.2",
        provider_name="identity",
    )

    assert first.action is ControlPlaneAction.OBSERVE
    assert first.reason_code == HarnessReasonCode.TIMEOUT_OBSERVED.value
    assert second.action is ControlPlaneAction.SYSTEMIC_DISABLE
    assert second.reason_code == HarnessReasonCode.PROVIDER_TIMEOUT_THRESHOLD.value
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0624_missing_request_context_is_systemic_contract_violation() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = controller.handle_provider_failure(
        ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT,
        correlation_id="corr.missing.context",
        provider_name="model_selection",
    )

    assert decision.action is ControlPlaneAction.SYSTEMIC_DISABLE
    assert decision.reason_code == HarnessReasonCode.MISSING_REQUEST_CONTEXT.value
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0624_schema_mismatch_is_systemic_contract_failure() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = controller.handle_provider_failure(
        ProviderFailureReasonCode.SCHEMA_MISMATCH,
        correlation_id="corr.schema",
        provider_name="definition",
    )

    assert decision.action is ControlPlaneAction.SYSTEMIC_DISABLE
    assert decision.reason_code == HarnessReasonCode.SCHEMA_MISMATCH.value
    assert controller.state is AS2WiringGateState.DISABLED_SYSTEMIC


def test_p0624_unauthorized_provider_failure_routes_to_operator_review_without_transition() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = controller.handle_provider_failure(
        ProviderFailureReasonCode.UNAUTHORIZED,
        correlation_id="corr.unauthorized",
        provider_name="capability_grants",
    )

    assert decision.action is ControlPlaneAction.OPERATOR_REVIEW
    assert decision.reason_code == ProviderFailureReasonCode.UNAUTHORIZED.value
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST


def test_p0624_wiring_bridge_disabled_is_config_event_without_gate_transition() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = controller.record_bridge_disabled(correlation_id="corr.bridge.disabled")

    assert decision.action is ControlPlaneAction.OPERATOR_REVIEW
    assert decision.reason_code == HarnessReasonCode.WIRING_BRIDGE_DISABLED.value
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST
    assert controller.get_audit_log()[-1].event_type == "config_boundary_event"


def test_p0624_agent_quarantine_transition_is_agent_scoped() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    decision = controller.request_agent_quarantine(
        correlation_id="corr.quarantine",
        agent_id="agent-42",
    )

    assert decision.action is ControlPlaneAction.AGENT_QUARANTINE
    assert controller.state is AS2WiringGateState.DISABLED_AGENT_QUARANTINE
    assert controller.get_audit_log()[-1].agent_id == "agent-42"


def test_p0624_audit_chain_uses_previous_state_hash() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    controller.handle_provider_failure(
        ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr.chain.1",
        provider_name="identity",
    )
    controller.handle_provider_failure(
        ProviderFailureReasonCode.TIMEOUT,
        correlation_id="corr.chain.2",
        provider_name="identity",
    )
    records = controller.get_audit_log()

    assert records[0].previous_state_hash is None
    for index in range(1, len(records)):
        assert records[index].previous_state_hash == records[index - 1].record_hash()


def test_p0624_restart_simulation_fails_safe_while_preserving_audit_log() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    controller.request_systemic_disable(
        correlation_id="corr.before.restart",
        reason_code="test_systemic",
    )

    controller.simulate_restart(correlation_id="corr.restart")

    assert controller.state is AS2WiringGateState.DISABLED_BY_DEFAULT
    records = controller.get_audit_log()
    assert [record.event_type for record in records] == ["gate_transition", "controller_restarted"]
    assert records[1].previous_state_hash == records[0].record_hash()
