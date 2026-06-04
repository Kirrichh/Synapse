"""P0.6.24 AS2 projection handoff integration harness tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.agent_snapshot import AgentSnapshot
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    WiringAgentQuarantineRequest,
    WiringBridgeDisabled,
    WiringSuccess,
    WiringSystemicDisableRequest,
    process_host_prestage,
)
from tests.support.as2_control_plane_fake import HarnessReasonCode, InMemoryAS2GateController
from tests.support.as2_projection_test_orchestrator import (
    ProjectionAgentScopedError,
    ProjectionHarnessOutcomeKind,
    ProjectionInternalError,
    ProjectionInterruptedError,
    AS2ProjectionTestOrchestrator,
)

AS2_BRIDGE_FIXTURE = Path(__file__).parent / "fixtures" / "as2_bridge" / "positive_bridge_minimal_host_prestage.json"


def _payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def _wiring_success(monkeypatch: pytest.MonkeyPatch, *, correlation_id: str = "corr.success") -> WiringSuccess:
    _enable_bridge(monkeypatch)
    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id=correlation_id,
    )
    assert isinstance(outcome, WiringSuccess)
    return outcome


def _prepared_with_config_variant(monkeypatch: pytest.MonkeyPatch, variant: str) -> WiringSuccess:
    _enable_bridge(monkeypatch)
    payload = _payload()
    payload["adapter_definition_source"]["config"]["harness_variant"] = variant
    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.idempotency.changed",
    )
    assert isinstance(outcome, WiringSuccess)
    return outcome


def test_p0624_wiring_success_with_control_plane_permit_calls_real_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.project.success")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    result = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is ProjectionHarnessOutcomeKind.COMPLETED
    assert isinstance(result.snapshot, AgentSnapshot)
    assert result.snapshot_hash == result.snapshot.snapshot_hash()
    assert result.derivation_record is not None
    assert result.derivation_record_hash is not None
    assert result.projection_call_count == 1
    events = [record.event_type for record in controller.get_audit_log()]
    assert events == ["projection_requested", "projection_approved", "projection_completed"]
    completed = controller.get_audit_log()[-1]
    assert completed.snapshot_hash == result.snapshot_hash
    assert completed.derivation_record_hash == result.derivation_record_hash


def test_p0624_control_plane_denial_blocks_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.project.denied")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.DISABLED_BY_DEFAULT)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    result = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is ProjectionHarnessOutcomeKind.DENIED
    assert result.reason_code == HarnessReasonCode.GATE_CHANGED.value
    assert result.snapshot is None
    assert result.projection_call_count == 0
    events = [record.event_type for record in controller.get_audit_log()]
    assert events == ["projection_requested", "projection_denied"]


def test_p0624_gate_change_between_wiring_success_and_projection_denies_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.race")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    controller.request_systemic_disable(correlation_id="corr.operator.close", reason_code="operator_closed_gate")
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    result = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is ProjectionHarnessOutcomeKind.DENIED
    assert result.reason_code == HarnessReasonCode.GATE_CHANGED.value
    assert result.snapshot is None
    assert result.projection_call_count == 0
    assert controller.get_audit_log()[-1].event_type == "projection_denied"
    assert controller.get_audit_log()[-1].reason_code == HarnessReasonCode.GATE_CHANGED.value


def test_p0624_wiring_bridge_disabled_short_circuits_projection() -> None:
    outcome = process_host_prestage(
        _payload(),
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.bridge.disabled.p0624",
    )
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)

    assert isinstance(outcome, WiringBridgeDisabled)
    decision = controller.record_bridge_disabled(correlation_id=outcome.correlation_id)

    assert decision.reason_code == HarnessReasonCode.WIRING_BRIDGE_DISABLED.value
    assert controller.state is AS2WiringGateState.ENABLED_FOR_TEST
    assert controller.get_audit_log()[-1].event_type == "config_boundary_event"


def test_p0624_systemic_and_quarantine_wiring_outcomes_map_to_control_plane() -> None:
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    systemic = WiringSystemicDisableRequest(
        correlation_id="corr.systemic.outcome",
        reason="systemic",
        reason_code="systemic_test",
    )
    quarantine = WiringAgentQuarantineRequest(
        correlation_id="corr.quarantine.outcome",
        agent_id="agent-17",
        reason_code="agent_scope_test",
    )

    systemic_decision = controller.request_systemic_disable(
        correlation_id=systemic.correlation_id,
        reason_code=str(systemic.reason_code),
    )
    quarantine_decision = controller.request_agent_quarantine(
        correlation_id=quarantine.correlation_id,
        agent_id=quarantine.agent_id,
        reason_code=str(quarantine.reason_code),
    )

    assert systemic_decision.action.value == "systemic_disable"
    assert quarantine_decision.action.value == "agent_quarantine"
    assert controller.state is AS2WiringGateState.DISABLED_AGENT_QUARANTINE


def test_p0624_strict_idempotency_same_inputs_same_correlation_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.idempotent")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    first = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)
    second = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)

    assert first.kind is ProjectionHarnessOutcomeKind.COMPLETED
    assert second.kind is ProjectionHarnessOutcomeKind.DUPLICATE
    assert first.snapshot_hash == second.snapshot_hash
    assert first.derivation_record_hash == second.derivation_record_hash
    assert first.projection_call_count == 1
    assert second.projection_call_count == 1


def test_p0624_same_correlation_changed_inputs_is_poison_pill(monkeypatch: pytest.MonkeyPatch) -> None:
    first_success = _prepared_with_config_variant(monkeypatch, "a")
    second_success = _prepared_with_config_variant(monkeypatch, "b")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    first = orchestrator.project(first_success.prepared_inputs, correlation_id="corr.poison")
    second = orchestrator.project(second_success.prepared_inputs, correlation_id="corr.poison")

    assert first.kind is ProjectionHarnessOutcomeKind.COMPLETED
    assert second.kind is ProjectionHarnessOutcomeKind.SYSTEMIC_FAILURE
    assert second.reason_code == HarnessReasonCode.IDEMPOTENCY_CONFLICT.value
    assert isinstance(second.outcome, WiringSystemicDisableRequest)
    assert second.projection_call_count == 1


def test_p0624_same_inputs_different_correlation_has_independent_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.source")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    first = orchestrator.project(success.prepared_inputs, correlation_id="corr.independent.1")
    second = orchestrator.project(success.prepared_inputs, correlation_id="corr.independent.2")

    assert first.kind is ProjectionHarnessOutcomeKind.COMPLETED
    assert second.kind is ProjectionHarnessOutcomeKind.COMPLETED
    assert first.snapshot_hash == second.snapshot_hash
    assert first.projection_call_count == 1
    assert second.projection_call_count == 2
    completed_correlations = [
        record.correlation_id for record in controller.get_audit_log() if record.event_type == "projection_completed"
    ]
    assert completed_correlations == ["corr.independent.1", "corr.independent.2"]


def test_p0624_projection_internal_error_maps_to_systemic_core_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.internal.failure")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    def _raise_internal(**_kwargs: Any):
        raise ProjectionInternalError("core invariant failed")

    result = orchestrator.project(
        success.prepared_inputs,
        correlation_id=success.correlation_id,
        projection_func=_raise_internal,
    )

    assert result.kind is ProjectionHarnessOutcomeKind.SYSTEMIC_FAILURE
    assert result.reason_code == HarnessReasonCode.SYSTEMIC_CORE_FAILURE.value
    assert isinstance(result.outcome, WiringSystemicDisableRequest)


def test_p0624_projection_interrupted_maps_to_interrupted_systemic_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.interrupted")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    def _raise_interrupted(**_kwargs: Any):
        raise ProjectionInterruptedError("projection interrupted")

    result = orchestrator.project(
        success.prepared_inputs,
        correlation_id=success.correlation_id,
        projection_func=_raise_interrupted,
    )

    assert result.kind is ProjectionHarnessOutcomeKind.SYSTEMIC_FAILURE
    assert result.reason_code == HarnessReasonCode.SYSTEMIC_CORE_FAILURE_INTERRUPTED.value
    assert isinstance(result.outcome, WiringSystemicDisableRequest)


def test_p0624_projection_agent_scoped_error_maps_to_quarantine(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.agent.failure")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    def _raise_agent(**_kwargs: Any):
        raise ProjectionAgentScopedError("agent-p0")

    result = orchestrator.project(
        success.prepared_inputs,
        correlation_id=success.correlation_id,
        projection_func=_raise_agent,
    )

    assert result.kind is ProjectionHarnessOutcomeKind.AGENT_QUARANTINE
    assert result.reason_code == HarnessReasonCode.PROJECTION_AGENT_SCOPE_FAILURE.value
    assert isinstance(result.outcome, WiringAgentQuarantineRequest)
    assert result.outcome.agent_id == "agent-p0"


def test_p0624_bridge_and_skeleton_outputs_do_not_retain_projected_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.retention")
    controller = InMemoryAS2GateController(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    orchestrator = AS2ProjectionTestOrchestrator(controller)

    result = orchestrator.project(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.snapshot is not None
    assert not hasattr(success, "snapshot")
    assert not hasattr(success, "agent_snapshot")
    assert not hasattr(success, "derivation_record")
    assert not hasattr(success.prepared_inputs, "snapshot")
    assert not hasattr(success.prepared_inputs, "agent_snapshot")
    assert not hasattr(success.prepared_inputs, "derivation_record")
