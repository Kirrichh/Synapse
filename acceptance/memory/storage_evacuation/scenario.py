"""Execute a durable storage evacuation acceptance scenario."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

from synapse.application import DurableRunRequest, execute_durable_run
from synapse.durable_cognitive import read_cognitive_session
from synapse.experiments.gold.knowledge_environment import (
    ConnectProjectRequest, execute_connect_project,
)
from synapse.memory_consolidation.configuration import parse_memory_configuration
from synapse.memory_consolidation.factory import MemoryFactory

from .cluster import Cluster

PHASES = (
    ("survey-original", "survey.syn", "2026-10-09T08:00:00Z"),
    ("evacuate-original", "evacuate.syn", "2026-10-09T08:30:00Z"),
    ("survey-corrected", "survey.syn", "2026-10-09T09:00:00Z"),
    ("evacuate-replanned", "evacuate.syn", "2026-10-09T09:30:00Z"),
)


def configuration():
    tools = []
    for name in ("topology_read", "plan_review", "migrate_batch"):
        contract = {"idempotent": True, "observation": True}
        if name == "migrate_batch":
            contract = {"idempotent": False, "effect_on_err": {"STALE_TOPOLOGY": "none"}}
        tools.append({"name": name, "server": "storage",
                      "descriptor_sha256": hashlib.sha256(name.encode()).hexdigest(),
                      "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
                      "source": "storage:engineering-cluster", "contract": contract})
    return parse_memory_configuration({
        "schema_version": "synapse.memory.configuration/v2",
        "tools": {"schema_version": "synapse.memory.tool-configuration/v2",
                  "servers": [{"id": "storage", "argv": ["own-local-cluster"]}],
                  "tools": tools,
                  "provenance": {"storage:engineering-cluster": {"ancestors": []}}},
        "court": {"decision_rule": "threshold", "parameters": {}},
        "advisor": None, "scorer": None, "element": "storage-engineering",
        "knowledge": {"embedder": None, "properties": {
            "free": {"freshness": "state"}, "alive": {"freshness": "state"},
            "placement": {"freshness": "state"}}, "budget": 100},
    })


def prepare_project(root, shard_count):
    root.mkdir(parents=True, exist_ok=False)
    source = root / "task-project"
    source.mkdir()
    (source / "storage-model.txt").write_text(
        f"24 nodes, 6 racks, {shard_count} shards with 3 replicas; drain nodes 0..3; "
        "preserve 2 live copies, distinct racks, capacities throughout every migration.\n"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "storage-model.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Storage Acceptance",
                    "-c", "user.email=acceptance@local.invalid", "commit", "-qm",
                    "Storage evacuation scenario"], check=True)
    identities = ("platform_attester_actor", "builder_actor", "taint_classifier_authority",
                  "taint_reviewer_authority", "supersession_reviewer_authority",
                  "revocation_reviewer_authority", "lifecycle_writer_actor")
    declaration = {"policy_version": "storage-acceptance/v1",
                   "environment_profile_id": "local-storage",
                   "identities": {key: "storage-acceptance-" + key.replace("_", "-")
                                  for key in identities}}
    path = root / "declaration.json"
    path.write_text(json.dumps(declaration))
    result = execute_connect_project(ConnectProjectRequest(source, root / "memory", path))
    assert result.exit_code == 0, result
    (root / "runs").mkdir()


def run_phase(root, factory, phase):
    name, template, date = phase
    source = (Path(__file__).parent / template).read_text().replace("DATE", date)
    path = root / (name + ".syn")
    path.write_text(source)
    started, cpu = time.perf_counter(), time.process_time()
    result = execute_durable_run(DurableRunRequest(
        path, root / "runs", run_id=name, memory=factory,
    ))
    measurement = {"name": name, "wall_s": time.perf_counter() - started,
                   "cpu_s": time.process_time() - cpu, "status": result.status,
                   "exit_code": result.exit_code}
    assert result.exit_code == 0, result
    (root / (name + "-result.json")).write_text(json.dumps({
        "measurement": measurement, "public": result.public_payload,
    }, indent=2))
    print(json.dumps(measurement), flush=True)
    return measurement


def replay_phases(root, factory, cluster):
    before = cluster.calls
    snapshot = json.dumps({"nodes": cluster.nodes, "shards": cluster.shards,
                           "applied": cluster.applied, "refusals": cluster.refusals})
    rows = []
    for name, _, _ in PHASES:
        session = read_cognitive_session(root / "runs" / (name + ".json"))
        assert session["integrity_error"] is None
        session["to"] = next(index for index, event in enumerate(session["history"])
                             if event["type"] == "session_consolidated")
        started, cpu = time.perf_counter(), time.process_time()
        result = factory.ports().replay(session)
        row = {"name": name, "wall_s": time.perf_counter() - started,
               "cpu_s": time.process_time() - cpu, "result": result}
        assert result["status"] == "replay_verified", result
        assert cluster.calls == before
        rows.append(row)
        print(json.dumps({"replay": row}), flush=True)
    assert snapshot == json.dumps({"nodes": cluster.nodes, "shards": cluster.shards,
                                  "applied": cluster.applied, "refusals": cluster.refusals})
    return rows


def inspect_retained(root, factory):
    searches = {}
    for name, _, _ in PHASES:
        artifact = json.loads((root / "runs" / (name + ".json")).read_text())
        history = artifact["replay_state"]["execution_history"]
        searches[name] = [event for event in history if event["type"] == "knowledge_searched"]
        assert sum(event["type"] == "session_consolidated" for event in history) == 1
    for name in ("evacuate-original", "evacuate-replanned"):
        assert len(searches[name]) == 1
        assert len(searches[name][0]["candidates"]) == 48
    corrected = searches["evacuate-replanned"][0]["candidates"]
    node12 = [fact for fact in corrected
              if fact["entity"]["id"] == 12 and fact["attribute"] == "alive"]
    assert len(node12) == 1 and node12[0]["value"] is False
    node5 = next(fact for fact in corrected
                 if fact["entity"]["id"] == 5 and fact["attribute"] == "free")
    assert node5["value"] == 8
    state = factory.owner.state()
    return {"knowledge_versions": len(state["knowledge"]["versions"]),
            "court_windows": state["window"], "corrected_node12_recalled_as_down": True,
            "corrected_node5_free_space": node5["value"], "recalled_candidates": {
                name: len(events[0]["candidates"]) for name, events in searches.items() if events}}


def validate_completion(cluster):
    assert cluster.validate(cluster.nodes, cluster.shards) == 3
    assert all(not cluster.nodes[node]["drain"] and cluster.nodes[node]["alive"]
               for shard in cluster.shards for node in shard["replicas"])
    assert len(cluster.refusals) == 1 and cluster.refusals[0]["applied"] == 0
    assert len(cluster.reviews) == 2
    return {"transport_calls": cluster.calls, "planned": cluster.reviews,
            "applied_moves": len(cluster.applied),
            "copied_units": sum(move["size"] for move in cluster.applied),
            "disturbance": cluster.disturbance, "stale_plan_refusals": cluster.refusals,
            "all_applied_steps_independently_checked": True, "final_minimum_live_copies": 3,
            "all_shards_distinct_racks": True, "all_node_capacities_respected": True,
            "drain_and_failed_nodes_empty": True}


def execute(root, shard_count=1536):
    root = Path(root)
    assert shard_count >= 384 and shard_count % 24 == 0
    prepare_project(root, shard_count)
    config, cluster = configuration(), Cluster(shard_count)
    (root / "configuration.json").write_text(json.dumps(config.raw, indent=2))
    factory = MemoryFactory(root / "memory", config, transport=cluster)
    started = time.perf_counter()
    rows = [run_phase(root, factory, phase) for phase in PHASES]
    total_wall = time.perf_counter() - started
    result = {"shards": shard_count, "rows": rows,
              "execution_with_consolidation_wall_s": total_wall,
              **validate_completion(cluster), **inspect_retained(root, factory)}
    assert result["knowledge_versions"] == 2 * (shard_count + 48)
    assert result["court_windows"] == 4
    result["replay"] = replay_phases(root, factory, cluster)
    result["replay_transport_calls"] = cluster.calls - result["transport_calls"]
    (root / "move-ledger.json").write_text(json.dumps(cluster.applied, indent=2))
    (root / "final-cluster.json").write_text(json.dumps({
        "nodes": cluster.nodes, "shards": cluster.shards,
    }, indent=2))
    (root / "summary.json").write_text(json.dumps(result, indent=2))
    return result
