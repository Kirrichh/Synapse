from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import ast
import json

import pytest

from synapse.experiments.swebench.contract import (
    ArtifactRef,
    AttemptVerdict,
    BaselineAttemptRecord,
    BaselineRunRecord,
    BaselineTask,
    ExperimentArm,
    OracleResult,
    PrimaryMetricStatus,
    TokenAccountingRecord,
    UsageConsistencyStatus,
    UsageSource,
)
from synapse.experiments.swebench.gold_attempt_writer import GoldAttemptWriteResult
from synapse.experiments.swebench.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_ORACLE_UNRESOLVED,
    GoldRunnerResult,
)
from synapse.experiments.swebench.paired_measurement import (
    ALL_ATTEMPTS_RECORDED,
    SELECTED_SUCCESS_ONLY,
    CarryState,
    ExecutionOrder,
    FingerprintAlignment,
    MeasurementMode,
    PAIRED_MEASUREMENT_SCHEMA_VERSION,
    PairedMeasurementMember,
    PairedMeasurementStatus,
    StatePolicy,
    baseline_member_from_run,
    build_paired_measurement_record,
    gold_member_from_result,
    paired_measurement_to_canonical_json,
)
from synapse.worker.candidate_materializer import MaterializationStatus, MaterializedCandidate
from synapse.worker.contract import (
    ExternalCodingWorkerResult,
    ExternalWorkerStatus,
    ExternalWorkerTokenStatus,
    ExternalWorkerUsage,
)


def worker_result() -> ExternalCodingWorkerResult:
    return ExternalCodingWorkerResult(
        worker_status=ExternalWorkerStatus.PROPOSED_PATCH,
        diff_text="diff --git a/src/calc.py b/src/calc.py\n",
        touched_files=("src/calc.py",),
        usage=ExternalWorkerUsage(
            token_status=ExternalWorkerTokenStatus.UNAVAILABLE,
            input_tokens=None,
            output_tokens=None,
            thinking_tokens=None,
            total_tokens=None,
            thinking_included=False,
            diagnostics={},
        ),
        diagnostics={},
    )


def token_record() -> TokenAccountingRecord:
    return TokenAccountingRecord(
        arm=ExperimentArm.BASELINE,
        usage_source=UsageSource.UNAVAILABLE,
        primary_metric_status=PrimaryMetricStatus.UNAVAILABLE,
        usage_consistency=UsageConsistencyStatus.MISSING_TOTAL,
        input_tokens=None,
        output_tokens=None,
        thinking_tokens=None,
        total_tokens=None,
        thinking_included=False,
        diagnostics={},
    )


def baseline_attempt(verdict: AttemptVerdict, attempt_id: int = 1) -> BaselineAttemptRecord:
    return BaselineAttemptRecord(
        attempt_id=attempt_id,
        arm=ExperimentArm.BASELINE,
        verdict=verdict,
        worker_result=worker_result(),
        token_accounting=token_record(),
        oracle_result=None,
        artifacts=(ArtifactRef("kind", "path", "sha", 1),),
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:01Z",
        diagnostics={},
    )


def baseline_run(*, resolved: bool, attempts: tuple[BaselineAttemptRecord, ...], max_attempts: int = 5) -> BaselineRunRecord:
    return BaselineRunRecord(
        run_id="baseline-run",
        task_id="task-1",
        instance_id="repo__issue-1",
        arm=ExperimentArm.BASELINE,
        base_revision="base-sha",
        replicate_id=7,
        max_attempts=max_attempts,
        resolved=resolved,
        attempts=attempts,
        total_provider_tokens=None,
        primary_metric_usable=False,
        started_at_utc="2026-01-01T00:00:00Z",
        finished_at_utc="2026-01-01T00:00:02Z",
        diagnostics={"source": "baseline"},
    )


