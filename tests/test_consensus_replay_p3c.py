from copy import deepcopy

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.hardening import hash_event_chain
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusDecision, ConsensusEngine, ConsensusRequest, ConsensusValidationError, ExplicitVoteSource


SOURCE = '''
agent Peer { model "mock" }
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    timeout 30
    policy "MajorityVote"
    bind vote
}
'''


ACTOR_SOURCE = '''
agent Peer {
    model "mock"
    fn consensus_vote(proposal) {
        return "yes"
    }
}
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    timeout 30
    policy "MajorityVote"
    bind vote
}
'''

REPLAY_ACTOR_SOURCE = ACTOR_SOURCE.replace('return "yes"', 'return "no"')

DEFERRED_SOURCE = '''
distributed consensus with ["A", "B"] on "deploy_v2" {
    quorum 2
    timeout 30
    policy "MajorityVote"
    bind vote
}
'''


def _run_live(source=SOURCE, votes=None, *, actor_method=False):
    interpreter = Interpreter()
    if actor_method:
        interpreter.enable_actor_method_vote_source()
    elif votes is not None:
        interpreter.set_consensus_vote_source(ExplicitVoteSource(votes, source_label="test_controlled"))
    interpreter.source_code = source
    interpreter.interpret(compile_to_ast(source))
    event = next(event for event in interpreter.execution_history if event.get("type") == "distributed_consensus_decided")
    return interpreter, interpreter.global_env.get("vote"), event


def _replay(event, source=SOURCE, *, history_prefix=None, configure=None):
    interpreter = Interpreter()
    interpreter.execution_history = list(history_prefix or []) + [deepcopy(event)]
    interpreter.runtime_mode = RuntimeMode.REPLAY
    interpreter.replay_cursor = 0
    if configure is not None:
        configure(interpreter)
    interpreter.source_code = source
    interpreter.interpret(compile_to_ast(source))
    return interpreter


def _assert_replay_failure(event, match, *, source=SOURCE):
    interpreter = Interpreter()
    interpreter.execution_history = [deepcopy(event)]
    before_history = deepcopy(interpreter.execution_history)
    interpreter.runtime_mode = RuntimeMode.REPLAY
    interpreter.source_code = source
    with pytest.raises(ConsensusReplayIntegrityError, match=match):
        interpreter.interpret(compile_to_ast(source))
    assert interpreter.replay_cursor == 0
    assert interpreter.execution_history == before_history
    with pytest.raises(RuntimeError):
        interpreter.global_env.get("vote")


def _run_deferred_live():
    interpreter, result, decided = _run_live(DEFERRED_SOURCE, votes={"A": "yes"})
    ticket = interpreter.execution_history[1]
    return interpreter, result, decided, ticket


def _replay_deferred(history):
    interpreter = Interpreter()
    interpreter.execution_history = deepcopy(history)
    interpreter.runtime_mode = RuntimeMode.REPLAY
    interpreter.source_code = DEFERRED_SOURCE
    interpreter.interpret(compile_to_ast(DEFERRED_SOURCE))
    return interpreter


def test_live_event_v2_is_replay_sufficient_while_public_result_stays_v1():
    interpreter, result, event = _run_live(votes={"Peer": "yes"})

    assert result["schema_version"] == "consensus.result.v1"
    assert event["schema_version"] == "consensus.event.v2"
    assert event["votes"] == {"Peer": "yes"}
    assert event["votes"] == result["votes"]
    for field in ("proposal_id", "votes_hash", "result_hash", "statement_identity"):
        assert event[field]
    assert interpreter.execution_history == [event]


