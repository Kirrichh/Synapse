import inspect

import pytest

from synapse import compile_to_ast, Interpreter, RuntimeMode
from synapse.application import durable_ast_inventory
from synapse.interpreter import RuntimeError
from synapse.runtime.consensus_engine import ExplicitVoteSource


def _run(source, vote_source=None):
    interp = Interpreter()
    if vote_source is not None:
        interp.set_consensus_vote_source(vote_source)
    interp.source_code = source
    interp.interpret(compile_to_ast(source))
    return interp


def _events(interp, event_type):
    return [event for event in interp.execution_history if event.get("type") == event_type]


def test_configured_vote_source_seam_commits_and_emits_canonical_event():
    src = """
agent Peer { model "mock" }
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    timeout 30
    policy "MajorityVote"
    bind vote
}
print(vote.committed)
"""
    interp = _run(src, ExplicitVoteSource({"Peer": "yes"}, source_label="test_controlled"))
    assert "True" in interp.get_output()
    result = interp.global_env.get("vote")
    assert result["outcome"] == "committed"
    assert result["reason"] == "quorum_reached"
    assert result["ticket_id"] is None
    event = _events(interp, "distributed_consensus_decided")[0]
    assert event["schema_version"] == "consensus.event.v1"
    assert event["proposal_id"] == result["proposal_id"]
    assert event["result_hash"] == result["result_hash"]
    assert event["votes_hash"] == result["votes_hash"]
    assert "votes" not in event
    assert not _events(interp, "distributed_consensus_committed")
    assert not _events(interp, "distributed_consensus_deferred")


def test_null_vote_source_missing_votes_no_default_commit_and_no_hidden_voters():
    src = """
agent Peer { model "mock" }
let self = Peer
distributed consensus with [Peer] on "deploy_v2" {
    quorum 1
    policy "MajorityVote"
    bind vote
}
print(vote.committed)
"""
    interp = _run(src)
    result = interp.global_env.get("vote")
    assert "False" in interp.get_output()
    assert result["outcome"] == "deferred"
    assert result["reason"] == "pending_missing_votes"
    assert result["votes"] == {"Peer": "missing"}
    assert "global" not in result["votes"]
    assert result["coordinator"] == "Peer"
    assert _events(interp, "distributed_consensus_decided")
    assert not _events(interp, "distributed_consensus_committed")
    assert not _events(interp, "distributed_consensus_deferred")
    assert interp.actor_log == []
    assert interp.consensus_tickets == {}


def test_top_level_execution_has_no_hidden_voter():
    src = """
distributed consensus with ["Peer"] on "deploy_v2" {
    quorum 1
    bind vote
}
"""
    interp = _run(src)
    result = interp.global_env.get("vote")
    assert result["coordinator"] == "global"
    assert result["participants"] == ["Peer"]
    assert result["votes"] == {"Peer": "missing"}


def test_coordinator_is_advisory_and_excluded_from_semantic_identity():
    with_self = """
agent Peer { model "mock" }
let self = Peer
distributed consensus with ["A"] on "topic" {
    quorum 1
    bind vote
}
"""
    top_level = """


distributed consensus with ["A"] on "topic" {
    quorum 1
    bind vote
}
"""
    source = ExplicitVoteSource({"A": "yes"}, source_label="test_controlled")
    interp_actor = _run(with_self, source)
    interp_global = _run(top_level, source)
    actor_result = interp_actor.global_env.get("vote")
    global_result = interp_global.global_env.get("vote")
    assert actor_result["coordinator"] == "Peer"
    assert global_result["coordinator"] == "global"
    assert actor_result["proposal_id"] == global_result["proposal_id"]
    assert actor_result["result_hash"] == global_result["result_hash"]
    assert actor_result["outcome"] == global_result["outcome"] == "committed"


