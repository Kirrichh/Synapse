"""Continue a retained project journal past its former lifetime event ceiling."""
import hashlib

from synapse.experiments.gold.project_memory_store import ProjectMemoryStore


def _request(index):
    return {"kind": "REQUESTED", "job_key": hashlib.sha256(f"maintenance-{index}".encode()).hexdigest(),
            "payload": {"partition": index % 1536, "maintenance_epoch": index // 1536,
                        "operation": "verify replica placement before certificate rotation"}}


def test_journal_continues_after_4096_events_without_losing_old_receipts_or_repeating_work(tmp_path):
    store = ProjectMemoryStore(tmp_path)
    with store.session() as guard:
        receipts = [store.put(**_request(index), guard=guard) for index in range(4098)]
        assert store.put(**_request(0), guard=guard) == receipts[0]
        assert store.put(**_request(4097), guard=guard) == receipts[-1]
    reader = ProjectMemoryStore(tmp_path, read_only=True)
    observed = reader.inventory(kind="REQUESTED")
    assert {event["job_key"]: receipt for event, receipt in observed} == {
        _request(index)["job_key"]: receipt for index, receipt in enumerate(receipts)}
    for index in (0, 4095, 4096, 4097):
        assert reader.read(receipts[index])["payload"] == _request(index)["payload"]
    reopened = ProjectMemoryStore(tmp_path)
    with reopened.session() as guard:
        completed = reopened.put(kind="FRAME_COMPLETED", job_key=_request(0)["job_key"],
                                 payload={"request": receipts[0], "reviewed_partitions": [0, 512, 1024]},
                                 guard=guard)
        assert reopened.put(**_request(0), guard=guard) == receipts[0]
    following = ProjectMemoryStore(tmp_path, read_only=True)
    assert following.read(completed)["payload"]["request"] == receipts[0]
    assert following.read(receipts[0])["payload"] == _request(0)["payload"]
    assert len(following.inventory()) == 4099
