"""§26 acceptance: every attempt of a run is kept, and each gets its own context.

This is the anti-cherry-picking check. A run that reached a resolved attempt on
its second try must still hold the first one, with its own immutable context and
its own recorded outcome, and the run result must name the whole set rather than
the part that went well.

Heavy: each attempt crosses the §22 barrier for real and goes through the C1
boundary with a real git bridge, controlled change and oracle.
"""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner import AttemptOutcome, FallbackPolicy, RunFinalStatus
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner_composition import run_gold_run

from acceptance.stage4.stage11._builders import record_paths, run_world


def test_a_two_attempt_run_keeps_both_attempts_with_distinct_contexts(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        new_knowledge={2: True},
    )
    result = run_gold_run(world.composition)

    assert [item.attempt_index for item in result.attempts] == [1, 2]
    assert result.attempts[0].outcome is AttemptOutcome.UNRESOLVED
    assert result.attempts[1].outcome is AttemptOutcome.RESOLVED
    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert result.resolved_attempt_index == 2

    # Both attempts are durable, not just the one that resolved.
    assert len(record_paths(world.run_root, RecordKind.ATTEMPT_RESULT)) == 2
    assert len(record_paths(world.run_root, RecordKind.ATTEMPT_CONTEXT)) == 2


def test_each_attempt_carries_its_own_immutable_context(tmp_path: Path) -> None:
    """§26: a new context per attempt, never the previous one edited in place."""

    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False), (True, False)],
        new_knowledge={2: True},
    )
    run_gold_run(world.composition)

    digests = {
        path.name.split(".")[1]
        for path in record_paths(world.run_root, RecordKind.ATTEMPT_CONTEXT)
    }
    assert len(digests) == 2, "two attempts produced one context identity"


def test_the_run_result_reloads_from_records_after_the_process_ends(tmp_path: Path) -> None:
    """The durable records are the run: a fresh reader reaches the same result."""

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
    )
    produced = run_gold_run(world.composition)
    reloaded = world.controller.load_result(world.run_root)
    assert reloaded.result_sha256 == produced.result_sha256
    assert reloaded.final_status is produced.final_status
