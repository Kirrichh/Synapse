from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from synapse.experiments.gold.persistence import (
    JOURNAL_FRAME_MAGIC_V1,
    LIBRARY_INTEGRITY_MANIFEST_V1,
    DurabilityProfile,
    ExclusiveStoreLock,
    IntegrityManifestDescriptor,
    PersistenceFailureCode,
    PersistenceViolation,
    active_durability_profile,
    append_journal_payload,
    atomic_replace_metadata,
    encode_journal_frame,
    initialize_journal,
    publish_immutable,
    scan_journal,
    store_transaction,
    truncate_journal_to_valid_prefix,
    write_staged_bytes,
)

from tests.gold_store_fence import fence_for


def _ticket(tmp_path: Path):
    """An open interval on a real coordinator, for tests about the primitives.

    Every authority primitive below requires one, and that is the point rather
    than a nuisance: the previous signatures let a caller write to an authority
    store with no coordinator anywhere in sight, and four of the six real
    mutation sites did exactly that. Holding the interval open for the body of a
    test is the same shape a production transaction has.
    """

    return store_transaction(fence_for(tmp_path / "primitive-coordinator"))


def _failure(exc: pytest.ExceptionInfo[PersistenceViolation]) -> PersistenceFailureCode:
    return exc.value.failure_code


def test_s4_p4_acc_persistence_01_immutable_publish_never_overwrites_existing_bytes(
    tmp_path: Path,
) -> None:
    first = b"first immutable payload\x00\xff"
    second = b"different payload"
    destination = tmp_path / "object"
    with _ticket(tmp_path) as ticket:
        staged = write_staged_bytes(
            tmp_path,
            final_name=destination.name,
            operation_id="1" * 32,
            value=first,
            maximum_bytes=1024,
            ticket=ticket,
        )

        publish_immutable(staged, destination, ticket=ticket)
        assert destination.read_bytes() == first
        assert (
            hashlib.sha256(destination.read_bytes()).hexdigest()
            == hashlib.sha256(first).hexdigest()
        )
        assert not staged.path.exists()

        conflicting = write_staged_bytes(
            tmp_path,
            final_name=destination.name,
            operation_id="2" * 32,
            value=second,
            maximum_bytes=1024,
            ticket=ticket,
        )
        with pytest.raises(PersistenceViolation) as exc:
            publish_immutable(conflicting, destination, ticket=ticket)
        assert _failure(exc) is PersistenceFailureCode.DESTINATION_EXISTS
        assert destination.read_bytes() == first
        conflicting.path.unlink()


def test_s4_p4_acc_persistence_02_rebuildable_metadata_uses_explicit_replace_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "index.v1"
    with _ticket(tmp_path) as ticket:
        atomic_replace_metadata(
            tmp_path, final_name=path.name, value=b"generation-one", ticket=ticket
        )
        atomic_replace_metadata(
            tmp_path, final_name=path.name, value=b"generation-two", ticket=ticket
        )
    assert path.read_bytes() == b"generation-two"


