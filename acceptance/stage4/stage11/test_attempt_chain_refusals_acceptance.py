"""§21 acceptance: every way of breaking a run's snapshot chain is refused.

Each case here presents a world where nothing is malformed -- the hashes are
valid, the records exist, the sequence numbers line up -- and the only thing
wrong is *which* boundary the attempt would attach to. That is the class of
fault a lineage check exists for, and the class that passes every other check.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from synapse.experiments.gold.runner.vocabulary import GoldRunViolation

from acceptance.stage4.stage11._attempt_chain import context_naming, run_factory


def test_a_second_genesis_inside_one_run_is_refused(tmp_path: Path) -> None:
    case = run_factory(tmp_path)
    case.factory.world_for_attempt(manifest=None, attempt_index=1, previous_context=None)

    with pytest.raises(GoldRunViolation) as raised:
        case.factory.world_for_attempt(
            manifest=None, attempt_index=1, previous_context=None
        )

    assert "cannot commit onto an existing boundary" in str(raised.value)


def test_a_continued_attempt_without_its_predecessor_context_is_refused(
    tmp_path: Path,
) -> None:
    case = run_factory(tmp_path)
    case.factory.world_for_attempt(manifest=None, attempt_index=1, previous_context=None)

    with pytest.raises(GoldRunViolation) as raised:
        case.factory.world_for_attempt(
            manifest=None, attempt_index=2, previous_context=None
        )

    assert "context of the attempt before it" in str(raised.value)


def test_a_predecessor_context_naming_another_snapshot_is_refused(
    tmp_path: Path,
) -> None:
    case = run_factory(tmp_path)
    first = case.factory.world_for_attempt(
        manifest=None, attempt_index=1, previous_context=None
    )
    forged = context_naming(first)
    # Every hash in this context is well-formed and names a real record. It just
    # does not name the boundary this run committed.
    forged.phase_refs.knowledge_snapshot_ref = (
        first.assembled.snapshot.boundary.completeness_decision_ref
    )

    with pytest.raises(GoldRunViolation) as raised:
        case.factory.world_for_attempt(
            manifest=None, attempt_index=2, previous_context=forged
        )

    assert "not the snapshot the previous attempt named" in str(raised.value)


def test_an_attempt_that_skips_its_predecessor_is_refused(tmp_path: Path) -> None:
    case = run_factory(tmp_path)
    first = case.factory.world_for_attempt(
        manifest=None, attempt_index=1, previous_context=None
    )

    with pytest.raises(GoldRunViolation) as raised:
        case.factory.world_for_attempt(
            manifest=None, attempt_index=3, previous_context=context_naming(first)
        )

    assert "neither this attempt nor the one before it" in str(raised.value)


def test_the_first_attempt_cannot_name_a_predecessor(tmp_path: Path) -> None:
    case = run_factory(tmp_path)

    with pytest.raises(GoldRunViolation) as raised:
        case.factory.world_for_attempt(
            manifest=None,
            attempt_index=1,
            previous_context=SimpleNamespace(phase_refs=SimpleNamespace()),
        )

    assert "cannot name a predecessor" in str(raised.value)
