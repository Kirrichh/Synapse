"""Reported worker spend blocks another attempt and survives reconstruction."""

from pathlib import Path

from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus

from acceptance.stage4.stage11._builders import create_composition, run_world


def test_worker_token_limit_stops_before_next_preparation(tmp_path: Path, monkeypatch) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH",),
        run_id="worker-budget-limit",
    )
    program = world.worker_process.program_path
    program.write_text(program.read_text().replace('"total_tokens": 0', '"total_tokens": 100000'))
    approval_checks = []
    original_check = world.attempt_inputs.check_approval

    def check_once(**kwargs):
        approval_checks.append(kwargs["manifest"].manifest_sha256)
        assert len(approval_checks) == 1, "exhausted budget must not ask for another approval"
        original_check(**kwargs)

    monkeypatch.setattr(world.attempt_inputs, "check_approval", check_once)
    result = world.execute()
    assert result.final_status is RunFinalStatus.GOLD_STOPPED_LIMIT
    assert len(result.attempts) == 1
    state = load_run_state(world.composition.record_store)
    assert state.preparation_failure.detail_code == "worker_token_budget_exhausted"
    assert world.attempt_inputs.calls == [1]
    assert len(approval_checks) == 1
    assert world.worker_process.calls == world.oracle.calls == 1
    assert create_composition(world).execute() == result
    assert world.worker_process.calls == world.oracle.calls == 1
