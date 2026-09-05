"""C1 acceptance: explicit infrastructure fallback keeps Baseline identity."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    RunFinalStatus,
    TerminalDecisionKind,
)

from acceptance.stage4.stage11._builders import run_world


def test_explicit_fallback_never_becomes_a_gold_result(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.EXPLICIT_BASELINE_ARM,
        oracle_outcomes=[(True, True)],
        run_id="oracle-fallback",
    )
    result = world.execute()
    assert result.final_status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT
    assert result.terminal_decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT
    assert result.fallback_arm_id is not None
    assert result.fallback_arm_id != world.manifest.gold_run_id
    assert result.resolved_attempt_index is None
