"""A retained million-frame coordinator still recovers into real owner work."""
import hashlib
import os

from synapse.experiments.gold.admission_journal import FENCE_EPOCH_JOURNAL_NAME
from synapse.experiments.gold.persistence import JOURNAL_FRAME_MAGIC_V1, encode_journal_frame
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore


def _retained_coordinator_history(fence):
    """Write an archived complete-frame history, ending at an abandoned opening."""
    old = encode_journal_frame(b"open-recovery\x00oldest retained intent")
    opened = encode_journal_frame(b"open" + b"o" * 16)
    closed = encode_journal_frame(b"close" + b"c" * 16)
    tail = encode_journal_frame(b"open-recovery\x00abandoned latest intent")
    path = fence.directory / FENCE_EPOCH_JOURNAL_NAME
    with fence.exclusive():
        with path.open("wb") as stream:
            stream.write(JOURNAL_FRAME_MAGIC_V1 + old + closed)
            group = (opened + closed) * 1000
            for _ in range(499):
                stream.write(group)
            stream.write((opened + closed) * 999 + tail)
            stream.flush()
            os.fsync(stream.fileno())
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def test_owner_recovers_past_one_million_epochs_without_losing_old_history(tmp_path):
    store = ProjectMemoryStore(tmp_path)
    path, digest, prefix_bytes = _retained_coordinator_history(store.fence)
    with store.fence.exclusive() as guard:
        assert store.fence.recovery_payload(1, guard=guard) == b"oldest retained intent"
        assert store.fence.recovery_payload(1_000_001, guard=guard) == b"abandoned latest intent"
    # The owner must validate its inventory before closing the abandoned epoch.
    with store.session() as guard:
        receipt = store.put(kind="REQUESTED", job_key="a" * 64,
                            payload={"operation": "continue retained memory"}, guard=guard)
    reader = ProjectMemoryStore(tmp_path, read_only=True)
    assert reader.fence.current_epoch() == 1_000_004
    assert reader.read(receipt)["payload"] == {"operation": "continue retained memory"}
    with path.open("rb") as stream:
        assert hashlib.sha256(stream.read(prefix_bytes)).hexdigest() == digest
    with store.fence.exclusive() as guard:
        assert store.fence.recovery_payload(1, guard=guard) == b"oldest retained intent"
