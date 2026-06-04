"""P0.6.24 test-only AS2GateController fake.

This module is an executable test contract for the P0.6.22 Control Plane RFC.
It intentionally lives under tests/support and must not be imported by synapse/.
The fake is a working in-memory implementation, not a mock with pre-programmed
expectations: it owns a gate state, applies transition rules, and records an
append-only audit chain for harness assertions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_runtime_wiring import AS2WiringGateState


class HarnessReasonCode(str, Enum):
    """Test-harness reason codes derived from P0.6.22/P0.6.23 RFCs."""

    PROJECTION_APPROVED = "projection_approved"
    PROJECTION_DENIED = "projection_denied"
    GATE_CHANGED = "gate_changed"
    TIMEOUT_OBSERVED = "timeout_observed"
    PROVIDER_TIMEOUT_THRESHOLD = "provider_timeout_threshold"
    MISSING_REQUEST_CONTEXT = "missing_request_context"
    SCHEMA_MISMATCH = "schema_mismatch"
    WIRING_BRIDGE_DISABLED = "wiring_bridge_disabled"
    SYSTEMIC_CORE_FAILURE = "systemic_core_failure"
    SYSTEMIC_CORE_FAILURE_INTERRUPTED = "systemic_core_failure_interrupted"
    PROJECTION_AGENT_SCOPE_FAILURE = "projection_agent_scope_failure"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    AGENT_QUARANTINE = "agent_quarantine"
    CONTROLLER_RESTART = "controller_restart"


class ProviderFailureReasonCode(str, Enum):
    """Subset of P0.6.21 provider failure taxonomy used by P0.6.24."""

    TIMEOUT = "timeout"
    MISSING_REQUEST_CONTEXT = "missing_request_context"
    SCHEMA_MISMATCH = "schema_mismatch"
    UNAUTHORIZED = "unauthorized"


class ControlPlaneAction(str, Enum):
    """Deterministic actions emitted by the in-memory fake."""

    OBSERVE = "observe"
    PERMIT_PROJECTION = "permit_projection"
    DENY_PROJECTION = "deny_projection"
    AGENT_QUARANTINE = "agent_quarantine"
    SYSTEMIC_DISABLE = "systemic_disable"
    OPERATOR_REVIEW = "operator_review"


@dataclass(frozen=True)
class AuditRecord:
    """In-memory append-only audit record with chain linkage."""

    event_type: str
    correlation_id: str
    reason_code: str
    from_state: str
    to_state: str
    previous_state_hash: str | None
    request_id: str | None = None
    agent_id: str | None = None
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_hash_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "correlation_id": self.correlation_id,
            "derivation_record_hash": self.derivation_record_hash,
            "detail": dict(self.detail),
            "event_type": self.event_type,
            "from_state": self.from_state,
            "previous_state_hash": self.previous_state_hash,
            "reason_code": self.reason_code,
            "request_id": self.request_id,
            "snapshot_hash": self.snapshot_hash,
            "to_state": self.to_state,
        }

    def record_hash(self) -> str:
        return stable_canonical_hash(self.to_hash_payload())


@dataclass(frozen=True)
class ControlPlaneDecision:
    """Result of a fake control-plane decision."""

    action: ControlPlaneAction
    reason_code: str
    gate_state: AS2WiringGateState
    permitted: bool = False


class InMemoryAS2GateController:
    """Working test fake for AS2GateController behavior.

    The fake keeps state in memory, records an append-only audit chain, exposes
    explicit restart simulation, and implements the threshold behavior required
    by P0.6.24 harness tests. It is not production control-plane code.
    """

    def __init__(
        self,
        *,
        initial_state: AS2WiringGateState = AS2WiringGateState.DISABLED_BY_DEFAULT,
        provider_failure_threshold: int = 2,
    ) -> None:
        if provider_failure_threshold < 1:
            raise ValueError("provider_failure_threshold must be >= 1")
        self._state = initial_state
        self._provider_failure_threshold = provider_failure_threshold
        self._failure_counts: dict[str, int] = {}
        self._audit_log: list[AuditRecord] = []

    @property
    def state(self) -> AS2WiringGateState:
        return self._state

    @property
    def provider_failure_threshold(self) -> int:
        return self._provider_failure_threshold

    def get_audit_log(self) -> list[AuditRecord]:
        return list(self._audit_log)

    def simulate_restart(self, *, correlation_id: str = "harness.restart") -> None:
        """Simulate a cold start: state fails safe, audit history remains."""

        previous_state = self._state
        self._state = AS2WiringGateState.DISABLED_BY_DEFAULT
        self._append_record(
            event_type="controller_restarted",
            correlation_id=correlation_id,
            reason_code=HarnessReasonCode.CONTROLLER_RESTART.value,
            from_state=previous_state,
            to_state=self._state,
        )

    def record_projection_requested(
        self,
        *,
        correlation_id: str,
        request_id: str = "test-request",
    ) -> None:
        self._append_record(
            event_type="projection_requested",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="projection_requested",
            from_state=self._state,
            to_state=self._state,
        )

    def record_projection_denied(
        self,
        *,
        correlation_id: str,
        reason_code: str,
        request_id: str = "test-request",
    ) -> None:
        self._append_record(
            event_type="projection_denied",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code,
            from_state=self._state,
            to_state=self._state,
        )

    def request_projection_approval(
        self,
        *,
        correlation_id: str,
        request_id: str = "test-request",
    ) -> ControlPlaneDecision:
        """Authorize projection only when the gate is freshly ENABLED_FOR_TEST."""

        if self._state is AS2WiringGateState.ENABLED_FOR_TEST:
            self._append_record(
                event_type="projection_approved",
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.PROJECTION_APPROVED.value,
                from_state=self._state,
                to_state=self._state,
            )
            return ControlPlaneDecision(
                action=ControlPlaneAction.PERMIT_PROJECTION,
                reason_code=HarnessReasonCode.PROJECTION_APPROVED.value,
                gate_state=self._state,
                permitted=True,
            )
        self._append_record(
            event_type="projection_denied",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=HarnessReasonCode.GATE_CHANGED.value,
            from_state=self._state,
            to_state=self._state,
        )
        return ControlPlaneDecision(
            action=ControlPlaneAction.DENY_PROJECTION,
            reason_code=HarnessReasonCode.GATE_CHANGED.value,
            gate_state=self._state,
            permitted=False,
        )

    def handle_provider_failure(
        self,
        reason_code: ProviderFailureReasonCode,
        *,
        correlation_id: str,
        provider_name: str = "mock-provider",
        request_id: str = "test-request",
    ) -> ControlPlaneDecision:
        """Map provider failures to P0.6.22 control-plane actions."""

        if reason_code is ProviderFailureReasonCode.TIMEOUT:
            count = self._failure_counts.get(provider_name, 0) + 1
            self._failure_counts[provider_name] = count
            if count >= self._provider_failure_threshold:
                return self.request_systemic_disable(
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code=HarnessReasonCode.PROVIDER_TIMEOUT_THRESHOLD.value,
                    detail={"failure_count": count, "provider_name": provider_name},
                )
            self._append_record(
                event_type="provider_failure_observed",
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.TIMEOUT_OBSERVED.value,
                from_state=self._state,
                to_state=self._state,
                detail={"failure_count": count, "provider_name": provider_name},
            )
            return ControlPlaneDecision(
                action=ControlPlaneAction.OBSERVE,
                reason_code=HarnessReasonCode.TIMEOUT_OBSERVED.value,
                gate_state=self._state,
            )
        if reason_code is ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT:
            return self.request_systemic_disable(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.MISSING_REQUEST_CONTEXT.value,
                detail={"provider_name": provider_name},
            )
        if reason_code is ProviderFailureReasonCode.SCHEMA_MISMATCH:
            return self.request_systemic_disable(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=HarnessReasonCode.SCHEMA_MISMATCH.value,
                detail={"provider_name": provider_name},
            )
        self._append_record(
            event_type="operator_review_requested",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code.value,
            from_state=self._state,
            to_state=self._state,
            detail={"provider_name": provider_name},
        )
        return ControlPlaneDecision(
            action=ControlPlaneAction.OPERATOR_REVIEW,
            reason_code=reason_code.value,
            gate_state=self._state,
        )

    def request_systemic_disable(
        self,
        *,
        correlation_id: str,
        reason_code: str,
        request_id: str = "test-request",
        detail: Mapping[str, Any] | None = None,
    ) -> ControlPlaneDecision:
        previous_state = self._state
        self._state = AS2WiringGateState.DISABLED_SYSTEMIC
        self._append_record(
            event_type="gate_transition",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code,
            from_state=previous_state,
            to_state=self._state,
            detail=detail or {},
        )
        return ControlPlaneDecision(
            action=ControlPlaneAction.SYSTEMIC_DISABLE,
            reason_code=reason_code,
            gate_state=self._state,
        )

    def request_agent_quarantine(
        self,
        *,
        correlation_id: str,
        agent_id: str | None,
        request_id: str = "test-request",
        reason_code: str = HarnessReasonCode.AGENT_QUARANTINE.value,
    ) -> ControlPlaneDecision:
        previous_state = self._state
        self._state = AS2WiringGateState.DISABLED_AGENT_QUARANTINE
        self._append_record(
            event_type="agent_quarantine_requested",
            correlation_id=correlation_id,
            request_id=request_id,
            agent_id=agent_id,
            reason_code=reason_code,
            from_state=previous_state,
            to_state=self._state,
        )
        return ControlPlaneDecision(
            action=ControlPlaneAction.AGENT_QUARANTINE,
            reason_code=reason_code,
            gate_state=self._state,
        )

    def record_bridge_disabled(
        self,
        *,
        correlation_id: str,
        request_id: str = "test-request",
    ) -> ControlPlaneDecision:
        self._append_record(
            event_type="config_boundary_event",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=HarnessReasonCode.WIRING_BRIDGE_DISABLED.value,
            from_state=self._state,
            to_state=self._state,
        )
        return ControlPlaneDecision(
            action=ControlPlaneAction.OPERATOR_REVIEW,
            reason_code=HarnessReasonCode.WIRING_BRIDGE_DISABLED.value,
            gate_state=self._state,
        )

    def record_projection_completed(
        self,
        *,
        correlation_id: str,
        snapshot_hash: str,
        derivation_record_hash: str,
        request_id: str = "test-request",
        agent_id: str | None = None,
    ) -> None:
        self._append_record(
            event_type="projection_completed",
            correlation_id=correlation_id,
            request_id=request_id,
            agent_id=agent_id,
            reason_code="projection_completed",
            from_state=self._state,
            to_state=self._state,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_record_hash,
        )

    def record_projection_failed(
        self,
        *,
        correlation_id: str,
        reason_code: str,
        request_id: str = "test-request",
        agent_id: str | None = None,
    ) -> None:
        self._append_record(
            event_type="projection_failed",
            correlation_id=correlation_id,
            request_id=request_id,
            agent_id=agent_id,
            reason_code=reason_code,
            from_state=self._state,
            to_state=self._state,
        )

    def _append_record(
        self,
        *,
        event_type: str,
        correlation_id: str,
        reason_code: str,
        from_state: AS2WiringGateState,
        to_state: AS2WiringGateState,
        request_id: str | None = None,
        agent_id: str | None = None,
        snapshot_hash: str | None = None,
        derivation_record_hash: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        previous_state_hash = self._audit_log[-1].record_hash() if self._audit_log else None
        self._audit_log.append(
            AuditRecord(
                event_type=event_type,
                correlation_id=correlation_id,
                request_id=request_id,
                agent_id=agent_id,
                reason_code=reason_code,
                from_state=from_state.value,
                to_state=to_state.value,
                previous_state_hash=previous_state_hash,
                snapshot_hash=snapshot_hash,
                derivation_record_hash=derivation_record_hash,
                detail=detail or {},
            )
        )
