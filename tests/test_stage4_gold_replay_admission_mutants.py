"""Stage 4 admission and replay mutant killers acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_mutant_replay_reinvokes_an_external_activity_is_killed() -> None:
    """Mutant: a ledger miss falls through to a live call.

    Three barriers are checked: the adapter has no producer to reach when no
    channel is attached, the channel raises on a miss, and the driver turns
    that into a stopped run with a typed reason rather than a step.
    """

    _, program, _ = effect_fixture()
    adapter = vm_adapter(program)
    with pytest.raises(R.ReplayViolation):
        for _ in range(10):
            adapter.step()

    channel = channel_for(budget=8)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        channel.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"nobody recorded this"),
            position=ACT.ActivityPosition(
                program_hash="sha256:scripted", instruction_pointer=0, frame_depth=0, sequence=1
            ),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED

    prepared, _ = scripted_prepared(["ADD", "LLM_EVAL"])
    result = run_scripted(prepared, opcodes=["ADD", "LLM_EVAL"], on_step=consuming_step())
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_FAILED
    assert result.failure_reason is R.ReplayFailureReason.MISSING_ACTIVITY_RECORD
    assert result.steps_executed == 1

def test_mutant_a_different_program_hash_is_accepted_is_killed() -> None:
    """Mutant: the execution-contract check before the first transition is dropped."""

    prepared, _ = scripted_prepared(["ADD"])
    result = run_scripted(prepared, program="sha256:some-other-program", opcodes=["ADD"])
    assert R.status_for_reason(result.failure_reason) is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH
    assert result.steps_executed == 0
    assert result.port.channel is None, "the channel opened before the program was verified"

def test_mutant_a_missing_transition_is_ignored_is_killed() -> None:
    """Mutant: the comparison checks the set but not the count, or vice versa.

    Three defects are exercised, because the two halves of the comparison fail
    independently. A substituted transition keeps the count, so a count-only
    check passes it. A duplicate transition standing in for a missing one keeps
    the deduplicated set, so a set-only check passes that. And a mismatch that
    is detected but not turned into a stopping reason is a third way to ignore
    the same thing.

    All three are asserted at the observation. The result's reason is also
    reachable from the pinned-root comparison, so a result-level assertion would
    survive a contract comparison that had been removed entirely.
    """

    short_prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"])
    assert_contract_rejected(run_scripted(short_prepared, opcodes=["ADD", "SUB"]))

    swapped_prepared, _ = scripted_prepared(["ADD", "SUB", "MUL"])
    assert_contract_rejected(
        run_scripted(swapped_prepared, opcodes=["ADD", "DIV", "MUL"])
    )

    duplicate_prepared, transitions = scripted_prepared(["ADD", "SUB"])
    duplicated = [transitions[0], *transitions]
    assert_contract_rejected(
        run_scripted(
            duplicate_prepared,
            opcodes=["ADD"] * len(duplicated),
            hash_script=duplicated,
        )
    )

def test_mutant_the_result_sets_full_by_itself_is_killed() -> None:
    """Mutant: the replay result asserts an outcome verdict of its own.

    Four checks. The module names no member of the §26 vocabulary; the result
    exposes no verdict field; the status vocabulary contains only the four §23
    members; and a status forged onto a failed run does not survive validation.
    """

    assert not _executable_names(R) & _OUTCOME_VOCABULARY

    prepared = pure_prepared()
    result = prepared.run()
    assert not set(result.to_dict()) & {"outcome", "verdict", "completeness", "full"}
    assert {item.value for item in R.ReplayStatus} == {
        "REPLAY_IDENTICAL", "REPLAY_INCOMPATIBLE", "REPLAY_FAILED", "INFRA_ERROR"
    }

    # Forged onto a run that failed, and onto a clean run whose root was never
    # pinned. Two barriers, and each has to hold on its own.
    starved, _transitions = scripted_prepared(["ADD", "SUB", "MUL"], gas_budget=3)
    failed = starved.run()
    assert failed.status is R.ReplayStatus.REPLAY_FAILED
    object.__setattr__(failed, "status", R.ReplayStatus.REPLAY_IDENTICAL)
    object.__setattr__(failed, "failure_reason", None)

    unpinned = pure_prepared().run()
    assert unpinned.status is R.ReplayStatus.REPLAY_IDENTICAL
    object.__setattr__(unpinned, "expected_transcript_root", None)

    for forged in (failed, unpinned):
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_result(forged)
        assert excinfo.value.failure_code is R.ReplayFailureCode.STATUS_REASON_INCONSISTENT

def test_mutant_the_consumption_gate_is_skipped_before_replay_is_killed() -> None:
    """The compiler is bracketed by two distinct fresh §22 evaluations."""

    import inspect

    source = inspect.getsource(R._prepare_replay)
    first = source.index("_admit_now(initial_request)")
    compiled = source.index("replay_program_binding")
    final = source.index("_admit_now(binding.final_admission)")
    assert first < compiled < final
