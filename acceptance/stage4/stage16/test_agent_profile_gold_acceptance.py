"""Heavy new-profile acceptance through the existing governed Gold/C1 scenario."""

import json

from acceptance.stage4.stage11._project_inputs import ProjectInputCase
from acceptance.stage4.stage16._multi_target_case import execute_multi_target_case
from synapse.agents.configuration import AGENT_CONFIGURATION_V1, capture_definition
from synapse.agents.mini_adapter import MiniAgentAdapter
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V4
from synapse.experiments.gold.stage10_composition import decode_worker_configuration


def test_admitted_profile_preserves_gold_task_proof_publication_and_recovery(tmp_path, monkeypatch):
    evidence = tmp_path / "admission.json"
    evidence.write_text(json.dumps({"authority": "acceptance scenario only",
                                   "profile": "mini-swe-agent/2.4.6"}))
    original_start = ProjectInputCase.start

    def start_with_profile(case):
        declaration = json.loads(case.input_path.read_text())
        native = declaration.pop("worker")
        native["input_profile"] = "mini-2.4.6-local-edit-proposals/v4"
        profile = MiniAgentAdapter(config=decode_worker_configuration(native)).profile
        definition = capture_definition(factory="mini", profile=profile, native=native,
            distributions=("mini-swe-agent", "litellm", "openai"), evidence_paths=(evidence,),
            capabilities=("repository.edit",))
        declaration["schema_version"] = EXPERIMENT_INPUT_SCHEMA_V4
        declaration["agents"] = {"schema_version": AGENT_CONFIGURATION_V1,
                                 "profiles": [definition], "preferred_profiles": []}
        case.input_path.write_text(json.dumps(declaration))
        return original_start(case)

    monkeypatch.setattr(ProjectInputCase, "start", start_with_profile)
    completed, result, publication = execute_multi_target_case(tmp_path, monkeypatch, omit_second=False)
    assert completed["status"] == "GOLD_RESOLVED"
    assert completed["outcome_status"] == "FULL"
    assert result.oracle_resolved is True
    assert publication["state"] == "COMMITTED"