def test_s4_p4_acc_persistence_03_journal_framing_checksum_and_torn_tail_are_exact(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "library.v1"
    payload = b"{\"phase\":\"BEGIN\"}\x00\xff"
    initialize_journal(journal)
    with _ticket(tmp_path) as ticket:
        end_offset = append_journal_payload(journal, payload, ticket=ticket)
    expected = JOURNAL_FRAME_MAGIC_V1 + encode_journal_frame(payload)

    assert journal.read_bytes() == expected
    assert end_offset == len(expected)
    scan = scan_journal(journal)
    assert tuple(frame.payload for frame in scan.frames) == (payload,)
    assert scan.valid_prefix_length == len(expected)
    assert scan.torn_tail == b""

    torn = len(b"unfinished").to_bytes(8, "big") + b"unfi"
    with journal.open("ab") as stream:
        stream.write(torn)
        stream.flush()
        os.fsync(stream.fileno())
    scan = scan_journal(journal)
    assert tuple(frame.payload for frame in scan.frames) == (payload,)
    assert scan.valid_prefix_length == len(expected)
    assert scan.torn_tail == torn

    truncate_journal_to_valid_prefix(journal, scan.valid_prefix_length)
    repaired = scan_journal(journal)
    assert repaired.torn_tail == b""
    assert journal.read_bytes() == expected


def test_s4_p4_acc_persistence_04_internal_checksum_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "library.v1"
    payload = b"committed-record"
    frame = bytearray(encode_journal_frame(payload))
    frame[-1] ^= 0x01
    journal.write_bytes(JOURNAL_FRAME_MAGIC_V1 + bytes(frame))

    with pytest.raises(PersistenceViolation) as exc:
        scan_journal(journal)
    assert _failure(exc) is PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH


def test_s4_p4_acc_persistence_05_store_lock_is_exclusive_and_non_blocking(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "writer.lock"
    with ExclusiveStoreLock(lock_path):
        with pytest.raises(PersistenceViolation) as exc:
            with ExclusiveStoreLock(lock_path):
                raise AssertionError("second writer must not acquire the lock")
        assert _failure(exc) is PersistenceFailureCode.LOCK_BUSY
    with ExclusiveStoreLock(lock_path):
        pass


def test_s4_p4_acc_persistence_06_integrity_manifest_is_strict_and_round_trips() -> None:
    descriptor = IntegrityManifestDescriptor(
        LIBRARY_INTEGRITY_MANIFEST_V1,
        7,
        41,
        "1" * 64,
        "2" * 64,
        "3" * 64,
        active_durability_profile(),
    )
    assert IntegrityManifestDescriptor.from_payload(descriptor.to_payload()) == descriptor

    malformed = descriptor.to_payload()
    malformed["unexpected"] = True
    with pytest.raises(PersistenceViolation) as exc:
        IntegrityManifestDescriptor.from_payload(malformed)
    assert _failure(exc) is PersistenceFailureCode.INTEGRITY_MANIFEST_MALFORMED

    assert active_durability_profile() in {
        DurabilityProfile.POSIX_LINK_DIRECTORY_FSYNC_V1,
        DurabilityProfile.WINDOWS_RENAME_FILE_FSYNC_V1,
    }


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation is privilege-dependent")
def test_s4_p4_acc_persistence_07_links_are_rejected_before_immutable_publication(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"target")
    link = tmp_path / "linked-object"
    link.symlink_to(target)
    with _ticket(tmp_path) as ticket:
        staged = write_staged_bytes(
            tmp_path,
            final_name=link.name,
            operation_id="3" * 32,
            value=b"replacement",
            maximum_bytes=1024,
            ticket=ticket,
        )

        with pytest.raises(PersistenceViolation) as exc:
            publish_immutable(staged, link, ticket=ticket)
    assert _failure(exc) is PersistenceFailureCode.DESTINATION_EXISTS
    assert target.read_bytes() == b"target"
    staged.path.unlink()


# ---------------------------------------------------------------------------
# Round 22 — the interval, and why a required argument replaced a convention
# ---------------------------------------------------------------------------


def test_a_write_is_visible_only_while_the_epoch_says_a_write_is_in_flight(
    tmp_path: Path,
) -> None:
    """The seqlock property, checked from inside the interval.

    The round-19 version of this test asserted the opposite order — append, then
    bump — and it was right about the defect it named: a counter advanced *after*
    a write lets a reader take its entry epoch, observe the record, take its exit
    epoch and see no change. What it could not fix is that marking an instant, in
    either order, leaves the other edge open.

    Marking the interval closes both. So the checkable statement is no longer
    about ordering: it is that the record becomes visible only at a moment when
    the epoch is odd, and the epoch does not return to even until the write is
    complete.
    """

    fence = fence_for(tmp_path / "primitive-coordinator")
    path = tmp_path / "journal.v1"
    settled = fence.current_epoch()
    assert settled % 2 == 0

    with store_transaction(fence) as ticket:
        assert fence.current_epoch() == settled + 1, "odd before anything is written"
        append_journal_payload(path, b"first", ticket=ticket)
        assert len(scan_journal(path).frames) == 1
        assert fence.current_epoch() % 2 == 1, (
            "the record is visible and the epoch still says a write is in flight"
        )

    assert fence.current_epoch() == settled + 2, "even again, and only once complete"


def test_an_authority_write_without_an_interval_is_refused_rather_than_unfenced(
    tmp_path: Path,
) -> None:
    """The bypass, removed from the language instead of from a checklist.

    Round 19 provided a *fenced* wrapper beside each unfenced primitive, and a
    tripwire that scanned for direct calls. It scanned for one of six primitives,
    and four of the six real mutation sites wrote outside any interval. A
    required argument is what makes that impossible: there is no unfenced call to
    find, because there is no unfenced call.
    """

    for call in (
        lambda: append_journal_payload(tmp_path / "journal.v1", b"payload", ticket=None),
        lambda: atomic_replace_metadata(
            tmp_path, final_name="index.v1", value=b"x", ticket=None
        ),
        lambda: write_staged_bytes(
            tmp_path,
            final_name="object",
            operation_id="4" * 32,
            value=b"x",
            maximum_bytes=16,
            ticket=None,
        ),
    ):
        with pytest.raises(PersistenceViolation) as exc:
            call()
        assert _failure(exc) is PersistenceFailureCode.MUTATION_NOT_FENCED
    assert not (tmp_path / "journal.v1").exists(), "a refused write leaves nothing behind"
    assert not (tmp_path / "index.v1").exists()


def test_a_ticket_outliving_its_interval_is_a_separate_refusal(tmp_path: Path) -> None:
    """Closed is not absent, and NR-10 forbids answering as though it were.

    A caller holding no ticket has a missing transaction. A caller holding a
    closed one followed the protocol and then kept the capability past the end of
    the interval that justified it — a different bug, in a different place, and a
    far more dangerous one, because the write would otherwise land in a window
    the coordinator has already declared settled.
    """

    fence = fence_for(tmp_path / "primitive-coordinator")
    with store_transaction(fence) as ticket:
        pass

    with pytest.raises(PersistenceViolation) as exc:
        append_journal_payload(tmp_path / "journal.v1", b"payload", ticket=ticket)
    assert _failure(exc) is PersistenceFailureCode.MUTATION_INTERVAL_CLOSED
    assert not (tmp_path / "journal.v1").exists()


def test_a_forged_ticket_cannot_authorise_a_write(tmp_path: Path) -> None:
    """The capability is sealed, so filling in its fields is not enough."""

    from synapse.experiments.gold.persistence import StoreMutationTicket

    forged = object.__new__(StoreMutationTicket)
    object.__setattr__(forged, "coordinator_id", "anything")
    object.__setattr__(forged, "interval_epoch", 1)
    object.__setattr__(forged, "_open", True)
    object.__setattr__(forged, "_trusted_seal", object())

    with pytest.raises(PersistenceViolation) as exc:
        append_journal_payload(tmp_path / "journal.v1", b"payload", ticket=forged)
    assert _failure(exc) is PersistenceFailureCode.MUTATION_NOT_FENCED
    with pytest.raises(TypeError):
        StoreMutationTicket()


def test_a_transaction_that_raises_never_reports_success(tmp_path: Path) -> None:
    """The abort, and the reflex it exists to refuse.

    Closing the interval in a ``finally`` is the reflex, and it is wrong here: a
    transaction that raised may have changed the store partially, and returning
    the epoch to even tells every reader the store is settled. The caller saw its
    exception; the readers would have seen a quiet world.
    """

    fence = fence_for(tmp_path / "primitive-coordinator")
    settled = fence.current_epoch()

    with pytest.raises(Exception) as exc:
        with store_transaction(fence) as ticket:
            append_journal_payload(tmp_path / "journal.v1", b"half a transaction", ticket=ticket)
            raise RuntimeError("the writer gave up")

    assert type(exc.value).__name__ == "JournalAdapterViolation"
    assert exc.value.failure_code.value == "MUTATION_ABORTED"
    assert fence.current_epoch() == settled + 1, "odd, and it stays odd until someone looks"