def member(
    *,
    mode: MeasurementMode,
    carry_state: CarryState,
    task_id: str = "task-1",
    instance_id: str = "repo__issue-1",
    base_revision: str = "base-sha",
    replicate_id: int = 7,
    oracle_config_fingerprint: str | None = "oracle-config",
    oracle_environment_fingerprint: str | None = "oracle-env",
    environment_fingerprint: str | None = "env",
    diagnostics: dict[str, object] | None = None,
) -> PairedMeasurementMember:
    return PairedMeasurementMember(
        mode=mode,
        run_id="baseline-run" if mode is MeasurementMode.BASELINE else "gold-run",
        task_id=task_id,
        instance_id=instance_id,
        base_revision=base_revision,
        replicate_id=replicate_id,
        resolved=True,
        infra_error=False,
        terminal_status="ORACLE_RESOLVED" if mode is MeasurementMode.BASELINE else GOLD_APPLIED_WITH_EVIDENCE,
        attempt_count=1,
        oracle_config_fingerprint=oracle_config_fingerprint,
        oracle_environment_fingerprint=oracle_environment_fingerprint,
        environment_fingerprint=environment_fingerprint,
        carry_state=carry_state,
        source_record_kind="test",
        diagnostics={
            "attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
            "attempts_observed_count": 1,
            "selected_attempt_count": 1,
            **(diagnostics or {}),
        },
    )


def baseline_member(**overrides) -> PairedMeasurementMember:
    return member(
        mode=overrides.pop("mode", MeasurementMode.BASELINE),
        carry_state=overrides.pop("carry_state", CarryState.BASELINE_RAW_RETRY_CARRY),
        **overrides,
    )


def gold_member(**overrides) -> PairedMeasurementMember:
    return member(
        mode=overrides.pop("mode", MeasurementMode.GOLD_WITHOUT_CARRY),
        carry_state=overrides.pop("carry_state", CarryState.GOLD_WITHOUT_CARRY),
        **overrides,
    )


def pair(
    baseline: PairedMeasurementMember | None = None,
    gold: PairedMeasurementMember | None = None,
    *,
    token_or_cost_claim_present: bool = False,
):
    return build_paired_measurement_record(
        pair_id="pair-1",
        baseline=baseline or baseline_member(),
        gold=gold or gold_member(),
        execution_order=ExecutionOrder.BASELINE_THEN_GOLD,
        cache_state_policy=StatePolicy.CLEAN,
        profile_state_policy=StatePolicy.CLEAN,
        token_or_cost_claim_present=token_or_cost_claim_present,
    )


def assert_common_non_reusable(record) -> None:
    assert record.non_reusable_for_token_claims is True
    assert record.non_reusable_for_cost_claims is True
    assert record.non_reusable_for_wall_clock_claims is True
    assert record.non_reusable_for_economic_calibration is True
    assert record.performance_claim_allowed is False


def test_valid_success_only_pair() -> None:
    record = pair()

    assert record.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
    assert record.diagnostics["success_only_diagnostic"] is True
    assert_common_non_reusable(record)
    assert record.diagnostics["pairing_failures"] == []
    assert record.diagnostics["soft_warnings"] == []


def test_task_mismatch() -> None:
    record = pair(gold=gold_member(task_id="other-task"))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_task"] is False
    assert "task_id_mismatch" in record.diagnostics["pairing_failures"]


def test_instance_mismatch() -> None:
    record = pair(gold=gold_member(instance_id="other__issue"))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_instance"] is False
    assert "instance_id_mismatch" in record.diagnostics["pairing_failures"]


def test_base_mismatch() -> None:
    record = pair(gold=gold_member(base_revision="other-base"))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_base_revision"] is False
    assert "base_revision_mismatch" in record.diagnostics["pairing_failures"]


def test_replicate_mismatch() -> None:
    record = pair(gold=gold_member(replicate_id=8))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_replicate"] is False
    assert "replicate_id_mismatch" in record.diagnostics["pairing_failures"]


