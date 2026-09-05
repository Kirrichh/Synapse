"""Recovery acceptance: a terminal decision is never recomputed."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._fresh_runtime import fresh_runtime


def test_fresh_runtime_recovers_terminal_decision_without_external_calls(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="terminal-decision-recovery",
    )
    first = world.execute()

    restarted, restart_oracle, restart_worker, restart_store = fresh_runtime(
        world, tmp_path
    )
    second = restarted.execute()
    state = load_run_state(restart_store)

    assert second.result_sha256 == first.result_sha256
    assert second.final_status is RunFinalStatus.GOLD_RESOLVED
    assert second.terminal_decision_sha256 == state.final_result.terminal_decision_sha256
    assert restart_oracle.calls == 0
    assert restart_worker.calls == 0
