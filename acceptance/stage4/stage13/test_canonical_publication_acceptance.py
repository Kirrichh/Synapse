"""The canonical CLI publishes verified negative knowledge and resumes it once."""

import json

from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process
from synapse.experiments.gold.knowledge_environment import open_gold_project


def test_canonical_negative_outcome_has_actual_publication_and_no_repeat_execution(tmp_path):
    case = project_input_case(tmp_path, outcomes=("PATCH",))
    create_oracle_process(tmp_path / "harness", (False,))
    code, pending = case.start()
    assert code == 3, pending
    code, result = case.approve(pending)
    assert code == 0, result
    outcome = result["result"]["structured_outcome"]
    assert outcome["payload"]["status"] == "VERIFIED_REUSABLE_PARTIAL", result
    assert outcome["payload"]["publication_result"] == "COMMITTED"
    assert len(outcome["payload"]["publication_refs"]) == 1
    project = open_gold_project(case.state_root)
    assert len(project.library.search_index()) == 2
    epoch = project.fence.current_epoch()
    code, resumed = case.cli("project", "resume", "--run-dir", case.run_root)
    assert code == 0, resumed
    assert resumed["result"]["structured_outcome"] == outcome
    assert open_gold_project(case.state_root).fence.current_epoch() == epoch
    assert case.worker.calls == 1
    assert json.loads((tmp_path / "harness" / "oracle_state.json").read_text())["calls"] == 1
