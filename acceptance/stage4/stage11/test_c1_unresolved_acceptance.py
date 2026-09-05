"""C1 acceptance: an applied patch with a non-resolving oracle stays unresolved."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
)

from acceptance.stage4.stage11._builders import run_world


def test_non_resolving_oracle_cannot_be_promoted_to_success(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        run_id="oracle-unresolved",
    )
    result = world.execute()
    assert result.attempts[0].outcome is AttemptOutcome.UNRESOLVED
    assert result.attempts[0].c1_status == "GOLD_ORACLE_UNRESOLVED"
    assert result.final_status is RunFinalStatus.GOLD_STOPPED_LIMIT
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1
