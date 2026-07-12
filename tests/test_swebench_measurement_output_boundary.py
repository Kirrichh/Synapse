from __future__ import annotations

import ast
from dataclasses import replace
import inspect
import json
import math
from pathlib import Path
import subprocess

import pytest

import synapse.experiments.swebench.measurement_output as measurement_output_module
from synapse.experiments.swebench.gold_evidence import (
    EVIDENCE_REF_NAMESPACE,
    GoldEvidence,
    ValidatedGoldEvidence,
    _make_validated_gold_evidence,
)
from synapse.experiments.swebench.measurement_output import (
    AdmissionSourceKind,
    AdmissionStatus,
    CLAIM_VOCABULARY,
    CarryAuthority,
    DistilledEvidenceCandidate,
    EvidenceAdmissionDecision,
    EVIDENCE_ADMISSION_SCHEMA_VERSION,
    MeasurementLabel,
    MEASUREMENT_OUTPUT_SCHEMA_VERSION,
    SuccessOnlyMeasurementOutput,
    TELEMETRY_GATEWAY_REQUIRED_FIELDS,
    TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
    TelemetryGatewayCandidateValidation,
    TelemetryGatewayCandidateStatus,
    TelemetryGatewayStatus,
    build_success_only_measurement_output,
    candidate_from_validated_gold_evidence,
    evaluate_evidence_admission,
    evidence_admission_to_canonical_json,
    measurement_output_to_canonical_json,
    telemetry_gateway_validation_to_canonical_json,
    validate_canonical_telemetry_gateway_candidate,
)
from synapse.experiments.swebench.paired_measurement import (
    ALL_ATTEMPTS_RECORDED,
    CarryState,
    ExecutionOrder,
    MeasurementMode,
    PairedMeasurementMember,
    PairedMeasurementStatus,
    StatePolicy,
    build_paired_measurement_record,
)


BASE_COMMIT = "ea4a9392b918df0503956531c49ffb55f992872a"
PRODUCTION_PATH = Path("synapse/experiments/swebench/measurement_output.py")


def member(
    *,
    mode: MeasurementMode,
    carry_state: CarryState,
    run_id: str,
    diagnostics: dict[str, object] | None = None,
) -> PairedMeasurementMember:
    return PairedMeasurementMember(
        mode=mode,
        run_id=run_id,
        task_id="task-1",
        instance_id="repo__issue-1",
        base_revision="base-sha",
        replicate_id=0,
        resolved=True,
        infra_error=False,
        terminal_status="ORACLE_RESOLVED",
        attempt_count=1,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
        carry_state=carry_state,
        source_record_kind="test",
        diagnostics={
            "attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
            "attempts_observed_count": 1,
            "selected_attempt_count": 1,
            **(diagnostics or {}),
        },
    )


def baseline_member() -> PairedMeasurementMember:
    return member(
        mode=MeasurementMode.BASELINE,
        carry_state=CarryState.BASELINE_RAW_RETRY_CARRY,
        run_id="baseline-run",
    )


def gold_member() -> PairedMeasurementMember:
    return member(
        mode=MeasurementMode.GOLD_WITHOUT_CARRY,
        carry_state=CarryState.GOLD_WITHOUT_CARRY,
        run_id="gold-run",
    )


def success_pair():
    return build_paired_measurement_record(
        pair_id="pair-1",
        baseline=baseline_member(),
        gold=gold_member(),
        execution_order=ExecutionOrder.BASELINE_THEN_GOLD,
        cache_state_policy=StatePolicy.CLEAN,
        profile_state_policy=StatePolicy.CLEAN,
    )


def invalid_pair():
    return build_paired_measurement_record(
        pair_id="pair-1",
        baseline=baseline_member(),
        gold=member(
            mode=MeasurementMode.GOLD_WITH_CARRY,
            carry_state=CarryState.GOLD_WITH_CARRY,
            run_id="gold-run",
        ),
        execution_order=ExecutionOrder.BASELINE_THEN_GOLD,
        cache_state_policy=StatePolicy.CLEAN,
        profile_state_policy=StatePolicy.CLEAN,
    )


def evidence() -> GoldEvidence:
    return GoldEvidence(
        evidence_ref=f"{EVIDENCE_REF_NAMESPACE}run-1",
        verified_commit="a" * 40,
        report_path="reports/report.json",
        report_sha256="b" * 64,
        base_sha="c" * 40,
        task_contract_sha256="d" * 64,
        patch_sha256="e" * 64,
    )


def proof() -> ValidatedGoldEvidence:
    return _make_validated_gold_evidence(evidence())


def candidate(
    *,
    source_kind: AdmissionSourceKind = AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE,
    allowed_scope: tuple[str, ...] = ("src/a.py",),
    summary: str = "bounded evidence summary",
    claims: dict[str, object] | None = None,
    diagnostics: dict[str, object] | None = None,
) -> DistilledEvidenceCandidate:
    sealed = proof()
    validated = source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE
    return DistilledEvidenceCandidate(
        source_kind=source_kind,
        evidence_ref=sealed.evidence.evidence_ref if validated else None,
        verified_commit=sealed.verified_commit if validated else None,
        base_sha=sealed.base_sha if validated else None,
        task_contract_sha256=sealed.task_contract_sha256 if validated else None,
        patch_sha256=sealed.patch_sha256 if validated else None,
        source_evidence_identity_sha256=(
            sealed.evidence_identity_sha256 if validated else None
        ),
        allowed_scope=allowed_scope,
        summary=summary,
        claims={} if claims is None else claims,
        diagnostics={} if diagnostics is None else diagnostics,
    )


def admission_decision(**overrides) -> EvidenceAdmissionDecision:
    base = {
        "schema_version": "synapse.stage4.c2s3.evidence_admission/v2",
        "candidate": candidate(),
        "status": AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY,
        "carry_authority": CarryAuthority.DISTILLED_EVIDENCE_CANDIDATE,
        "admitted_to_application": False,
        "admitted_to_session_memory": False,
        "gold_with_carry_enabled": False,
        "scope_expansion_allowed": False,
        "raw_carry_authority_allowed": False,
        "overclaim_detected": False,
        "rejection_reasons": (),
        "diagnostics": {},
    }
    base.update(overrides)
    return EvidenceAdmissionDecision(**base)


def valid_telemetry_record() -> dict[str, object]:
    return {
        "llm_call_id": "call-1",
        "llm_call_accounting_category": "candidate_generation",
        "provider_id": "provider",
        "model_id": "model",
        "service_tier": "standard",
        "input_tokens": 10,
        "output_tokens": 5,
        "cached_read_input_tokens_if_reported": 0,
        "cache_write_input_tokens_if_reported": 0,
        "request_cost_if_reported": None,
        "mixed_unallocated_tokens": 0,
        "usage_source": "provider",
        "usage_consistency": "consistent",
        "primary_metric_status": "usable",
    }


def production_source() -> str:
    assert PRODUCTION_PATH.exists()
    return PRODUCTION_PATH.read_text(encoding="utf-8")


