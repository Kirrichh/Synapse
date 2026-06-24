from copy import deepcopy

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource
from synapse.runtime.consensus_mailbox_collection import (
    ConsensusMailboxCollectionError,
    IMPORT_EVENT_TYPE,
    LIFECYCLE_CANCEL_EVENT_TYPE,
    LIFECYCLE_EXPIRE_EVENT_TYPE,
    VOTE_RECEIVED_EVENT_TYPE,
    apply_vote_received_event,
    build_lifecycle_event,
    build_lifecycle_projection,
    build_vote_received_event,
    compute_lifecycle_action_hash,
    new_collection_projection,
    validate_lifecycle_command,
    validate_lifecycle_event,
    validate_lifecycle_transition,
)
from synapse.runtime.consensus_ticket_resolution import (
    ConsensusTicketLifecycleError,
    build_resolved_projection,
)


SOURCE = 'receive { sender => msg { print(sender) } }'


def ticket(*, topic="life", statement_identity="source:lifecycle:1"):
    decision = ConsensusEngine().decide(ConsensusRequest(
        topic=topic, participants=["A", "B"], quorum=2, statement_identity=statement_identity,
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


def resolved_ticket():
    value = ticket()
    resolution = ConsensusEngine().resolve_pending_ticket(value, {"B": "yes"})
    return build_resolved_projection(value, resolution.event_payload)


def vote_response(value, **changes):
    result = {
        "kind": "consensus_vote_response",
        "schema_version": "consensus.vote.response.v1",
        "ticket_id": value["ticket_id"],
        "proposal_id": value["proposal_id"],
        "participant": "B",
        "participant_mailbox": None,
        "coordinator": "global",
        "vote": "yes",
        "reason": None,
        "request_id": None,
        "response_id": "post-terminal-response",
    }
    result.update(changes)
    return result


def ticket_import(value):
    return {
        "kind": "consensus_ticket_import",
        "schema_version": "consensus.ticket.import.v1",
        "bootstrap_id": "post-terminal-import",
        "coordinator": "global",
        "ticket": deepcopy(value),
    }


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


def test_replay_out_of_order_terminal_event_fails_closed_with_consensus_replay_integrity_error():
    value = ticket()
    terminal_event = build_lifecycle_event(command(value))
    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = [terminal_event]
    replay.runtime_mode = RuntimeMode.REPLAY
    history_length = len(replay.execution_history)

    with pytest.raises(
        ConsensusReplayIntegrityError,
        match="p3c ticket lifecycle replay integrity mismatch: out-of-order terminal event",
    ) as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 0
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "pending"
    assert len(replay.execution_history) == history_length


def test_replay_unexpected_non_lifecycle_event_keeps_durable_mailbox_runtime_error():
    replay = Interpreter()
    replay.execution_history = [{"type": "some_unrelated_event"}]
    replay.runtime_mode = RuntimeMode.REPLAY

    with pytest.raises(RuntimeError, match="DURABLE_MAILBOX_REPLAY_INTEGRITY") as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is RuntimeError
    assert replay.replay_cursor == 0


@pytest.mark.parametrize("kind", ["consensus_ticket_cancel", "consensus_ticket_expire"])
def test_non_existing_ticket_lifecycle_commands_fail_closed(kind):
    missing = ticket(topic="missing", statement_identity="source:lifecycle:missing")
    unrelated = ticket(topic="unrelated", statement_identity="source:lifecycle:unrelated")
    interpreter = Interpreter()
    interpreter.consensus_tickets[unrelated["ticket_id"]] = deepcopy(unrelated)
    before_tickets = deepcopy(interpreter.consensus_tickets)

    with pytest.raises(
        RuntimeError,
        match="consensus_ticket_lifecycle_ticket_not_found",
    ) as excinfo:
        deliver(interpreter, command(missing, kind))

    assert excinfo.type is RuntimeError
    assert interpreter.consensus_tickets == before_tickets
    assert missing["ticket_id"] not in interpreter.consensus_tickets
    assert not any(
        event["type"] in {LIFECYCLE_CANCEL_EVENT_TYPE, LIFECYCLE_EXPIRE_EVENT_TYPE}
        for event in interpreter.execution_history
    )


@pytest.mark.parametrize("kind", ["consensus_ticket_cancel", "consensus_ticket_expire"])
def test_lifecycle_commands_reject_resolved_tickets(kind):
    resolved = resolved_ticket()
    before = deepcopy(resolved)

    with pytest.raises(
        ConsensusTicketLifecycleError,
        match="consensus_ticket_lifecycle_terminal_conflict",
    ) as excinfo:
        validate_lifecycle_transition(command(resolved, kind), resolved, [])

    assert excinfo.type is ConsensusTicketLifecycleError
    assert resolved == before
    assert resolved["projection_state"] == "resolved"


@pytest.mark.parametrize(
    "first_kind, second_kind, terminal_state, terminal_event_type",
    [
        ("consensus_ticket_cancel", "consensus_ticket_expire", "cancelled", LIFECYCLE_CANCEL_EVENT_TYPE),
        ("consensus_ticket_expire", "consensus_ticket_cancel", "expired", LIFECYCLE_EXPIRE_EVENT_TYPE),
    ],
)
def test_terminal_conflicts_fail_closed_through_mailbox_path(
    first_kind,
    second_kind,
    terminal_state,
    terminal_event_type,
):
    value = ticket()
    interpreter = Interpreter()
    interpreter.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(interpreter, command(value, first_kind, action_id="first-terminal-action"))
    terminal_before = deepcopy(interpreter.consensus_tickets[value["ticket_id"]])

    with pytest.raises(
        RuntimeError,
        match="consensus_ticket_lifecycle_terminal_conflict",
    ) as excinfo:
        deliver(interpreter, command(value, second_kind, action_id="second-terminal-action"))

    assert excinfo.type is RuntimeError
    assert interpreter.consensus_tickets[value["ticket_id"]] == terminal_before
    assert interpreter.consensus_tickets[value["ticket_id"]]["projection_state"] == terminal_state
    assert [
        event["type"]
        for event in interpreter.execution_history
        if event["type"] in {LIFECYCLE_CANCEL_EVENT_TYPE, LIFECYCLE_EXPIRE_EVENT_TYPE}
    ] == [terminal_event_type]


def test_replay_same_action_identity_with_different_semantics_fails_closed():
    value = ticket()
    action_id = "same-terminal-action"
    live = Interpreter()
    live.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(live, command(value, action_id=action_id))
    history = deepcopy(live.execution_history)
    history[1] = build_lifecycle_event(command(value, reason="different", action_id=action_id))
    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY

    with pytest.raises(
        ConsensusReplayIntegrityError,
        match="p3c ticket lifecycle replay integrity mismatch: terminal event",
    ) as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "pending"
    assert replay.execution_history == history


def test_replay_missing_terminal_event_fails_closed():
    value = ticket()
    live = Interpreter()
    live.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(live, command(value))
    history = deepcopy(live.execution_history[:-1])
    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY

    with pytest.raises(
        ConsensusReplayIntegrityError,
        match="p3c ticket lifecycle replay integrity mismatch: missing terminal event",
    ) as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "pending"
    assert replay.execution_history == history


def test_replay_malformed_terminal_event_fails_closed():
    value = ticket()
    live = Interpreter()
    live.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(live, command(value))
    history = deepcopy(live.execution_history)
    history[1].pop("action_hash")
    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY

    with pytest.raises(
        ConsensusReplayIntegrityError,
        match="p3c ticket lifecycle replay integrity mismatch: malformed terminal event",
    ) as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "pending"
    assert replay.execution_history == history


def test_replay_mismatched_terminal_event_fails_closed():
    value = ticket()
    live = Interpreter()
    live.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(live, command(value, "consensus_ticket_cancel"))
    history = deepcopy(live.execution_history)
    history[1] = build_lifecycle_event(command(value, "consensus_ticket_expire"))
    replay = Interpreter()
    replay.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY

    with pytest.raises(
        ConsensusReplayIntegrityError,
        match="p3c ticket lifecycle replay integrity mismatch: terminal event",
    ) as excinfo:
        next(replay.interpret_async(compile_to_ast(SOURCE)))

    assert excinfo.type is ConsensusReplayIntegrityError
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets[value["ticket_id"]]["projection_state"] == "pending"
    assert replay.execution_history == history


@pytest.mark.parametrize("kind, terminal_state", [("consensus_ticket_cancel", "cancelled"), ("consensus_ticket_expire", "expired")])
def test_post_terminal_vote_response_path_cannot_mutate_ticket(kind, terminal_state):
    value = ticket()
    interpreter = Interpreter()
    interpreter.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(interpreter, command(value, kind))
    terminal_before = deepcopy(interpreter.consensus_tickets[value["ticket_id"]])

    with pytest.raises(RuntimeError, match="p3cn1_ticket_projection_.*ticket_projection_state") as excinfo:
        deliver(interpreter, vote_response(value))

    assert excinfo.type is RuntimeError
    assert interpreter.consensus_tickets[value["ticket_id"]] == terminal_before
    assert interpreter.consensus_tickets[value["ticket_id"]]["projection_state"] == terminal_state
    assert not any(event["type"] == VOTE_RECEIVED_EVENT_TYPE for event in interpreter.execution_history)


@pytest.mark.parametrize("kind, terminal_state", [("consensus_ticket_cancel", "cancelled"), ("consensus_ticket_expire", "expired")])
def test_post_terminal_import_path_cannot_mutate_ticket(kind, terminal_state):
    value = ticket()
    interpreter = Interpreter()
    interpreter.consensus_tickets[value["ticket_id"]] = deepcopy(value)
    deliver(interpreter, command(value, kind))
    terminal_before = deepcopy(interpreter.consensus_tickets[value["ticket_id"]])

    with pytest.raises(RuntimeError, match="p3cn1_ticket_schema") as excinfo:
        deliver(interpreter, ticket_import(terminal_before))

    assert excinfo.type is RuntimeError
    assert interpreter.consensus_tickets[value["ticket_id"]] == terminal_before
    assert interpreter.consensus_tickets[value["ticket_id"]]["projection_state"] == terminal_state
    assert not any(event["type"] == IMPORT_EVENT_TYPE for event in interpreter.execution_history)


@pytest.mark.parametrize("kind, terminal_state", [("consensus_ticket_cancel", "cancelled"), ("consensus_ticket_expire", "expired")])
def test_terminal_tickets_reject_new_and_existing_collection_projection_updates(kind, terminal_state):
    value = ticket()
    terminal = build_lifecycle_projection(value, build_lifecycle_event(command(value, kind)))
    collection = new_collection_projection(value, {})
    collection_before = deepcopy(collection)
    terminal_before = deepcopy(terminal)
    vote_event = build_vote_received_event(vote_response(value))

    with pytest.raises(
        ConsensusMailboxCollectionError,
        match="p3cn1_ticket_projection_.*ticket_projection_state",
    ) as create_excinfo:
        new_collection_projection(terminal, {})
    with pytest.raises(
        ConsensusMailboxCollectionError,
        match="p3cn1_ticket_projection_.*ticket_projection_state",
    ) as update_excinfo:
        apply_vote_received_event(collection, terminal, vote_event)

    assert create_excinfo.type is ConsensusMailboxCollectionError
    assert update_excinfo.type is ConsensusMailboxCollectionError
    assert collection == collection_before
    assert terminal == terminal_before
    assert terminal["projection_state"] == terminal_state


def test_terminal_ticket_remains_rejected_by_vote_collection_and_import():
    value = ticket()
    terminal = build_lifecycle_projection(value, build_lifecycle_event(command(value)))
    from synapse.runtime.consensus_mailbox_collection import new_collection_projection, validate_pending_ticket_projection
    with pytest.raises(Exception):
        new_collection_projection(terminal, {})
    with pytest.raises(Exception):
        validate_pending_ticket_projection(terminal)


def test_missing_reason_uses_lifecycle_command_schema_taxonomy_not_mailbox_taxonomy():
    payload = command(ticket())
    payload.pop("reason")

    with pytest.raises(
        ConsensusTicketLifecycleError,
        match="consensus_ticket_lifecycle_command_schema",
    ) as excinfo:
        validate_lifecycle_command(payload)

    assert excinfo.type is ConsensusTicketLifecycleError
    assert not isinstance(excinfo.value, ConsensusMailboxCollectionError)


@pytest.mark.parametrize(
    "kind, mutate",
    [
        ("consensus_ticket_cancel", lambda payload: payload.pop("reason")),
        ("consensus_ticket_cancel", lambda payload: payload.pop("request_id")),
        ("consensus_ticket_cancel", lambda payload: payload.__setitem__("expires_at", 1)),
        ("consensus_ticket_cancel", lambda payload: payload.__setitem__("action_id", "")),
        ("consensus_ticket_cancel", lambda payload: payload.__setitem__("ticket_id", "sha256:bad")),
        ("consensus_ticket_expire", lambda payload: payload.pop("reason")),
        ("consensus_ticket_expire", lambda payload: payload.pop("request_id")),
        ("consensus_ticket_expire", lambda payload: payload.__setitem__("deadline", 1)),
        ("consensus_ticket_cancel", lambda payload: payload.__setitem__("reason", object())),
    ],
)
def test_lifecycle_command_shape_failures_use_exact_lifecycle_taxonomy(kind, mutate):
    payload = command(ticket(), kind)
    mutate(payload)

    with pytest.raises(
        ConsensusTicketLifecycleError,
        match="consensus_ticket_lifecycle_command_schema",
    ) as excinfo:
        validate_lifecycle_command(payload)

    assert excinfo.type is ConsensusTicketLifecycleError


@pytest.mark.parametrize(
    "mutate",
    [
        lambda event: event.pop("action_hash"),
        lambda event: event.__setitem__("terminal_event_hash", "sha256:" + "0" * 64),
        lambda event: event.__setitem__("action_hash", "sha256:bad"),
        lambda event: event.__setitem__("proposal_id", "sha256:bad"),
        lambda event: event.__setitem__("reason", object()),
    ],
)
def test_lifecycle_event_shape_failures_use_exact_lifecycle_taxonomy(mutate):
    event = build_lifecycle_event(command(ticket()))
    mutate(event)

    with pytest.raises(
        ConsensusTicketLifecycleError,
        match="consensus_ticket_lifecycle_event_schema",
    ) as excinfo:
        validate_lifecycle_event(event)

    assert excinfo.type is ConsensusTicketLifecycleError


def test_lifecycle_event_action_hash_mismatch_uses_semantic_lifecycle_taxonomy():
    event = build_lifecycle_event(command(ticket()))
    event["action_hash"] = "sha256:" + "0" * 64

    with pytest.raises(
        ConsensusTicketLifecycleError,
        match="consensus_ticket_lifecycle_action_hash_mismatch",
    ) as excinfo:
        validate_lifecycle_event(event)

    assert excinfo.type is ConsensusTicketLifecycleError
