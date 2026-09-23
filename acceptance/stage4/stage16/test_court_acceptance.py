"""Heavy shard: the court through the canonical CLI, interruption and recovery.

A completed run without a candidate loses its court decision to an
interruption, then its physical records are moved away. Memory stays usable
but grants no new automatic authority. After the records return, one resume
judges the run once. Pinned frames keep their original decision, and two more
task streams extend one court chain and reuse the admitted patch without
dispatching any agent. The pluggable agent (Mini in this acceptance)
plans only the ordinary route; its controlled provider returns neutral edit
proposals. C1 and the executing oracle verify every candidate.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._completed_attempt import completed_attempt
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold import project_agents
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_court import read_court
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.experiments.gold.runner_composition import execute_gold_project_run
from synapse.experiments.gold.source_snapshot import memory_source_basis
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V6, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def _court_frame(snapshot):
    return project_agents.read_active_memory(snapshot["project_memory"], source_snapshot=memory_source_basis(snapshot),
        run_memory_selection=snapshot["run_memory_selection"])["active_memory_frame"]["court"]


def test_court_judges_each_outcome_once_across_interruption_damage_and_task_streams(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    original = (case.repo / "src/calc.py").read_bytes()
    create_executing_oracle(tmp_path / "harness", repo=case.repo,
        base_revision=declaration["config"]["base_revision"], target_paths=("src/calc.py",),
        command=(sys.executable, "-B", "-c", "from src.calc import add; assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(0,0)])"))
    good = {"edits": [{"path": "src/calc.py", "old": "a - b", "new": "a + b"}]}
    commands = [LOCAL_EDIT_COMMAND + json.dumps({"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": alternatives})
                for alternatives in ([], [good])]
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "acceptance-only")

    with provider_endpoint(commands=commands) as (endpoint, requests):
        mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
        declaration["config"]["model"] = "gpt-4o-mini"
        declaration["worker"] = {"provider": "mini", "command": [str(mini)], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1", "input_profile": LOCAL_EDIT_PROFILE_V6,
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                           "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}

        def prepared(name):
            current = replace(case, run_root=tmp_path / (name + "-run"), input_path=tmp_path / (name + ".json"))
            current.input_path.write_text(json.dumps({**declaration, "run_id": name}))
            return current

        def completed(current):
            code, pending = current.start()
            assert code == 3, pending
            code, result = current.approve(pending)
            assert code == 0 and result["outcome_status"] == "FULL", result
            return result

        # 1. A run without a candidate completes, but its court decision is interrupted.
        interrupted = prepared("interrupted")
        code, pending = interrupted.start()
        assert code == 3, pending
        memory = ProjectMemoryStore(tmp_path / "state", read_only=True)  # The first job created the journal.
        code, _ = interrupted.cli("approve", pending["request_path"], "--store", interrupted.run_root / "approvals")
        assert code == 0

        def interruption(*args, **kwargs):
            raise RuntimeError("acceptance interruption before the court decision")

        with monkeypatch.context() as patch:
            patch.setattr(project_agents, "consolidate_court", interruption)
            code, lost = execute_gold_project_run(run_root=interrupted.run_root)
        assert code == 0 and lost["outcome_status"] != "FULL" and lost["project_memory"]["status"] == "UNAVAILABLE"
        kinds = [event["kind"] for event, _ in memory.inventory()]
        assert kinds.count("OUTCOME_RECORDED") == 1 and not {"CONSOLIDATED", "OBSERVED", "JUDGED"} & set(kinds)
        identity = json.loads((interrupted.run_root / "experiment.json").read_text())["project_record_sha256"]

        # 2. Its physical records disappear: memory stays usable, without new automatic authority.
        moved = tmp_path / "moved-away"
        interrupted.run_root.rename(moved)
        damaged = prepared("during-damage")
        damage_result = completed(damaged)
        assert len(requests) == 2
        judgement = damage_result["project_memory"]["court"]
        assert judgement["verdict"] == "COUNTED" and judgement["reasons"] == ["FULFILLED"]
        emergency = read_court(memory, project_identity=identity, decision=judgement["decision"])
        assert emergency["mode"] == "EMERGENCY"
        assert [item["reasons"] for item in emergency["pending"]] == [["BASIS_UNAVAILABLE"]]
        subject, = emergency["subjects"].values()
        assert subject["state"] == "OBSERVED" and len(subject["support"]) == 1  # Credited, admission withheld.
        withheld, = memory.read(judgement["decision"])["payload"]["transitions"]
        assert withheld["to"] == "OBSERVED" and withheld["withheld"] == "PENDING_OUTCOMES"
        frozen, _, _, done = completed_attempt(damaged)
        assert done.worker_result.diagnostics["local_edit_result"].get("planning_route") != "EXACT_MEMORY"
        damaged_snapshot = frozen.data["source_snapshot"]
        pinned = _court_frame(damaged_snapshot)
        assert pinned["mode"] == "EMERGENCY" and len(pinned["pending"]) == 1 and pinned["automatic_patches"] == []
        before = memory.inventory()
        code, resumed = damaged.cli("project", "resume", "--run-dir", damaged.run_root)
        assert code == 0 and resumed == damage_result and memory.inventory() == before

        # 3. The records return: one resume judges the interrupted run exactly once.
        moved.rename(interrupted.run_root)
        code, recovered = interrupted.cli("project", "resume", "--run-dir", interrupted.run_root)
        assert code == 0 and recovered["outcome_status"] == lost["outcome_status"], recovered
        assert recovered["project_memory"]["status"] == "RECORDED"
        judgement = recovered["project_memory"]["court"]
        assert judgement["verdict"] == "COUNTED" and judgement["reasons"] == ["NOT_FULFILLED"]
        before = memory.inventory()
        code, again = interrupted.cli("project", "resume", "--run-dir", interrupted.run_root)
        assert code == 0 and again == recovered and memory.inventory() == before
        full = read_court(memory, project_identity=identity, decision=judgement["decision"])
        assert full["mode"] == "FULL" and full["pending"] == []
        subject_id, subject = next(iter(full["subjects"].items()))
        assert subject["state"] == "ADMITTED" and len(subject["support"]) == 1  # No new credit, only admission.
        assert memory.read(judgement["decision"])["payload"]["transitions"] == [
            {"subject_id": subject_id, "patch_sha256": subject["subject"]["patch_sha256"],
             "from": "OBSERVED", "to": "ADMITTED", "support": 1, "refutations": 0}]

        # 4. A pinned frame keeps its decision; the next job pins the new one.
        assert _court_frame(damaged_snapshot) == pinned
        later = prepared("later")
        snapshot = freeze_gold_inputs(declaration_path=later.input_path, project=open_gold_project(tmp_path / "state"),
                                      run_root=later.run_root).data["source_snapshot"]
        current = _court_frame(snapshot)
        assert current["mode"] == "FULL" and current["pending"] == [] and current["judged_episodes"] == 2
        assert current["automatic_patches"] == [subject["subject"]["patch_sha256"]]

        # 5. Two more task streams: Synapse executes the admitted patch, one court chain.
        # Concurrent writers of one project journal are proven by the court contract;
        # Gold's publication stores refuse a second simultaneous run (LOCK_BUSY).
        streams = [prepared("stream-a"), prepared("stream-b")]
        results = [completed(stream) for stream in streams]
        assert len(requests) == 2  # No agent was asked for either stream.
        for stream in streams:
            _, _, _, done = completed_attempt(stream)
            assert done.worker_result.diagnostics["local_edit_result"]["planning_route"] == "EXACT_MEMORY"
            assert done.delivery_receipt.transport_name == "synapse.exact-memory/v1"
            assert not (stream.run_root / "stage10" / "agent-executions").exists()
    decisions = [event for event, _ in memory.inventory() if event["kind"] == "JUDGED"]
    # Every decision extends its own predecessor; there is no second version of the state.
    assert len({json.dumps(item["payload"]["predecessor"], sort_keys=True) for item in decisions}) == len(decisions)
    views = [read_court(memory, project_identity=identity, decision=item["project_memory"]["court"]["decision"])
             for item in results]
    view = max(views, key=lambda value: len(value["judged"]))
    outcomes = [event for event, _ in memory.inventory() if event["kind"] == "OUTCOME_RECORDED"]
    assert len(view["judged"]) == len(outcomes) == 4 and view["pending"] == []
    assert len({json.dumps(item["outcome"], sort_keys=True) for item in view["judged"]}) == 4
    assert [value["state"] for value in view["subjects"].values()] == ["ADMITTED"]
    assert (case.repo / "src/calc.py").read_bytes() == original
