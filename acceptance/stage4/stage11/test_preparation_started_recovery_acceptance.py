"""An uncertain preparation must not repeat snapshot/retrieval/reference work."""

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy

from acceptance.stage4.stage11._builders import create_composition, run_world


def test_restart_preserves_uncertain_preparation_without_repeating_inputs(tmp_path: Path, monkeypatch) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="preparation-started-recovery",
    )
    original = world.attempt_inputs.prepare

    def interrupted_prepare(**kwargs):
        original(**kwargs)  # Real snapshot, retrieval and reference capture.
        raise SystemExit("process lost before context persistence")

    monkeypatch.setattr(world.attempt_inputs, "prepare", interrupted_prepare)
    with pytest.raises(SystemExit):
        world.composition.execute()

    def forbidden_repeat(**kwargs):
        pytest.fail("uncertain preparation was repeated")

    monkeypatch.setattr(world.attempt_inputs, "prepare", forbidden_repeat)
    resumed = create_composition(world)
    result = resumed.execute()
    assert result.attempts == ()
    state = load_run_state(resumed.record_store)
    assert state.preparation_failure.detail_code == "preparation_interrupted_outcome_unknown"
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
