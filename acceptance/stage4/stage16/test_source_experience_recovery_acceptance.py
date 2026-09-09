"""Unknown effects, infrastructure errors and missing retained bytes stay honest."""

import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from acceptance.stage4.stage16._source_inputs import prepare, learn, recall
from synapse.experiments.gold import source_verification as verifier
from synapse.experiments.gold import source_ingestion as ingestion
from synapse.experiments.gold.source_ingestion import execute_source_ingestion
from synapse.experiments.gold.stage10.context_codec import decode_canonical


def _recipe(tmp_path, program):
    repo, state, path = prepare(tmp_path, execute=True)
    value = json.loads(path.read_text())
    value["claim"].update(kind="VERIFICATION_RECIPE", recipe={
        "command": [sys.executable, "-B", "-c", program], "expectation": {
            "expected_exit_codes": [0], "expected_nonzero_exit": False, "combined_output_contains": ["done"],
            "combined_output_not_contains": [], "timeout_seconds": 10}})
    path.write_text(json.dumps(value))
    return repo, state, path, value["claim"]


def test_effect_before_process_interruption_is_never_repeated_or_classified_as_a_method_failure(tmp_path, monkeypatch):
    counter = tmp_path / "effect-count"
    program = f'from pathlib import Path; p = Path({str(counter)!r}); p.write_text(p.read_text() + "x" if p.exists() else "x"); print("done")'
    _, state, path, claim = _recipe(tmp_path, program)
    original = verifier.run_expected_command

    def die_after_command(*args, **kwargs):
        original(*args, **kwargs)
        raise SystemExit("acceptance interruption before observation commit")

    monkeypatch.setattr(verifier, "run_expected_command", die_after_command)
    with pytest.raises(SystemExit):
        execute_source_ingestion(state_root=state, input_path=path)
    assert counter.read_text() == "x"
    assert learn(state, path)[1]["status"] == "INTERRUPTED"
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["execution"] == "STARTED_UNKNOWN" and record["command_result"] is None
    assert record["verification"] == "UNVERIFIED" and record["applicability"] == "UNASSESSED"
    assert record["bindings"] and record["sources"]
    assert counter.read_text() == "x"


