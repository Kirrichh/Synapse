"""Production AS2GateController skeleton (Alpha3g P0.6.25).

This module is the production-facing control-plane skeleton derived from the
P0.6.17/P0.6.22 RFCs and validated by the P0.6.24 harness. It is intentionally
limited to decision types, state-transition skeleton behavior, and injectable
audit-event emission.

Concurrency guardrail: the skeleton is designed for concurrent access and does
not assume single-threaded execution. It uses an internal lock around mutable
in-memory state. This is not a persistence or distributed-locking mechanism.

Explicit non-goals for P0.6.25:
- no projection calls;
- no AgentSnapshot construction;
- no production Host providers;
- no CAS/storage/file/network I/O;
- no operator RPC;
- no production ``ENABLED`` state;
- no degraded mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Mapping, Protocol

from synapse.runtime.as2_audit_sink import AS2AuditEvent, AS2AuditSink, NoOpAuditSink
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    AS2WiringOutcome,
    AS2WiringReasonCode,
    WiringAgentQuarantineRequest,
    WiringBridgeDisabled,
    WiringGateClosed,
    WiringSuccess,
    WiringSystemicDisableRequest,
)


class AS2GateDecisionKind(str, Enum):
    """Production-facing decision discriminants for the AS2 control plane."""

    OBSERVE = "observe"
    PERMIT_TEST_WIRING = "permit_test_wiring"
    PERMIT_PROJECTION = "projection_approved"
    DENY = "deny"
    DENY_PROJECTION = "projection_denied"
    AGENT_QUARANTINE = "agent_quarantine"
    SYSTEMIC_DISABLE = "systemic_disable"
    OPERATOR_REVIEW = "operator_review"


class AS2GateReasonCode(str, Enum):
    """Stable reason codes emitted by the P0.6.25 gate-controller skeleton."""

    WIRING_SUCCESS_OBSERVED = "wiring_success_observed"
    GATE_CLOSED_OBSERVED = "gate_closed_observed"
    WIRING_BRIDGE_DISABLED = "wiring_bridge_disabled"
    WIRING_SYSTEMIC_DISABLE_REQUESTED = "wiring_systemic_disable_requested"
    WIRING_AGENT_QUARANTINE_REQUESTED = "wiring_agent_quarantine_requested"
    PROVIDER_TIMEOUT_OBSERVED = "provider_timeout_observed"
    PROVIDER_TIMEOUT_THRESHOLD = "provider_timeout_threshold"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_NOT_FOUND = "provider_not_found"
    PROVIDER_INVALID_INPUT = "provider_invalid_input"
    PROVIDER_CANCELLED = "provider_cancelled"
    MISSING_REQUEST_CONTEXT = "missing_request_context"
    SCHEMA_MISMATCH = "schema_mismatch"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    BACKPRESSURE_REJECTED = "backpressure_rejected"
    PROJECTION_APPROVED = "projection_approved"
    PROJECTION_DENIED = "projection_denied"
    GATE_CHANGED = "gate_changed"


class AS2ProviderFailureReasonCode(str, Enum):
    """Provider-failure taxonomy subset consumed by the controller skeleton."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BACKPRESSURE_REJECTED = "backpressure_rejected"
    PROJECTION_APPROVED = "projection_approved"
    PROJECTION_DENIED = "projection_denied"
    GATE_CHANGED = "gate_changed"
    MISSING_REQUEST_CONTEXT = "missing_request_context"
    SCHEMA_MISMATCH = "schema_mismatch"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AS2GateDecision:
    """Typed result of a gate-controller decision."""

    kind: AS2GateDecisionKind
    reason_code: AS2GateReasonCode
    gate_state: AS2WiringGateState
    correlation_id: str
    request_id: str | None = None
    agent_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AS2GateTransitionRequest:
    """Explicit request to transition or observe AS2 gate state."""

    target_state: AS2WiringGateState
    reason_code: AS2GateReasonCode
    correlation_id: str
    request_id: str | None = None
    agent_id: str | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AS2GateTransitionResult:
    """Result of applying a transition request in the skeleton."""

    from_state: AS2WiringGateState
    to_state: AS2WiringGateState
    decision: AS2GateDecision
    changed: bool


