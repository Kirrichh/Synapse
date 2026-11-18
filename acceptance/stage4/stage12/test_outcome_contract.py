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


def _sealed_record(payload=None):
    raw = encode_canonical({"schema_version": verification_contract.VERIFICATION_SCHEMA_V5, "run_id": "run-1",
                            **(payload or {})})
    record = object.__new__(VerificationRecord)
    object.__setattr__(record, "_bytes", raw)
    object.__setattr__(record, "_digest", hashlib.sha256(raw).hexdigest())
    object.__setattr__(record, "_seal", verification_contract._SEAL)
    return record


def _plant_digest(record, kind):
    class Planted(str):
        def __eq__(self, other):
            self.compared += 1
            if kind == "unseals":
                object.__setattr__(record, "_seal", None)
                return True
            if kind == "answers-first":
                return self.compared == 1
            return str.__eq__(self, other)

        def __ne__(self, other):
            return not self.__eq__(other)

        __hash__ = str.__hash__

    planted = Planted(record._digest)
    planted.compared = 0
    object.__setattr__(record, "_digest", planted)


_SHAPE = (ValueError, None, "verification payload has an unknown shape")


# Characterizes the record contract as of 56585964: a digest planted after the seal is
# refused only where the record is checked again. Changing this is a contract decision.
@pytest.mark.parametrize("kind,to_dict", [
    ("plain", _SHAPE),
    ("answers-first", (GoldRunViolation, GoldRunFailureCode.IDENTITY_MISMATCH, "verification record is malformed")),
    ("unseals", (GoldRunViolation, GoldRunFailureCode.TYPE_MISMATCH, "verification must be evaluator-sealed")),
])
def test_a_planted_digest_is_refused_where_the_record_is_checked_again(kind, to_dict):
    for access in (lambda record: record.payload(), lambda record: record.reference,
                   verification_contract.require_verification_record):
        record = _sealed_record()
        _plant_digest(record, kind)
        access(record)
    record = _sealed_record()
    _plant_digest(record, kind)
    with pytest.raises(to_dict[0]) as exc:
        record.to_dict()
    assert getattr(exc.value, "failure_code", None) is to_dict[1]
    assert getattr(exc.value, "detail", str(exc.value)) == to_dict[2]


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
    for access in (record.payload, lambda: record.reference, record.to_dict, record.payload):
        with pytest.raises(GoldRunViolation) as exc:
            access()
        assert exc.value.failure_code is GoldRunFailureCode.IDENTITY_MISMATCH


@pytest.mark.parametrize("unseal", [False, True])
def test_a_guard_refusal_reads_its_record_again_before_persisting(monkeypatch, unseal):
    from types import SimpleNamespace

    from synapse.experiments.gold.runner import controller_recovery
    from synapse.experiments.gold.runner.controller_recovery import AttemptPhaseMaterializer

    worker_ref = verification_contract.HashBoundRef(
        verification_contract.RefKind.ARTIFACT, "1" * 64, "test/v1", "1" * 64, 1, "application/json")
    record = _sealed_record({"failure_codes": [], "worker_result_ref": worker_ref.to_dict()})
    persisted = []

    def verified_outcome(self, *, session, context):
        if unseal:
            object.__setattr__(record, "_seal", None)
        return SimpleNamespace(status=FinalStatus.UNRESOLVED, to_dict=lambda: {})

    monkeypatch.setattr(controller_recovery, "require_progress_payload", lambda progress: (b"{}", None))
    monkeypatch.setattr(controller_recovery, "promote_verified_use", lambda **kwargs: None)
    monkeypatch.setattr(controller_recovery, "GoldAttemptResult", SimpleNamespace(create=lambda **kwargs: kwargs))
    monkeypatch.setattr(AttemptPhaseMaterializer, "_verify", lambda self, *, session, context: record)
    monkeypatch.setattr(AttemptPhaseMaterializer, "_verified_outcome", verified_outcome)
    monkeypatch.setattr(AttemptPhaseMaterializer, "_persist_result",
                        lambda self, *, session, context, result: persisted.append(result))
    materializer = object.__new__(AttemptPhaseMaterializer)
    object.__setattr__(materializer, "_manifest", SimpleNamespace(run_id="run-1", gold_run_id="gold-1"))
    object.__setattr__(materializer, "_publisher", None)
    context = SimpleNamespace(attempt_index=0, attempt_id="attempt-1", context_sha256="0" * 64)
    persist = lambda: materializer._persist_guard_result(session=None, context=context, progress=None)
    if not unseal:
        persist()
        assert [item["worker_result_ref"] for item in persisted] == [worker_ref]
        return
    with pytest.raises(GoldRunViolation) as exc:
        persist()
    assert exc.value.failure_code is GoldRunFailureCode.TYPE_MISMATCH
    assert persisted == []
