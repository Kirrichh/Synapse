"""Physical call discrepancies; deletion cannot be interpreted as zero usage."""

import pytest

from acceptance.stage4.stage15.test_provider_capture_acceptance import provider_endpoint, run_actual_mini
from synapse.experiments.gold.stage15.reconciliation import reconcile_telemetry, reconstruct_call_records, TelemetryStatus
from synapse.experiments.gold.stage15.telemetry import TELEMETRY_SCHEMA, reference
from synapse.experiments.gold.stage15.export import export_call_attributes


@pytest.mark.parametrize("peer,status", [
    ({"usage_total": 20}, TelemetryStatus.TOTAL_MISMATCH),
    ({"first_status": 429, "request_identity": "same-provider-request"}, TelemetryStatus.DOUBLE_COUNT_RISK),
])
def test_actual_provider_discrepancy_never_becomes_complete(tmp_path, peer, status):
    with provider_endpoint(**peer) as (endpoint, _):
        store, _ = run_actual_mini(tmp_path, endpoint)
    report = reconcile_telemetry(store.cut())
    assert report.status is status, report.to_dict()


def test_missing_call_and_corrupt_source_have_distinct_fail_closed_evidence(tmp_path):
    with provider_endpoint() as (endpoint, requests):
        store, _ = run_actual_mini(tmp_path, endpoint)
    cut = store.cut()
    call = reconstruct_call_records(cut)[0]
    exported = export_call_attributes(call)
    assert exported["attributes"]["gen_ai.usage.input_tokens"] == 11
    assert exported["attributes"]["gen_ai.usage.output_tokens"] == 7
    assert exported["attributes"]["gen_ai.usage.cache_read.input_tokens"] == 3
    assert "provider_total_tokens" not in exported["attributes"]
    path = store.root / "sources" / reference(call.to_dict(), TELEMETRY_SCHEMA).sha256
    raw = path.read_bytes()
    path.unlink()
    report = reconcile_telemetry(cut)
    assert report.status is TelemetryStatus.MISSING_CALL
    assert report.to_dict()["completeness_manifest"]["missing_call_ids"] == [call.llm_call_id]
    path.write_bytes(raw)
    response_path = store.root / "sources" / call.response_ref.sha256
    response_path.write_bytes(b'{"usage":{"total_tokens":0}}')
    report = reconcile_telemetry(cut)
    assert report.status is TelemetryStatus.SOURCE_INCONSISTENT
    assert report.to_dict()["source_totals"]["physical_provider_reported_tokens"] is None
    assert len(requests) == 1
