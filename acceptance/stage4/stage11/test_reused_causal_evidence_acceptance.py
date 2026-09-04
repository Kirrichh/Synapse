"""Cross-attempt acceptance: causal evidence is bound to its attempt identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)

from acceptance.stage4.stage11._builders import run_world


def test_prior_attempt_causal_evidence_cannot_authorize_the_next_dispatch(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path,
        max_attempts=2,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(False, False)],
        worker_outcomes=("PATCH", "PATCH"),
        run_id="reused-causal-evidence",
    )
    world.attempt_inputs.reused_inputs[2] = 1

    with pytest.raises(GoldRunViolation) as caught:
        world.execute()

    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
    assert world.worker_process.calls == 1
    assert world.oracle.calls == 1