def changed_files() -> set[str]:
    committed = subprocess.run(
        ["git", "diff", "--name-only", f"{BASE_COMMIT}...HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    working = subprocess.run(
        ["git", "diff", "--name-only"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    status = subprocess.run(
        ["git", "status", "--short"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    status_paths = [line[3:] for line in status if line.startswith("?? ")]
    return set(committed + staged + working + status_paths)


def test_success_only_output_label_required() -> None:
    pair = success_pair()
    assert pair.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC

    output = build_success_only_measurement_output(pair)

    assert output.measurement_label is MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC
    assert output.token_bearing is False
    assert output.telemetry_gateway_status in (
        TelemetryGatewayStatus.MISSING,
        TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED,
    )
    assert output.performance_claim_allowed is False
    assert output.gold_with_carry_allowed is False


def test_c2s2_non_reusable_flags_preserved() -> None:
    output = build_success_only_measurement_output(success_pair())

    assert output.non_reusable_for_token_claims is True
    assert output.non_reusable_for_cost_claims is True
    assert output.non_reusable_for_wall_clock_claims is True
    assert output.non_reusable_for_economic_calibration is True
    assert output.performance_claim_allowed is False


def test_token_fields_are_non_reusable_not_reusable() -> None:
    output = build_success_only_measurement_output(success_pair(), token_fields_present=True)

    assert output.measurement_label is MeasurementLabel.TOKEN_BEARING_NON_REUSABLE
    assert output.token_bearing is True
    assert output.non_reusable_for_token_claims is True
    assert output.non_reusable_for_cost_claims is True
    assert output.measurement_label is not MeasurementLabel.TOKEN_BEARING_REUSABLE_AFTER_GATEWAY


def test_token_savings_claim_rejected() -> None:
    output = build_success_only_measurement_output(success_pair(), requested_claims={"token_savings": "10%"})

    assert output.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert output.diagnostics["overclaim_reasons"] != []


def test_cost_economic_roi_claims_rejected() -> None:
    cost = build_success_only_measurement_output(success_pair(), requested_claims={"cost_savings": "10%"})
    roi = build_success_only_measurement_output(success_pair(), requested_claims={"roi": "high"})
    economic = build_success_only_measurement_output(success_pair(), requested_claims={"economic_calibration": "complete"})

    assert cost.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert roi.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert economic.measurement_label is MeasurementLabel.INVALID_OVERCLAIM


def test_performance_wall_clock_claims_rejected() -> None:
    performance = build_success_only_measurement_output(success_pair(), requested_claims={"performance_improvement": "yes"})
    wall_clock = build_success_only_measurement_output(success_pair(), requested_claims={"wall_clock_speedup": "yes"})
    latency = build_success_only_measurement_output(success_pair(), requested_claims={"latency_improvement": "yes"})

    assert performance.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert performance.performance_claim_allowed is False
    assert wall_clock.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert wall_clock.performance_claim_allowed is False
    assert latency.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert latency.performance_claim_allowed is False


def test_target_gold_and_gold_with_carry_claims_rejected() -> None:
    explicit = build_success_only_measurement_output(success_pair(), target_gold_claim=True)
    textual_target = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "target Gold"})
    textual_carry = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "carry-enabled Gold"})
    measured = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "Gold-with-carry measured"})

    assert explicit.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert explicit.gold_with_carry_allowed is False
    assert textual_target.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert textual_target.gold_with_carry_allowed is False
    assert textual_carry.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert textual_carry.gold_with_carry_allowed is False
    assert measured.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert measured.gold_with_carry_allowed is False


def test_full_claim_rejected() -> None:
    explicit = build_success_only_measurement_output(success_pair(), full_verification_claim=True)
    gold_full = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "GOLD_FULL_VERIFIED"})
    full_verified = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "FULL_VERIFIED"})
    promotion = build_success_only_measurement_output(success_pair(), requested_claims={"claim": "FULL_PROMOTION"})

    assert explicit.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert gold_full.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert full_verified.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert promotion.measurement_label is MeasurementLabel.INVALID_OVERCLAIM


def test_non_success_only_source_pair_cannot_become_success_output() -> None:
    pair = invalid_pair()
    assert pair.status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC

    output = build_success_only_measurement_output(pair)

    assert output.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert output.diagnostics["source_pair_not_success_only"] is True


def test_candidate_can_be_built_from_validated_gold_evidence() -> None:
    sealed = proof()
    result = candidate_from_validated_gold_evidence(
        sealed,
        allowed_scope=("src/a.py",),
        summary="bounded summary",
    )

    assert result.evidence_ref == sealed.evidence.evidence_ref
    assert result.verified_commit == sealed.verified_commit
    assert result.base_sha == sealed.base_sha
    assert result.task_contract_sha256 == sealed.task_contract_sha256
    assert result.patch_sha256 == sealed.patch_sha256
    assert result.source_evidence_identity_sha256 == sealed.evidence_identity_sha256
    assert result.allowed_scope == ("src/a.py",)
    assert result.summary == "bounded summary"
    assert result.source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE


def test_unsafe_evidence_admission_api_is_absent() -> None:
    signature = inspect.signature(evaluate_evidence_admission)

    assert not hasattr(measurement_output_module, "candidate_from_gold_evidence")
    assert "validation_ok" not in signature.parameters
    with pytest.raises(TypeError):
        evaluate_evidence_admission(
            candidate(),
            proof=proof(),
            validation_ok=True,
            allowed_scope=("src/a.py",),
        )


def test_evidence_admission_requires_exact_runtime_contract_types() -> None:
    with pytest.raises(TypeError, match="candidate"):
        evaluate_evidence_admission(
            object(),  # type: ignore[arg-type]
            proof=None,
            allowed_scope=("src/a.py",),
        )
    with pytest.raises(TypeError, match="proof"):
        evaluate_evidence_admission(
            candidate(),
            proof=object(),  # type: ignore[arg-type]
            allowed_scope=("src/a.py",),
        )
    with pytest.raises(TypeError, match="application_integration_available"):
        evaluate_evidence_admission(
            candidate(),
            proof=proof(),
            allowed_scope=("src/a.py",),
            application_integration_available=1,  # type: ignore[arg-type]
        )


