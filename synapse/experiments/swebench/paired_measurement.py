"""Stage 3C C2-S2 paired Baseline/GOLD measurement contract.

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
import math
from types import MappingProxyType

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
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


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
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid {enum_type.__name__}") from exc


def _non_empty_string(value: Any, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _strict_int(value: object) -> bool:
    return type(value) is int


def _freeze_json_tree(value: object, field_name: str) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{field_name} mapping keys must be exact strings")
            frozen[key] = _freeze_json_tree(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if type(value) in (list, tuple):
        return tuple(
            _freeze_json_tree(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise ValueError(f"{field_name} contains unsupported JSON value")


def _freeze_json_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    frozen = _freeze_json_tree(value, field_name)
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return frozen


def _thaw_json_tree(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_tree(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json_tree(item) for item in value]
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
        mode = _enum_value(self.mode, MeasurementMode, "mode")
        carry_state = _enum_value(self.carry_state, CarryState, "carry_state")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "carry_state", carry_state)
        for field_name in ("run_id", "task_id", "instance_id", "base_revision", "terminal_status", "source_record_kind"):
            object.__setattr__(self, field_name, _non_empty_string(getattr(self, field_name), field_name))
        diagnostics = _freeze_json_mapping(self.diagnostics, "diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)
        _validate_paired_measurement_member_consistency(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_paired_measurement_member_consistency(self)
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
            "diagnostics": _thaw_json_tree(self.diagnostics),
        }


def _validate_paired_measurement_member_consistency(
    member: PairedMeasurementMember,
) -> None:
    if type(member) is not PairedMeasurementMember:
        raise ValueError("member must be an exact PairedMeasurementMember")
    if type(member.mode) is not MeasurementMode:
        raise ValueError("mode must be an exact MeasurementMode")
    if type(member.carry_state) is not CarryState:
        raise ValueError("carry_state must be an exact CarryState")
    for field_name in (
        "run_id",
        "task_id",
        "instance_id",
        "base_revision",
        "terminal_status",
        "source_record_kind",
    ):
        _non_empty_string(getattr(member, field_name), field_name)
    if not _strict_int(member.replicate_id) or member.replicate_id < 0:
        raise ValueError("replicate_id must be >= 0")
    if not _strict_int(member.attempt_count) or member.attempt_count < 0:
        raise ValueError("attempt_count must be >= 0")
    if type(member.infra_error) is not bool:
        raise ValueError("infra_error must be a boolean")
    if member.infra_error is True and member.resolved is not None:
        raise ValueError("resolved must be None when infra_error is true")
    if member.infra_error is False and type(member.resolved) is not bool:
        raise ValueError("resolved must be an exact boolean when infra_error is false")
    expected_carry = {
        MeasurementMode.BASELINE: CarryState.BASELINE_RAW_RETRY_CARRY,
        MeasurementMode.GOLD_WITHOUT_CARRY: CarryState.GOLD_WITHOUT_CARRY,
        MeasurementMode.GOLD_WITH_CARRY: CarryState.GOLD_WITH_CARRY,
    }[member.mode]
    if member.carry_state is not expected_carry:
        raise ValueError("mode and carry_state are inconsistent")
    for field_name in (
        "oracle_config_fingerprint",
        "oracle_environment_fingerprint",
        "environment_fingerprint",
    ):
        value = getattr(member, field_name)
        if value is not None and (type(value) is not str or not value):
            raise ValueError(f"{field_name} must be None or a non-empty string")
    if type(member.diagnostics) is not _MAPPING_PROXY_TYPE:
        raise ValueError("diagnostics must remain a frozen mapping")
    observed = member.diagnostics.get("attempts_observed_count")
    selected = member.diagnostics.get("selected_attempt_count")
    if observed is not None:
        if not _strict_int(observed) or observed < 0:
            raise ValueError("attempts_observed_count must be a non-negative strict integer")
        if observed != member.attempt_count:
            raise ValueError("attempts_observed_count must equal attempt_count")
    if selected is not None:
        if not _strict_int(selected) or selected < 0:
            raise ValueError("selected_attempt_count must be a non-negative strict integer")
        comparison_observed = member.attempt_count if observed is None else observed
        if selected > comparison_observed:
            raise ValueError("selected_attempt_count must not exceed attempts_observed_count")


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
        if type(self.baseline) is not PairedMeasurementMember:
            raise ValueError("baseline must be an exact PairedMeasurementMember")
        if type(self.gold) is not PairedMeasurementMember:
            raise ValueError("gold must be an exact PairedMeasurementMember")
        object.__setattr__(self, "pair_id", _non_empty_string(self.pair_id, "pair_id"))
        object.__setattr__(self, "status", _enum_value(self.status, PairedMeasurementStatus, "status"))
        object.__setattr__(self, "execution_order", _enum_value(self.execution_order, ExecutionOrder, "execution_order"))
        object.__setattr__(self, "cache_state_policy", _enum_value(self.cache_state_policy, StatePolicy, "cache_state_policy"))
        object.__setattr__(self, "profile_state_policy", _enum_value(self.profile_state_policy, StatePolicy, "profile_state_policy"))
        diagnostics = _freeze_json_mapping(self.diagnostics, "diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)
        _validate_paired_measurement_record_consistency(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_paired_measurement_record_consistency(self)
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
            "diagnostics": _thaw_json_tree(self.diagnostics),
        }


def paired_measurement_to_canonical_json(record: PairedMeasurementRecord) -> str:
    if type(record) is not PairedMeasurementRecord:
        raise TypeError("record must be an exact PairedMeasurementRecord")
    return json.dumps(
        record.to_dict(),
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


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
    if not _strict_int(replicate_id) or replicate_id < 0:
        raise ValueError("replicate_id must be a non-negative strict integer")
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
    if payload_diagnostics is not None and not isinstance(payload_diagnostics, Mapping):
        raise ValueError("payload diagnostics must be a mapping")
    diagnostics: dict[str, Any] = (
        dict(payload_diagnostics) if payload_diagnostics is not None else {}
    )
    for key in ("attempt_selection_policy", "attempts_observed_count", "selected_attempt_count"):
        if key in payload:
            diagnostics[key] = payload[key]
    diagnostics.setdefault("attempt_selection_policy", ALL_ATTEMPTS_RECORDED)
    diagnostics.setdefault("attempts_observed_count", 1)
    diagnostics.setdefault("selected_attempt_count", 1)
    observed = diagnostics["attempts_observed_count"]
    selected = diagnostics["selected_attempt_count"]
    if not _strict_int(observed) or observed < 0:
        raise ValueError("attempts_observed_count must be a non-negative strict integer")
    if not _strict_int(selected) or selected < 0:
        raise ValueError("selected_attempt_count must be a non-negative strict integer")
    if selected > observed:
        raise ValueError("selected_attempt_count must not exceed attempts_observed_count")
    run_id = payload.get("gold_run_id")
    if type(run_id) is not str or not run_id:
        raise ValueError("payload.gold_run_id must be a non-empty string")

    return PairedMeasurementMember(
        mode=MeasurementMode.GOLD_WITHOUT_CARRY if carry_state is not CarryState.GOLD_WITH_CARRY else MeasurementMode.GOLD_WITH_CARRY,
        run_id=run_id,
        task_id=task_id,
        instance_id=instance_id,
        base_revision=base_revision,
        replicate_id=replicate_id,
        resolved=resolved,
        infra_error=infra_error,
        terminal_status=result.status,
        attempt_count=observed,
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
    if not _strict_int(observed) or not _strict_int(selected):
        return True
    if selected < 1:
        return True
    return observed < selected


_PAIRING_DIAGNOSTIC_KEYS = (
    "pairing_failures",
    "soft_warnings",
    "same_task",
    "same_instance",
    "same_base_revision",
    "same_replicate",
    "same_oracle_config_fingerprint",
    "oracle_environment_fingerprint_alignment",
    "environment_fingerprint_alignment",
    "baseline_mode_valid",
    "gold_mode_valid",
    "baseline_carry_state",
    "gold_carry_state",
    "execution_order_declared",
    "cache_state_policy",
    "profile_state_policy",
    "token_or_cost_claim_present",
    "success_only_diagnostic",
)


def _evaluate_pairing_contract(
    *,
    baseline: PairedMeasurementMember,
    gold: PairedMeasurementMember,
    execution_order: ExecutionOrder,
    cache_state_policy: StatePolicy,
    profile_state_policy: StatePolicy,
    token_or_cost_claim_present: bool,
) -> tuple[PairedMeasurementStatus, Mapping[str, object]]:
    _validate_paired_measurement_member_consistency(baseline)
    _validate_paired_measurement_member_consistency(gold)
    if type(execution_order) is not ExecutionOrder:
        raise ValueError("execution_order must be an exact ExecutionOrder")
    if type(cache_state_policy) is not StatePolicy:
        raise ValueError("cache_state_policy must be an exact StatePolicy")
    if type(profile_state_policy) is not StatePolicy:
        raise ValueError("profile_state_policy must be an exact StatePolicy")
    if type(token_or_cost_claim_present) is not bool:
        raise TypeError("token_or_cost_claim_present must be an exact boolean")

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
    if baseline.infra_error is True:
        failures.append("baseline_infra_error")
    elif baseline.resolved is not True:
        failures.append("baseline_not_resolved")
    if gold.infra_error is True:
        failures.append("gold_infra_error")
    elif gold.resolved is not True:
        failures.append("gold_not_resolved")
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
        failures.append("gold_with_carry_requires_c3")
        status = PairedMeasurementStatus.INVALID_GOLD_WITH_CARRY
    elif token_or_cost_claim_present:
        failures.append("token_or_cost_claim_requires_canonical_telemetry_gateway")
        status = PairedMeasurementStatus.INVALID_TOKEN_OR_COST_CLAIM
    elif failures:
        status = PairedMeasurementStatus.UNPAIRED_DIAGNOSTIC_ONLY
    else:
        status = PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC

    return status, _freeze_json_mapping(
        {
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
            "success_only_diagnostic": (
                status is PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
            ),
        },
        "pairing_diagnostics",
    )


def _validate_paired_measurement_record_consistency(
    record: PairedMeasurementRecord,
) -> None:
    if type(record) is not PairedMeasurementRecord:
        raise ValueError("record must be an exact PairedMeasurementRecord")
    if record.schema_version != PAIRED_MEASUREMENT_SCHEMA_VERSION:
        raise ValueError("schema_version must equal PAIRED_MEASUREMENT_SCHEMA_VERSION")
    _non_empty_string(record.pair_id, "pair_id")
    if type(record.baseline) is not PairedMeasurementMember:
        raise ValueError("baseline must be an exact PairedMeasurementMember")
    if type(record.gold) is not PairedMeasurementMember:
        raise ValueError("gold must be an exact PairedMeasurementMember")
    _validate_paired_measurement_member_consistency(record.baseline)
    _validate_paired_measurement_member_consistency(record.gold)
    if record.baseline.mode is not MeasurementMode.BASELINE:
        raise ValueError("baseline.mode must be BASELINE")
    if record.gold.mode not in (
        MeasurementMode.GOLD_WITHOUT_CARRY,
        MeasurementMode.GOLD_WITH_CARRY,
    ):
        raise ValueError("gold.mode must be a Gold measurement mode")
    if type(record.status) is not PairedMeasurementStatus:
        raise ValueError("status must be an exact PairedMeasurementStatus")
    if type(record.execution_order) is not ExecutionOrder:
        raise ValueError("execution_order must be an exact ExecutionOrder")
    if type(record.cache_state_policy) is not StatePolicy:
        raise ValueError("cache_state_policy must be an exact StatePolicy")
    if type(record.profile_state_policy) is not StatePolicy:
        raise ValueError("profile_state_policy must be an exact StatePolicy")
    for field_name in (
        "non_reusable_for_token_claims",
        "non_reusable_for_cost_claims",
        "non_reusable_for_wall_clock_claims",
        "non_reusable_for_economic_calibration",
        "performance_claim_allowed",
    ):
        if type(getattr(record, field_name)) is not bool:
            raise ValueError(f"{field_name} must be a boolean")
    for field_name in (
        "non_reusable_for_token_claims",
        "non_reusable_for_cost_claims",
        "non_reusable_for_wall_clock_claims",
        "non_reusable_for_economic_calibration",
    ):
        if getattr(record, field_name) is not True:
            raise ValueError(f"{field_name} must remain true in C2-S2")
    if record.performance_claim_allowed is not False:
        raise ValueError("performance_claim_allowed must remain false in C2-S2")
    if type(record.diagnostics) is not _MAPPING_PROXY_TYPE:
        raise ValueError("diagnostics must remain a frozen mapping")
    token_claim = record.diagnostics.get("token_or_cost_claim_present", False)
    if type(token_claim) is not bool:
        raise ValueError("diagnostics.token_or_cost_claim_present must be an exact boolean")
    expected_status, expected_diagnostics = _evaluate_pairing_contract(
        baseline=record.baseline,
        gold=record.gold,
        execution_order=record.execution_order,
        cache_state_policy=record.cache_state_policy,
        profile_state_policy=record.profile_state_policy,
        token_or_cost_claim_present=token_claim,
    )
    if record.status is not expected_status:
        raise ValueError("status is inconsistent with authoritative pairing semantics")
    for key in _PAIRING_DIAGNOSTIC_KEYS:
        if key in record.diagnostics and record.diagnostics[key] != expected_diagnostics[key]:
            raise ValueError(f"diagnostics.{key} is inconsistent with pairing semantics")
    if record.status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC:
        failures = record.diagnostics.get("pairing_failures")
        if type(failures) is not tuple or not failures:
            raise ValueError("non-success status requires non-empty diagnostics.pairing_failures")
        if failures != expected_diagnostics["pairing_failures"]:
            raise ValueError("diagnostics.pairing_failures is inconsistent with pairing semantics")
    for field_name in (
        "non_reusable_for_token_claims",
        "non_reusable_for_cost_claims",
        "non_reusable_for_wall_clock_claims",
        "non_reusable_for_economic_calibration",
        "performance_claim_allowed",
    ):
        if field_name in record.diagnostics and record.diagnostics[field_name] is not getattr(record, field_name):
            raise ValueError(f"diagnostics.{field_name} is inconsistent with {field_name}")


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
    if type(baseline) is not PairedMeasurementMember:
        raise TypeError("baseline must be an exact PairedMeasurementMember")
    if type(gold) is not PairedMeasurementMember:
        raise TypeError("gold must be an exact PairedMeasurementMember")
    if type(token_or_cost_claim_present) is not bool:
        raise TypeError("token_or_cost_claim_present must be an exact boolean")
    pair_id = _non_empty_string(pair_id, "pair_id")
    execution_order = _enum_value(execution_order, ExecutionOrder, "execution_order")
    cache_state_policy = _enum_value(cache_state_policy, StatePolicy, "cache_state_policy")
    profile_state_policy = _enum_value(profile_state_policy, StatePolicy, "profile_state_policy")
    status, diagnostics = _evaluate_pairing_contract(
        baseline=baseline,
        gold=gold,
        execution_order=execution_order,
        cache_state_policy=cache_state_policy,
        profile_state_policy=profile_state_policy,
        token_or_cost_claim_present=token_or_cost_claim_present,
    )
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
