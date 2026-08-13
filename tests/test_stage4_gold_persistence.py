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
    append_journal_payload_fenced,
    atomic_replace_metadata,
    encode_journal_frame,
    initialize_journal,
    publish_immutable,
    scan_journal,
    truncate_journal_to_valid_prefix,
    write_staged_bytes,
)


def _failure(exc: pytest.ExceptionInfo[PersistenceViolation]) -> PersistenceFailureCode:
    return exc.value.failure_code


def test_s4_p4_acc_persistence_01_immutable_publish_never_overwrites_existing_bytes(
    tmp_path: Path,
) -> None:
    first = b"first immutable payload\x00\xff"
    second = b"different payload"
    destination = tmp_path / "object"
    staged = write_staged_bytes(
        tmp_path,
        final_name=destination.name,
        operation_id="1" * 32,
        value=first,
        maximum_bytes=1024,
    )

    publish_immutable(staged, destination)
    assert destination.read_bytes() == first
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == hashlib.sha256(first).hexdigest()
    assert not staged.path.exists()

    conflicting = write_staged_bytes(
        tmp_path,
        final_name=destination.name,
        operation_id="2" * 32,
        value=second,
        maximum_bytes=1024,
    )
    with pytest.raises(PersistenceViolation) as exc:
        publish_immutable(conflicting, destination)
    assert _failure(exc) is PersistenceFailureCode.DESTINATION_EXISTS
    assert destination.read_bytes() == first
    conflicting.path.unlink()


def test_s4_p4_acc_persistence_02_rebuildable_metadata_uses_explicit_replace_semantics(
    tmp_path: Path,
) -> None:
    path = tmp_path / "index.v1"
    atomic_replace_metadata(tmp_path, final_name=path.name, value=b"generation-one")
    atomic_replace_metadata(tmp_path, final_name=path.name, value=b"generation-two")
    assert path.read_bytes() == b"generation-two"


def test_s4_p4_acc_persistence_03_journal_framing_checksum_and_torn_tail_are_exact(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "library.v1"
    payload = b"{\"phase\":\"BEGIN\"}\x00\xff"
    initialize_journal(journal)
    end_offset = append_journal_payload(journal, payload)
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
    staged = write_staged_bytes(
        tmp_path,
        final_name=link.name,
        operation_id="3" * 32,
        value=b"replacement",
        maximum_bytes=1024,
    )

    with pytest.raises(PersistenceViolation) as exc:
        publish_immutable(staged, link)
    assert _failure(exc) is PersistenceFailureCode.DESTINATION_EXISTS
    assert target.read_bytes() == b"target"
    staged.path.unlink()


# ---------------------------------------------------------------------------
# Round 19 — append then bump, and the order is the property
# ---------------------------------------------------------------------------


class _RecordingFence:
    """A fence that records what the journal held at the moment it was bumped."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self.frames_seen: list[int] = []

    def bump(self) -> int:
        self.frames_seen.append(len(scan_journal(self._path).frames))
        return len(self.frames_seen)


def test_a_fenced_append_bumps_after_the_record_is_durable(tmp_path: Path) -> None:
    """Reversing these two is the one ordering that is not fail-closed.

    Bump-then-append announces a mutation that may never land. That costs a
    concurrent reader an observation it could have kept — wasteful, and safe,
    because the reader errs towards refusing.

    Append-then-bump is what makes a reader whose window spans this call see the
    epoch move. Reverse them and a reader can complete its whole window between
    the bump and the append: it sees a settled epoch, concludes nothing changed,
    and accepts an observation that the append has just torn. So the fence is
    asked what the journal held when it was called, and the answer must already
    include this record.
    """

    path = tmp_path / "journal.v1"
    fence = _RecordingFence(path)

    append_journal_payload_fenced(path, b"first", fence=fence)
    assert fence.frames_seen == [1], "the bump must see its own record already committed"

    append_journal_payload_fenced(path, b"second", fence=fence)
    assert fence.frames_seen == [1, 2]


def test_a_fenced_append_whose_fence_refuses_reports_the_fence_and_not_the_io(
    tmp_path: Path,
) -> None:
    """Two conditions asking for opposite responses, so they get separate codes.

    A failed append left nothing behind and a retry is safe. An append that
    landed while the fence stayed put left a durable record no reader can detect,
    and a retry writes it twice.
    """

    class RefusingFence:
        def bump(self) -> int:
            raise RuntimeError("the fence is exhausted")

    path = tmp_path / "journal.v1"
    with pytest.raises(PersistenceViolation) as exc:
        append_journal_payload_fenced(path, b"payload", fence=RefusingFence())
    assert _failure(exc) is PersistenceFailureCode.FENCE_NOT_ADVANCED
    assert len(scan_journal(path).frames) == 1, "the record is durable, which is why a retry is wrong"


def test_a_fenced_append_refuses_something_that_cannot_advance_an_epoch(tmp_path: Path) -> None:
    with pytest.raises(PersistenceViolation) as exc:
        append_journal_payload_fenced(tmp_path / "journal.v1", b"payload", fence=object())
    assert _failure(exc) is PersistenceFailureCode.TYPE_MISMATCH
    assert not (tmp_path / "journal.v1").exists(), "a refused fence must not have written anything"
