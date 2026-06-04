"""AS2 Integration Harness skeleton (Alpha3g P0.6.38).

This module connects the approved AS2 skeletons in a controlled
``ENABLED_FOR_TEST`` integration path:

ProviderAggregator -> Host Pre-Stage bridge -> PreparedAS2Inputs hash ->
PersistentIdempotencyStore -> Projection Handoff -> idempotency terminal state.

Explicit non-goals for P0.6.38:
- no default runtime wiring changes;
- no production ENABLED activation;
- no concrete provider adapters;
- no network/file/database/queue I/O;
- no persistent storage backend or schema migration;
- no projection handoff mutation or dedup-index replacement;
- no direct gate-controller mutation;
- no new audit relay layer;
- no concurrent provider execution;
- no automatic STALE_IN_PROGRESS retry.

The harness requires explicit dependency injection. It accepts a deterministic
``clock`` for integration tests, but does not import ambient time directly.
Audit timestamps remain explicit caller data and are not generated here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Union

from synapse.agent_snapshot_bridge import PreparedAS2Inputs, prepare_as2_inputs_from_host_prestage
from synapse.canonical_service import stable_canonical_hash
from synapse.runtime.as2_idempotency_store import (
    IdempotencyFailureReason,
    IdempotencyKey,
    IdempotencyRecord,
    IdempotencyRecordState,
    IdempotencyReservationResult,
    IdempotencyResultReason,
    IdempotencyStoreUnavailable,
    InMemoryIdempotencyStore,
)
from synapse.runtime.as2_projection_handoff import (
    AS2ProjectionFailureReasonCode,
    AS2ProjectionHandoffResult,
    AS2ProjectionHandoffResultKind,
    AS2ProjectionHandoffSkeleton,
)
from synapse.runtime.as2_provider_aggregator import (
    AS2ProviderAggregatorSkeleton,
    AggregatorFailure,
    AggregatorSuccess,
)
from synapse.runtime.as2_provider_ports import HostProviderRequestContext, ProviderFailure

FailureScope = Literal["agent", "systemic", "infrastructure"]
BridgePrepareFunction = Callable[[Mapping[str, object]], PreparedAS2Inputs]


@dataclass(frozen=True)
class IntegrationSuccess:
    """Successful full pipeline execution with completed idempotency state."""

    prepared_inputs_hash: str
    idempotency_state: IdempotencyRecordState
    result_ref: Mapping[str, str | None]
    record: IdempotencyRecord


@dataclass(frozen=True)
class IntegrationProviderFailure:
    """Aggregator/provider-stage failure; no idempotency reservation was made."""

    failure: ProviderFailure
    scope: Literal["agent", "systemic"]


@dataclass(frozen=True)
class IntegrationFailure:
    """Pipeline failure after provider aggregation or infrastructure denial."""

    reason: str
    scope: FailureScope
    idempotency_state: IdempotencyRecordState | None
    record: IdempotencyRecord | None = None
    result_ref: Mapping[str, str | None] | None = None


@dataclass(frozen=True)
class IntegrationDuplicate:
    """Duplicate request with a completed or in-progress idempotency record."""

    idempotency_state: IdempotencyRecordState
    record: IdempotencyRecord
    result_ref: Mapping[str, str | None] | None


@dataclass(frozen=True)
class IntegrationPoisonPill:
    """Terminal poison-pill reuse of a correlation id with changed inputs."""

    correlation_id: str
    idempotency_state: IdempotencyRecordState
    record: IdempotencyRecord


IntegrationResult = Union[
    IntegrationSuccess,
    IntegrationProviderFailure,
    IntegrationFailure,
    IntegrationDuplicate,
    IntegrationPoisonPill,
]


class AS2IntegrationHarness:
    """Controlled production-facing integration harness under ENABLED_FOR_TEST.

    All collaborators are injected. ``enabled_for_test`` is an explicit harness
    policy switch, not a production feature activation and not a gate mutation.
    """

    def __init__(
        self,
        *,
        aggregator: AS2ProviderAggregatorSkeleton,
        idempotency_store: InMemoryIdempotencyStore,
        projection_handoff: AS2ProjectionHandoffSkeleton,
        enabled_for_test: bool,
        clock: Callable[[], float],
        bridge_prepare: BridgePrepareFunction = prepare_as2_inputs_from_host_prestage,
    ) -> None:
        self._aggregator = aggregator
        self._idempotency_store = idempotency_store
        self._projection_handoff = projection_handoff
        self._enabled_for_test = enabled_for_test
        self._clock = clock
        self._bridge_prepare = bridge_prepare

    def execute(
        self,
        context: HostProviderRequestContext,
        *,
        agent_id: str,
        correlation_id: str,
    ) -> IntegrationResult:
        """Run the controlled P0.6.38 full-pipeline integration flow."""

        _ = self._clock  # retained for deterministic integration wiring; no ambient calls here
        if not self._enabled_for_test:
            return IntegrationFailure(
                reason="gate_not_enabled_for_test",
                scope="infrastructure",
                idempotency_state=None,
            )

        aggregation = self._aggregator.aggregate(context, agent_id=agent_id)
        if isinstance(aggregation, AggregatorFailure):
            return IntegrationProviderFailure(failure=aggregation.failure, scope=aggregation.scope)
        assert isinstance(aggregation, AggregatorSuccess)

        try:
            prepared = self._bridge_prepare(aggregation.payload)
        except Exception as exc:
            return IntegrationFailure(
                reason=f"bridge_conversion_failed:{exc.__class__.__name__}",
                scope="systemic",
                idempotency_state=None,
            )

        prepared_hash = stable_canonical_hash(prepared.to_validate_kwargs())
        key = IdempotencyKey(correlation_id=correlation_id, prepared_inputs_hash=prepared_hash)

        try:
            reservation = self._idempotency_store.reserve_if_absent(key, prepared_hash)
        except IdempotencyStoreUnavailable:
            return IntegrationFailure(
                reason=IdempotencyResultReason.STORE_UNAVAILABLE.value,
                scope="infrastructure",
                idempotency_state=None,
            )
        except Exception as exc:
            return IntegrationFailure(
                reason=f"idempotency_reserve_failed:{exc.__class__.__name__}",
                scope="infrastructure",
                idempotency_state=None,
            )

        non_reserved = self._handle_non_reserved_reservation(reservation)
        if non_reserved is not None:
            return non_reserved

        projection = self._projection_handoff.execute_projection(
            prepared,
            correlation_id=correlation_id,
            request_id=context.request_id,
        )
        if projection.kind is AS2ProjectionHandoffResultKind.COMPLETED:
            return self._complete_success(key=key, prepared_hash=prepared_hash, projection=projection)

        return self._fail_projection(key=key, projection=projection)

    def _handle_non_reserved_reservation(
        self,
        reservation: IdempotencyReservationResult,
    ) -> IntegrationResult | None:
        if reservation.accepted:
            return None
        record = reservation.record
        if record is None:
            return IntegrationFailure(
                reason=reservation.reason_code.value,
                scope="infrastructure",
                idempotency_state=None,
            )
        if reservation.reason_code in (
            IdempotencyResultReason.POISON_PILL,
            IdempotencyResultReason.TERMINAL_POISON_PILL,
        ):
            return IntegrationPoisonPill(
                correlation_id=record.key.correlation_id,
                idempotency_state=record.state,
                record=record,
            )
        if reservation.reason_code is IdempotencyResultReason.DUPLICATE:
            return IntegrationDuplicate(
                idempotency_state=record.state,
                record=record,
                result_ref=_record_result_ref(record),
            )
        return IntegrationFailure(
            reason=reservation.reason_code.value,
            scope="infrastructure",
            idempotency_state=record.state,
            record=record,
        )

    def _complete_success(
        self,
        *,
        key: IdempotencyKey,
        prepared_hash: str,
        projection: AS2ProjectionHandoffResult,
    ) -> IntegrationResult:
        snapshot_hash = projection.snapshot_hash or ""
        derivation_hash = projection.derivation_record_hash or ""
        try:
            transition = self._idempotency_store.complete_if_state(
                key,
                expected_state=IdempotencyRecordState.IN_PROGRESS,
                snapshot_hash=snapshot_hash,
                derivation_record_hash=derivation_hash,
            )
        except Exception as exc:
            current = self._idempotency_store.inspect(key)
            return IntegrationFailure(
                reason=f"idempotency_complete_failed:{exc.__class__.__name__}",
                scope="infrastructure",
                idempotency_state=None if current is None else current.state,
                record=current,
                result_ref=_record_result_ref(current),
            )
        if not transition.accepted or transition.record is None:
            return IntegrationFailure(
                reason=transition.reason_code.value,
                scope="infrastructure",
                idempotency_state=None if transition.record is None else transition.record.state,
                record=transition.record,
                result_ref=_record_result_ref(transition.record),
            )
        result_ref = _record_result_ref(transition.record)
        if result_ref is None:
            result_ref = {}
        return IntegrationSuccess(
            prepared_inputs_hash=prepared_hash,
            idempotency_state=transition.record.state,
            result_ref=result_ref,
            record=transition.record,
        )

    def _fail_projection(
        self,
        *,
        key: IdempotencyKey,
        projection: AS2ProjectionHandoffResult,
    ) -> IntegrationFailure:
        reason = _projection_failure_to_idempotency_reason(projection.reason_code)
        try:
            transition = self._idempotency_store.fail_if_state(
                key,
                expected_state=IdempotencyRecordState.IN_PROGRESS,
                reason=reason,
            )
        except Exception as exc:
            current = self._idempotency_store.inspect(key)
            return IntegrationFailure(
                reason=f"idempotency_fail_failed:{exc.__class__.__name__}",
                scope="infrastructure",
                idempotency_state=None if current is None else current.state,
                record=current,
            )
        return IntegrationFailure(
            reason=_projection_reason_value(projection.reason_code),
            scope=_projection_scope(projection),
            idempotency_state=None if transition.record is None else transition.record.state,
            record=transition.record,
            result_ref=_record_result_ref(transition.record),
        )


def _record_result_ref(record: IdempotencyRecord | None) -> Mapping[str, str | None] | None:
    if record is None:
        return None
    if record.snapshot_hash is None and record.derivation_record_hash is None:
        return None
    return {
        "snapshot_hash": record.snapshot_hash,
        "derivation_record_hash": record.derivation_record_hash,
    }


def _projection_reason_value(reason_code: AS2ProjectionFailureReasonCode | str | None) -> str:
    if isinstance(reason_code, AS2ProjectionFailureReasonCode):
        return reason_code.value
    if reason_code is None:
        return "projection_failed"
    return str(reason_code)


def _projection_failure_to_idempotency_reason(
    reason_code: AS2ProjectionFailureReasonCode | str | None,
) -> IdempotencyFailureReason:
    if reason_code is AS2ProjectionFailureReasonCode.SYSTEMIC_CORE_FAILURE_INTERRUPTED:
        return IdempotencyFailureReason.PROJECTION_INTERRUPTED
    return IdempotencyFailureReason.SYSTEMIC_CORE_FAILURE


def _projection_scope(projection: AS2ProjectionHandoffResult) -> FailureScope:
    if projection.kind is AS2ProjectionHandoffResultKind.AGENT_QUARANTINE:
        return "agent"
    return "systemic"


__all__ = [
    "AS2IntegrationHarness",
    "IntegrationDuplicate",
    "IntegrationFailure",
    "IntegrationPoisonPill",
    "IntegrationProviderFailure",
    "IntegrationResult",
    "IntegrationSuccess",
]
