import hashlib

import pytest

from synapse.builtins import AgentRuntime, DurableActorRef
from synapse.hardening import canonical_json
from synapse.runtime.consensus_engine import (
    ConsensusEngine,
    ConsensusRequest,
    ConsensusValidationError,
    ExplicitVoteSource,
    SEMANTIC_REASON_VALUES,
    VoteRecord,
)


def _hash(payload):
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _decide(
    *,
    participants=("A", "B"),
    votes=None,
    strategy=None,
    quorum=None,
    timeout=0,
    topic="deploy",
):
    source = ExplicitVoteSource(votes or {}, source_label="test_controlled")
    return ConsensusEngine().decide(
        ConsensusRequest(
            topic=topic,
            participants=list(participants),
            quorum=quorum,
            timeout=timeout,
            policy_ref=strategy,
            coordinator="coordinator",
            statement_identity="source:1:1",
            vote_source=source,
        )
    )


def _invalid(**kwargs):
    with pytest.raises(ConsensusValidationError) as exc:
        _decide(**kwargs)
    return str(exc.value)


def test_participant_validation_failures():
    assert "empty_participants" in _invalid(participants=())
    assert "duplicate_participant" in _invalid(participants=("A", " A "))
    assert "unresolved_participant" in _invalid(participants=(None,))
    assert "unsupported_participant_identity" in _invalid(participants=(object(),))


def test_supported_participant_identity_sources():
    agent = AgentRuntime("AgentA", "mock")
    actor_ref = DurableActorRef(actor_name="ActorA", process_id="ActorA#not-semantic")
    decision = _decide(
        participants=(actor_ref, agent),
        votes={"ActorA": "yes", "AgentA": "yes"},
    )
    assert decision.result["participants"] == ["ActorA", "AgentA"]
    assert "ActorA#not-semantic" not in decision.result["participants"]


def test_unsupported_object_rejected_before_hashing():
    assert "unsupported_canonical_value" in _invalid(
        participants=("A",),
        votes={"A": "yes"},
        topic=object(),
    )


def test_vote_validation_and_explicit_states():
    decision = _decide(
        participants=("A", "B", "C", "D"),
        votes={"A": "yes", "B": "no", "C": "abstain", "D": "missing"},
        quorum=4,
    )
    assert decision.result["votes"] == {
        "A": "yes",
        "B": "no",
        "C": "abstain",
        "D": "missing",
    }
    assert decision.result["vote_counts"] == {
        "yes": 1,
        "no": 1,
        "abstain": 1,
        "missing": 1,
    }


def test_conflicting_votes_and_unknown_vote_participant_fail():
    source = ExplicitVoteSource(
        [VoteRecord("A", "yes", "test_controlled"), VoteRecord("A", "no", "test_controlled")]
    )
    with pytest.raises(ConsensusValidationError, match="conflicting_vote"):
        ConsensusEngine().decide(
            ConsensusRequest(
                topic="x",
                participants=["A"],
                statement_identity="source:1:1",
                vote_source=source,
            )
        )
    assert "vote_for_unknown_participant" in _invalid(votes={"C": "yes"})
    assert "unknown_vote_state" in _invalid(votes={"A": "maybe"})


def test_quorum_and_timeout_validation():
    assert "quorum_out_of_bounds" in _invalid(quorum=0)
    assert "quorum_out_of_bounds" in _invalid(quorum=3)
    assert "negative_timeout" in _invalid(timeout=-1)
    assert "non_integer_quorum" in _invalid(quorum="1")
    assert "non_integer_quorum" in _invalid(quorum=True)
    assert "non_integer_timeout" in _invalid(timeout="1")
    assert "non_integer_timeout" in _invalid(timeout=False)


def test_strategy_defaults_and_case_sensitive_matching():
    decision = _decide(participants=("A",), votes={"A": "yes"}, strategy=None)
    assert decision.result["strategy"] == "MajorityVote"
    assert decision.result["policy"] is None
    assert "unknown_strategy" in _invalid(strategy="majorityvote")
    assert "unknown_strategy" in _invalid(strategy="UnknownVote")


def test_default_quorum_correction_for_odd_and_even_counts():
    for count, expected in [(1, 1), (3, 2), (5, 3), (2, 2), (4, 3), (6, 4)]:
        participants = tuple(f"P{i}" for i in range(count))
        decision = _decide(participants=participants, votes={}, strategy="MajorityVote")
        assert decision.result["quorum"] == expected
    decision = _decide(participants=("A", "B", "C"), votes={}, strategy="NoVetoVote")
    assert decision.result["quorum"] == 2


def test_majority_vote_outcomes():
    assert _decide(votes={"A": "yes", "B": "yes"}).result["reason"] == "quorum_reached"
    deferred = _decide(participants=("A", "B", "C"), votes={"A": "yes"})
    assert deferred.result["outcome"] == "deferred"
    assert deferred.result["reason"] == "pending_missing_votes"
    rejected = _decide(participants=("A", "B", "C"), votes={"A": "no", "B": "no"})
    assert rejected.result["outcome"] == "rejected"
    assert rejected.result["reason"] == "insufficient_quorum"


def test_unanimous_vote_outcomes():
    committed = _decide(
        participants=("A", "B"),
        votes={"A": "yes", "B": "yes"},
        strategy="UnanimousVote",
    )
    assert committed.result["reason"] == "unanimity_reached"
    by_no = _decide(
        participants=("A", "B"),
        votes={"A": "yes", "B": "no"},
        strategy="UnanimousVote",
    )
    assert by_no.result["reason"] == "unanimity_broken_by_no"
    by_abstain = _decide(
        participants=("A", "B"),
        votes={"A": "yes", "B": "abstain"},
        strategy="UnanimousVote",
    )
    assert by_abstain.result["reason"] == "unanimity_broken_by_abstain"


def test_no_veto_vote_outcomes():
    committed = _decide(
        participants=("A", "B", "C"),
        votes={"A": "yes", "B": "yes"},
        strategy="NoVetoVote",
    )
    assert committed.result["reason"] == "no_veto_quorum_reached"
    rejected = _decide(
        participants=("A", "B", "C"),
        votes={"A": "yes", "B": "no"},
        strategy="NoVetoVote",
    )
    assert rejected.result["reason"] == "explicit_no_vote"


def test_canonical_hashes_and_closed_reason_values():
    decision = _decide(participants=("B", "A"), votes={"A": "yes", "B": "missing"})
    assert decision.result["participants"] == ["A", "B"]
    assert decision.result["proposal_id"] == _hash(decision.proposal_preimage)
    assert decision.result["votes_hash"] == _hash(
        {
            "schema_version": "consensus.votes.v1",
            "votes": [["A", "yes"], ["B", "missing"]],
        }
    )
    assert decision.result["result_hash"] == _hash(decision.result_preimage)
    assert decision.result["reason"] in SEMANTIC_REASON_VALUES
    assert SEMANTIC_REASON_VALUES == {
        "quorum_reached",
        "unanimity_reached",
        "no_veto_quorum_reached",
        "explicit_no_vote",
        "unanimity_broken_by_no",
        "unanimity_broken_by_abstain",
        "insufficient_quorum",
        "pending_missing_votes",
    }
