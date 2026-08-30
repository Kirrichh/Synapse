"""Stage 4 result authority and identity acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_replay_cannot_express_full_at_all() -> None:
    """NR-14 for this stage: replay success does not establish FULL.

    The check is structural rather than behavioural, because a *guarded* FULL is
    still a FULL this module could produce, and a guard is one edit away from
    being removed. §26 owns the outcome vocabulary; nothing executable here may
    name a member of it.
    """

    named = _executable_names(R) & _OUTCOME_VOCABULARY
    assert not named, f"replay.py names the outcome concept(s) {sorted(named)}"
    assert not [item for item in dir(R) if "FULL" in item.upper()]
    assert not [item for item in dir(R) if "COMPLETENESS" in item.upper()]

def test_a_result_carries_no_verdict_field() -> None:
    prepared = pure_prepared()
    result = prepared.run()
    fields = set(result.to_dict())
    assert not fields & {
        "outcome", "verdict", "completeness", "correctness", "task_success", "full"
    }
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL

def test_an_observation_makes_no_claim_about_task_success() -> None:
    """§23: replay observations do not gain instruction or task-success authority."""

    prepared = pure_prepared()
    result = prepared.run()
    stored = result.observations[0].to_dict()
    payload = stored["payload"]
    assert not set(payload) & {
        "correct", "passed", "verdict", "task_success", "oracle", "authority"
    }
    assert set(payload) >= {"transition_hash_chain", "transcript_matched", "failure_reason"}

def test_identity_requires_a_root_pinned_before_the_run() -> None:
    """A sorted-set contract cannot see a permutation; a pinned root can.

    Both halves of that sentence, asked of the rule that decides. A run whose
    every transition matched, but for which nothing was pinned in advance, is
    not an identity — there is nothing that could have distinguished it from the
    same transitions in another order.
    """

    result = pure_prepared().run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert all(item.transcript_matched for item in result.observations)

    status, reason = R.replay_verdict(
        observations=result.observations,
        stopping_reason=None,
        expected_transcript_root=None,
        expected_terminal_snapshot_digests=result.terminal_snapshot_digests,
    )
    assert status is R.ReplayStatus.REPLAY_FAILED
    assert reason is R.ReplayFailureReason.TRANSITION_MISMATCH

    # The permutation the sorted sets cannot see: the same transitions folded in
    # another order are another root, so a root pinned in advance separates them.
    transitions = tuple(result.transition_hash_chain)
    assert len(transitions) > 1, "a permutation needs at least two transitions"
    assert R.transcript_root(transitions=transitions, activities=()) != R.transcript_root(
        transitions=tuple(reversed(transitions)), activities=()
    )

def test_a_result_cannot_be_built_by_its_constructor() -> None:
    for factory in (R.BehaviorReplayRequest, R.BehaviorReplayResult, R.ReplayObservation):
        with pytest.raises(TypeError):
            factory()  # type: ignore[call-arg]

def test_rewriting_a_result_field_invalidates_it() -> None:
    prepared = pure_prepared()
    result = prepared.run()
    object.__setattr__(result, "status", R.ReplayStatus.REPLAY_FAILED)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT

def test_a_forged_identical_status_over_a_failed_run_is_refused() -> None:
    """A sealed result of a run that failed cannot be relabelled as an identity.

    The forgery is applied to a *sealed* result, which is what the rule is about.
    An earlier revision forged the two fields onto the transition driver's raw
    output and asked ``validate_replay_result`` about it; that object is not a
    result at all, so the refusal it got was about the forgery's shape and never
    reached the status rule.
    """

    starved, _ = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=3)
    failed = starved.run()
    assert failed.status is R.ReplayStatus.REPLAY_FAILED
    object.__setattr__(failed, "status", R.ReplayStatus.REPLAY_IDENTICAL)
    object.__setattr__(failed, "failure_reason", None)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(failed)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT

def test_a_forged_identical_status_over_an_unpinned_run_is_refused() -> None:
    """Every observation matched, but no root was pinned. Still not identity.

    This is the case the observation check cannot catch — the run was clean —
    so the pinned-root requirement has to be enforced in validation on its own.
    """

    result = pure_prepared().run()
    assert all(item.transcript_matched for item in result.observations)
    # The one field the rule reads, unpinned on an otherwise honest identity.
    # ``root_matches_expectation`` is derived from it, so this is the smallest
    # edit that produces the state under test.
    object.__setattr__(result, "expected_transcript_root", None)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT

def test_a_status_that_its_reason_does_not_produce_is_refused() -> None:
    """The mapping is enforced, not merely consulted.

    A program-hash mismatch is an incompatibility; recording it as a failure
    would misreport whether the behavior or its execution contract was at fault.
    """

    # A sealed result, because the rule under test is the validator's: a status
    # its own reason does not produce must not survive validation. The pairing is
    # forced onto an otherwise honest record rather than obtained from a
    # misbehaving machine, which is what makes the case about the check.
    prepared = pure_prepared()
    result = prepared.run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    object.__setattr__(result, "failure_reason", R.ReplayFailureReason.PROGRAM_HASH_MISMATCH)
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT

def test_rewriting_the_transcript_invalidates_the_root() -> None:
    prepared = pure_prepared()
    result = prepared.run()
    object.__setattr__(result, "transition_hash_chain", result.transition_hash_chain[:-1])
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.validate_replay_result(result)
    assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
