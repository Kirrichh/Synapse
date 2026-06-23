from copy import deepcopy

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource
from synapse.runtime.consensus_mailbox_collection import (
    ConsensusMailboxCollectionError,
    build_lifecycle_event,
    build_lifecycle_projection,
    compute_lifecycle_action_hash,
    validate_lifecycle_command,
    validate_lifecycle_transition,
)


SOURCE = 'receive { sender => msg { print(sender) } }'


def ticket():
    decision = ConsensusEngine().decide(ConsensusRequest(
        topic="life", participants=["A", "B"], quorum=2, statement_identity="source:lifecycle:1",
        vote_source=ExplicitVoteSource({"A": "yes"}),
    ))
    value = {key: deepcopy(decision.ticket_payload[key]) for key in decision.ticket_payload if key not in {"type", "schema_version"}}
    value["projection_state"] = "pending"
    return value


def command(value, kind="consensus_ticket_cancel", **changes):
    result = {
        "kind": kind,
        "schema_version": "consensus.ticket.cancel.v1" if kind.endswith("cancel") else "consensus.ticket.expire.v1",
        "ticket_id": value["ticket_id"], "proposal_id": value["proposal_id"],
        "statement_identity": value["statement_identity"], "coordinator": "global",
        "reason": None, "request_id": None, "action_id": "action-1",
    }
    result.update(changes)
    return result


def message(value):
    return {"sender": "external", "receiver": "global", "method": value["kind"], "args": [value], "payload": value}


def deliver(interpreter, value):
    flow = interpreter.interpret_async(compile_to_ast(SOURCE))
    next(flow)
    with pytest.raises(StopIteration):
        flow.send(message(value))


@pytest.mark.parametrize("kind,state", [("consensus_ticket_cancel", "cancelled"), ("consensus_ticket_expire", "expired")])
def test_pending_ticket_lifecycle_projection(kind, state):
    value = ticket()
    event = build_lifecycle_event(command(value, kind))
    projection = build_lifecycle_projection(value, event)
    assert projection["projection_state"] == state
    assert projection["terminal_kind"] == state
    assert projection["terminal_action_hash"] == compute_lifecycle_action_hash(command(value, kind))


def test_lifecycle_identity_and_schema_fail_closed():
    value = ticket()
    with pytest.raises(Exception):
        validate_lifecycle_command({**command(value), "expires_at": 1})
    with pytest.raises(Exception):
        validate_lifecycle_command({**command(value), "action_id": ""})
    with pytest.raises(Exception):
        validate_lifecycle_transition(command(value), None, [])
    with pytest.raises(Exception):
        validate_lifecycle_transition(command(value, proposal_id="sha256:" + "0" * 64), value, [])


def test_terminal_duplicates_and_cross_ticket_action_id_conflicts():
    value = ticket()
    first = command(value)
    event = build_lifecycle_event(first)
    terminal = build_lifecycle_projection(value, event)
    assert validate_lifecycle_transition(first, terminal, [event]) is True
    with pytest.raises(Exception):
        validate_lifecycle_transition(command(value, reason="different"), terminal, [event])
    with pytest.raises(Exception):
        validate_lifecycle_transition(command(value, "consensus_ticket_expire"), terminal, [event])
    other = ticket()
    other["ticket_id"] = "sha256:" + "1" * 64
    with pytest.raises(Exception):
        validate_lifecycle_transition(command(other), other, [event])


def test_mailbox_live_and_replay_terminal_event_paths():
    value = ticket()
    live = Interpreter()
    live.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    payload = command(value)
    deliver(live, payload)
    assert live.consensus_tickets[value["ticket_id"]]["projection_state"] == "cancelled"
    assert [event["type"] for event in live.execution_history] == ["message_received", "distributed_consensus_ticket_cancelled"]

    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    with pytest.raises(StopIteration):
        next(replay.interpret_async(compile_to_ast(SOURCE)))
    assert replay.replay_cursor == 2
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "cancelled"


def test_terminal_ticket_remains_rejected_by_vote_collection_and_import():
    value = ticket()
    terminal = build_lifecycle_projection(value, build_lifecycle_event(command(value)))
    from synapse.runtime.consensus_mailbox_collection import new_collection_projection, validate_pending_ticket_projection
    with pytest.raises(Exception):
        new_collection_projection(terminal, {})
    with pytest.raises(Exception):
        validate_pending_ticket_projection(terminal)
