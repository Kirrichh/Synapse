"""Alpha3f P5: Golden Artifact -> TraceContext bridge tests.

Covers GoldenArtifactTraceAdapter (real artifact loading) and
ForkRegistry.create_fork_from_artifact (forensic-trail fork anchoring).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse.debugger_core import (
    GoldenArtifactTraceAdapter,
    ForkRegistry,
    REPLAY_DETERMINISTIC,
    REPLAY_EXPLORATORY_LIVE,
    TraceContextProtocol,
)
from synapse.golden_replay import (
    DeterministicReplayError,
    ReplayArtifactError,
    record_source,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def golden_artifact(tmp_path) -> Path:
    """Record a real golden artifact from a program that emits durable events.

    The program uses memory palace imprint/recall/consolidate which write
    durable history events (memory_imprinted, memory_recalled, etc.), giving a
    non-empty final_history_hash needed for forensic fork anchoring. A pure
    arithmetic program would record zero durable events and could not be forked.
    """
    source = (
        'memory palace "AgentMemory" {\n'
        '    rooms { episodic semantic procedural }\n'
        '    backend sqlite\n'
        '    bind palace\n'
        '}\n'
        '\n'
        'imprint into palace.episodic {\n'
        '    content "Production migration failed at step 3"\n'
        '    confidence 0.99\n'
        '    source "plan_weave_execution"\n'
        '    bind imprint_id\n'
        '}\n'
        '\n'
        'recall from palace.episodic {\n'
        '    query "migration"\n'
        '    limit 3\n'
        '    bind found\n'
        '}\n'
        '\n'
        'print(imprint_id)\n'
    )
    out = tmp_path / "artifact"
    record_source(source, out, source_path="test.syn", layer="strict")
    return out


# ---------------------------------------------------------------------------
# 1. Valid artifact loading
# ---------------------------------------------------------------------------

def test_adapter_loads_valid_artifact(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    assert adapter.cursor == 0
    assert adapter.artifact_dir == str(golden_artifact)
    assert adapter.program_hash.startswith("sha256:")
    assert isinstance(adapter.execution_history, tuple)


def test_adapter_implements_protocol(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    assert isinstance(adapter, TraceContextProtocol)


def test_adapter_cursor_walks_real_history(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    # Walk the entire recorded history via the cursor
    count = 0
    while adapter.next_expected_event() is not None:
        adapter.consume_expected_event()
        count += 1
    assert count == len(adapter.execution_history)
    assert adapter.next_expected_event() is None


def test_adapter_does_not_mutate_artifact_file(golden_artifact):
    history_path = golden_artifact / "history.json"
    before = history_path.read_text(encoding="utf-8")

    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    while adapter.next_expected_event() is not None:
        adapter.consume_expected_event()

    after = history_path.read_text(encoding="utf-8")
    assert before == after, "adapter must never modify the on-disk artifact"


def test_adapter_history_is_immutable_tuple(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    assert isinstance(adapter.execution_history, tuple)
    # Mutating a returned event dict must not affect the adapter's internal copy
    if adapter.execution_history:
        first = adapter.next_expected_event()
        # event is a plain dict copy; mutate it
        if isinstance(first, dict):
            first["__injected__"] = "tampered"
        # internal tuple element should be unaffected on next read after reset
        # (cursor hasn't advanced, so re-read same index)
        again = adapter.next_expected_event()
        assert "__injected__" not in again


# ---------------------------------------------------------------------------
# 2. EOF behavior
# ---------------------------------------------------------------------------

def test_adapter_eof_next_returns_none(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    while adapter.next_expected_event() is not None:
        adapter.consume_expected_event()
    assert adapter.next_expected_event() is None


def test_adapter_eof_consume_raises(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    while adapter.next_expected_event() is not None:
        adapter.consume_expected_event()
    with pytest.raises(DeterministicReplayError, match="end of golden artifact trace"):
        adapter.consume_expected_event()


# ---------------------------------------------------------------------------
# 3. Fail-fast on bad artifacts
# ---------------------------------------------------------------------------

def test_missing_directory_raises_artifact_error(tmp_path):
    with pytest.raises(ReplayArtifactError, match="artifact directory not found"):
        GoldenArtifactTraceAdapter(tmp_path / "does_not_exist")


def test_missing_manifest_raises_artifact_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReplayArtifactError, match="missing manifest.json"):
        GoldenArtifactTraceAdapter(empty)


def test_missing_history_raises_artifact_error(golden_artifact):
    # Remove history.json but keep manifest
    (golden_artifact / "history.json").unlink()
    with pytest.raises(ReplayArtifactError, match="missing history file"):
        GoldenArtifactTraceAdapter(golden_artifact)


def test_malformed_history_raises_artifact_error(golden_artifact):
    # Replace history with a non-list
    (golden_artifact / "history.json").write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ReplayArtifactError, match="must be a JSON array"):
        GoldenArtifactTraceAdapter(golden_artifact)


def test_malformed_event_raises_artifact_error(golden_artifact):
    # History is a list but contains a non-object element
    (golden_artifact / "history.json").write_text('[{"type": "ok"}, 42]', encoding="utf-8")
    with pytest.raises(ReplayArtifactError, match="malformed history event"):
        GoldenArtifactTraceAdapter(golden_artifact)


def test_broken_chain_raises_deterministic_error(golden_artifact):
    # Tamper with history so the chain no longer matches final_history_hash.
    history = json.loads((golden_artifact / "history.json").read_text(encoding="utf-8"))
    if not history:
        pytest.skip("artifact history is empty; cannot test chain tamper")
    # Inject a tampered event that breaks the recomputed chain hash
    history.append({"type": "TAMPERED_EVENT", "injected": True})
    (golden_artifact / "history.json").write_text(json.dumps(history), encoding="utf-8")
    with pytest.raises(DeterministicReplayError, match="chain broken"):
        GoldenArtifactTraceAdapter(golden_artifact)


def test_verify_chain_can_be_disabled(golden_artifact):
    # With verify_chain=False, a tampered chain loads without raising
    history = json.loads((golden_artifact / "history.json").read_text(encoding="utf-8"))
    history.append({"type": "TAMPERED_EVENT"})
    (golden_artifact / "history.json").write_text(json.dumps(history), encoding="utf-8")
    adapter = GoldenArtifactTraceAdapter(golden_artifact, verify_chain=False)
    assert adapter.cursor == 0


# ---------------------------------------------------------------------------
# 4. Fork from artifact (forensic trail)
# ---------------------------------------------------------------------------

def test_create_fork_from_artifact_binds_final_hash(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    registry = ForkRegistry()
    fork = registry.create_fork_from_artifact(adapter, mode=REPLAY_DETERMINISTIC)

    assert fork.parent_history_hash == adapter.final_history_hash
    assert fork.metadata["source"] == "golden_artifact"
    assert fork.metadata["artifact_dir"] == adapter.artifact_dir
    assert fork.metadata["program_hash"] == adapter.program_hash


def test_exploratory_fork_from_artifact_preserves_artifact(golden_artifact):
    history_before = (golden_artifact / "history.json").read_text(encoding="utf-8")

    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    registry = ForkRegistry()
    fork = registry.create_fork_from_artifact(adapter, mode=REPLAY_EXPLORATORY_LIVE)

    assert fork.mode == REPLAY_EXPLORATORY_LIVE
    # The original artifact is untouched by forking
    history_after = (golden_artifact / "history.json").read_text(encoding="utf-8")
    assert history_before == history_after


def test_fork_from_artifact_without_final_hash_raises(golden_artifact, monkeypatch):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    # Simulate an artifact whose final_history_hash is empty
    monkeypatch.setattr(adapter, "_final_history_hash", "")
    registry = ForkRegistry()
    with pytest.raises(ReplayArtifactError, match="without final_history_hash"):
        registry.create_fork_from_artifact(adapter)


def test_two_forks_from_same_artifact_independent(golden_artifact):
    adapter = GoldenArtifactTraceAdapter(golden_artifact)
    registry = ForkRegistry()
    fork_a = registry.create_fork_from_artifact(adapter, mode=REPLAY_DETERMINISTIC)
    fork_b = registry.create_fork_from_artifact(adapter, mode=REPLAY_EXPLORATORY_LIVE)
    assert fork_a.fork_id != fork_b.fork_id
    assert fork_a.parent_history_hash == fork_b.parent_history_hash == adapter.final_history_hash
