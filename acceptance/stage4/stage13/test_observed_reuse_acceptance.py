"""Observed usefulness requires actual downstream consumption and independent proof."""

import json

from acceptance.stage4.stage13._observed_reuse import observed_reuse_case
from synapse.experiments.gold.knowledge_environment import open_gold_project


def test_exact_rejected_candidate_is_consumed_without_repeating_c1(tmp_path):
    producer, consumer, original, request = observed_reuse_case(tmp_path)
    code, pending = consumer.start()
    assert code == 3, pending
    code, result = consumer.approve(pending)
    assert code == 0, result
    outcome = result["result"]["structured_outcome"]
    assert outcome["payload"]["status"] == "UNRESOLVED", result
    assert len(outcome["payload"]["observed_reuse"]) == 1, result
    promotion = outcome["payload"]["observed_reuse"][0]
    assert promotion["state"] == "OBSERVED_USEFUL_REUSE"
    assert consumer.worker.calls == 2
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 1
    epoch = open_gold_project(consumer.state_root).fence.current_epoch()
    code, resumed = consumer.cli("project", "resume", "--run-dir", consumer.run_root)
    assert code == 0, resumed
    assert resumed["result"]["structured_outcome"] == outcome
    assert open_gold_project(consumer.state_root).fence.current_epoch() == epoch
    code, old = producer.cli("project", "resume", "--run-dir", producer.run_root)
    assert code == 0, old
    assert old["result"]["structured_outcome"] == original
    assert original["payload"]["observed_reuse"] == []
    assert consumer.worker.calls == 2
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 1
