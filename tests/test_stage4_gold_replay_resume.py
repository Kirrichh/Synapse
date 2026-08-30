"""Stage 4 snapshot and resume acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_the_golden_vm_snapshot_restores_to_the_recorded_terminal_state() -> None:
    record = golden("pure_add_v1")
    snapshot = (GOLDEN / "pure_add_v1.vm_snapshot.json").read_bytes()
    resumed = restore_vm_adapter(snapshot)
    assert resumed.program_hash() == record["program_hash"]
    assert resumed.snapshot_digest() == record["expected_terminal_snapshot_digest"]
    assert resumed.transition_hash() == record["expected_transition_ids"][-1]

def test_the_effect_snapshot_is_the_state_the_injected_result_produced() -> None:
    """The effect fixture's snapshot, and the run that is supposed to reach it.

    ``llm_effect_v1`` is the behaviour whose ``LLM_EVAL`` is served from record,
    so its terminal state is the one place where "the exact recorded bytes were
    injected" becomes a durable artifact rather than an assertion about a run.
    Both directions are checked: the stored snapshot restores to the digest the
    manifest records, and a live run over the recorded result arrives at the same
    digest. Either alone would let the two drift — a fixture nobody reaches, or a
    run measured only against itself.
    """

    record, _, _ = effect_fixture()
    restored = restore_vm_adapter(
        (GOLDEN / "llm_effect_v1.vm_snapshot.json").read_bytes()
    )
    assert restored.program_hash() == record["program_hash"]
    assert restored.snapshot_digest() == record["expected_terminal_snapshot_digest"]
    assert restored.transition_hash() == record["expected_transition_ids"][-1]

    _, _, digests = effect_run(GOLDEN_EFFECT_RESULT)
    assert digests[-1] == restored.snapshot_digest()

def test_a_resumed_replay_reaches_the_same_terminal_state() -> None:
    """Resume accepts the state its predecessor left, and leaves it unchanged.

    The resumed run executes nothing — the snapshot it attaches to is already
    terminal — so it does not, and should not, reproduce the earlier run's
    transcript. What it must reproduce is the terminal state, and that is what
    is asserted. A resumed run whose transcript is empty against a contract
    describing a full run is correctly a failure, not an unearned identity.
    """

    unit, _ = pure_behavior()
    first = pure_prepared().run()
    assert first.status is R.ReplayStatus.REPLAY_IDENTICAL

    # No machine is built here. A continuation attaches to the terminal state its
    # predecessor recorded, resolved from the durable reference the observation
    # carries; an adapter constructed at the call site was left over from when a
    # caller brought the machine, and nothing has read it since.
    again = prepare_for(unit).resume(resumed_from=first)
    assert again.terminal_snapshot_digests == first.terminal_snapshot_digests
    assert again.steps_executed == 0
    assert again.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE, (
        "resume verification rejected a state it should have accepted"
    )

def test_resume_refuses_a_machine_in_another_state() -> None:
    """And now it is refused before the run is even prepared.

    The predecessor's terminal state is recorded, and a continuation's manifest
    declares the state it starts from, so the two are compared as records rather
    than discovered by asking a machine handed in at the call site. That is the
    stronger form of the same check: it does not depend on the caller having
    brought an honest machine.
    """

    unit, _ = pure_behavior()
    prepared = pure_prepared()
    first = prepared.run()

    # A manifest for a *fresh* run: its initial state is where a machine starts,
    # not where the predecessor stopped. Resuming against it is a continuation
    # that begins somewhere its predecessor never reached, and the two records
    # disagree before any machine is built. Handing the run an honest-looking
    # machine is no longer a way to express this, and that is the repair — the
    # comparison is between records now, so it does not depend on the caller
    # having brought an honest machine.
    continuation = prepare_for(unit)
    fresh_manifest = continuation.manifest_ref(prepared.bundle.replay_store)
    governed = continuation._governed()
    with pytest.raises(R.ReplayViolation) as excinfo:
        RC.resume_governed_replay(
            admission=continuation.admission,
            binding=governed["binding"],
            subjects=continuation.subjects,
            compiler=continuation.compiler,
            manifest_ref=fresh_manifest,
            resumed_from_result_ref=R.replay_result_ref(first),
            gas_budget=GAS,
            cognitive_budget=8,
            step_limit=1_000,
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH

def test_resume_refuses_a_continuation_across_a_knowledge_snapshot() -> None:
    """A continuation cannot reach across worlds, and it is stopped twice.

    First by the store, which is where a continuation now finds the result it
    continues: a run committed under another boundary was recorded in that
    world's journal, and asking this one for it gets a typed refusal rather than
    an answer. That is earlier and stronger than the lineage comparison, and it
    is the answer the production path gives.

    The lineage comparison is still asserted, at the record level, because a
    restored continuation is not built by that path and can name anything.
    """

    from synapse.experiments.gold.replay_store import ReplayStoreViolation

    prepared = pure_prepared()
    first = prepared.run()

    # A real behaviour, whose replay contract is the transcript its own program
    # produces. A contract written out of a scripted opcode list describes a
    # machine nobody runs, so its reference capture can never reproduce it and
    # the run is refused for that instead of for crossing a boundary — which is
    # a true refusal about the wrong thing.
    other_unit = real_behavior(literal=99)
    elsewhere = prepare_for(other_unit)
    with pytest.raises(ReplayStoreViolation) as store_error:
        # The refusal under test comes from the store, before any machine is
        # built: the continuation asks this world for a result that was committed
        # in another one.
        elsewhere.resume(resumed_from=first)
    assert store_error.value.failure_code.value == "RECORD_UNKNOWN"

    # And the record-level check: a continuation naming a result from another
    # committed boundary is refused for crossing it, before any machine is asked.
    crossing = prepare_for(
        other_unit, resumed_from_result_ref=R.replay_result_ref(first)
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        # Asked of the rule rather than of the body. The subject is which
        # predecessor a request may name, and that is settled between two
        # records, before a machine or an execution receipt is involved.
        R._require_resume_lineage(crossing.request(), resumed_from=first)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH

def test_resume_refuses_a_continuation_of_another_result() -> None:
    """Пара «запрос + результат» больше не выбирается на месте вызова."""

    unit, _ = pure_behavior()
    first = pure_prepared().run()
    second = pure_prepared(cognitive_budget=7).run()
    assert R.replay_result_ref(first) != R.replay_result_ref(second)

    # The public continuation path derives the lineage from the result it is
    # given, so the pair cannot be chosen at the call site any more. The check
    # is still asserted at the record level, because a restored continuation is
    # not built by that path and could name anything.
    continuation = prepare_for(
        unit, resumed_from_result_ref=R.replay_result_ref(first)
    )
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._require_resume_lineage(continuation.request(), resumed_from=second)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH

def test_resume_refuses_another_program() -> None:
    """§23: the program a continuation runs must be the one it resumes from.

    Two behaviors with genuinely different bytecode, published into one world
    and admitted together, so both requests name the same committed boundary and
    the lineage check — which is checked first, and correctly — has nothing to
    object to. The continuation then runs the same admitted set in the other
    execution order, so its program hashes are the resumed-from hashes reversed:
    the same programs, not in the places that result left them.
    """

    unit_a, _binding_a = pure_behavior()
    unit_b = real_behavior(literal=99)
    assert compile_behavior_unit(unit_a).actual_program_hash != (
        compile_behavior_unit(unit_b).actual_program_hash
    ), "the two behaviors must compile to different programs for this case to exist"

    primary, extra = world_of(unit_a, unit_b)
    forward = A.canonical_subject_refs(
        tuple(admitted_subject_in(item, primary, extra) for item in (unit_a, unit_b))
    )
    prepared = prepare_many((unit_a, unit_b), order=forward)
    # The public governed path for both attempts. What this case is about is
    # which programs a continuation attaches to, and that is settled between two
    # records before either machine is asked anything.
    first = prepared.run()
    assert len(first.observations) == 2, "both behaviors must have run to their end"

    continuation = prepare_many((unit_a, unit_b), order=tuple(reversed(forward)))
    assert tuple(item.subject_ref for item in continuation.subjects) != tuple(
        item.subject_ref for item in prepared.subjects
    )
    result = continuation.resume(resumed_from=first)
    assert result.status is R.ReplayStatus.REPLAY_INCOMPATIBLE
    assert result.failure_reason is R.ReplayFailureReason.PROGRAM_HASH_MISMATCH

def test_resume_uses_the_predecessors_exact_durable_activity_history() -> None:
    """A continuation inherits the history its predecessor consumed, not its own.

    The activity published for this attempt is deliberately *not* the one the
    predecessor used: the predecessor consumed none, so the continuation must
    consume none either. Its own preparation offering one changes nothing,
    because the history a continuation replays is resolved from the predecessor's
    durable request rather than assembled again from what happens to be at hand.
    """

    activity = recorded_llm_call()
    unit, _ = pure_behavior()
    first = pure_prepared().run()
    result = prepare_for(unit, activities=(activity,)).resume(resumed_from=first)
    assert result.recorded_activity_refs == first.recorded_activity_refs == ()
    assert result.status is not R.ReplayStatus.REPLAY_INCOMPATIBLE

def test_a_tampered_terminal_state_is_detected() -> None:
    """A clean transcript that ends somewhere else is a tamper, not an identity.

    Asked of the verdict rule rather than of a run, because a run can no longer
    be pointed at expectations of its caller's choosing: the expected digests
    come from a manifest, the manifest is issued from a reference run that
    observed them, and the store refuses a manifest that does not project its
    capture. The rule is still the thing that decides, so it is still the thing
    that is asked — with real observations from a real governed run, and with
    an expectation those observations do not meet.
    """

    result = pure_prepared().run()
    assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
    assert all(item.transcript_matched for item in result.observations)

    status, reason = R.replay_verdict(
        observations=result.observations,
        stopping_reason=None,
        expected_transcript_root=result.expected_transcript_root,
        expected_terminal_snapshot_digests=("f" * 64,) * len(result.observations),
    )
    assert status is R.ReplayStatus.REPLAY_FAILED
    assert reason is R.ReplayFailureReason.SNAPSHOT_TAMPERED

    # And the same observations against the digests they actually reached are an
    # identity, so what the case above measured is the mismatch and not the run.
    assert R.replay_verdict(
        observations=result.observations,
        stopping_reason=None,
        expected_transcript_root=result.expected_transcript_root,
        expected_terminal_snapshot_digests=result.terminal_snapshot_digests,
    ) == (R.ReplayStatus.REPLAY_IDENTICAL, None)

def test_a_snapshot_that_is_not_a_machine_snapshot_is_refused() -> None:
    with pytest.raises(R.ReplayViolation) as excinfo:
        restore_vm_adapter({"not": "a snapshot"})
    assert excinfo.value.failure_code is R.ReplayFailureCode.TYPE_MISMATCH
