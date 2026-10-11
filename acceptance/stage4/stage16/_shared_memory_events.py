"""Build physical journal fixtures for shared-event custody acceptance."""
from copy import deepcopy
import hashlib
import json

from synapse.experiments.gold import project_memory_store as M
from synapse.experiments.gold.persistence import read_committed_snapshot_transaction


def payload(*, source="observer-a", generation=7):
    return {
        "origin": {"source": source, "observed_at": "2026-10-09T09:30:00Z", "generation": generation},
        "versions": [{"subject": {"cluster": "storage-evacuation", "node": index},
            "statement": {"free": index % 31, "capacity": 256, "failure_domain": index % 6},
            "provenance": {"source": "inventory-service", "receipt": f"observation-{index:04d}"},
            "conditions": {"generation": 7, "replicas": 3, "placement": [index, index + 1, index + 2]},
            "description": "storage placement constraint " * 32} for index in range(256)],
    }


def event(*, kind="SNAPSHOT_BOUNDARY", job="a", body=None):
    return {"schema_version": M.MEMORY_EVENT_V1, "kind": kind, "job_key": job * 64,
            "payload": payload() if body is None else deepcopy(body)}


def put(store, guard, value):
    return store.put(kind=value["kind"], job_key=value["job_key"], payload=value["payload"], guard=guard)


def commit_members(store, guard, members, *, logical, job_key):
    transaction = M.new_operation_id()
    with M.store_transaction(store.fence, guard=guard) as ticket:
        staged = M.stage_snapshot_transaction(store.events, transaction_id=transaction, members=members,
                                             maximum_bytes=M.MAX_MEMORY_EVENT_BYTES, ticket=ticket)
        M.commit_snapshot_transaction(store.events, transaction_id=transaction, members=staged,
            boundary_id=job_key, marker_sha256=hashlib.sha256(logical).hexdigest(), ticket=ticket)
    return {"transaction_id": transaction, "ref": M.source_ref(logical, M.MEMORY_EVENT_V1).to_dict()}


def commit_legacy(store, guard, value, *, raw=None):
    raw = M.canonical(value) if raw is None else raw
    return commit_members(store, guard, {"event.json": raw}, logical=raw, job_key=value["job_key"])


def manifest(store, receipt):
    _, members = read_committed_snapshot_transaction(store.events,
                                                    transaction_id=receipt["transaction_id"])
    assert set(members) == {"event-manifest.json"}, "large selected event must use the shared physical form"
    return json.loads(members["event-manifest.json"])


def blocks(store, receipt):
    return {store.root / "blocks" / (chunk["sha256"] + ".bin")
            for chunk in manifest(store, receipt)["chunks"]}


def physical_files(root):
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
