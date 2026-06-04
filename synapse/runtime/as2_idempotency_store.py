"""In-memory AS2 persistent idempotency store skeleton (Alpha3g P0.6.36).

This module materializes the P0.6.32 Persistent Idempotency Store RFC as a
production-facing, storage-free skeleton. It deliberately performs no file,
network, database, CAS, provider, projection, or runtime I/O.

Boundary summary:
* Idempotency is scoped to projection handoff only; this module never calls the
  projection core and never constructs AgentSnapshot artifacts.
* ``IdempotencyKey`` is the canonical ``correlation_id`` +
  ``prepared_inputs_hash`` pair.
* State transitions use CAS-like conditional update semantics guarded by a
  local ``threading.RLock``.
* State-changing transitions model the RFC atomic linkage surface by emitting
  the audit event before committing the in-memory state change. If audit
  emission raises, the idempotency record remains unchanged: no audit record,
  no idempotency state transition.
* TTL checks use an injected clock. The module does not call ambient time
  directly after construction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from time import time as _default_clock
from typing import Callable

from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_audit_outbox import CHAIN_START
from synapse.runtime.as2_audit_sink import AS2AuditEvent, AS2AuditSink, NoOpAuditSink


class IdempotencyRecordState(str, Enum):
    """Projection idempotency record states."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE_IN_PROGRESS = "stale_in_progress"


class IdempotencyFailureReason(str, Enum):
    """Reasons attached to FAILED idempotency records."""

    POISON_PILL = "poison_pill"
    SYSTEMIC_CORE_FAILURE = "systemic_core_failure"
    PROJECTION_INTERRUPTED = "projection_interrupted"
    STORE_UNAVAILABLE = "store_unavailable"


class IdempotencyResultReason(str, Enum):
    """Machine-readable operation result reasons."""

    RESERVED = "reserved"
    DUPLICATE = "duplicate"
    POISON_PILL = "poison_pill"
    TERMINAL_POISON_PILL = "terminal_poison_pill"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    MARKED_STALE = "marked_stale"
    NOT_FOUND = "not_found"
    WRONG_STATE = "wrong_state"
    NOT_EXPIRED = "not_expired"
    STORE_UNAVAILABLE = "store_unavailable"
    CAPACITY_EXCEEDED = "capacity_exceeded"


class IdempotencyStoreUnavailable(RuntimeError):
    """Raised when the idempotency store is unavailable and must fail closed."""


@dataclass(frozen=True)
class IdempotencyKey:
    """Canonical idempotency key: logical request + canonical input hash."""

    correlation_id: str
    prepared_inputs_hash: str

    def canonical_hash(self) -> str:
        """Return a deterministic hash representation of this key."""

        return stable_canonical_hash(
            {
                "correlation_id": self.correlation_id,
                "prepared_inputs_hash": self.prepared_inputs_hash,
            }
        )


@dataclass(frozen=True)
class IdempotencyRecord:
    """Immutable idempotency record snapshot."""

    key: IdempotencyKey
    input_hash: str
    state: IdempotencyRecordState
    created_at: float
    updated_at: float
    snapshot_hash: str | None = None
    derivation_record_hash: str | None = None
    failure_reason: IdempotencyFailureReason | None = None


@dataclass(frozen=True)
class IdempotencyReservationResult:
    """Outcome of a reservation attempt."""

    accepted: bool
    reason_code: IdempotencyResultReason
    record: IdempotencyRecord | None = None


@dataclass(frozen=True)
class IdempotencyTransitionResult:
    """Outcome of a conditional state transition."""

    accepted: bool
    reason_code: IdempotencyResultReason
    record: IdempotencyRecord | None = None