def test_replay_consumes_matching_event_once_binds_engine_result_and_preserves_side_effect_state():
    _, live_result, event = _run_live(votes={"Peer": "yes"})
    prefix = [{"type": "checkpoint", "checkpoint_id": "before-consensus"}]
    tail = {"type": "side_effect", "name": "after_consensus", "result": "later"}
    interpreter = Interpreter()
    interpreter.execution_history = prefix + [deepcopy(event), tail]
    interpreter.runtime_mode = RuntimeMode.REPLAY
    interpreter.source_code = SOURCE
    side_effect_state = {
        "history": deepcopy(interpreter.execution_history),
        "actor_log": deepcopy(interpreter.actor_log),
        "mailboxes": deepcopy(interpreter.mailboxes),
        "promises": deepcopy(interpreter.promises),
        "outbound_packets": deepcopy(interpreter.outbound_packets),
        "tickets": deepcopy(interpreter.consensus_tickets),
    }

    interpreter.interpret(compile_to_ast(SOURCE))

    assert interpreter.global_env.get("vote") == live_result
    assert interpreter.replay_cursor == 2
    assert interpreter.execution_history == side_effect_state["history"]
    assert interpreter.execution_history[interpreter.replay_cursor] == tail
    assert interpreter.actor_log == side_effect_state["actor_log"]
    assert interpreter.mailboxes == side_effect_state["mailboxes"]
    assert interpreter.promises == side_effect_state["promises"]
    assert interpreter.outbound_packets == side_effect_state["outbound_packets"]
    assert interpreter.consensus_tickets == side_effect_state["tickets"]


def test_replay_uses_recorded_votes_without_actor_method_or_live_source_selection():
    _, live_result, event = _run_live(ACTOR_SOURCE, actor_method=True)

    def configure(interpreter):
        interpreter.enable_actor_method_vote_source()
        interpreter._select_consensus_vote_source = lambda: pytest.fail("live VoteSource selected during replay")

    replay = _replay(event, REPLAY_ACTOR_SOURCE, configure=configure)

    assert replay.global_env.get("vote") == live_result
    assert replay._last_actor_method_vote_source is None


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("proposal_id", "sha256:tampered", "proposal_id mismatch / non-determinism"),
        ("statement_identity", "source:999:1", "statement_identity mismatch / non-determinism"),
        ("votes_hash", "sha256:tampered", "votes_hash replay integrity mismatch"),
        ("result_hash", "sha256:tampered", "result_hash replay integrity mismatch"),
    ],
)
def test_replay_integrity_anchor_mismatches_fail_closed(field, value, match):
    _, _, event = _run_live(votes={"Peer": "yes"})
    event[field] = value

    _assert_replay_failure(event, match)


def test_replay_rejects_wrong_pre_frontier_event_without_live_fallthrough():
    wrong_event = {"type": "distributed_consensus_committed", "result": {"committed": True}}

    _assert_replay_failure(wrong_event, "expected distributed_consensus_decided")


@pytest.mark.parametrize(
    "mutate,match",
    [
        (
            lambda event: event.update(schema_version="consensus.event.v1") or event.pop("votes"),
            "unsupported legacy consensus event or missing votes map",
        ),
        (lambda event: event.pop("votes"), "unsupported legacy consensus event or missing votes map"),
        (lambda event: event.__setitem__("votes", ["Peer", "yes"]), "unsupported legacy consensus event or missing votes map"),
    ],
)
def test_replay_rejects_legacy_missing_or_non_mapping_votes(mutate, match):
    _, _, event = _run_live(votes={"Peer": "yes"})
    mutate(event)

    _assert_replay_failure(event, match)


@pytest.mark.parametrize(
    "votes,context",
    [
        ({"Peer": "maybe"}, "unknown_vote_state"),
        ({"Other": "yes"}, "vote_for_unknown_participant"),
        ({"Peer": ["yes"]}, "unknown_vote_state"),
    ],
)
def test_replay_translates_malformed_recorded_votes_with_engine_context(votes, context):
    _, _, event = _run_live(votes={"Peer": "yes"})
    event["votes"] = votes

    _assert_replay_failure(event, f"malformed recorded votes: .*{context}")


