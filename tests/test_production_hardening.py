import os
import tempfile

from synapse import Interpreter, SQLiteStorage, InMemoryStorage, RuntimeStressHarness, compile_to_ast
from synapsed import SwarmNodeDaemon


def test_in_memory_storage_roundtrip_and_history_hash():
    interp = Interpreter().attach_storage(InMemoryStorage(), run_id="r1")
    interp.execution_history.append({"type": "message_sent", "message": {"payload": "x"}})
    chain = interp.history_hash_chain()
    assert interp.verify_history_chain(chain)
    saved = interp.save_runtime_state()
    assert saved["status"] == "saved"

    restored = Interpreter().attach_storage(interp.storage_backend, run_id="r1")
    state = restored.load_runtime_state()
    assert state["history_hash"] == interp.compute_history_hash()


def test_sqlite_storage_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "synapse.db")
        storage = SQLiteStorage(db)
        interp = Interpreter().attach_storage(storage, run_id="sqlite-run")
        interp.execution_history.extend([
            {"type": "llm_call", "prompt_hash": "a", "result": "b"},
            {"type": "identity_integrated", "fracture_id": "f1"},
        ])
        interp.save_runtime_state()
        count = interp.append_runtime_events()
        assert count == 2
        assert "sqlite-run" in storage.list_runs()
        loaded = storage.load_state("sqlite-run")
        assert loaded["history_hash"] == interp.compute_history_hash()
        assert len(storage.load_events("sqlite-run")) == 2


def test_metrics_snapshot_and_prometheus_text():
    interp = Interpreter()
    interp.execution_history.extend([
        {"type": "identity_fractured", "fracture_id": "f"},
        {"type": "subagent_terminated", "death_type": "NATURAL"},
        {"type": "resonance_profile_computed", "profile": {"drift_vector": {"urgency": 0.2}}},
    ])
    metrics = interp.metrics_snapshot()
    assert metrics["events_total"] == 3
    assert metrics["fractures_total"] == 1
    assert metrics["subagent_death_counts"]["NATURAL"] == 1
    text = interp.metrics_text()
    assert "synapse_events_total" in text
    assert "synapse_event_count" in text


def test_stress_harness_detects_event_mutation():
    interp = Interpreter()
    interp.execution_history.extend([{"type": "a"}, {"type": "b"}, {"type": "c"}])
    result = RuntimeStressHarness(seed=1).run_integrity_scenarios(interp.execution_history)
    assert result["baseline_valid"] is True
    assert result["drop_detected"] is True
    assert result["duplicate_detected"] is True


def test_swarm_daemon_metrics_packet():
    daemon = SwarmNodeDaemon(node_id="node-test")
    interp = Interpreter()
    interp.execution_history.append({"type": "message_sent"})
    daemon.local_actors["Guide"] = interp
    response = daemon.collect_metrics({"type": "metrics", "actor_name": "Guide"})
    assert response["metrics"]["events_total"] == 1
    all_response = daemon.collect_metrics({"type": "metrics"})
    assert "Guide" in all_response["actors"]


def test_source_program_still_runs_after_hardening():
    source = 'print("ok")'
    interp = Interpreter()
    interp.interpret(compile_to_ast(source))
    assert interp.get_output() == "ok"


if __name__ == "__main__":
    test_in_memory_storage_roundtrip_and_history_hash()
    test_sqlite_storage_roundtrip()
    test_metrics_snapshot_and_prometheus_text()
    test_stress_harness_detects_event_mutation()
    test_swarm_daemon_metrics_packet()
    test_source_program_still_runs_after_hardening()
    print("All production hardening tests passed!")
