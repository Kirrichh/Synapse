"""Single production composition for one multi-attempt Gold run.

This root binds one run-record coordinator, one sealed Stage 10 composition,
one exact C1 boundary, and one coherent attempt-input port to the controller.
It does not accept phase callbacks, worker-result sources, transports, or a
second knowledge predicate.
"""

from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.stage10_composition import (
    Stage10ProductionComposition,
    require_stage10_production_composition,
)

from .runner.attempt_inputs import AttemptInputsPort, require_attempt_inputs_port
from .runner.c1_boundary import C1AttemptBoundary
from .runner.controller import GoldRunController
from .runner.controller_recovery import (
    AttemptPhaseMaterializer,
    require_attempt_phase_materializer,
)
from .runner.models import GoldRunConfig, GoldRunManifest
from .runner.records import RunRecordStore
from .runner.run_recovery import RunRecordRecovery
from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


_RUN_COMPOSITION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldRunProductionComposition:
    """Immutable identity snapshot for the only controller construction path."""

    __slots__ = (
        "_manifest",
        "_controller",
        "_run_root",
        "_record_store",
        "_record_recovery",
        "_run_record_fence",
        "_stage10_composition",
        "_c1_boundary",
        "_attempt_inputs",
        "_attempt_materializer",
        "_identity_snapshot",
        "_trusted_seal",
    )

    def __new__(cls, *args: object, **kwargs: object) -> GoldRunProductionComposition:
        raise TypeError("GoldRunProductionComposition is factory-created")

    @property
    def manifest(self) -> GoldRunManifest:
        return self._manifest

    @property
    def controller(self) -> GoldRunController:
        return self._controller

    @property
    def run_root(self) -> Path:
        return self._run_root

    @property
    def record_store(self) -> RunRecordStore:
        return self._record_store

    @property
    def record_recovery(self) -> RunRecordRecovery:
        return self._record_recovery

    @property
    def stage10_composition(self) -> Stage10ProductionComposition:
        return self._stage10_composition

    def execute(self):
        """Drive or resume the bound run through its controller."""

        return require_gold_run_composition(self).controller.execute()

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")


def _validate_cross_owner_bindings(
    *,
    run_root: Path,
    manifest: GoldRunManifest,
    c1_boundary: C1AttemptBoundary,
    run_record_fence: FileSnapshotFence,
    stage10_composition: Stage10ProductionComposition,
) -> None:
    config = manifest.config
    if (
        config.provider != "mini"
        or c1_boundary.environment_kind != config.environment_kind
        or c1_boundary.command_policy.task_id != config.task_id
        or c1_boundary.command_policy.instance_id != config.instance_id
        or c1_boundary.oracle_identity != config.oracle_name
        or c1_boundary.writer.run_root != run_root
        or c1_boundary.writer.repo_root != c1_boundary.repo_root
    ):
        raise _fail(
            GoldRunFailureCode.C1_BOUNDARY_MISMATCH,
            "C1 boundary or closed worker provider differs from frozen config",
        )
    mini_config = stage10_composition.worker_transport.config
    if mini_config.model is None or mini_config.model != config.model:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "Stage 10 worker model differs from the frozen run configuration",
        )
    stage10_fence = stage10_composition.record_store.mutation_fence
    if stage10_fence.coordinator_id() == run_record_fence.coordinator_id():
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run records and Stage 10 records require independent coordinators",
        )