def test_replay_frontier_transitions_to_live_consensus_execution():
    interpreter = Interpreter()
    interpreter.runtime_mode = RuntimeMode.REPLAY
    interpreter.set_consensus_vote_source(ExplicitVoteSource({"Peer": "yes"}, source_label="test_controlled"))
    interpreter.source_code = SOURCE

    interpreter.interpret(compile_to_ast(SOURCE))

    assert interpreter.runtime_mode == RuntimeMode.LIVE
    assert interpreter.global_env.get("vote")["outcome"] == "committed"
    assert len(interpreter.execution_history) == 1
    assert interpreter.execution_history[0]["schema_version"] == "consensus.event.v2"


def test_source_labels_do_not_change_consensus_hashes_for_equivalent_votes():
    engine = ConsensusEngine()
    request = ConsensusRequest(
        topic="deploy_v2",
        participants=["Peer"],
        quorum=1,
        statement_identity="source:2:1",
    )
    live = engine.decide(
        ConsensusRequest(**{**request.__dict__, "vote_source": ExplicitVoteSource({"Peer": "yes"}, source_label="test_controlled")})
    )
    replay = engine.decide(
        ConsensusRequest(**{**request.__dict__, "vote_source": ExplicitVoteSource({"Peer": "yes"})})
    )

    assert live.result["votes_hash"] == replay.result["votes_hash"]
    assert live.result["result_hash"] == replay.result["result_hash"]


def test_history_hash_covers_votes_and_is_stable_across_votes_map_ordering():
    event = {
        "type": "distributed_consensus_decided",
        "schema_version": "consensus.event.v2",
        "proposal_id": "sha256:proposal",
        "statement_identity": "source:2:1",
        "votes": {"A": "yes", "B": "no"},
    }
    reordered = {**event, "votes": {"B": "no", "A": "yes"}}
    changed = {**event, "votes": {"A": "no", "B": "no"}}

    assert hash_event_chain([event])[-1]["hash"] == hash_event_chain([reordered])[-1]["hash"]
    assert hash_event_chain([event])[-1]["hash"] != hash_event_chain([changed])[-1]["hash"]


def test_consensus_decision_backward_compatible_without_ticket_fields():
    from synapse.runtime.consensus_engine import ConsensusDecision

    decision = ConsensusDecision(
        result={"outcome": "committed"},
        event_payload={"type": "distributed_consensus_decided"},
        proposal_preimage={},
        votes_preimage={},
        result_preimage={},
    )
    assert decision.ticket_id is None
    assert decision.ticket_payload is None


def test_live_deferred_creates_adjacent_deterministic_ticket_and_projection():
    interpreter, result, decided, ticket = _run_deferred_live()

    assert [event["type"] for event in interpreter.execution_history] == [
        "distributed_consensus_decided",
        "distributed_consensus_ticket_created",
    ]
    assert result["outcome"] == "deferred"
    assert result["reason"] == "pending_missing_votes"
    assert result["ticket_id"] == ticket["ticket_id"]
    assert ticket["proposal_id"] == decided["proposal_id"]
    assert ticket["votes"] == decided["votes"] == {"A": "yes", "B": "missing"}
    assert ticket["missing_participants"] == ["B"]
    assert ticket["schema_version"] == "consensus.ticket.event.v1"
    assert "status" not in ticket and "result_hash" not in ticket
    assert interpreter.consensus_tickets[result["ticket_id"]]["projection_state"] == "pending"
    assert interpreter.consensus_tickets[result["ticket_id"]] is not ticket


@pytest.mark.parametrize(
    "reason,ticket_id,ticket_payload,error",
    [
        ("pending_missing_votes", None, None, "missing_consensus_ticket"),
        ("future_reason", "sha256:ticket", {"ticket_id": "sha256:ticket"}, "unsupported_deferred_reason"),
    ],
)
def test_live_deferred_ticket_preflight_prevents_partial_history(reason, ticket_id, ticket_payload, error):
    class InconsistentConsensusEngine:
        def decide(self, request):
            return ConsensusDecision(
                result={"outcome": "deferred", "reason": reason},
                event_payload={"type": "distributed_consensus_decided"},
                proposal_preimage={},
                votes_preimage={},
                result_preimage={},
                ticket_id=ticket_id,
                ticket_payload=ticket_payload,
            )

    interpreter = Interpreter()
    interpreter._consensus_engine = InconsistentConsensusEngine()
    interpreter.source_code = DEFERRED_SOURCE

    with pytest.raises(RuntimeError, match=error):
        interpreter.interpret(compile_to_ast(DEFERRED_SOURCE))
    assert interpreter.execution_history == []
    assert interpreter.consensus_tickets == {}
    with pytest.raises(RuntimeError):
        interpreter.global_env.get("vote")


