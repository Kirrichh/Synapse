"""Cheap public-contract checks: untrusted labels cannot mint authority."""

import pytest

from synapse.experiments.gold.stage12.outcome import (
    FinalStatus, StructuredOutcome, evaluate_attempt_outcome, inspect_outcome,
)
from synapse.experiments.gold.stage12.verification import VerificationRecord


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
