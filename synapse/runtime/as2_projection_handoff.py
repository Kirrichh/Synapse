"""Runtime AS2 projection handoff skeleton (Alpha3g P0.6.26).

This module is the only approved production-namespace caller of
``project_validated_as2_inputs(...)``. It implements the P0.6.23 Projection
Handoff contract under the P0.6.25 AS2GateController skeleton:

- projection is allowed only after fresh control-plane approval;
- projection is limited to ``ENABLED_FOR_TEST`` semantics;
- bridge, runtime wiring, and gate-controller modules remain projection-free;
- audit events are emitted in a strict order;
- no storage, CAS, provider I/O, operator RPC, or persistent idempotency store is
  implemented here.

Audit event ordering (must not be violated):
1. ``projection_requested``  -> on handoff invocation;
2. ``projection_approved``   -> after controller approval;
3. ``projection_started``    -> immediately before projection call;
4. ``projection_completed``  -> after successful return, with artifact hashes;
5. ``projection_failed``     -> on exception, with reason_code.

Or, when approval is denied:
2b. ``projection_denied``    -> no projection_started follows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Callable, Mapping

from synapse.agent_snapshot import AgentSnapshot
from synapse.agent_snapshot_adapter import AdapterDerivationRecordSkeleton, project_validated_as2_inputs
from synapse.agent_snapshot_bridge import PreparedAS2Inputs
from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_audit_sink import AS2AuditEvent, AS2AuditSink, NoOpAuditSink
from synapse.runtime.as2_gate_controller import AS2GateController, AS2GateDecisionKind, AS2GateReasonCode
from synapse.runtime.as2_runtime_wiring import (
    AS2WiringGateState,
    WiringAgentQuarantineRequest,
    WiringSystemicDisableRequest,
)


class AS2ProjectionHandoffResultKind(str, Enum):
    """Stable result discriminants for the P0.6.26 handoff skeleton.

    ``DUPLICATE`` is a hash-only idempotency result by design. It does
    not carry ``AgentSnapshot`` or ``AdapterDerivationRecord`` instances;
    callers receive ``snapshot_hash`` and ``derivation_record_hash`` for a
    future CAS-backed lookup once CAS/storage is explicitly approved.
    """

    COMPLETED = "projection_completed"
    DENIED = "projection_denied"
    DUPLICATE = "projection_duplicate"
    AGENT_QUARANTINE = "projection_agent_quarantine"
    SYSTEMIC_FAILURE = "projection_systemic_failure"


class AS2ProjectionFailureReasonCode(str, Enum):
    """Reason codes emitted by the projection handoff skeleton."""

    PROJECTION_APPROVED = "projection_approved"
    PROJECTION_DENIED = "projection_denied"
    GATE_CHANGED = "gate_changed"
    NOT_ENABLED_FOR_TEST = "not_enabled_for_test"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    PROJECTION_DUPLICATE = "projection_duplicate"
    SYSTEMIC_CORE_FAILURE = "systemic_core_failure"
    SYSTEMIC_CORE_FAILURE_INTERRUPTED = "systemic_core_failure_interrupted"
    PROJECTION_AGENT_SCOPE_FAILURE = "projection_agent_scope_failure"


class AS2ProjectionDedupPolicy(str, Enum):
    """In-memory handoff deduplication policy.

    This is not a persistent idempotency store. P0.6.26 only provides a local
    skeleton surface so future runtime expansion can wire a production store via
    an explicit decision.
    """

    STRICT = "strict"
    POISON_PILL = "poison_pill"
    NONE = "none"


@dataclass(frozen=True)
class AS2ProjectionHandoffResult:
    """Typed result returned by the projection handoff skeleton."""

    kind: AS2ProjectionHandoffResultKind
    correlation_id: str
    snapshot: AgentSnapshot | None = None
    derivation_record: AdapterDerivationRecordSkeleton | None = None
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None
    reason_code: AS2ProjectionFailureReasonCode | str | None = None
    outcome: WiringSystemicDisableRequest | WiringAgentQuarantineRequest | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)
    projection_call_count: int = 0


ProjectionFunction = Callable[..., tuple[AgentSnapshot, AdapterDerivationRecordSkeleton]]


def prepared_inputs_hash(prepared_inputs: PreparedAS2Inputs) -> str:
    """Return a stable hash for the projection payload."""

    return stable_canonical_hash(prepared_inputs.to_validate_kwargs())


def derivation_record_hash(derivation_record: AdapterDerivationRecordSkeleton) -> str:
    """Return a stable hash for the derivation record artifact."""

    return stable_canonical_hash(asdict(derivation_record))


@dataclass(frozen=True)
class _DedupEntry:
    """In-memory dedup entry used by the projection handoff skeleton.

    ``in_progress`` prevents concurrent callers with the same correlation id
    from passing the check-then-project boundary simultaneously. Completed
    entries store hashes only; projected artifact instances are never retained.
    """

    input_hash: str
    status: str
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None

class AS2ProjectionHandoffSkeleton:
    """Production-namespace runtime projection handoff skeleton.

    The skeleton accepts its controller and audit sink via dependency injection.
    It does not construct a controller, does not mutate gate state directly, and
    does not retain AgentSnapshot or AdapterDerivationRecord instances after a
    successful handoff. The in-memory dedup cache stores only hashes and is
    protected by an internal re-entrant lock. P0.6.27 uses a two-phase
    ``in_progress`` entry so duplicate concurrent calls for the same
    correlation id cannot execute projection twice, while projection work for
    unrelated correlation ids does not hold the dedup lock.
    """

    def __init__(
        self,
        *,
        controller: AS2GateController,
        audit_sink: AS2AuditSink | None = None,
        dedup_policy: AS2ProjectionDedupPolicy = AS2ProjectionDedupPolicy.STRICT,
        projection_func: ProjectionFunction = project_validated_as2_inputs,
    ) -> None:
        self._controller = controller
        self._audit_sink = audit_sink or NoOpAuditSink()
        self._dedup_policy = dedup_policy
        self._projection_func = projection_func
        self._dedup_index: dict[str, _DedupEntry] = {}
        self._dedup_lock = RLock()
        self._last_audit_hash: str | None = None
        self._projection_call_count = 0

    @property
    def projection_call_count(self) -> int:
        """Return how many real projection calls this skeleton executed."""

        return self._projection_call_count

    def execute_projection(
        self,
        prepared_inputs: PreparedAS2Inputs,
        *,
        correlation_id: str,
        request_id: str | None = None,
    ) -> AS2ProjectionHandoffResult:
        """Authorize and execute projection under the P0.6.26 constraints."""

        if not correlation_id:
            raise ValueError("correlation_id is required for AS2 projection handoff")

        input_hash = prepared_inputs_hash(prepared_inputs)
        self._emit_projection_event(
            event_type="projection_requested",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="projection_requested",
            detail={"prepared_inputs_hash": input_hash},
        )

        dedup_result = self._check_or_mark_in_progress(
            prepared_inputs,
            correlation_id=correlation_id,
            input_hash=input_hash,
        )
        if dedup_result is not None:
            return dedup_result
        dedup_marked_in_progress = self._dedup_policy is not AS2ProjectionDedupPolicy.NONE

        approval = self._controller.request_projection_approval(
            correlation_id=correlation_id,
            request_id=request_id,
        )
        if approval.kind is not AS2GateDecisionKind.PERMIT_PROJECTION:
            if dedup_marked_in_progress:
                self._rollback_in_progress(correlation_id=correlation_id, input_hash=input_hash)
            return AS2ProjectionHandoffResult(
                kind=AS2ProjectionHandoffResultKind.DENIED,
                correlation_id=correlation_id,
                reason_code=AS2ProjectionFailureReasonCode.GATE_CHANGED,
                projection_call_count=self._projection_call_count,
            )
        if approval.gate_state is not AS2WiringGateState.ENABLED_FOR_TEST:
            if dedup_marked_in_progress:
                self._rollback_in_progress(correlation_id=correlation_id, input_hash=input_hash)
            self._emit_projection_event(
                event_type="projection_denied",
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=AS2ProjectionFailureReasonCode.NOT_ENABLED_FOR_TEST.value,
                detail={"gate_state": approval.gate_state.value},
            )
            return AS2ProjectionHandoffResult(
                kind=AS2ProjectionHandoffResultKind.DENIED,
                correlation_id=correlation_id,
                reason_code=AS2ProjectionFailureReasonCode.NOT_ENABLED_FOR_TEST,
                projection_call_count=self._projection_call_count,
            )

        self._emit_projection_event(
            event_type="projection_started",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="projection_started",
            detail={"prepared_inputs_hash": input_hash},
        )

        try:
            self._projection_call_count += 1
            snapshot, derivation = self._projection_func(**prepared_inputs.to_validate_kwargs())
        except Exception as exc:  # defensive handoff boundary
            if dedup_marked_in_progress:
                self._rollback_in_progress(correlation_id=correlation_id, input_hash=input_hash)
            return self._projection_failure(
                exc,
                correlation_id=correlation_id,
                request_id=request_id,
            )

        snapshot_hash = snapshot.snapshot_hash()
        derivation_hash = derivation_record_hash(derivation)
        self._emit_projection_event(
            event_type="projection_completed",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="projection_completed",
            agent_id=snapshot.agent_id,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_hash,
            detail={"prepared_inputs_hash": input_hash},
        )
        self._remember_dedup(
            correlation_id=correlation_id,
            input_hash=input_hash,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_hash,
        )
        return AS2ProjectionHandoffResult(
            kind=AS2ProjectionHandoffResultKind.COMPLETED,
            correlation_id=correlation_id,
            snapshot=snapshot,
            derivation_record=derivation,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_hash,
            projection_call_count=self._projection_call_count,
        )

    def _check_or_mark_in_progress(
        self,
        prepared_inputs: PreparedAS2Inputs,
        *,
        correlation_id: str,
        input_hash: str,
    ) -> AS2ProjectionHandoffResult | None:
        """Return an idempotency result or mark this call in progress.

        This method closes the P0.6.27 dedup check-then-act race. It creates an
        ``in_progress`` entry before projection starts, but completed hash data
        is written only after successful projection return. Failures roll the
        entry back, so a retry cannot be falsely classified as ``DUPLICATE``.
        """

        if self._dedup_policy is AS2ProjectionDedupPolicy.NONE:
            return None

        with self._dedup_lock:
            existing = self._dedup_index.get(correlation_id)
            if existing is not None and existing.input_hash != input_hash:
                outcome = WiringSystemicDisableRequest(
                    correlation_id=correlation_id,
                    reason="same correlation_id was reused with changed PreparedAS2Inputs",
                    reason_code=AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT.value,
                    failure_context={
                        "prepared_inputs_hash": input_hash,
                        "previous_prepared_inputs_hash": existing.input_hash,
                        "previous_status": existing.status,
                    },
                )
                self._emit_projection_event(
                    event_type="projection_failed",
                    correlation_id=correlation_id,
                    request_id=None,
                    reason_code=AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT.value,
                    detail=dict(outcome.failure_context or {}),
                )
                return AS2ProjectionHandoffResult(
                    kind=AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE,
                    correlation_id=correlation_id,
                    reason_code=AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT,
                    outcome=outcome,
                    projection_call_count=self._projection_call_count,
                )

            if existing is not None and existing.status == "completed":
                return AS2ProjectionHandoffResult(
                    kind=AS2ProjectionHandoffResultKind.DUPLICATE,
                    correlation_id=correlation_id,
                    snapshot_hash=existing.snapshot_hash,
                    derivation_record_hash=existing.derivation_record_hash,
                    reason_code=AS2ProjectionFailureReasonCode.PROJECTION_DUPLICATE,
                    projection_call_count=self._projection_call_count,
                )

            if existing is not None and existing.status == "in_progress":
                outcome = WiringSystemicDisableRequest(
                    correlation_id=correlation_id,
                    reason="same correlation_id is already in projection",
                    reason_code=AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT.value,
                    failure_context={
                        "prepared_inputs_hash": input_hash,
                        "previous_status": existing.status,
                    },
                )
                return AS2ProjectionHandoffResult(
                    kind=AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE,
                    correlation_id=correlation_id,
                    reason_code=AS2ProjectionFailureReasonCode.IDEMPOTENCY_CONFLICT,
                    outcome=outcome,
                    projection_call_count=self._projection_call_count,
                )

            self._dedup_index[correlation_id] = _DedupEntry(
                input_hash=input_hash,
                status="in_progress",
            )
            return None

    def _remember_dedup(
        self,
        *,
        correlation_id: str,
        input_hash: str,
        snapshot_hash: str,
        derivation_record_hash: str,
    ) -> None:
        if self._dedup_policy is AS2ProjectionDedupPolicy.NONE:
            return
        with self._dedup_lock:
            self._dedup_index[correlation_id] = _DedupEntry(
                input_hash=input_hash,
                status="completed",
                snapshot_hash=snapshot_hash,
                derivation_record_hash=derivation_record_hash,
            )

    def _rollback_in_progress(self, *, correlation_id: str, input_hash: str) -> None:
        """Remove an in-progress entry after denial or projection failure."""

        with self._dedup_lock:
            existing = self._dedup_index.get(correlation_id)
            if existing is not None and existing.status == "in_progress" and existing.input_hash == input_hash:
                self._dedup_index.pop(correlation_id, None)

    def _projection_failure(
        self,
        exc: Exception,
        *,
        correlation_id: str,
        request_id: str | None,
    ) -> AS2ProjectionHandoffResult:
        agent_id = getattr(exc, "agent_id", None)
        if isinstance(agent_id, str) and agent_id:
            reason_code = AS2ProjectionFailureReasonCode.PROJECTION_AGENT_SCOPE_FAILURE
            outcome = WiringAgentQuarantineRequest(
                correlation_id=correlation_id,
                agent_id=agent_id,
                reason=str(exc),
                reason_code=reason_code.value,
            )
            kind = AS2ProjectionHandoffResultKind.AGENT_QUARANTINE
        else:
            name = exc.__class__.__name__.lower()
            reason_code = (
                AS2ProjectionFailureReasonCode.SYSTEMIC_CORE_FAILURE_INTERRUPTED
                if "interrupt" in name or "cancel" in name or "timeout" in name
                else AS2ProjectionFailureReasonCode.SYSTEMIC_CORE_FAILURE
            )
            outcome = WiringSystemicDisableRequest(
                correlation_id=correlation_id,
                reason=str(exc),
                reason_code=reason_code.value,
                failure_context={"error_type": exc.__class__.__name__},
            )
            kind = AS2ProjectionHandoffResultKind.SYSTEMIC_FAILURE
        self._emit_projection_event(
            event_type="projection_failed",
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code.value,
            agent_id=agent_id if isinstance(agent_id, str) else None,
            detail={"error_type": exc.__class__.__name__},
        )
        return AS2ProjectionHandoffResult(
            kind=kind,
            correlation_id=correlation_id,
            reason_code=reason_code,
            outcome=outcome,
            projection_call_count=self._projection_call_count,
        )

    def _emit_projection_event(
        self,
        *,
        event_type: str,
        correlation_id: str,
        reason_code: str,
        request_id: str | None = None,
        agent_id: str | None = None,
        snapshot_hash: str | None = None,
        derivation_record_hash: str | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        event = AS2AuditEvent(
            event_type=event_type,
            correlation_id=correlation_id,
            request_id=request_id,
            agent_id=agent_id,
            reason_code=reason_code,
            from_state="projection_handoff",
            to_state="projection_handoff",
            previous_state_hash=self._last_audit_hash,
            snapshot_hash=snapshot_hash,
            derivation_record_hash=derivation_record_hash,
            detail=dict(detail or {}),
        )
        self._last_audit_hash = event.record_hash()
        self._audit_sink.record(event)

    def _dedup_key(self, correlation_id: str, input_hash: str) -> str:
        return stable_canonical_hash(
            {
                "correlation_id": correlation_id,
                "prepared_inputs_hash": input_hash,
                "type": "as2_projection_handoff_dedup_key",
            }
        )


__all__ = [
    "AS2ProjectionDedupPolicy",
    "AS2ProjectionFailureReasonCode",
    "AS2ProjectionHandoffResult",
    "AS2ProjectionHandoffResultKind",
    "AS2ProjectionHandoffSkeleton",
    "derivation_record_hash",
    "prepared_inputs_hash",
]
