"""OBS-03: actual Mini process, LiteLLM/OpenAI SDK, HTTP and retained sources.

The controlled endpoint replaces only the remote provider. No test implements
capture, fills a production ledger, or turns aggregate usage into call records.
"""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading

from synapse.experiments.gold.stage10.worker_transport import WorkerInvocation
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture, read_source
from synapse.experiments.gold.stage15.telemetry import reference
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, TelemetryStatus
from synapse.experiments.gold.stage15.worker_accounting import WorkerAccounting
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.worker.mini_adapter import MiniAdapterConfig, MiniWorkerTransport
from synapse.worker.provider_transport import MiniProviderConfiguration


@contextmanager
def provider_endpoint(*, first_status=200, usage_total=18, request_identity=None,
                      command="echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", model="gpt-4o-mini",
                      path="/v1/chat/completions", usage_details=True, commands=None, thought_signature=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            status = first_status if len(requests) == 1 else 200
            if self.path != path:
                status = 404
            value = {"id": f"chatcmpl-{len(requests)}", "object": "chat.completion", "created": 123,
                "model": model, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": "The task needs no changes.", "tool_calls": [{
                        "id": "tool-1", "type": "function", "function": {"name": "bash",
                            "arguments": json.dumps({"command": command})}}]}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": usage_total,
                          "prompt_tokens_details": {"cached_tokens": 3},
                          "completion_tokens_details": {"reasoning_tokens": 2}}}
            if not usage_details:
                value["usage"].pop("prompt_tokens_details")
                value["usage"].pop("completion_tokens_details")
            call = value["choices"][0]["message"]["tool_calls"][0]
            call["id"] = f"tool-{len(requests)}"
            if commands is not None:
                call["function"]["arguments"] = json.dumps({"command": commands[min(len(requests) - 1, len(commands) - 1)]})
            if thought_signature is not None:
                call["extra_content"] = {"google": {"thought_signature": thought_signature}}
            if commands is not None and commands[min(len(requests) - 1, len(commands) - 1)] is None:
                value["choices"][0]["message"].pop("tool_calls")
                value["choices"][0]["finish_reason"] = "stop"
            if status != 200:
                value = {"error": {"message": "transient provider rejection", "type": "rate_limit_error"}}
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-request-id", request_identity or f"provider-request-{len(requests)}")
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


def run_actual_mini(root, endpoint, *, model="gpt-4o-mini", credential_env=None, payload=None):
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Acceptance", "-c", "user.email=acceptance@example.invalid",
                    "commit", "--allow-empty", "-qm", "initial"], check=True)
    mini = Path(sys.executable).parent / ("mini.exe" if sys.platform == "win32" else "mini")
    assert mini.is_file(), "the real-call acceptance job must install the pinned Mini dependency"
    store = CaptureStore(root / "capture", run_id="run-1", manifest_ref=reference({"run_id": "run-1"}, "test.run/v1"))
    payload = payload or "Inspect the task. No source change is necessary; finish now."
    invocation = WorkerInvocation("inv_" + "1" * 64, "attempt-1", "ctx_" + "2" * 64, payload,
        hashlib.sha256(payload.encode()).hexdigest(), len(payload.encode()), "3" * 64, ("src",), ("read",))
    worker = MiniWorkerTransport(config=MiniAdapterConfig(command=(str(mini),), model="openai/" + model,
        timeout_seconds=45, max_steps=3, cost_limit=1.0),
        accounting=WorkerAccounting(store=store,
            configuration=MiniProviderConfiguration(model, None if credential_env else "acceptance-only", endpoint,
                30 if credential_env else 10, credential_env=credential_env)))
    result = worker.run(repo, invocation)
    return store, result


def test_actual_mini_sdk_request_is_retained_before_delivery(tmp_path):
    with provider_endpoint() as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint)
    records = inspect_capture(store.cut())
    assert len(requests) == 1, result
    assert [r["kind"] for r in records] == ["RUN_OPEN", "INVOCATION_OPEN", "LOGICAL_OPEN", "CALL_STARTED",
        "CALL_RESPONSE", "LOGICAL_CLOSED", "INVOCATION_CLOSED"]
    receipt = records[4]["payload"]
    response = json.loads(read_source(store.root, HashBoundRef.from_dict(receipt["response_ref"])))
    assert response["usage"]["total_tokens"] == 18
    assert receipt["provider_request_id"] == "provider-request-1"
    trajectory = json.loads(read_source(store.root, HashBoundRef.from_dict(records[-1]["payload"]["trajectory_ref"])))
    observed = [m["extra"]["capture_logical_id"] for m in trajectory["messages"] if "capture_logical_id" in m.get("extra", {})]
    assert observed == [records[2]["payload"]["logical_call_id"]]
    assert result.status.value == "NO_PATCH"
    report = reconcile_telemetry(store.cut())
    assert report.status is TelemetryStatus.COMPLETE, report.to_dict()
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] == 18


def test_mini_retry_through_sdk_is_a_distinct_physical_call_with_retained_failure(tmp_path):
    with provider_endpoint(first_status=429) as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint)
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
