"""Actual Mini receives local information; only the public task reaches HTTP."""

import json

from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.input_contract import SPLIT_INPUT_PROFILE_V1

from acceptance.stage4.stage10.test_worker_input_contract import task_input, information_input
from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini


def test_same_public_task_has_identical_wire_requests_under_different_local_memory(tmp_path):
    public = task_input()
    wire_requests = []
    for index, role in enumerate(("REFERENCE", "REJECTED_HYPOTHESIS")):
        root = tmp_path / str(index)
        root.mkdir()
        private = information_input(f"PRIVATE-MEMORY-{index}; contradictory experience".encode(), role=role)
        with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                commands=(None, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")) as (endpoint, requests):
            store, result = run_actual_mini(root, endpoint, model="gemini-3.1-flash-lite",
                                            payload=public.text, information=private)
        assert result.status.value == "NO_PATCH", result
        assert len(requests) == 2  # Actual provider FormatError remains a public continuation.
        wire_requests.append(requests)
        outgoing = json.dumps(requests)
        for forbidden in ("PRIVATE-MEMORY", private.to_dict()["items"][0]["content_base64url"],
                          private.sha256, "admitted_knowledge", "accepted_plan", "SYNAPSE_MINI_INFORMATION"):
            assert forbidden not in outgoing
        assert public.text in requests[0]["messages"][1]["content"]
        closure, = [frame["payload"] for frame in inspect_capture(store.cut()) if frame["kind"] == "INVOCATION_CLOSED"]
        trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(closure["trajectory_ref"])))
        receipt = trajectory["info"]["input_delivery"]
        assert receipt == {"profile": SPLIT_INPUT_PROFILE_V1,
            "task_sha256": result.delivery_evidence.payload_sha256,
            "information_sha256": private.sha256, "information_byte_length": len(private.canonical_bytes),
            "information_item_count": 1, "local_interpretation": "NOT_PERFORMED"}
        assert trajectory["info"]["model_stats"]["api_calls"] == 2
        report = reconcile_telemetry(store.cut()).to_dict()
        assert report["status"] == "COMPLETE", report
        assert report["source_totals"]["physical_provider_reported_tokens"] == 36
    assert wire_requests[0] == wire_requests[1]
