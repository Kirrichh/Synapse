"""Canonical source ingestion with real stores and a fresh interpreter."""
import json

from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.source_verification import SOURCE_CLAIM_V1
from acceptance.stage4.stage16._source_inputs import prepare, learn


def test_source_is_published_by_canonical_cli_and_reopens_without_a_gold_producer(tmp_path):
    repo, state, input_path = prepare(tmp_path)
    code, result = learn(state, input_path)
    assert code == 0, result
    assert result["status"] == "PUBLISHED"
    candidates = result["knowledge"]["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["attestation"]["task_contract_ref"]["schema_id"] == SOURCE_CLAIM_V1
    assert candidates[0]["bindings"][0]["qualname"] == "double"
    project = open_gold_project(state)
    assert len(project.library.search_index()) == 1
    code, reopened = learn(state, input_path)
    assert code == 0, reopened
    assert reopened == result
    assert len(open_gold_project(state).library.search_index()) == 1


def test_absent_symbol_never_creates_admitted_knowledge(tmp_path):
    repo, state, input_path = prepare(tmp_path)
    data = json.loads(input_path.read_text())
    data["claim"]["symbols"][0]["qualname"] = "imagined_function"
    input_path.write_text(json.dumps(data))
    code, result = learn(state, input_path)
    assert code != 0
    assert len(open_gold_project(state).library.search_index()) == 0


def test_operation_identity_cannot_be_reused_for_a_different_claim(tmp_path):
    repo, state, input_path = prepare(tmp_path)
    code, result = learn(state, input_path)
    assert code == 0, result
    data = json.loads(input_path.read_text())
    data["claim"]["symbols"][0]["qualname"] = "another_function"
    input_path.write_text(json.dumps(data))
    code, result = learn(state, input_path)
    assert code != 0
    assert "identity" in result["reason"]
    assert len(open_gold_project(state).library.search_index()) == 1


def test_repeating_the_same_assertion_is_not_new_knowledge(tmp_path):
    _, state, input_path = prepare(tmp_path)
    code, original = learn(state, input_path)
    assert code == 0, original
    value = json.loads(input_path.read_text())
    value['claim']['operation_id'] = 'another-source-operation'
    input_path.write_text(json.dumps(value))
    code, result = learn(state, input_path)
    assert code == 0 and result['status'] == 'ALREADY_KNOWN', result
    assert result['publication'] == original['publication']
    assert len(open_gold_project(state).library.search_index()) == 1
    assert learn(state, input_path) == (code, result)
