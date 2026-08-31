"""C1 acceptance: oracle infrastructure failure remains Gold unavailable."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
)

from acceptance.stage4.stage11._builders import run_world


def test_oracle_infrastructure_failure_is_not_a_negative_or_success(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, True)],
        run_id="oracle-infrastructure",
    )
    result = world.execute()
    assert result.attempts[0].outcome is AttemptOutcome.INFRA_ERROR
    assert result.attempts[0].c1_status == "GOLD_INFRA_ERROR"
    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1
