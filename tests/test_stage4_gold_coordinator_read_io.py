"""Coordinator identity readback must not create another physical write."""

import os
import pytest

from synapse.experiments.gold import admission_journal as J
from synapse.experiments.gold import persistence as P


def test_established_identity_reopens_bytes_without_any_write(tmp_path, monkeypatch):
    directory = tmp_path / "coordinator"
    expected = J.FileSnapshotFence(directory).coordinator_id()
    final = directory / J.FENCE_IDENTITY_NAME
    before = final.stat()
    actual_open = P.os.open
    actual_read = P.read_regular_bytes
    reads = []

    def read_only_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return actual_open(path, flags, *args, **kwargs)

    def observed_read(path, *, maximum_bytes):
        reads.append(path)
        return actual_read(path, maximum_bytes=maximum_bytes)

    def unexpected_mutation(*args, **kwargs):
        raise AssertionError("an established coordinator identity attempted filesystem mutation")

    with monkeypatch.context() as patch:
        patch.setattr(P.os, "open", read_only_open)
        patch.setattr(P, "read_regular_bytes", observed_read)
        for operation in ("write", "fsync", "link", "rename", "replace", "unlink"):
            patch.setattr(P.os, operation, unexpected_mutation)
        for _ in range(4):
            assert J.FileSnapshotFence(directory).coordinator_id() == expected

    assert reads == [final] * 4
    assert final.read_bytes() == expected.encode("ascii")
    assert final.stat().st_mtime_ns == before.st_mtime_ns
    assert sorted(directory.iterdir()) == [final]


@pytest.mark.parametrize("raw", (b"g" * 32, b"a" * 33))
def test_invalid_existing_identity_is_refused_without_staging(tmp_path, monkeypatch, raw):
    directory = tmp_path / "coordinator"
    directory.mkdir()
    final = directory / J.FENCE_IDENTITY_NAME
    final.write_bytes(raw)

    def unexpected_stage(*args, **kwargs):
        raise AssertionError("invalid existing identity must not cause a new publication")

    monkeypatch.setattr(P, "_write_staged_bytes_unfenced", unexpected_stage)
    with pytest.raises(J.JournalAdapterViolation) as caught:
        J.FileSnapshotFence(directory).coordinator_id()
    assert caught.value.failure_code is J.JournalAdapterFailureCode.EPOCH_CORRUPT
    assert final.read_bytes() == raw
    assert sorted(directory.iterdir()) == [final]


def test_same_fence_rechecks_the_physical_identity(tmp_path):
    directory = tmp_path / "coordinator"
    fence = J.FileSnapshotFence(directory)
    original = fence.coordinator_id()
    final = directory / J.FENCE_IDENTITY_NAME
    changed = ("b" if original != "b" * 32 else "c") * 32
    final.write_bytes(changed.encode("ascii"))

    assert fence.coordinator_id() == changed


def test_existing_identity_read_error_does_not_start_creation(tmp_path, monkeypatch):
    directory = tmp_path / "coordinator"
    original = J.FileSnapshotFence(directory).coordinator_id()
    final = directory / J.FENCE_IDENTITY_NAME

    def unavailable(path):
        assert path == final
        raise P.PersistenceViolation(P.PersistenceFailureCode.FILESYSTEM_IO_FAILED, "identity read unavailable")

    def unexpected_stage(*args, **kwargs):
        raise AssertionError("a failed existing read must not be treated as first creation")

    monkeypatch.setattr(P, "_open_no_follow_read", unavailable)
    monkeypatch.setattr(P, "_write_staged_bytes_unfenced", unexpected_stage)
    with pytest.raises(P.PersistenceViolation) as caught:
        P.create_coordinator_metadata_once(
            directory, final_name=final.name, value=b"c" * 32, maximum_bytes=32)
    assert caught.value.failure_code is P.PersistenceFailureCode.FILESYSTEM_IO_FAILED
    assert final.read_bytes() == original.encode("ascii")
    assert sorted(directory.iterdir()) == [final]


def test_creator_adopts_winner_published_after_its_initial_read(tmp_path, monkeypatch):
    directory = tmp_path / "coordinator"
    directory.mkdir()
    final = directory / J.FENCE_IDENTITY_NAME
    actual_publish = P._publish_by_platform
    publications = []

    def interleave_publish(staged, destination):
        publications.append(staged)
        if len(publications) == 1:
            assert not final.exists()
            winner = P.create_coordinator_metadata_once(
                directory, final_name=final.name, value=b"b" * 32, maximum_bytes=32)
            assert winner == b"b" * 32
        actual_publish(staged, destination)

    monkeypatch.setattr(P, "_publish_by_platform", interleave_publish)
    observed = P.create_coordinator_metadata_once(
        directory, final_name=final.name, value=b"a" * 32, maximum_bytes=32)

    assert observed == final.read_bytes() == b"b" * 32
    assert len(publications) == 2
    assert sorted(directory.iterdir()) == [final]


def test_initial_identity_remains_unpublished_when_sync_fails(tmp_path, monkeypatch):
    directory = tmp_path / "coordinator"
    directory.mkdir()
    final = directory / J.FENCE_IDENTITY_NAME

    def failed_sync(fd):
        assert not final.exists()
        assert os.fstat(fd).st_size == 32
        raise OSError("initial identity sync failed")

    monkeypatch.setattr(P.os, "fsync", failed_sync)
    with pytest.raises(OSError, match="initial identity sync failed"):
        P.create_coordinator_metadata_once(
            directory, final_name=final.name, value=b"a" * 32, maximum_bytes=32)

    assert not final.exists()
    assert not tuple(directory.iterdir())