def create_gold_run_composition(
    *,
    run_root: Path,
    manifest: GoldRunManifest,
    c1_boundary: C1AttemptBoundary,
    run_record_fence: FileSnapshotFence,
    attempt_inputs: AttemptInputsPort,
    stage10_composition: Stage10ProductionComposition,
) -> GoldRunProductionComposition:
    """Construct the sole exact production controller graph.

    Validation and construction remain one linear factory so no partially
    assembled graph can escape between owner bindings or bypass the final
    sealed identity check.
    """

    if type(run_root) is not type(Path()):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be exact")
    if type(manifest) is not GoldRunManifest:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
    manifest.validate_identity()
    if type(manifest.config) is not GoldRunConfig:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest config must be exact")
    if type(c1_boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "C1 boundary must be exact")
    if type(run_record_fence) is not FileSnapshotFence:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "run record fence must be the exact file coordinator",
        )
    inputs = require_attempt_inputs_port(attempt_inputs)
    stage10 = require_stage10_production_composition(stage10_composition)
    _validate_cross_owner_bindings(
        run_root=run_root,
        manifest=manifest,
        c1_boundary=c1_boundary,
        run_record_fence=run_record_fence,
        stage10_composition=stage10,
    )

    record_store = RunRecordStore(
        run_root,
        mutation_fence=run_record_fence,
    )
    record_recovery = RunRecordRecovery(
        store=record_store,
        fence=run_record_fence,
    )
    attempt_materializer = AttemptPhaseMaterializer(
        manifest=manifest,
        boundary=c1_boundary,
        stage10_record_store=stage10.record_store,
        worker_adapter=stage10.worker_adapter,
        run_root=run_root,
    )
    controller = GoldRunController(
        manifest=manifest,
        record_recovery=record_recovery,
        attempt_inputs=inputs,
        attempt_materializer=attempt_materializer,
        run_root=run_root,
    )
    result = object.__new__(GoldRunProductionComposition)
    fields = {
        "_manifest": manifest,
        "_controller": controller,
        "_run_root": run_root,
        "_record_store": record_store,
        "_record_recovery": record_recovery,
        "_run_record_fence": run_record_fence,
        "_stage10_composition": stage10,
        "_c1_boundary": c1_boundary,
        "_attempt_inputs": inputs,
        "_attempt_materializer": attempt_materializer,
        "_identity_snapshot": (
            manifest,
            controller,
            run_root,
            record_store,
            record_recovery,
            run_record_fence,
            stage10,
            c1_boundary,
            inputs,
            attempt_materializer,
        ),
        "_trusted_seal": _RUN_COMPOSITION_SEAL,
    }
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    return require_gold_run_composition(result)


def require_gold_run_composition(value: object) -> GoldRunProductionComposition:
    """Refuse a forged composition or any changed concrete binding."""

    if (
        type(value) is not GoldRunProductionComposition
        or getattr(value, "_trusted_seal", None) is not _RUN_COMPOSITION_SEAL
    ):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "an exact sealed run composition is required",
        )
    snapshot = getattr(value, "_identity_snapshot", None)
    current = (
        value._manifest,
        value._controller,
        value._run_root,
        value._record_store,
        value._record_recovery,
        value._run_record_fence,
        value._stage10_composition,
        value._c1_boundary,
        value._attempt_inputs,
        value._attempt_materializer,
    )
    if type(snapshot) is not tuple or len(snapshot) != len(current) or any(
        original is not bound for original, bound in zip(snapshot, current)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run composition identity changed",
        )
    value.manifest.validate_identity()
    require_stage10_production_composition(value.stage10_composition)
    require_attempt_phase_materializer(
        value._attempt_materializer,
        manifest=value.manifest,
        run_root=value.run_root,
    )
    if (
        value.record_recovery.store is not value.record_store
        or value.record_recovery.fence is not value._run_record_fence
        or value.record_store.record_root != value.run_root / "run-records"
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "run store, recovery, fence, and root bindings differ",
        )
    _validate_cross_owner_bindings(
        run_root=value.run_root,
        manifest=value.manifest,
        c1_boundary=value._c1_boundary,
        run_record_fence=value._run_record_fence,
        stage10_composition=value.stage10_composition,
    )
    return value


__all__ = [
    "GoldRunProductionComposition",
    "create_gold_run_composition",
    "require_gold_run_composition",
]
