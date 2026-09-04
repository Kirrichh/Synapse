"""Recovery acceptance: durable CONTINUE precedes every next-attempt side effect."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.attempt_inputs import PreparedAttemptInputs
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.run_recovery import PendingRunRecord
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
    TerminalDecisionKind,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._fresh_runtime import fresh_runtime


def test_fresh_runtime_resumes_durable_continue_before_materializing_attempt_two(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH",),
        run_id="continue-decision-restart",
    )

    controller = world.controller
    with world.composition.record_recovery.session() as session:
        session.put(
            PendingRunRecord(
                kind=RecordKind.MANIFEST,
                key="manifest",
                payload=world.manifest.stored_dict(),
            )
        )
        prepared = controller._prepare_attempt(
            session=session,
            attempt_index=1,
            previous_context=None,
        )
        assert type(prepared) is PreparedAttemptInputs
        controller._attempt_materializer.execute_prepared_attempt(
            session=session,
            attempt_index=1,
            prepared_inputs=prepared,
        )
        completed = load_run_state(session.store)
        assert completed.attempts[0].result is not None
        assert completed.attempts[0].result.outcome is AttemptOutcome.UNRESOLVED

        decision = controller._record_tail_decision(session, completed)
        assert decision.decision is TerminalDecisionKind.CONTINUE

    before_restart = load_run_state(world.composition.record_store)
    first_result_sha256 = before_restart.attempts[0].result.result_sha256
    first_decision_sha256 = before_restart.decision_for(1).decision_sha256
    assert before_restart.continuation_for(1) is not None
    assert len(before_restart.attempts) == 1
    assert world.composition.record_store.get(
        kind=RecordKind.ATTEMPT_CONTEXT,
        key="2",
    ) is None
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1

    restarted, restart_oracle, restart_worker, restart_store = fresh_runtime(
        world,
        tmp_path,
        worker_outcomes=("PATCH",),
        oracle_outcomes=[(True, False)],
        environment_suffix="primary",
    )
    result = restarted.execute()
    recovered = load_run_state(restart_store)

    assert result.final_status is RunFinalStatus.GOLD_RESOLVED
    assert len(recovered.attempts) == 2
    assert recovered.attempts[0].result.result_sha256 == first_result_sha256
    assert recovered.decision_for(1).decision_sha256 == first_decision_sha256
    assert recovered.attempts[1].result.outcome is AttemptOutcome.RESOLVED
    assert restarted._attempt_inputs.calls == [2]
    assert restart_worker.calls == 1
    assert restart_oracle.calls == 1
