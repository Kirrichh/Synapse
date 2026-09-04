"""Cross-stage acceptance: A's evidence cannot execute under B's authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)

from acceptance.stage4.stage11._builders import (
    ProductionAttemptInputs,
    create_composition,
    run_world,
    with_admission_from,
)


@dataclass(frozen=True)
class _OneAttemptInputs:
    value: object

    def check_approval(self, *, manifest):
        assert self.value.plan_authority.approval_policy is None

    def prepare(self, *, manifest, attempt_index, previous_context):
        return self.value


def test_candidate_evidence_from_a_cannot_use_b_point_of_use_authority(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="candidate-substitution",
    )
    candidate_a = world.attempt_inputs.prepare(
        manifest=world.manifest,
        attempt_index=1,
        previous_context=None,
    )
    source_b = ProductionAttemptInputs(
        run_root=tmp_path / "candidate-b-run",
        source_repo=world.repo,
        environment_suffix="candidate-b",
    )
    candidate_b = source_b.prepare(
        manifest=world.manifest,
        attempt_index=1,
        previous_context=None,
    )
    substituted = with_admission_from(candidate_a, candidate_b)
    composition = create_composition(
        world,
        attempt_inputs=_OneAttemptInputs(substituted),
    )

    with pytest.raises(GoldRunViolation) as caught:
        composition.execute()

    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