def test_nonvalidated_candidate_sources_cannot_carry_evidence_bindings() -> None:
    with pytest.raises(ValueError, match="evidence_ref"):
        replace(
            candidate(source_kind=AdmissionSourceKind.UNKNOWN),
            evidence_ref=f"{EVIDENCE_REF_NAMESPACE}forbidden",
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("evidence_ref", f"{EVIDENCE_REF_NAMESPACE}other"),
        ("verified_commit", "f" * 40),
        ("base_sha", "f" * 40),
        ("task_contract_sha256", "f" * 64),
        ("patch_sha256", "f" * 64),
        ("source_evidence_identity_sha256", "f" * 64),
    ),
)
def test_candidate_must_match_every_sealed_proof_field(
    field_name: str,
    replacement: str,
) -> None:
    changed = replace(candidate(), **{field_name: replacement})

    decision = evaluate_evidence_admission(
        changed,
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
    assert decision.rejection_reasons == (f"evidence_identity_mismatch:{field_name}",)


@pytest.mark.parametrize(
    ("proof_field", "replacement", "reason_field"),
    (
        ("evidence_identity_sha256", "f" * 64, "proof_identity"),
        ("verified_commit", "f" * 40, "proof_verified_commit"),
        ("base_sha", "f" * 40, "proof_base_sha"),
        ("task_contract_sha256", "f" * 64, "proof_task_contract_sha256"),
        ("patch_sha256", "f" * 64, "proof_patch_sha256"),
    ),
)
def test_admission_rechecks_sealed_proof_consistency(
    proof_field: str,
    replacement: str,
    reason_field: str,
) -> None:
    sealed = proof()
    object.__setattr__(sealed, proof_field, replacement)

    decision = evaluate_evidence_admission(
        candidate(),
        proof=sealed,
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
    assert decision.rejection_reasons == (f"evidence_identity_mismatch:{reason_field}",)


def test_gold_evidence_candidate_is_contract_admissible_only() -> None:
    decision = evaluate_evidence_admission(
        candidate(),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert decision.admitted_to_application is False
    assert decision.admitted_to_session_memory is False
    assert decision.gold_with_carry_enabled is False
    assert decision.scope_expansion_allowed is False
    assert decision.raw_carry_authority_allowed is False
    assert decision.carry_authority is CarryAuthority.DISTILLED_EVIDENCE_CANDIDATE


def test_missing_validation_proof_rejects_gold_evidence_candidate() -> None:
    decision = evaluate_evidence_admission(
        candidate(),
        proof=None,
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
    assert decision.rejection_reasons == ("missing_validation_proof",)


def test_raw_baseline_carry_source_rejected() -> None:
    raw = candidate(source_kind=AdmissionSourceKind.RAW_BASELINE_CARRY)
    decision = evaluate_evidence_admission(raw, proof=None, allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY
    assert decision.raw_carry_authority_allowed is False


def test_unknown_and_unvalidated_source_rejected() -> None:
    report = candidate(source_kind=AdmissionSourceKind.UNVALIDATED_REPORT)
    unknown = candidate(source_kind=AdmissionSourceKind.UNKNOWN)
    report_decision = evaluate_evidence_admission(report, proof=None, allowed_scope=("src/a.py",))
    unknown_decision = evaluate_evidence_admission(unknown, proof=None, allowed_scope=("src/a.py",))

    assert report_decision.status is AdmissionStatus.REJECTED_INVALID_SOURCE
    assert unknown_decision.status is AdmissionStatus.REJECTED_INVALID_SOURCE


def test_candidate_cannot_expand_allowed_scope() -> None:
    expanded = candidate(allowed_scope=("src/a.py", "src/b.py"))
    decision = evaluate_evidence_admission(expanded, proof=proof(), allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.REJECTED_SCOPE_VIOLATION
    assert decision.scope_expansion_allowed is False


def test_invalid_path_shape_rejected() -> None:
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("../secret.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("/abs/path.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("C:\\abs\\path.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("src//file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("src/./file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("src/../file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_validated_gold_evidence(proof(), allowed_scope=("src/\x00/file.py",), summary="summary")


def test_admission_rejects_target_gold_and_carry_overclaim() -> None:
    target = evaluate_evidence_admission(candidate(summary="target Gold"), proof=proof(), allowed_scope=("src/a.py",))
    carry = evaluate_evidence_admission(candidate(claims={"claim": "carry-enabled Gold"}), proof=proof(), allowed_scope=("src/a.py",))
    measured = evaluate_evidence_admission(candidate(summary="Gold-with-carry measured"), proof=proof(), allowed_scope=("src/a.py",))

    assert target.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert target.overclaim_detected is True
    assert carry.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert carry.overclaim_detected is True
    assert measured.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert measured.overclaim_detected is True


def test_admission_rejects_application_session_append_overclaim() -> None:
    app = evaluate_evidence_admission(candidate(claims={"application_appended": True}), proof=proof(), allowed_scope=("src/a.py",))
    session = evaluate_evidence_admission(candidate(claims={"session_memory_appended": True}), proof=proof(), allowed_scope=("src/a.py",))
    repo = evaluate_evidence_admission(candidate(claims={"repository_knowledge_admitted": True}), proof=proof(), allowed_scope=("src/a.py",))

    assert app.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert app.admitted_to_application is False
    assert app.admitted_to_session_memory is False
    assert session.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert session.admitted_to_application is False
    assert session.admitted_to_session_memory is False
    assert repo.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert repo.admitted_to_application is False
    assert repo.admitted_to_session_memory is False


def test_admission_rejects_full_claim() -> None:
    gold_full = evaluate_evidence_admission(candidate(claims={"claim": "GOLD_FULL_VERIFIED"}), proof=proof(), allowed_scope=("src/a.py",))
    full_verified = evaluate_evidence_admission(candidate(summary="FULL_VERIFIED"), proof=proof(), allowed_scope=("src/a.py",))
    promotion = evaluate_evidence_admission(candidate(diagnostics={"claim": "FULL_PROMOTION"}), proof=proof(), allowed_scope=("src/a.py",))

    assert gold_full.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert full_verified.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert promotion.status is AdmissionStatus.REJECTED_OVERCLAIM


def test_admission_rejects_token_cost_performance_claims() -> None:
    token = evaluate_evidence_admission(candidate(claims={"token_savings": "10%"}), proof=proof(), allowed_scope=("src/a.py",))
    cost = evaluate_evidence_admission(candidate(claims={"cost_savings": "10%"}), proof=proof(), allowed_scope=("src/a.py",))
    roi = evaluate_evidence_admission(candidate(claims={"roi": "high"}), proof=proof(), allowed_scope=("src/a.py",))
    economic = evaluate_evidence_admission(candidate(claims={"economic_calibration": "done"}), proof=proof(), allowed_scope=("src/a.py",))
    wall = evaluate_evidence_admission(candidate(claims={"wall_clock_speedup": "yes"}), proof=proof(), allowed_scope=("src/a.py",))
    perf = evaluate_evidence_admission(candidate(claims={"performance_improvement": "yes"}), proof=proof(), allowed_scope=("src/a.py",))

    assert token.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert cost.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert roi.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert economic.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert wall.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert perf.status is AdmissionStatus.REJECTED_OVERCLAIM


def test_roi_boundary_rejects_claim_tokens_without_false_positive_words() -> None:
    heroic = evaluate_evidence_admission(
        candidate(summary="heroic bugfix in parser"),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    intro = evaluate_evidence_admission(
        candidate(summary="introspection cleanup"),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    android = evaluate_evidence_admission(
        candidate(summary="android parser cleanup"),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    output = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "heroic parser cleanup"},
    )
    roi_admission = evaluate_evidence_admission(
        candidate(claims={"note": "roi:high"}),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    roi_output = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "roi:high"},
    )
    roi_phrase = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "project ROI analysis"},
    )
    roi_pct = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "roi_pct"},
    )
    roi_metric = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "roi-metric increased"},
    )
    roi_colon = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "roi:high"},
    )

    assert heroic.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert heroic.overclaim_detected is False
    assert intro.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert intro.overclaim_detected is False
    assert android.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert android.overclaim_detected is False
    assert output.measurement_label is MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC
    assert roi_admission.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert roi_output.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert roi_phrase.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert roi_pct.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert roi_metric.measurement_label is MeasurementLabel.INVALID_OVERCLAIM
    assert roi_colon.measurement_label is MeasurementLabel.INVALID_OVERCLAIM