class AS2GateController(Protocol):
    """Protocol for the AS2 production control-plane skeleton."""

    @property
    def state(self) -> AS2WiringGateState:
        """Return the current gate state."""

    def handle_wiring_outcome(
        self,
        outcome: AS2WiringOutcome,
        *,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Map a runtime wiring outcome to a control-plane decision."""

    def handle_provider_failure(
        self,
        reason_code: AS2ProviderFailureReasonCode,
        *,
        correlation_id: str,
        provider_name: str,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Map a provider failure to a control-plane decision."""

    def request_projection_approval(
        self,
        *,
        correlation_id: str,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Request fresh permission for projection handoff."""


class AS2GateControllerSkeleton:
    """In-memory/no-I/O AS2GateController skeleton for production namespace.

    The skeleton is intentionally smaller and stricter than the P0.6.24 test
    fake. It exposes production-facing decision types and emits audit events via
    an injected sink, but it does not implement persistence, operator RPC, or
    projection handoff.
    """

    def __init__(
        self,
        *,
        initial_state: AS2WiringGateState = AS2WiringGateState.DISABLED_BY_DEFAULT,
        provider_failure_threshold: int = 2,
        audit_sink: AS2AuditSink | None = None,
    ) -> None:
        if provider_failure_threshold < 1:
            raise ValueError("provider_failure_threshold must be >= 1")
        self._state = initial_state
        self._provider_failure_threshold = provider_failure_threshold
        self._audit_sink = audit_sink or NoOpAuditSink()
        self._failure_counts: dict[str, int] = {}
        self._last_audit_hash: str | None = None
        self._lock = RLock()

    @property
    def state(self) -> AS2WiringGateState:
        """Return current skeleton gate state."""

        with self._lock:
            return self._state

    @property
    def provider_failure_threshold(self) -> int:
        """Return configured provider-failure threshold for the skeleton."""

        return self._provider_failure_threshold

    def handle_wiring_outcome(
        self,
        outcome: AS2WiringOutcome,
        *,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Map runtime-wiring outcomes to P0.6.22 control-plane decisions."""

        if isinstance(outcome, WiringSuccess):
            return self._observe(
                correlation_id=outcome.correlation_id,
                request_id=request_id,
                reason_code=AS2GateReasonCode.WIRING_SUCCESS_OBSERVED,
                detail={"wiring_reason": "success"},
            )
        if isinstance(outcome, WiringGateClosed):
            return self._observe(
                correlation_id=outcome.correlation_id,
                request_id=request_id,
                reason_code=AS2GateReasonCode.GATE_CLOSED_OBSERVED,
                detail={"wiring_reason_code": outcome.reason_code.value},
            )
        if isinstance(outcome, WiringBridgeDisabled):
            return self._operator_review(
                correlation_id=outcome.correlation_id,
                request_id=request_id,
                reason_code=AS2GateReasonCode.WIRING_BRIDGE_DISABLED,
                detail={"wiring_reason_code": outcome.reason_code.value},
            )
        if isinstance(outcome, WiringSystemicDisableRequest):
            return self._transition(
                AS2GateTransitionRequest(
                    target_state=AS2WiringGateState.DISABLED_SYSTEMIC,
                    reason_code=AS2GateReasonCode.WIRING_SYSTEMIC_DISABLE_REQUESTED,
                    correlation_id=outcome.correlation_id,
                    request_id=request_id,
                    detail={
                        "wiring_reason_code": outcome.reason_code.value,
                        "failure_context": dict(outcome.failure_context or {}),
                    },
                )
            ).decision
        if isinstance(outcome, WiringAgentQuarantineRequest):
            return self._transition(
                AS2GateTransitionRequest(
                    target_state=AS2WiringGateState.DISABLED_AGENT_QUARANTINE,
                    reason_code=AS2GateReasonCode.WIRING_AGENT_QUARANTINE_REQUESTED,
                    correlation_id=outcome.correlation_id,
                    request_id=request_id,
                    agent_id=outcome.agent_id,
                    detail={"wiring_reason_code": outcome.reason_code.value},
                )
            ).decision
        return self._observe(
            correlation_id=outcome.correlation_id,
            request_id=request_id,
            reason_code=AS2GateReasonCode.GATE_CLOSED_OBSERVED,
            detail={"wiring_reason": "unknown_outcome"},
        )

    def request_projection_approval(
        self,
        *,
        correlation_id: str,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Authorize projection only for the test-enabled AS2 gate state.

        P0.6.26 keeps production ``ENABLED`` locked. Projection handoff callers
        must request this fresh approval immediately before invoking projection.
        """

        with self._lock:
            if self._state is AS2WiringGateState.ENABLED_FOR_TEST:
                decision = AS2GateDecision(
                    kind=AS2GateDecisionKind.PERMIT_PROJECTION,
                    reason_code=AS2GateReasonCode.PROJECTION_APPROVED,
                    gate_state=self._state,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    detail={"approval_scope": "enabled_for_test"},
                )
                self._emit_event(decision, from_state=self._state, to_state=self._state)
                return decision

            decision = AS2GateDecision(
                kind=AS2GateDecisionKind.DENY_PROJECTION,
                reason_code=AS2GateReasonCode.GATE_CHANGED,
                gate_state=self._state,
                correlation_id=correlation_id,
                request_id=request_id,
                detail={"approval_scope": "projection_denied"},
            )
            self._emit_event(decision, from_state=self._state, to_state=self._state)
            return decision

    def handle_provider_failure(
        self,
        reason_code: AS2ProviderFailureReasonCode,
        *,
        correlation_id: str,
        provider_name: str,
        request_id: str | None = None,
    ) -> AS2GateDecision:
        """Map provider failures to deterministic control-plane decisions."""

        if reason_code is AS2ProviderFailureReasonCode.TIMEOUT:
            with self._lock:
                count = self._failure_counts.get(provider_name, 0) + 1
                self._failure_counts[provider_name] = count
                if count >= self._provider_failure_threshold:
                    return self._transition(
                        AS2GateTransitionRequest(
                            target_state=AS2WiringGateState.DISABLED_SYSTEMIC,
                            reason_code=AS2GateReasonCode.PROVIDER_TIMEOUT_THRESHOLD,
                            correlation_id=correlation_id,
                            request_id=request_id,
                            detail={"failure_count": count, "provider_name": provider_name},
                        )
                    ).decision
                return self._observe(
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code=AS2GateReasonCode.PROVIDER_TIMEOUT_OBSERVED,
                    detail={"failure_count": count, "provider_name": provider_name},
                )
        if reason_code is AS2ProviderFailureReasonCode.UNAVAILABLE:
            return self._transition(
                AS2GateTransitionRequest(
                    target_state=AS2WiringGateState.DISABLED_SYSTEMIC,
                    reason_code=AS2GateReasonCode.PROVIDER_UNAVAILABLE,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    detail={"provider_name": provider_name},
                )
            ).decision
        if reason_code in {
            AS2ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT,
            AS2ProviderFailureReasonCode.SCHEMA_MISMATCH,
            AS2ProviderFailureReasonCode.NOT_FOUND,
            AS2ProviderFailureReasonCode.INVALID_INPUT,
        }:
            mapped_reason = {
                AS2ProviderFailureReasonCode.MISSING_REQUEST_CONTEXT: AS2GateReasonCode.MISSING_REQUEST_CONTEXT,
                AS2ProviderFailureReasonCode.SCHEMA_MISMATCH: AS2GateReasonCode.SCHEMA_MISMATCH,
                AS2ProviderFailureReasonCode.NOT_FOUND: AS2GateReasonCode.PROVIDER_NOT_FOUND,
                AS2ProviderFailureReasonCode.INVALID_INPUT: AS2GateReasonCode.PROVIDER_INVALID_INPUT,
            }[reason_code]
            return self._transition(
                AS2GateTransitionRequest(
                    target_state=AS2WiringGateState.DISABLED_SYSTEMIC,
                    reason_code=mapped_reason,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    detail={"provider_name": provider_name},
                )
            ).decision
        if reason_code in {
            AS2ProviderFailureReasonCode.UNAUTHORIZED,
            AS2ProviderFailureReasonCode.FORBIDDEN,
        }:
            mapped_reason = (
                AS2GateReasonCode.UNAUTHORIZED
                if reason_code is AS2ProviderFailureReasonCode.UNAUTHORIZED
                else AS2GateReasonCode.FORBIDDEN
            )
            return self._operator_review(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=mapped_reason,
                detail={"provider_name": provider_name},
            )
        if reason_code is AS2ProviderFailureReasonCode.BACKPRESSURE_REJECTED:
            return self._observe(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=AS2GateReasonCode.BACKPRESSURE_REJECTED,
                detail={"provider_name": provider_name},
            )
        if reason_code is AS2ProviderFailureReasonCode.CANCELLED:
            return self._observe(
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=AS2GateReasonCode.PROVIDER_CANCELLED,
                detail={"provider_name": provider_name},
            )
        return self._observe(
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=AS2GateReasonCode.PROVIDER_TIMEOUT_OBSERVED,
            detail={"provider_name": provider_name, "unmapped_reason": reason_code.value},
        )

    def _observe(
        self,
        *,
        correlation_id: str,
        request_id: str | None,
        reason_code: AS2GateReasonCode,
        detail: Mapping[str, Any] | None = None,
    ) -> AS2GateDecision:
        with self._lock:
            state = self._state
        decision = AS2GateDecision(
            kind=AS2GateDecisionKind.OBSERVE,
            reason_code=reason_code,
            gate_state=state,
            correlation_id=correlation_id,
            request_id=request_id,
            detail=dict(detail or {}),
        )
        self._emit_event(decision, from_state=state, to_state=state)
        return decision

    def _operator_review(
        self,
        *,
        correlation_id: str,
        request_id: str | None,
        reason_code: AS2GateReasonCode,
        detail: Mapping[str, Any] | None = None,
    ) -> AS2GateDecision:
        with self._lock:
            state = self._state
        decision = AS2GateDecision(
            kind=AS2GateDecisionKind.OPERATOR_REVIEW,
            reason_code=reason_code,
            gate_state=state,
            correlation_id=correlation_id,
            request_id=request_id,
            detail=dict(detail or {}),
        )
        self._emit_event(decision, from_state=state, to_state=state)
        return decision

    def _transition(self, request: AS2GateTransitionRequest) -> AS2GateTransitionResult:
        with self._lock:
            from_state = self._state
            self._state = request.target_state
            to_state = self._state
        decision_kind = (
            AS2GateDecisionKind.AGENT_QUARANTINE
            if request.target_state is AS2WiringGateState.DISABLED_AGENT_QUARANTINE
            else AS2GateDecisionKind.SYSTEMIC_DISABLE
        )
        decision = AS2GateDecision(
            kind=decision_kind,
            reason_code=request.reason_code,
            gate_state=to_state,
            correlation_id=request.correlation_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            detail=dict(request.detail),
        )
        self._emit_event(decision, from_state=from_state, to_state=to_state)
        return AS2GateTransitionResult(
            from_state=from_state,
            to_state=to_state,
            decision=decision,
            changed=from_state is not to_state,
        )

    def _emit_event(
        self,
        decision: AS2GateDecision,
        *,
        from_state: AS2WiringGateState,
        to_state: AS2WiringGateState,
    ) -> None:
        with self._lock:
            previous_hash = self._last_audit_hash
            event = AS2AuditEvent(
                event_type=decision.kind.value,
                correlation_id=decision.correlation_id,
                request_id=decision.request_id,
                agent_id=decision.agent_id,
                reason_code=decision.reason_code.value,
                from_state=from_state.value,
                to_state=to_state.value,
                previous_state_hash=previous_hash,
                detail=dict(decision.detail),
            )
            self._last_audit_hash = event.record_hash()
        self._audit_sink.record(event)


__all__ = [
    "AS2GateController",
    "AS2GateControllerSkeleton",
    "AS2GateDecision",
    "AS2GateDecisionKind",
    "AS2GateReasonCode",
    "AS2GateTransitionRequest",
    "AS2GateTransitionResult",
    "AS2ProviderFailureReasonCode",
]
