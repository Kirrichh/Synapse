"""§26 acceptance: all attempts survive with their actual cross-stage identities."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.replay import replay_result_ref
from synapse.experiments.gold.retrieval import retrieval_causal_record_ref
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    MechanismActivationStatus,
    RunFinalStatus,
    TelemetryCompleteness,
)

from acceptance.stage4.stage11._builders import run_world


def test_two_attempts_keep_real_retrieval_replay_context_and_result_authority(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        worker_outcomes=("NO_PATCH", "PATCH"),
        run_id="two-attempt-authority",
    )

    result = world.execute()
    state = load_run_state(world.composition.record_store)

    assert [item.outcome for item in result.attempts] == [
        AttemptOutcome.NO_CANDIDATE,
        AttemptOutcome.RESOLVED,
    ]
    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert result.resolved_attempt_index == 2
    assert world.worker_process.calls == 2
    assert world.oracle.calls == 1
    assert len(state.attempts) == 2

    for index, attempt in enumerate(state.attempts, start=1):
        prepared = world.attempt_inputs.prepared[index]
        refs = attempt.context.phase_refs
        assert refs.retrieval_ref == retrieval_causal_record_ref(
            prepared.retrieval_causal_record
        )
        assert refs.replay_ref == replay_result_ref(prepared.replay_result)
        assert refs.knowledge_snapshot_ref == prepared.accepted_plan.candidate.knowledge_snapshot_ref
        assert refs.worker_context_id is not None
        assert refs.worker_context_audit_sha256 is not None
        assert attempt.result.worker_result_ref is not None
        assert attempt.result.c1_result_ref is not None

    first_decision = state.decision_for(1)
    assert first_decision.next_retrieval_causal_ref == state.attempts[1].context.phase_refs.retrieval_ref
    assert result.telemetry_completeness is TelemetryCompleteness.UNAVAILABLE
    assert result.telemetry_refs == ()
    assert result.mechanism_activation is MechanismActivationStatus.NOT_EVALUATED
    assert result.mechanism_activation_refs == ()
