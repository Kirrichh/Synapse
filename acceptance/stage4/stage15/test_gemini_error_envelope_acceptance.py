"""Actual Mini retry of Gemini's observed array-shaped HTTP 503 response."""
import json

import pytest

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini
from acceptance.stage4.stage16.live_worker_evidence import require_connection_capture
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.stage15.capture_store import inspect_capture, read_source
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, reconstruct_call_records
from synapse.experiments.gold.stage15.telemetry import reference, TELEMETRY_SCHEMA


def test_gemini_error_array_is_retained_as_an_unknown_cost_retry(tmp_path):
    error = [{'error': {'code': 503, 'message': 'Model is temporarily unavailable.', 'status': 'UNAVAILABLE'}}]
    with provider_endpoint(first_status=503, error_body=error, model='gemini-3.1-flash-lite',
            path='/v1beta/openai/chat/completions', commands=['pwd', 'pwd', 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT']) as (endpoint, requests):
        store, result = run_actual_mini(tmp_path, endpoint, model='gemini-3.1-flash-lite')
    assert result.status.value == 'NO_PATCH', result
    assert len(requests) == 3
    frames = inspect_capture(store.cut())
    responses = [item['payload'] for item in frames if item['kind'] == 'CALL_RESPONSE']
    assert [item['status_code'] for item in responses] == [503, 200, 200]
    raw = read_source(store.root, HashBoundRef.from_dict(responses[0]['response_ref']))
    assert json.loads(raw) == error
    report = reconcile_telemetry(store.cut()).to_dict()
    assert report['status'] == 'MISSING_CALL' and report['source_totals']['physical_provider_reported_tokens'] is None
    evidence = require_connection_capture(store.cut())
    failed, *successes = reconstruct_call_records(store.cut())
    assert failed.status == 'FAILED' and all(item.status == 'COMPLETED' for item in successes)
    assert evidence['unknown_usage_call_ids'] == [failed.llm_call_id]
    assert evidence['accounting_status'] == 'MISSING_CALL'
    assert failed.logical_call_id == successes[0].logical_call_id
    # Missing a canonical call is never excused as absent usage on a retry.
    path = store.root / 'sources' / reference(successes[0].to_dict(), TELEMETRY_SCHEMA).sha256
    raw = path.read_bytes()
    path.unlink()
    try:
        with pytest.raises(AssertionError):
            require_connection_capture(store.cut())
    finally:
        path.write_bytes(raw)
    assert require_connection_capture(store.cut()) == evidence
