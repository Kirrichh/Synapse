from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess

import pytest

from synapse.experiments.swebench.gold_evidence import EVIDENCE_REF_NAMESPACE, GoldEvidence
from synapse.experiments.swebench.measurement_output import (
    AdmissionSourceKind,
    AdmissionStatus,
    CarryAuthority,
    DistilledEvidenceCandidate,
    EvidenceAdmissionDecision,
    MeasurementLabel,
    SuccessOnlyMeasurementOutput,
    TelemetryGatewayCandidateStatus,
    TelemetryGatewayStatus,
    build_success_only_measurement_output,
    candidate_from_gold_evidence,
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


BASE_COMMIT = "b91365e13797bca98dcd05a579cd34990e212270"
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


def candidate(
    *,
    source_kind: AdmissionSourceKind = AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE,
    allowed_scope: tuple[str, ...] = ("src/a.py",),
    summary: str = "bounded evidence summary",
    claims: dict[str, object] | None = None,
    diagnostics: dict[str, object] | None = None,
) -> DistilledEvidenceCandidate:
    return DistilledEvidenceCandidate(
        source_kind=source_kind,
        evidence_ref=f"{EVIDENCE_REF_NAMESPACE}run-1" if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE else None,
        verified_commit="a" * 40 if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE else None,
        base_sha="c" * 40 if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE else None,
        task_contract_sha256="d" * 64 if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE else None,
        patch_sha256="e" * 64 if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE else None,
        allowed_scope=allowed_scope,
        summary=summary,
        claims=claims or {},
        diagnostics=diagnostics or {},
    )


def admission_decision(**overrides) -> EvidenceAdmissionDecision:
    base = {
        "schema_version": "synapse.stage4.c2s3.evidence_admission/v1",
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


def test_candidate_can_be_built_from_gold_evidence() -> None:
    source = evidence()
    result = candidate_from_gold_evidence(source, allowed_scope=("src/a.py",), summary="bounded summary")

    assert result.evidence_ref == source.evidence_ref
    assert result.verified_commit == source.verified_commit
    assert result.base_sha == source.base_sha
    assert result.task_contract_sha256 == source.task_contract_sha256
    assert result.patch_sha256 == source.patch_sha256
    assert result.allowed_scope == ("src/a.py",)
    assert result.summary == "bounded summary"
    assert result.source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE


def test_gold_evidence_candidate_is_contract_admissible_only() -> None:
    decision = evaluate_evidence_admission(candidate(), validation_ok=True, allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
    assert decision.admitted_to_application is False
    assert decision.admitted_to_session_memory is False
    assert decision.gold_with_carry_enabled is False
    assert decision.scope_expansion_allowed is False
    assert decision.raw_carry_authority_allowed is False
    assert decision.carry_authority is CarryAuthority.DISTILLED_EVIDENCE_CANDIDATE


def test_validation_failure_rejects_gold_evidence_candidate() -> None:
    decision = evaluate_evidence_admission(candidate(), validation_ok=False, allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE


def test_raw_baseline_carry_source_rejected() -> None:
    raw = candidate(source_kind=AdmissionSourceKind.RAW_BASELINE_CARRY)
    decision = evaluate_evidence_admission(raw, validation_ok=True, allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY
    assert decision.raw_carry_authority_allowed is False


def test_unknown_and_unvalidated_source_rejected() -> None:
    report = candidate(source_kind=AdmissionSourceKind.UNVALIDATED_REPORT)
    unknown = candidate(source_kind=AdmissionSourceKind.UNKNOWN)
    report_decision = evaluate_evidence_admission(report, validation_ok=True, allowed_scope=("src/a.py",))
    unknown_decision = evaluate_evidence_admission(unknown, validation_ok=True, allowed_scope=("src/a.py",))

    assert report_decision.status is AdmissionStatus.REJECTED_INVALID_SOURCE
    assert unknown_decision.status is AdmissionStatus.REJECTED_INVALID_SOURCE


def test_candidate_cannot_expand_allowed_scope() -> None:
    expanded = candidate(allowed_scope=("src/a.py", "src/b.py"))
    decision = evaluate_evidence_admission(expanded, validation_ok=True, allowed_scope=("src/a.py",))

    assert decision.status is AdmissionStatus.REJECTED_SCOPE_VIOLATION
    assert decision.scope_expansion_allowed is False


def test_invalid_path_shape_rejected() -> None:
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("../secret.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("/abs/path.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("C:\\abs\\path.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("src//file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("src/./file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("src/../file.py",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("",), summary="summary")
    with pytest.raises(ValueError):
        candidate_from_gold_evidence(evidence(), allowed_scope=("src/\x00/file.py",), summary="summary")


def test_admission_rejects_target_gold_and_carry_overclaim() -> None:
    target = evaluate_evidence_admission(candidate(summary="target Gold"), validation_ok=True, allowed_scope=("src/a.py",))
    carry = evaluate_evidence_admission(candidate(claims={"claim": "carry-enabled Gold"}), validation_ok=True, allowed_scope=("src/a.py",))
    measured = evaluate_evidence_admission(candidate(summary="Gold-with-carry measured"), validation_ok=True, allowed_scope=("src/a.py",))

    assert target.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert target.overclaim_detected is True
    assert carry.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert carry.overclaim_detected is True
    assert measured.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert measured.overclaim_detected is True


def test_admission_rejects_application_session_append_overclaim() -> None:
    app = evaluate_evidence_admission(candidate(claims={"application_appended": True}), validation_ok=True, allowed_scope=("src/a.py",))
    session = evaluate_evidence_admission(candidate(claims={"session_memory_appended": True}), validation_ok=True, allowed_scope=("src/a.py",))
    repo = evaluate_evidence_admission(candidate(claims={"repository_knowledge_admitted": True}), validation_ok=True, allowed_scope=("src/a.py",))

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
    gold_full = evaluate_evidence_admission(candidate(claims={"claim": "GOLD_FULL_VERIFIED"}), validation_ok=True, allowed_scope=("src/a.py",))
    full_verified = evaluate_evidence_admission(candidate(summary="FULL_VERIFIED"), validation_ok=True, allowed_scope=("src/a.py",))
    promotion = evaluate_evidence_admission(candidate(diagnostics={"claim": "FULL_PROMOTION"}), validation_ok=True, allowed_scope=("src/a.py",))

    assert gold_full.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert full_verified.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert promotion.status is AdmissionStatus.REJECTED_OVERCLAIM


def test_admission_rejects_token_cost_performance_claims() -> None:
    token = evaluate_evidence_admission(candidate(claims={"token_savings": "10%"}), validation_ok=True, allowed_scope=("src/a.py",))
    cost = evaluate_evidence_admission(candidate(claims={"cost_savings": "10%"}), validation_ok=True, allowed_scope=("src/a.py",))
    roi = evaluate_evidence_admission(candidate(claims={"roi": "high"}), validation_ok=True, allowed_scope=("src/a.py",))
    economic = evaluate_evidence_admission(candidate(claims={"economic_calibration": "done"}), validation_ok=True, allowed_scope=("src/a.py",))
    wall = evaluate_evidence_admission(candidate(claims={"wall_clock_speedup": "yes"}), validation_ok=True, allowed_scope=("src/a.py",))
    perf = evaluate_evidence_admission(candidate(claims={"performance_improvement": "yes"}), validation_ok=True, allowed_scope=("src/a.py",))

    assert token.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert cost.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert roi.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert economic.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert wall.status is AdmissionStatus.REJECTED_OVERCLAIM
    assert perf.status is AdmissionStatus.REJECTED_OVERCLAIM


def test_roi_boundary_rejects_claim_tokens_without_false_positive_words() -> None:
    heroic = evaluate_evidence_admission(
        candidate(summary="heroic bugfix in parser"),
        validation_ok=True,
        allowed_scope=("src/a.py",),
    )
    intro = evaluate_evidence_admission(
        candidate(summary="introspection cleanup"),
        validation_ok=True,
        allowed_scope=("src/a.py",),
    )
    android = evaluate_evidence_admission(
        candidate(summary="android parser cleanup"),
        validation_ok=True,
        allowed_scope=("src/a.py",),
    )
    output = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"note": "heroic parser cleanup"},
    )
    roi_admission = evaluate_evidence_admission(
        candidate(claims={"roi": "high"}),
        validation_ok=True,
        allowed_scope=("src/a.py",),
    )
    roi_output = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"roi": "high"},
    )
    roi_phrase = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"claim": "project ROI analysis"},
    )
    roi_pct = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"roi_pct": 0.3},
    )
    roi_metric = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"claim": "roi-metric increased"},
    )
    roi_colon = build_success_only_measurement_output(
        success_pair(),
        requested_claims={"claim": "roi:high"},
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


def test_application_integration_flag_cannot_create_append_authority() -> None:
    decision = evaluate_evidence_admission(
        candidate(),
        validation_ok=True,
        allowed_scope=("src/a.py",),
        application_integration_available=True,
    )

    assert decision.admitted_to_application is False
    assert decision.admitted_to_session_memory is False
    assert decision.gold_with_carry_enabled is False


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

    assert validation.status is TelemetryGatewayCandidateStatus.REJECTED_ALIAS_ONLY
    assert "accounting_category" in validation.alias_only_fields_by_index[0]
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


def test_canonical_json_deterministic_for_output() -> None:
    first = build_success_only_measurement_output(success_pair(), requested_claims={"z_key": "z", "a_key": "a"})
    second = build_success_only_measurement_output(success_pair(), requested_claims={"a_key": "a", "z_key": "z"})
    first_json = measurement_output_to_canonical_json(first)
    second_json = measurement_output_to_canonical_json(second)
    parsed = json.loads(first_json)

    assert first_json == second_json
    assert first_json.endswith("\n")
    assert first_json.index('"a_key"') < first_json.index('"z_key"')
    assert parsed["measurement_label"] == MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC.value
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_canonical_json_deterministic_for_admission_decision() -> None:
    first_candidate = candidate(claims={"z_key": "z", "a_key": "a"}, diagnostics={"y_key": "y", "b_key": "b"})
    second_candidate = candidate(claims={"a_key": "a", "z_key": "z"}, diagnostics={"b_key": "b", "y_key": "y"})
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

    assert source.count('"full_verified"') == 1
    assert source.count('"gold_full_verified"') == 1
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
    decision = evaluate_evidence_admission(raw, validation_ok=True, allowed_scope=("src/a.py",))

    assert "RawCarryEntry" not in source
    assert "RawTranscriptCarry" not in source
    assert decision.status is AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY


def test_third_file_scope_tripwire() -> None:
    files = changed_files()

    assert "synapse/experiments/swebench/measurement_output.py" in files
    assert "tests/test_swebench_measurement_output_boundary.py" in files
    assert files == {
        "synapse/experiments/swebench/measurement_output.py",
        "tests/test_swebench_measurement_output_boundary.py",
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