def test_missing_oracle_config_fingerprint() -> None:
    record = pair(gold=gold_member(oracle_config_fingerprint=None))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_oracle_config_fingerprint"] is False
    assert "oracle_config_fingerprint_missing" in record.diagnostics["pairing_failures"]


def test_oracle_config_mismatch() -> None:
    record = pair(gold=gold_member(oracle_config_fingerprint="other-config"))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["same_oracle_config_fingerprint"] is False
    assert "oracle_config_fingerprint_mismatch" in record.diagnostics["pairing_failures"]


def test_missing_oracle_environment_fingerprint_is_soft() -> None:
    record = pair(gold=gold_member(oracle_environment_fingerprint=None))

    assert record.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
    assert record.diagnostics["oracle_environment_fingerprint_alignment"] == FingerprintAlignment.MISSING.value
    assert "oracle_environment_fingerprint_missing" in record.diagnostics["soft_warnings"]
    assert "oracle_environment_fingerprint_missing" not in record.diagnostics["pairing_failures"]
    assert record.non_reusable_for_wall_clock_claims is True
    assert record.performance_claim_allowed is False


def test_oracle_environment_mismatch_is_soft() -> None:
    record = pair(gold=gold_member(oracle_environment_fingerprint="other-env"))

    assert record.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
    assert record.diagnostics["oracle_environment_fingerprint_alignment"] == FingerprintAlignment.MISMATCH.value
    assert "oracle_environment_fingerprint_mismatch" in record.diagnostics["soft_warnings"]
    assert "oracle_environment_fingerprint_mismatch" not in record.diagnostics["pairing_failures"]
    assert record.non_reusable_for_wall_clock_claims is True
    assert record.performance_claim_allowed is False


def test_environment_fingerprint_mismatch_is_soft() -> None:
    record = pair(gold=gold_member(environment_fingerprint="other-env"))

    assert record.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
    assert record.diagnostics["environment_fingerprint_alignment"] == FingerprintAlignment.MISMATCH.value
    assert "environment_fingerprint_mismatch" in record.diagnostics["soft_warnings"]
    assert "environment_fingerprint_mismatch" not in record.diagnostics["pairing_failures"]
    assert record.non_reusable_for_wall_clock_claims is True
    assert record.performance_claim_allowed is False


def test_wrong_baseline_mode() -> None:
    record = pair(baseline=baseline_member(mode=MeasurementMode.GOLD_WITHOUT_CARRY))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["baseline_mode_valid"] is False
    assert "baseline_mode_invalid" in record.diagnostics["pairing_failures"]


def test_wrong_gold_mode() -> None:
    record = pair(gold=gold_member(mode=MeasurementMode.BASELINE))

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert record.diagnostics["gold_mode_valid"] is False
    assert "gold_mode_invalid" in record.diagnostics["pairing_failures"]


def test_gold_with_carry_rejected() -> None:
    record = pair(gold=gold_member(mode=MeasurementMode.GOLD_WITH_CARRY, carry_state=CarryState.GOLD_WITH_CARRY))

    assert record.status is PairedMeasurementStatus.INVALID_GOLD_WITH_CARRY
    assert "gold_with_carry_requires_c3" in record.diagnostics["pairing_failures"]


def test_token_cost_claim_rejected() -> None:
    record = pair(token_or_cost_claim_present=True)

    assert record.status is PairedMeasurementStatus.INVALID_TOKEN_OR_COST_CLAIM
    assert "token_or_cost_claim_requires_canonical_telemetry_gateway" in record.diagnostics["pairing_failures"]
    assert record.non_reusable_for_token_claims is True
    assert record.non_reusable_for_cost_claims is True
    assert record.non_reusable_for_wall_clock_claims is True
    assert record.non_reusable_for_economic_calibration is True


def test_cherry_pick_risk_rejected() -> None:
    risky_gold = gold_member(diagnostics={"attempt_selection_policy": SELECTED_SUCCESS_ONLY})
    record = pair(gold=risky_gold)

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert "gold_cherry_pick_risk" in record.diagnostics["pairing_failures"]


