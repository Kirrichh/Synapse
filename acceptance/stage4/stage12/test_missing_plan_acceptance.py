"""One heavy scenario: C1 succeeds but an accepted-plan member is missing."""

from acceptance.stage4.stage11._builders import create_composition, run_world
from acceptance.stage4.stage11._crash_prefix import (
    begin_attempt, dispatch_and_publish_worker, invoke_c1_without_completion_checkpoint,
    publish_c1_completed, publish_c1_started, publish_delivery_started,
)
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy


def test_oracle_success_does_not_replace_missing_plan_proof(tmp_path):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)])
    prefix = begin_attempt(world)
    publish_delivery_started(prefix)
    dispatch_and_publish_worker(prefix)
    publish_c1_started(prefix)
    invoke_c1_without_completion_checkpoint(prefix)
    publish_c1_completed(prefix)
    member = next((world.stage10_composition.record_store.record_root / "plan-decision").glob("*.stage10"))
    member.unlink()
    result = create_composition(world).execute()
    state = load_run_state(world.composition.record_store)
    attempt = state.attempts[0].result
    assert attempt.oracle_resolved is True
    assert attempt.structured_outcome["payload"]["status"] == "INVALID_CONTRACT"
    assert attempt.verified_finding_sha256 is None
    assert result.structured_outcome["payload"]["status"] == "INVALID_CONTRACT"
    assert world.worker_process.calls == world.oracle.calls == 1
