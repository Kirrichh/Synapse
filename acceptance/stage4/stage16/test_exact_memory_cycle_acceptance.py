"""Heavy shard: physical run -> consolidation -> restart -> automatic proposal.

Every effect uses the canonical CLI, an admitted agent, C1 and a separately
executed oracle. The provider supplies only the initial patch proposal.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16._completed_attempt import completed_attempt
from synapse.experiments.gold.project_agents import read_active_memory
from synapse.experiments.gold.project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.source_snapshot import memory_source_basis
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROPOSAL_V1
from acceptance.agents.coding_agents import use_model_agent


def test_learning_survives_restart_and_automatic_proposal_keeps_fresh_c1_and_oracle(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = case.input_path.parent / knowledge_path
    knowledge = json.loads(knowledge_path.read_text())
    original = (case.repo / "src/calc.py").read_bytes()
    create_executing_oracle(tmp_path / "harness", repo=case.repo,
        base_revision=declaration["config"]["base_revision"], target_paths=("src/calc.py",),
        command=(sys.executable, "-B", "-c", "from src.calc import add; assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(4,3),(0,0)])"))
    good = {"edits": [{"path": "src/calc.py", "old": "a - b", "new": "a + b"}]}
    commands = [LOCAL_EDIT_COMMAND + json.dumps({"schema_version": LOCAL_EDIT_PROPOSAL_V1,
                "alternatives": alternatives}) for alternatives in ([good], [], [])]
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "acceptance-only")
    results, snapshots = [], {}
    with provider_endpoint(commands=commands) as (endpoint, requests):
        for name, selection, profile, expected_calls in (
                ("learn", "ALL", LOCAL_EDIT_PROFILE_V5, 1),
                ("automatic", "ALL", LOCAL_EDIT_PROFILE_V5, 1),
                ("slow-only", "ALL", LOCAL_EDIT_PROFILE_V4, 2),
                ("without-run-memory", "NONE", LOCAL_EDIT_PROFILE_V5, 3)):
            current = replace(case, run_root=tmp_path / (name + "-run"), input_path=tmp_path / (name + ".json"))
            selected = tmp_path / (name + "-knowledge.json")
            selected.write_text(json.dumps({**knowledge, "schema_version": PROJECT_KNOWLEDGE_INPUT_V4,
                                           "run_memory_selection": selection}))
            chosen = use_model_agent({**declaration, "config": dict(declaration["config"])},
                                     tmp_path / (name + "-agent"), endpoint=endpoint, protocol=profile)
            current.input_path.write_text(json.dumps({**chosen, "run_id": name, "knowledge_path": str(selected)}))
            before_calls = len(requests)
            code, pending = current.start()
            assert code == 3 and len(requests) == before_calls, pending
            code, result = current.approve(pending)
            assert code == 0 and result["project_memory"]["status"] == "RECORDED", result
            assert len(requests) == expected_calls
            frozen, _, _, completed = completed_attempt(current)
            snapshot = frozen.data["source_snapshot"]
            frame = read_active_memory(snapshot["project_memory"], source_snapshot=memory_source_basis(snapshot),
                                       run_memory_selection=selection)["active_memory_frame"]
            layers = frame["layers"]
            assert set(layers) == {"schema_version", "declared", "learned", "working", "episodic"}
            assert layers["working"]["status"] == "AWAITING_FRESH_VERIFICATION"
            assert all(item["generalization"] == "NOT_ESTABLISHED" for item in layers["learned"])
            local = completed.worker_result.diagnostics["local_edit_result"]
            if name == "automatic":
                assert layers["learned"] and local["planning_route"] == "EXACT_MEMORY"
                assert local["proposal"]["alternatives"] == []
                assert local["status"] == "UNVERIFIED_PATCH_PROPOSAL"
                # Synapse, not the pluggable agent, executed the admitted memory route.
                assert completed.delivery_receipt.transport_name == "synapse.exact-memory/v1"
                assert not (current.run_root / "stage10" / "agent-executions").exists()
            if selection == "NONE":
                assert layers["learned"] == [] and layers["episodic"] == []
            observation = inspect_observability(run_root=current.run_root, assessment_key=result["observability"]["assessment_key"])
            assert observation["telemetry_report"]["status"] == "COMPLETE", observation["telemetry_report"]
            assert observation["artifact_report"]["status"] == "COMPLETE", observation["artifact_report"]
            results.append((current, result, local))
            snapshots[current.run_root / "experiment.json"] = frozen.canonical_bytes
            assert all(path.read_bytes() == raw for path, raw in snapshots.items())
            assert (case.repo / "src/calc.py").read_bytes() == original
        assert [result["outcome_status"] for _, result, _ in results[:3]] == ["FULL"] * 3
        assert results[3][2]["diff_text"] is None
        assert requests[1] == requests[2]  # Removing local memory does not change public request content.
        assert "procedural_memory" not in json.dumps(requests)
        observed_path = tmp_path / "harness/oracle_observations.jsonl"
        observed = [json.loads(line) for line in observed_path.read_text().splitlines()]
        assert len(observed) == 3 and all(row["after_returncode"] == 0 for row in observed)
        current, result, _ = results[1]
        memory = ProjectMemoryStore(tmp_path / "state", read_only=True)
        before = memory.inventory()
        oracle_before = observed_path.read_bytes()
        code, resumed = current.cli("project", "resume", "--run-dir", current.run_root)
        assert code == 0 and resumed == result
        assert memory.inventory() == before and observed_path.read_bytes() == oracle_before and len(requests) == 3
        learning = [event for event, _ in before if event["kind"] == "CONSOLIDATED"]
        assert len(learning) == 4
        control_outcome = results[-1][1]["project_memory"]["event"]
        control, = [event for event in learning if event["payload"]["outcome_ref"] == control_outcome]
        assert control["payload"]["assertions"] == []
        assert control["payload"]["unresolved"] and all(item["status"] == "PROVISIONAL" for item in control["payload"]["unresolved"])
