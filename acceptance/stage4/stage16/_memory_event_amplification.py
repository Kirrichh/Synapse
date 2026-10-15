"""Construct physical repeated-block trees for reconstruction acceptance."""
from __future__ import annotations

import hashlib
import json

from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.memory_event_encoding import MEMORY_EVENT_INDEX_V2, MEMORY_EVENT_TREE_V2
from synapse.experiments.gold.memory_event_storage import MemoryEventStorage


def retain(raw, logical, height, blocks):
    digest = hashlib.sha256(raw).hexdigest()
    blocks[digest] = raw
    return {"sha256": digest, "byte_length": len(raw),
            "logical_byte_length": logical, "height": height}


def tree(leaf, *, fanout=256, height=2):
    blocks = {}
    descriptor = retain(leaf, len(leaf), 0, blocks)
    for level in range(1, height + 1):
        raw = M.canonical({"schema_version": MEMORY_EVENT_INDEX_V2, "children": [descriptor] * fanout})
        descriptor = retain(raw, descriptor["logical_byte_length"] * fanout, level, blocks)
    return descriptor, blocks


def committed_tree(state_root, descriptor, blocks):
    manifest = {"schema_version": MEMORY_EVENT_TREE_V2, "logical_sha256": "0" * 64,
                "logical_byte_length": descriptor["logical_byte_length"], "root": descriptor}
    store = M.ProjectMemoryStore(state_root)
    with store.session() as guard:
        with M.store_transaction(store.fence, guard=guard) as ticket:
            store.storage.blocks.publish(blocks, ticket=ticket)
            transaction = M.new_operation_id()
            members = M.stage_snapshot_transaction(store.events, transaction_id=transaction,
                members={"event-tree.json": M.canonical(manifest)}, maximum_bytes=65536, ticket=ticket)
            M.commit_snapshot_transaction(store.events, transaction_id=transaction, members=members,
                boundary_id="a" * 64, marker_sha256="0" * 64, ticket=ticket)
    return store, transaction


def bounded_read(state_root, transaction, entry):
    import resource
    import time

    maximum = 384 * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (maximum, maximum))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    started = time.monotonic()
    store = M.ProjectMemoryStore(state_root)
    try:
        if entry == "owner_session":
            with store.session():
                pass
        elif entry == "referenced_blocks":
            MemoryEventStorage(store.root).referenced_blocks(transaction)
        else:
            store.storage.read(transaction)
    except (ValueError, MemoryError) as failure:
        result = {"exception": type(failure).__name__, "detail": str(failure)}
    else:
        result = {"exception": None}
    result.update(elapsed_seconds=time.monotonic() - started,
                  peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    print(json.dumps(result))
