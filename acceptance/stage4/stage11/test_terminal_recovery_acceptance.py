"""Recovery acceptance: a terminal decision is never recomputed."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.attempt_knowledge_store import (
    RunRecordAttemptKnowledgeBasisStore,
)
from synapse.experiments.gold.runner.records import RunRecordStore
from synapse.experiments.gold.runner.state_machine import load_run_state, restore_manifest
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, RunFinalStatus
from synapse.experiments.gold.runner_composition import create_gold_run_composition
from synapse.experiments.gold.stage10_composition import create_stage10_production_composition

from acceptance.stage4.stage11._builders import (
    ProductionAttemptInputs,
    ScriptedOracle,
    c1_boundary,
    run_world,
)
from acceptance.stage4.stage11._worker_process import create_worker_process
from tests.gold_store_fence import fence_for
from tests.test_swebench_gold_runner import NEW_SOURCE


def _fresh_runtime(world, tmp_path: Path):
    """Re-open every process-local binding over the already durable run roots."""

    run_fence = fence_for(world.run_root / "run-record-owner")
    run_store = RunRecordStore(world.run_root, mutation_fence=run_fence)
    manifest = restore_manifest(run_store)

    oracle = ScriptedOracle([(False, True)])
    boundary = c1_boundary(world.repo, world.run_root, oracle)

    worker = create_worker_process(
        tmp_path / "restart-external-worker",
        outcomes=("PATCH",),
        patch_source=NEW_SOURCE,
    )
    stage10_root = world.run_root / "stage10-owner"
    stage10_fence = fence_for(stage10_root)
    stage10 = create_stage10_production_composition(
        record_root=stage10_root / "records",
        mutation_fence=stage10_fence,
        mini_config=worker.config(model=manifest.config.model),
    )

    inputs = ProductionAttemptInputs(
        run_root=world.run_root,
        source_repo=world.repo,
        environment_suffix="restart",
    )
    inputs.knowledge_basis = RunRecordAttemptKnowledgeBasisStore(run_store)
    composition = create_gold_run_composition(
        run_root=world.run_root,
        manifest=manifest,
        c1_boundary=boundary,
        run_record_fence=run_fence,
        attempt_inputs=inputs,
        stage10_composition=stage10,
    )
    return composition, oracle, worker, run_store


def test_fresh_runtime_recovers_terminal_decision_without_external_calls(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="terminal-decision-recovery",
    )
    first = world.execute()

    restarted, restart_oracle, restart_worker, restart_store = _fresh_runtime(
        world, tmp_path
    )
    second = restarted.execute()
    state = load_run_state(restart_store)

    assert second.result_sha256 == first.result_sha256
    assert second.final_status is RunFinalStatus.GOLD_RESOLVED
    assert second.terminal_decision_sha256 == state.final_result.terminal_decision_sha256
    assert restart_oracle.calls == 0
    assert restart_worker.calls == 0
