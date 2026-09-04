"""Mutation acceptance: Stage 11 cannot invent C1 publication authority."""

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
from acceptance.stage4.stage11._record_mutations import clone_run_records, rehash_record


def test_c1_result_with_fabricated_publication_ref_is_refused(tmp_path: Path) -> None:
    world = run_world(
        tmp_path / "source",
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="fabricated-c1-publication",
    )
    world.execute()

    def mutate(kind: str, key: str, stored: dict[str, object]):
        if kind != RecordKind.ATTEMPT_RESULT or key != "1":
            return stored
        payload = stored["payload"]
        payload["publication_refs"] = [dict(payload["c1_result_ref"])]
        return rehash_record(stored)

    forged = clone_run_records(
        world.composition.record_store,
        tmp_path / "forged",
        mutate=mutate,
        omit_kinds=frozenset({RecordKind.DECISION, RecordKind.CONTINUATION_EVIDENCE, RecordKind.RUN_RESULT}),
    )
    with pytest.raises(GoldRunViolation) as caught:
        load_run_state(forged)
    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
    assert caught.value.detail == "attempt result differs from durable C1 authority"
