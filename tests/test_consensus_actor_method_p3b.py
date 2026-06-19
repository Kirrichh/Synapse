import inspect

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.builtins import DurableActorRef
from synapse.interpreter import RuntimeError as SynapseRuntimeError
from synapse.runtime.consensus_engine import (
    ConsensusEngine,
    ConsensusRequest,
    ExplicitVoteSource,
)
from synapse.runtime.consensus_proposal_view import (
    FrozenDict,
    FrozenList,
    ProposalViewMutationError,
)
from synapse.runtime.consensus_vote_sources import ActorMethodVoteSource


def _run(source, *, enable_actor_method=False, vote_source=None):
    interp = Interpreter()
    if enable_actor_method:
        interp.enable_actor_method_vote_source()
    if vote_source is not None:
        interp.set_consensus_vote_source(vote_source)
    interp.source_code = source
    interp.interpret(compile_to_ast(source))
    return interp


def _events(interp):
    return [event for event in interp.execution_history if event.get("type") == "distributed_consensus_decided"]


def _source(method_body='return "yes"'):
    return f'''
agent Peer {{
    model "mock"
    fn consensus_vote(proposal) {{
        {method_body}
    }}
}}
distributed consensus with [Peer] on "deploy_v2" {{
    quorum 1
    timeout 0
    policy "MajorityVote"
    bind vote
}}
'''


def test_actor_method_source_is_explicitly_enabled_and_emits_p3a_event():
    interp = _run(_source(), enable_actor_method=True)
    result = interp.global_env.get("vote")
    assert result["committed"] is True
    assert result["reason"] == "quorum_reached"
    assert result["votes"] == {"Peer": "yes"}
    assert _events(interp)[0]["type"] == "distributed_consensus_decided"
    assert interp._last_actor_method_vote_source.last_vote_diagnostics == {"Peer": "actor_method"}


def test_default_source_stays_null_and_does_not_query_actor_method():
    interp = _run(_source('print("should_not_run")'))
    result = interp.global_env.get("vote")
    assert result["votes"] == {"Peer": "missing"}
    assert result["outcome"] == "deferred"
    assert result["reason"] == "pending_missing_votes"
    assert "should_not_run" not in interp.get_output()
    assert interp._last_actor_method_vote_source is None


@pytest.mark.parametrize("enable_first", [True, False])
def test_explicit_vote_source_override_wins_over_actor_method(enable_first):
    interp = Interpreter()
    explicit = ExplicitVoteSource({"Peer": "no"}, source_label="test_controlled")
    if enable_first:
        interp.enable_actor_method_vote_source()
        interp.set_consensus_vote_source(explicit)
    else:
        interp.set_consensus_vote_source(explicit)
        interp.enable_actor_method_vote_source()
    source = _source('print("actor_method_called")\n        return "yes"')
    interp.source_code = source
    interp.interpret(compile_to_ast(source))
    assert interp.global_env.get("vote")["votes"] == {"Peer": "no"}
    assert "actor_method_called" not in interp.get_output()
    assert interp._last_actor_method_vote_source is None


def test_clearing_explicit_source_restores_enabled_actor_method_fallback():
    interp = Interpreter()
    interp.enable_actor_method_vote_source()
    interp.set_consensus_vote_source(ExplicitVoteSource({"Peer": "no"}, source_label="test_controlled"))
    interp.set_consensus_vote_source(None)
    assert interp._consensus_vote_source_registry_version == 3
    source = _source()
    interp.source_code = source
    interp.interpret(compile_to_ast(source))
    assert interp.global_env.get("vote")["votes"] == {"Peer": "yes"}


def test_actor_method_receives_frozen_proposal_with_supported_access_patterns():
    source = _source(
        '''
if proposal.topic == "deploy_v2" and proposal.__getitem__("topic") == "deploy_v2" and proposal.get("topic") == "deploy_v2" and proposal.proposal_id != "" and proposal.strategy == "MajorityVote" and proposal.participants.length == 1 {
    return "yes"
}
return "no"'''
    )
    interp = _run(source, enable_actor_method=True)
    assert interp.global_env.get("vote")["votes"] == {"Peer": "yes"}


@pytest.mark.parametrize(
    "method_body",
    [
        'proposal.topic = "changed"\n        return "yes"',
        'proposal.clear()\n        return "yes"',
        'proposal.update({"topic": "changed"})\n        return "yes"',
        'proposal.participants.append("X")\n        return "yes"',
        'proposal.participants.pop()\n        return "yes"',
    ],
)
def test_proposal_view_mutation_aborts_without_binding_or_event(method_body):
    interp = Interpreter()
    interp.enable_actor_method_vote_source()
    source = _source(method_body)
    interp.source_code = source
    with pytest.raises(SynapseRuntimeError, match="invalid_request: proposal_view_mutated"):
        interp.interpret(compile_to_ast(source))
    with pytest.raises(SynapseRuntimeError):
        interp.global_env.get("vote")
    assert not _events(interp)


def test_frozen_views_block_direct_nested_mutation_without_host_errors():
    proposal = FrozenDict({"topic": "deploy", "participants": ["Peer"]})
    assert proposal.topic == proposal["topic"] == proposal.get("topic") == "deploy"
    assert isinstance(proposal.participants, FrozenList)
    for mutation in (
        lambda: proposal.__setitem__("topic", "changed"),
        lambda: proposal.clear(),
        lambda: proposal.participants.append("X"),
        lambda: proposal.participants.pop(),
        lambda: proposal.participants.__setitem__(0, "X"),
    ):
        with pytest.raises(ProposalViewMutationError):
            mutation()


