"""§32: local rejection requires retained producer and independent influence proof.

Mini now rejects an exact failed proposal before C1. Do not relabel that local
selection as the older C1 dispatch guard's OBSERVED_USEFUL_REUSE mechanism.
"""

from dataclasses import replace
import json
from pathlib import Path
import sys

from acceptance.stage4.stage11._oracle_process import create_oracle_process
from acceptance.stage4.stage16._source_inputs import consumer_case
from acceptance.stage4.stage16.test_partial_memory_loop_acceptance import completed_attempt
from acceptance.stage4.stage16.run_evidence import inspect_mechanisms
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.experiments.gold.persistence import read_committed_snapshot_transaction
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.stage10.influence import observe_local_context_influence
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE
from synapse.worker.local_edits import LOCAL_EDIT_COMMAND, LOCAL_EDIT_PROFILE_V1, LOCAL_EDIT_PROPOSAL_V1
from tests.test_swebench_gold_runner import OLD_SOURCE, NEW_SOURCE


def test_local_rejection_reopens_every_stage_and_preserves_the_producer(tmp_path, monkeypatch):
    producer, _ = consumer_case(tmp_path, automatic_targets=True)
    producer = replace(producer, cli_timeout_seconds=1800)
    create_oracle_process(tmp_path / "harness", (False,))
    publication_root = producer.state_root / "publications"
    existing = set((publication_root / "committed").iterdir())
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    proposal = {"schema_version": LOCAL_EDIT_PROPOSAL_V1, "alternatives": [
        {"edits": [{"path": "src/calc.py", "old": OLD_SOURCE, "new": NEW_SOURCE}]}]}
    command = LOCAL_EDIT_COMMAND + json.dumps(proposal)
    with provider_endpoint(command=command) as (endpoint, requests):
        mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
        configuration = {"provider": "mini", "command": [str(mini)], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1", "input_profile": LOCAL_EDIT_PROFILE_V1,
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        declaration = json.loads(producer.input_path.read_text())
        declaration["worker"] = configuration
        declaration["config"]["model"] = configuration["model"]
        producer.input_path.write_text(json.dumps(declaration))
        code, pending = producer.start()
        assert code == 3 and not requests, pending
        code, first = producer.approve(pending)
        assert code == 0 and first["outcome_status"] == "VERIFIED_REUSABLE_PARTIAL", first
        transaction, = set((publication_root / "committed").iterdir()) - existing
        _, members = read_committed_snapshot_transaction(publication_root / "prepared", transaction_id=transaction.name)
        request = json.loads(members["request.json"])
        assert request["verification"]["payload"]["c1"]["oracle_resolved"] is False
        assert request["outcome"]["payload"]["scope"] == "ATTEMPT"
        assert first["result"]["structured_outcome"]["payload"]["scope"] == "RUN"
        retained = {p: p.read_bytes() for p in (producer.run_root / "run-records").rglob("*.json")}
        consumer = replace(producer, run_root=tmp_path / "consumer-run", input_path=tmp_path / "consumer-input.json")
        consumer.input_path.write_text(json.dumps({**declaration, "run_id": "observed-reuse-consumer"}))
        code, pending = consumer.start()
        assert code == 3, pending
        code, result = consumer.approve(pending)
        assert code == 0 and result["outcome_status"] == "NO_CANDIDATE", result
        frozen, records, attempt, completed = completed_attempt(consumer)
        assert any(item["transaction_id"] == transaction.name
                   for item in frozen.data["source_snapshot"]["run_publications"])
        local = completed.worker_result.diagnostics["local_edit_result"]
        candidate, = local["candidates"]
        assert local["selected_index"] is None and local["diff_text"] is None
        assert candidate["reason"] == "EXACT_VERIFIED_PATCH_REJECTED"
        assert candidate["patch_sha256"] == request["domain"]["patch_sha256"]
        proof = observe_local_context_influence(receipt=completed.delivery_receipt,
            invocation=completed.invocation, worker_result=completed.worker_result).payload(completed.delivery_receipt)
        comparison, = [item for item in proof["comparisons"] if item["removed_role"] == "EXECUTION_OBSERVATION"]
        assert comparison["selection_changed"] is True
        assert comparison["selection"] == {"status": "UNVERIFIED_PATCH_PROPOSAL",
                                             "patch_sha256": request["domain"]["patch_sha256"]}
        graph = LineageGraph.from_dict(records.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1").payload)
        roles = dict(graph.roles)
        assert {"context_influence", "local_selection", "influence_proof", "verification"} <= set(roles)
        assert roles["context_influence"] in {node.node_id for node in graph.ancestors(roles["verification"])}
        verification = attempt.result.structured_outcome["payload"]["verification"]["payload"]
        assert verification["c1"]["no_candidate"] is True
        assert verification["c1"]["commands_complete"] is False
        assert verification["c1"]["oracle_result_ref"] is None
        assert request["outcome"]["outcome_ref"] != attempt.result.structured_outcome["outcome_ref"]
        observed = inspect_mechanisms(run_root=consumer.run_root)
        assert observed["status"] == "MECHANISM_NOT_ACTIVATED" and observed["proofs"] == [], observed
        assert len(requests) == 2
        observation = inspect_observability(run_root=consumer.run_root, assessment_key=result["observability"]["assessment_key"])
        assert observation["telemetry_report"]["status"] == "COMPLETE", observation
        assert observation["artifact_report"]["status"] == "COMPLETE", observation
        assert json.loads((tmp_path / "harness/oracle_state.json").read_bytes())["calls"] == 1
        assert {p: p.read_bytes() for p in retained} == retained
        # Hash-valid summaries cannot replace a retained physical source.
        source = next((consumer.run_root / "run-records/attempt-lineage").glob("*.json"))
        raw = source.read_bytes()
        source.unlink()
        try:
            unavailable = inspect_mechanisms(run_root=consumer.run_root)
            assert unavailable["status"] == "INCOMPLETE"
            assert unavailable["proofs"] == []
            assert result["result"]["structured_outcome"]["payload"]["status"] == "NO_CANDIDATE"
        finally:
            source.write_bytes(raw)
        assert inspect_mechanisms(run_root=consumer.run_root) == observed
