"""P0.6.26 Runtime Projection Handoff Skeleton tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import synapse.agent_snapshot_bridge as bridge
from synapse.agent_snapshot import AgentSnapshot
from synapse.runtime.as2_audit_sink import AS2AuditEvent
from synapse.runtime.as2_gate_controller import AS2GateControllerSkeleton
from synapse.runtime.as2_projection_handoff import (
    AS2ProjectionDedupPolicy,
    AS2ProjectionFailureReasonCode,
    AS2ProjectionHandoffResultKind,
    AS2ProjectionHandoffSkeleton,
)
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState, WiringAgentQuarantineRequest, WiringSystemicDisableRequest, process_host_prestage, WiringSuccess

AS2_BRIDGE_FIXTURE = Path(__file__).parent / "fixtures" / "as2_bridge" / "positive_bridge_minimal_host_prestage.json"


class CaptureSink:
    def __init__(self) -> None:
        self.events: list[AS2AuditEvent] = []

    def record(self, event: AS2AuditEvent) -> None:
        self.events.append(event)


class ProjectionAgentScopedError(RuntimeError):
    def __init__(self, agent_id: str, message: str = "agent projection failed") -> None:
        super().__init__(message)
        self.agent_id = agent_id


class ProjectionInterruptedError(RuntimeError):
    pass


@pytest.fixture
def capture_sink() -> CaptureSink:
    return CaptureSink()


@pytest.fixture
def controller(capture_sink: CaptureSink) -> AS2GateControllerSkeleton:
    return AS2GateControllerSkeleton(
        initial_state=AS2WiringGateState.ENABLED_FOR_TEST,
        audit_sink=capture_sink,
    )


def _payload() -> dict[str, Any]:
    fixture = json.loads(AS2_BRIDGE_FIXTURE.read_text(encoding="utf-8"))
    payload = copy.deepcopy(fixture["host_prestage_outputs"])
    payload.pop("notes", None)
    return payload


def _enable_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "AS2_HOST_PRESTAGE_BRIDGE_ENABLED", True)


def _wiring_success(monkeypatch: pytest.MonkeyPatch, *, correlation_id: str = "corr.p0626") -> WiringSuccess:
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
    payload["adapter_definition_source"]["config"]["p0626_variant"] = variant
    outcome = process_host_prestage(
        payload,
        gate_state=AS2WiringGateState.ENABLED_FOR_TEST,
        correlation_id="corr.poison.p0626",
    )
    assert isinstance(outcome, WiringSuccess)
    return outcome


def _projection_events(capture_sink: CaptureSink) -> list[str]:
    return [event.event_type for event in capture_sink.events if event.event_type.startswith("projection_")]


def test_p0626_handoff_success_calls_real_projection_after_fresh_approval(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.success")
    handoff = AS2ProjectionHandoffSkeleton(controller=controller, audit_sink=capture_sink)

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert isinstance(result.snapshot, AgentSnapshot)
    assert result.snapshot_hash == result.snapshot.snapshot_hash()
    assert result.derivation_record is not None
    assert result.derivation_record_hash is not None
    assert result.projection_call_count == 1
    assert _projection_events(capture_sink) == [
        "projection_requested",
        "projection_approved",
        "projection_started",
        "projection_completed",
    ]


def test_p0626_handoff_denial_never_starts_projection(
    monkeypatch: pytest.MonkeyPatch,
    capture_sink: CaptureSink,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.denied")
    controller = AS2GateControllerSkeleton(
        initial_state=AS2WiringGateState.DISABLED_BY_DEFAULT,
        audit_sink=capture_sink,
    )
    handoff = AS2ProjectionHandoffSkeleton(controller=controller, audit_sink=capture_sink)

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.DENIED
    assert result.reason_code is AS2ProjectionFailureReasonCode.GATE_CHANGED
    assert result.snapshot is None
    assert result.projection_call_count == 0
    assert _projection_events(capture_sink) == ["projection_requested", "projection_denied"]


def test_p0626_projection_started_is_immediately_before_call(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.order")
    observed_events_at_projection: list[str] = []

    def _projection_probe(**kwargs: Any):
        observed_events_at_projection.extend(_projection_events(capture_sink))
        from synapse.agent_snapshot_adapter import project_validated_as2_inputs

        return project_validated_as2_inputs(**kwargs)

    handoff = AS2ProjectionHandoffSkeleton(
        controller=controller,
        audit_sink=capture_sink,
        projection_func=_projection_probe,
    )

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert observed_events_at_projection == [
        "projection_requested",
        "projection_approved",
        "projection_started",
    ]


def test_p0626_projection_internal_error_maps_to_systemic_failure(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
    capture_sink: CaptureSink,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.internal")

    def _raise_internal(**_kwargs: Any):
        raise RuntimeError("core invariant failed")

    handoff = AS2ProjectionHandoffSkeleton(
        controller=controller,
        audit_sink=capture_sink,
        projection_func=_raise_internal,
    )

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE
    assert result.reason_code is AS2ProjectionFailureReasonCode.SYSTEMIC_CORE_FAILURE
    assert isinstance(result.outcome, WiringSystemicDisableRequest)
    assert _projection_events(capture_sink)[-1] == "projection_failed"


def test_p0626_projection_interrupted_maps_to_interrupted_systemic_failure(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.interrupted")

    def _raise_interrupted(**_kwargs: Any):
        raise ProjectionInterruptedError("projection interrupted")

    handoff = AS2ProjectionHandoffSkeleton(controller=controller, projection_func=_raise_interrupted)

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE
    assert result.reason_code is AS2ProjectionFailureReasonCode.SYSTEMIC_CORE_FAILURE_INTERRUPTED
    assert isinstance(result.outcome, WiringSystemicDisableRequest)


def test_p0626_agent_scoped_projection_error_maps_to_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.agent")

    def _raise_agent(**_kwargs: Any):
        raise ProjectionAgentScopedError("agent-p0626")

    handoff = AS2ProjectionHandoffSkeleton(controller=controller, projection_func=_raise_agent)

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.kind is AS2ProjectionHandoffResultKind.AGENT_QUARANTINE
    assert result.reason_code is AS2ProjectionFailureReasonCode.PROJECTION_AGENT_SCOPE_FAILURE
    assert isinstance(result.outcome, WiringAgentQuarantineRequest)
    assert result.outcome.agent_id == "agent-p0626"


def test_p0626_same_inputs_same_correlation_deduplicates_without_retaining_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.dedup")
    handoff = AS2ProjectionHandoffSkeleton(controller=controller)

    first = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)
    second = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert first.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert second.kind is AS2ProjectionHandoffResultKind.DUPLICATE
    assert first.snapshot_hash == second.snapshot_hash
    assert first.derivation_record_hash == second.derivation_record_hash
    assert first.projection_call_count == 1
    assert second.projection_call_count == 1
    assert second.snapshot is None
    assert second.derivation_record is None


def test_p0626_same_correlation_changed_inputs_is_poison_pill(monkeypatch: pytest.MonkeyPatch) -> None:
    first_success = _prepared_with_config_variant(monkeypatch, "a")
    second_success = _prepared_with_config_variant(monkeypatch, "b")
    controller = AS2GateControllerSkeleton(initial_state=AS2WiringGateState.ENABLED_FOR_TEST)
    handoff = AS2ProjectionHandoffSkeleton(controller=controller)

    first = handoff.execute_projection(first_success.prepared_inputs, correlation_id="corr.p0626.poison")
    second = handoff.execute_projection(second_success.prepared_inputs, correlation_id="corr.p0626.poison")

    assert first.kind is AS2ProjectionHandoffResultKind.COMPLETED
    assert second.kind is AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE
    assert second.reason_code is AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT
    assert isinstance(second.outcome, WiringSystemicDisableRequest)
    assert second.projection_call_count == 1


def test_p0626_handoff_does_not_retain_projected_artifact_instances(
    monkeypatch: pytest.MonkeyPatch,
    controller: AS2GateControllerSkeleton,
) -> None:
    success = _wiring_success(monkeypatch, correlation_id="corr.p0626.retention")
    handoff = AS2ProjectionHandoffSkeleton(controller=controller)

    result = handoff.execute_projection(success.prepared_inputs, correlation_id=success.correlation_id)

    assert result.snapshot is not None
    assert result.derivation_record is not None
    assert not any(isinstance(value, AgentSnapshot) for value in handoff.__dict__.values())
    assert not any(value is result.derivation_record for value in handoff.__dict__.values())
