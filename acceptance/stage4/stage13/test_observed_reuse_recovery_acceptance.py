"""Resume actual consumption checkpoints without repeating worker or C1 effects."""

import json
from pathlib import Path

import pytest

from acceptance.stage4.stage13._observed_reuse import observed_reuse_case
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.runner_composition import compose_frozen_gold_run
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.run_recovery import RunRecordSession
from synapse.experiments.gold.stage10.approval import grant_approval


@pytest.mark.parametrize("boundary", ["observation", "promotion"])
def test_guard_and_promotion_are_recovered_once(tmp_path, monkeypatch, boundary):
    producer, consumer, original, request = observed_reuse_case(tmp_path)
    code, pending = consumer.start()
    assert code == 3, pending
    grant_approval(request_path=Path(pending["request_path"]), store_root=consumer.run_root / "approvals", duration_seconds=3600)
    composition = compose_frozen_gold_run(reopen_frozen_inputs(consumer.run_root))
    put = RunRecordSession.put

    def interrupted(session, record):
        result = put(session, record)
        if (boundary == "observation" and record.kind == RecordKind.ATTEMPT_PROGRESS
                and record.key.endswith("reuse_guard_completed")
                or boundary == "promotion" and record.kind == RecordKind.REUSE_PROMOTION):
            raise SystemExit("acceptance interruption after durable checkpoint")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(RunRecordSession, "put", interrupted)
        with pytest.raises(SystemExit):
            composition.execute()
    assert consumer.worker.calls == 2
    code, recovered = consumer.cli("project", "resume", "--run-dir", consumer.run_root)
    assert code == 0, recovered
    outcome = recovered["result"]["structured_outcome"]
    assert outcome["payload"]["status"] == "UNRESOLVED"
    assert len(outcome["payload"]["observed_reuse"]) == 1
    code, repeated = consumer.cli("project", "resume", "--run-dir", consumer.run_root)
    assert code == 0, repeated
    assert repeated["result"]["structured_outcome"] == outcome
    assert consumer.worker.calls == 2
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 1