def test_cherry_pick_counter_mismatch_rejected_and_equal_counter_allowed() -> None:
    risky_gold = gold_member(
        diagnostics={
            "attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
            "attempts_observed_count": 1,
            "selected_attempt_count": 3,
        }
    )
    safe_gold = gold_member(
        diagnostics={
            "attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
            "attempts_observed_count": 3,
            "selected_attempt_count": 3,
        }
    )
    risky_record = pair(gold=risky_gold)
    safe_record = pair(gold=safe_gold)

    assert risky_record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert "gold_cherry_pick_risk" in risky_record.diagnostics["pairing_failures"]
    assert "gold_cherry_pick_risk" not in safe_record.diagnostics["pairing_failures"]
    assert safe_record.status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC


def test_unknown_attempt_selection_policy_is_cherry_pick_risk() -> None:
    risky_gold = gold_member(
        diagnostics={
            "attempt_selection_policy": "UNKNOWN_POLICY",
            "attempts_observed_count": 3,
            "selected_attempt_count": 3,
        }
    )
    record = pair(gold=risky_gold)

    assert record.status is PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    assert "gold_cherry_pick_risk" in record.diagnostics["pairing_failures"]


def test_deterministic_canonical_json() -> None:
    first = pair()
    second = pair()
    first_json = paired_measurement_to_canonical_json(first)
    second_json = paired_measurement_to_canonical_json(second)
    parsed = json.loads(first_json)

    assert first_json == second_json
    assert first_json.endswith("\n")
    assert first_json.index('"baseline"') < first_json.index('"cache_state_policy"')
    assert parsed["status"] == PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC.value
    assert parsed["baseline"]["mode"] == MeasurementMode.BASELINE.value
    assert isinstance(parsed["baseline"], dict)
    assert isinstance(parsed["gold"], dict)


def test_canonical_json_sorts_nested_diagnostics_keys() -> None:
    baseline_a = baseline_member(diagnostics={"z_key": "z", "a_key": "a"})
    baseline_b = baseline_member(diagnostics={"a_key": "a", "z_key": "z"})
    gold_a = gold_member(diagnostics={"y_key": "y", "b_key": "b"})
    gold_b = gold_member(diagnostics={"b_key": "b", "y_key": "y"})
    record_a = pair(baseline=baseline_a, gold=gold_a)
    record_b = pair(baseline=baseline_b, gold=gold_b)
    json_a = paired_measurement_to_canonical_json(record_a)
    json_b = paired_measurement_to_canonical_json(record_b)

    assert json_a == json_b
    assert json_a.endswith("\n")
    assert json.loads(json_a) == json.loads(json_b)
    assert json_a.index('"a_key"') < json_a.index('"z_key"')
    assert json_a.index('"b_key"') < json_a.index('"y_key"')


def test_member_resolved_none_requires_infra_error() -> None:
    with pytest.raises(ValueError):
        PairedMeasurementMember(
            mode=MeasurementMode.BASELINE,
            run_id="run",
            task_id="task",
            instance_id="repo__issue",
            base_revision="base",
            replicate_id=0,
            resolved=None,
            infra_error=False,
            terminal_status="INFRA_ERROR",
            attempt_count=1,
            oracle_config_fingerprint="oracle-config",
            oracle_environment_fingerprint="oracle-env",
            environment_fingerprint="env",
            carry_state=CarryState.BASELINE_RAW_RETRY_CARRY,
            source_record_kind="test",
            diagnostics={},
        )

    member_value = PairedMeasurementMember(
        mode=MeasurementMode.BASELINE,
        run_id="run",
        task_id="task",
        instance_id="repo__issue",
        base_revision="base",
        replicate_id=0,
        resolved=None,
        infra_error=True,
        terminal_status="INFRA_ERROR",
        attempt_count=1,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
        carry_state=CarryState.BASELINE_RAW_RETRY_CARRY,
        source_record_kind="test",
        diagnostics={},
    )

    assert member_value.resolved is None
    assert member_value.infra_error is True


