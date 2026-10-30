"""Cheap public-contract checks: untrusted labels cannot mint authority."""

import hashlib

import pytest

from synapse.experiments.gold.runner.vocabulary import GoldRunFailureCode, GoldRunViolation
from synapse.experiments.gold.stage10.context_codec import encode_canonical
from synapse.experiments.gold.stage12 import verification_contract
from synapse.experiments.gold.stage12.outcome import (
    FinalStatus, StructuredOutcome, evaluate_attempt_outcome, inspect_outcome,
)
from synapse.experiments.gold.stage12.verification_contract import VerificationRecord


def test_status_vocabulary_excludes_later_usefulness_claims():
    assert {item.value for item in FinalStatus} == {
        "FULL", "VERIFIED_REUSABLE_PARTIAL", "UNRESOLVED", "NO_CANDIDATE",
        "FAIL", "INFRA_ERROR", "INVALID_CONTRACT",
    }


@pytest.mark.parametrize("record", [StructuredOutcome, VerificationRecord])
def test_authority_records_have_no_public_data_constructor(record):
    with pytest.raises(TypeError):
        record()
    with pytest.raises(TypeError):
        record(status="FULL", discharged_operation_ids={"operation-main"})


@pytest.mark.parametrize("claim", [
    {"status": "FULL", "oracle_resolved": True},
    {"verified": True, "admission_id": "approved", "future_use_domain": "all"},
    {"telemetry_completeness": "COMPLETE", "worker_report": "all tests passed"},
])
def test_worker_shaped_claims_are_not_verification(claim):
    with pytest.raises(ValueError):
        evaluate_attempt_outcome(claim)
    with pytest.raises(ValueError):
        inspect_outcome(claim)


def test_constructing_an_uninitialized_instance_does_not_bypass_the_seal():
    with pytest.raises(ValueError):
        evaluate_attempt_outcome(object.__new__(VerificationRecord))


def _sealed_record(digest_type=str):
    raw = encode_canonical({"schema_version": verification_contract.VERIFICATION_SCHEMA_V5, "run_id": "run-1"})
    record = object.__new__(VerificationRecord)
    object.__setattr__(record, "_bytes", raw)
    object.__setattr__(record, "_digest", digest_type(hashlib.sha256(raw).hexdigest()))
    object.__setattr__(record, "_seal", verification_contract._SEAL)
    return record


class _AnswersOnlyFirst(str):
    compared = 0

    def __eq__(self, other):
        type(self).compared += 1
        return type(self).compared == 1

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = str.__hash__


class _UnsealsOnCompare(str):
    compared = 0
    record = None

    def __eq__(self, other):
        type(self).compared += 1
        object.__setattr__(type(self).record, "_seal", None)
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = str.__hash__


@pytest.mark.parametrize("planted", [_AnswersOnlyFirst, _UnsealsOnCompare])
@pytest.mark.parametrize("access", [
    lambda record: record.payload(),
    lambda record: record.to_dict(),
    lambda record: record.reference,
    verification_contract.require_verification_record,
    verification_contract.require_verification_payload,
])
def test_a_planted_digest_type_is_refused_before_it_is_compared(planted, access):
    record = _sealed_record(planted)
    planted.compared, planted.record = 0, record
    with pytest.raises(GoldRunViolation) as exc:
        access(record)
    assert exc.value.failure_code is GoldRunFailureCode.IDENTITY_MISMATCH
    assert planted.compared == 0


def test_a_sealed_record_decodes_once_per_call(monkeypatch):
    record = _sealed_record()
    decoded = []
    original = verification_contract.decode_canonical

    def counted(raw):
        decoded.append(raw)
        return original(raw)

    monkeypatch.setattr(verification_contract, "decode_canonical", counted)
    monkeypatch.setattr(verification_contract, "inspect_verification_record", lambda value: value)
    assert record.payload()["run_id"] == "run-1"
    assert len(decoded) == 1
    assert record.to_dict()["payload"]["run_id"] == "run-1"
    assert len(decoded) == 2
    assert verification_contract.require_verification_payload(record)["run_id"] == "run-1"
    assert len(decoded) == 3


def test_a_record_reference_is_its_checked_digest():
    record = _sealed_record()
    digest = hashlib.sha256(record._bytes).hexdigest()
    reference = record.reference
    assert (reference.kind, reference.ref_id, reference.schema_id, reference.sha256, reference.byte_length,
            reference.media_type) == (verification_contract.RefKind.ARTIFACT, digest,
            verification_contract.VERIFICATION_SCHEMA_V5, digest, len(record._bytes), "application/json")


def test_a_record_changed_after_its_seal_is_refused_on_every_call():
    record = _sealed_record()
    returned = record.payload()
    returned["run_id"] = "edited"
    assert record.payload()["run_id"] == "run-1"
    object.__setattr__(record, "_bytes", encode_canonical(
        {"schema_version": verification_contract.VERIFICATION_SCHEMA_V5, "run_id": "run-2"}))
    for access in (record.payload, record.to_dict, record.payload):
        with pytest.raises(GoldRunViolation) as exc:
            access()
        assert exc.value.failure_code is GoldRunFailureCode.IDENTITY_MISMATCH
