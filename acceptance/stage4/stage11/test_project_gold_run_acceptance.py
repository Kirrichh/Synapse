"""Canonical CLI run and one-command approval, each in a fresh interpreter."""

import json

from acceptance.stage4.stage11._project_inputs import project_input_case


def test_project_run_reaches_real_replay_delivery_and_c1_after_one_approval(tmp_path):
    case = project_input_case(tmp_path)
    code, pending = case.start()
    assert code == 3, pending
    assert pending["status"] == "APPROVAL_REQUIRED"
    assert case.worker.calls == 0
    code, completed = case.approve(pending)
    assert code == 0, completed
    assert completed["status"] == "GOLD_STOPPED_NO_PROGRESS", completed
    assert completed["result"]["structured_outcome"]["payload"]["status"] == "NO_CANDIDATE"
    assert case.worker.calls == 1
    rows = [json.loads(line) for line in (case.run_root / "gold_attempts.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "GOLD_NO_CANDIDATE"
    usage = rows[0]["payload"]["materialization_diagnostics"]["usage"]
    assert usage["token_status"] == "PROVIDER_REPORTED"
    assert usage["total_tokens"] == 0  # The fixture makes no paid model calls.
