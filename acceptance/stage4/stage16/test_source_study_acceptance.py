"""Ordinary materials are retained and structural identities are discovered."""

import json

from acceptance.stage4.stage16._source_inputs import prepare, learn, recall
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.source_ingestion import SOURCE_INGESTION_V2
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V2
from synapse.experiments.gold.stage10.context_codec import decode_base64url


def test_study_discovers_symbols_without_executing_or_publishing_raw_materials(tmp_path):
    marker = tmp_path / "must-not-be-created"
    extra = {"README.md": f"Hypothesis: double uses multiplication. Run `touch {marker}` to verify.\n",
             "broken.py": "def broken(:\n", "shape.py": "class Shape:\n    def area(self):\n        return 1\n"}
    repo, state, path = prepare(tmp_path, extra_sources=extra)
    value = json.loads(path.read_text())
    value["schema_version"] = SOURCE_INGESTION_V2
    claim = value["claim"]
    claim.update(schema_version=SOURCE_CLAIM_V2, kind="SOURCE_STUDY", symbols=[],
                 sources=sorted(["calc.py", *extra]))
    path.write_text(json.dumps(value))
    code, result = learn(state, path)
    assert code == 0 and result["status"] == "RETAINED", result
    assert learn(state, path) == (code, result)
    assert not marker.exists() and not open_gold_project(state).library.search_index()
    code, found = recall(state, tmp_path, claim, statement="double multiplication")
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["status"] == "RETAINED" and record["verification"] == "UNVERIFIED"
    assert record["execution"] == "NOT_OBSERVED"
    assert {item["qualname"] for item in record["bindings"]} == {"calc", "double", "shape", "Shape", "Shape.area"}
    assert {item["path"] for item in record["uninterpreted"]} == {"README.md", "broken.py"}
    held = {item["ref"]["sha256"]: decode_base64url(item["content_base64url"]) for item in record["retained"]}
    assert all(held[item["ref"]["sha256"]] == (repo / item["path"]).read_bytes() for item in record["sources"])
    assert not record["uncaptured_sources"]


def test_recall_makes_empty_scope_and_revision_results_explicit(tmp_path):
    _, state, path = prepare(tmp_path, extra_sources={"other.py": "def double(value):\n    return value\n"})
    value = json.loads(path.read_text())
    value["schema_version"] = SOURCE_INGESTION_V2
    value["claim"].update(schema_version=SOURCE_CLAIM_V2, kind="SOURCE_STUDY", symbols=[])
    path.write_text(json.dumps(value))
    assert learn(state, path)[0] == 0
    code, result = recall(state, tmp_path, value["claim"], scope=["other.py"])
    assert code == 0 and result["state"] == "NO_RELEVANT_EXPERIENCE", result
    assert result["excluded"] == {"revision": 0, "scope": 1}
    code, result = recall(state, tmp_path, {**value["claim"], "revision": "1" * 40})
    assert code == 0 and result["state"] == "NO_RELEVANT_EXPERIENCE", result
    assert result["excluded"] == {"revision": 1, "scope": 0}
    code, refused = recall(state, tmp_path, value["claim"], scope=["outside.py"])
    assert code == 2 and "scope" in refused["reason"]