class InMemoryIdempotencyStore:
    """In-memory idempotency store skeleton with conditional update semantics.

    The class is a behavior skeleton, not a persistence backend. It does not
    replace the existing handoff-local dedup index in P0.6.36; integration is a
    later ENABLED_FOR_TEST harness concern.
    """

    def __init__(
        self,
        *,
        audit_sink: AS2AuditSink | None = None,
        clock: Callable[[], float] = _default_clock,
        available: bool = True,
        max_records: int | None = None,
    ) -> None:
        if max_records is not None and max_records < 0:
            raise ValueError("max_records must be non-negative or None")
        self._audit_sink = audit_sink if audit_sink is not None else NoOpAuditSink()
        self._clock = clock
        self._available = available
        self._max_records = max_records
        self._records: dict[IdempotencyKey, IdempotencyRecord] = {}
        self._last_audit_hash = CHAIN_START
        self._lock = RLock()

    def reserve_if_absent(self, key: IdempotencyKey, input_hash: str) -> IdempotencyReservationResult:
        """Reserve ``key`` as IN_PROGRESS if no matching record exists.

        Same key + same input hash is a duplicate. Same correlation_id +
        different input hash is a Poison Pill and is recorded as
        FAILED(reason_code=POISON_PILL).
        """

        with self._lock:
            self._ensure_available()
            terminal = self._terminal_poison_for_correlation(key.correlation_id)
            if terminal is not None:
                return IdempotencyReservationResult(
                    accepted=False,
                    reason_code=IdempotencyResultReason.TERMINAL_POISON_PILL,
                    record=terminal,
                )

            existing = self._records.get(key)
            if existing is not None:
                if existing.failure_reason == IdempotencyFailureReason.POISON_PILL:
                    return IdempotencyReservationResult(
                        accepted=False,
                        reason_code=IdempotencyResultReason.TERMINAL_POISON_PILL,
                        record=existing,
                    )
                if existing.input_hash == input_hash:
                    return IdempotencyReservationResult(
                        accepted=False,
                        reason_code=IdempotencyResultReason.DUPLICATE,
                        record=existing,
                    )

            sibling = self._record_with_same_correlation(key.correlation_id)
            if sibling is not None and sibling.input_hash != input_hash:
                now = self._clock()
                poison = IdempotencyRecord(
                    key=key,
                    input_hash=input_hash,
                    state=IdempotencyRecordState.FAILED,
                    created_at=now,
                    updated_at=now,
                    failure_reason=IdempotencyFailureReason.POISON_PILL,
                )
                self._emit_then_commit(
                    key=key,
                    next_record=poison,
                    event_type="idempotency_poison_pill",
                    reason_code=IdempotencyFailureReason.POISON_PILL.value,
                    from_state=sibling.state.value,
                    to_state=IdempotencyRecordState.FAILED.value,
                    detail={
                        "existing_input_hash": sibling.input_hash,
                        "attempted_input_hash": input_hash,
                        "idempotency_key_hash": key.canonical_hash(),
                    },
                )
                return IdempotencyReservationResult(
                    accepted=False,
                    reason_code=IdempotencyResultReason.POISON_PILL,
                    record=poison,
                )

            self._ensure_capacity()
            now = self._clock()
            record = IdempotencyRecord(
                key=key,
                input_hash=input_hash,
                state=IdempotencyRecordState.IN_PROGRESS,
                created_at=now,
                updated_at=now,
            )
            self._emit_then_commit(
                key=key,
                next_record=record,
                event_type="idempotency_reserved",
                reason_code=IdempotencyResultReason.RESERVED.value,
                from_state="absent",
                to_state=IdempotencyRecordState.IN_PROGRESS.value,
                detail={"idempotency_key_hash": key.canonical_hash()},
            )
            return IdempotencyReservationResult(
                accepted=True,
                reason_code=IdempotencyResultReason.RESERVED,
                record=record,
            )

    def complete_if_state(
        self,
        key: IdempotencyKey,
        *,
        expected_state: IdempotencyRecordState = IdempotencyRecordState.IN_PROGRESS,
        snapshot_hash: str,
        derivation_record_hash: str,
    ) -> IdempotencyTransitionResult:
        """Transition an IN_PROGRESS record to COMPLETED if state matches."""

        with self._lock:
            self._ensure_available()
            current = self._records.get(key)
            guard = self._guard_transition(current, expected_state)
            if guard is not None:
                return guard
            assert current is not None
            next_record = replace(
                current,
                state=IdempotencyRecordState.COMPLETED,
                updated_at=self._clock(),
                snapshot_hash=snapshot_hash,
                derivation_record_hash=derivation_record_hash,
                failure_reason=None,
            )
            self._emit_then_commit(
                key=key,
                next_record=next_record,
                event_type="idempotency_completed",
                reason_code=IdempotencyResultReason.COMPLETED.value,
                from_state=current.state.value,
                to_state=IdempotencyRecordState.COMPLETED.value,
                detail={
                    "idempotency_key_hash": key.canonical_hash(),
                    "snapshot_hash": snapshot_hash,
                    "derivation_record_hash": derivation_record_hash,
                },
            )
            return IdempotencyTransitionResult(
                accepted=True,
                reason_code=IdempotencyResultReason.COMPLETED,
                record=next_record,
            )

    def fail_if_state(
        self,
        key: IdempotencyKey,
        *,
        reason: IdempotencyFailureReason,
        expected_state: IdempotencyRecordState = IdempotencyRecordState.IN_PROGRESS,
    ) -> IdempotencyTransitionResult:
        """Transition a record to FAILED if state matches."""

        with self._lock:
            self._ensure_available()
            current = self._records.get(key)
            guard = self._guard_transition(current, expected_state)
            if guard is not None:
                return guard
            assert current is not None
            next_record = replace(
                current,
                state=IdempotencyRecordState.FAILED,
                updated_at=self._clock(),
                failure_reason=reason,
            )
            self._emit_then_commit(
                key=key,
                next_record=next_record,
                event_type="idempotency_failed",
                reason_code=reason.value,
                from_state=current.state.value,
                to_state=IdempotencyRecordState.FAILED.value,
                detail={"idempotency_key_hash": key.canonical_hash()},
            )
            return IdempotencyTransitionResult(
                accepted=True,
                reason_code=IdempotencyResultReason.FAILED,
                record=next_record,
            )

    def cancel_if_state(
        self,
        key: IdempotencyKey,
        *,
        expected_state: IdempotencyRecordState = IdempotencyRecordState.IN_PROGRESS,
    ) -> IdempotencyTransitionResult:
        """Transition a record to CANCELLED if state matches."""

        with self._lock:
            self._ensure_available()
            current = self._records.get(key)
            guard = self._guard_transition(current, expected_state)
            if guard is not None:
                return guard
            assert current is not None
            next_record = replace(
                current,
                state=IdempotencyRecordState.CANCELLED,
                updated_at=self._clock(),
                failure_reason=None,
            )
            self._emit_then_commit(
                key=key,
                next_record=next_record,
                event_type="idempotency_cancelled",
                reason_code=IdempotencyResultReason.CANCELLED.value,
                from_state=current.state.value,
                to_state=IdempotencyRecordState.CANCELLED.value,
                detail={"idempotency_key_hash": key.canonical_hash()},
            )
            return IdempotencyTransitionResult(
                accepted=True,
                reason_code=IdempotencyResultReason.CANCELLED,
                record=next_record,
            )

    def mark_stale_if_expired(
        self,
        key: IdempotencyKey,
        *,
        ttl_seconds: float,
        expected_state: IdempotencyRecordState = IdempotencyRecordState.IN_PROGRESS,
    ) -> IdempotencyTransitionResult:
        """Mark an expired IN_PROGRESS record as STALE_IN_PROGRESS."""

        with self._lock:
            self._ensure_available()
            current = self._records.get(key)
            guard = self._guard_transition(current, expected_state)
            if guard is not None:
                return guard
            assert current is not None
            if self._clock() - current.created_at <= ttl_seconds:
                return IdempotencyTransitionResult(
                    accepted=False,
                    reason_code=IdempotencyResultReason.NOT_EXPIRED,
                    record=current,
                )
            next_record = replace(
                current,
                state=IdempotencyRecordState.STALE_IN_PROGRESS,
                updated_at=self._clock(),
            )
            self._emit_then_commit(
                key=key,
                next_record=next_record,
                event_type="idempotency_stale_in_progress",
                reason_code=IdempotencyResultReason.MARKED_STALE.value,
                from_state=current.state.value,
                to_state=IdempotencyRecordState.STALE_IN_PROGRESS.value,
                detail={
                    "idempotency_key_hash": key.canonical_hash(),
                    "operator_review_required": True,
                },
            )
            return IdempotencyTransitionResult(
                accepted=True,
                reason_code=IdempotencyResultReason.MARKED_STALE,
                record=next_record,
            )

    def inspect(self, key: IdempotencyKey) -> IdempotencyRecord | None:
        """Return an immutable record snapshot, if present."""

        with self._lock:
            return self._records.get(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def _ensure_available(self) -> None:
        if not self._available:
            raise IdempotencyStoreUnavailable("idempotency store unavailable; projection must fail closed")

    def _ensure_capacity(self) -> None:
        if self._max_records is not None and len(self._records) >= self._max_records:
            raise MemoryError("in-memory idempotency store capacity exceeded")

    def _guard_transition(
        self,
        current: IdempotencyRecord | None,
        expected_state: IdempotencyRecordState,
    ) -> IdempotencyTransitionResult | None:
        if current is None:
            return IdempotencyTransitionResult(
                accepted=False,
                reason_code=IdempotencyResultReason.NOT_FOUND,
                record=None,
            )
        if current.failure_reason == IdempotencyFailureReason.POISON_PILL:
            return IdempotencyTransitionResult(
                accepted=False,
                reason_code=IdempotencyResultReason.TERMINAL_POISON_PILL,
                record=current,
            )
        if current.state != expected_state:
            return IdempotencyTransitionResult(
                accepted=False,
                reason_code=IdempotencyResultReason.WRONG_STATE,
                record=current,
            )
        return None

    def _record_with_same_correlation(self, correlation_id: str) -> IdempotencyRecord | None:
        for record in self._records.values():
            if record.key.correlation_id == correlation_id:
                return record
        return None

    def _terminal_poison_for_correlation(self, correlation_id: str) -> IdempotencyRecord | None:
        for record in self._records.values():
            if (
                record.key.correlation_id == correlation_id
                and record.failure_reason == IdempotencyFailureReason.POISON_PILL
            ):
                return record
        return None

    def _emit_then_commit(
        self,
        *,
        key: IdempotencyKey,
        next_record: IdempotencyRecord,
        event_type: str,
        reason_code: str,
        from_state: str,
        to_state: str,
        detail: dict[str, object],
    ) -> None:
        event = AS2AuditEvent(
            event_type=event_type,
            correlation_id=key.correlation_id,
            reason_code=reason_code,
            from_state=from_state,
            to_state=to_state,
            previous_state_hash=self._last_audit_hash,
            detail=detail,
        )
        # Audit first. If this raises, the in-memory state below is untouched.
        self._audit_sink.record(event)
        self._records[key] = next_record
        self._last_audit_hash = event.record_hash()


__all__ = [
    "IdempotencyFailureReason",
    "IdempotencyKey",
    "IdempotencyRecord",
    "IdempotencyRecordState",
    "IdempotencyReservationResult",
    "IdempotencyResultReason",
    "IdempotencyStoreUnavailable",
    "IdempotencyTransitionResult",
    "InMemoryIdempotencyStore",
]
