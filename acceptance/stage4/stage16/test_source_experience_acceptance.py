"""A real unsuccessful source operation remains useful, typed local evidence."""

import json
from pathlib import Path
import sys

from acceptance.stage4.stage16._source_inputs import prepare, learn, recall
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.stage10.context_codec import decode_base64url


def test_failed_claim_retains_materials_output_and_verified_parts_after_restart(tmp_path):
    repo, state, path = prepare(tmp_path, execute=True)
    value = json.loads(path.read_text())
    claim = value["claim"]
    claim.update(kind="VERIFICATION_RECIPE", recipe={
        "command": [sys.executable, "-B", "-c", 'print("double partial-result", flush=True); raise SystemExit(3)'],
        "expectation": {"expected_exit_codes": [0], "expected_nonzero_exit": False,
            "combined_output_contains": ["complete-result"], "combined_output_not_contains": [], "timeout_seconds": 10}})
    path.write_text(json.dumps(value))
    code, failure = learn(state, path)
    assert code == 2 and failure["status"] == "REJECTED", failure
    # Historical reads must not depend on the continued existence of operator
    # input files, or on an unchanged live working tree.
    for item in value["files"]:
        Path(item["path"]).unlink()
    original = (repo / "calc.py").read_bytes()
    (repo / "calc.py").write_text("working tree changed")
    assert learn(state, path) == (code, failure)
    code, recalled = recall(state, tmp_path, claim)
    assert code == 0, recalled
    record = recalled["selected"][0]["experience"]
    assert record["status"] == "REJECTED"
    assert record["verification"] == "UNVERIFIED" and record["applicability"] == "UNASSESSED"
    assert record["execution"] == "EXITED_NONZERO"
    assert record["command_result"]["returncode"] == 3
    assert record["command_result"]["stdout"] == "double partial-result\n"
    assert record["bindings"][0]["qualname"] == "double"
    retained = {item["ref"]["sha256"]: decode_base64url(item["content_base64url"]) for item in record["retained"]}
    assert retained[record["sources"][0]["ref"]["sha256"]] == original
    assert all(item["ref"]["sha256"] in retained for item in value["files"])
    assert not open_gold_project(state).library.search_index()


def test_absent_symbol_preserves_sources_and_the_preceding_verified_symbol(tmp_path):
    _, state, path = prepare(tmp_path)
    value = json.loads(path.read_text())
    value["claim"]["symbols"].append({**value["claim"]["symbols"][0], "qualname": "imagined"})
    path.write_text(json.dumps(value))
    code, failure = learn(state, path)
    assert code == 2 and failure["status"] == "REFUSED", failure
    code, recalled = recall(state, tmp_path, value["claim"])
    assert code == 0, recalled
    record = recalled["selected"][0]["experience"]
    assert [item["qualname"] for item in record["bindings"]] == ["double"]
    assert len(record["sources"]) == 1 and record["execution"] == "NOT_OBSERVED"
    assert not open_gold_project(state).library.search_index()
