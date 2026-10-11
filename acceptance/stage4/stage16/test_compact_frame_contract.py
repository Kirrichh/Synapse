"""Exact frame representation preserves worker data within its local scope."""
from copy import deepcopy

import pytest

from acceptance.stage4.stage16._compact_frame_case import PRIVATE, migration_frame
from synapse.experiments.gold.project_agents import _retained_frame_payload
from synapse.experiments.gold.project_frame_storage import COMPACT_FRAME_V1, compact_frame, restore_frame
from synapse.experiments.gold.project_memory_store import MAX_MEMORY_EVENT_BYTES
from synapse.experiments.gold.source_snapshot import _memory_information
from synapse.experiments.gold.source_verification import canonical


@pytest.mark.parametrize("version", [1, 2, 3])
def test_exact_frame_versions_preserve_worker_bytes_and_independent_occurrences(version):
    frame = migration_frame(version=version)
    packed = compact_frame(frame, max_bytes=MAX_MEMORY_EVENT_BYTES)
    restored = restore_frame(packed, max_bytes=MAX_MEMORY_EVENT_BYTES)
    assert canonical(restored) == canonical(frame)
    worker = canonical(_memory_information(restored))
    assert worker == canonical(_memory_information(frame))
    assert PRIVATE.encode() not in worker
    assert len(canonical(packed)) < len(canonical(frame))
    retained = canonical(packed)
    first, second = restored["elements"][:2]
    first["memory"]["Context"]["allowed_scope"]["entries"].append("unrelated.py")
    assert "unrelated.py" not in second["memory"]["Context"]["allowed_scope"]["entries"]
    assert canonical(packed) == retained
    assert canonical(restore_frame(packed, max_bytes=MAX_MEMORY_EVENT_BYTES)) == canonical(frame)


@pytest.mark.parametrize("reference", [-1, True, "../neighbor", {"transaction_id": "neighbor"}, 10_000])
def test_reference_cannot_leave_the_bound_frame(reference):
    packed = compact_frame(migration_frame(), max_bytes=MAX_MEMORY_EVENT_BYTES)
    packed["elements"][0]["states"]["target_state"] = reference
    with pytest.raises(ValueError, match="reference leaves its bound frame"):
        restore_frame(packed, max_bytes=MAX_MEMORY_EVENT_BYTES)


def test_frame_identity_rejects_substitution_and_unbound_neighbor_values():
    packed = compact_frame(migration_frame(), max_bytes=MAX_MEMORY_EVENT_BYTES)
    unrelated = deepcopy(packed)
    unrelated["values"].append({"private_neighbor": "not selected"})
    with pytest.raises(ValueError, match="outside its element bindings"):
        restore_frame(unrelated, max_bytes=MAX_MEMORY_EVENT_BYTES)
    slot = packed["elements"][0]["memory"]["Context"]
    packed["values"][slot]["repository_revision"] = "f" * 40
    with pytest.raises(ValueError, match="expanded identity"):
        restore_frame(packed, max_bytes=MAX_MEMORY_EVENT_BYTES)


def test_compaction_cannot_extend_the_expanded_budget():
    frame = migration_frame()
    packed = compact_frame(frame, max_bytes=MAX_MEMORY_EVENT_BYTES)
    limit = len(canonical(frame)) - 1
    with pytest.raises(ValueError, match="expanded byte budget"):
        compact_frame(frame, max_bytes=limit)
    packed["expanded_bytes"] = limit
    with pytest.raises(ValueError, match="expanded byte budget"):
        restore_frame(packed, max_bytes=limit)


def test_equal_observation_bodies_keep_both_logical_origins():
    frame = migration_frame(version=1)
    histories = frame["elements"][0]["memory"]["Episodic"]["task_outcomes"]
    histories[1]["outcome"] = deepcopy(histories[0]["outcome"])
    restored = restore_frame(compact_frame(frame, max_bytes=MAX_MEMORY_EVENT_BYTES), max_bytes=MAX_MEMORY_EVENT_BYTES)
    result = restored["elements"][0]["memory"]["Episodic"]["task_outcomes"]
    assert result[0]["outcome"] == result[1]["outcome"]
    assert result[0]["run_id"] != result[1]["run_id"]
    assert result[0]["result_ref"] != result[1]["result_ref"]
    assert canonical(result) == canonical(histories)


def test_full_event_budget_includes_the_project_model_outside_the_frame():
    frame = migration_frame()
    padding = MAX_MEMORY_EVENT_BYTES - len(canonical(frame))
    payload = {"active_memory_frame": frame, "project_model": {"retained": "x" * padding}}
    with pytest.raises(ValueError, match="expanded byte budget"):
        _retained_frame_payload(payload, {"frame_storage": COMPACT_FRAME_V1})
