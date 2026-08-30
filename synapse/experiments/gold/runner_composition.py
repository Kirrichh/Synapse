"""The production composition root for one multi-attempt Gold run.

Every side of a run meets here and nowhere else. ``runner/controller.py`` owns
the sequence and may not choose its own C1 boundary; ``runner/c1_boundary.py``
owns the edge to the unchanged single-attempt adapter and decides nothing about
the run; ``runner/delivery.py`` crosses the §22 barrier and holds no transport;
``runner/records.py`` holds bytes and knows no rules. None of them can assemble
a run, which is deliberate — a module able to assemble one would be able to point
it at whatever it liked.

This module does the assembling and is the only module allowed to. NR-06 is the
reason it exists at all: without it the sole path from inputs to a running
controller would be through a test, and a production contract whose only
assembler is an acceptance fixture is not a production contract. It is not,
however, an entrypoint: NR-01/NR-02 keep the canonical program start at
``python -m synapse`` → ``synapse.cli.main()``, and nothing here runs on import.

Every rule it applies belongs to an owner and is called by name. It decides only
the order in which the questions are asked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .persistence import StoreMutationFencePort, require_store_mutation_fence
from .runner.c1_boundary import C1AttemptBoundary
from .runner.controller import GoldRunController
from .runner.delivery import AttemptDeliveryPlan, WorkerDelivery, deliver_attempt_context
from .runner.models import AttemptPhaseRefs, GoldRunConfig, GoldRunManifest
from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation

_RUN_COMPOSITION_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class GoldRunProductionComposition:
    """Immutable identity binding for the exact production run wiring."""

    __slots__ = ("_manifest", "_controller", "_run_root", "_trusted_seal")

    def __new__(cls, *args: object, **kwargs: object) -> "GoldRunProductionComposition":
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

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("GoldRunProductionComposition is immutable")


def create_gold_run_composition(
    *,
    run_root: Path,
    manifest: GoldRunManifest,
    c1_boundary: C1AttemptBoundary,
    mutation_fence: StoreMutationFencePort,
    phase_refs_source: Callable[[int], AttemptPhaseRefs],
    delivery_plan_source: Callable[[object], AttemptDeliveryPlan],
    worker_result_source: Callable[[object], object],
    new_knowledge_available: Callable[[int], bool],
) -> GoldRunProductionComposition:
    """Bind one run's owners and adapters into a controller ready to run.

    The manifest identity is revalidated here rather than trusted: a composition
    built over a manifest whose payload was edited after minting is refused
    where it is built, not deep inside an attempt.
    """

    if not isinstance(run_root, Path):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run root must be a Path")
    if type(manifest) is not GoldRunManifest:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
    manifest.validate_identity()
    if type(manifest.config) is not GoldRunConfig:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest config must be exact")
    if type(c1_boundary) is not C1AttemptBoundary:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "c1 boundary must be exact")
    require_store_mutation_fence(mutation_fence)

    if not callable(delivery_plan_source):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery plan source must be callable")

    def delivery_port(context: object) -> WorkerDelivery:
        """Bind the run's delivery owner: the §22 crossing happens in there."""

        plan = delivery_plan_source(context)
        if type(plan) is not AttemptDeliveryPlan:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "delivery plan source returned an invalid plan")
        return deliver_attempt_context(
            admission_request=plan.admission_request,
            context_source=plan.context_source,
            invocation_source=plan.invocation_source,
            transport=plan.transport,
        )

    controller = GoldRunController(
        manifest=manifest,
        boundary=c1_boundary,
        fence=mutation_fence,
        phase_refs_source=phase_refs_source,
        delivery_port=delivery_port,
        worker_result_source=worker_result_source,
        new_knowledge_available=new_knowledge_available,
    )
    result = object.__new__(GoldRunProductionComposition)
    object.__setattr__(result, "_manifest", manifest)
    object.__setattr__(result, "_controller", controller)
    object.__setattr__(result, "_run_root", run_root)
    object.__setattr__(result, "_trusted_seal", _RUN_COMPOSITION_SEAL)
    return result


def require_gold_run_composition(value: object) -> GoldRunProductionComposition:
    """Refuse a forged composition or any changed concrete binding."""

    if (
        type(value) is not GoldRunProductionComposition
        or getattr(value, "_trusted_seal", None) is not _RUN_COMPOSITION_SEAL
    ):
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "an exact sealed run composition is required")
    if type(value.controller) is not GoldRunController:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "composition holds a foreign controller")
    value.manifest.validate_identity()
    return value


def run_gold_run(composition: GoldRunProductionComposition):
    """Start and drive the composed run; the production entry to §26."""

    checked = require_gold_run_composition(composition)
    checked.controller.start(checked.run_root)
    return checked.controller.run(checked.run_root)


__all__ = [
    "GoldRunProductionComposition",
    "create_gold_run_composition",
    "require_gold_run_composition",
    "run_gold_run",
]
