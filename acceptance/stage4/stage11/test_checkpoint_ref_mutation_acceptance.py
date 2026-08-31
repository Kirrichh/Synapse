"""Mutation acceptance: worker and C1 checkpoint refs bind their exact bytes."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._record_mutations import (
    clone_run_records,
    replace_progress_ref_digest,
)


def test_worker_and_c1_checkpoint_refs_from_other_bytes_are_refused(
    tmp_path: Path,
) -> None:
    world = run_world(
        tmp_path / "source",
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="mismatched-checkpoint-refs",
    )
    world.execute()

    for phase in ("worker_completed", "c1_completed"):
        def mutate(
            kind: str,
            key: str,
            stored: dict[str, object],
            phase: str = phase,
        ):
            if kind == RecordKind.ATTEMPT_PROGRESS and key.endswith(phase):
                claimed = stored["payload"]["payload_ref"]["sha256"]
                replacement = ("0" if claimed[0] != "0" else "1") + claimed[1:]
                return replace_progress_ref_digest(stored, digest=replacement)
            return stored

        forged = clone_run_records(
            world.composition.record_store,
            tmp_path / f"forged-{phase}",
            mutate=mutate,
        )
        with pytest.raises(GoldRunViolation) as caught:
            load_run_state(forged)
        assert caught.value.failure_code is GoldRunFailureCode.IDENTITY_MISMATCH
