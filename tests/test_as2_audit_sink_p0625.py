"""P0.6.25 tests for AS2 audit sink skeleton."""
from __future__ import annotations

from dataclasses import dataclass, field

from synapse.runtime.as2_audit_sink import AS2AuditEvent, NoOpAuditSink


@dataclass
class CapturingAuditSink:
    events: list[AS2AuditEvent] = field(default_factory=list)

    def record(self, event: AS2AuditEvent) -> None:
        self.events.append(event)


def test_p0625_audit_record_hash_is_deterministic() -> None:
    record = AS2AuditEvent(
        event_type="test_event",
        correlation_id="corr-001",
        reason_code="test_reason",
        from_state="disabled_by_default",
        to_state="disabled_by_default",
        previous_state_hash=None,
        request_id="request-001",
        detail={"k": "v"},
    )

    assert record.record_hash() == record.record_hash()


def test_p0625_audit_record_hash_excludes_explicit_timestamp_until_audit_decision() -> None:
    base = AS2AuditEvent(
        event_type="test_event",
        correlation_id="corr-001",
        reason_code="test_reason",
        from_state="disabled_by_default",
        to_state="disabled_by_default",
        previous_state_hash=None,
        timestamp="2026-06-01T12:00:00Z",
    )
    shifted = AS2AuditEvent(
        event_type="test_event",
        correlation_id="corr-001",
        reason_code="test_reason",
        from_state="disabled_by_default",
        to_state="disabled_by_default",
        previous_state_hash=None,
        timestamp="2026-06-01T12:00:01Z",
    )

    assert base.record_hash() == shifted.record_hash()
    assert "timestamp" not in base.to_hash_payload()


def test_p0625_noop_audit_sink_performs_no_io_or_mutation() -> None:
    sink = NoOpAuditSink()
    event = AS2AuditEvent(
        event_type="test_event",
        correlation_id="corr-001",
        reason_code="test_reason",
        from_state="disabled_by_default",
        to_state="disabled_by_default",
    )

    assert sink.record(event) is None
    assert not hasattr(sink, "events")


def test_p0625_capturing_sink_is_test_only_fixture_not_production_default() -> None:
    sink = CapturingAuditSink()
    event = AS2AuditEvent(
        event_type="test_event",
        correlation_id="corr-001",
        reason_code="test_reason",
        from_state="disabled_by_default",
        to_state="disabled_by_default",
    )

    sink.record(event)

    assert sink.events == [event]
