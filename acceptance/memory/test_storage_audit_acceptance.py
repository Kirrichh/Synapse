"""Accept a temporal storage investigation through the public durable runtime."""
from acceptance.memory.storage_audit.scenario import execute


def test_historical_investigation_distinguishes_valid_time_from_later_knowledge_then_replays(tmp_path):
    result = execute(tmp_path / "storage-audit")
    assert result["queries"] == 64 and result["distinct_query_texts"] == 16
    assert result["knowledge_versions"] == 3168
    assert result["initial_window"] == 4 and result["final_window"] == 5
    assert result["frozen_reference_equal"] is True
    assert result["node12_original_alive"] is True
    assert result["node12_before_correction_known_alive"] is True
    assert result["node12_corrected_alive"] is False
    assert result["node5_corrected_free"] == 8
    assert result["transport_calls"] == result["replay_transport_calls"] == 0
    assert result["replay"]["result"]["status"] == "replay_verified"
