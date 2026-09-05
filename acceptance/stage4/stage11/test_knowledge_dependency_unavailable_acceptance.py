"""Continuation acceptance for unavailable next-attempt knowledge authority."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.attempt_inputs import PreparedAttemptInputs
from synapse.experiments.gold.runner.models import AttemptPreparationFailure
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.run_recovery import PendingRunRecord
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.stop_policy import decide_dependency_unavailable
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    RunFinalStatus,
    TerminalDecisionKind,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._fresh_runtime import fresh_runtime


def test_unavailable_next_knowledge_stops_without_starting_another_attempt(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH", "PATCH"),
        unavailable_attempts={2},
        run_id="next-knowledge-unavailable",
    )

    result = world.execute()
    state = load_run_state(world.composition.record_store)

    assert [item.outcome for item in result.attempts] == [AttemptOutcome.UNRESOLVED]
    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert result.terminal_decision is TerminalDecisionKind.STOP_UNRECOVERABLE
    assert state.preparation_failure is not None
    assert state.preparation_failure.target_attempt_index == 2
    assert state.preparation_failure.source_attempt_index == 1
    assert state.decision_for(1).decision is TerminalDecisionKind.CONTINUE
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1


def test_unavailable_initial_knowledge_finishes_without_fabricating_an_attempt(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[],
        unavailable_attempts={1},
        run_id="initial-knowledge-unavailable",
    )

    result = world.execute()
    state = load_run_state(world.composition.record_store)

    assert result.attempts == ()
    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert result.terminal_decision is TerminalDecisionKind.STOP_UNRECOVERABLE
    assert state.preparation_failure is not None
    assert state.preparation_failure.target_attempt_index == 1
    assert state.preparation_failure.source_attempt_index is None
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0


def test_unavailable_next_knowledge_uses_only_the_explicit_baseline_arm(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.EXPLICIT_BASELINE_ARM,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH",),
        unavailable_attempts={2},
        run_id="next-knowledge-explicit-baseline",
    )

    result = world.execute()

    assert [item.outcome for item in result.attempts] == [AttemptOutcome.UNRESOLVED]
    assert result.final_status is RunFinalStatus.BASELINE_FALLBACK_EXPLICIT
    assert result.terminal_decision is TerminalDecisionKind.FALLBACK_BASELINE_EXPLICIT
    assert result.fallback_arm_id is not None
    assert result.fallback_arm_id.startswith("baseline-explicit-")
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1


def test_restart_finalizes_a_durable_preparation_failure_without_retrying_inputs(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH",),
        run_id="preparation-failure-restart",
    )
    controller = world.controller

    with world.composition.record_recovery.session() as session:
        session.put(PendingRunRecord(
            kind=RecordKind.MANIFEST,
            key="manifest",
            payload=world.manifest.stored_dict(),
        ))
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
        decision = controller._record_tail_decision(session, completed)
        evidence = load_run_state(session.store).continuation_for(1)
        assert evidence is not None
        draft = decide_dependency_unavailable(
            fallback_policy=world.manifest.config.fallback_policy,
            fallback_arm_id=controller._fallback_arm_id(),
        )
        failure = AttemptPreparationFailure.create(
            run_id=world.manifest.run_id,
            gold_run_id=world.manifest.gold_run_id,
            manifest_sha256=world.manifest.manifest_sha256,
            target_attempt_index=2,
            source_attempt_index=1,
            source_attempt_result_sha256=completed.attempts[0].result.result_sha256,
            source_decision_sha256=decision.decision_sha256,
            continuation_evidence_sha256=evidence.digest(),
            terminal_decision=draft.decision,
            reason=draft.reason,
            detail_code="stage11_authority_unavailable",
            fallback_arm_id=draft.fallback_arm_id,
        )
        session.put(PendingRunRecord(
            kind=RecordKind.PREPARATION_FAILURE,
            key="final",
            payload=failure.stored_dict(),
        ))

    restarted, restart_oracle, restart_worker, restart_store = fresh_runtime(
        world,
        tmp_path,
        worker_outcomes=("PATCH",),
        oracle_outcomes=[(True, False)],
    )
    result = restarted.execute()
    recovered = load_run_state(restart_store)

    assert result.final_status is RunFinalStatus.GOLD_UNAVAILABLE
    assert len(result.attempts) == 1
    assert recovered.preparation_failure.failure_sha256 == failure.failure_sha256
    assert restarted._attempt_inputs.calls == []
    assert restart_worker.calls == 0
    assert restart_oracle.calls == 0
