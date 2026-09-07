"""Controlled provider inputs for real isolated Baseline/Gold acceptance runs."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from acceptance.stage4.stage11._project_inputs import project_input_case
from acceptance.stage4.stage11._oracle_process import create_oracle_process
from synapse.experiments.gold.run_inputs import EXPERIMENT_INPUT_SCHEMA_V2
from synapse.experiments.swebench.mini_config import MiniInvocationConfig
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE

from .protocol import canonical, preregister, source


REPOSITORY = Path(__file__).resolve().parents[3]


def paired_case(root, endpoint, *, replicates=1, max_attempts=1, oracle_outcomes=None):
    pairs, gold_cases = [], []
    mini_path = str(Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini"))
    for replicate in range(replicates):
        base = root / str(replicate)
        # Replica repositories have identical commit metadata as well as source
        # bytes. Only fixture creation uses a fixed clock; execution is measured.
        with patch.dict(os.environ, {"GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}):
            gold = project_input_case(base / "gold", max_attempts=max_attempts)
        gold_cases.append(gold)
        declaration = json.loads(gold.input_path.read_bytes())
        declaration["schema_version"] = EXPERIMENT_INPUT_SCHEMA_V2
        declaration["run_id"] = f"stage16-gold-{replicate}"
        declaration["config"]["model"] = "gpt-4o-mini"
        declaration["worker"] = {"provider": "mini", "command": [mini_path], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1",
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        gold.input_path.write_bytes(canonical(declaration))
        baseline_root = base / "baseline"
        baseline_root.mkdir()
        baseline_repo = baseline_root / "repo"
        subprocess.run(["git", "clone", "--no-local", "-q", str(gold.repo), str(baseline_repo)], check=True)
        baseline = {"arm": "BASELINE", "repo_root": str(baseline_repo), "run_root": str(baseline_root / "run"),
            "base_revision": declaration["config"]["base_revision"], "max_attempts": max_attempts,
            "task": {"task_id": declaration["config"]["task_id"], "instance_id": declaration["config"]["instance_id"],
                "statement": declaration["task_contract"]["task_statement"], "allowed_scope": ["src/calc.py"]},
            "mini": asdict(MiniInvocationConfig(executable=mini_path, model="gpt-4o-mini", cost_limit=1, step_limit=3, timeout_seconds=60)),
            "oracle": {**declaration["oracle"], "swebench_work_dir": str(baseline_root / "harness")},
            "provider_connection": {"credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "endpoint": endpoint, "timeout_seconds": 60}}
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
