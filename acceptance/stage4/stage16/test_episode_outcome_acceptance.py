"""Heavy shard: an unknown effect never becomes a success or a failure basis.

Both runs use the canonical CLI, the installed Mini, C1 and the independent
oracle adapter. The first oracle is not installed, which is a real
infrastructure failure; the second is the executing oracle. New owner jobs
then read the two episodes under every operator selection.
"""
import base64
from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold.admission_journal import FileSnapshotFence
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_agents import OWNER_LIFECYCLE_V3, read_active_memory
from synapse.experiments.gold.project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.run_inputs import freeze_gold_inputs
from synapse.experiments.gold.runner.records import RunRecordStore
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.source_snapshot import memory_source_basis, source_experience_delivery
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROPOSAL_V1
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE


def _selected_knowledge(tmp_path, knowledge, name, selection):
    path = tmp_path / (name + "-knowledge.json")
    path.write_text(json.dumps({**knowledge, "schema_version": PROJECT_KNOWLEDGE_INPUT_V4,
                                "run_memory_selection": selection}))
    return str(path)


def test_unknown_effect_and_verified_fulfilment_stay_separate_memory_facts(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = case.input_path.parent / knowledge_path
    knowledge = json.loads(knowledge_path.read_text())
    original = (case.repo / "src/calc.py").read_bytes()
    good = {"edits": [{"path": "src/calc.py", "old": "a - b", "new": "a + b"}]}
    command = LOCAL_EDIT_COMMAND + json.dumps({"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [good]})
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "acceptance-only")
    runs = {}
    with provider_endpoint(commands=[command, command]) as (endpoint, requests):
        mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
        assert mini.is_file()
        declaration["config"]["model"] = "gpt-4o-mini"
        declaration["worker"] = {"provider": "mini", "command": [str(mini)], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1", "input_profile": LOCAL_EDIT_PROFILE_V5,
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                           "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        for name in ("uncertain", "fulfilled"):
            if name == "fulfilled":
                create_executing_oracle(tmp_path / "harness", repo=case.repo,
                    base_revision=declaration["config"]["base_revision"], target_paths=("src/calc.py",),
                    command=(sys.executable, "-B", "-c",
                             "from src.calc import add; assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(0,0)])"))
            current = replace(case, run_root=tmp_path / (name + "-run"), input_path=tmp_path / (name + ".json"))
            current.input_path.write_text(json.dumps({**declaration, "run_id": name,
                "knowledge_path": _selected_knowledge(tmp_path, knowledge, name, "ALL")}))
            code, pending = current.start()
            assert code == 3, pending
            code, result = current.approve(pending)
            assert code == 0 and result["project_memory"]["status"] == "RECORDED", result
            runs[name] = (current, result)
    assert [runs[name][1]["outcome_status"] for name in ("uncertain", "fulfilled")] == ["INFRA_ERROR", "FULL"]
    assert len(requests) == 2 and (case.repo / "src/calc.py").read_bytes() == original

    memory = ProjectMemoryStore(tmp_path / "state", read_only=True)
    events = memory.inventory()
    observed = {}
    for name, (current, result) in runs.items():
        recorded, = [receipt for event, receipt in events if event["kind"] == "OUTCOME_RECORDED"
                     and receipt == result["project_memory"]["event"]]
        job = memory.read(recorded)["job_key"]
        observation, = [event["payload"] for event, _ in events
                        if event["kind"] == "OBSERVED" and event["job_key"] == job]
        assert observation["outcome_ref"] == recorded
        records = RunRecordStore(current.run_root, mutation_fence=FileSnapshotFence(current.run_root / "run-coordinator"))
        attempt, = load_run_state(records).attempts
        proof = attempt.result.structured_outcome["payload"]["verification"]["payload"]
        assert observation["attempts"][0]["attempt_id"] == proof["attempt_id"]
        observed[name] = (observation, proof)

    uncertain, proof = observed["uncertain"]
    assert proof["c1"]["infra_error"] is True
    assert uncertain["requirement"]["outcome"] == "UNCERTAIN" and uncertain["requirement"]["basis"] == []
    assert uncertain["uncertainties"] == [{"attempt_id": proof["attempt_id"], "reasons": ["VERIFICATION_INFRASTRUCTURE"]}]
    fulfilled, proof = observed["fulfilled"]
    assert fulfilled["requirement"]["outcome"] == "FULFILLED" and fulfilled["recovery"] is None
    assert fulfilled["uncertainties"] == []
    assert {"attempt_id": proof["attempt_id"], "role": "INDEPENDENT_ORACLE",
            "ref": proof["c1"]["oracle_result_ref"]} in fulfilled["requirement"]["basis"]

    project = open_gold_project(tmp_path / "state")
    frames = {}
    for selection in ("ALL", "SUCCESS_ONLY", "FAILURE_ONLY"):
        name = "read-" + selection.lower()
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({**declaration, "run_id": name,
            "knowledge_path": _selected_knowledge(tmp_path, knowledge, name, selection)}))
        frozen = freeze_gold_inputs(declaration_path=path, project=project, run_root=tmp_path / (name + "-run"))
        snapshot = frozen.data["source_snapshot"]
        assert snapshot["project_memory"]["profile"] == OWNER_LIFECYCLE_V3
        frame = read_active_memory(snapshot["project_memory"], source_snapshot=memory_source_basis(snapshot),
                                   run_memory_selection=selection)["active_memory_frame"]
        # The module and its symbols are separate elements sharing one path history.
        memories = [item["memory"] for item in frame["elements"] if item["path"] == "src/calc.py"]
        assert memories
        frames[selection] = (snapshot, frame, memories)

    def outcomes(memories, view):
        return {tuple(sorted((entry["run_id"], entry["observation"]["requirement"]["outcome"])
                             for entry in memory[view]["task_outcomes"])) for memory in memories}

    snapshot, frame, memories = frames["ALL"]
    assert outcomes(memories, "Episodic") == {(("fulfilled", "FULFILLED"), ("uncertain", "UNCERTAIN"))}
    assert outcomes(memories, "Defect") == {()}  # An infrastructure failure is not an element defect.
    assert [item["status"] for item in frame["layers"]["learned"]] == ["CONFIRMED"]
    assert outcomes(frames["SUCCESS_ONLY"][2], "Episodic") == {(("fulfilled", "FULFILLED"),)}
    assert outcomes(frames["FAILURE_ONLY"][2], "Episodic") == {()}
    assert frames["FAILURE_ONLY"][1]["layers"]["learned"] == []

    information = source_experience_delivery(snapshot)["information"]
    values = [json.loads(base64.urlsafe_b64decode(item["content_base64url"] +
              "=" * (-len(item["content_base64url"]) % 4))) for item in information["items"]]
    neutral, = [item for item in values if "memory_kinds" in item]
    assert {(entry["run_id"], entry["requirement_outcome"]) for element in neutral["elements"]
            for entry in element["episodes"]} == {("fulfilled", "FULFILLED"), ("uncertain", "UNCERTAIN")}
    assert "verification_ref" not in json.dumps(neutral) and "synapse.stage4.gold." not in json.dumps(neutral)

    before = memory.inventory()
    current, result = runs["fulfilled"]
    code, resumed = current.cli("project", "resume", "--run-dir", current.run_root)
    assert code == 0 and resumed == result
    assert memory.inventory() == before and len(requests) == 2