def test_claim_vocabulary_allows_note_and_rejects_unknown_keys_deterministically() -> None:
    assert CLAIM_VOCABULARY == frozenset({"note"})
    allowed = evaluate_evidence_admission(
        candidate(claims={"note": "bounded diagnostic"}),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    rejected = evaluate_evidence_admission(
        candidate(
            claims={
                "performanceResult": "better",
                "claim": "text",
                "note": "target Gold token savings project ROI analysis",
            }
        ),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert allowed.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert rejected.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert rejected.rejection_reasons == (
        "unknown_claim_key:claim",
        "unknown_claim_key:performanceResult",
        "overclaim:target gold",
        "overclaim:token savings",
        "overclaim:roi",
    )
    assert len(rejected.rejection_reasons) == len(set(rejected.rejection_reasons))


@pytest.mark.parametrize(
    "claims",
    (
        {"claim": "text"},
        {"token_savings": "10%"},
        {"costReduction": "10%"},
        {"performanceResult": "better"},
    ),
)
def test_unknown_claim_key_is_rejected_without_key_substring_double_count(
    claims: dict[str, object],
) -> None:
    key = next(iter(claims))
    decision = evaluate_evidence_admission(
        candidate(claims=claims),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert decision.rejection_reasons == (f"unknown_claim_key:{key}",)


@pytest.mark.parametrize(
    "value",
    (
        "token savings",
        "target Gold",
        "carry-enabled Gold",
        "GOLD_FULL_VERIFIED",
        "project ROI analysis",
    ),
)
def test_allowed_claim_value_still_enforces_overclaim_blacklist(value: str) -> None:
    decision = evaluate_evidence_admission(
        candidate(claims={"note": value}),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert decision.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert decision.overclaim_detected is True


def test_diagnostics_keys_and_values_cannot_smuggle_authority() -> None:
    key_smuggle = evaluate_evidence_admission(
        candidate(diagnostics={"session_memory_appended": False}),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    value_smuggle = evaluate_evidence_admission(
        candidate(diagnostics={"note": "FULL_PROMOTION"}),
        proof=proof(),
        allowed_scope=("src/a.py",),
    )

    assert key_smuggle.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert key_smuggle.rejection_reasons == ("overclaim:session_memory_appended",)
    assert value_smuggle.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert value_smuggle.rejection_reasons == ("overclaim:full_promotion",)


def test_scope_is_canonical_sorted_set_and_duplicates_are_rejected() -> None:
    first = candidate_from_validated_gold_evidence(
        proof(),
        allowed_scope=("tests/test_b.py", "src/a.py"),
        summary="bounded",
    )
    second = candidate_from_validated_gold_evidence(
        proof(),
        allowed_scope=("src/a.py", "tests/test_b.py"),
        summary="bounded",
    )

    assert first.allowed_scope == ("src/a.py", "tests/test_b.py")
    assert second.allowed_scope == first.allowed_scope
    with pytest.raises(ValueError, match="allowed_scope contains duplicates: src/a.py"):
        candidate_from_validated_gold_evidence(
            proof(),
            allowed_scope=("src/a.py", "src/a.py"),
            summary="bounded",
        )
    with pytest.raises(ValueError, match="allowed_scope contains duplicates: src/a.py"):
        candidate_from_validated_gold_evidence(
            proof(),
            allowed_scope=("src\\a.py", "src/a.py"),
            summary="bounded",
        )


def test_application_integration_flag_cannot_create_append_authority() -> None:
    decision = evaluate_evidence_admission(
        candidate(),
        proof=proof(),
        allowed_scope=("src/a.py",),
        application_integration_available=True,
    )

    assert decision.admitted_to_application is False
    assert decision.admitted_to_session_memory is False
    assert decision.gold_with_carry_enabled is False


def test_measurement_contract_json_trees_are_deeply_immutable_and_detached() -> None:
    claims_source = {"note": {"items": ["a", "b"]}}
    diagnostics_source = {"nested": {"items": [1, 2]}}
    built_candidate = candidate(claims=claims_source, diagnostics=diagnostics_source)
    decision = evaluate_evidence_admission(
        built_candidate,
        proof=proof(),
        allowed_scope=("src/a.py",),
    )
    output = build_success_only_measurement_output(
        success_pair(),
        requested_claims=claims_source,
        admission_decision=decision,
    )
    telemetry = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))
    decision_source = {"nested": {"items": ["decision"]}}
    output_source = {
        "nested": {"items": ["output"]},
        "telemetry_gateway_integrated": False,
    }
    telemetry_source = {
        "gateway_integrated": False,
        "runtime_gateway_authority": False,
        "nested": {"items": ["telemetry"]},
    }
    decision = replace(decision, diagnostics=decision_source)
    output = replace(output, diagnostics=output_source)
    telemetry = replace(telemetry, diagnostics=telemetry_source)
    structural_source = {0: ("llm_call_id",)}
    missing_validation = replace(
        telemetry,
        status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
        missing_fields_by_index=structural_source,
    )
    output_before = measurement_output_to_canonical_json(output)

    claims_source["note"]["items"].append("source mutation")  # type: ignore[index,union-attr]
    diagnostics_source["nested"]["items"].append(3)  # type: ignore[index,union-attr]
    decision_source["nested"]["items"].append("mutated")  # type: ignore[index,union-attr]
    output_source["nested"]["items"].append("mutated")  # type: ignore[index,union-attr]
    telemetry_source["nested"]["items"].append("mutated")  # type: ignore[index,union-attr]
    structural_source[0] = ("provider_id",)
    detached = decision.to_dict()
    detached["candidate"]["claims"]["note"]["items"].append("detached mutation")

    assert built_candidate.claims["note"]["items"] == ("a", "b")
    assert built_candidate.diagnostics["nested"]["items"] == (1, 2)
    assert decision.diagnostics["nested"]["items"] == ("decision",)
    assert output.diagnostics["nested"]["items"] == ("output",)
    assert telemetry.diagnostics["nested"]["items"] == ("telemetry",)
    assert missing_validation.missing_fields_by_index[0] == ("llm_call_id",)
    with pytest.raises(TypeError):
        built_candidate.claims["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        decision.diagnostics["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        output.diagnostics["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        telemetry.diagnostics["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        telemetry.missing_fields_by_index[0] = ("field",)  # type: ignore[index]
    assert measurement_output_to_canonical_json(output) == output_before


class MeasurementToDictSentinel:
    def __init__(self) -> None:
        self.called = False

    def to_dict(self) -> dict[str, object]:
        self.called = True
        return {"unsafe": True}


@pytest.mark.parametrize(
    "invalid",
    (
        {1: "integer key"},
        {1: "collision", "1": "string key"},
        {"value": math.nan},
        {"value": math.inf},
        {"value": -math.inf},
        {"value": b"bytes"},
        {"value": {"set"}},
        {"value": object()},
    ),
)
def test_measurement_json_trees_reject_unsupported_values(invalid: object) -> None:
    with pytest.raises(ValueError):
        candidate(claims=invalid)  # type: ignore[arg-type]


def test_measurement_json_tree_never_invokes_arbitrary_to_dict() -> None:
    sentinel = MeasurementToDictSentinel()

    with pytest.raises(ValueError):
        candidate(diagnostics={"value": sentinel})

    assert sentinel.called is False

    with pytest.raises(TypeError, match="output"):
        measurement_output_to_canonical_json(sentinel)  # type: ignore[arg-type]
    assert sentinel.called is False


def test_telemetry_gateway_candidate_requires_llm_call_accounting_category() -> None:
    record = valid_telemetry_record()
    assert "llm_call_accounting_category" in record
    del record["llm_call_accounting_category"]

    validation = validate_canonical_telemetry_gateway_candidate((record,))

    assert validation.status is TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD
    assert "llm_call_accounting_category" in validation.missing_fields_by_index[0]


def test_accounting_category_alias_rejected() -> None:
    record = valid_telemetry_record()
    assert "llm_call_accounting_category" in record
    del record["llm_call_accounting_category"]
    record["accounting_category"] = "candidate_generation"

    validation = validate_canonical_telemetry_gateway_candidate((record,))

    assert validation.status is TelemetryGatewayCandidateStatus.REJECTED_FORBIDDEN_ALIAS
    assert "accounting_category" in validation.forbidden_alias_fields_by_index[0]
    assert "llm_call_accounting_category" in validation.missing_fields_by_index[0]


def test_stage3a_token_record_is_not_canonical_gateway() -> None:
    stage3a_record = {
        "arm": "BASELINE",
        "usage_source": "UNAVAILABLE",
        "primary_metric_status": "UNAVAILABLE",
        "usage_consistency": "MISSING_TOTAL",
        "input_tokens": None,
        "output_tokens": None,
        "thinking_tokens": None,
        "total_tokens": None,
        "thinking_included": False,
        "diagnostics": {},
    }

    validation = validate_canonical_telemetry_gateway_candidate((stage3a_record,))

    assert validation.status is TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD
    assert "llm_call_id" in validation.missing_fields_by_index[0]
    assert "llm_call_accounting_category" in validation.missing_fields_by_index[0]


def test_valid_future_telemetry_candidate_validates_but_does_not_integrate_gateway() -> None:
    validation = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))
    output = build_success_only_measurement_output(success_pair(), token_fields_present=True)

    assert validation.status is TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE
    assert output.telemetry_gateway_status is not TelemetryGatewayStatus.CANDIDATE_VALIDATED_BUT_NOT_INTEGRATED
    assert output.measurement_label is not MeasurementLabel.TOKEN_BEARING_REUSABLE_AFTER_GATEWAY


def test_telemetry_v2_shape_removes_old_alias_api_and_requires_exact_fields() -> None:
    validation = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))

    assert TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION.endswith("/v2")
    assert validation.required_fields == TELEMETRY_GATEWAY_REQUIRED_FIELDS
    assert not hasattr(TelemetryGatewayCandidateStatus, "REJECTED_ALIAS_ONLY")
    assert not hasattr(validation, "alias_only_fields_by_index")
    for required_fields in (
        TELEMETRY_GATEWAY_REQUIRED_FIELDS[:-1],
        TELEMETRY_GATEWAY_REQUIRED_FIELDS + ("extra",),
        tuple(reversed(TELEMETRY_GATEWAY_REQUIRED_FIELDS)),
        TELEMETRY_GATEWAY_REQUIRED_FIELDS + (TELEMETRY_GATEWAY_REQUIRED_FIELDS[-1],),
    ):
        with pytest.raises(ValueError, match="required_fields"):
            replace(validation, required_fields=required_fields)


def test_forbidden_telemetry_alias_is_unconditional_and_preserves_other_errors() -> None:
    alias_and_canonical = valid_telemetry_record()
    alias_and_canonical["accounting_category"] = alias_and_canonical[
        "llm_call_accounting_category"
    ]
    alias_missing_and_type = valid_telemetry_record()
    del alias_missing_and_type["llm_call_accounting_category"]
    alias_missing_and_type["accounting_category"] = "other"
    alias_missing_and_type["input_tokens"] = True

    canonical_result = validate_canonical_telemetry_gateway_candidate(
        (alias_and_canonical,)
    )
    combined_result = validate_canonical_telemetry_gateway_candidate(
        (alias_missing_and_type,)
    )

    assert canonical_result.status is TelemetryGatewayCandidateStatus.REJECTED_FORBIDDEN_ALIAS
    assert canonical_result.forbidden_alias_fields_by_index[0] == ("accounting_category",)
    assert combined_result.status is TelemetryGatewayCandidateStatus.REJECTED_FORBIDDEN_ALIAS
    assert combined_result.forbidden_alias_fields_by_index[0] == ("accounting_category",)
    assert combined_result.missing_fields_by_index[0] == ("llm_call_accounting_category",)
    assert combined_result.field_type_errors_by_index[0] == (
        "input_tokens:expected_non_negative_int",
    )


@pytest.mark.parametrize(
    ("field_name", "invalid", "expected_error"),
    (
        ("llm_call_id", "", "llm_call_id:expected_non_empty_string"),
        ("provider_id", "   ", "provider_id:expected_non_empty_string"),
        ("input_tokens", "1", "input_tokens:expected_non_negative_int"),
        ("input_tokens", True, "input_tokens:expected_non_negative_int"),
        ("output_tokens", 1.0, "output_tokens:expected_non_negative_int"),
        ("mixed_unallocated_tokens", -1, "mixed_unallocated_tokens:expected_non_negative_int"),
        ("cached_read_input_tokens_if_reported", "1", "cached_read_input_tokens_if_reported:expected_optional_non_negative_int"),
        ("cached_read_input_tokens_if_reported", False, "cached_read_input_tokens_if_reported:expected_optional_non_negative_int"),
        ("cache_write_input_tokens_if_reported", 1.0, "cache_write_input_tokens_if_reported:expected_optional_non_negative_int"),
        ("cache_write_input_tokens_if_reported", -1, "cache_write_input_tokens_if_reported:expected_optional_non_negative_int"),
        ("request_cost_if_reported", True, "request_cost_if_reported:expected_optional_non_negative_finite_number"),
        ("request_cost_if_reported", -1, "request_cost_if_reported:expected_optional_non_negative_finite_number"),
        ("request_cost_if_reported", math.nan, "request_cost_if_reported:expected_optional_non_negative_finite_number"),
        ("request_cost_if_reported", math.inf, "request_cost_if_reported:expected_optional_non_negative_finite_number"),
        ("request_cost_if_reported", -math.inf, "request_cost_if_reported:expected_optional_non_negative_finite_number"),
        ("request_cost_if_reported", "1.0", "request_cost_if_reported:expected_optional_non_negative_finite_number"),
    ),
)
def test_telemetry_rejects_invalid_required_field_types(
    field_name: str,
    invalid: object,
    expected_error: str,
) -> None:
    record = valid_telemetry_record()
    record[field_name] = invalid

    validation = validate_canonical_telemetry_gateway_candidate((record,))

    assert validation.status is TelemetryGatewayCandidateStatus.REJECTED_FIELD_TYPE
    assert validation.field_type_errors_by_index[0] == (expected_error,)


@pytest.mark.parametrize("cost", (None, 0, 3, 0.0, 1.25))
def test_telemetry_accepts_valid_optional_request_cost(cost: object) -> None:
    record = valid_telemetry_record()
    record["request_cost_if_reported"] = cost

    validation = validate_canonical_telemetry_gateway_candidate((record,))

    assert validation.status is TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE


def test_telemetry_rejects_invalid_record_container_non_mapping_and_extra_values() -> None:
    for invalid_records in ("records", b"records", {"record": 1}):
        with pytest.raises(ValueError, match="records must be a sequence"):
            validate_canonical_telemetry_gateway_candidate(invalid_records)  # type: ignore[arg-type]
    non_mapping = validate_canonical_telemetry_gateway_candidate(
        (valid_telemetry_record(), "not-a-record")  # type: ignore[arg-type]
    )
    non_string_key_record = valid_telemetry_record()
    non_string_key_record[1] = "bad key"  # type: ignore[index]
    non_string_key = validate_canonical_telemetry_gateway_candidate(
        (non_string_key_record,)
    )
    unsupported_extra_record = valid_telemetry_record()
    unsupported_extra_record["extension"] = object()
    unsupported_extra = validate_canonical_telemetry_gateway_candidate(
        (unsupported_extra_record,)
    )
    valid_extra_record = valid_telemetry_record()
    valid_extra_record["extension"] = {"nested": [1, "two"]}
    valid_extra = validate_canonical_telemetry_gateway_candidate((valid_extra_record,))

    assert non_mapping.status is TelemetryGatewayCandidateStatus.REJECTED_NON_MAPPING_RECORD
    assert non_mapping.non_mapping_record_index == 1
    assert non_mapping.missing_fields_by_index == {}
    assert non_mapping.forbidden_alias_fields_by_index == {}
    assert non_mapping.field_type_errors_by_index == {}
    assert non_string_key.status is TelemetryGatewayCandidateStatus.REJECTED_FIELD_TYPE
    assert non_string_key.field_type_errors_by_index[0] == ("record_key:expected_string",)
    assert unsupported_extra.status is TelemetryGatewayCandidateStatus.REJECTED_FIELD_TYPE
    assert unsupported_extra.field_type_errors_by_index[0] == (
        "extension:unsupported_json_value",
    )
    assert valid_extra.status is TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE


def test_telemetry_type_error_order_is_required_then_key_then_sorted_extensions() -> None:
    record = valid_telemetry_record()
    record["input_tokens"] = True
    record["output_tokens"] = "5"
    record[2] = "bad key"  # type: ignore[index]
    record["z_extra"] = object()
    record["a_extra"] = object()

    validation = validate_canonical_telemetry_gateway_candidate((record,))

    assert validation.field_type_errors_by_index[0] == (
        "input_tokens:expected_non_negative_int",
        "output_tokens:expected_non_negative_int",
        "record_key:expected_string",
        "a_extra:unsupported_json_value",
        "z_extra:unsupported_json_value",
    )


def test_direct_output_dataclass_rejects_reserved_states() -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError):
        SuccessOnlyMeasurementOutput(
            **{
                **base,
                "measurement_label": MeasurementLabel.TOKEN_BEARING_REUSABLE_AFTER_GATEWAY,
            }
        )
    with pytest.raises(ValueError):
        SuccessOnlyMeasurementOutput(
            **{
                **base,
                "telemetry_gateway_status": TelemetryGatewayStatus.CANDIDATE_VALIDATED_BUT_NOT_INTEGRATED,
            }
        )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    (
        ("replicate_id", True),
        ("baseline_attempt_count", False),
        ("gold_attempt_count", "1"),
        ("gold_attempt_count", 1.0),
        ("baseline_attempt_count", -1),
    ),
)
def test_output_integer_fields_are_strict(field_name: str, invalid: object) -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError, match=field_name):
        SuccessOnlyMeasurementOutput(**{**base, field_name: invalid})


