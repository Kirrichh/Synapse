"""One heavy scenario: a completed result loses its retained C1 report."""

import json

import pytest

from acceptance.stage4.stage11._builders import run_world
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy, GoldRunViolation


def test_completed_result_cannot_survive_a_missing_report(tmp_path):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)])
    result = world.execute()
    assert result.structured_outcome["payload"]["status"] == "FULL"
    history_path = world.run_root / "gold_attempts.jsonl"
    history = history_path.read_bytes()
    row = json.loads(history.splitlines()[0])
    report = world.run_root / "controlled-change-reports" / row["gold_evidence"]["report_path"]
    report.unlink()
    with pytest.raises(GoldRunViolation, match="revalidated evidence"):
        world.controller.load_result()
    assert history_path.read_bytes() == history
    assert world.worker_process.calls == world.oracle.calls == 1
