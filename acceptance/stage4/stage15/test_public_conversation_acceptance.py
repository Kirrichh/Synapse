"""An actual agent receives local information; only the public conversation reaches HTTP.

The protocol's roots, the provider's own replies and its fixed correction are
the only messages Synapse's model broker forwards while local information is
delivered. A paid reply that is not a proposal stays in the accounting.
"""

import json

from synapse.agents.contracts import AgentExecutionStatus
from synapse.experiments.gold.stage15.capture_store import inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry
from synapse.worker.local_edits import LOCAL_EDIT_CORRECTION, LOCAL_EDIT_PROFILE_V6, public_messages

from acceptance.stage4.stage10.test_worker_input_contract import task_input, information_input
from acceptance.stage4.stage15.test_provider_capture_acceptance import candidate, provider_endpoint, run_actual_agent


def test_same_public_task_has_identical_wire_requests_under_different_local_memory(tmp_path, monkeypatch):
    public = task_input()
    wire_requests = []
    for index, role in enumerate(("REFERENCE", "REJECTED_HYPOTHESIS")):
        root = tmp_path / str(index)
        root.mkdir()
        private = information_input(f"PRIVATE-MEMORY-{index}; contradictory experience".encode(), role=role)
        with provider_endpoint(model="gemini-3.1-flash-lite", path="/v1beta/openai/chat/completions",
                commands=("The change is probably small.", "COMPLETE")) as (endpoint, requests):
            store, result = run_actual_agent(root, endpoint, model="gemini-3.1-flash-lite", payload=public.text,
                information=private, protocol=LOCAL_EDIT_PROFILE_V6, monkeypatch=monkeypatch)
        assert candidate(result)["status"] == "NO_PATCH", result
        assert len(requests) == 2  # A paid non-proposal reply continues only publicly.
        wire_requests.append(requests)
        outgoing = json.dumps(requests)
        for forbidden in ("PRIVATE-MEMORY", private.to_dict()["items"][0]["content_base64url"],
                          private.sha256, "admitted_knowledge", "accepted_plan"):
            assert forbidden not in outgoing
        roots = public_messages(public.text, LOCAL_EDIT_PROFILE_V6)
        reply = {"role": "assistant", "content": "The change is probably small."}
        assert requests[0]["messages"] == roots
        assert requests[1]["messages"] == roots + [reply, {"role": "user", "content": LOCAL_EDIT_CORRECTION}]
        report = reconcile_telemetry(store.cut()).to_dict()
        assert report["status"] == "COMPLETE", report
        assert report["source_totals"]["physical_provider_reported_tokens"] == 36
    assert wire_requests[0] == wire_requests[1]


def test_local_information_added_by_an_agent_never_reaches_the_provider(tmp_path, monkeypatch):
    private = information_input(b"PRIVATE-MEMORY; must stay local")
    with provider_endpoint() as (endpoint, requests):
        store, result = run_actual_agent(tmp_path, endpoint, payload=task_input().text, information=private,
            protocol=LOCAL_EDIT_PROFILE_V6, environment={"SYNAPSE_ACCEPTANCE_LEAK": "1"}, monkeypatch=monkeypatch)
    assert requests == []
    assert result.status is AgentExecutionStatus.ERROR
    kinds = [frame["kind"] for frame in inspect_capture(store.cut())]
    assert "LOGICAL_OPEN" not in kinds and "CALL_STARTED" not in kinds
    assert kinds[-1] == "INVOCATION_CLOSED"