@pytest.mark.parametrize(
    "overrides",
    (
        {"baseline_resolved": True, "baseline_infra_error": True},
        {"baseline_resolved": False, "baseline_infra_error": True},
        {"baseline_resolved": None, "baseline_infra_error": False},
        {"gold_resolved": "true", "gold_infra_error": False},
    ),
)
def test_output_rejects_resolved_infra_contradictions(
    overrides: dict[str, object],
) -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError):
        SuccessOnlyMeasurementOutput(**{**base, **overrides})


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "token_bearing": True,
            "telemetry_gateway_status": TelemetryGatewayStatus.MISSING,
        },
        {
            "token_bearing": False,
            "telemetry_gateway_status": TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED,
        },
        {
            "measurement_label": MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC,
            "token_bearing": True,
            "telemetry_gateway_status": TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED,
        },
        {
            "measurement_label": MeasurementLabel.TOKEN_BEARING_NON_REUSABLE,
            "token_bearing": False,
            "telemetry_gateway_status": TelemetryGatewayStatus.MISSING,
        },
        {
            "source_pair_status": PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY,
            "measurement_label": MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC,
        },
    ),
)
def test_output_rejects_label_token_telemetry_matrix(
    overrides: dict[str, object],
) -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError):
        SuccessOnlyMeasurementOutput(**{**base, **overrides})


