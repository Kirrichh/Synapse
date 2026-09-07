"""Closed-vocabulary read-only events and deterministic retained projections.

Events report domain evidence; they never complete a domain phase. Payloads
remain external hash-bound artifacts. A retained assessment fixes the expected
sequence and identities, so deletion, gaps and stale cursors remain visible.
Projection time is not misrepresented as execution time.
"""

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path

from ..canonicalization import HashBoundRef
from ..admission_journal import FileSnapshotFence
from ..runner.records import RunRecordStore, RecordKind
from .telemetry import Phase, canonical, exact_fields, identifier, identity, reference

EVENT_SCHEMA = "synapse.stage4.gold.event/v1"
EVENT_STREAM_SCHEMA = "synapse.stage4.gold.event-stream/v1"


class EventType(str, Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    REFUSED = "REFUSED"
    NOT_REACHED = "NOT_REACHED"
    GAP = "GAP"


@dataclass(frozen=True)
class GoldEvent:
    run_id: str
    attempt_id: str
    sequence: int
    phase: Phase
    event_type: EventType
    subject_ref: HashBoundRef
    payload_ref: HashBoundRef
    previous_event_hash: str | None

    def __post_init__(self):
        identifier(self.run_id)
        identifier(self.attempt_id)
        if type(self.sequence) is not int or not 1 <= self.sequence <= 100_000:
            raise ValueError("event sequence is outside its bounded run")
        if type(self.phase) is not Phase or type(self.event_type) is not EventType:
            raise TypeError("event vocabulary must be exact")
        if type(self.subject_ref) is not HashBoundRef or type(self.payload_ref) is not HashBoundRef:
            raise TypeError("events contain typed references, never inline payloads")
        if self.sequence == 1 and self.previous_event_hash is not None or self.sequence > 1 and (
                type(self.previous_event_hash) is not str or len(self.previous_event_hash) != 64
                or any(c not in "0123456789abcdef" for c in self.previous_event_hash)):
            raise ValueError("event predecessor must bind the complete sequence")

    def payload(self):
        return {"schema_version": EVENT_SCHEMA, "run_id": self.run_id, "attempt_id": self.attempt_id,
            "sequence": self.sequence, "phase": self.phase.value, "event_type": self.event_type.value,
            "subject_ref": self.subject_ref.to_dict(), "payload_ref": self.payload_ref.to_dict(),
            "timestamp_unix_ns": None, "clock_source": "DURABLE_SOURCE_NO_EVENT_CLOCK",
            "projection": "DERIVED_FROM_RETAINED_RECORDS", "previous_event_hash": self.previous_event_hash,
            "read_only": True}

    @property
    def event_id(self):
        return identity("synapse.gold-event/v1", self.payload())

    def to_dict(self):
        return {**self.payload(), "event_id": self.event_id}

    @property
    def reference(self):
        return reference(self.to_dict(), EVENT_SCHEMA)

    @classmethod
    def from_dict(cls, value):
        v = exact_fields(value, {"schema_version", "run_id", "attempt_id", "sequence", "phase", "event_type",
            "subject_ref", "payload_ref", "timestamp_unix_ns", "clock_source", "projection", "previous_event_hash",
            "read_only", "event_id"})
        result = cls(v["run_id"], v["attempt_id"], v["sequence"], Phase(v["phase"]), EventType(v["event_type"]),
            HashBoundRef.from_dict(v["subject_ref"]), HashBoundRef.from_dict(v["payload_ref"]), v["previous_event_hash"])
        if result.to_dict() != v:
            raise ValueError("event identity or read-only contract changed")
        return result


@dataclass(frozen=True)
class EventStream:
    run_id: str
    assessment_ref: HashBoundRef
    expected: tuple[GoldEvent, ...]
    observed: tuple[GoldEvent, ...]

    def __post_init__(self):
        identifier(self.run_id)
        if type(self.assessment_ref) is not HashBoundRef or type(self.expected) is not tuple or type(self.observed) is not tuple:
            raise TypeError("stream requires exact immutable evidence")
        previous = None
        for seq, event in enumerate(self.expected, 1):
            if type(event) is not GoldEvent or event.run_id != self.run_id or event.sequence != seq or event.previous_event_hash != previous:
                raise ValueError("expected event inventory has an invalid chain")
            previous = event.reference.sha256
        seen = set()
        for event in self.observed:
            if type(event) is not GoldEvent or event.sequence in seen or event.sequence > len(self.expected) or self.expected[event.sequence - 1] != event:
                raise ValueError("observed stream has a duplicate, foreign or changed event")
            seen.add(event.sequence)

    def projection(self):
        observed = {event.sequence: event for event in self.observed}
        missing = [event for event in self.expected if event.sequence not in observed or event.event_type is EventType.GAP]
        return {"schema_version": EVENT_STREAM_SCHEMA, "run_id": self.run_id,
            "assessment_ref": self.assessment_ref.to_dict(), "read_only": True,
            "chain_integrity": "COMPLETE" if len(observed) == len(self.expected) else "GAPS",
            "expected_count": len(self.expected), "observed_count": len(observed),
            "missing_sequences": [event.sequence for event in self.expected if event.sequence not in observed],
            "missing_phases": [{"attempt_id": event.attempt_id, "phase": event.phase.value} for event in missing],
            "events": [event.to_dict() for event in sorted(self.observed, key=lambda item: item.sequence)]}

    def page(self, *, cursor=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("event page limit is outside its bound")
        after = 0
        if cursor is not None:
            exact_fields(cursor, {"assessment_sha256", "after_sequence"})
            if cursor["assessment_sha256"] != self.assessment_ref.sha256:
                raise ValueError("cursor belongs to another retained assessment")
            after = cursor["after_sequence"]
            if type(after) is not int or not 0 <= after <= len(self.expected):
                raise ValueError("cursor has an invalid sequence")
        end = min(len(self.expected), after + limit)
        value = self.projection()
        value["events"] = [event for event in value["events"] if after < event["sequence"] <= end]
        value["next_cursor"] = None if end == len(self.expected) else {"assessment_sha256": self.assessment_ref.sha256, "after_sequence": end}
        return value


def open_event_stream(*, run_root: Path, assessment_key: str) -> EventStream:
    """Only read methods are exposed to an event/UI consumer."""
    store = RunRecordStore(run_root, mutation_fence=FileSnapshotFence(run_root / "run-coordinator", read_only=True), read_only=True)
    assessment = store.get(kind=RecordKind.OBSERVABILITY_MANIFEST, key=assessment_key)
    if assessment is None:
        raise ValueError("requested event assessment is absent")
    body = assessment.payload
    expected = tuple(GoldEvent.from_dict(item) for item in body["event_inventory"])
    observed = []
    for event in expected:
        record = store.get(kind=RecordKind.GOLD_EVENT, key=event.event_id)
        if record is not None:
            observed.append(GoldEvent.from_dict(record.payload))
    return EventStream(body["run_id"], reference(body, body["schema_version"]), expected, tuple(observed))
