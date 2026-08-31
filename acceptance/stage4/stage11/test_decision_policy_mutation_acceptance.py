"""Mutation acceptance: durable decisions are recomputed from stop policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import (
    FallbackPolicy,
    GoldRunFailureCode,
    GoldRunViolation,
    TerminalDecisionKind,
)

from acceptance.stage4.stage11._builders import run_world
from acceptance.stage4.stage11._record_mutations import clone_run_records, rehash_record


def test_resolved_attempt_cannot_store_an_arbitrary_stop_decision(tmp_path: Path) -> None:
    world = run_world(
        tmp_path / "source",
        max_attempts=1,
        fallback_policy=FallbackPolicy.FORBIDDEN,
        oracle_outcomes=[(True, False)],
        run_id="arbitrary-stop-decision",
    )
    world.execute()

    def mutate(kind: str, key: str, stored: dict[str, object]):
        if kind == RecordKind.DECISION and key == "1":
            stored["payload"]["decision"] = TerminalDecisionKind.STOP_LIMIT.value
            return rehash_record(stored)
        return stored

    forged = clone_run_records(
        world.composition.record_store,
        tmp_path / "forged",
        mutate=mutate,
        omit_kinds=frozenset({RecordKind.RUN_RESULT}),
    )
    with pytest.raises(GoldRunViolation) as caught:
        load_run_state(forged)
    assert caught.value.failure_code is GoldRunFailureCode.AUTHORITY_MISMATCH