def test_invalid_output_requires_explicit_nonempty_reasons() -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError, match="overclaim_reasons"):
        SuccessOnlyMeasurementOutput(
            **{
                **base,
                "measurement_label": MeasurementLabel.INVALID_OVERCLAIM,
                "diagnostics": {"overclaim_reasons": []},
            }
        )
    with pytest.raises(ValueError, match="source_pair_not_success_only"):
        SuccessOnlyMeasurementOutput(
            **{
                **base,
                "source_pair_status": PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY,
                "measurement_label": MeasurementLabel.INVALID_OVERCLAIM,
                "diagnostics": {"overclaim_reasons": ["other_reason"]},
            }
        )


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    (
        ("non_reusable_for_token_claims", False),
        ("non_reusable_for_cost_claims", False),
        ("non_reusable_for_wall_clock_claims", False),
        ("non_reusable_for_economic_calibration", False),
        ("performance_claim_allowed", True),
        ("gold_with_carry_allowed", True),
        ("admitted_to_application", True),
        ("admitted_to_session_memory", True),
        ("raw_carry_authority_allowed", True),
    ),
)
def test_output_rejects_fixed_authority_changes(
    field_name: str,
    invalid: bool,
) -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError, match=field_name):
        SuccessOnlyMeasurementOutput(**{**base, field_name: invalid})


@pytest.mark.parametrize(
    "diagnostics",
    (
        {"telemetry_gateway_integrated": True},
        {"application_appended": True},
        {"session_memory_appended": True},
        {"raw_carry_authority": True},
        {"gold_with_carry_enabled": True},
        {"full_verified": True},
        {"performance_claim_allowed": True},
    ),
)
def test_output_rejects_diagnostic_authority_contradictions(
    diagnostics: dict[str, object],
) -> None:
    base = build_success_only_measurement_output(success_pair()).to_dict()

    with pytest.raises(ValueError, match="diagnostics"):
        SuccessOnlyMeasurementOutput(**{**base, "diagnostics": diagnostics})


def test_output_builder_requires_exact_contract_input_types() -> None:
    with pytest.raises(TypeError, match="pair"):
        build_success_only_measurement_output(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="token_fields_present"):
        build_success_only_measurement_output(success_pair(), token_fields_present=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requested_claims"):
        build_success_only_measurement_output(success_pair(), requested_claims=[])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="admission_decision"):
        build_success_only_measurement_output(success_pair(), admission_decision=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="target_gold_claim"):
        build_success_only_measurement_output(success_pair(), target_gold_claim=1)  # type: ignore[arg-type]


def test_admission_decision_constructor_enforces_status_matrix_and_authority() -> None:
    valid = admission_decision()

    with pytest.raises(ValueError, match="carry_authority"):
        replace(valid, carry_authority=CarryAuthority.DIAGNOSTIC_CONTEXT_ONLY)
    with pytest.raises(ValueError, match="rejection_reasons"):
        replace(valid, rejection_reasons=("unexpected",))
    rejected = EvidenceAdmissionDecision(
        schema_version=EVIDENCE_ADMISSION_SCHEMA_VERSION,
        candidate=candidate(),
        status=AdmissionStatus.REJECTED_INVALID_SOURCE,
        carry_authority=CarryAuthority.DIAGNOSTIC_CONTEXT_ONLY,
        admitted_to_application=False,
        admitted_to_session_memory=False,
        gold_with_carry_enabled=False,
        scope_expansion_allowed=False,
        raw_carry_authority_allowed=False,
        overclaim_detected=False,
        rejection_reasons=("invalid_source",),
        diagnostics={},
    )
    with pytest.raises(ValueError, match="overclaim_detected"):
        replace(rejected, overclaim_detected=True)
    with pytest.raises(ValueError, match="diagnostics.admitted_to_application"):
        replace(rejected, diagnostics={"admitted_to_application": True})


