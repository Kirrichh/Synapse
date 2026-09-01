"""Build the run world these chain cases drive, and the context an attempt carries.

Test-support only: it supplies the world a deployment would have and reads two
refs back off it. Every rule about what may attach to what belongs to
``GoldAttemptWorldFactory`` and is asserted, not reproduced, here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from synapse.experiments.gold.retrieval import retrieval_causal_record_ref

from acceptance.stage4.stage11._retrieval_inputs import acceptance_retrieval_bindings
from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case


class _UnusedReplay:
    """A replay these cases never reach, and which refuses to be reached.

    The chain cases drive the factory directly to see which boundary an attempt
    commits onto; they never run an attempt, so nothing asks this for a replay.
    It refuses rather than returning something, so a case that started reaching
    it would fail loudly instead of silently replaying nothing.
    """

    def replay_for_attempt(self, *, manifest, attempt_index):
        del manifest, attempt_index
        raise AssertionError("the chain cases must not reach a governed replay")


class _UnusedReplayBinding:
    """Bind the replay above. Production requires the second phase to produce
    a real port, so these cases supply one rather than skipping the phase.

    This is not the suite's replay double: ``_builders`` binds a real governed
    replay through ``replay_composition``, and the lifecycle case exercises it.
    """

    def bind(self, context):
        del context
        return _UnusedReplay()


def run_factory(tmp_path: Path):
    """One run's authority world, and production's per-attempt factory over it."""

    return production_point_of_use_case(
        tmp_path / "case",
        retrieval_bindings=acceptance_retrieval_bindings(),
        retrieval_root=tmp_path / "retrieval",
        attempt_world_factory=True,
        replay_binding=_UnusedReplayBinding(),
    )


def context_naming(world) -> SimpleNamespace:
    """The predecessor context an attempt would carry forward.

    Only the two refs the factory and the input source actually read: the
    snapshot this attempt planned against, and the retrieval it decided.
    """

    return SimpleNamespace(
        phase_refs=SimpleNamespace(
            knowledge_snapshot_ref=world.assembled.snapshot.boundary.manifest_ref,
            retrieval_ref=retrieval_causal_record_ref(
                world.retrieval.retrieved.result.causal_record
            ),
        )
    )


__all__ = ["context_naming", "run_factory"]
