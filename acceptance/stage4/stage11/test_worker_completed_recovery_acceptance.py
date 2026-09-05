"""Recovery acceptance for a durable worker result not yet delegated to C1."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
)

from acceptance.stage4.stage11._builders import create_composition, run_world
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt,
    dispatch_and_publish_worker,
    publish_delivery_started,
)


def test_durable_worker_result_resumes_c1_without_second_worker_process(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="worker-completed-recovery",
    )
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    before_worker = world.worker_process.calls

    result = create_composition(world).execute()

    assert result.attempts[0].outcome is AttemptOutcome.RESOLVED
    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert world.worker_process.calls == before_worker == 1
    assert world.oracle.calls == 1
