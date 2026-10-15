"""Measure detached owner reads without changing retained evidence."""
from __future__ import annotations

from collections import Counter
import hashlib
import time


def fingerprint(value):
    from synapse.memory_consolidation.records import canonical

    raw = canonical(value)
    return {"canonical_sha256": hashlib.sha256(raw).hexdigest(), "canonical_bytes": len(raw)}


def timed(call):
    wall, cpu = time.perf_counter(), time.process_time()
    result = call()
    return result, {"wall_s": time.perf_counter() - wall, "cpu_s": time.process_time() - cpu}


def read_pair(state_root, operation):
    from synapse.memory_consolidation.owner import MemoryOwner

    owner, construction = timed(lambda: MemoryOwner(state_root, read_only=True))
    first, fresh = timed(lambda: operation(owner))
    second, reused = timed(lambda: operation(owner))
    assert first == second, "Fresh and reused owner reads differ"
    return first, {"owner_construction": construction, "fresh_reader": fresh,
                   "reused_reader": reused, "exact_values_equal": True, **fingerprint(first)}


def inspect_dataset(dataset):
    from synapse.memory_consolidation.owner import MemoryOwner

    state_root = dataset / "memory"
    configuration, config_reads = read_pair(state_root, lambda owner: owner.bound_configuration().raw)
    state, state_reads = read_pair(state_root, lambda owner: owner.state())
    inventory = MemoryOwner(state_root, read_only=True).store.inventory()
    ordered = sorted(inventory, key=lambda item: (item[0]["kind"], item[0]["job_key"]))
    return {"dataset": str(dataset), "configuration": config_reads, "state": state_reads,
            "window": state["window"], "knowledge_versions": len(state["knowledge"]["versions"]),
            "knowledge": fingerprint(state["knowledge"]),
            "logical_inventory_with_receipts": fingerprint([
                {"event": event, "receipt": receipt} for event, receipt in ordered]),
            "logical_events_without_receipts": fingerprint([item[0] for item in ordered]),
            "event_kinds": dict(Counter(event["kind"] for event, _ in inventory)),
            "scope": "Owner API reads; fresh/reused describes reader objects, not OS cache. "
                     "Hashes retain every field; no provenance, identity or receipt is normalized away."}
