"""§21 acceptance: a run's snapshots form one chain, one boundary per attempt.

Before this, one environment was built per run and handed to every attempt, so a
run's second attempt rested on evidence minted before its own predecessor had
run. These cases drive ``GoldAttemptWorldFactory`` directly, because what is
under test is the chain itself rather than what a controller does with it.
"""

from __future__ import annotations

from pathlib import Path

from acceptance.stage4.stage11._attempt_chain import context_naming, run_factory


def test_the_first_attempt_commits_a_boundary_with_no_parent(tmp_path: Path) -> None:
    case = run_factory(tmp_path)

    first = case.factory.world_for_attempt(
        manifest=None, attempt_index=1, previous_context=None
    )

    assert first.parent_snapshot is None
    assert first.assembled.snapshot.boundary.parent_boundary_digest is None
    assert first.assembled.snapshot.manifest.parent_snapshot_digest is None


def test_the_second_attempt_commits_onto_the_first_ones_boundary(tmp_path: Path) -> None:
    case = run_factory(tmp_path)
    first = case.factory.world_for_attempt(
        manifest=None, attempt_index=1, previous_context=None
    )

    second = case.factory.world_for_attempt(
        manifest=None, attempt_index=2, previous_context=context_naming(first)
    )

    parent = first.assembled.snapshot.boundary
    child = second.assembled.snapshot
    assert second.parent_snapshot is not None
    # The transaction it extends, and the state it descends from: §21 checks
    # both, so a child cannot declare one lineage while chaining onto another.
    assert child.boundary.parent_boundary_digest == parent.atomic_boundary_id.digest_sha256
    assert child.manifest.parent_snapshot_digest == parent.manifest_ref.ref_id
    # The chain is a sequence, not only a pointer: a child starts where its
    # parent committed, so two boundaries can never claim the same span.
    assert child.boundary.start_sequence == parent.commit_sequence


def test_each_attempt_gets_its_own_environment_and_retrieval(tmp_path: Path) -> None:
    case = run_factory(tmp_path)
    first = case.factory.world_for_attempt(
        manifest=None, attempt_index=1, previous_context=None
    )

    second = case.factory.world_for_attempt(
        manifest=None, attempt_index=2, previous_context=context_naming(first)
    )

    assert second.environment is not first.environment
    assert second.retrieval is not first.retrieval
    assert (
        second.assembled.snapshot.boundary.manifest_ref
        != first.assembled.snapshot.boundary.manifest_ref
    )
