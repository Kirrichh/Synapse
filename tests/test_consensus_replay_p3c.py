from copy import deepcopy

import pytest

from synapse import Interpreter, RuntimeMode, compile_to_ast
from synapse.hardening import hash_event_chain
from synapse.interpreter import ConsensusReplayIntegrityError, RuntimeError
from synapse.runtime.consensus_engine import ConsensusEngine, ConsensusRequest, ExplicitVoteSource


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
