"""Recovery acceptance for a content-bound C1 completion checkpoint."""

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
    invoke_c1_without_completion_checkpoint,
    publish_c1_completed,
    publish_c1_started,
    publish_delivery_started,
)


def test_content_bound_c1_completion_finishes_without_external_reexecution(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="c1-completed-recovery",
    )
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    publish_c1_started(prefix)
    invoke_c1_without_completion_checkpoint(prefix)
    publish_c1_completed(prefix)
    before_worker = world.worker_process.calls
    before_oracle = world.oracle.calls

    result = create_composition(world).execute()

    assert result.attempts[0].outcome is AttemptOutcome.RESOLVED
    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert world.worker_process.calls == before_worker == 1
    assert world.oracle.calls == before_oracle == 1
