"""Composition acceptance: changed owner bindings cannot execute a run."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)

from acceptance.stage4.stage11._builders import run_world


def test_changed_attempt_input_owner_is_refused_before_dispatch(tmp_path: Path) -> None:
    world = run_world(
        tmp_path,
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="changed-composition-owner",
    )
    object.__setattr__(world.composition, "_attempt_inputs", object())

    with pytest.raises(GoldRunViolation) as caught:
        world.execute()

    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
