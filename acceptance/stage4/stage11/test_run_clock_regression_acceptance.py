"""One heavy shard: a regressed clock stops without corrupting recovery."""

import time
from types import SimpleNamespace

from synapse.experiments.gold.runner import controller
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus
from acceptance.stage4.stage11._builders import run_world


def test_regressed_clock_records_unavailability_without_another_attempt(tmp_path, monkeypatch):
    world = run_world(
        tmp_path, max_attempts=2, fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)], worker_outcomes=("PATCH",),
        run_id="regressed-run-clock",
    )
    origin = time.time_ns()
    readings = iter((origin, origin - 1_000_000))
    monkeypatch.setattr(controller, "time", SimpleNamespace(time_ns=lambda: next(readings)))
    result = world.execute()
    state = load_run_state(world.composition.record_store)
    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert state.preparation_failure.detail_code == "run_clock_unavailable"
    assert world.worker_process.calls == world.oracle.calls == 1
    assert world.execute() == result
    assert world.worker_process.calls == world.oracle.calls == 1