def test_ticket_id_is_stable_and_history_hash_covers_ticket_event():
    _, live_result, _, ticket = _run_deferred_live()
    replay = _replay_deferred(_run_deferred_live()[0].execution_history)

    assert replay.global_env.get("vote")["ticket_id"] == live_result["ticket_id"]
    assert hash_event_chain([ticket])[-1]["hash"] != hash_event_chain([{**ticket, "votes": {"A": "missing", "B": "missing"}}])[-1]["hash"]


def test_replay_consumes_deferred_decision_and_adjacent_ticket_without_append():
    live, result, _, ticket = _run_deferred_live()
    replay = _replay_deferred(live.execution_history)

    assert replay.replay_cursor == 2
    assert replay.execution_history == live.execution_history
    assert replay.global_env.get("vote") == result
    assert replay.consensus_tickets[result["ticket_id"]]["ticket_id"] == ticket["ticket_id"]


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("ticket_id", "sha256:tampered", "ticket_id mismatch"),
        ("proposal_id", "sha256:tampered", "proposal_id mismatch"),
        ("statement_identity", "source:99:1", "statement_identity mismatch"),
        ("votes_hash", "sha256:tampered", "votes_hash mismatch"),
        ("votes", {"A": "yes", "B": "yes"}, "votes mismatch"),
        ("missing_participants", [], "missing_participants mismatch"),
    ],
)
def test_ticket_replay_mismatches_restore_cursor_and_do_not_project(field, value, match):
    live, _, _, ticket = _run_deferred_live()
    ticket[field] = value
    replay = Interpreter()
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = DEFERRED_SOURCE

    with pytest.raises(ConsensusReplayIntegrityError, match=match):
        replay.interpret(compile_to_ast(DEFERRED_SOURCE))
    assert replay.replay_cursor == 0
    assert replay.consensus_tickets == {}
    with pytest.raises(RuntimeError):
        replay.global_env.get("vote")


def _assert_ticket_schema_failure(mutate, match="invalid ticket event schema"):
    live, _, _, ticket = _run_deferred_live()
    mutate(ticket)
    replay = Interpreter()
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = DEFERRED_SOURCE

    with pytest.raises(ConsensusReplayIntegrityError, match=match):
        replay.interpret(compile_to_ast(DEFERRED_SOURCE))
    assert replay.replay_cursor == 0
    assert replay.consensus_tickets == {}
    with pytest.raises(RuntimeError):
        replay.global_env.get("vote")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ticket: ticket.__setitem__("status", "pending"),
        lambda ticket: ticket.__setitem__("result_hash", "sha256:extra"),
        lambda ticket: ticket.__setitem__("previous_hash", "sha256:extra"),
        lambda ticket: ticket.__setitem__("projection_state", "pending"),
        lambda ticket: ticket.__setitem__("runtime_uuid", "runtime"),
        lambda ticket: ticket.__setitem__("source_label", "test_controlled"),
        lambda ticket: ticket.pop("timeout"),
        lambda ticket: ticket.pop("votes"),
    ],
)
def test_ticket_replay_requires_closed_event_schema(mutate):
    _assert_ticket_schema_failure(mutate)


def test_ticket_replay_rejects_non_string_event_key_before_field_access():
    _assert_ticket_schema_failure(
        lambda ticket: ticket.__setitem__(1, "extra"),
        match="malformed distributed_consensus_ticket_created event",
    )


