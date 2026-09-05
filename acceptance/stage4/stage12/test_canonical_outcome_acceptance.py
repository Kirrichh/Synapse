"""One heavy shard: the canonical CLI reaches real C1 and the C2 contract."""

import json

from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process


def test_automatic_approval_run_and_fresh_process_resume_report_full(tmp_path):
    case = project_input_case(tmp_path, outcomes=("PATCH",))
    create_oracle_process(tmp_path / "harness", (True,))
    code, pending = case.start()
    assert code == 3, pending
    code, result = case.approve(pending)
    assert code == 0, result
    outcome = result["result"]["structured_outcome"]
    assert outcome["payload"]["status"] == "FULL", result
    assert outcome["payload"]["publication_result"] == "NOT_ATTEMPTED"
    code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
    assert code == 0, resumed
    assert resumed["result"]["structured_outcome"] == outcome
    assert case.worker.calls == 1
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 1
