"""Stage 4 filesystem-backed admission ports (Patch 8 repair, round 12).

The four gates, their durable lineage and their fenced reads were all built
against ports, and every one of those ports was only ever satisfied by a class
defined inside a test file. That is the NR-06 line: durability demonstrated
against a Python list is not durability, and a fence backed by an attribute
cannot see anything a second process does.

So these tests are about the properties the in-memory doubles could not show.
A chain committed by one object is verified by a *different* object built on the
same path, which is what a restart actually is. A rolled-back and a forked
history are produced by editing the file rather than by a flag. And the epoch is
advanced by real concurrent processes, because the failure mode of the obvious
counter implementation — two writers reading *n* and both writing *n+1* — is
invisible to any single-threaded test.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import admission_journal as J
from synapse.experiments.gold import admission_store as S
from synapse.experiments.gold import coordination as C
from synapse.experiments.gold.persistence import scan_journal

from tests.test_stage4_gold_admission import (
    BOUNDARY_REF,
    CONTEXT_REF,
    NOW,
    SUBJECTS,
    anchors,
    controller,
    full_chain,
)


def journal_at(root: Path) -> J.FileAdmissionJournal:
    return J.FileAdmissionJournal(root / "admission" / "decisions.journal", fence_at(root))


def fence_at(root: Path) -> J.FileSnapshotFence:
    return J.FileSnapshotFence(root / "fence")


def committed_chain(root: Path):
    """A real four-gate chain written to a real file."""

    control = controller()
    ingestion, publication, retrieval, consumption = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=ingestion, publication=publication, retrieval=retrieval, consumption=consumption
    )
    evidence = S.commit_gate_chain(chain, store=journal_at(root), trusted_clock=lambda: NOW)
    return control, chain, evidence


# ---------------------------------------------------------------------------
# The ports are satisfied by shape, not by inheritance
# ---------------------------------------------------------------------------


def test_the_file_journal_satisfies_both_admission_ports(tmp_path: Path) -> None:
    store = journal_at(tmp_path)
    assert S.require_admission_history(store) is store
    assert isinstance(store, A.DecisionJournalPort)


def test_the_file_fence_satisfies_the_coordination_port(tmp_path: Path) -> None:
    fence = fence_at(tmp_path)
    assert C.require_snapshot_fence(fence) is fence


# ---------------------------------------------------------------------------
# What only a real file can demonstrate: surviving a restart
# ---------------------------------------------------------------------------


def test_a_committed_chain_verifies_against_a_freshly_opened_journal(tmp_path: Path) -> None:
    """The restart property, stated the only way it can honestly be stated.

    The object that wrote the chain is discarded. Verification runs against a
    new instance that shares nothing with it but the path, so a pass here cannot
    come from state the writer happened to still be holding.
    """

    _, chain, evidence = committed_chain(tmp_path)
    reopened = journal_at(tmp_path)
    assert S.recover_chain_evidence(evidence, chain=chain, store=reopened) is evidence


def test_positions_survive_a_restart_and_stay_contiguous(tmp_path: Path) -> None:
    _, chain, evidence = committed_chain(tmp_path)
    reopened = journal_at(tmp_path)
    positions = [reopened.record_position(digest) for digest in evidence.decision_digests]
    assert positions == [evidence.first_position + offset for offset in range(4)]


def test_an_unwritten_journal_reports_an_empty_history_not_an_outage(tmp_path: Path) -> None:
    """Nothing committed yet is a definite answer, and the genesis anchor is it.

    NR-10 refuses the substitution in both directions. A journal that has never
    been appended to is not unreachable, and reporting it as unreachable would
    tell a caller to retry a question that is already settled.
    """

    store = journal_at(tmp_path)
    assert store.current_anchor() == J.FileAdmissionJournal(tmp_path / "other" / "j", fence_at(tmp_path)).current_anchor()
    assert store.extends(store.current_anchor())
    assert store.contains_record("0" * 64) is False


# ---------------------------------------------------------------------------
# Rollback and fork, produced by editing the file
# ---------------------------------------------------------------------------


def test_a_truncated_history_is_refused_after_the_restart(tmp_path: Path) -> None:
    """Restoring an older copy of the file must not leave the chain verifiable."""

    _, chain, evidence = committed_chain(tmp_path)
    path = journal_at(tmp_path).path
    before = path.read_bytes()
    journal_at(tmp_path).append_record(b"a later unrelated record")
    path.write_bytes(before[: len(before) // 2])

    with pytest.raises(A.AdmissionViolation) as caught:
        S.recover_chain_evidence(evidence, chain=chain, store=journal_at(tmp_path))
    assert caught.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_reordered_history_keeps_every_record_and_still_fails(tmp_path: Path) -> None:
    """The case inclusion alone cannot catch.

    Every one of the four decisions is still in the file. Only the order changed,
    so a membership test passes on all four and the chain is nonetheless a record
    of something that did not happen — which is exactly what the anchor is for.
    """

    _, chain, evidence = committed_chain(tmp_path)
    original = journal_at(tmp_path)
    payload_digests = list(original._digests())

    forked_root = tmp_path / "forked"
    forked_root.mkdir()
    forked = J.FileAdmissionJournal(forked_root / "admission" / "decisions.journal", fence_at(forked_root))
    frames = _payloads(original.path)
    for payload in [frames[1], frames[0], *frames[2:]]:
        forked.append_record(payload)

    assert sorted(forked._digests()) == sorted(payload_digests)
    assert all(forked.contains_record(digest) for digest in evidence.decision_digests)
    with pytest.raises(A.AdmissionViolation) as caught:
        S.recover_chain_evidence(evidence, chain=chain, store=forked)
    assert caught.value.failure_code is A.AdmissionFailureCode.JOURNAL_ROLLED_BACK


def _payloads(path: Path) -> list[bytes]:
    return [frame.payload for frame in scan_journal(path).frames]


def test_an_appended_history_still_extends_the_witnessed_anchor(tmp_path: Path) -> None:
    """Growth is legal; only a fork is not."""

    _, chain, evidence = committed_chain(tmp_path)
    later = journal_at(tmp_path)
    later.append_record(b"an unrelated later decision")
    assert later.extends(evidence.final_anchor)
    assert S.recover_chain_evidence(evidence, chain=chain, store=journal_at(tmp_path)) is evidence


# ---------------------------------------------------------------------------
# Absent, corrupt and unreachable are three different answers
# ---------------------------------------------------------------------------


def test_a_missing_record_is_reported_as_absent_and_not_as_an_outage(tmp_path: Path) -> None:
    store = journal_at(tmp_path)
    store.append_record(b"something else")
    with pytest.raises(J.JournalAdapterViolation) as caught:
        store.record_position("f" * 64)
    assert caught.value.failure_code is J.JournalAdapterFailureCode.RECORD_ABSENT
    assert not isinstance(caught.value, A.GateDependencyUnavailable)


def test_a_foreign_file_at_the_journal_path_is_corruption_and_not_an_outage(tmp_path: Path) -> None:
    """Present and not ours is settled; retrying cannot change it."""

    store = journal_at(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(b"this is somebody else's file entirely")
    with pytest.raises(J.JournalAdapterViolation) as caught:
        store.current_anchor()
    assert caught.value.failure_code is J.JournalAdapterFailureCode.JOURNAL_CORRUPT

    with pytest.raises(J.JournalAdapterViolation) as appended:
        store.append_record(b"payload")
    assert appended.value.failure_code is J.JournalAdapterFailureCode.JOURNAL_CORRUPT


def test_an_unreachable_journal_is_declared_the_way_the_gates_expect(tmp_path: Path) -> None:
    """An outage must arrive as ``GateDependencyUnavailable``.

    The gates classify by exception type: an adapter that raised its own error
    for an outage would be filed as a broken adapter, and the incident would be
    investigated in the wrong place.
    """

    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"not a directory")
    store = J.FileAdmissionJournal(blocked / "admission" / "decisions.journal", fence_at(tmp_path))
    with pytest.raises(A.GateDependencyUnavailable):
        store.current_anchor()
    with pytest.raises(A.GateDependencyUnavailable):
        store.append_record(b"payload")


def test_a_journal_record_must_be_non_empty_bytes(tmp_path: Path) -> None:
    store = journal_at(tmp_path)
    for bad in (b"", "text", bytearray(b"x"), None):
        with pytest.raises(J.JournalAdapterViolation) as caught:
            store.append_record(bad)
        assert caught.value.failure_code is J.JournalAdapterFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# The fence
# ---------------------------------------------------------------------------


def test_a_fenced_read_of_a_still_world_succeeds(tmp_path: Path) -> None:
    fence = fence_at(tmp_path)
    fence.bump()
    state = C.read_current_authority_state(controller(), fence=fence)
    assert state.exit_epoch == state.lease.entry_epoch == fence.current_epoch()


def test_a_store_that_moves_mid_capture_makes_the_read_refuse(tmp_path: Path) -> None:
    """The scenario the fence exists for, driven by a real epoch on disk.

    Nothing is malformed: every head reads normally. The world simply moved
    between the entry and the exit epoch, so the six values may not describe one
    moment and there is no way from here to tell which came from before.
    """

    fence = fence_at(tmp_path)

    def moving_heads():
        fence.bump()
        return anchors()

    with pytest.raises(C.FenceViolation) as caught:
        C.read_current_authority_state(controller(heads=moving_heads), fence=fence)
    assert caught.value.failure_code is C.FenceFailureCode.OBSERVATION_TORN


def test_the_epoch_survives_a_restart(tmp_path: Path) -> None:
    fence_at(tmp_path).bump()
    fence_at(tmp_path).bump()
    assert fence_at(tmp_path).current_epoch() == 2


def test_the_epoch_never_decreases_when_writers_run_concurrently(tmp_path: Path) -> None:
    """The property a counter file cannot provide.

    Read-modify-write on a number loses mutations under concurrency: two writers
    both read *n*, both write *n+1*, and the second store change becomes
    invisible to precisely the readers this fence protects. Real processes are
    the only way to show the difference, so this test forks them.
    """

    directory = tmp_path / "fence"
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_bump_many, args=(str(directory), 25)) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=120)

    # Diagnostic rather than a bare tuple comparison. This test failed once
    # inside a mutation-campaign copy and could not be reproduced in eight
    # subsequent runs, so its cause is unknown — and a bare `== [0, 0, 0, 0]`
    # says nothing about which of "did not finish", "was killed" and "raised"
    # actually happened. The next occurrence should not need a re-run to be
    # readable.
    codes = [worker.exitcode for worker in workers]
    for worker in workers:
        if worker.is_alive():
            worker.terminate()
    assert codes == [0, 0, 0, 0], (
        f"worker exit codes {codes}: None means the worker never finished, a "
        f"negative value means it was killed by that signal, a positive one means "
        f"it raised. Epoch reached {fence_at(tmp_path).current_epoch()} of 100."
    )
    assert fence_at(tmp_path).current_epoch() == 100


def _bump_many(directory: str, count: int) -> None:
    fence = J.FileSnapshotFence(Path(directory))
    for _ in range(count):
        fence.bump()


def test_a_lease_is_released_only_by_the_holder_that_still_owns_it(tmp_path: Path) -> None:
    """A superseded holder must not erase a window it no longer owns."""

    fence = fence_at(tmp_path)
    first = fence.acquire_lease()
    second = fence.acquire_lease()
    assert first != second

    fence.release_lease(first)
    lease_path = tmp_path / "fence" / J.FENCE_LEASE_NAME
    assert lease_path.read_bytes() == second.encode("ascii")

    fence.release_lease(second)
    assert not lease_path.exists()
    fence.release_lease(second)


def test_an_invalid_lease_id_is_refused(tmp_path: Path) -> None:
    fence = fence_at(tmp_path)
    for bad in ("", "x" * (J.MAX_LEASE_ID_BYTES + 1), b"bytes", None):
        with pytest.raises(J.JournalAdapterViolation) as caught:
            fence.release_lease(bad)
        assert caught.value.failure_code is J.JournalAdapterFailureCode.TYPE_MISMATCH


def test_a_foreign_file_at_the_epoch_path_is_corruption_and_not_an_outage(tmp_path: Path) -> None:
    directory = tmp_path / "fence"
    directory.mkdir()
    (directory / J.FENCE_EPOCH_JOURNAL_NAME).write_bytes(b"somebody else's counter")
    with pytest.raises(J.JournalAdapterViolation) as caught:
        fence_at(tmp_path).current_epoch()
    assert caught.value.failure_code is J.JournalAdapterFailureCode.EPOCH_CORRUPT


def test_an_unreachable_fence_is_declared_as_a_dependency_outage(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"not a directory")
    fence = J.FileSnapshotFence(blocked / "fence")
    with pytest.raises(A.GateDependencyUnavailable):
        fence.current_epoch()
    with pytest.raises(A.GateDependencyUnavailable):
        fence.acquire_lease()


# ---------------------------------------------------------------------------
# The two together, through the point-of-use barrier
# ---------------------------------------------------------------------------


def test_the_whole_admission_path_runs_on_files_and_survives_a_restart(tmp_path: Path) -> None:
    """Both ports, the real gates, and a restart between commit and use.

    This is the acceptance the review asked for: the durable and the coordinated
    parts of §22 exercised end to end without a single test double standing in
    for a store.
    """

    control, chain, evidence = committed_chain(tmp_path)

    fence = fence_at(tmp_path)
    state = C.read_current_authority_state(control, fence=fence)
    assert state.exit_epoch == state.lease.entry_epoch

    reopened = journal_at(tmp_path)
    handle = A.admit_for_consumption(
        chain,
        controller=control,
        subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1",
        receipts=evidence.receipts,
        head_set=state.head_set,
        journal=reopened,
    )
    A.validate_admitted_handle(handle)
    assert handle.subject_refs == SUBJECTS


def test_the_admission_path_refuses_when_the_journal_was_rolled_back(tmp_path: Path) -> None:
    """A handle needs four receipts that are still in the history *now*.

    The file is rebuilt without its last record, which is what restoring an
    older copy looks like from the adapter's side: three of the four decisions
    are still present, so nothing about the chain object changed and only the
    durable history is short.
    """

    control, chain, evidence = committed_chain(tmp_path)
    head_set = A.capture_authority_heads(control)

    path = journal_at(tmp_path).path
    frames = _payloads(path)
    path.unlink()
    rebuilt = journal_at(tmp_path)
    for payload in frames[:-1]:
        rebuilt.append_record(payload)

    with pytest.raises(A.AdmissionViolation) as caught:
        A.admit_for_consumption(
            chain,
            controller=control,
            subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1",
            receipts=evidence.receipts,
            head_set=head_set,
            journal=rebuilt,
        )
    assert caught.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


# ---------------------------------------------------------------------------
# Round 19 — a store that mutates says so
# ---------------------------------------------------------------------------


def test_a_decision_append_advances_the_shared_epoch(tmp_path: Path) -> None:
    """The property the fenced read depends on and nothing used to provide.

    `capture_authority_heads` detects a torn observation by confirming the epoch
    did not move during the read. Before this round no production writer called
    `bump` at all: the epoch never advanced, every fenced read confirmed
    "unchanged", and `OBSERVATION_TORN` was unreachable. The barrier reported a
    coherent moment it had not checked, which is worse than having no barrier —
    the record claimed a verification that never happened.
    """

    fence = fence_at(tmp_path)
    store = journal_at(tmp_path)
    before = fence.current_epoch()

    store.append_record(b"a committed gate decision")
    assert fence.current_epoch() == before + 1

    store.append_record(b"a second committed gate decision")
    assert fence.current_epoch() == before + 2


def test_a_read_only_question_does_not_advance_the_epoch(tmp_path: Path) -> None:
    """A bump for something that did not mutate costs every concurrent reader.

    The epoch says "some store changed". If asking a question moved it, a reader
    holding a lease would refuse a perfectly coherent observation whenever anyone
    else merely looked — fail-closed in the letter and useless in practice.
    """

    fence = fence_at(tmp_path)
    store = journal_at(tmp_path)
    store.append_record(b"a committed gate decision")
    settled = fence.current_epoch()

    store.contains_record("0" * 64)
    store.current_anchor()
    store.extends(store.current_anchor())
    assert fence.current_epoch() == settled


def test_an_append_whose_fence_refuses_is_not_reported_as_an_outage(tmp_path: Path) -> None:
    """The record is durable, so telling the caller to retry appends it twice.

    This is the classification NR-10 is about. An unreachable journal is an
    outage and a retry is the right response. A journal that appended while the
    fence stayed put is a different fact: the decision is committed, and the only
    thing missing is the signal that lets a concurrent read notice it. Calling
    that unavailable invites a second copy of a decision that already landed.
    """

    class RefusingFence:
        def bump(self) -> int:
            raise RuntimeError("the fence is out of frames")

    store = J.FileAdmissionJournal(tmp_path / "admission" / "decisions.journal", RefusingFence())
    with pytest.raises(J.JournalAdapterViolation) as caught:
        store.append_record(b"a committed gate decision")
    assert caught.value.failure_code is J.JournalAdapterFailureCode.FENCE_NOT_ADVANCED

    # And the record really is durable, which is what makes a retry wrong.
    assert store.contains_record(
        __import__("hashlib").sha256(b"a committed gate decision").hexdigest()
    )
