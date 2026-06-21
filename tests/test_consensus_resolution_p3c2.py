from copy import deepcopy

import pytest

from synapse import Interpreter, RuntimeMode
from synapse.ast import Literal, SuspendExpr
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource
from synapse.runtime.consensus_ticket_resolution import (
    RESOLUTION_EVENT_TYPE,
    RESOLUTION_KIND,
    RESOLUTION_SCHEMA_VERSION,
)


def _ticket(*, topic="topic", statement_identity="source:1:1"):
    decision = ConsensusEngine().decide(
        ConsensusRequest(
            topic=topic,
            participants=["A", "B"],
            quorum=2,
            statement_identity=statement_identity,
            vote_source=ExplicitVoteSource({"A": "yes"}),
        )
    )
    return decision.ticket_payload


def _request(ticket):
    return {
        "kind": RESOLUTION_KIND,
        "ticket_id": ticket["ticket_id"],
        "missing_participants": ticket["missing_participants"],
        "votes_hash": ticket["votes_hash"],
    }


def _signal(ticket, votes=None):
    return {
        "kind": RESOLUTION_KIND,
        "ticket_id": ticket["ticket_id"],
        "votes": votes if votes is not None else {"B": "yes"},
    }


def _pending_interpreter(ticket):
    interpreter = Interpreter()
    interpreter._project_consensus_ticket(ticket["ticket_id"], ticket)
    return interpreter


def _suspend(interpreter, request):
    return interpreter.suspend_expression(SuspendExpr(request=Literal(value=request)), interpreter.global_env)


def _send(generator, signal):
    with pytest.raises(StopIteration) as completed:
        generator.send(signal)
    return completed.value.value


def _resolution_event(interpreter):
    return next(event for event in interpreter.execution_history if event.get("type") == RESOLUTION_EVENT_TYPE)


def test_live_resolution_uses_existing_suspend_boundary_and_returns_signal():
    ticket = _ticket()
    interpreter = _pending_interpreter(ticket)
    signal = _signal(ticket)
    generator = _suspend(interpreter, _request(ticket))
    suspension = next(generator)

    assert suspension.reason == "awaiting_external_signal"
    assert suspension.payload == {"promise_id": suspension.payload["promise_id"], "request": _request(ticket)}
    assert _send(generator, signal) == signal
    event = _resolution_event(interpreter)
    assert set(event) == {
        "type", "schema_version", "ticket_id", "proposal_id", "statement_identity",
        "resolution_votes", "votes_final", "vote_counts_final", "outcome", "reason",
        "votes_hash_final", "result_hash_final",
    }
    assert event["schema_version"] == RESOLUTION_SCHEMA_VERSION
    assert event["ticket_id"] == ticket["ticket_id"]
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "resolved"
    assert [event["type"] for event in interpreter.execution_history] == [
        "promise_created", "promise_resolved", RESOLUTION_EVENT_TYPE,
    ]


def test_generic_suspend_behavior_is_unchanged():
    interpreter = Interpreter()
    generator = _suspend(interpreter, {"kind": "generic"})
    next(generator)
    signal = {"answer": "ok"}
    assert _send(generator, signal) == signal
    assert [event["type"] for event in interpreter.execution_history] == ["promise_created", "promise_resolved"]


@pytest.mark.parametrize(
    "request_mutation",
    [
        lambda request: request.__setitem__("extra", True),
        lambda request: request.__setitem__("missing_participants", ["A"]),
        lambda request: request.__setitem__("votes_hash", "sha256:wrong"),
        lambda request: request.__setitem__(1, "bad"),
    ],
)
def test_invalid_consensus_request_fails_before_promise_created(request_mutation):
    ticket = _ticket()
    interpreter = _pending_interpreter(ticket)
    request = _request(ticket)
    request_mutation(request)

    with pytest.raises(RuntimeError, match="consensus_ticket_resolution"):
        next(_suspend(interpreter, request))
    assert interpreter.execution_history == []


def test_unknown_ticket_request_fails_before_promise_created():
    ticket = _ticket()
    interpreter = Interpreter()

    with pytest.raises(RuntimeError, match="ticket_not_found"):
        next(_suspend(interpreter, _request(ticket)))
    assert interpreter.execution_history == []


@pytest.mark.parametrize(
    "signal_mutation",
    [
        lambda signal: signal.__setitem__("ticket_id", "sha256:wrong"),
        lambda signal: signal.__setitem__("votes", {"B": "missing"}),
        lambda signal: signal.__setitem__("votes", {}),
        lambda signal: signal.__setitem__("votes", {"A": "yes", "B": "yes"}),
        lambda signal: signal.__setitem__("votes", {"B": "maybe"}),
        lambda signal: signal.__setitem__("extra", True),
        lambda signal: signal.__setitem__(1, "bad"),
    ],
)
def test_invalid_consensus_signal_fails_before_promise_resolved(signal_mutation):
    ticket = _ticket()
    interpreter = _pending_interpreter(ticket)
    generator = _suspend(interpreter, _request(ticket))
    next(generator)
    signal = _signal(ticket)
    signal_mutation(signal)

    with pytest.raises(RuntimeError, match="consensus_ticket_resolution"):
        generator.send(signal)
    assert [event["type"] for event in interpreter.execution_history] == ["promise_created"]
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"


