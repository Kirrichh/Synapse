"""Continuation acceptance for unavailable next-attempt knowledge authority."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
)

from acceptance.stage4.stage11._builders import run_world


def test_unavailable_next_knowledge_stops_without_starting_another_attempt(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        worker_outcomes=("NO_PATCH", "PATCH"),
        unavailable_attempts={2},
        run_id="next-knowledge-unavailable",
    )

    result = world.execute()

    assert [item.outcome for item in result.attempts] == [AttemptOutcome.NO_CANDIDATE]
    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 0
