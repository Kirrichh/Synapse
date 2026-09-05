"""Mutation acceptance: interrupted results require an exact crash prefix."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    AttemptOutcome,
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._record_mutations import clone_run_records, rehash_record


def test_full_c1_phase_chain_cannot_be_relabelled_as_interrupted(tmp_path: Path) -> None:
    world = run_world(
        tmp_path / "source",
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="invalid-interrupted-prefix",
    )
    world.execute()

    def mutate(kind: str, key: str, stored: dict[str, object]):
        if kind != RecordKind.ATTEMPT_RESULT or key != "1":
            return stored
        payload = stored["payload"]
        payload.update(
            outcome=AttemptOutcome.CONTROLLER_INTERRUPTED.value,
            c1_status=None,
            oracle_invoked=False,
            oracle_resolved=None,
            c1_result_ref=None,
            oracle_result_ref=None,
            publication_refs=[],
        )
        return rehash_record(stored)

    forged = clone_run_records(
        world.composition.record_store,
        tmp_path / "forged",
        mutate=mutate,
        omit_kinds=frozenset({RecordKind.DECISION, RecordKind.RUN_RESULT}),
    )
    with pytest.raises(GoldRunViolation) as caught:
        load_run_state(forged)
    assert caught.value.failure_code is GoldRunFailureCode.PHASE_INVALID