def test_cleanup_failure_preserves_the_completed_command_without_proving_the_whole_claim(tmp_path, monkeypatch):
    _, state, path, claim = _recipe(tmp_path, 'print("done")')
    original = verifier.cleanup_worktree

    def failed_cleanup(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("acceptance cleanup observation failed")

    monkeypatch.setattr(verifier, "cleanup_worktree", failed_cleanup)
    code, failure = execute_source_ingestion(state_root=state, input_path=path)
    assert code == 2 and failure["status"] == "INFRA_ERROR", failure
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["status"] == "INFRA_ERROR" and record["execution"] == "EXITED_ZERO"
    assert record["command_result"]["status"] == "PASS" and record["command_result"]["stdout"] == "done\n"
    assert record["verification"] == "UNVERIFIED" and record["bindings"]


@pytest.mark.parametrize("damage", ["source", "last_observation"])
def test_committed_corruption_is_refused_instead_of_returning_a_smaller_history(tmp_path, damage):
    _, state, path, claim = _recipe(tmp_path, 'print("partial")')
    assert learn(state, path)[0] == 2
    root = state / "source-operations" / hashlib.sha256(claim["operation_id"].encode()).hexdigest()
    observations = sorted(root.glob("observation-*"))
    if damage == "last_observation":
        shutil.rmtree(observations[-1])
    else:
        for directory in observations:
            event = decode_canonical((directory / "record.json").read_bytes())["payload"]["observation"]
            if event["phase"] == "SOURCE_CAPTURED":
                (directory / event["data"]["ref"]["sha256"]).write_bytes(b"altered")
                break
        else:
            raise AssertionError("source was not retained")
    code, result = recall(state, tmp_path, claim)
    assert code == 2 and result["status"] == "REFUSED", result
    assert "selected" not in result


def test_uncommitted_command_start_cannot_execute_and_preserves_committed_parts(tmp_path, monkeypatch):
    marker = tmp_path / "forbidden-effect"
    program = f'from pathlib import Path; Path({str(marker)!r}).write_text("done"); print("done")'
    _, state, path, claim = _recipe(tmp_path, program)
    original = ingestion.stage_snapshot_transaction

    def refuse_start(*args, **kwargs):
        record = decode_canonical(kwargs["members"]["record.json"])
        if record["payload"].get("observation", {}).get("phase") == "COMMAND_STARTED":
            raise OSError("acceptance checkpoint write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(ingestion, "stage_snapshot_transaction", refuse_start)
    assert execute_source_ingestion(state_root=state, input_path=path)[0] == 2
    assert not marker.exists()
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["status"] == "INTERRUPTED" and record["checkpoint_complete"] is False
    assert record["bindings"] and record["command_result"] is None
    assert learn(state, path)[1]["status"] == "INTERRUPTED"
    assert not marker.exists()


def test_input_read_failure_retains_the_preceding_input_without_claiming_a_method_failed(tmp_path):
    _, state, path, claim = _recipe(tmp_path, 'print("done")')
    value = json.loads(path.read_text())
    Path(value["files"][1]["path"]).unlink()
    code, failure = learn(state, path)
    assert code == 2 and failure["status"] == "INFRA_ERROR", failure
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["status"] == "INFRA_ERROR" and record["execution"] == "NOT_OBSERVED"
    assert [item["ref"] for item in record["retained"]] == [value["files"][0]["ref"]]
    assert record["uncaptured_sources"] == claim["sources"]


def test_rejected_observation_before_journal_mutation_does_not_break_the_retained_prefix(tmp_path, monkeypatch):
    _, state, path, claim = _recipe(tmp_path, 'print("done")')
    original = ingestion._checkpoint

    def refuse_oversized_observation(root, name, payload, *args, **kwargs):
        if payload.get("observation", {}).get("phase") == "COMMAND_FINISHED":
            raise ValueError("source checkpoint exceeds its retained byte budget")
        return original(root, name, payload, *args, **kwargs)

    monkeypatch.setattr(ingestion, "_checkpoint", refuse_oversized_observation)
    code, failure = execute_source_ingestion(state_root=state, input_path=path)
    assert code == 2 and failure["status"] == "REFUSED", failure
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["status"] == "REFUSED" and record["checkpoint_complete"] is True
    assert record["execution"] == "STARTED_UNKNOWN" and record["command_result"] is None
    assert record["sources"] and record["bindings"]


def test_mismatched_input_is_retained_with_its_actual_identity_and_never_used_for_verification(tmp_path):
    from synapse.experiments.gold.stage10.context_codec import decode_base64url
    _, state, path, claim = _recipe(tmp_path, 'print("done")')
    value = json.loads(path.read_text())
    wrong_bytes = b"the observed policy does not match the declared policy"
    Path(value["files"][0]["path"]).write_bytes(wrong_bytes)
    code, failure = learn(state, path)
    assert code == 2 and failure["status"] == "REFUSED", failure
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["inputs"][0]["input_ref"] == value["files"][0]["ref"]
    assert record["inputs"][0]["identity_matches"] is False
    assert record["retained"][0]["ref"] == record["inputs"][0]["observed_ref"]
    assert decode_base64url(record["retained"][0]["content_base64url"]) == wrong_bytes
    assert record["observations"] == [] and record["command_result"] is None


def test_legacy_failed_checkpoint_reopens_without_inventing_missing_source_bytes(tmp_path):
    from synapse.experiments.gold.admission_journal import FileSnapshotFence
    from synapse.experiments.gold.persistence import stage_snapshot_transaction, commit_snapshot_transaction, store_transaction
    _, state, path, claim = _recipe(tmp_path, 'print("partial")')
    # Generate the historical command result with the real verifier, then
    # encode only the fields the previous journal format actually retained.
    code, failure = learn(state, path)
    assert code == 2 and failure["status"] == "REJECTED", failure
    root = state / "source-operations" / hashlib.sha256(claim["operation_id"].encode()).hexdigest()
    shutil.rmtree(root)
    root.mkdir()
    fence = FileSnapshotFence(root / "fence")
    records = {"started": {"schema_version": ingestion.SOURCE_INGESTION_V1, "claim": claim,
               "claim_sha256": hashlib.sha256(verifier.canonical(claim)).hexdigest()}, "result": failure}
    with fence.exclusive() as guard:
        for name, payload in records.items():
            raw = verifier.canonical(payload)
            with store_transaction(fence, guard=guard) as ticket:
                members = stage_snapshot_transaction(root, transaction_id=name, members={"record.json": raw}, ticket=ticket)
                commit_snapshot_transaction(root, transaction_id=name, members=members,
                    boundary_id=hashlib.sha256(raw).hexdigest(), marker_sha256=hashlib.sha256(raw).hexdigest(), ticket=ticket)
    before = (root / "result" / "record.json").read_bytes()
    assert learn(state, path) == (code, failure)
    code, found = recall(state, tmp_path, claim)
    assert code == 0, found
    record = found["selected"][0]["experience"]
    assert record["origin"] == "LEGACY_RESULT" and record["execution"] == "EXITED_ZERO"
    assert record["command_result"] == failure["command_result"]
    assert record["uncaptured_sources"] == claim["sources"] and record["retained"] == []
    assert (root / "result" / "record.json").read_bytes() == before
