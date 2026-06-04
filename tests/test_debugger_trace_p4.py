from __future__ import annotations

from typing import Mapping

import pytest

from synapse.debugger_core import (
    EventInjectionValidator,
    ForkRegistry,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
    ReplayRuntimeStub,
    TraceContextProtocol,
)
from synapse.golden_replay import DeterministicReplayError


class DuckTrace:
    def __init__(self, events):
        self.events = tuple(events)
        self.index = 0

    def next_expected_event(self) -> Mapping | None:
        if self.index >= len(self.events):
            return None
        return self.events[self.index]

    def consume_expected_event(self) -> None:
        self.index += 1


def test_protocol_structural_compliance():
    trace = DuckTrace([{"type": "GUARD_ENTER", "guard_hash": "guard-a"}])
    assert isinstance(trace, TraceContextProtocol)

    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)
    result = EventInjectionValidator(registry).validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "guard-a"},
        trace_context=trace,
    )

    assert result.allowed is True
    assert trace.index == 1


def test_stub_cursor_advances_without_mutation():
    parent_history = [{"type": "GUARD_ENTER", "guard_hash": "guard-a"}]
    stub = ReplayRuntimeStub(parent_history)

    assert stub.cursor == 0
    assert stub.next_expected_event() == {"type": "GUARD_ENTER", "guard_hash": "guard-a"}
    stub.consume_expected_event()

    assert stub.cursor == 1
    assert parent_history == [{"type": "GUARD_ENTER", "guard_hash": "guard-a"}]
    assert stub.execution_history == ({"type": "GUARD_ENTER", "guard_hash": "guard-a"},)


def test_stub_returns_none_at_end_of_trace():
    stub = ReplayRuntimeStub([])

    assert stub.next_expected_event() is None


def test_consume_at_end_of_trace_raises_deterministic_replay_error():
    stub = ReplayRuntimeStub([])

    with pytest.raises(DeterministicReplayError, match="end of deterministic trace"):
        stub.consume_expected_event()


def test_deterministic_replay_strict_consumption():
    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)
    stub = ReplayRuntimeStub([{"type": "GUARD_ENTER", "guard_hash": "guard-a"}])
    validator = EventInjectionValidator(registry)

    allowed = validator.validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "guard-a"},
        trace_context=stub,
    )
    assert allowed.reason == "recorded guard replay"
    assert stub.cursor == 1

    with pytest.raises(DeterministicReplayError, match="missing recorded GUARD_ENTER"):
        validator.validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "guard-b"},
            trace_context=stub,
        )


def test_exploratory_live_ignores_trace():
    class ExplodingTrace:
        def next_expected_event(self):  # pragma: no cover - must not be called
            raise AssertionError("exploratory-live must ignore trace")

        def consume_expected_event(self):  # pragma: no cover - must not be called
            raise AssertionError("exploratory-live must ignore trace")

    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_EXPLORATORY_LIVE)

    result = EventInjectionValidator(registry).validate_injection(
        fork_id=fork.fork_id,
        event_type="GUARD_ENTER",
        payload={"guard_hash": "new-guard", "scope": "fork-local"},
        trace_context=ExplodingTrace(),
    )

    assert result.allowed is True
    assert result.reason == "fork-local guard evaluation path"


def test_validator_typed_against_protocol():
    annotations = EventInjectionValidator.validate_injection.__annotations__
    assert annotations["trace_context"] == "Optional[TraceContextProtocol]"


def test_malformed_trace_handling():
    with pytest.raises(DeterministicReplayError, match="malformed trace event"):
        ReplayRuntimeStub(["not-a-mapping"])

    class MalformedTrace:
        def next_expected_event(self):
            return "not-a-mapping"

        def consume_expected_event(self):
            raise AssertionError("consume must not be called on malformed event")

    registry = ForkRegistry()
    fork = registry.create_fork(parent_history_hash="sha256:parent", mode=REPLAY_DETERMINISTIC)

    with pytest.raises(DeterministicReplayError, match="must return mapping or None"):
        EventInjectionValidator(registry).validate_injection(
            fork_id=fork.fork_id,
            event_type="GUARD_ENTER",
            payload={"guard_hash": "guard-a"},
            trace_context=MalformedTrace(),
        )