@pytest.mark.parametrize(
    "between",
    [
        None,
        {"type": "checkpoint"},
        {"type": "policy_evaluated"},
        {"type": "message_sent"},
        {"type": "distributed_consensus_committed"},
        "not-a-mapping",
    ],
)
def test_deferred_ticket_requires_literal_raw_adjacency(between):
    live, _, decided, ticket = _run_deferred_live()
    history = [decided] if between is None else [decided, between, ticket]
    replay = Interpreter()
    replay.execution_history = deepcopy(history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = DEFERRED_SOURCE

    with pytest.raises(ConsensusReplayIntegrityError, match="consensus ticket replay integrity mismatch"):
        replay.interpret(compile_to_ast(DEFERRED_SOURCE))
    assert replay.replay_cursor == 0
    assert replay.consensus_tickets == {}


def test_terminal_and_insufficient_quorum_outcomes_do_not_create_tickets():
    _, committed, _ = _run_live(votes={"Peer": "yes"})
    rejected = ConsensusEngine().decide(
        ConsensusRequest(
            topic="deploy_v2",
            participants=["A", "B", "C"],
            quorum=2,
            statement_identity="source:2:1",
            vote_source=ExplicitVoteSource({"A": "no", "B": "no"}),
        )
    )

    assert committed["ticket_id"] is None
    assert rejected.result["reason"] == "insufficient_quorum"
    assert rejected.ticket_id is None and rejected.ticket_payload is None


def test_projection_copy_does_not_mutate_ticket_payload_and_failure_rolls_back_cursor():
    live, result, _, ticket = _run_deferred_live()
    original_ticket = deepcopy(ticket)
    projection = live.consensus_tickets[result["ticket_id"]]
    projection["votes"]["B"] = "yes"
    assert ticket == original_ticket

    replay = Interpreter()
    replay.execution_history = deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = DEFERRED_SOURCE
    replay._project_consensus_ticket = lambda *_: (_ for _ in ()).throw(RuntimeError("projection failed"))
    with pytest.raises(ConsensusReplayIntegrityError, match="ticket projection update failed"):
        replay.interpret(compile_to_ast(DEFERRED_SOURCE))
    assert replay.replay_cursor == 0
    assert replay.consensus_tickets == {}


def test_ticket_identity_ignores_vote_source_label_and_advisory_coordinator():
    engine = ConsensusEngine()
    first = engine.decide(
        ConsensusRequest(
            topic="deploy_v2", participants=["A", "B"], quorum=2,
            coordinator="one", statement_identity="source:2:1",
            vote_source=ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"),
        )
    )
    second = engine.decide(
        ConsensusRequest(
            topic="deploy_v2", participants=["B", "A"], quorum=2,
            coordinator="two", statement_identity="source:2:1",
            vote_source=ExplicitVoteSource({"A": "yes"}),
        )
    )

    assert first.ticket_id == second.ticket_id
    assert first.ticket_payload["missing_participants"] == ["B"]


def test_unknown_deferred_reason_fails_before_ticket_event_construction():
    engine = ConsensusEngine()
    engine._evaluate_outcome = lambda *_: ("deferred", "future_reason")
    with pytest.raises(ConsensusValidationError, match="unsupported_deferred_reason"):
        engine.decide(
            ConsensusRequest(
                topic="deploy_v2", participants=["A"], quorum=1,
                statement_identity="source:2:1", vote_source=ExplicitVoteSource({}),
            )
        )


def test_duplicate_ticket_before_next_consensus_statement_fails_closed():
    source = '''
distributed consensus with ["A"] on "first" { quorum 1 bind first }
distributed consensus with ["A"] on "second" { quorum 1 bind second }
'''
    live = Interpreter()
    live.set_consensus_vote_source(ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    live.source_code = source
    live.interpret(compile_to_ast(source))
    duplicate = deepcopy(_run_deferred_live()[3])
    replay = Interpreter()
    replay.execution_history = [live.execution_history[0], duplicate, live.execution_history[1]]
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = source

    with pytest.raises(ConsensusReplayIntegrityError, match="expected distributed_consensus_decided"):
        replay.interpret(compile_to_ast(source))
    assert replay.replay_cursor == 1