def test_identical_duplicate_is_noop_and_conflicting_duplicate_fails_closed():
    ticket = _ticket()
    interpreter = _pending_interpreter(ticket)
    signal = _signal(ticket)
    generator = _suspend(interpreter, _request(ticket))
    next(generator)
    _send(generator, signal)
    before = deepcopy(interpreter.consensus_tickets[ticket["ticket_id"]])
    resolution_count = len([event for event in interpreter.execution_history if event.get("type") == RESOLUTION_EVENT_TYPE])

    duplicate = _suspend(interpreter, _request(ticket))
    next(duplicate)
    assert _send(duplicate, signal) == signal
    assert len([event for event in interpreter.execution_history if event.get("type") == RESOLUTION_EVENT_TYPE]) == resolution_count
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == before

    conflicting = _suspend(interpreter, _request(ticket))
    next(conflicting)
    with pytest.raises(RuntimeError, match="conflicting_duplicate"):
        conflicting.send(_signal(ticket, {"B": "no"}))
    assert len([event for event in interpreter.execution_history if event.get("type") == RESOLUTION_EVENT_TYPE]) == resolution_count
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == before


def test_multiple_tickets_resolve_independently():
    first = _ticket(topic="one", statement_identity="source:1:1")
    second = _ticket(topic="two", statement_identity="source:2:1")
    interpreter = _pending_interpreter(first)
    interpreter._project_consensus_ticket(second["ticket_id"], second)

    first_suspend = _suspend(interpreter, _request(first))
    next(first_suspend)
    _send(first_suspend, _signal(first, {"B": "no"}))
    assert interpreter.consensus_tickets[first["ticket_id"]]["projection_state"] == "resolved"
    assert interpreter.consensus_tickets[second["ticket_id"]]["projection_state"] == "pending"
    second_suspend = _suspend(interpreter, _request(second))
    next(second_suspend)
    _send(second_suspend, _signal(second))
    assert interpreter.consensus_tickets[second["ticket_id"]]["projection_state"] == "resolved"


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda event: event.__setitem__("schema_version", "wrong"), "event_schema_version"),
        (lambda event: event.__setitem__("extra", True), "event_schema"),
        (lambda event: event.pop("outcome"), "event_schema"),
        (lambda event: event.__setitem__(1, "bad"), "non_string_mapping_key"),
        (lambda event: event.__setitem__("votes_final", {"A": "yes", "B": "no"}), "resolution_votes_final"),
    ],
)
def test_replay_resolution_validates_event_and_rolls_back(mutate, match):
    ticket = _ticket()
    live = _pending_interpreter(ticket)
    generator = _suspend(live, _request(ticket))
    next(generator)
    signal = _signal(ticket)
    _send(generator, signal)
    history = deepcopy(live.execution_history)
    mutate(history[-1])

    replay = _pending_interpreter(ticket)
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    generator = _suspend(replay, _request(ticket))
    with pytest.raises(ConsensusReplayIntegrityError, match=match):
        next(generator)
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"


def test_replay_consumes_resolution_before_suspend_early_return():
    ticket = _ticket()
    live = _pending_interpreter(ticket)
    generator = _suspend(live, _request(ticket))
    next(generator)
    signal = _signal(ticket)
    _send(generator, signal)

    replay = _pending_interpreter(ticket)
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    generator = _suspend(replay, _request(ticket))
    with pytest.raises(StopIteration) as completed:
        next(generator)
    assert completed.value.value == signal
    assert replay.replay_cursor == 3
    assert replay.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "resolved"


def test_replay_missing_resolution_event_restores_cursor_and_projection():
    ticket = _ticket()
    live = _pending_interpreter(ticket)
    generator = _suspend(live, _request(ticket))
    next(generator)
    signal = _signal(ticket)
    _send(generator, signal)

    replay = _pending_interpreter(ticket)
    replay.execution_history = deepcopy(live.execution_history[:-1])
    replay.runtime_mode = RuntimeMode.REPLAY
    generator = _suspend(replay, _request(ticket))
    with pytest.raises(ConsensusReplayIntegrityError, match="missing_resolution_event"):
        next(generator)
    assert replay.replay_cursor == 1
    assert replay.runtime_mode == RuntimeMode.REPLAY
    assert replay.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"
