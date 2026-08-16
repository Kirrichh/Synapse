from __future__ import annotations

import hashlib

import pytest

from synapse.experiments.gold import admission_store as AS
from synapse.experiments.gold import compatibility_store as CS
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.compatibility import (
    evaluate_conflicts,
    revalidate_before_loading,
)

from tests.gold_store_fence import fence_for
from tests.test_stage4_gold_compatibility import _make_harness


def _records(tmp_path):
    harness = _make_harness(tmp_path / "world")
    scan = evaluate_conflicts(
        evaluator=harness.evaluator,
        context=harness.context,
        decisions=(harness.decision,),
        descriptors=(harness.descriptor,),
        considered_index_entries=(harness.entry,),
        proposals=(),
    )
    revalidation = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    return (
        harness.context,
        harness.decision.evidence,
        harness.decision,
        revalidation,
        scan,
    )


def test_all_five_closed_record_types_are_exactly_committed(tmp_path) -> None:
    store = CS.FileCompatibilityStore(
        tmp_path / "compatibility", mutation_fence=fence_for(tmp_path)
    )
    genesis = store.current_anchor()
    records = _records(tmp_path)
    receipts = []
    for record in records:
        receipts.append(
            store.append_record(record, expected_parent_anchor=store.current_anchor())
        )
    assert tuple(item.record_kind for item in receipts) == tuple(CS.CompatibilityRecordKind)
    assert store.current_sequence() == 5
    assert store.extends(genesis)
    for record, receipt in zip(records, receipts):
        assert store.contains_ref(receipt.record_ref)
        assert store.resolve_ref(receipt.record_ref) == canonicalize_stage4_payload(
            record.to_dict(),
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
        CS.validate_compatibility_receipt(receipt, store=store)


def test_stale_parent_duplicate_torn_tail_and_corruption_fail_closed(tmp_path) -> None:
    store = CS.FileCompatibilityStore(
        tmp_path / "compatibility", mutation_fence=fence_for(tmp_path)
    )
    records = _records(tmp_path)
    record = records[0]
    stale = store.current_anchor()
    receipt = store.append_record(record, expected_parent_anchor=stale)
    with pytest.raises(CS.CompatibilityStoreViolation) as duplicate:
        store.append_record(record, expected_parent_anchor=store.current_anchor())
    assert duplicate.value.failure_code is CS.CompatibilityStoreFailureCode.RECORD_DUPLICATE
    with pytest.raises(CS.CompatibilityStoreViolation) as moved:
        store.append_record(records[1], expected_parent_anchor=stale)
    assert moved.value.failure_code is CS.CompatibilityStoreFailureCode.STALE_PARENT

    original = store.path.read_bytes()
    store.path.write_bytes(original + b"partial")
    with pytest.raises(CS.CompatibilityStoreViolation) as torn:
        store.current_anchor()
    assert torn.value.failure_code is CS.CompatibilityStoreFailureCode.HISTORY_TORN
    store.path.write_bytes(original)
    damaged = bytearray(original)
    damaged[-1] ^= 1
    store.path.write_bytes(bytes(damaged))
    with pytest.raises(CS.CompatibilityStoreViolation) as corrupt:
        store.current_anchor()
    assert corrupt.value.failure_code in {
        CS.CompatibilityStoreFailureCode.HISTORY_TORN,
        CS.CompatibilityStoreFailureCode.HISTORY_CORRUPT,
    }
    assert receipt.sequence == 1


def test_receipt_detects_rollback_and_foreign_coordinator(tmp_path) -> None:
    root = tmp_path / "compatibility"
    store = CS.FileCompatibilityStore(root, mutation_fence=fence_for(tmp_path))
    receipt = store.append_record(
        _records(tmp_path)[0], expected_parent_anchor=store.current_anchor()
    )
    store.path.write_bytes(b"")
    with pytest.raises(CS.CompatibilityStoreViolation) as rolled_back:
        CS.validate_compatibility_receipt(receipt, store=store)
    assert rolled_back.value.failure_code is CS.CompatibilityStoreFailureCode.HISTORY_ROLLED_BACK

    foreign = CS.FileCompatibilityStore(
        tmp_path / "foreign", mutation_fence=fence_for(tmp_path / "foreign-owner")
    )
    with pytest.raises(CS.CompatibilityStoreViolation) as mismatch:
        CS.validate_compatibility_receipt(receipt, store=foreign)
    assert mismatch.value.failure_code is CS.CompatibilityStoreFailureCode.COORDINATOR_MISMATCH


def _causal_ref(payload: bytes) -> HashBoundRef:
    digest = hashlib.sha256(payload).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=AS.RETRIEVAL_DECISION_SCHEMA_V1,
        sha256=digest,
        byte_length=len(payload),
        media_type="application/json",
    )


def test_retrieval_decision_is_an_opaque_admission_causal_artifact(tmp_path) -> None:
    fence = fence_for(tmp_path)
    journal = FileAdmissionJournal(tmp_path / "admission.journal", fence)
    journal.append_record(b"gate")
    store = AS.FileAdmissionCausalStore(
        tmp_path / "causal", mutation_fence=fence, admission_history=journal
    )
    payload = canonicalize_stage4_payload(
        {"opaque": "retrieval-decision"},
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    ref = _causal_ref(payload)
    receipt = store.append_retrieval_decision(
        canonical_bytes=payload,
        record_ref=ref,
        expected_parent_anchor=store.current_anchor(),
    )
    assert store.contains_ref(ref)
    assert store.resolve_ref(ref) == payload
    assert journal.extends(receipt.admission_history_root)
    AS.validate_causal_receipt(receipt, store=store)


def test_causal_artifact_ref_and_admission_root_are_reverified(tmp_path) -> None:
    fence = fence_for(tmp_path)
    journal = FileAdmissionJournal(tmp_path / "admission.journal", fence)
    journal.append_record(b"gate")
    store = AS.FileAdmissionCausalStore(
        tmp_path / "causal", mutation_fence=fence, admission_history=journal
    )
    payload = canonicalize_stage4_payload(
        {"opaque": "retrieval-decision"},
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    ref = _causal_ref(payload)
    with pytest.raises(AS.CausalHistoryViolation) as wrong_bytes:
        store.append_retrieval_decision(
            canonical_bytes=payload + b" ",
            record_ref=ref,
            expected_parent_anchor=store.current_anchor(),
        )
    assert wrong_bytes.value.failure_code is AS.CausalHistoryFailureCode.TYPE_MISMATCH

    receipt = store.append_retrieval_decision(
        canonical_bytes=payload,
        record_ref=ref,
        expected_parent_anchor=store.current_anchor(),
    )
    journal.path.write_bytes(b"")
    with pytest.raises(AS.CausalHistoryViolation) as rolled_back:
        AS.validate_causal_receipt(receipt, store=store)
    assert rolled_back.value.failure_code is AS.CausalHistoryFailureCode.ADMISSION_ROOT_UNCONFIRMED
