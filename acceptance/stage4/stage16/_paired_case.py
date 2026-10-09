"""Controlled provider inputs for real isolated Baseline/Gold acceptance runs."""

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from acceptance.agents.coding_agents import agents_configuration, model_agent_definition
from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V4
from synapse.worker.local_edits import LOCAL_EDIT_PROFILE_V1

from .protocol import canonical, preregister, source


REPOSITORY = Path(__file__).resolve().parents[3]


def paired_case(root, endpoint, *, replicates=1, max_attempts=1, oracle_outcomes=None,
                gold_input_profile=None):
    pairs, gold_cases = [], []
    # One admitted agent for every replicate and both arms; only Gold adds Synapse.
    agents = agents_configuration(model_agent_definition(root / "agent", endpoint=endpoint,
                                                         protocol=gold_input_profile or LOCAL_EDIT_PROFILE_V1))
    for replicate in range(replicates):
        base = root / str(replicate)
        # Replica repositories have identical commit metadata as well as source
        # bytes. Only fixture creation uses a fixed clock; execution is measured.
        with patch.dict(os.environ, {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}):
            if gold_input_profile is None:
                gold = project_input_case(base / "gold", max_attempts=max_attempts)
            else:
                from acceptance.stage4.stage16._source_inputs import consumer_case
                gold, _ = consumer_case(base / "gold", automatic_targets=True)
        gold_cases.append(gold)
        declaration = json.loads(gold.input_path.read_bytes())
        declaration["run_id"] = f"stage16-gold-{replicate}"
        declaration["config"]["max_attempts"] = max_attempts
        declaration.pop("worker", None)
        declaration.update(schema_version=EXPERIMENT_INPUT_SCHEMA_V4, agents=agents)
        declaration["config"]["model"] = "gpt-4o-mini"
        gold.input_path.write_bytes(canonical(declaration))
        baseline_root = base / "baseline"
        baseline_root.mkdir()
        baseline_repo = baseline_root / "repo"
        subprocess.run(["git", "clone", "--no-local", "-q", str(gold.repo), str(baseline_repo)], check=True)
        baseline = {"arm": "BASELINE", "repo_root": str(baseline_repo), "run_root": str(baseline_root / "run"),
            "base_revision": declaration["config"]["base_revision"], "max_attempts": max_attempts,
            "task": {"task_id": declaration["config"]["task_id"], "instance_id": declaration["config"]["instance_id"],
                "statement": declaration["task_contract"]["task_statement"], "allowed_scope": ["src/calc.py"]},
            "agents": declaration["agents"],
            "oracle": {**declaration["oracle"], "swebench_work_dir": str(baseline_root / "harness")}}
        definitions = {"BASELINE": baseline, "GOLD": {"arm": "GOLD", "repo_root": str(gold.repo),
            "run_root": str(gold.run_root), "state_root": str(gold.state_root), "declaration_ref": source(gold.input_path)}}
        if oracle_outcomes is not None:
            for configuration in (baseline["oracle"], declaration["oracle"]):
                create_oracle_process(Path(configuration["swebench_work_dir"]), oracle_outcomes)
        inputs = {}
        for arm, value in definitions.items():
            path = base / (arm.lower() + "-definition.json")
            path.write_bytes(canonical(value))
            inputs[arm] = source(path)
        pairs.append({"pair_id": f"pair-{replicate}", "task_id": declaration["config"]["task_id"],
            "replicate_id": replicate, "inputs": inputs})
    return preregister(experiment_id="paired-acceptance", pairs=pairs, repository=REPOSITORY,
        specification={"version": "Stage4-v2.2-operator-acceptance-profile", "sha256": "a" * 64}, seed=7), gold_cases
