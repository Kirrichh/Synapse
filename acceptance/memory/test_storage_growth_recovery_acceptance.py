"""A large committed memory generation recovers after projection publication dies."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from acceptance.memory._world import REPOSITORY
from acceptance.memory.storage_growth import cluster, programs
from acceptance.memory.storage_growth.observations import replay_without_effects, searched
from synapse.memory_consolidation.records import canonical


def _interrupt(world, source, point):
    program, inputs = world.root / "interrupted.syn", world.root / "interrupted.input.json"
    program.write_text(source)
    inputs.write_text(json.dumps({}))
    result = subprocess.run([sys.executable, "-B", "-m", "acceptance.memory.storage_growth.crash", str(world.state),
                             str(world.configuration_path), str(program), str(world.runs), "interrupted", str(inputs),
                             point], cwd=REPOSITORY, env={**os.environ, "PYTHONPATH": str(REPOSITORY)},
                            capture_output=True, text=True, timeout=900)
    assert result.returncode == 9, (result.returncode, result.stdout, result.stderr)


@pytest.mark.parametrize("point", ["boundary", "state"])
def test_large_applied_generation_reopens_after_publication_crash(tmp_path, point):
    world = cluster.world(tmp_path)
    source = programs.SURVEY
    if point == "state":
        world.run(programs.SURVEY, "survey", {})
        for index in range(6):
            world.run(programs.CHECKPOINT, f"continued-{index}", {})
        source = programs.CHECKPOINT
    _interrupt(world, source, point)
    reports = world.reports()
    assert len(reports) == (8 if point == "state" else 1)
    head = reports[-1]
    assert head["consolidation_id"] not in world.owner().boundaries()
    before = world.owner().state()
    assert len(before["knowledge"]["versions"]) == cluster.NODES + 1
    assert len(canonical(before)) > 16 * 1024 * 1024
    calls = world.world()

    world.run(programs.RECALL, "reopened", {"options": {"channels": ["lexical"]}})
    assert world.opening("reopened")["boundary"] == head["snapshot_boundary_after"]
    assert searched(world, "reopened")["configuration"]["value"] == cluster.configuration(0)
    assert searched(world, "reopened")["epoch"]["value"] == 7
    assert len(world.world()["calls"]) == len(calls["calls"]) + 1
    assert world.world()["effects"] == calls["effects"] == []
    assert world.calls("routing_snapshot") == [{}]
    assert world.owner().state()["knowledge"] == before["knowledge"]
    boundaries = world.owner().boundaries()
    assert boundaries[head["consolidation_id"]]["id"] == head["snapshot_boundary_after"]
    assert [report["consolidation_id"] for report in world.reports()].count(head["consolidation_id"]) == 1
    replay_without_effects(world, "reopened")
    world.run(programs.CHECKPOINT, "continued-again", {})
    assert world.owner().state()["knowledge"] == before["knowledge"]
    assert len(world.reports()) == len(reports) + 2
