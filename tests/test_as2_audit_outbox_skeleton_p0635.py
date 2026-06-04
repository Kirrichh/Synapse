"""P0.6.35 tests for the AS2 in-memory audit outbox skeleton."""
from __future__ import annotations

from dataclasses import replace

import pytest

from synapse.runtime.as2_audit_outbox import (
    AuditChainValidationResult,
    AuditChainViolation,
    CHAIN_START,
    InMemoryOutboxAuditSink,
    OUTBOX_ENVELOPE_SCHEMA_VERSION,
    OutboxCapacityExceeded,
    validate_chain_link,
)
from synapse.runtime.as2_audit_sink import AS2AuditEvent, NoOpAuditSink
from synapse.runtime.as2_gate_controller import AS2GateControllerSkeleton
from synapse.runtime.as2_projection_handoff import AS2ProjectionHandoffSkeleton


def _event(
    *,
    previous_state_hash: str | None = CHAIN_START,
    event_type: str = "gate_transition",
    correlation_id: str = "corr-p0635",
    reason_code: str = "test_reason",
    detail: dict[str, object] | None = None,
) -> AS2AuditEvent:
    return AS2AuditEvent(
        event_type=event_type,
        correlation_id=correlation_id,
        reason_code=reason_code,
        from_state="from_state",
        to_state="to_state",
        previous_state_hash=previous_state_hash,
        request_id="req-p0635",
        detail=detail or {"stage": "p0635"},
    )


def test_outbox_sink_appends_events_in_order() -> None:
    sink = InMemoryOutboxAuditSink()
    first = _event(previous_state_hash=CHAIN_START, correlation_id="corr-order")
    first_result = sink.append(first)
    second = _event(previous_state_hash=first.record_hash(), correlation_id="corr-order")
    second_result = sink.append(second)

    envelopes = sink.envelopes

    assert first_result.accepted is True
    assert second_result.accepted is True
    assert [envelope.sequence_number for envelope in envelopes] == [0, 1]
    assert [envelope.payload for envelope in envelopes] == [first, second]


def test_outbox_sink_preserves_payload_hash() -> None:
    sink = InMemoryOutboxAuditSink()
    event = _event(previous_state_hash=CHAIN_START)
    before = event.record_hash()

    sink.append(event)

    assert sink.envelopes[0].payload.record_hash() == before


def test_outbox_envelope_metadata_not_in_record_hash() -> None:
    sink = InMemoryOutboxAuditSink()
    event = _event(previous_state_hash=CHAIN_START)
    sink.append(event, wall_clock_timestamp="2026-06-02T12:00:00Z", ingestion_node_id="node-a")
    original_envelope = sink.envelopes[0]
    shifted_envelope = replace(
        original_envelope,
        wall_clock_timestamp="2026-06-02T12:00:01Z",
        relay_attempt_count=original_envelope.relay_attempt_count + 1,
        partition_key="different-partition",
    )

    assert original_envelope.payload.record_hash() == shifted_envelope.payload.record_hash()
    assert original_envelope.payload.record_hash() == event.record_hash()


def test_chain_start_first_record_semantics() -> None:
    event = _event(previous_state_hash=CHAIN_START)

    assert (
        validate_chain_link(previous_envelope=None, current_payload=event)
        is AuditChainValidationResult.VALID_CHAIN_START
    )
    assert event.previous_state_hash == CHAIN_START
    assert event.previous_state_hash is not None


def test_previous_state_hash_links_records() -> None:
    sink = InMemoryOutboxAuditSink()
    first = _event(previous_state_hash=CHAIN_START, correlation_id="corr-chain")
    sink.append(first)
    second = _event(previous_state_hash=first.record_hash(), correlation_id="corr-chain")
    sink.append(second)

    assert sink.envelopes[1].payload.previous_state_hash == sink.envelopes[0].payload.record_hash()
    assert (
        validate_chain_link(previous_envelope=sink.envelopes[0], current_payload=second)
        is AuditChainValidationResult.VALID_LINK
    )


def test_noop_audit_sink_remains_default() -> None:
    controller = AS2GateControllerSkeleton()
    handoff = AS2ProjectionHandoffSkeleton(controller=controller)

    assert isinstance(controller._audit_sink, NoOpAuditSink)  # noqa: SLF001 - default-behavior guard
    assert isinstance(handoff._audit_sink, NoOpAuditSink)  # noqa: SLF001 - default-behavior guard


def test_outbox_sink_stable_event_id_for_same_payload_and_sequence() -> None:
    event = _event(previous_state_hash=CHAIN_START, correlation_id="corr-event-id")
    first_sink = InMemoryOutboxAuditSink()
    second_sink = InMemoryOutboxAuditSink()

    first = first_sink.append(event)
    second = second_sink.append(event)

    assert first.sequence_number == second.sequence_number == 0
    assert first.event_id == second.event_id


def test_outbox_sink_schema_version_carried_in_envelope() -> None:
    sink = InMemoryOutboxAuditSink()

    sink.append(_event(previous_state_hash=CHAIN_START))

    assert sink.envelopes[0].schema_version == OUTBOX_ENVELOPE_SCHEMA_VERSION
    assert sink.envelopes[0].schema_version == "v1"


def test_none_vs_chain_start_distinction() -> None:
    chain_start = _event(previous_state_hash=CHAIN_START)
    missing_previous = _event(previous_state_hash=None)
    broken_previous = _event(previous_state_hash="not-the-previous-hash")

    assert (
        validate_chain_link(previous_envelope=None, current_payload=chain_start)
        is AuditChainValidationResult.VALID_CHAIN_START
    )
    assert (
        validate_chain_link(previous_envelope=None, current_payload=missing_previous)
        is AuditChainValidationResult.MISSING_PREVIOUS
    )
    assert (
        validate_chain_link(previous_envelope=None, current_payload=broken_previous)
        is AuditChainValidationResult.BROKEN_PREVIOUS_HASH
    )

    sink = InMemoryOutboxAuditSink()
    with pytest.raises(AuditChainViolation):
        sink.append(missing_previous)


def test_bounded_queue_fail_closed_for_critical_events() -> None:
    sink = InMemoryOutboxAuditSink(max_entries=1)
    first = _event(previous_state_hash=CHAIN_START, event_type="projection_completed")
    sink.append(first)
    second = _event(previous_state_hash=first.record_hash(), event_type="projection_completed")

    with pytest.raises(OutboxCapacityExceeded):
        sink.append(second)


def test_bounded_queue_drops_diagnostic_events() -> None:
    sink = InMemoryOutboxAuditSink(max_entries=1)
    first = _event(previous_state_hash=CHAIN_START, event_type="gate_transition")
    sink.append(first)
    diagnostic = _event(previous_state_hash=first.record_hash(), event_type="observe")

    result = sink.append(diagnostic)

    assert result.accepted is False
    assert result.reason_code == "DROPPED_DIAGNOSTIC_OUTBOX_FULL"
    assert len(sink) == 1


def test_sequence_numbers_are_monotonic() -> None:
    sink = InMemoryOutboxAuditSink()
    first = _event(previous_state_hash=CHAIN_START, correlation_id="corr-seq")
    sink.append(first)
    second = _event(previous_state_hash=first.record_hash(), correlation_id="corr-seq")
    sink.append(second)
    third = _event(previous_state_hash=second.record_hash(), correlation_id="corr-seq")
    sink.append(third)

    assert [envelope.sequence_number for envelope in sink.envelopes] == [0, 1, 2]
