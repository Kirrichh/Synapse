"""Heavy shard: canonical learning, partial composition and valid memory controls.

All runs start at the same original revision. Memory selection is declared by
the operator before freezing each run; physical archives and proofs stay intact.
Only neutral edit proposals reach the installed Mini and the real C1/oracle.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16.test_partial_memory_loop_acceptance import completed_attempt
from synapse.experiments.gold.project_agents import read_active_memory
from synapse.experiments.gold.project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.project_model import MEMORY_KINDS
from synapse.experiments.gold.run_inputs import reopen_frozen_inputs
from synapse.experiments.gold.source_snapshot import memory_source_basis
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V4, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def test_canonical_cycle_with_independent_success_and_failure_memory_selections(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True,
        extra_sources={"src/scale.py": "def double(value):\n    return value\n"},
        task_statement="Fix add(a, b) in src/calc.py and double(value) in src/scale.py.")
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = case.input_path.parent / knowledge_path
    original_knowledge = json.loads(knowledge_path.read_text())
    originals = {name: (case.repo / name).read_bytes() for name in ("src/calc.py", "src/scale.py")}
    create_executing_oracle(tmp_path / "harness", repo=case.repo,
        base_revision=declaration["config"]["base_revision"], target_paths=tuple(originals),
        command=(sys.executable, "-B", "-c",
            "from src.calc import add; from src.scale import double; "
            "assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(4,3),(0,0)]); "
            "assert all(double(x)==2*x for x in [-2,0,7])"))
    calc = {"edits": [{"path": "src/calc.py", "old": "a - b", "new": "a + b"}]}
    scale = {"edits": [{"path": "src/scale.py", "old": "return value", "new": "return value * 2"}]}
    commands = [LOCAL_EDIT_COMMAND + json.dumps({"schema_version": LOCAL_EDIT_PROPOSAL_V1,
        "alternatives": alternatives}) for alternatives in ([calc], [calc, scale], [calc, scale], [], [])]
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "acceptance-only")
    with provider_endpoint(commands=commands) as (endpoint, requests):
        mini = Path(sys.executable).parent / "mini"
        assert mini.is_file()
        declaration["config"]["model"] = "gpt-4o-mini"
        declaration["worker"] = {"provider": "mini", "command": [str(mini)], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1", "input_profile": LOCAL_EDIT_PROFILE_V4,
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        runs, retained, owner_ids = [], {}, []
        for index, (name, selection) in enumerate((("partial", "ALL"), ("without-failure", "NONE"),
                ("composed", "ALL"), ("without-success", "FAILURE_ONLY"), ("reused", "SUCCESS_ONLY"))):
            current = replace(case, run_root=tmp_path / (name + "-run"), input_path=tmp_path / (name + ".json"))
            run_knowledge = tmp_path / (name + "-knowledge.json")
            run_knowledge.write_text(json.dumps({**original_knowledge,
                "schema_version": PROJECT_KNOWLEDGE_INPUT_V4, "run_memory_selection": selection}))
            current.input_path.write_text(json.dumps({**declaration, "run_id": name, "knowledge_path": str(run_knowledge)}))
            code, pending = current.start()
            assert code == 3 and len(requests) == index, pending
            code, result = current.approve(pending)
            assert code == 0 and result["project_memory"]["status"] == "RECORDED", result
            frozen, _, attempt, completed = completed_attempt(current)
            snapshot = frozen.data["source_snapshot"]
            assert snapshot["run_memory_selection"] == selection
            frame = read_active_memory(snapshot["project_memory"], source_snapshot=memory_source_basis(snapshot),
                run_memory_selection=selection)
            owner_ids.append([item["owner_id"] for item in frame["owners"]])
            assert all(owner["state"] == "IDLE" for owner in frame["owners"])
            assert all(set(item["memory"]) == set(MEMORY_KINDS) for item in frame["active_memory_frame"]["elements"])
            assert all(item["development_delta"]["status"] == "AWAITING_FRESH_VERIFICATION"
                       for item in frame["active_memory_frame"]["elements"])
            observation = inspect_observability(run_root=current.run_root,
                assessment_key=result["observability"]["assessment_key"])
            assert observation["telemetry_report"]["status"] == "COMPLETE", observation["telemetry_report"]
            assert observation["artifact_report"]["status"] == "COMPLETE", observation["artifact_report"]
            assert observation["telemetry_report"]["source_totals"]["physical_provider_reported_tokens"] == 18
            local = completed.worker_result.diagnostics["local_edit_result"]
            runs.append((current, result, local, frame))
            retained[current.run_root / "experiment.json"] = frozen.canonical_bytes
            assert {path: path.read_bytes() for path in retained} == retained
            assert {name: (case.repo / name).read_bytes() for name in originals} == originals

        assert runs[0][1]["outcome_status"] == runs[1][1]["outcome_status"] == "VERIFIED_REUSABLE_PARTIAL"
        assert runs[2][1]["outcome_status"] == runs[4][1]["outcome_status"] == "FULL"
        assert runs[1][2]["candidate_origins"][runs[1][2]["selected_index"]]["kind"] == "PUBLIC_PROPOSAL"
        assert runs[2][2]["candidate_origins"][runs[2][2]["selected_index"]]["kind"] == "LOCAL_PARTIAL_MEMORY"
        assert runs[3][2]["status"] == "NO_APPLICABLE_PROPOSAL"
        assert runs[4][2]["candidate_origins"][runs[4][2]["selected_index"]]["kind"] == "LOCAL_MEMORY"
        assert runs[4][2]["proposal"]["alternatives"] == []
        assert all(owners == owner_ids[0] for owners in owner_ids)
        assert any(item["memory"]["Episodic"]["task_outcomes"]
                   for item in runs[2][3]["active_memory_frame"]["elements"])
        assert all(entry["status"] == "FULL" for item in runs[4][3]["active_memory_frame"]["elements"]
                   for entry in item["memory"]["Episodic"]["task_outcomes"])
        assert requests[1] == requests[2] and requests[3] == requests[4]
        assert len(requests) == 5
        observed_path = tmp_path / "harness/oracle_observations.jsonl"
        observed = [json.loads(line) for line in observed_path.read_text().splitlines()]
        assert len(observed) == 4 and all(item["before_returncode"] != 0 for item in observed)
        assert [item["after_returncode"] == 0 for item in observed] == [False, False, True, True]
        before = observed_path.read_bytes()
        current, result, _, _ = runs[-1]
        memory_store = ProjectMemoryStore(tmp_path / "state", read_only=True)
        events = memory_store.inventory()
        code, resumed = current.cli("project", "resume", "--run-dir", current.run_root)
        assert code == 0 and resumed == result
        assert memory_store.inventory() == events
        assert len(requests) == 5 and observed_path.read_bytes() == before
        (tmp_path / "full-memory-cycle-evidence.json").write_text(json.dumps({
            "outcomes": [result["outcome_status"] for _, result, _, _ in runs],
            "memory_selections": ["ALL", "NONE", "ALL", "FAILURE_ONLY", "SUCCESS_ONLY"],
            "provider_calls": len(requests), "independent_oracle_observations": observed,
            "unchanged_original_sources": True, "resume_repeated_effects": False}, indent=2))