def test_missing_method_and_non_local_participants_are_missing_with_local_diagnostics():
    no_method = '''
agent Peer { model "mock" }
distributed consensus with [Peer, "StringPeer"] on "deploy_v2" {
    quorum 2
    policy "MajorityVote"
    bind vote
}
'''
    interp = _run(no_method, enable_actor_method=True)
    assert interp.global_env.get("vote")["votes"] == {"Peer": "missing", "StringPeer": "missing"}
    assert interp._last_actor_method_vote_source.last_vote_diagnostics == {
        "Peer": "actor_method_missing",
        "StringPeer": "actor_not_local",
    }

    durable = DurableActorRef("DurablePeer", "durable-process-id")
    source = ActorMethodVoteSource(interp)
    decision = ConsensusEngine().decide(
        ConsensusRequest(
            topic="deploy_v2",
            participants=[durable],
            quorum=1,
            statement_identity="source:1:1",
            vote_source=source,
        )
    )
    assert decision.result["votes"] == {"DurablePeer": "missing"}
    assert source.last_vote_diagnostics == {"DurablePeer": "actor_not_local"}


@pytest.mark.parametrize(
    "method_body,label",
    [
        ('return "maybe"', "actor_method_invalid"),
        ("return 42", "actor_method_invalid"),
        ('return {"unexpected": "yes"}', "actor_method_invalid"),
        ("return 1 / 0", "actor_method_exception"),
    ],
)
def test_invalid_or_faulting_participant_methods_become_missing(method_body, label):
    interp = _run(_source(method_body), enable_actor_method=True)
    assert interp.global_env.get("vote")["votes"] == {"Peer": "missing"}
    assert interp._last_actor_method_vote_source.last_vote_diagnostics == {"Peer": label}


def test_advisory_reason_is_accepted_but_not_exposed_in_public_schema():
    interp = _run(_source('return {"vote": "yes", "reason": "local note"}'), enable_actor_method=True)
    result = interp.global_env.get("vote")
    event = _events(interp)[0]
    assert result["votes"] == {"Peer": "yes"}
    assert "vote_diagnostics" not in result
    assert "vote_diagnostics" not in event
    assert "actor_method" not in event.values()


@pytest.mark.parametrize(
    "method_body,reason",
    [
        ('print("nope")\n        return "yes"', "vote_collection_side_effect"),
        ('runtime.enable_actor_method_vote_source()\n        return "yes"', "dynamic_votesource_registration"),
        ('distributed consensus with [Peer] on "inner" { quorum 1 bind inner }\n        return "yes"', "vote_collection_side_effect"),
    ],
)
def test_contract_violations_abort_whole_evaluation(method_body, reason):
    interp = Interpreter()
    interp.enable_actor_method_vote_source()
    interp.global_env.define("runtime", interp)
    source = _source(method_body)
    interp.source_code = source
    with pytest.raises(SynapseRuntimeError, match=f"invalid_request: {reason}"):
        interp.interpret(compile_to_ast(source))
    with pytest.raises(SynapseRuntimeError):
        interp.global_env.get("vote")
    assert not _events(interp)
    assert interp.actor_log == []
    assert interp.consensus_tickets == {}


def test_python_callable_and_agent_special_members_are_blocked_before_execution():
    calls = []

    def unsafe_callable():
        calls.append("called")
        return "yes"

    for method_body in ("unsafe()", "self.think(\"vote\")"):
        interp = Interpreter()
        interp.enable_actor_method_vote_source()
        interp.global_env.define("unsafe", unsafe_callable)
        source = _source(method_body)
        interp.source_code = source
        with pytest.raises(SynapseRuntimeError, match="invalid_request: vote_collection_side_effect"):
            interp.interpret(compile_to_ast(source))
        assert not _events(interp)
    assert calls == []


def test_actor_votes_preserve_p3a_hash_contract_for_equivalent_vote_values():
    source = _source()
    interp = _run(source, enable_actor_method=True)
    peer = interp.global_env.get("Peer")
    common = dict(
        topic="deploy_v2",
        participants=[peer],
        quorum=1,
        timeout=0,
        policy_ref="MajorityVote",
        statement_identity="source:100:1",
    )
    explicit = ConsensusEngine().decide(
        ConsensusRequest(**common, vote_source=ExplicitVoteSource({"Peer": "yes"}))
    )
    actor_source = ActorMethodVoteSource(interp)
    actor = ConsensusEngine().decide(ConsensusRequest(**common, vote_source=actor_source))
    assert actor.result["votes_hash"] == explicit.result["votes_hash"]
    assert actor.result["result_hash"] == explicit.result["result_hash"]
    assert actor.result["outcome"] == explicit.result["outcome"] == "committed"
    assert actor.result["reason"] == explicit.result["reason"] == "quorum_reached"


def test_snapshot_implementation_uses_only_stable_structural_values():
    source = inspect.getsource(Interpreter._consensus_vote_side_effect_snapshot)
    assert "default=str" not in source
    assert "repr(" not in source
    assert "id(" not in source