def test_two_statements_have_distinct_source_statement_identities():
    src = """
distributed consensus with ["A"] on "one" {
    quorum 1
    bind first_vote
}

distributed consensus with ["A"] on "two" {
    quorum 1
    bind second_vote
}
"""
    interp = _run(src, ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    events = _events(interp, "distributed_consensus_decided")
    identities = [event["statement_identity"] for event in events]
    assert len(events) == 2
    assert identities == ["source:2:12", "source:7:12"]
    assert len(set(identities)) == 2
    assert interp.global_env.get("first_vote")["proposal_id"] != interp.global_env.get("second_vote")["proposal_id"]


def test_governance_policy_name_extracted_without_guard_execution():
    src = """
policy MajorityVote {
    target "consensus.*"
    guard(sender) {
        print("guard_executed")
    }
}
distributed consensus with ["A"] on "topic" {
    quorum 1
    policy MajorityVote
    bind vote
}
"""
    interp = _run(src, ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    result = interp.global_env.get("vote")
    assert result["policy"] == "MajorityVote"
    assert result["strategy"] == "MajorityVote"
    assert "guard_executed" not in interp.get_output()


def test_policy_literal_not_shadowed_by_environment_binding():
    src = """
let MajorityVote = "NoVetoVote"
distributed consensus with ["A"] on "topic" {
    quorum 1
    policy "MajorityVote"
    bind vote
}
"""
    interp = _run(src, ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    result = interp.global_env.get("vote")
    assert result["policy"] == "MajorityVote"
    assert result["strategy"] == "MajorityVote"
    assert result["outcome"] == "committed"
    assert "guard_executed" not in interp.get_output()


def test_invalid_policy_strategy_validation_has_no_binding_or_event():
    src = """
policy BadStrategy {
    target "consensus.*"
    guard(sender) {
        print("guard_executed")
    }
}
distributed consensus with ["A"] on "topic" {
    policy BadStrategy
    bind vote
}
"""
    interp = Interpreter()
    interp.set_consensus_vote_source(ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    interp.source_code = src
    with pytest.raises(RuntimeError, match="invalid_request: unknown_strategy"):
        interp.interpret(compile_to_ast(src))
    with pytest.raises(RuntimeError):
        interp.global_env.get("vote")
    assert "guard_executed" not in interp.get_output()
    assert not _events(interp, "distributed_consensus_decided")
    assert not _events(interp, "distributed_consensus_committed")
    assert interp.actor_log == []
    assert interp.consensus_tickets == {}


def test_no_llm_daemon_network_actor_log_or_ticket_side_effects():
    src = """
distributed consensus with ["A"] on "topic" {
    bind vote
}
"""
    interp = _run(src, ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    assert not _events(interp, "llm_call")
    assert interp.outbound_packets == []
    assert interp.actor_log == []
    assert interp.consensus_tickets == {}
    assert interp.global_env.get("vote")["ticket_id"] is None


def test_durable_allowlist_not_expanded_for_distributed_consensus():
    rows = {
        row["class_name"]: row
        for row in durable_ast_inventory()
    }
    assert rows["DistributedConsensusStmt"]["classification"] == "UNSUPPORTED_EXECUTION_ENGINE"
    assert rows["DistributedConsensusStmt"]["constraint"] == "distributed consensus"


def test_replay_mode_does_not_consume_legacy_distributed_consensus_events():
    src = """
distributed consensus with ["A"] on "topic" {
    bind vote
}
"""
    interp = Interpreter()
    interp.set_consensus_vote_source(ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    interp.execution_history = [{"type": "distributed_consensus_committed", "result": {"committed": True}}]
    interp.runtime_mode = RuntimeMode.REPLAY
    interp.replay_cursor = 0
    interp.source_code = src
    interp.interpret(compile_to_ast(src))
    assert interp.replay_cursor == 0
    assert [event["type"] for event in interp.execution_history] == [
        "distributed_consensus_committed",
        "distributed_consensus_decided",
    ]
    assert interp.global_env.get("vote")["outcome"] == "committed"


def test_validation_error_does_not_bind_or_append_event():
    src = """
distributed consensus with ["A"] on "topic" {
    quorum 2
    bind vote
}
"""
    interp = Interpreter()
    interp.set_consensus_vote_source(ExplicitVoteSource({"A": "yes"}, source_label="test_controlled"))
    interp.source_code = src
    with pytest.raises(RuntimeError, match="invalid_request: quorum_out_of_bounds"):
        interp.interpret(compile_to_ast(src))
    with pytest.raises(RuntimeError):
        interp.global_env.get("vote")
    assert interp.execution_history == []
    assert interp.actor_log == []
    assert interp.consensus_tickets == {}


def test_interpreter_adapter_delegates_semantics_to_engine():
    source = inspect.getsource(Interpreter.evaluate_distributed_consensus)
    assert "ConsensusRequest" in source
    assert "self._consensus_engine.decide" in source
    assert "votes = {p: \"yes\"" not in source
    assert "uuid.uuid4" not in source
    assert "consensus_tickets" not in source
    assert "actor_log.append" not in source
