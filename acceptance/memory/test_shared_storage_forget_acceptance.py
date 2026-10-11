"""A real storage evacuation retains independent experience across forget plus block GC."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from acceptance.memory.storage_audit.scenario import execute as investigate
from acceptance.memory.storage_evacuation.cluster import Cluster
from synapse.application import DurableRunRequest, execute_durable_run
from synapse.experiments.gold.memory_event_storage import MemoryEventStorage
from synapse.memory_consolidation import records
from synapse.memory_consolidation.configuration import read_memory_configuration
from synapse.memory_consolidation.court.custody import reproduce_session, reproduces
from synapse.memory_consolidation.factory import MemoryFactory

REPOSITORY = Path(__file__).resolve().parents[2]
MAINTENANCE = '''memory palace "storage-engineering" { rooms { episodic procedural } consolidate during dream }
consolidate palace
print("maintained")'''


def _case(state, run_id):
    matches = [entry for entry in state["quanta"].values()
               if entry.get("replay_ref", {}).get("run_id") == run_id]
    assert len(matches) == 1
    return matches[0]


def _recorded_events(store):
    return {receipt["transaction_id"]: {"receipt": receipt, "sha256": records.digest(event)}
            for event, receipt in store.inventory()}


def _forget(root, qid):
    completed = subprocess.run([sys.executable, "-B", "-m", "synapse", "memory", "forget",
        "--project-state", str(root / "memory"), "--memory-config", str(root / "configuration.json"),
        "--quantum", qid, "--reason", "retire the obsolete pre-failure observation",
        "--operator", "storage-acceptance.operator"], cwd=REPOSITORY, text=True,
        capture_output=True, timeout=300)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)
    assert result["status"] == "RECORDED"
    return result["acts"]


def _consolidate(root, factory):
    program = root / "apply-forget.syn"
    program.write_text(MAINTENANCE, encoding="utf-8")
    result = execute_durable_run(DurableRunRequest(program, root / "runs",
        run_id="apply-storage-forget", memory=factory))
    assert result.exit_code == 0 and result.status == "COMPLETED", result


def _assert_reproduces(factory, entry):
    replay = entry["replay_ref"]
    ports = factory.ports()
    result = reproduce_session(ports.reproduce, ports.gateway.evidence, replay["data_ref"])
    assert result["status"] == "reproduced", result
    assert reproduces(result["history"], replay["positions"], entry["canonical"]["trace_ref"])


def execute(root, *, corpus=None):
    """Check the operator boundary on an authentic 1536-shard history."""
    audit = investigate(root, corpus=corpus)
    transport = Cluster()
    factory = MemoryFactory(root / "memory", read_memory_configuration(root / "configuration.json"),
                            transport=transport)
    state = factory.owner.state()
    forgotten, retained = _case(state, "survey-original"), _case(state, "survey-corrected")
    before = _recorded_events(factory.owner.store)
    storage = MemoryEventStorage(factory.owner.store.root)
    live_blocks = set().union(*(storage.referenced_blocks(transaction) for transaction in before))
    assert live_blocks, "forget must run over real shared journal payloads"
    _assert_reproduces(factory, retained)
    acts = _forget(root, forgotten["qid"])
    assert forgotten["qid"] in {act["qid"] for act in acts}
    assert retained["qid"] not in {act["qid"] for act in acts}
    _consolidate(root, factory)
    with factory.owner.store.session() as guard:
        collected = factory.owner.store.collect_unreferenced_blocks(guard=guard)
    after = factory.owner.state()
    assert after["quanta"][forgotten["qid"]]["retention_state"] == "forgotten"
    assert after["quanta"][retained["qid"]]["retention_state"] == retained["retention_state"]
    evidence = factory.gateway.evidence
    assert all(evidence.get(ref) is None for act in acts for ref in act["refs"])
    assert all(evidence.get(ref) is not None for ref in retained["evidence_refs"])
    for saved in before.values():
        assert records.digest(factory.owner.store.read(saved["receipt"])) == saved["sha256"]
    for digest in live_blocks:
        path = factory.owner.store.root / "blocks" / (digest + ".bin")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    _assert_reproduces(factory, retained)
    assert transport.calls == 0
    result = {"knowledge_versions_before_forget": audit["knowledge_versions"], "acts": len(acts),
              "historical_receipts_preserved": len(before), "shared_blocks_preserved": len(live_blocks),
              "retained_replay_verified": True, "transport_calls": transport.calls, "gc": collected}
    (root / "forget-acceptance.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def test_forgetting_original_storage_observation_keeps_corrected_evidence_and_historical_receipts(tmp_path):
    result = execute(tmp_path / "shared-storage-forget")
    assert result["knowledge_versions_before_forget"] == 3168
    assert result["retained_replay_verified"] and result["shared_blocks_preserved"] > 0
