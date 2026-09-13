"""Heavy shard: real element-owner maintenance and recovery before worker effects."""
import base64
import json
from pathlib import Path
import pytest

from acceptance.stage4.stage16._source_inputs import consumer_case
from synapse.experiments.gold import project_agents
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.project_memory_store import ProjectMemoryStore
from synapse.experiments.gold.project_model import MEMORY_KINDS
from synapse.experiments.gold.run_inputs import freeze_gold_inputs, FrozenGoldInputs, FROZEN_INPUT_SCHEMA_V5
from synapse.experiments.gold.source_snapshot import memory_source_basis, source_experience_delivery
from synapse.experiments.gold.source_verification import canonical


def test_interrupted_memory_job_resumes_its_original_history_without_an_effect(tmp_path, monkeypatch):
    case, _ = consumer_case(tmp_path, automatic_targets=True)
    project = open_gold_project(tmp_path / "state")
    original = {path: path.read_bytes() for path in (case.repo / "src").glob("*.py")}
    build = project_agents._frame_payload

    def interrupted(*args, **kwargs):
        raise RuntimeError("acceptance interruption during pure memory maintenance")

    monkeypatch.setattr(project_agents, "_frame_payload", interrupted)
    with pytest.raises(RuntimeError, match="pure memory maintenance"):
        freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root)
    store = ProjectMemoryStore(tmp_path / "state", read_only=True)
    assert {event["kind"] for event, _ in store.inventory()} == {"REQUESTED", "STARTED"}
    monkeypatch.setattr(project_agents, "_frame_payload", build)
    frozen = freeze_gold_inputs(declaration_path=case.input_path, project=project, run_root=case.run_root)
    snapshot = frozen.data["source_snapshot"]
    frame = project_agents.read_active_memory(snapshot["project_memory"],
        source_snapshot=memory_source_basis(snapshot), run_memory_selection=snapshot["run_memory_selection"])
    assert {event["kind"] for event, _ in store.inventory()} == {"REQUESTED", "STARTED", "FRAME_COMPLETED"}
    assert {node["kind"] for node in frame["project_model"]["nodes"]} == {"System", "Subsystem", "Element"}
    assert frame["owners"] and all(owner["state"] == "IDLE" for owner in frame["owners"])
    for element in frame["active_memory_frame"]["elements"]:
        assert set(element["memory"]) == set(MEMORY_KINDS)
        assert element["development_delta"]["status"] == "AWAITING_FRESH_VERIFICATION"
        assert element["current_state"]["binding"]["path"] == element["path"]
    assert {path: path.read_bytes() for path in original} == original
    assert not case.run_root.exists()  # Maintenance completed before any worker/run allocation.

    information = source_experience_delivery(snapshot)["information"]
    values = [json.loads(base64.urlsafe_b64decode(item["content_base64url"] +
              "=" * (-len(item["content_base64url"]) % 4))) for item in information["items"]]
    neutral, = [item for item in values if "memory_kinds" in item]
    assert neutral["elements"]
    for element in neutral["elements"]:
        effects = [{"kind": "PATH_MODIFIED", "disposition": "EXPECTED", "subject_path": element["path"]}]
        assert element["goal"] == {"expected_effects": effects, "status": "DECLARED"}
        assert element["development_delta"] == {"expected_effects": effects, "status": "AWAITING_FRESH_VERIFICATION"}
        assert element["context"] == {"repository_revision": neutral["repository_revision"], "allowed_scope": ["src/calc.py"]}
    assert "verification_ref" not in json.dumps(neutral)
    assert "synapse.stage4.gold." not in json.dumps(neutral)

    # Schema transitions preserve the declared owner lifecycle on every read.
    without_memory = frozen.data
    without_memory["source_snapshot"] = memory_source_basis(snapshot)
    with pytest.raises(ValueError, match="frozen input version"):
        FrozenGoldInputs(canonical(without_memory))
    historical = frozen.data
    historical["schema_version"] = FROZEN_INPUT_SCHEMA_V5
    historical.pop("planning_profile")
    with pytest.raises(ValueError, match="frozen input version"):
        FrozenGoldInputs(canonical(historical))
