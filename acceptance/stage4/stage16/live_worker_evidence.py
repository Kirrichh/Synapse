"""Acceptance-only criteria for connectivity and retained retry accounting.

A connection can recover while total usage remains unmeasured. This assertion
never changes the product's accounting report or grants economics completeness.
"""
from synapse.experiments.gold.stage15.capture_store import inspect_capture
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, reconstruct_call_records
from synapse.experiments.gold.stage15.telemetry import UsageConsistency


def require_connection_capture(cut):
    frames = inspect_capture(cut)
    calls = reconstruct_call_records(cut, frames)
    assert calls, "connection requires actual physical provider calls"
    report = reconcile_telemetry(cut).to_dict()
    physical = {f['payload']['call_id']: f['payload'] for f in frames if f['kind'] == 'CALL_RESPONSE'}
    unknown = set()
    for call in calls:
        assert call.llm_call_id in physical, 'physical request has no retained HTTP response'
        if call.status == 'COMPLETED':
            assert call.usage.consistency is UsageConsistency.CONSISTENT, call.to_dict()
            continue
        status = physical[call.llm_call_id]['status_code']
        assert call.status == 'FAILED' and (status in {408, 409, 429} or 500 <= status <= 599), call.to_dict()
        assert any(other.logical_call_id == call.logical_call_id and other.status == 'COMPLETED'
                   for other in calls), 'failed logical request has no successful SDK retry'
        if call.usage.consistency is UsageConsistency.UNAVAILABLE:
            assert call.usage.provider_total_tokens is None and call.usage.component_total_tokens is None
            unknown.add(call.llm_call_id)
        else:
            assert call.usage.consistency is UsageConsistency.CONSISTENT, call.to_dict()
    expected = sorted((name, 'physical_usage_unavailable', 'MISSING_CALL') for name in unknown)
    actual = sorted((item['subject'], item['code'], item['status']) for item in report['discrepancies'])
    assert actual == expected, report
    assert report['status'] == ('MISSING_CALL' if unknown else 'COMPLETE'), report
    inventory = report['completeness_manifest']
    assert not inventory['missing_call_ids'] and not inventory['duplicate_provider_ids'], inventory
    assert sorted(inventory['expected_physical_call_ids']) == sorted(inventory['observed_physical_call_ids']), inventory
    if unknown:
        assert report['source_totals']['physical_provider_reported_tokens'] is None
        assert report['source_totals']['physical_component_tokens'] is None
    return {'connection_capture': 'COMPLETE', 'accounting_status': report['status'],
            'unknown_usage_call_ids': sorted(unknown), 'reconciliation_report_id': report['report_id']}
