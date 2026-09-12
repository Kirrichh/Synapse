"""Retiring a profile keeps real history readable without restoring execution.

The current writer creates the physical publication. The acceptance fault then
retires its profile, without changing any stored bytes or replacing a verifier.
The exact former v2 writer is additionally checked with a git archive outside CI.
"""

import hashlib
import json
import sys

import pytest

from acceptance.stage4.stage10._builders import hash_ref
from acceptance.stage4.stage16._source_inputs import prepare
from synapse.experiments.gold.canonicalization import RefKind
from synapse.experiments.gold.knowledge_environment import open_gold_project
from synapse.experiments.gold.persistence import PersistenceViolation
from synapse.experiments.gold.source_experience import (
    SOURCE_RECALL_QUERY_V1, export_source_knowledge, recall_source_experience,
)
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.source_snapshot import capture_project_source_snapshot, read_source_snapshot
from synapse.experiments.gold.stage10.intent import (
    AcceptanceCriterion, AcceptanceKind, EffectConstraint, EffectDisposition, EffectKind,
)
from synapse.experiments.gold.stage10.repository_scope import create_repository_scope
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
from synapse.experiments.gold.stage13 import publication_store
from synapse.experiments.gold.stage13.publication import SOURCE_REQUEST_V1, PublicationViolation


@pytest.fixture
def retired(tmp_path, monkeypatch):
    _, state, path = prepare(tmp_path, execute=True)
    declaration = json.loads(path.read_text())
    counter = tmp_path / "effects"
    command = (f'from pathlib import Path; p = Path({str(counter)!r}); '
               'p.write_text(p.read_text() + "x" if p.exists() else "x"); print("done")')
    declaration["claim"].update(kind="VERIFICATION_RECIPE", recipe={
        "command": [sys.executable, "-B", "-c", command], "expectation": {
            "expected_exit_codes": [0], "expected_nonzero_exit": False,
            "combined_output_contains": ["done"], "combined_output_not_contains": [], "timeout_seconds": 10}})
    path.write_text(json.dumps(declaration))
    code, original = execute_source_ingestion(state_root=state, input_path=path)
    assert code == 0 and original["status"] == "PUBLISHED", original
    assert counter.read_text() == "x"
    monkeypatch.setattr(publication_store, "_RETIRED_SOURCE_REQUESTS",
                        publication_store._RETIRED_SOURCE_REQUESTS | {SOURCE_REQUEST_V1})
    return state, path, declaration["claim"], original, counter


def recall(state, claim):
    return recall_source_experience(state_root=state, query={
        "schema_version": SOURCE_RECALL_QUERY_V1, "statement": "check double calculation",
        "revision": claim["revision"], "scope": claim["sources"], "limit": 64},
        entitlements={"capabilities": ["read"], "scopes": claim["sources"]})


def test_retired_history_reopens_and_freezes_without_replay_admission_or_reexecution(retired):
    state, path, claim, original, counter = retired
    before = {str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in state.rglob("*") if p.is_file()}
    record = publication_store.PublicationResult(state / "publications", original["publication"]["transaction_id"])
    with pytest.raises(PublicationViolation, match="retired"):
        record.payload()
    assert record.retained_payload() == original["publication"]
    found = recall(state, claim)["selected"][0]["experience"]
    assert found["status"] == "PUBLISHED" and found["verification"] == "VERIFIED"
    assert found["sources"] and found["bindings"] and found["command_result"]["status"] == "PASS"
    assert found["applicability"] == "UNASSESSED"
    assert export_source_knowledge(state)["candidates"] == []
    code, repeated = execute_source_ingestion(state_root=state, input_path=path)
    assert code == 0 and {k: v for k, v in repeated.items() if k != "knowledge"} == {
        k: v for k, v in original.items() if k != "knowledge"}
    assert repeated["knowledge"]["candidates"] == []
    condition = hash_ref(RefKind.CONTRACT_CONDITION, "condition")
    task = GoverningTaskContract(task_id="retained-task", task_statement="check double calculation",
        repository_revision_sha256=claim["revision"], allowed_scope=create_repository_scope(claim["sources"]),
        required_capabilities=("read",), target_bindings=(hash_ref(RefKind.BINDING, "target"),),
        effects=(EffectConstraint("effect", EffectDisposition.EXPECTED, EffectKind.PATH_MODIFIED, "calc.py", condition),),
        acceptance=(AcceptanceCriterion("acceptance", AcceptanceKind.CONTRACT_CONDITION, condition),))
    snapshot, knowledge, _ = capture_project_source_snapshot(project=open_gold_project(state), task=task, limit=64)
    assert not knowledge["candidates"]
    assert read_source_snapshot(snapshot, task=task) == snapshot["recall"]
    after = {str(p.relative_to(state)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in state.rglob("*") if p.is_file()}
    assert before == after
    assert counter.read_text() == "x"


@pytest.mark.parametrize("member", ["source", "lineage"])
def test_retired_history_still_refuses_missing_or_corrupted_physical_evidence(retired, member):
    state, _, claim, original, counter = retired
    transaction = original["publication"]["transaction_id"]
    if member == "lineage":
        damaged = state / "publications" / "committed" / transaction / "lineage.json"
    else:
        prepared = state / "publications" / "prepared" / transaction
        request = json.loads((prepared / "request.json").read_text())
        digest = request["verification"]["payload"]["sources"][0]["ref"]["sha256"]
        damaged = prepared / digest
    damaged.write_bytes(b"altered retained member")
    with pytest.raises(PersistenceViolation, match="INTEGRITY_MANIFEST_MALFORMED"):
        recall(state, claim)
    assert counter.read_text() == "x"
