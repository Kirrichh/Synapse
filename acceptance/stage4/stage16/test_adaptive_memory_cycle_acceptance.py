"""Canonical CLI learning, adaptive reuse on a later revision, and finished resume."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import pytest

from acceptance.agents.coding_agents import use_model_agent
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from acceptance.stage4.stage16._completed_attempt import completed_attempt
from acceptance.stage4.stage16._executing_oracle import create_executing_oracle
from acceptance.stage4.stage16._source_inputs import consumer_case, learn, recall
from synapse.experiments.gold.contracts import ActorIdentity, RepositoryRevision
from synapse.experiments.gold.provenance import OracleObservation, ORACLE_OBSERVATION_V1
from synapse.experiments.gold.project_memory_selection import PROJECT_KNOWLEDGE_INPUT_V4
from synapse.experiments.gold.runner.run_progress import AttemptProgressPhase, load_attempt_progress, require_progress_payload
from synapse.experiments.gold.runner.c1_boundary import restore_c1_authority_receipt
from synapse.experiments.gold.source_verification import canonical, source_ref
from synapse.experiments.gold.stage10.task_contract import GoverningTaskContract
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V5, LOCAL_EDIT_PROPOSAL_V1


def _advance_after_learning(case, declaration, knowledge_path, *, same_source=False):
    """Change only text outside the learned function after learning completed."""
    target = case.repo / "src/calc.py"
    advanced = target.read_bytes() if same_source else b"# Later revision: the learned function is byte-identical.\n" + target.read_bytes()
    if same_source:
        (case.repo / 'unrelated.txt').write_text('another revision\n')
    else:
        target.write_bytes(advanced)
    subprocess.run(["git", "add", "unrelated.txt" if same_source else "src/calc.py"], cwd=case.repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "advance outside learned function"],
                   cwd=case.repo, check=True, capture_output=True)
    revision = RepositoryRevision.git_commit(subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=case.repo, text=True).strip())

    previous = GoverningTaskContract.from_dict(declaration["task_contract"])
    task = replace(previous, repository_revision_sha256=revision.git_sha)
    observed = subprocess.run(
        [sys.executable, "-B", "-c", "from src.calc import add; print(add(2, 3)); assert add(2, 3) == 5"],
        cwd=case.repo, capture_output=True, text=True, timeout=10)
    assert observed.returncode != 0 and observed.stdout.strip() == "-1"
    raw = canonical({"task_contract_ref": task.reference.to_dict(), "returncode": observed.returncode,
        "stdout": observed.stdout, "stderr": observed.stderr, "meaning": "consumer-base-reproduction"})
    observation_ref = source_ref(raw, "source-consumer-precondition/v1")
    for ref, data in ((observation_ref, raw), (task.reference, task.canonical_bytes())):
        path = knowledge_path.parent / ref.sha256
        path.write_bytes(data)
        knowledge = json.loads(knowledge_path.read_text())
        if all(item["ref"]["sha256"] != ref.sha256 for item in knowledge["files"]):
            knowledge["files"].append({"ref": ref.to_dict(), "path": str(path)})
        knowledge_path.write_text(json.dumps(knowledge))

    oracle = OracleObservation(ORACLE_OBSERVATION_V1, ActorIdentity("source-consumer-oracle"),
                               revision, task.reference, observation_ref)
    declaration["run_id"] = "changed-revision-reuse"
    declaration["config"]["base_revision"] = revision.git_sha
    declaration["task_contract"] = task.to_dict()
    declaration["observation"].update(
        base_revision=revision.to_dict(), task_contract_ref=task.reference.to_dict(),
        source_refs=[observation_ref.to_dict()], verification_refs=[observation_ref.to_dict()],
        oracle_observation=oracle.to_dict())
    return declaration, revision, advanced


def _publish_current_source(case, source_input, revision):
    declaration = json.loads(source_input.read_text())
    declaration["claim"].update(operation_id="learn-calculation-advanced", revision=revision.git_sha)
    source_input.write_text(json.dumps(declaration))
    code, publication = learn(case.state_root, source_input)
    assert code == 0, publication
    return declaration


def _run(current, *, expected="FULL"):
    code, pending = current.start()
    assert code == 3, pending
    code, result = current.approve(pending)
    assert code == 0, result
    assert result["project_memory"]["status"] == "RECORDED", result
    assert result["outcome_status"] == expected, result
    return result


def _verified_patch(frozen, records, attempt):
    progress = load_attempt_progress(records, manifest=frozen.manifest, context=attempt.context)
    raw, ref = require_progress_payload(progress.get(AttemptProgressPhase.C1_COMPLETED))
    receipt = restore_c1_authority_receipt(raw, expected_ref=ref)
    digest = receipt.verified_patch_sha256
    assert digest is not None
    return digest


@pytest.mark.parametrize('same_source', [False, True])
def test_changed_revision_reuse_through_canonical_cli(tmp_path, monkeypatch, same_source):
    case, _ = consumer_case(tmp_path, automatic_targets=True, verification_commands=True)
    source_input = tmp_path / "learn.json"
    case = replace(case, cli_timeout_seconds=3600)
    declaration = json.loads(case.input_path.read_text())
    knowledge_path = Path(declaration["knowledge_path"])
    if not knowledge_path.is_absolute():
        knowledge_path = case.input_path.parent / knowledge_path
    selected = tmp_path / "selected-knowledge.json"
    selected.write_text(json.dumps({**json.loads(knowledge_path.read_text()),
        "schema_version": PROJECT_KNOWLEDGE_INPUT_V4, "run_memory_selection": "ALL"}))
    original = (case.repo / "src/calc.py").read_bytes()
    create_executing_oracle(tmp_path / "harness", repo=case.repo,
        base_revision=declaration["config"]["base_revision"], target_paths=("src/calc.py",),
        command=(sys.executable, "-B", "-c",
            "from src.calc import add; assert all(add(a,b)==a+b for a,b in [(2,3),(-2,3),(4,3),(0,0)])"))
    good = {"edits": [{"path": "src/calc.py", "old": "a - b", "new": "a + b"}]}
    command = LOCAL_EDIT_COMMAND + json.dumps({"schema_version": LOCAL_EDIT_PROPOSAL_V1,
                                                "alternatives": [good]})
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "acceptance-only")
    with provider_endpoint(commands=[command, command]) as (endpoint, requests):
        chosen = use_model_agent({**declaration, "config": dict(declaration["config"])},
                                 tmp_path / "learn-agent", endpoint=endpoint, protocol=LOCAL_EDIT_PROFILE_V5)
        learn_case = replace(case, run_root=tmp_path / "learn-run", input_path=tmp_path / "learn-input.json")
        learn_case.input_path.write_text(json.dumps({**chosen, "run_id": "learn",
                                                     "knowledge_path": str(selected)}))
        learn_result = _run(learn_case)
        assert len(requests) == 1
        learn_frozen, learn_records, learn_attempt, learn_completed = completed_attempt(learn_case)
        learned_patch = _verified_patch(learn_frozen, learn_records, learn_attempt)
        learned_diff = learn_completed.worker_result.diagnostics["local_edit_result"]["diff_text"]
        assert (case.repo / "src/calc.py").read_bytes() == original

        changed_declaration, revision, advanced = _advance_after_learning(case, declaration, selected, same_source=same_source)
        _publish_current_source(case, source_input, revision)
        oracle_input = tmp_path / "harness/oracle_input.json"
        oracle_data = {**json.loads(oracle_input.read_text()), "base_revision": revision.git_sha}
        if same_source:
            oracle_data['command'] = [sys.executable, '-B', '-c', 'raise SystemExit(1)']
        oracle_input.write_text(json.dumps(oracle_data))
        chosen = use_model_agent({**changed_declaration, "config": dict(changed_declaration["config"])},
                                 tmp_path / "reuse-agent", endpoint=endpoint, protocol=LOCAL_EDIT_PROFILE_V5)
        reuse_case = replace(case, run_root=tmp_path / "reuse-run", input_path=tmp_path / "reuse-input.json")
        reuse_case.input_path.write_text(json.dumps({**chosen, "run_id": "changed-revision-reuse",
                                                     "knowledge_path": str(selected)}))
        reuse_result = _run(reuse_case, expected='VERIFIED_REUSABLE_PARTIAL' if same_source else 'FULL')
        frozen, records, attempt, completed = completed_attempt(reuse_case)
        progress = load_attempt_progress(records, manifest=frozen.manifest, context=attempt.context)
        c1_completed = progress.get(AttemptProgressPhase.C1_COMPLETED) is not None
        assert c1_completed
        local = completed.worker_result.diagnostics["local_edit_result"]
        assert local["diff_text"] and tuple(local["touched_files"]) == ("src/calc.py",)
        assert local["planning_route"] == ("MODEL" if same_source else "ADAPT_MEMORY")
        assert local["status"] == "UNVERIFIED_PATCH_PROPOSAL"
        if not same_source:
            assert completed.delivery_receipt.delivery_status.value == "SYNAPSE_ADAPTIVE_MEMORY"
            assert completed.delivery_receipt.transport_name == "synapse.adaptive-memory/v1"
            assert not (reuse_case.run_root / "stage10/agent-executions").exists()
        assert len(requests) == (2 if same_source else 1)
        assert (case.repo / "src/calc.py").read_bytes() == advanced
        for name, current, result in (("learn", learn_case, learn_result), ("reuse", reuse_case, reuse_result)):
            observation = inspect_observability(run_root=current.run_root,
                assessment_key=result["observability"]["assessment_key"])
            assert observation["telemetry_report"]["status"] == "COMPLETE"
            assert observation["artifact_report"]["status"] == "COMPLETE"
        observed_path = tmp_path / "harness/oracle_observations.jsonl"
        observed = [json.loads(line) for line in observed_path.read_text().splitlines()]
        assert len(observed) == 2 and observed[0]["after_returncode"] == 0
        assert (observed[1]["after_returncode"] != 0) is same_source
        assert all(row["before_returncode"] != 0 for row in observed)
        assert observed[0]["base_revision"] != observed[1]["base_revision"]
        assert (learned_diff == local["diff_text"]) is same_source
        assert (learned_patch == _verified_patch(frozen, records, attempt)) is same_source
        frozen_bytes = frozen.canonical_bytes
        code, resumed = reuse_case.cli("project", "resume", "--run-dir", reuse_case.run_root)
        assert code == 0 and resumed == reuse_result and len(requests) == (2 if same_source else 1)
        assert (reuse_case.run_root / "experiment.json").read_bytes() == frozen_bytes
        repository_clean = subprocess.run(["git", "status", "--porcelain"], cwd=case.repo,
            check=True, capture_output=True, text=True).stdout == ""
        assert repository_clean
