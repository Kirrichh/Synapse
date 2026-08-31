"""Recovery acceptance for uncertain C1 delegation without an authority row."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import AttemptOutcome, FallbackPolicy

from acceptance.stage4.stage11._builders import create_composition, run_world
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt,
    dispatch_and_publish_worker,
    publish_c1_started,
    publish_delivery_started,
)


def test_uncertain_c1_is_not_invoked_again_when_no_authority_row_exists(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="c1-started-recovery",
    )
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    publish_c1_started(prefix)
    before_worker = world.worker_process.calls

    result = create_composition(world).execute()

    assert result.attempts[0].outcome is AttemptOutcome.CONTROLLER_INTERRUPTED
    assert world.worker_process.calls == before_worker == 1
    assert world.oracle.calls == 0
