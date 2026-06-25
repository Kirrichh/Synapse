"""P3c-N2 fresh consensus mailbox request delivery contract tests."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource
from synapse.runtime.consensus_mailbox_collection import (
    IMPORT_EVENT_TYPE,
    VOTE_RECEIVED_EVENT_TYPE,
    build_ticket_import_event,
    ConsensusVoteRequestError,
    imported_ticket_from_event,
)
from synapse.runtime.consensus_vote_request_delivery import (
    REQUEST_EVENT_TYPE,
    compute_request_hash,
    compute_request_id,
    validate_vote_request_event,
    validate_vote_request_message,
)


FRESH_SOURCE = """
agent Peer { model "mock" }
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    timeout 30
    policy "MajorityVote"
    bind vote
}
"""

RECEIVE_SOURCE = "receive { sender => msg { print(sender) } }"


def _run_fresh_request(source: str = FRESH_SOURCE) -> Interpreter:
    interpreter = Interpreter()
    interpreter._durable_mailbox_wait_enabled = True
    interpreter.source_code = source
    interpreter.interpret(compile_to_ast(source))
    return interpreter


def _event_types(interpreter: Interpreter) -> list[str]:
    return [event["type"] for event in interpreter.execution_history]


def _fresh_ticket(interpreter: Interpreter) -> dict:
    assert len(interpreter.consensus_tickets) == 1
    return next(iter(interpreter.consensus_tickets.values()))


def _fresh_request_event(interpreter: Interpreter) -> dict:
    return next(event for event in interpreter.execution_history if event["type"] == REQUEST_EVENT_TYPE)


def _fresh_message_sent(interpreter: Interpreter) -> dict:
    return next(event for event in interpreter.execution_history if event["type"] == "message_sent")


def _response_from_request(ticket: dict, request_event: dict, *, participant: str | None = None, **overrides) -> dict:
    value = {
        "kind": "consensus_vote_response",
        "schema_version": "consensus.vote.response.v1",
        "ticket_id": ticket["ticket_id"],
        "proposal_id": ticket["proposal_id"],
        "participant": participant or request_event["participant"],
        "participant_mailbox": None,
        "coordinator": "global",
        "vote": "yes",
        "reason": None,
        "request_id": request_event["request_id"],
        "response_id": f"response-{participant or request_event['participant']}",
    }
    value.update(overrides)
    return value


def _internal_message(method: str, payload: dict) -> dict:
    return {
        "sender": "external",
        "receiver": "global",
        "method": method,
        "args": [deepcopy(payload)],
        "payload": deepcopy(payload),
    }


def _deliver(interpreter: Interpreter, method: str, payload: dict):
    interpreter._durable_mailbox_wait_enabled = False
    flow = interpreter.interpret_async(compile_to_ast(RECEIVE_SOURCE))
    next(flow)
    with pytest.raises(StopIteration) as completed:
        flow.send(_internal_message(method, payload))
    return completed.value.value


def _imported_ticket(*, participants=("A", "B"), votes=None):
    votes = votes if votes is not None else {"A": "yes"}
    decision = ConsensusEngine().decide(
        ConsensusRequest(
            topic="p3cn2-imported",
            participants=list(participants),
            quorum=2,
            timeout=30,
            policy_ref="MajorityVote",
            statement_identity="source:p3cn2-imported:1",
            vote_source=ExplicitVoteSource(votes),
        )
    )
    assert decision.ticket_payload is not None
    ticket = {
        key: deepcopy(decision.ticket_payload[key])
        for key in (
            "ticket_id",
            "proposal_id",
            "statement_identity",
            "participants",
            "missing_participants",
            "votes",
            "vote_counts",
            "votes_hash",
            "strategy",
            "policy",
            "quorum",
            "timeout",
        )
    }
    ticket["projection_state"] = "pending"
    return ticket


def _import_payload(ticket: dict, *, bootstrap_id: str = "bootstrap-p3cn2") -> dict:
    return {
        "kind": "consensus_ticket_import",
        "schema_version": "consensus.ticket.import.v1",
        "bootstrap_id": bootstrap_id,
        "coordinator": "global",
        "ticket": deepcopy(ticket),
    }


def _p3cn1_response(ticket: dict, participant: str = "B", vote: str = "yes", **overrides) -> dict:
    value = {
        "kind": "consensus_vote_response",
        "schema_version": "consensus.vote.response.v1",
        "ticket_id": ticket["ticket_id"],
        "proposal_id": ticket["proposal_id"],
        "participant": participant,
        "participant_mailbox": None,
        "coordinator": "global",
        "vote": vote,
        "reason": None,
        "request_id": None,
        "response_id": f"response-{participant}",
    }
    value.update(overrides)
    return value


def test_fresh_distributed_consensus_creates_request_projection_and_local_message():
    interpreter = _run_fresh_request()
    ticket = _fresh_ticket(interpreter)
    request_event = _fresh_request_event(interpreter)
    message_sent = _fresh_message_sent(interpreter)

    assert _event_types(interpreter) == [
        "distributed_consensus_decided",
        "distributed_consensus_ticket_created",
        REQUEST_EVENT_TYPE,
        "message_sent",
    ]
    assert ticket["projection_state"] == "pending"
    assert request_event["ticket_id"] == ticket["ticket_id"]
    assert request_event["proposal_id"] == ticket["proposal_id"]
    assert request_event["participant"] == "Peer"
    assert request_event["participant_mailbox"] == "Peer"
    assert request_event["request_id"].startswith("sha256:")
    assert request_event["request_batch_id"].startswith("sha256:")
    assert request_event["request_hash"] == compute_request_hash(
        {key: request_event[key] for key in request_event if key != "request_hash"}
    )
    assert request_event["request_id"] == compute_request_id(
        request_batch_id=request_event["request_batch_id"],
        ticket_id=ticket["ticket_id"],
        proposal_id=ticket["proposal_id"],
        participant="Peer",
        participant_mailbox="Peer",
    )
    validate_vote_request_event(request_event)

    message = message_sent["message"]
    assert message["receiver"] == "Peer"
    assert message["method"] == "consensus_vote_request"
    assert message["payload"] == message["args"][0]
    validate_vote_request_message(message["payload"], request_event)
    assert interpreter.mailboxes["Peer"] == [message]
    assert interpreter.outbound_packets == []
    assert not any(event.get("type") == "message_forwarded" for event in interpreter.execution_history)

    projection = interpreter._consensus_vote_requests[ticket["ticket_id"]]
    assert projection["projection_state"] == "collecting"
    assert projection["requested_participants"] == ["Peer"]
    assert projection["request_ids"] == {"Peer": request_event["request_id"]}
    assert projection["request_hashes"] == {"Peer": request_event["request_hash"]}


def test_request_identifiers_are_deterministic_for_equivalent_fresh_runs():
    first = _run_fresh_request()
    second = _run_fresh_request()

    first_request = _fresh_request_event(first)
    second_request = _fresh_request_event(second)
    assert first_request["request_batch_id"] == second_request["request_batch_id"]
    assert first_request["request_id"] == second_request["request_id"]
    assert first_request["request_hash"] == second_request["request_hash"]
    assert first_request["proposal_view_hash"] == second_request["proposal_view_hash"]


def test_remote_participant_fails_closed_before_send_message_or_forwarding():
    interpreter = Interpreter()
    interpreter._durable_mailbox_wait_enabled = True
    interpreter.register_route("Peer", "node-2")
    interpreter.source_code = FRESH_SOURCE

    with pytest.raises(RuntimeError, match="p3cn2_remote_participant_not_supported") as excinfo:
        interpreter.interpret(compile_to_ast(FRESH_SOURCE))
    assert excinfo.type is RuntimeError

    assert REQUEST_EVENT_TYPE not in _event_types(interpreter)
    assert "message_sent" not in _event_types(interpreter)
    assert not any(event.get("type") == "message_forwarded" for event in interpreter.execution_history)
    assert interpreter.outbound_packets == []
    assert interpreter.mailboxes.get("Peer") in (None, [])


@pytest.mark.parametrize(
    ("source", "match"),
    [
        ('distributed consensus with [UnknownPeer] on "topic" { quorum 1 bind vote }', "p3cn2_vote_request_participant_mailbox"),
        ('let Peer = null\ndistributed consensus with [Peer] on "topic" { quorum 1 bind vote }', "p3cn2_vote_request_participant_mailbox"),
        (
            'agent Peer { model "mock" }\ndistributed consensus with [Peer, Peer] on "topic" { quorum 1 bind vote }',
            "p3cn2_vote_request_duplicate",
        ),
    ],
)
def test_invalid_participant_bindings_fail_closed_before_delivery(source, match):
    interpreter = Interpreter()
    interpreter._durable_mailbox_wait_enabled = True
    interpreter.source_code = source

    with pytest.raises(RuntimeError, match=match) as excinfo:
        interpreter.interpret(compile_to_ast(source))
    assert excinfo.type is RuntimeError
    assert REQUEST_EVENT_TYPE not in _event_types(interpreter)
    assert "message_sent" not in _event_types(interpreter)
    assert interpreter.outbound_packets == []


def test_unresolvable_string_participant_fails_closed_after_ticket_without_delivery():
    source = 'distributed consensus with ["Peer"] on "topic" { quorum 1 bind vote }'
    interpreter = Interpreter()
    interpreter._durable_mailbox_wait_enabled = True
    interpreter.source_code = source

    with pytest.raises(RuntimeError, match="p3cn2_vote_request_participant_mailbox") as excinfo:
        interpreter.interpret(compile_to_ast(source))
    assert excinfo.type is RuntimeError
    assert _event_types(interpreter) == [
        "distributed_consensus_decided",
        "distributed_consensus_ticket_created",
    ]
    assert REQUEST_EVENT_TYPE not in _event_types(interpreter)
    assert "message_sent" not in _event_types(interpreter)
    assert interpreter.outbound_packets == []


def test_fresh_response_without_prior_request_projection_fails_closed():
    ticket = _imported_ticket()
    interpreter = Interpreter()
    interpreter.consensus_tickets[ticket["ticket_id"]] = deepcopy(ticket)
    response = _p3cn1_response(ticket, request_id="sha256:" + "1" * 64)

    with pytest.raises(RuntimeError, match="p3cn2_unsolicited_response") as excinfo:
        _deliver(interpreter, "consensus_vote_response", response)
    assert excinfo.type is RuntimeError
    assert VOTE_RECEIVED_EVENT_TYPE not in _event_types(interpreter)
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == ticket


def test_imported_p3cn1_response_remains_compatible_without_prior_request_projection():
    ticket = _imported_ticket()
    interpreter = Interpreter()
    _deliver(interpreter, "consensus_ticket_import", _import_payload(ticket))
    assert IMPORT_EVENT_TYPE in _event_types(interpreter)
    assert interpreter._consensus_vote_requests == {}

    response = _p3cn1_response(ticket)
    _deliver(interpreter, "consensus_vote_response", response)

    assert VOTE_RECEIVED_EVENT_TYPE in _event_types(interpreter)
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "resolved"
    assert interpreter._consensus_vote_requests == {}
    imported_event = next(event for event in interpreter.execution_history if event["type"] == IMPORT_EVENT_TYPE)
    assert imported_ticket_from_event(imported_event) == ticket
    assert imported_event == build_ticket_import_event(_import_payload(ticket))


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"request_id": "sha256:" + "2" * 64}, "p3cn2_unsolicited_response"),
        ({"ticket_id": "sha256:" + "3" * 64}, "p3cn2_unsolicited_response"),
        ({"proposal_id": "sha256:" + "4" * 64}, "p3cn2_unsolicited_response"),
    ],
)
def test_fresh_response_identity_mismatches_fail_closed(override, match):
    interpreter = _run_fresh_request()
    ticket = _fresh_ticket(interpreter)
    request_event = _fresh_request_event(interpreter)
    response = _response_from_request(ticket, request_event, **override)

    with pytest.raises(RuntimeError, match=match) as excinfo:
        _deliver(interpreter, "consensus_vote_response", response)
    assert excinfo.type is RuntimeError
    assert VOTE_RECEIVED_EVENT_TYPE not in _event_types(interpreter)
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"
    assert interpreter._consensus_vote_requests[ticket["ticket_id"]]["projection_state"] == "collecting"


def test_fresh_response_with_matching_request_resolves_pending_ticket():
    interpreter = _run_fresh_request()
    ticket = _fresh_ticket(interpreter)
    request_event = _fresh_request_event(interpreter)

    _deliver(interpreter, "consensus_vote_response", _response_from_request(ticket, request_event))

    assert _event_types(interpreter) == [
        "distributed_consensus_decided",
        "distributed_consensus_ticket_created",
        REQUEST_EVENT_TYPE,
        "message_sent",
        "message_received",
        VOTE_RECEIVED_EVENT_TYPE,
        "distributed_consensus_ticket_resolved",
    ]
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "resolved"
    assert interpreter._consensus_vote_requests[ticket["ticket_id"]]["projection_state"] == "completed"


def test_replay_consumes_request_events_without_resending_messages():
    live = _run_fresh_request()
    history = deepcopy(live.execution_history)
    replay = Interpreter()
    replay._durable_mailbox_wait_enabled = True
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = FRESH_SOURCE

    def fail_send(*_args, **_kwargs):
        raise AssertionError("P3c-N2 replay must not re-send vote requests")

    replay.send_message = fail_send
    replay.interpret(compile_to_ast(FRESH_SOURCE))

    ticket = _fresh_ticket(live)
    assert replay.replay_cursor == len(history)
    assert replay.execution_history == history
    assert replay.mailboxes == {"global": []}
    assert replay.outbound_packets == []
    assert replay._consensus_vote_requests[ticket["ticket_id"]] == live._consensus_vote_requests[ticket["ticket_id"]]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda history: history.pop(2),
        lambda history: history[2].pop("request_hash"),
        lambda history: history[2].__setitem__("participant", "Other"),
    ],
)
def test_replay_request_event_mismatches_fail_closed_without_repair(mutate):
    live = _run_fresh_request()
    history = deepcopy(live.execution_history)
    mutate(history)
    replay = Interpreter()
    replay._durable_mailbox_wait_enabled = True
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = FRESH_SOURCE

    with pytest.raises(ConsensusReplayIntegrityError, match="p3cn2 vote request replay mismatch") as excinfo:
        replay.interpret(compile_to_ast(FRESH_SOURCE))
    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 0
    assert replay.execution_history == history
    assert replay._consensus_vote_requests == {}


def test_terminal_ticket_blocks_request_delivery_adapter_guard():
    interpreter = _run_fresh_request()
    ticket = _fresh_ticket(interpreter)
    peer = interpreter.global_env.get("Peer")
    before_history_len = len(interpreter.execution_history)
    terminal_ticket = deepcopy(ticket)
    terminal_ticket["projection_state"] = "cancelled"
    interpreter.consensus_tickets[ticket["ticket_id"]] = terminal_ticket
    decision = SimpleNamespace(
        ticket_id=ticket["ticket_id"],
        ticket_payload=ticket,
        result={"coordinator": "global"},
    )

    with pytest.raises(ConsensusVoteRequestError, match="p3cn2_vote_request_terminal_ticket") as excinfo:
        interpreter._send_p3cn2_vote_requests(decision, [peer])
    assert excinfo.type.__name__ == "ConsensusVoteRequestError"
    assert len(interpreter.execution_history) == before_history_len
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == terminal_ticket


@pytest.mark.parametrize("state", ["resolved", "cancelled", "expired"])
def test_terminal_ticket_blocks_fresh_response_collection(state):
    interpreter = _run_fresh_request()
    ticket = _fresh_ticket(interpreter)
    request_event = _fresh_request_event(interpreter)
    terminal = deepcopy(ticket)
    terminal["projection_state"] = state
    interpreter.consensus_tickets[ticket["ticket_id"]] = terminal

    with pytest.raises(RuntimeError, match="p3cn2_vote_request_terminal_ticket") as excinfo:
        _deliver(interpreter, "consensus_vote_response", _response_from_request(ticket, request_event))
    assert excinfo.type is RuntimeError
    assert VOTE_RECEIVED_EVENT_TYPE not in _event_types(interpreter)
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == terminal


def test_unrelated_non_p3cn2_replay_error_remains_durable_mailbox_runtime_error():
    replay = Interpreter()
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.execution_history = [{"type": "some_unrelated_event"}]

    flow = replay.interpret_async(compile_to_ast(RECEIVE_SOURCE))
    with pytest.raises(RuntimeError, match="DURABLE_MAILBOX_REPLAY_INTEGRITY") as excinfo:
        next(flow)
    assert excinfo.type is RuntimeError
    assert replay.replay_cursor == 0
    assert replay._consensus_vote_requests == {}