def test_telemetry_validation_constructor_enforces_status_matrix_and_indices() -> None:
    valid = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))

    with pytest.raises(ValueError, match="structural fields"):
        replace(valid, missing_fields_by_index={0: ("llm_call_id",)})
    with pytest.raises(ValueError, match="invalid index"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
            missing_fields_by_index={True: ("llm_call_id",)},
        )
    with pytest.raises(ValueError, match="invalid index"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
            missing_fields_by_index={1: ("llm_call_id",)},
        )
    with pytest.raises(ValueError, match="invalid index"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
            missing_fields_by_index={"0": ("llm_call_id",)},
        )
    with pytest.raises(ValueError, match="invalid index"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
            missing_fields_by_index={-1: ("llm_call_id",)},
        )
    with pytest.raises(ValueError, match="non-empty tuples"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD,
            missing_fields_by_index={0: ()},
        )
    with pytest.raises(ValueError, match="non-empty string"):
        replace(
            valid,
            status=TelemetryGatewayCandidateStatus.REJECTED_FIELD_TYPE,
            field_type_errors_by_index={0: ("",)},
        )
    with pytest.raises(ValueError, match="gateway_integrated"):
        replace(valid, diagnostics={"gateway_integrated": True, "runtime_gateway_authority": False})
    with pytest.raises(ValueError, match="runtime_gateway_authority"):
        replace(valid, diagnostics={"gateway_integrated": False})


def test_canonical_json_deterministic_for_output() -> None:
    first = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": {"z_key": "z", "a_key": "a"}},
    )
    second = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": {"a_key": "a", "z_key": "z"}},
    )
    first_json = measurement_output_to_canonical_json(first)
    second_json = measurement_output_to_canonical_json(second)
    parsed = json.loads(first_json)

    assert first_json == second_json
    assert first_json.endswith("\n")
    assert first_json.index('"a_key"') < first_json.index('"z_key"')
    assert parsed["measurement_label"] == MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC.value
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_canonical_json_deterministic_for_admission_decision() -> None:
    first_candidate = candidate(claims={"note": {"z_key": "z", "a_key": "a"}}, diagnostics={"y_key": "y", "b_key": "b"})
    second_candidate = candidate(claims={"note": {"a_key": "a", "z_key": "z"}}, diagnostics={"b_key": "b", "y_key": "y"})
    first = admission_decision(candidate=first_candidate, diagnostics={"z_key": "z", "a_key": "a"})
    second = admission_decision(candidate=second_candidate, diagnostics={"a_key": "a", "z_key": "z"})
    first_json = evidence_admission_to_canonical_json(first)
    second_json = evidence_admission_to_canonical_json(second)
    parsed = json.loads(first_json)

    assert first_json == second_json
    assert first_json.endswith("\n")
    assert first_json.index('"a_key"') < first_json.index('"z_key"')
    assert first_json.index('"b_key"') < first_json.index('"y_key"')
    assert parsed["status"] == AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY.value
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_canonical_json_deterministic_for_telemetry_validation() -> None:
    first = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))
    second = validate_canonical_telemetry_gateway_candidate((valid_telemetry_record(),))
    first_json = telemetry_gateway_validation_to_canonical_json(first)
    second_json = telemetry_gateway_validation_to_canonical_json(second)
    parsed = json.loads(first_json)

    assert first_json == second_json
    assert first_json.endswith("\n")
    assert first_json.index('"diagnostics"') < first_json.index('"missing_fields_by_index"')
    assert parsed["status"] == TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE.value
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_measurement_output_v1_exact_canonical_fixture() -> None:
    output = SuccessOnlyMeasurementOutput(
        schema_version=MEASUREMENT_OUTPUT_SCHEMA_VERSION,
        pair_id="pair-compat-1",
        source_pair_status=PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC.value,
        task_id="task-compat-1",
        instance_id="repo__issue-compat-1",
        base_revision="base-compat-1",
        replicate_id=2,
        baseline_run_id="baseline-compat-1",
        gold_run_id="gold-compat-1",
        baseline_resolved=True,
        gold_resolved=True,
        baseline_infra_error=False,
        gold_infra_error=False,
        baseline_attempt_count=2,
        gold_attempt_count=3,
        oracle_config_fingerprint="oracle-config-compat",
        oracle_environment_fingerprint_alignment="MATCH",
        environment_fingerprint_alignment="MATCH",
        carry_state_baseline=CarryState.BASELINE_RAW_RETRY_CARRY.value,
        carry_state_gold=CarryState.GOLD_WITHOUT_CARRY.value,
        measurement_label=MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC,
        telemetry_gateway_status=TelemetryGatewayStatus.MISSING,
        token_bearing=False,
        non_reusable_for_token_claims=True,
        non_reusable_for_cost_claims=True,
        non_reusable_for_wall_clock_claims=True,
        non_reusable_for_economic_calibration=True,
        performance_claim_allowed=False,
        gold_with_carry_allowed=False,
        diagnostics={
            "overclaim_reasons": [],
            "requested_claims": {"note": "bounded diagnostic"},
            "source_pair_not_success_only": False,
            "telemetry_gateway_integrated": False,
            "token_fields_present": False,
        },
    )
    expected = """{
  "admission_status": null,
  "admitted_to_application": false,
  "admitted_to_session_memory": false,
  "base_revision": "base-compat-1",
  "baseline_attempt_count": 2,
  "baseline_infra_error": false,
  "baseline_resolved": true,
  "baseline_run_id": "baseline-compat-1",
  "carry_state_baseline": "BASELINE_RAW_RETRY_CARRY",
  "carry_state_gold": "GOLD_WITHOUT_CARRY",
  "diagnostics": {
    "overclaim_reasons": [],
    "requested_claims": {
      "note": "bounded diagnostic"
    },
    "source_pair_not_success_only": false,
    "telemetry_gateway_integrated": false,
    "token_fields_present": false
  },
  "environment_fingerprint_alignment": "MATCH",
  "gold_attempt_count": 3,
  "gold_infra_error": false,
  "gold_resolved": true,
  "gold_run_id": "gold-compat-1",
  "gold_with_carry_allowed": false,
  "instance_id": "repo__issue-compat-1",
  "measurement_label": "SUCCESS_ONLY_DIAGNOSTIC",
  "non_reusable_for_cost_claims": true,
  "non_reusable_for_economic_calibration": true,
  "non_reusable_for_token_claims": true,
  "non_reusable_for_wall_clock_claims": true,
  "oracle_config_fingerprint": "oracle-config-compat",
  "oracle_environment_fingerprint_alignment": "MATCH",
  "pair_id": "pair-compat-1",
  "performance_claim_allowed": false,
  "raw_carry_authority_allowed": false,
  "replicate_id": 2,
  "schema_version": "synapse.stage4.c2s3.measurement_output/v1",
  "source_pair_status": "PAIRED_SUCCESS_ONLY_DIAGNOSTIC",
  "task_id": "task-compat-1",
  "telemetry_gateway_status": "MISSING",
  "token_bearing": false
}
"""

    serialized = measurement_output_to_canonical_json(output)

    assert serialized == expected
    assert serialized.endswith("\n")
    assert json.loads(serialized)["measurement_label"] == "SUCCESS_ONLY_DIAGNOSTIC"
    assert measurement_output_to_canonical_json(output) == serialized


