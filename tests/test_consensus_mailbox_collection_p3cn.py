"""P3c-N1 pending-ticket import and mailbox vote-collection contract tests."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource
from synapse.runtime.consensus_mailbox_collection import (
    IMPORT_EVENT_TYPE,
    VOTE_RECEIVED_EVENT_TYPE,
    ConsensusMailboxCollectionError,
    apply_vote_received_event,
    build_ticket_import_event,
    build_vote_received_event,
    compute_response_hash,
    compute_ticket_import_hash,
    imported_ticket_from_event,
    new_collection_projection,
    recompute_vote_counts,
    recompute_votes_hash,
    resolution_votes_from_collection,
    validate_import_idempotency,
    validate_pending_ticket_projection,
    validate_response_for_ticket,
    validate_ticket_import_event,
    validate_ticket_import_payload,
    validate_vote_received_event,
    validate_vote_response_payload,
)
from synapse.runtime.mailbox_wait import validate_mailbox_resume


_RECEIVE_SOURCE = "receive { sender => msg { print(sender) } }"


def _ticket(*, participants=("A", "B", "C"), votes=None, statement_identity="source:p3cn1:1"):
    votes = votes if votes is not None else {"A": "yes"}
    decision = ConsensusEngine().decide(
        ConsensusRequest(
            topic="p3cn1",
            participants=list(participants),
            quorum=2,
            timeout=30,
            policy_ref="MajorityVote",
            statement_identity=statement_identity,
            vote_source=ExplicitVoteSource(votes),
        )
    )
    assert decision.ticket_payload is not None
    payload = decision.ticket_payload
    return {
        key: deepcopy(payload[key])
        for key in (
            "ticket_id", "proposal_id", "statement_identity", "participants",
            "missing_participants", "votes", "vote_counts", "votes_hash",
            "strategy", "policy", "quorum", "timeout",
        )
    } | {"projection_state": "pending"}


def _import(ticket, *, bootstrap_id="bootstrap-1"):
    return {
        "kind": "consensus_ticket_import",
        "schema_version": "consensus.ticket.import.v1",
        "bootstrap_id": bootstrap_id,
        "coordinator": "global",
        "ticket": deepcopy(ticket),
    }


def _response(ticket, participant="B", vote="yes", **overrides):
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


def _internal_message(method, payload):
    return {
        "sender": "external",
        "receiver": "global",
        "method": method,
        "args": [deepcopy(payload)],
        "payload": deepcopy(payload),
    }


def _mailbox_resume(method, payload, *, message_id="message-1"):
    return {
        "kind": "mailbox_message",
        "message_id": message_id,
        "actor": "global",
        "message": {
            "sender": "external",
            "receiver": "global",
            "method": method,
            "args": [deepcopy(payload)],
        },
    }


def _deliver(interpreter, method, payload):
    flow = interpreter.interpret_async(compile_to_ast(_RECEIVE_SOURCE))
    next(flow)
    with pytest.raises(StopIteration) as completed:
        flow.send(_internal_message(method, payload))
    return completed.value.value


def _event_types(interpreter):
    return [event["type"] for event in interpreter.execution_history]


def _import_live(interpreter, ticket, *, bootstrap_id="bootstrap-1"):
    return _deliver(interpreter, "consensus_ticket_import", _import(ticket, bootstrap_id=bootstrap_id))


def test_valid_ticket_import_is_accepted_after_p2_envelope_validation():
    ticket = _ticket()
    envelope = _mailbox_resume("consensus_ticket_import", _import(ticket))
    internal = validate_mailbox_resume(
        envelope,
        "awaiting_message",
        {"mailbox_wait_schema": "synapse.mailbox.wait.v1", "actor": "global", "timeout": None, "receive_shape": {"patterns": 1, "has_else": False}},
    ).internal_value
    interpreter = Interpreter()
    _deliver(interpreter, internal["method"], internal["args"][0])

    imported = next(event for event in interpreter.execution_history if event["type"] == IMPORT_EVENT_TYPE)
    assert imported == build_ticket_import_event(_import(ticket))
    assert interpreter.consensus_tickets[ticket["ticket_id"]] == ticket
    assert _event_types(interpreter) == ["message_received", IMPORT_EVENT_TYPE]


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda value: value.__setitem__("kind", "wrong"), "ticket_import_kind"),
        (lambda value: value.__setitem__("schema_version", "wrong"), "ticket_import_schema_version"),
        (lambda value: value.pop("bootstrap_id"), "ticket_import_schema"),
        (lambda value: value.__setitem__("extra", True), "ticket_import_schema"),
        (lambda value: value.__setitem__("bootstrap_id", 1), "ticket_import_bootstrap_id"),
    ],
)
def test_ticket_import_envelope_rejects_invalid_closed_schema(mutate, match):
    value = _import(_ticket())
    mutate(value)
    with pytest.raises(ConsensusMailboxCollectionError, match=match):
        validate_ticket_import_payload(value)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda ticket: ticket.__setitem__("projection_state", "resolved"), "ticket_projection_state"),
        (lambda ticket: ticket.__setitem__("projection_state", "other"), "ticket_projection_state"),
        (lambda ticket: ticket.__setitem__("extra", True), "ticket_schema"),
        (lambda ticket: ticket.pop("votes_hash"), "ticket_schema"),
        (lambda ticket: ticket["vote_counts"].__setitem__("yes", 99), "ticket_vote_counts_mismatch"),
        (lambda ticket: ticket.__setitem__("votes_hash", "sha256:" + "0" * 64), "ticket_votes_hash_mismatch"),
        (lambda ticket: ticket.__setitem__("ticket_id", "bad"), "ticket_id"),
        (lambda ticket: ticket.__setitem__("proposal_id", "bad"), "proposal_id"),
    ],
)
def test_ticket_import_rejects_invalid_pending_projection(mutate, match):
    ticket = _ticket()
    mutate(ticket)
    with pytest.raises(ConsensusMailboxCollectionError, match=match):
        validate_ticket_import_payload(_import(ticket))


def test_ticket_import_hash_and_integrity_recomputation_are_deterministic():
    ticket = _ticket()
    validated = validate_pending_ticket_projection(ticket)
    assert recompute_vote_counts(validated["votes"]) == ticket["vote_counts"]
    assert recompute_votes_hash(validated) == ticket["votes_hash"]
    assert compute_ticket_import_hash(validated) == compute_ticket_import_hash(deepcopy(validated))


def test_import_idempotency_and_conflicts_for_ticket_and_bootstrap_identities():
    ticket = _ticket()
    event = build_ticket_import_event(_import(ticket))
    assert validate_import_idempotency(event, [], None) is False
    assert validate_import_idempotency(event, [event], ticket) is True

    changed_ticket = deepcopy(ticket)
    changed_ticket["votes"]["A"] = "abstain"
    changed_ticket["vote_counts"] = recompute_vote_counts(changed_ticket["votes"])
    changed_ticket["votes_hash"] = recompute_votes_hash(changed_ticket)
    changed_event = build_ticket_import_event(_import(changed_ticket))
    with pytest.raises(ConsensusMailboxCollectionError, match="ticket_import_ticket_conflict"):
        validate_import_idempotency(changed_event, [event], ticket)

    different_ticket = _ticket(statement_identity="source:p3cn1:other")
    same_bootstrap = build_ticket_import_event(_import(different_ticket))
    with pytest.raises(ConsensusMailboxCollectionError, match="ticket_import_bootstrap_conflict"):
        validate_import_idempotency(same_bootstrap, [event], None)
    assert validate_import_idempotency(event, [event], ticket) is True


def test_import_event_is_emitted_only_after_validation_and_projects_ticket():
    ticket = _ticket()
    interpreter = Interpreter()
    with pytest.raises(RuntimeError, match="ticket_import_kind"):
        _deliver(interpreter, "consensus_ticket_import", {**_import(ticket), "kind": "bad"})
    assert _event_types(interpreter) == ["message_received"]
    assert interpreter.consensus_tickets == {}

    _import_live(interpreter, ticket)
    assert _event_types(interpreter) == ["message_received", "message_received", IMPORT_EVENT_TYPE]
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda event: event.__setitem__("extra", True), "ticket_import_event_schema"),
        (lambda event: event.pop("ticket"), "ticket_import_event_schema"),
        (lambda event: event["ticket"]["vote_counts"].__setitem__("yes", 9), "ticket_vote_counts_mismatch"),
        (lambda event: event["ticket"].__setitem__("votes_hash", "sha256:" + "f" * 64), "ticket_votes_hash_mismatch"),
        (lambda event: event.__setitem__("ticket_import_hash", "sha256:" + "f" * 64), "ticket_import_event_hash_mismatch"),
    ],
)
def test_import_event_replay_validation_is_closed_and_recomputes_integrity(mutate, match):
    event = build_ticket_import_event(_import(_ticket()))
    mutate(event)
    with pytest.raises(ConsensusMailboxCollectionError, match=match):
        validate_ticket_import_event(event)


def test_import_replay_reconstructs_ticket_after_message_received_in_order():
    ticket = _ticket()
    live = Interpreter()
    _import_live(live, ticket)
    assert _event_types(live) == ["message_received", IMPORT_EVENT_TYPE]

    replay = Interpreter()
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    flow = replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE))
    with pytest.raises(StopIteration):
        next(flow)
    assert replay.replay_cursor == 2
    assert replay.consensus_tickets[ticket["ticket_id"]] == ticket


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda value: value.__setitem__("kind", "wrong"), "vote_response_kind"),
        (lambda value: value.__setitem__("schema_version", "wrong"), "vote_response_schema_version"),
        (lambda value: value.pop("response_id"), "vote_response_schema"),
        (lambda value: value.__setitem__("extra", True), "vote_response_schema"),
        (lambda value: value.__setitem__("ticket_id", None), "vote_response_ticket_id"),
        (lambda value: value.__setitem__("vote", "missing"), "vote_response_vote"),
        (lambda value: value.__setitem__("vote", "maybe"), "vote_response_vote"),
        (lambda value: value.__setitem__(1, "bad"), "non_string_mapping_key"),
        (lambda value: value.__setitem__("reason", float("nan")), "non_finite_float"),
    ],
)
def test_vote_response_schema_is_strict_and_closed(mutate, match):
    value = _response(_ticket())
    mutate(value)
    with pytest.raises(ConsensusMailboxCollectionError, match=match):
        validate_vote_response_payload(value)


def test_vote_response_requires_imported_or_existing_pending_ticket_and_matching_identity():
    ticket = _ticket()
    response = _response(ticket)
    with pytest.raises(ConsensusMailboxCollectionError, match="ticket_not_found"):
        validate_response_for_ticket(response, None, {})

    wrong_ticket = _response(ticket, ticket_id="sha256:" + "0" * 64)
    with pytest.raises(ConsensusMailboxCollectionError, match="vote_response_ticket_id"):
        validate_response_for_ticket(wrong_ticket, ticket, {})
    wrong_proposal = _response(ticket, proposal_id="sha256:" + "0" * 64)
    with pytest.raises(ConsensusMailboxCollectionError, match="vote_response_proposal_id"):
        validate_response_for_ticket(wrong_proposal, ticket, {})
    with pytest.raises(ConsensusMailboxCollectionError, match="vote_response_participant"):
        validate_response_for_ticket(_response(ticket, participant="A"), ticket, {})
    with pytest.raises(ConsensusMailboxCollectionError, match="vote_response_participant"):
        validate_response_for_ticket(_response(ticket, participant="unknown"), ticket, {})
    resolved = {**ticket, "projection_state": "resolved"}
    with pytest.raises(ConsensusMailboxCollectionError, match="ticket_resolved"):
        validate_response_for_ticket(response, resolved, {})


def test_participant_mailbox_binding_is_null_first_path_or_unique_spawned_actor_match():
    ticket = _ticket()
    spawned = {"A#one": {"actor_name": "A"}, "B#one": {"actor_name": "B"}}
    accepted = _response(ticket, participant_mailbox="B#one")
    assert validate_response_for_ticket(accepted, ticket, spawned)["participant_mailbox"] == "B#one"
    with pytest.raises(ConsensusMailboxCollectionError, match="participant_mailbox_mismatch"):
        validate_response_for_ticket(_response(ticket, participant_mailbox="B#other"), ticket, spawned)
    ambiguous = {"B#one": {"actor_name": "B"}, "B#two": {"actor_name": "B"}}
    with pytest.raises(ConsensusMailboxCollectionError, match="participant_mailbox_ambiguous"):
        validate_response_for_ticket(accepted, ticket, ambiguous)


def test_response_hash_event_validation_and_duplicate_policy_fail_closed_before_mutation():
    ticket = _ticket()
    collection = new_collection_projection(ticket, {})
    first = build_vote_received_event(_response(ticket, reason="because"))
    updated, applied = apply_vote_received_event(collection, ticket, first)
    assert applied is True
    assert compute_response_hash(_response(ticket, reason="because")) == first["response_hash"]
    duplicate, applied = apply_vote_received_event(updated, ticket, first)
    assert applied is False
    assert duplicate == updated

    conflicting = build_vote_received_event(_response(ticket, reason="different"))
    before = deepcopy(updated)
    with pytest.raises(ConsensusMailboxCollectionError, match="duplicate_conflict"):
        apply_vote_received_event(updated, ticket, conflicting)
    assert updated == before

    tampered = deepcopy(first)
    tampered["response_hash"] = "sha256:" + "0" * 64
    assert validate_vote_received_event(tampered)["response_hash"] == tampered["response_hash"]


def test_single_and_multiple_responses_convert_to_mapping_only_on_full_coverage():
    ticket = _ticket()
    collection = new_collection_projection(ticket, {})
    first, _ = apply_vote_received_event(collection, ticket, build_vote_received_event(_response(ticket, "B", "no")))
    with pytest.raises(ConsensusMailboxCollectionError, match="vote_collection_incomplete"):
        resolution_votes_from_collection(first, ticket)
    complete, _ = apply_vote_received_event(first, ticket, build_vote_received_event(_response(ticket, "C", "yes")))
    assert resolution_votes_from_collection(complete, ticket) == {"B": "no", "C": "yes"}


def test_live_vote_collection_emits_domain_event_after_import_and_resolves_only_on_full_coverage():
    ticket = _ticket()
    interpreter = Interpreter()
    _import_live(interpreter, ticket)
    calls = []
    original = interpreter._consensus_engine.resolve_pending_ticket

    def capture(ticket_payload, resolution_votes):
        assert isinstance(resolution_votes, Mapping)
        assert set(resolution_votes) == set(ticket_payload["missing_participants"])
        calls.append(dict(resolution_votes))
        return original(ticket_payload, resolution_votes)

    interpreter._consensus_engine.resolve_pending_ticket = capture
    _deliver(interpreter, "consensus_vote_response", _response(ticket, "B", "no"))
    assert calls == []
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"
    assert _event_types(interpreter).count(VOTE_RECEIVED_EVENT_TYPE) == 1
    assert "distributed_consensus_ticket_resolved" not in _event_types(interpreter)

    _deliver(interpreter, "consensus_vote_response", _response(ticket, "C", "yes"))
    assert calls == [{"B": "no", "C": "yes"}]
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "resolved"
    assert _event_types(interpreter)[-2:] == [VOTE_RECEIVED_EVENT_TYPE, "distributed_consensus_ticket_resolved"]


def test_malformed_vote_has_transport_evidence_but_no_domain_event_and_timeout_is_non_terminal():
    ticket = _ticket()
    interpreter = Interpreter()
    _import_live(interpreter, ticket)
    with pytest.raises(RuntimeError, match="vote_response_vote"):
        _deliver(interpreter, "consensus_vote_response", _response(ticket, "B", "missing"))
    assert _event_types(interpreter).count(VOTE_RECEIVED_EVENT_TYPE) == 0
    assert _event_types(interpreter).count("message_received") == 2

    flow = interpreter.interpret_async(compile_to_ast('receive timeout 1 { sender => msg { print(sender) } } else { print("timeout") }'))
    next(flow)
    with pytest.raises(StopIteration):
        flow.send({"timeout": True})
    assert interpreter.consensus_tickets[ticket["ticket_id"]]["projection_state"] == "pending"
    assert "distributed_consensus_ticket_resolved" not in _event_types(interpreter)


def test_vote_before_import_fails_and_replay_enforces_import_before_vote_ordering():
    ticket = _ticket()
    interpreter = Interpreter()
    with pytest.raises(RuntimeError, match="ticket_not_found"):
        _deliver(interpreter, "consensus_vote_response", _response(ticket, "B"))
    assert _event_types(interpreter) == ["message_received"]

    vote_message = _internal_message("consensus_vote_response", _response(ticket, "B"))
    vote_event = build_vote_received_event(_response(ticket, "B"))
    replay = Interpreter()
    replay.execution_history = [
        {"type": "message_received", "actor": "global", "message": vote_message},
        vote_event,
    ]
    replay.runtime_mode = RuntimeMode.REPLAY
    with pytest.raises(RuntimeError, match="ticket_not_found"):
        next(replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE)))
    assert replay.replay_cursor == 1
    assert replay.consensus_tickets == {}


def test_replay_consumes_message_then_vote_event_without_live_poll_send_or_pattern_body():
    ticket = _ticket()
    live = Interpreter()
    _import_live(live, ticket)
    _deliver(live, "consensus_vote_response", _response(ticket, "B", "no"))
    history = deepcopy(live.execution_history)

    replay = Interpreter()
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    first = replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE))
    with pytest.raises(StopIteration):
        next(first)
    second = replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE))
    with pytest.raises(StopIteration):
        next(second)
    assert replay.replay_cursor == len(history)
    assert replay.output_buffer == []
    assert replay.mailboxes == {"global": []}
    assert replay._last_actor_method_vote_source is None
    assert replay._consensus_mailbox_collections[ticket["ticket_id"]]["votes_collected"] == {"B": "no"}


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda event: event.__setitem__("extra", True), "vote_received_event_schema"),
        (lambda event: event.__setitem__("response_hash", "sha256:" + "0" * 64), "distributed_consensus_vote_received"),
        (lambda event: event.__setitem__("vote", "missing"), "vote_received_event_vote"),
    ],
)
def test_replay_vote_event_mismatch_fails_before_collection_projection_mutation(mutate, match):
    ticket = _ticket()
    live = Interpreter()
    _import_live(live, ticket)
    _deliver(live, "consensus_vote_response", _response(ticket, "B"))
    history = deepcopy(live.execution_history)
    mutate(history[-1])

    replay = Interpreter()
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    with pytest.raises(StopIteration):
        next(replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE)))
    with pytest.raises((ConsensusReplayIntegrityError, RuntimeError), match=match):
        next(replay.interpret_async(compile_to_ast(_RECEIVE_SOURCE)))
    assert ticket["ticket_id"] not in replay._consensus_mailbox_collections
