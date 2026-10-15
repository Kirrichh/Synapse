"""Streaming coordinator reads retain failure classification and exclusion."""
import pytest

from synapse.experiments.gold import admission_journal as J
from synapse.experiments.gold import persistence as P
from synapse.experiments.gold.admission import GateDependencyUnavailable


def _fence(tmp_path, raw, *, read_only=False):
    directory = tmp_path / "coordinator"
    writer = J.FileSnapshotFence(directory)
    writer.coordinator_id()
    (directory / J.FENCE_EPOCH_JOURNAL_NAME).write_bytes(raw)
    return J.FileSnapshotFence(directory, read_only=read_only)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("tail", [b"\x00", (9).to_bytes(8, "big") + b"partial"])
def test_torn_epoch_never_reports_settled_state(tmp_path, read_only, tail):
    raw = P.JOURNAL_FRAME_MAGIC_V1 + P.encode_journal_frame(b"open") + P.encode_journal_frame(b"close") + tail
    fence = _fence(tmp_path, raw, read_only=read_only)
    with pytest.raises(J.JournalAdapterViolation) as caught:
        fence.current_epoch()
    assert caught.value.failure_code is J.JournalAdapterFailureCode.EPOCH_CORRUPT
    assert fence._epoch_path.read_bytes() == raw
    if not read_only:
        with pytest.raises(J.JournalAdapterViolation):
            with P.store_transaction(fence):
                pytest.fail("a torn coordinator must issue no mutation ticket")
        assert fence._epoch_path.read_bytes() == raw


def test_old_recovery_lookup_still_validates_the_tail(tmp_path):
    first = P.encode_journal_frame(b"open-recovery\x00retained intent")
    damaged = bytearray(P.encode_journal_frame(b"close"))
    damaged[-1] ^= 1
    fence = _fence(tmp_path, P.JOURNAL_FRAME_MAGIC_V1 + first + damaged)
    with fence.exclusive() as guard:
        with pytest.raises(GateDependencyUnavailable) as caught:
            fence.recovery_payload(1, guard=guard)
    assert caught.value.__cause__.failure_code is P.PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH
    reader = J.FileSnapshotFence(fence.directory, read_only=True)
    with pytest.raises(P.PersistenceViolation) as caught:
        reader.current_epoch()
    assert caught.value.failure_code is P.PersistenceFailureCode.JOURNAL_CHECKSUM_MISMATCH


def test_streaming_fence_keeps_frame_byte_limits(tmp_path):
    oversized = (P.MAX_JOURNAL_PAYLOAD_BYTES_V1 + 1).to_bytes(8, "big")
    fence = _fence(tmp_path, P.JOURNAL_FRAME_MAGIC_V1 + oversized)
    with pytest.raises(GateDependencyUnavailable) as caught:
        fence.current_epoch()
    assert caught.value.__cause__.failure_code is P.PersistenceFailureCode.RESOURCE_LIMIT_EXCEEDED


def test_read_only_missing_epoch_does_not_create_it(tmp_path):
    fence = J.FileSnapshotFence(tmp_path / "absent", read_only=True)
    with pytest.raises(P.PersistenceViolation):
        fence.current_epoch()
    assert not fence.directory.exists()


def test_bounded_iterator_still_refuses_an_over_budget_history(tmp_path):
    raw = P.JOURNAL_FRAME_MAGIC_V1 + P.encode_journal_frame(b"first") + P.encode_journal_frame(b"second")
    fence = _fence(tmp_path, raw)
    with pytest.raises(P.PersistenceViolation) as caught:
        list(P.iter_journal_frames(fence._epoch_path, maximum_frames=1))
    assert caught.value.failure_code is P.PersistenceFailureCode.JOURNAL_FRAME_LIMIT_EXCEEDED
    assert [frame.payload for frame in P.iter_journal_frames(fence._epoch_path, maximum_frames=None)] == [b"first", b"second"]


def test_abandoned_mutation_retains_recovery_bytes_through_reopen(tmp_path):
    fence = J.FileSnapshotFence(tmp_path / "coordinator")
    with pytest.raises(J.JournalAdapterViolation) as caught:
        with P.store_transaction(fence, recovery_payload=b"pending memory publication"):
            raise RuntimeError("acceptance process failure")
    assert caught.value.failure_code is J.JournalAdapterFailureCode.MUTATION_ABORTED
    reopened = J.FileSnapshotFence(fence.directory)
    with reopened.exclusive() as guard:
        assert reopened.recovery_payload(1, guard=guard) == b"pending memory publication"
        with reopened.resume_abandoned_interval(guard=guard, expected_epoch=1):
            pass
    assert reopened.current_epoch() == 2
    with pytest.raises(J.JournalAdapterViolation):
        reopened.recovery_payload(1, guard=guard)
