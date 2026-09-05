"""Frozen operator input mutation cannot inherit an already issued grant."""

import json

from synapse.experiments.gold.stage10.context_codec import encode_canonical
from acceptance.stage4.stage11._project_inputs import project_input_case


def test_changed_frozen_budget_cannot_reuse_original_manifest_and_approval(tmp_path):
    case = project_input_case(tmp_path)
    code, pending = case.start()
    assert code == 3, pending
    path = case.run_root / "experiment.json"
    data = json.loads(path.read_bytes())
    data["declaration"]["config"]["max_attempts"] += 1
    path.write_bytes(encode_canonical(data))
    code, refused = case.approve(pending)
    assert code == 1 and refused["status"] == "GOLD_UNAVAILABLE", refused
    assert case.worker.calls == 0
