"""Heavy shard: exact migration frame reduction preserves the full worker view."""
import hashlib
import json

from acceptance.stage4.stage16._compact_frame_case import PRIVATE, migration_frame
from synapse.experiments.gold.memory_event_encoding import decode_tree_manifest
from synapse.experiments.gold.persistence import read_committed_snapshot_transaction
from synapse.experiments.gold.project_frame_storage import MAX_FRAME_PUBLICATION_BYTES, compact_frame, restore_frame
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.source_delivery import _memory_information
from synapse.experiments.gold.source_verification import canonical


def _retain_and_reopen(root, frame):
    root.mkdir()
    owner = ProjectMemoryStore(root)
    with owner.session() as guard:
        receipt = owner.put(kind="FRAME_COMPLETED", job_key="a" * 64,
            payload={"active_memory_frame": frame}, guard=guard)
    reader = ProjectMemoryStore(root, read_only=True)
    event = reader.read(receipt)
    logical = canonical(event)
    marker, members = read_committed_snapshot_transaction(reader.events, transaction_id=receipt["transaction_id"])
    assert set(members) == {"event-tree.json"}
    manifest = decode_tree_manifest(members["event-tree.json"])
    assert manifest["logical_sha256"] == receipt["ref"]["sha256"] == hashlib.sha256(logical).hexdigest()
    assert marker["marker_sha256"] == manifest["logical_sha256"]
    assert manifest["logical_byte_length"] == len(logical)
    assert canonical(event["payload"]["active_memory_frame"]) == canonical(frame)
    return event["payload"]["active_memory_frame"], {"physical_manifest_bytes": len(members["event-tree.json"]),
        "logical_event_bytes": len(logical), "retained_blocks": len(reader.storage.referenced_blocks(receipt["transaction_id"])),
        "fresh_reader_verified": True, "physical_representation": manifest["schema_version"]}


def test_migration_frame_reduces_retention_without_reducing_evidence(tmp_path):
    frame = migration_frame(paths=32, symbols=8, episodes=64)
    original = canonical(frame)
    compact = compact_frame(frame, max_bytes=MAX_FRAME_PUBLICATION_BYTES)
    encoded = canonical(compact)
    retained, physical = _retain_and_reopen(tmp_path / "state", compact)
    restored = restore_frame(retained, max_bytes=MAX_FRAME_PUBLICATION_BYTES)
    worker = canonical(_memory_information(frame))
    assert canonical(restored) == original
    assert canonical(_memory_information(restored)) == worker
    assert len(encoded) < len(original) // 4
    assert PRIVATE.encode() not in worker
    (tmp_path / "compact-frame-evidence.json").write_text(json.dumps({
        "scenario": "synthetic migration frame through actual builder, shared event publication, fresh reader and worker projection",
        "paths": 32, "symbols": 256, "episodes": 64, "partition_count": 1536, "nodes": 24,
        "expanded_bytes": len(original), "compact_bytes": len(encoded), "worker_bytes": len(worker),
        "frame_sha256": hashlib.sha256(original).hexdigest(), "worker_sha256": hashlib.sha256(worker).hexdigest(),
        "roundtrip_equal": True, "worker_bytes_equal": True, "physical_retention": physical,
        "limitation": "Shape and exact representation acceptance; these synthetic observations were not court-admitted."
    }, indent=2))
