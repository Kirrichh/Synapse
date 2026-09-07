"""A real HTTP effect followed by process death cannot be replayed on recovery."""

from pathlib import Path
import subprocess
import os
import sys

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint
from synapse.experiments.gold.stage15.capture_store import CaptureStore, inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, TelemetryStatus
from synapse.experiments.gold.stage15.telemetry import reference


def test_process_death_before_receipt_keeps_unknown_call_and_repairs_only_storage(tmp_path):
    program = tmp_path / "fault_process.py"
    program.write_text('''
import os, sys
from pathlib import Path
from synapse.experiments.gold.stage15.capture_store import CaptureStore
from synapse.experiments.gold.stage15.telemetry import reference, UsageProfile
from synapse.experiments.gold.persistence import store_transaction
from synapse.llm.http_transport import provider_http_exchange
store = CaptureStore(Path(sys.argv[1]), run_id="crash-run", manifest_ref=reference({"run": "crash-run"}, "test.run/v1"))
capture = store.open_invocation(invocation_id="invocation", attempt_id="attempt", invocation_payload={"physical": "acceptance"},
    provider="openai", model="gpt-4o-mini", profile=UsageProfile.OPENAI_CHAT, worker_profile="mini-2.4.6-litellm-openai-chat/v1")
logical = capture.register_logical_call(request_identity="request")
def crash_before_receipt(**kwargs):
    with store.fence.exclusive() as guard:
        with store_transaction(store.fence, guard=guard):
            with (store.root / "calls.v1").open("ab") as stream:
                stream.write(b"torn")
                stream.flush()
                os.fsync(stream.fileno())
            os._exit(73)
capture.after_response = crash_before_receipt
provider_http_exchange(url=sys.argv[2], request=b'{"model":"gpt-4o-mini","messages":[]}',
    headers={"Content-Type":"application/json"}, timeout=10, capture=capture, logical_call_id=logical)
''')
    root = tmp_path / "capture"
    with provider_endpoint() as (endpoint, requests):
        completed = subprocess.run([sys.executable, str(program), str(root), endpoint],
            cwd=Path(__file__).resolve().parents[3], capture_output=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])})
        assert completed.returncode == 73, completed.stderr
        assert len(requests) == 1
        store = CaptureStore(root, run_id="crash-run", manifest_ref=reference({"run": "crash-run"}, "test.run/v1"))
        cut = store.cut()
        assert [r["kind"] for r in inspect_capture(cut)][-1] == "CALL_STARTED"
        report = reconcile_telemetry(cut)
        assert report.status is TelemetryStatus.MISSING_CALL
        assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] is None
        # Reopening another fresh owner settles no new call and appends no
        # invented zero-cost response or trajectory.
        reopened = CaptureStore(root, run_id="crash-run", manifest_ref=reference({"run": "crash-run"}, "test.run/v1"))
        assert reopened.cut() == cut
        assert len(requests) == 1


def test_required_capture_failure_precedes_the_provider_effect(tmp_path, monkeypatch):
    from synapse.experiments.gold.stage15.telemetry import UsageProfile
    from synapse.llm.http_transport import provider_http_exchange
    import pytest
    store = CaptureStore(tmp_path / "capture", run_id="refused-run", manifest_ref=reference({"run": "refused"}, "test.run/v1"))
    capture = store.open_invocation(invocation_id="invocation", attempt_id="attempt", invocation_payload={"test": "boundary"},
        provider="openai", model="gpt-4o-mini", profile=UsageProfile.OPENAI_CHAT, worker_profile="mini-2.4.6-litellm-openai-chat/v1")
    logical = capture.register_logical_call(request_identity="request")
    def fail_storage(*args, **kwargs):
        raise OSError("required capture storage is unavailable")
    monkeypatch.setattr(store, "_append", fail_storage)
    with provider_endpoint() as (endpoint, requests):
        with pytest.raises(OSError):
            provider_http_exchange(url=endpoint, request=b'{"model":"gpt-4o-mini","messages":[]}',
                headers={"Content-Type":"application/json"}, timeout=10, capture=capture, logical_call_id=logical)
        assert requests == []
