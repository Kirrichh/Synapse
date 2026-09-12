"""§32: activation requires retained producer, consumer and avoided-work evidence."""

import json
from pathlib import Path
import shlex
import sys

from acceptance.stage4.stage13._observed_reuse import observed_reuse_case
from acceptance.stage4.stage16.run_evidence import inspect_mechanisms
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.experiments.gold.stage15.run_observability import inspect_observability
from synapse.worker.provider_transport import MINI_ACCOUNTING_PROFILE
from tests.test_swebench_gold_runner import NEW_SOURCE


def test_external_activation_reopens_every_stage_and_preserves_the_producer(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNAPSE_ACCEPTANCE_PROVIDER_KEY", "controlled-provider-credential")
    command = "python -c " + shlex.quote("from pathlib import Path; Path('src/calc.py').write_text(" + repr(NEW_SOURCE) + ")")
    command += "; echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    with provider_endpoint(command=command) as (endpoint, requests):
        mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
        configuration = {"provider": "mini", "command": [str(mini)], "model": "gpt-4o-mini",
            "timeout_seconds": 60, "max_steps": 3, "cost_limit": "1",
            "accounting": {"profile": MINI_ACCOUNTING_PROFILE, "endpoint": endpoint,
                "credential_env": "SYNAPSE_ACCEPTANCE_PROVIDER_KEY"}}
        producer, consumer, original, request = observed_reuse_case(tmp_path, worker_configuration=configuration)
        retained = {p: p.read_bytes() for p in (producer.run_root / "run-records").rglob("*.json")}
        code, pending = consumer.start()
        assert code == 3, pending
        code, result = consumer.approve(pending)
        assert code == 0, result
        observed = inspect_mechanisms(run_root=consumer.run_root)
        assert observed["status"] == "ACTIVATED", observed
        proof, = observed["proofs"]
        assert proof["effect"] == "EXACT_REJECTED_C1_DISPATCH_AVOIDED"
        assert proof["avoided_dispatches"] == 1
        assert proof["producer_outcome_ref"] == request["outcome"]["outcome_ref"]
        assert request["outcome"]["payload"]["scope"] == "ATTEMPT"
        assert original["payload"]["scope"] == "RUN"
        assert proof["producer_outcome_ref"] != proof["consumer_outcome_ref"]
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
            assert result["result"]["structured_outcome"]["payload"]["status"] == "UNRESOLVED"
        finally:
            source.write_bytes(raw)
        assert inspect_mechanisms(run_root=consumer.run_root) == observed
