"""Re-open one durable Stage 11 run with wholly new process-local bindings."""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.runner.records import RunRecordStore
from synapse.experiments.gold.runner.state_machine import restore_manifest
from synapse.experiments.gold.runner_composition import create_gold_run_composition
from synapse.experiments.gold.stage10_composition import create_stage10_production_composition

from acceptance.stage4.stage11._builders import (
    ProductionAttemptInputs,
    ScriptedOracle,
    c1_boundary,
)
from acceptance.stage4.stage11._worker_process import create_worker_process
from tests.gold_store_fence import fence_for
from tests.test_swebench_gold_runner import NEW_SOURCE


def fresh_runtime(
    world,
    tmp_path: Path,
    *,
    worker_outcomes: tuple[str, ...] = ("PATCH",),
    oracle_outcomes: list[tuple[bool, bool]] | None = None,
    environment_suffix: str = "restart",
):
    """Construct a new production composition over the run's existing durable roots."""

    run_fence = fence_for(world.run_root / "run-record-owner")
    run_store = RunRecordStore(world.run_root, mutation_fence=run_fence)
    manifest = restore_manifest(run_store)

    oracle = ScriptedOracle(
        [(False, True)] if oracle_outcomes is None else oracle_outcomes
    )
    boundary = c1_boundary(world.repo, world.run_root, oracle)

    worker = create_worker_process(
        tmp_path / f"{environment_suffix}-external-worker",
        outcomes=worker_outcomes,
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
        environment_suffix=environment_suffix,
    )
    composition = create_gold_run_composition(
        run_root=world.run_root,
        manifest=manifest,
        c1_boundary=boundary,
        run_record_fence=run_fence,
        attempt_inputs=inputs,
        stage10_composition=stage10,
    )
    return composition, oracle, worker, run_store


__all__ = ["fresh_runtime"]