def test_baseline_member_from_run_consumes_baseline_run_record() -> None:
    run = baseline_run(
        resolved=True,
        attempts=(baseline_attempt(AttemptVerdict.ORACLE_UNRESOLVED, 1), baseline_attempt(AttemptVerdict.ORACLE_RESOLVED, 2)),
        max_attempts=5,
    )
    assert len(run.attempts) != run.max_attempts

    member_value = baseline_member_from_run(
        run,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )

    assert member_value.run_id == run.run_id
    assert member_value.task_id == run.task_id
    assert member_value.instance_id == run.instance_id
    assert member_value.base_revision == run.base_revision
    assert member_value.replicate_id == run.replicate_id
    assert member_value.resolved == run.resolved
    assert member_value.attempt_count == len(run.attempts)
    assert member_value.attempt_count != run.max_attempts
    assert member_value.source_record_kind == "BaselineRunRecord"
    assert member_value.carry_state is CarryState.BASELINE_RAW_RETRY_CARRY
    assert member_value.terminal_status == "ORACLE_RESOLVED"
    assert member_value.infra_error is False
    assert member_value.diagnostics["attempt_selection_policy"] == ALL_ATTEMPTS_RECORDED
    assert member_value.diagnostics["attempts_observed_count"] == len(run.attempts)
    assert member_value.diagnostics["selected_attempt_count"] == len(run.attempts)


def test_baseline_member_from_run_infra_and_unresolved_terminal_status() -> None:
    infra_run = baseline_run(resolved=False, attempts=(baseline_attempt(AttemptVerdict.INFRA_ERROR),), max_attempts=4)
    unresolved_run = baseline_run(resolved=False, attempts=(), max_attempts=4)

    infra_member = baseline_member_from_run(
        infra_run,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )
    unresolved_member = baseline_member_from_run(
        unresolved_run,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )

    assert infra_member.terminal_status == "INFRA_ERROR"
    assert infra_member.resolved is None
    assert infra_member.infra_error is True
    assert unresolved_member.terminal_status == "ORACLE_UNRESOLVED"
    assert unresolved_member.resolved is False
    assert unresolved_member.infra_error is False
    assert unresolved_member.attempt_count == 0


def materialized_candidate() -> MaterializedCandidate:
    return MaterializedCandidate(
        status=MaterializationStatus.MATERIALIZED,
        patch_text="diff --git a/src/calc.py b/src/calc.py\n",
        touched_files=("src/calc.py",),
        scope_violations=(),
        source_forms=("worker_diff_text",),
        diagnostics={},
    )


def gold_result(
    *,
    status: str,
    oracle_result: OracleResult | None,
    payload: dict[str, object] | None = None,
) -> GoldRunnerResult:
    return GoldRunnerResult(
        status=status,
        write_result=GoldAttemptWriteResult(ok=True, status="GOLD_ATTEMPT_WRITTEN", path="gold_attempts.jsonl", failure_code=None, detail=None),
        payload={"gold_run_id": "gold-run", **(payload or {})},
        materialized_candidate=materialized_candidate(),
        controlled_change_result=None,
        gold_evidence=None,
        evidence_validation=None,
        oracle_result=oracle_result,
        bridge_commit=None,
        task_path=None,
        patch_path=None,
        target_ref="refs/heads/synapse/gold/run/1",
        report_root="reports",
    )


