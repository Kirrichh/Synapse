"""Recovery acceptance for a crash after the pre-dispatch durable boundary."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.vocabulary import AttemptOutcome, FallbackPolicy

from acceptance.stage4.stage11._builders import create_composition, run_world
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt,
    publish_delivery_started,
)


def test_uncertain_delivery_is_not_dispatched_again_after_restart(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="delivery-started-recovery",
    )
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)

    result = create_composition(world).execute()

    assert result.attempts[0].outcome is AttemptOutcome.CONTROLLER_INTERRUPTED
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
