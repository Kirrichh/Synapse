"""Real Mini delivery uses a proposal environment with no external capabilities.

This acceptance executes only the public completion declaration. Active
boundary-circumvention experiments are intentionally not part of this scenario.
"""

import json

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.mini_protocol import MINI_EFFECT_POLICY_V1
from acceptance.stage4.stage10.test_worker_input_contract import task_input, information_input
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini


def test_private_delivery_uses_an_environment_without_external_capabilities(tmp_path, monkeypatch):
    from synapse.worker import mini_adapter
    normalize = mini_adapter._normalize_worker_process_result
    failures = []
    def observe_completion(plan, process, observation):
        result = normalize(plan, process, observation)
        if process.completed.returncode:
            failures.append(process.stderr + "\n" + process.stdout)
        return result
    monkeypatch.setattr(mini_adapter, "_normalize_worker_process_result", observe_completion)
    private = information_input(b"Local reference material")
    with provider_endpoint() as (endpoint, requests):
        store, result = run_actual_mini(
            tmp_path, endpoint, payload=task_input().text, information=private)
    assert result.status.value == "NO_PATCH", "\n".join(failures) or repr(result.report)
    assert len(requests) == 1
    frames = inspect_capture(store.cut())
    closure, = [frame["payload"] for frame in frames if frame["kind"] == "INVOCATION_CLOSED"]
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
    environment = trajectory["info"]["environment"]
    assert environment["effect_policy"] == MINI_EFFECT_POLICY_V1
    assert environment["capabilities"] == []
    assert trajectory["info"]["input_delivery"]["information_sha256"] == private.sha256
    assert private.sha256 not in json.dumps(requests)
    report = reconcile_telemetry(store.cut()).to_dict()
    assert report["status"] == "COMPLETE", report
    assert report["source_totals"]["physical_provider_reported_tokens"] == 18