def test_gold_member_from_result_consumes_explicit_context_and_oracle_priority() -> None:
    oracle = OracleResult(
        resolved=True,
        returncode=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        diagnostics={"infra_error": False},
    )
    result = gold_result(status=GOLD_INFRA_ERROR, oracle_result=oracle)

    member_value = gold_member_from_result(
        result,
        task_id="task-1",
        instance_id="repo__issue-1",
        base_revision="base-sha",
        replicate_id=7,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )

    assert member_value.run_id == "gold-run"
    assert member_value.task_id == "task-1"
    assert member_value.instance_id == "repo__issue-1"
    assert member_value.base_revision == "base-sha"
    assert member_value.replicate_id == 7
    assert member_value.carry_state is CarryState.GOLD_WITHOUT_CARRY
    assert member_value.resolved is True
    assert member_value.infra_error is False
    assert member_value.terminal_status == GOLD_INFRA_ERROR
    assert member_value.diagnostics["attempt_selection_policy"] == ALL_ATTEMPTS_RECORDED
    assert member_value.diagnostics["attempts_observed_count"] == 1
    assert member_value.diagnostics["selected_attempt_count"] == 1


def test_gold_member_from_result_status_fallback_and_missing_infra_key() -> None:
    fallback = gold_member_from_result(
        gold_result(status=GOLD_ORACLE_UNRESOLVED, oracle_result=None),
        task_id="task-1",
        instance_id="repo__issue-1",
        base_revision="base-sha",
        replicate_id=7,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )
    missing_infra_key = gold_member_from_result(
        gold_result(
            status=GOLD_INFRA_ERROR,
            oracle_result=OracleResult(False, 1, "", "", 0.1, diagnostics={}),
        ),
        task_id="task-1",
        instance_id="repo__issue-1",
        base_revision="base-sha",
        replicate_id=7,
        oracle_config_fingerprint="oracle-config",
        oracle_environment_fingerprint="oracle-env",
        environment_fingerprint="env",
    )

    assert fallback.resolved is False
    assert fallback.infra_error is False
    assert fallback.terminal_status == GOLD_ORACLE_UNRESOLVED
    assert missing_infra_key.resolved is False
    assert missing_infra_key.infra_error is False
    assert missing_infra_key.terminal_status == GOLD_INFRA_ERROR


def test_no_forbidden_imports() -> None:
    source = Path("synapse/experiments/swebench/paired_measurement.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    assert "synapse.change" not in imported
    assert "synapse.worker" not in imported
    assert "synapse.interpreter" not in imported
    assert "synapse.cvm" not in imported
    assert "runtime.CVM" not in imported
    assert "synapse.experiments.swebench.baseline" not in imported
    assert "synapse.experiments.swebench.telemetry" not in imported
    assert "synapse.experiments.swebench.carry" not in imported


def test_same_oracle_object_satisfies_baseline_and_gold_protocol_shapes(tmp_path: Path) -> None:
    class SharedOracle:
        def __init__(self) -> None:
            self.calls: list[tuple[Path, BaselineTask]] = []

        def verify(self, worktree_path: Path, task: BaselineTask) -> OracleResult:
            self.calls.append((worktree_path, task))
            return OracleResult(
                resolved=True,
                returncode=0,
                stdout="",
                stderr="",
                duration_seconds=0.01,
                diagnostics={"infra_error": False, "oracle_authority": "shared-test-oracle"},
            )

    shared = SharedOracle()
    baseline_oracle = shared
    gold_oracle = shared
    first_task = BaselineTask("task", "repo__issue", "statement", ("src",))
    second_task = BaselineTask("task", "repo__issue", "statement", ("src",))

    baseline_result = baseline_oracle.verify(tmp_path / "baseline", first_task)
    gold_result_value = gold_oracle.verify(tmp_path / "gold", second_task)

    assert baseline_oracle is shared
    assert gold_oracle is shared
    assert len(shared.calls) == 2
    assert isinstance(baseline_result, OracleResult)
    assert isinstance(gold_result_value, OracleResult)
    assert baseline_result.resolved == gold_result_value.resolved
    assert baseline_result.diagnostics["oracle_authority"] == "shared-test-oracle"
    assert gold_result_value.diagnostics["oracle_authority"] == "shared-test-oracle"
