"""Read authoritative outcomes of the storage-growth acceptance scenario."""
from __future__ import annotations

from synapse.durable_cognitive import read_cognitive_session
from synapse.memory_consolidation.configuration import read_memory_configuration
from synapse.memory_consolidation.factory import MemoryFactory
from synapse.memory_consolidation.records import canonical


def large_events(world):
    """Logical event bytes, independent of the physical storage representation."""
    sizes = {}
    for event, _ in world.journal():
        sizes[event["kind"]] = max(sizes.get(event["kind"], 0), len(canonical(event)))
    for kind in ("CONSOLIDATION_REPORTED", "SNAPSHOT_BOUNDARY", "MEMORY_STATE"):
        assert sizes.get(kind, 0) > 16 * 1024 * 1024, (kind, sizes)
    return sizes


def replay_without_effects(world, run_id):
    before = world.world()
    factory = MemoryFactory(world.state, read_memory_configuration(world.configuration_path))
    run = read_cognitive_session(world.runs / f"{run_id}.json")
    assert run["integrity_error"] is None
    cuts = [index for index, event in enumerate(run["history"]) if event["type"] == "session_consolidated"]
    assert len(cuts) == 1
    run["to"] = cuts[0]
    assert factory.ports().replay(run)["status"] == "replay_verified"
    assert world.world() == before


def searched(world, run_id):
    event, = world.events(run_id, "knowledge_searched")
    return {item["attribute"]: item for item in event["candidates"] if item["entity"] == "router-000"}
