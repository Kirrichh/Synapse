"""Large authentic memory remains usable through the public runtime."""
from __future__ import annotations

from acceptance.memory.storage_growth import cluster, programs
from acceptance.memory.storage_growth.observations import large_events, replay_without_effects, searched


def test_large_memory_continues_without_weakening_action_admission(tmp_path):
    world = cluster.world(tmp_path)
    world.run(programs.SURVEY, "survey", {})
    assert len(world.owner().state()["knowledge"]["versions"]) == cluster.NODES + 1
    for index in range(7):
        world.run(programs.CHECKPOINT, f"continued-{index}", {})
    large_events(world)
    world.run(programs.RECALL, "before", {"options": {"channels": ["lexical"]}})
    assert searched(world, "before")["configuration"]["value"] == cluster.configuration(0)
    assert searched(world, "before")["epoch"]["value"] == 7

    cluster.publish_epoch(world, 8)
    world.run(programs.CORRECT, "corrected", {})
    corrected = world.reports()[-1]
    assert len(corrected["knowledge"]["corrections"]) == 1
    world.run(programs.ACTION, "stale", {"task_name": "stale", "wanted_epoch": 7})
    assert world.calls("activate_routes") == [] and world.world()["effects"] == []
    refused, = [event["outcome"]["view"] for event in world.events("stale", "external_action")
                if event["request"]["tool"] == "activate_routes"]
    assert refused["op_result"] == "rejected" and refused["effect"] == "none"
    assert "not established" in refused["reason"]
    world.run(programs.ACTION, "replanned", {"task_name": "replanned", "wanted_epoch": 8})
    assert world.calls("activate_routes") == [{"node": "router-000", "expected_epoch": 8}]
    assert len(world.world()["effects"]) == 1

    world.run(programs.RECALL, "current", {"options": {"channels": ["lexical"]}})
    assert searched(world, "current")["epoch"]["value"] == 8
    world.run(programs.RECALL, "historical", {"options": {"channels": ["lexical"], "known_as_of": 1}})
    assert searched(world, "historical")["epoch"]["value"] == 7
    assert len(world.owner().state()["knowledge"]["versions"]) == cluster.NODES + 2
    for run_id in ("survey", "stale", "replanned", "historical"):
        replay_without_effects(world, run_id)
