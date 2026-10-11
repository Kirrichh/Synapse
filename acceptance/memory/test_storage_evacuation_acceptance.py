"""Accept memory search during evacuation of a large changing storage cluster."""
from acceptance.memory.storage_evacuation.scenario import execute


def test_evacuation_replans_after_failure_using_corrected_memory_then_replays_without_effects(tmp_path):
    result = execute(tmp_path / "storage-evacuation")
    assert result["applied_moves"] == 962
    assert result["transport_calls"] == 48
    assert result["replay_transport_calls"] == 0
    assert result["knowledge_versions"] == 3168
    assert result["stale_plan_refusals"] == [
        {"plan_epoch": 0, "live_epoch": 1, "attempted_moves": 24, "applied": 0},
    ]
