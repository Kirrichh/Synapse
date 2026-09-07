"""Observation identity, gaps, cursors and authority-free projections."""
from dataclasses import FrozenInstanceError, replace
import pytest
from synapse.experiments.gold.stage15.events import GoldEvent, EventStream, EventType
from synapse.experiments.gold.stage15.telemetry import Phase, reference


def test_missing_phase_remains_visible_and_projection_has_no_authority():
    ref = reference({"evidence": "retained"}, "test.evidence/v1")
    events = []
    for phase in Phase:
        events.append(GoldEvent("run", "attempt", len(events) + 1, phase, EventType.COMPLETED,
            ref, ref, None if not events else events[-1].reference.sha256))
    stream = EventStream("run", ref, tuple(events), tuple(e for e in events if e.phase is not Phase.WORKER))
    projection = stream.projection()
    assert projection["missing_phases"] == [{"attempt_id": "attempt", "phase": "WORKER"}]
    assert projection["chain_integrity"] == "GAPS"
    assert projection["read_only"] is True
    page = stream.page(limit=2)
    assert stream.page(cursor=page["next_cursor"], limit=2)["events"][0]["sequence"] == 3
    with pytest.raises(ValueError):
        stream.page(cursor={"assessment_sha256": "0" * 64, "after_sequence": 2})
    with pytest.raises((FrozenInstanceError, AttributeError)):
        stream.observed = ()
    assert not any(hasattr(stream, action) for action in ("admit", "publish", "execute", "approve", "transition", "put"))
    projection["events"][0]["read_only"] = False
    assert stream.projection()["events"][0]["read_only"] is True
    with pytest.raises(ValueError):
        GoldEvent.from_dict(projection["events"][0])
    with pytest.raises(ValueError):
        EventStream("run", ref, tuple(events), (*events, events[0]))
    assert replace(events[0], subject_ref=reference({"changed": True}, "test.evidence/v1")).event_id != events[0].event_id
