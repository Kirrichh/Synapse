"""Stage 4 C2-S2 paired Baseline/GOLD measurement contract.

This module validates whether already-produced Baseline and GOLD measurements
can be compared as a success-only SWE-bench oracle diagnostic. It does not run
workers, runners, telemetry, carry admission, controlled-change, or FULL
verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping
import json

from synapse.experiments.swebench.contract import (
    AttemptVerdict,
    BaselineRunRecord,
    BaselineTask,
    OracleResult,
)
from synapse.experiments.swebench.gold_runner import (
    GOLD_APPLIED_WITH_EVIDENCE,
    GOLD_INFRA_ERROR,
    GOLD_ORACLE_UNRESOLVED,
    GoldRunnerResult,
)


PAIRED_MEASUREMENT_SCHEMA_VERSION = "synapse.stage4.c2s2.paired_measurement/v1"
ALL_ATTEMPTS_RECORDED = "ALL_ATTEMPTS_RECORDED"
SELECTED_SUCCESS_ONLY = "SELECTED_SUCCESS_ONLY"


class MeasurementMode(str, Enum):
    BASELINE = "BASELINE"
    GOLD_WITHOUT_CARRY = "GOLD_WITHOUT_CARRY"
    GOLD_WITH_CARRY = "GOLD_WITH_CARRY"


class CarryState(str, Enum):
    BASELINE_RAW_RETRY_CARRY = "BASELINE_RAW_RETRY_CARRY"
    GOLD_WITHOUT_CARRY = "GOLD_WITHOUT_CARRY"
    GOLD_WITH_CARRY = "GOLD_WITH_CARRY"


class PairedMeasurementStatus(str, Enum):
    PAIRED_SUCCESS_ONLY_DIAGNOSTIC = "PAIRED_SUCCESS_ONLY_DIAGNOSTIC"
    UNPAIRED_DIAGNOSTIC_ONLY = "UNPAIRED_DIAGNOSTIC_ONLY"
    INVALID_GOLD_WITH_CARRY = "INVALID_GOLD_WITH_CARRY"
    INVALID_TOKEN_OR_COST_CLAIM = "INVALID_TOKEN_OR_COST_CLAIM"


class ExecutionOrder(str, Enum):
    BASELINE_THEN_GOLD = "BASELINE_THEN_GOLD"
    GOLD_THEN_BASELINE = "GOLD_THEN_BASELINE"
    DECLARED_EXTERNAL_ORDER = "DECLARED_EXTERNAL_ORDER"


class StatePolicy(str, Enum):
    CLEAN = "CLEAN"
    REUSED = "REUSED"
    UNKNOWN = "UNKNOWN"


class FingerprintAlignment(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"


def _enum_value(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid {enum_type.__name__}") from exc


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _json_safe_mapping(value: Mapping[str, Any], field_name: str) -> dict[str, Any]:
    data = dict(value)
    try:
        json.dumps(data, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-serializable: {exc}") from exc
    return data


def _enum_to_json(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_enum_to_json(item) for item in value]
    if isinstance(value, list):
        return [_enum_to_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _enum_to_json(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class PairedMeasurementMember:
    mode: MeasurementMode
    run_id: str
    task_id: str
    instance_id: str
    base_revision: str
    replicate_id: int
    resolved: bool | None
    infra_error: bool
    terminal_status: str
    attempt_count: int
    oracle_config_fingerprint: str | None
    oracle_environment_fingerprint: str | None
    environment_fingerprint: str | None
    carry_state: CarryState
    source_record_kind: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum_value(self.mode, MeasurementMode, "mode"))
        object.__setattr__(self, "carry_state", _enum_value(self.carry_state, CarryState, "carry_state"))
        for field_name in ("run_id", "task_id", "instance_id", "base_revision", "terminal_status", "source_record_kind"):
            object.__setattr__(self, field_name, _non_empty_string(getattr(self, field_name), field_name))
        if not isinstance(self.replicate_id, int) or self.replicate_id < 0:
            raise ValueError("replicate_id must be >= 0")
        if not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise ValueError("attempt_count must be >= 0")
        if not isinstance(self.infra_error, bool):
            raise ValueError("infra_error must be a boolean")
        if self.resolved is None and not self.infra_error:
            raise ValueError("resolved may be None only when infra_error is true")
        if self.resolved is not None and not isinstance(self.resolved, bool):
            raise ValueError("resolved must be a boolean or None")
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "base_revision": self.base_revision,
            "replicate_id": self.replicate_id,
            "resolved": self.resolved,
            "infra_error": self.infra_error,
            "terminal_status": self.terminal_status,
            "attempt_count": self.attempt_count,
            "oracle_config_fingerprint": self.oracle_config_fingerprint,
            "oracle_environment_fingerprint": self.oracle_environment_fingerprint,
            "environment_fingerprint": self.environment_fingerprint,
            "carry_state": self.carry_state.value,
            "source_record_kind": self.source_record_kind,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class PairedMeasurementRecord:
    schema_version: str
    pair_id: str
    baseline: PairedMeasurementMember
    gold: PairedMeasurementMember
    status: PairedMeasurementStatus
    execution_order: ExecutionOrder
    cache_state_policy: StatePolicy
    profile_state_policy: StatePolicy
    non_reusable_for_token_claims: bool
    non_reusable_for_cost_claims: bool
    non_reusable_for_wall_clock_claims: bool
    non_reusable_for_economic_calibration: bool
    performance_claim_allowed: bool
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != PAIRED_MEASUREMENT_SCHEMA_VERSION:
            raise ValueError("schema_version must equal PAIRED_MEASUREMENT_SCHEMA_VERSION")
        object.__setattr__(self, "pair_id", _non_empty_string(self.pair_id, "pair_id"))
        object.__setattr__(self, "status", _enum_value(self.status, PairedMeasurementStatus, "status"))
        object.__setattr__(self, "execution_order", _enum_value(self.execution_order, ExecutionOrder, "execution_order"))
        object.__setattr__(self, "cache_state_policy", _enum_value(self.cache_state_policy, StatePolicy, "cache_state_policy"))
        object.__setattr__(self, "profile_state_policy", _enum_value(self.profile_state_policy, StatePolicy, "profile_state_policy"))
        for field_name in (
            "non_reusable_for_token_claims",
            "non_reusable_for_cost_claims",
            "non_reusable_for_wall_clock_claims",
            "non_reusable_for_economic_calibration",
            "performance_claim_allowed",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean")
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pair_id": self.pair_id,
            "baseline": self.baseline.to_dict(),
            "gold": self.gold.to_dict(),
            "status": self.status.value,
            "execution_order": self.execution_order.value,
            "cache_state_policy": self.cache_state_policy.value,
            "profile_state_policy": self.profile_state_policy.value,
            "non_reusable_for_token_claims": self.non_reusable_for_token_claims,
            "non_reusable_for_cost_claims": self.non_reusable_for_cost_claims,
            "non_reusable_for_wall_clock_claims": self.non_reusable_for_wall_clock_claims,
            "non_reusable_for_economic_calibration": self.non_reusable_for_economic_calibration,
            "performance_claim_allowed": self.performance_claim_allowed,
            "diagnostics": dict(self.diagnostics),
        }


def paired_measurement_to_canonical_json(record: PairedMeasurementRecord) -> str:
    return json.dumps(_enum_to_json(record.to_dict()), sort_keys=True, indent=2) + "\n"


def baseline_member_from_run(
    run: BaselineRunRecord,
    *,
    oracle_config_fingerprint: str | None,
    oracle_environment_fingerprint: str | None,
    environment_fingerprint: str | None,
    carry_state: CarryState = CarryState.BASELINE_RAW_RETRY_CARRY,
) -> PairedMeasurementMember:
    """Adapt a BaselineRunRecord using the C2-S2 terminal-status rule.

    If run.resolved is true, the member is ORACLE_RESOLVED. Otherwise, a final
    INFRA_ERROR attempt makes the member infra. All other unresolved runs,
    including zero-attempt unresolved runs, are ORACLE_UNRESOLVED.
    """

    attempt_count = len(run.attempts)
    final_attempt = run.attempts[-1] if run.attempts else None
    if run.resolved:
        terminal_status = AttemptVerdict.ORACLE_RESOLVED.value
        resolved: bool | None = True
        infra_error = False
    elif final_attempt is not None and final_attempt.verdict is AttemptVerdict.INFRA_ERROR:
        terminal_status = AttemptVerdict.INFRA_ERROR.value
        resolved = None
        infra_error = True
    else:
        terminal_status = AttemptVerdict.ORACLE_UNRESOLVED.value
        resolved = False
        infra_error = False
    diagnostics = {
        **dict(run.diagnostics),
        "attempt_selection_policy": ALL_ATTEMPTS_RECORDED,
        "attempts_observed_count": attempt_count,
        "selected_attempt_count": attempt_count,
    }
    return PairedMeasurementMember(
        mode=MeasurementMode.BASELINE,
        run_id=run.run_id,
        task_id=run.task_id,
        instance_id=run.instance_id,
        base_revision=run.base_revision,
        replicate_id=run.replicate_id,
        resolved=resolved,
        infra_error=infra_error,
        terminal_status=terminal_status,
        attempt_count=attempt_count,
        oracle_config_fingerprint=oracle_config_fingerprint,
        oracle_environment_fingerprint=oracle_environment_fingerprint,
        environment_fingerprint=environment_fingerprint,
        carry_state=carry_state,
        source_record_kind="BaselineRunRecord",
        diagnostics=diagnostics,
    )


def gold_member_from_result(
    result: GoldRunnerResult,
    *,
    task_id: str,
    instance_id: str,
    base_revision: str,
    replicate_id: int,
    oracle_config_fingerprint: str | None,
    oracle_environment_fingerprint: str | None,
    environment_fingerprint: str | None,
    carry_state: CarryState = CarryState.GOLD_WITHOUT_CARRY,
) -> PairedMeasurementMember:
    carry_state = _enum_value(carry_state, CarryState, "carry_state")
    payload = dict(result.payload)
    oracle_result = result.oracle_result
    if oracle_result is not None:
        resolved: bool | None = oracle_result.resolved
        infra_error = bool(oracle_result.diagnostics.get("infra_error") is True)
    elif result.status == GOLD_APPLIED_WITH_EVIDENCE:
        resolved = True
        infra_error = False
    elif result.status == GOLD_ORACLE_UNRESOLVED:
        resolved = False
        infra_error = False
    elif result.status == GOLD_INFRA_ERROR:
        resolved = None
        infra_error = True
    else:
        resolved = False
        infra_error = False

    payload_diagnostics = payload.get("diagnostics")
    diagnostics: dict[str, Any] = dict(payload_diagnostics) if isinstance(payload_diagnostics, Mapping) else {}
    for key in ("attempt_selection_policy", "attempts_observed_count", "selected_attempt_count"):
        if key in payload:
            diagnostics[key] = payload[key]
    diagnostics.setdefault("attempt_selection_policy", ALL_ATTEMPTS_RECORDED)
    diagnostics.setdefault("attempts_observed_count", 1)
    diagnostics.setdefault("selected_attempt_count", 1)
    attempt_count = diagnostics.get("attempts_observed_count")
    if not isinstance(attempt_count, int) or attempt_count < 0:
        attempt_count = 1

    return PairedMeasurementMember(
        mode=MeasurementMode.GOLD_WITHOUT_CARRY if carry_state is not CarryState.GOLD_WITH_CARRY else MeasurementMode.GOLD_WITH_CARRY,
        run_id=str(payload.get("gold_run_id") or ""),
        task_id=task_id,
        instance_id=instance_id,
        base_revision=base_revision,
        replicate_id=replicate_id,
        resolved=resolved,
        infra_error=infra_error,
        terminal_status=result.status,
        attempt_count=attempt_count,
        oracle_config_fingerprint=oracle_config_fingerprint,
        oracle_environment_fingerprint=oracle_environment_fingerprint,
        environment_fingerprint=environment_fingerprint,
        carry_state=carry_state,
        source_record_kind="GoldRunnerResult",
        diagnostics=diagnostics,
    )


def _fingerprint_alignment(left: str | None, right: str | None) -> FingerprintAlignment:
    if left is None or right is None:
        return FingerprintAlignment.MISSING
    if left == right:
        return FingerprintAlignment.MATCH
    return FingerprintAlignment.MISMATCH


def _gold_cherry_pick_risk(gold: PairedMeasurementMember) -> bool:
    policy = gold.diagnostics.get("attempt_selection_policy")
    observed = gold.diagnostics.get("attempts_observed_count")
    selected = gold.diagnostics.get("selected_attempt_count")
    if policy == SELECTED_SUCCESS_ONLY:
        return True
    if policy != ALL_ATTEMPTS_RECORDED:
        return True
    if not isinstance(observed, int) or not isinstance(selected, int):
        return True
    if selected < 1:
        return True
    return observed < selected


def build_paired_measurement_record(
    *,
    pair_id: str,
    baseline: PairedMeasurementMember,
    gold: PairedMeasurementMember,
    execution_order: ExecutionOrder,
    cache_state_policy: StatePolicy,
    profile_state_policy: StatePolicy,
    token_or_cost_claim_present: bool = False,
) -> PairedMeasurementRecord:
    pair_id = _non_empty_string(pair_id, "pair_id")
    execution_order = _enum_value(execution_order, ExecutionOrder, "execution_order")
    cache_state_policy = _enum_value(cache_state_policy, StatePolicy, "cache_state_policy")
    profile_state_policy = _enum_value(profile_state_policy, StatePolicy, "profile_state_policy")
    oracle_env_alignment = _fingerprint_alignment(
        baseline.oracle_environment_fingerprint,
        gold.oracle_environment_fingerprint,
    )
    env_alignment = _fingerprint_alignment(
        baseline.environment_fingerprint,
        gold.environment_fingerprint,
    )

    failures: list[str] = []
    soft_warnings: list[str] = []
    same_task = baseline.task_id == gold.task_id
    same_instance = baseline.instance_id == gold.instance_id
    same_base = baseline.base_revision == gold.base_revision
    same_replicate = baseline.replicate_id == gold.replicate_id
    baseline_mode_valid = baseline.mode is MeasurementMode.BASELINE
    gold_mode_valid = gold.mode is MeasurementMode.GOLD_WITHOUT_CARRY
    same_config = (
        baseline.oracle_config_fingerprint is not None
        and gold.oracle_config_fingerprint is not None
        and baseline.oracle_config_fingerprint == gold.oracle_config_fingerprint
    )

    if not same_task:
        failures.append("task_id_mismatch")
    if not same_instance:
        failures.append("instance_id_mismatch")
    if not same_base:
        failures.append("base_revision_mismatch")
    if not same_replicate:
        failures.append("replicate_id_mismatch")
    if baseline.oracle_config_fingerprint is None or gold.oracle_config_fingerprint is None:
        failures.append("oracle_config_fingerprint_missing")
    elif baseline.oracle_config_fingerprint != gold.oracle_config_fingerprint:
        failures.append("oracle_config_fingerprint_mismatch")
    if not baseline_mode_valid:
        failures.append("baseline_mode_invalid")
    if not gold_mode_valid:
        failures.append("gold_mode_invalid")
    if baseline.carry_state is not CarryState.BASELINE_RAW_RETRY_CARRY:
        failures.append("baseline_carry_state_invalid")
    if gold.carry_state is not CarryState.GOLD_WITHOUT_CARRY:
        failures.append("gold_carry_state_invalid")
    if _gold_cherry_pick_risk(gold):
        failures.append("gold_cherry_pick_risk")

    if oracle_env_alignment is FingerprintAlignment.MISSING:
        soft_warnings.append("oracle_environment_fingerprint_missing")
    elif oracle_env_alignment is FingerprintAlignment.MISMATCH:
        soft_warnings.append("oracle_environment_fingerprint_mismatch")
    if env_alignment is FingerprintAlignment.MISSING:
        soft_warnings.append("environment_fingerprint_missing")
    elif env_alignment is FingerprintAlignment.MISMATCH:
        soft_warnings.append("environment_fingerprint_mismatch")

    if gold.mode is MeasurementMode.GOLD_WITH_CARRY or gold.carry_state is CarryState.GOLD_WITH_CARRY:
        status = PairedMeasurementStatus.INVALID_GOLD_WITH_CARRY
        if "gold_with_carry_requires_c3" not in failures:
            failures.append("gold_with_carry_requires_c3")
    elif token_or_cost_claim_present:
        status = PairedMeasurementStatus.INVALID_TOKEN_OR_COST_CLAIM
        failures.append("token_or_cost_claim_requires_canonical_telemetry_gateway")
    elif failures:
        status = PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    else:
        status = PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC

    diagnostics = {
        "pairing_failures": failures,
        "soft_warnings": soft_warnings,
        "same_task": same_task,
        "same_instance": same_instance,
        "same_base_revision": same_base,
        "same_replicate": same_replicate,
        "same_oracle_config_fingerprint": same_config,
        "oracle_environment_fingerprint_alignment": oracle_env_alignment.value,
        "environment_fingerprint_alignment": env_alignment.value,
        "baseline_mode_valid": baseline_mode_valid,
        "gold_mode_valid": gold_mode_valid,
        "baseline_carry_state": baseline.carry_state.value,
        "gold_carry_state": gold.carry_state.value,
        "execution_order_declared": True,
        "cache_state_policy": cache_state_policy.value,
        "profile_state_policy": profile_state_policy.value,
        "token_or_cost_claim_present": token_or_cost_claim_present,
        "success_only_diagnostic": status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC,
    }
    return PairedMeasurementRecord(
        schema_version=PAIRED_MEASUREMENT_SCHEMA_VERSION,
        pair_id=pair_id,
        baseline=baseline,
        gold=gold,
        status=status,
        execution_order=execution_order,
        cache_state_policy=cache_state_policy,
        profile_state_policy=profile_state_policy,
        non_reusable_for_token_claims=True,
        non_reusable_for_cost_claims=True,
        non_reusable_for_wall_clock_claims=True,
        non_reusable_for_economic_calibration=True,
        performance_claim_allowed=False,
        diagnostics=diagnostics,
    )
