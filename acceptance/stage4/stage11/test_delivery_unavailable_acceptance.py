"""§26 acceptance: unavailable attempt inputs remain distinct from refusal."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import AttemptOutcome, FallbackPolicy

from acceptance.stage4.stage11._builders import run_world


def test_unavailable_authority_records_no_worker_c1_or_oracle_refs(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        delivery_unavailable_attempts={1},
        run_id="authority-unavailable",
    )

    result = world.execute()
    state = load_run_state(world.composition.record_store)
    attempt = state.attempts[0]

    assert result.attempts[0].outcome is AttemptOutcome.DELIVERY_UNAVAILABLE
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
    assert attempt.context.phase_refs.worker_context_id is None
    assert attempt.context.phase_refs.worker_context_audit_sha256 is None
    assert attempt.result.worker_result_ref is None
    assert attempt.result.c1_result_ref is None
    assert attempt.result.oracle_result_ref is None
    assert attempt.result.publication_refs == ()