def test_evidence_admission_v2_exact_canonical_fixture() -> None:
    source = GoldEvidence(
        evidence_ref="refs/synapse/change/evidence/compat-1",
        verified_commit="a" * 40,
        report_path="reports/compat-report.json",
        report_sha256="b" * 64,
        base_sha="c" * 40,
        task_contract_sha256="d" * 64,
        patch_sha256="e" * 64,
    )
    sealed = _make_validated_gold_evidence(source)
    built_candidate = candidate_from_validated_gold_evidence(
        sealed,
        allowed_scope=("src/a.py",),
        summary="bounded evidence summary",
        claims={"note": "bounded diagnostic"},
        diagnostics={},
    )
    decision = evaluate_evidence_admission(
        built_candidate,
        proof=sealed,
        allowed_scope=("src/a.py",),
    )
    expected = """{
  "admitted_to_application": false,
  "admitted_to_session_memory": false,
  "candidate": {
    "allowed_scope": [
      "src/a.py"
    ],
    "base_sha": "cccccccccccccccccccccccccccccccccccccccc",
    "claims": {
      "note": "bounded diagnostic"
    },
    "diagnostics": {},
    "evidence_ref": "refs/synapse/change/evidence/compat-1",
    "patch_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "source_evidence_identity_sha256": "e48a568e99b95e081ee0091a48bf9227d5b0c0aff61333c53b9d8e3c83e64f55",
    "source_kind": "VALIDATED_GOLD_EVIDENCE",
    "summary": "bounded evidence summary",
    "task_contract_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "verified_commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "carry_authority": "DISTILLED_EVIDENCE_CANDIDATE",
  "diagnostics": {
    "allowed_scope": [
      "src/a.py"
    ],
    "application_integration_available": false,
    "candidate_scope": [
      "src/a.py"
    ],
    "contract_only": true
  },
  "gold_with_carry_enabled": false,
  "overclaim_detected": false,
  "raw_carry_authority_allowed": false,
  "rejection_reasons": [],
  "schema_version": "synapse.stage4.c2s3.evidence_admission/v2",
  "scope_expansion_allowed": false,
  "status": "ADMISSIBLE_CONTRACT_ONLY"
}
"""

    serialized = evidence_admission_to_canonical_json(decision)

    assert serialized == expected
    assert serialized.endswith("\n")
    assert json.loads(serialized)["schema_version"] == EVIDENCE_ADMISSION_SCHEMA_VERSION
    assert evidence_admission_to_canonical_json(decision) == serialized


def test_telemetry_validation_v2_exact_canonical_fixture() -> None:
    record = valid_telemetry_record()
    record.update(
        {
            "provider_id": "ollama",
            "model_id": "qwen3-coder:30b",
            "service_tier": "local",
        }
    )
    validation = validate_canonical_telemetry_gateway_candidate((record,))
    expected = """{
  "diagnostics": {
    "gateway_integrated": false,
    "runtime_gateway_authority": false
  },
  "field_type_errors_by_index": {},
  "forbidden_alias_fields_by_index": {},
  "missing_fields_by_index": {},
  "non_mapping_record_index": null,
  "record_count": 1,
  "required_fields": [
    "llm_call_id",
    "llm_call_accounting_category",
    "provider_id",
    "model_id",
    "service_tier",
    "input_tokens",
    "output_tokens",
    "cached_read_input_tokens_if_reported",
    "cache_write_input_tokens_if_reported",
    "request_cost_if_reported",
    "mixed_unallocated_tokens",
    "usage_source",
    "usage_consistency",
    "primary_metric_status"
  ],
  "schema_version": "synapse.stage4.c2s3.telemetry_gateway_validation/v2",
  "status": "VALID_CANONICAL_CANDIDATE"
}
"""

    serialized = telemetry_gateway_validation_to_canonical_json(validation)

    assert serialized == expected
    assert serialized.endswith("\n")
    assert json.loads(serialized)["schema_version"] == TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION
    assert telemetry_gateway_validation_to_canonical_json(validation) == serialized


def test_no_forbidden_imports() -> None:
    tree = ast.parse(production_source())
    imported: list[str] = []
    import_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
                import_names.append(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
            for alias in node.names:
                import_names.append(alias.name)

    assert "synapse.experiments.swebench.carry" not in imported
    assert "synapse.experiments.swebench.baseline" not in imported
    assert "synapse.experiments.swebench.telemetry" not in imported
    assert "synapse.experiments.swebench.contract" not in imported
    assert "synapse.experiments.swebench.gold_runner" not in imported
    assert "synapse.experiments.swebench.gold_oracle_binding" not in imported
    assert "synapse.change" not in imported
    assert "synapse.worker" not in imported
    assert "synapse.interpreter" not in imported
    assert "synapse.cvm" not in imported
    assert "runtime.CVM" not in imported
    assert "subprocess" not in imported
    assert "os" not in imported
    assert "validate_gold_evidence" not in import_names


def test_forbidden_claim_source_terms_are_deduplicated_and_preserved() -> None:
    source = production_source()
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_FORBIDDEN_CLAIMS"
    )
    terms = ast.literal_eval(assignment.value)

    assert terms.count("full_verified") == 1
    assert terms.count("gold_full_verified") == 1
    assert "target_gold" in source
    assert "target gold" in source
    assert "carry_enabled_gold" in source
    assert "carry-enabled gold" in source
    assert "gold_with_carry_measured" in source
    assert "gold-with-carry measured" in source
    assert "session_memory_appended" in source
    assert "session memory appended" in source
    assert "application_appended" in source
    assert "application appended" in source
    assert "repository_knowledge_admitted" in source
    assert "repositoryknowledge admitted" in source
    assert "full_verified" in source
    assert "gold_full_verified" in source
    assert "full_promotion" in source
    assert "token_savings" in source
    assert "cost_savings" in source
    assert "economic_calibration" in source
    assert "performance_improvement" in source
    assert "wall_clock_speedup" in source
    assert "latency_improvement" in source
    assert "throughput_improvement" in source


def test_no_file_io_or_git_subprocess_calls() -> None:
    tree = ast.parse(production_source())
    called_names: list[str] = []
    called_attrs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.append(node.func.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attrs.append(node.func.attr)

    assert "open" not in called_names
    assert "read_text" not in called_attrs
    assert "read_bytes" not in called_attrs
    assert "write_text" not in called_attrs
    assert "write_bytes" not in called_attrs
    assert "run" not in called_attrs


def test_no_gold_evidence_validation_duplication() -> None:
    source = production_source()

    assert "validate_gold_evidence" not in source


def test_raw_carry_not_imported_but_semantically_rejected() -> None:
    source = production_source()
    raw = candidate(source_kind=AdmissionSourceKind.RAW_BASELINE_CARRY)
    decision = evaluate_evidence_admission(raw, proof=None, allowed_scope=("src/a.py",))

    assert "RawCarryEntry" not in source
    assert "RawTranscriptCarry" not in source
    assert decision.status is AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY


def test_exact_six_file_scope_tripwire() -> None:
    files = changed_files()

    assert "synapse/experiments/swebench/measurement_output.py" in files
    assert "tests/test_swebench_measurement_output_boundary.py" in files
    assert files == {
        "synapse/experiments/swebench/measurement_output.py",
        "synapse/experiments/swebench/gold_evidence.py",
        "synapse/experiments/swebench/paired_measurement.py",
        "tests/test_swebench_measurement_output_boundary.py",
        "tests/test_swebench_gold_evidence.py",
        "tests/test_swebench_paired_measurement_contract.py",
    }


def test_tests_are_not_the_system() -> None:
    source = production_source()
    files = changed_files()

    assert "test_swebench_measurement_output_boundary" not in source
    assert "pytest" not in source
    assert "tmp_path" not in source
    assert "monkeypatch" not in source
    assert "caplog" not in source
    assert "tests/fixtures" not in files
    assert "tests/support" not in files
