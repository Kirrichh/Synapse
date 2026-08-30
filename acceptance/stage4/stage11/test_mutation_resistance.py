"""§26 mutation resistance: the defects this stage must not be able to hide.

Each check names one weakening of the production code and fails if it were made.
The four the implementation plan requires are here — only the successful attempt
kept, a context mutated between attempts, the oracle skipped after an applied
change, a fallback counted as Gold — together with the three invariants this
patch introduces: the §22 barrier before dispatch, an interrupted attempt id
never reused, and a continuation without new knowledge.

Heavy: most cases drive a real run through the real C1 boundary.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import tests.gold_point_of_use_world as pou
from synapse.experiments.gold.runner import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunViolation,
    RunFinalStatus,
    TerminalDecisionKind,
    classify_c1_attempt,
    decide_next_attempt,
    run_c1_attempt,
)
from synapse.experiments.gold.runner.delivery import deliver_attempt_context
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner_composition import run_gold_run

from synapse.experiments.gold.runner.models import GoldAttemptContext

from acceptance.stage4.stage11._builders import (
    c1_boundary,
    candidate_result,
    invocation_for,
    phase_refs,
    record_paths,
    run_world,
    scripted_transport,
    worker_context_source,
)


def test_mutant_only_the_successful_attempt_is_persisted_is_killed(tmp_path: Path) -> None:
    """Mutant: the run keeps the attempt that resolved and drops the rest."""

    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        new_knowledge={2: True},
    )
    result = run_gold_run(world.composition)

    outcomes = [item.outcome for item in result.attempts]
    assert AttemptOutcome.UNRESOLVED in outcomes, "the unresolved attempt vanished from the result"
    assert len(record_paths(world.run_root, RecordKind.ATTEMPT_RESULT)) == 2


def test_mutant_the_attempt_context_is_mutated_in_place_is_killed(tmp_path: Path) -> None:
    """Mutant: the second attempt edits the first attempt's context."""

    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        new_knowledge={2: True},
    )
    run_gold_run(world.composition)

    contexts = record_paths(world.run_root, RecordKind.ATTEMPT_CONTEXT)
    digests = {path.name.split(".")[1] for path in contexts}
    assert len(contexts) == 2 and len(digests) == 2

    # And a context cannot be edited in the first place: the record is frozen,
    # and a copy carrying an edited field no longer matches its own identity.
    context = GoldAttemptContext.create(
        manifest=world.manifest, attempt_index=1, phase_refs=phase_refs(1)
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.attempt_index = 2

    edited = dataclasses.replace(context, phase_refs=phase_refs(2))
    with pytest.raises(GoldRunViolation) as caught:
        edited.validate_identity()
    assert caught.value.failure_code.value == "IDENTITY_MISMATCH"


def test_mutant_the_oracle_is_skipped_after_applied_is_killed(tmp_path: Path) -> None:
    """Mutant: an applied change is called resolved without an oracle verdict.

    The C1 result here is genuine; only the oracle evidence is taken away, which
    is exactly what a boundary that skipped the oracle would return.
    """

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
    )
    genuine = run_c1_attempt(
        c1_boundary(world.repo, world.run_root, world.oracle),
        gold_run_id=world.manifest.gold_run_id,
        attempt_id="1",
        worker_result=candidate_result(world.patch_text),
        run_root=world.run_root,
    )
    assert classify_c1_attempt(genuine).outcome is AttemptOutcome.RESOLVED

    without_oracle = dataclasses.replace(
        genuine,
        oracle_result=None,
        payload={**dict(genuine.payload), "oracle_invoked": False, "oracle_resolved": None},
    )
    assert classify_c1_attempt(without_oracle).outcome is AttemptOutcome.C1_RESULT_INVALID

    unresolved_oracle = dataclasses.replace(
        genuine,
        payload={**dict(genuine.payload), "oracle_resolved": False},
    )
    assert classify_c1_attempt(unresolved_oracle).outcome is AttemptOutcome.C1_RESULT_INVALID


def test_mutant_a_fallback_is_counted_as_gold_is_killed(tmp_path: Path) -> None:
    """Mutant: an explicit Baseline arm is reported as a Gold execution."""

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.EXPLICIT_BASELINE_ARM,
        oracle_outcomes=[(True, True)],
    )
    result = run_gold_run(world.composition)

    assert result.final_status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT
    assert not result.final_status.value.startswith("GOLD")
    assert result.terminal_decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT
    assert result.fallback_arm_id and result.fallback_arm_id != world.manifest.gold_run_id
    assert result.resolved_attempt_index is None


def test_mutant_the_consumption_barrier_is_skipped_before_dispatch_is_killed() -> None:
    """Mutant: delivery proceeds on something other than a fresh admission."""

    with pytest.raises(Exception):
        deliver_attempt_context(
            admission_request=object(),
            context_source=worker_context_source,
            invocation_source=invocation_for,
            transport=scripted_transport,
        )

    stale = pou.admit(pou.admission_request())
    stale_context = worker_context_source(stale)
    with pytest.raises(GoldRunViolation) as caught:
        deliver_attempt_context(
            admission_request=pou.admission_request(),
            context_source=lambda _fresh: stale_context,
            invocation_source=invocation_for,
            transport=scripted_transport,
        )
    assert caught.value.failure_code.value == "CONSUMPTION_REFUSED"


def test_mutant_an_interrupted_attempt_id_is_reused_is_killed(tmp_path: Path) -> None:
    """Mutant: the attempt that crashed runs again under the same identity."""

    class Crash(RuntimeError):
        pass

    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        worker_outcomes=[Crash("worker died")],
        new_knowledge={2: True},
    )
    with pytest.raises(Crash):
        run_gold_run(world.composition)

    world.worker.outcomes = [candidate_result(world.patch_text)]
    world.worker.calls = 0
    result = run_gold_run(world.composition)

    assert result.attempts[0].outcome is AttemptOutcome.CONTROLLER_INTERRUPTED
    indexes = [item.attempt_index for item in result.attempts]
    assert indexes == sorted(set(indexes)) == [1, 2]
    assert len(record_paths(world.run_root, RecordKind.ATTEMPT_RESULT)) == 2


def test_mutant_continuation_without_new_knowledge_is_allowed_is_killed(tmp_path: Path) -> None:
    """Mutant: the next attempt runs against knowledge that did not change."""

    draft = decide_next_attempt(
        outcome=AttemptOutcome.UNRESOLVED,
        attempts_used=1,
        max_attempts=4,
        new_knowledge_available=False,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        fallback_arm_id="arm",
    )
    assert draft.decision is TerminalDecisionKind.STOP_NO_NEW_KNOWLEDGE

    world = run_world(
        tmp_path,
        max_attempts=3,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        new_knowledge={},
    )
    result = run_gold_run(world.composition)

    assert len(result.attempts) == 1, "the run continued without newly admitted knowledge"
    assert result.final_status is RunFinalStatus.GOLD_STOPPED_NO_NEW_KNOWLEDGE
