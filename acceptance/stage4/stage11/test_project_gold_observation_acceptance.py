"""Compatibility must observe the same governing task the worker is given."""

import json

from acceptance.stage4.stage10._builders import hash_ref
from acceptance.stage4.stage11._project_inputs import project_input_case
from synapse.experiments.gold.canonicalization import RefKind


def test_observation_cannot_substitute_another_seed_task_for_the_actual_task(tmp_path):
    case = project_input_case(tmp_path)
    data = json.loads(case.input_path.read_bytes())
    data["observation"]["task_contract_ref"] = hash_ref(RefKind.CONTRACT_CONDITION, "another-task").to_dict()
    case.input_path.write_text(json.dumps(data))
    code, pending = case.start()
    assert code == 3, pending
    code, refused = case.approve(pending)
    assert code == 1 and refused["status"] == "GOLD_UNAVAILABLE", refused
    assert "different governing task" in refused["detail"]
    assert case.worker.calls == 0
