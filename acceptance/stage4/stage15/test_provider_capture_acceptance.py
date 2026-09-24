"""OBS-03: an actual agent process, Synapse's model broker, HTTP and retained sources.

The controlled endpoint replaces only the remote provider; the pluggable agent is
an ordinary admitted STDIO profile. No test implements capture, fills a
production ledger, or turns aggregate usage into call records.
"""

from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import subprocess
import threading

from acceptance.agents.coding_agents import COMPLETE, CREDENTIAL_ENV, model_agent_registry
from synapse.agents.codec import decode_json
from synapse.agents.contracts import AgentExecutionRequest, AgentRuntimeContext, LocalInformationPolicy
from synapse.agents.execution import AgentExecutionPort
from synapse.agents.outputs import PATCH_CANDIDATE_OUTPUT_V1
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, TelemetryStatus
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.experiments.gold.stage15.worker_accounting import WorkerAccounting


@contextmanager
def provider_endpoint(*, first_status=200, usage_total=18, request_identity=None, command=COMPLETE,
                      model="gpt-4o-mini", path="/v1/chat/completions", usage_details=True, commands=None,
                      error_body=None, statuses=None):
    """A controlled Chat Completions provider; each reply's content is scripted."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            count = len(requests)
            status = (statuses[min(count - 1, len(statuses) - 1)] if statuses is not None
                      else first_status if count == 1 else 200)
            if self.path != path:
                status = 404
            content = command if commands is None else commands[min(count - 1, len(commands) - 1)]
            value = {"id": f"chatcmpl-{count}", "object": "chat.completion", "created": 123, "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": usage_total,
                          "prompt_tokens_details": {"cached_tokens": 3},
                          "completion_tokens_details": {"reasoning_tokens": 2}}}
            if not usage_details:
                value["usage"].pop("prompt_tokens_details")
                value["usage"].pop("completion_tokens_details")
            if status != 200:
                value = ({"error": {"message": "transient provider rejection", "type": "rate_limit_error"}}
                         if error_body is None else error_body)
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-request-id", request_identity or f"provider-request-{count}")
            self.send_header("retry-after", "0")
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{path}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def agent_request(payload, *, information=None, scope=("src",), invocation_id="inv_" + "1" * 64):
    raw = payload.encode()
    fields = {} if information is None else {"information_text": information.text,
        "information_sha256": information.sha256, "information_byte_length": len(information.canonical_bytes)}
    return AgentExecutionRequest(invocation_id=invocation_id, attempt_id="attempt-1", context_id="ctx_" + "2" * 64,
        task_text=payload, task_sha256=hashlib.sha256(raw).hexdigest(), task_byte_length=len(raw),
        envelope_sha256="3" * 64, required_capabilities=("repository.edit",),
        required_output_profile=PATCH_CANDIDATE_OUTPUT_V1, required_effect_classes=("PATH_MODIFIED",),
        allowed_effects=("PATH_MODIFIED",), allowed_scope=tuple(scope), allowed_networks=("LOCAL_BROKER",),
        information_policy=LocalInformationPolicy.NOT_SUPPORTED if information is None else LocalInformationPolicy.LOCAL_ONLY,
        **fields)


def run_actual_agent(root, endpoint, *, model="gpt-4o-mini", payload=None, information=None, protocol=None,
                     repo=None, environment=None, max_steps=3, monkeypatch=None, timeout_seconds=45):
    """Dispatch the admitted model agent once through the universal execution port."""
    if repo is None:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Acceptance", "-c",
                        "user.email=acceptance@example.invalid", "commit", "--allow-empty", "-qm", "initial"], check=True)
    if monkeypatch is not None:
        monkeypatch.setenv(CREDENTIAL_ENV, "acceptance-only")
    store = CaptureStore(root / "capture", run_id="run-1", manifest_ref=reference({"run_id": "run-1"}, "test.run/v1"))
    registry = model_agent_registry(root / "agent", endpoint=endpoint, model=model, protocol=protocol,
        accounting=WorkerAccounting(store=store), environment=environment, max_steps=max_steps,
        timeout_seconds=timeout_seconds)
    port = AgentExecutionPort(registry, evidence_root=root / "agent-evidence")
    payload = payload or "Inspect the task. No source change is necessary; finish now."
    request = replace(agent_request(payload, information=information),
                      resource_budget=registry.adapters[0].profile.resource_limits)
    result = port.execute(request=request,
                          runtime=AgentRuntimeContext(execution_root=repo.absolute()))
    return store, result


def candidate(result):
    output, = result.outputs
    return decode_json(output.payload)


def test_actual_agent_request_is_retained_before_delivery(tmp_path, monkeypatch):
    with provider_endpoint() as (endpoint, requests):
        store, result = run_actual_agent(tmp_path, endpoint, monkeypatch=monkeypatch)
    records = inspect_capture(store.cut())
    assert len(requests) == 1, result
    assert [r["kind"] for r in records] == ["RUN_OPEN", "INVOCATION_OPEN", "LOGICAL_OPEN", "CALL_STARTED",
        "CALL_RESPONSE", "LOGICAL_CLOSED", "INVOCATION_CLOSED"]
    receipt = records[4]["payload"]
    response = json.loads(read_source(store.root, HashBoundRef.from_dict(receipt["response_ref"])))
    assert response["usage"]["total_tokens"] == 18
    assert receipt["provider_request_id"] == "provider-request-1"
    # The agent never receives the provider credential, only its invocation capability.
    assert "acceptance-only" not in json.dumps(requests)
    # Synapse reconciles the agent's neutral account, not its private record.
    inventory = json.loads(read_source(store.root, HashBoundRef.from_dict(records[-1]["payload"]["inventory_ref"])))
    assert inventory["schema_version"] == "synapse.agent.response-inventory/v1" and inventory["declared_calls"] == 1
    assert [item["logical_call_id"] for item in inventory["responses"]] == [records[2]["payload"]["logical_call_id"]]
    assert candidate(result)["status"] == "NO_PATCH"
    report = reconcile_telemetry(store.cut())
    assert report.status is TelemetryStatus.COMPLETE, report.to_dict()
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] == 18


def test_agent_retry_is_a_distinct_physical_call_with_retained_failure(tmp_path, monkeypatch):
    with provider_endpoint(first_status=429) as (endpoint, requests):
        store, result = run_actual_agent(tmp_path, endpoint, monkeypatch=monkeypatch)
    records = inspect_capture(store.cut())
    calls = [r for r in records if r["kind"] == "CALL_STARTED"]
    responses = [r["payload"] for r in records if r["kind"] == "CALL_RESPONSE"]
    assert len(requests) == len(calls) == 2, result
    assert len({r["payload"]["call_id"] for r in calls}) == 2
    assert len({r["payload"]["logical_call_id"] for r in calls}) == 1
    assert [r["status_code"] for r in responses] == [429, 200]
    # The failed provider request has no reported usage. It was observed, but
    # claiming its unknown cost/token usage is zero would be a false total.
    report = reconcile_telemetry(store.cut())
    assert report.status is TelemetryStatus.MISSING_CALL
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] is None
