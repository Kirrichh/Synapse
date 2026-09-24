"""§§27, 30, 35: actual solved task, measured cost, independent evidence loss."""

import json
from pathlib import Path
import subprocess
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._paired_case import paired_case, REPOSITORY
from acceptance.stage4.stage16.assessment import assess
from acceptance.stage4.stage16.harness import Experiment
from acceptance.stage4.stage16.protocol import read_source
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROPOSAL_V1
from tests.test_swebench_gold_runner import OLD_SOURCE, NEW_SOURCE


def test_solved_task_reports_real_results_and_lost_accounting_does_not_rewrite_full(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    proposal = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [
        {"edits": [{"path": "src/calc.py", "old": OLD_SOURCE, "new": NEW_SOURCE}]}]}
    commands = []
    with provider_endpoint(commands=commands) as (endpoint, requests):
        protocol, cases = paired_case(tmp_path / "inputs", endpoint, oracle_outcomes=(True,),
            gold_input_profile=LOCAL_EDIT_PROFILE_V1)
        # The one admitted agent receives the same edit in both arms. In Baseline
        # it applies the edit itself; in Gold, Synapse interprets the proposal.
        commands.extend(LOCAL_EDIT_COMMAND + json.dumps(proposal) for _ in protocol.schedule())
        experiment = Experiment(tmp_path / "experiment", repository=REPOSITORY, protocol=protocol)
        allocations = experiment.run_all(approve_pending=True)
        assert all(slot["state"] == "FINISHED" for slot in allocations), allocations
        result = assess(experiment)
        by_arm = {row["arm"]: row for row in result["runs"]}
        assert by_arm["GOLD"]["outcome"]["status"] == "FULL", result
        assert by_arm["BASELINE"]["outcome"]["status"] == "ORACLE_RESOLVED", result
        assert all(row["outcome"]["task_resolved"] is True for row in by_arm.values())
        assert all(row["provider_tokens"] == 18 and row["attempt_count"] == 1 for row in by_arm.values())
        assert all(row["measurements_status"] == "COMPLETE" for row in by_arm.values()), result
        assert all(row["provider_money"] is None for row in by_arm.values())
        assert all(row["verified_resolved"] == row["requested_runs"] == 1 for row in result["task_results"])
        assert all(row["samples"]["provider_tokens"]["mean"] == "18" for row in result["task_results"])
        assert len(requests) == 2
        for slot in allocations:
            definition = read_source(slot["input_ref"])
            oracle = definition["oracle"] if slot["arm"] == "BASELINE" else read_source(definition["declaration_ref"])["oracle"]
            assert json.loads((Path(oracle["swebench_work_dir"]) / "oracle_state.json").read_bytes())["calls"] == 1
        completed = subprocess.run([sys.executable, "-B", "-m", "acceptance.stage4.stage16", "report",
            "--experiment", str(experiment.root), "--format", "table"], cwd=REPOSITORY,
            capture_output=True, text=True, timeout=90)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "| FULL | 1 | 18 |" in completed.stdout
        assert "| ORACLE_RESOLVED | 1 | 18 |" in completed.stdout
        assert result["assessment_id"] in completed.stdout
        resource = by_arm["GOLD"]["infrastructure"]
        ref = reference(resource, resource["schema_version"])
        path, = (cases[0].run_root / "run-records" / "observation").glob(ref.sha256 + ".*.json")
        raw = path.read_bytes()
        history = experiment.history()
        path.unlink()
        try:
            damaged = assess(experiment)
            gold = next(row for row in damaged["runs"] if row["arm"] == "GOLD")
            assert gold["outcome"] == by_arm["GOLD"]["outcome"]
            assert gold["measurements_status"] == "INCOMPLETE"
            assert gold["infrastructure"] is None
            assert damaged["pairs"][0]["status"] == "INCOMPLETE"
            assert experiment.history() == history
            assert len(requests) == 2
        finally:
            path.write_bytes(raw)
        assert assess(experiment) == result
