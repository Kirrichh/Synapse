"""§26 acceptance: a crashed run resumes from a verified phase boundary.

The controller keeps nothing in memory between processes, so recovery is a
property of the records. Two rules are checked here. An attempt whose context
was persisted but whose result never was is recorded as interrupted and is never
re-executed under the same identity — re-running it would be the hidden retry
§26 forbids, and would let a crash loop buy attempts the budget never granted.
And a terminal result, once written, is what a later process reads: the run does
not start again around it.

Heavy: real attempts, real barrier crossings, real C1 boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner import AttemptOutcome, FallbackPolicy, RunFinalStatus
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner_composition import run_gold_run

from acceptance.stage4.stage11._builders import candidate_result, record_paths, run_world


class WorkerCrash(RuntimeError):
    """Stands in for a process that died between C1 delegation and its record."""


def test_an_attempt_that_lost_its_result_is_interrupted_not_repeated(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        worker_outcomes=[WorkerCrash("worker process died")],
        new_knowledge={2: True},
    )
    with pytest.raises(WorkerCrash):
        run_gold_run(world.composition)

    # The context of the crashed attempt survived; its result did not.
    assert len(record_paths(world.run_root, RecordKind.ATTEMPT_CONTEXT)) == 1
    assert record_paths(world.run_root, RecordKind.ATTEMPT_RESULT) == []

    world.worker.outcomes = [candidate_result(world.patch_text)]
    world.worker.calls = 0
    result = run_gold_run(world.composition)

    assert result.attempts[0].outcome is AttemptOutcome.CONTROLLER_INTERRUPTED
    assert result.attempts[0].attempt_index == 1
    assert [item.attempt_index for item in result.attempts] == [1, 2]
    assert result.attempts[1].outcome is AttemptOutcome.RESOLVED
    assert result.final_status is RunFinalStatus.GOLD_RESOLVED


def test_an_interrupted_attempt_still_costs_its_budget(tmp_path: Path) -> None:
    """A crash does not buy an extra attempt: every started attempt counts."""

    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        worker_outcomes=[WorkerCrash("worker process died")],
    )
    with pytest.raises(WorkerCrash):
        run_gold_run(world.composition)

    result = run_gold_run(world.composition)
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome is AttemptOutcome.CONTROLLER_INTERRUPTED
    assert result.final_status is not RunFinalStatus.GOLD_RESOLVED


def test_a_terminal_result_is_not_recomputed_by_a_later_process(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
    )
    first = run_gold_run(world.composition)
    oracle_calls = world.oracle.calls
    second = run_gold_run(world.composition)

    assert second.result_sha256 == first.result_sha256
    assert world.oracle.calls == oracle_calls, "a settled run re-invoked the oracle"
