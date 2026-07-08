"""Stage 4 C2-S3 measurement output boundary contracts.

This module labels already-built paired measurements and classifies
GoldEvidence-derived admission candidates. It does not implement telemetry,
carry runtime, application/session memory, controlled-change, or FULL
verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any, Mapping, Sequence

from synapse.experiments.swebench.gold_evidence import (
    EVIDENCE_REF_NAMESPACE,
    GoldEvidence,
)
from synapse.experiments.swebench.paired_measurement import (
    CarryState,
    PairedMeasurementRecord,
    PairedMeasurementStatus,
)


MEASUREMENT_OUTPUT_SCHEMA_VERSION = "synapse.stage4.c2s3.measurement_output/v1"
EVIDENCE_ADMISSION_SCHEMA_VERSION = "synapse.stage4.c2s3.evidence_admission/v1"
TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION = "synapse.stage4.c2s3.telemetry_gateway_validation/v1"

_LOWER_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUMMARY_MAX_CHARS = 2000


class MeasurementLabel(str, Enum):
    SUCCESS_ONLY_DIAGNOSTIC = "SUCCESS_ONLY_DIAGNOSTIC"
    TOKEN_BEARING_NON_REUSABLE = "TOKEN_BEARING_NON_REUSABLE"
    TOKEN_BEARING_REUSABLE_AFTER_GATEWAY = "TOKEN_BEARING_REUSABLE_AFTER_GATEWAY"
    INVALID_OVERCLAIM = "INVALID_OVERCLAIM"


class TelemetryGatewayStatus(str, Enum):
    MISSING = "MISSING"
    CANDIDATE_VALIDATED_BUT_NOT_INTEGRATED = "CANDIDATE_VALIDATED_BUT_NOT_INTEGRATED"
    CANONICAL_GATEWAY_NOT_IMPLEMENTED = "CANONICAL_GATEWAY_NOT_IMPLEMENTED"


class TelemetryGatewayCandidateStatus(str, Enum):
    VALID_CANONICAL_CANDIDATE = "VALID_CANONICAL_CANDIDATE"
    REJECTED_EMPTY_RECORDS = "REJECTED_EMPTY_RECORDS"
    REJECTED_MISSING_REQUIRED_FIELD = "REJECTED_MISSING_REQUIRED_FIELD"
    REJECTED_ALIAS_ONLY = "REJECTED_ALIAS_ONLY"
    REJECTED_NON_MAPPING_RECORD = "REJECTED_NON_MAPPING_RECORD"


class AdmissionSourceKind(str, Enum):
    VALIDATED_GOLD_EVIDENCE = "VALIDATED_GOLD_EVIDENCE"
    RAW_BASELINE_CARRY = "RAW_BASELINE_CARRY"
    UNVALIDATED_REPORT = "UNVALIDATED_REPORT"
    UNKNOWN = "UNKNOWN"


class AdmissionStatus(str, Enum):
    ADMISSIBLE_CONTRACT_ONLY = "ADMISSIBLE_CONTRACT_ONLY"
    REJECTED_INVALID_SOURCE = "REJECTED_INVALID_SOURCE"
    REJECTED_INVALID_GOLD_EVIDENCE = "REJECTED_INVALID_GOLD_EVIDENCE"
    REJECTED_SCOPE_VIOLATION = "REJECTED_SCOPE_VIOLATION"
    REJECTED_RAW_CARRY_AUTHORITY = "REJECTED_RAW_CARRY_AUTHORITY"
    REJECTED_OVERCLAIM = "REJECTED_OVERCLAIM"
    REJECTED_APPLICATION_INTEGRATION_REQUIRED = "REJECTED_APPLICATION_INTEGRATION_REQUIRED"


class CarryAuthority(str, Enum):
    DIAGNOSTIC_CONTEXT_ONLY = "DIAGNOSTIC_CONTEXT_ONLY"
    DISTILLED_EVIDENCE_CANDIDATE = "DISTILLED_EVIDENCE_CANDIDATE"
    SESSION_MEMORY_AUTHORITY_NOT_IMPLEMENTED = "SESSION_MEMORY_AUTHORITY_NOT_IMPLEMENTED"


TELEMETRY_GATEWAY_REQUIRED_FIELDS: tuple[str, ...] = (
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
    "primary_metric_status",
)

_FORBIDDEN_CLAIMS = (
    "target_gold",
    "target gold",
    "carry_enabled_gold",
    "carry-enabled gold",
    "memory_safe_gold",
    "memory-safe gold",
    "admitted_evidence_gold",
    "admitted evidence gold",
    "gold_with_carry_measured",
    "gold-with-carry measured",
    "session_memory_appended",
    "session memory appended",
    "application_appended",
    "application appended",
    "repository_knowledge_admitted",
    "repositoryknowledge admitted",
    "full_verified",
    "full_verified",
    "gold_full_verified",
    "gold_full_verified",
    "full_promotion",
    "token_savings",
    "token savings",
    "cost_savings",
    "cost savings",
    "percentage_savings",
    "roi",
    "economic_calibration",
    "economic calibration",
    "performance_improvement",
    "performance improvement",
    "wall_clock_speedup",
    "wall-clock speedup",
    "latency_improvement",
    "throughput_improvement",
)


def _enum_value(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid {enum_type.__name__}") from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_value(value.to_dict())
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _json_safe_mapping(value: Mapping[str, object], field_name: str) -> dict[str, object]:
    data = dict(value)
    try:
        json.dumps(_json_value(data), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-serializable: {exc}") from exc
    return data


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _summary_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("summary must be a string")
    summary = value.strip()
    if not summary:
        raise ValueError("summary must be non-empty")
    if len(summary) > _SUMMARY_MAX_CHARS:
        raise ValueError("summary is too long")
    return summary


def _normalize_scope_entry(path: str) -> str:
    if not isinstance(path, str):
        raise ValueError("allowed_scope entries must be strings")
    if not path:
        raise ValueError("allowed_scope entries must be non-empty")
    if "\x00" in path:
        raise ValueError("allowed_scope entries must not contain NUL")
    windows = PureWindowsPath(path)
    if windows.is_absolute() or bool(windows.drive):
        raise ValueError("allowed_scope entries must be repo-relative")
    if PurePosixPath(path).is_absolute():
        raise ValueError("allowed_scope entries must be repo-relative")
    if "//" in path:
        raise ValueError("allowed_scope entries must not contain empty segments")
    normalized = path.replace("\\", "/")
    if "//" in normalized:
        raise ValueError("allowed_scope entries must not contain empty segments")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("allowed_scope entries must not contain traversal or dot segments")
    return normalized


def _normalize_scope(scope: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(scope, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence of paths")
    normalized = tuple(_normalize_scope_entry(item) for item in scope)
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _lower_hex(value: str | None, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} has invalid lowercase hex shape")
    return value


def _find_forbidden_claims(*values: Any) -> tuple[str, ...]:
    found: list[str] = []

    def inspect(value: Any) -> None:
        if isinstance(value, str):
            lowered = value.lower()
            for term in _FORBIDDEN_CLAIMS:
                if term in lowered and term not in found:
                    found.append(term)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                inspect(str(key))
                inspect(item)
        elif isinstance(value, (tuple, list)):
            for item in value:
                inspect(item)

    for item in values:
        inspect(item)
    return tuple(found)


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_value(value), sort_keys=True, indent=2) + "\n"


@dataclass(frozen=True)
class DistilledEvidenceCandidate:
    source_kind: AdmissionSourceKind
    evidence_ref: str | None
    verified_commit: str | None
    base_sha: str | None
    task_contract_sha256: str | None
    patch_sha256: str | None
    allowed_scope: tuple[str, ...]
    summary: str
    claims: Mapping[str, object] = field(default_factory=dict)
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_kind = _enum_value(self.source_kind, AdmissionSourceKind, "source_kind")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "allowed_scope", _normalize_scope(self.allowed_scope, "allowed_scope"))
        object.__setattr__(self, "summary", _summary_text(self.summary))
        object.__setattr__(self, "claims", _json_safe_mapping(self.claims, "claims"))
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))
        if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE:
            if not isinstance(self.evidence_ref, str) or not self.evidence_ref.startswith(EVIDENCE_REF_NAMESPACE):
                raise ValueError("evidence_ref must use the controlled-change evidence namespace")
            _lower_hex(self.verified_commit, _LOWER_COMMIT_RE, "verified_commit")
            _lower_hex(self.base_sha, _LOWER_COMMIT_RE, "base_sha")
            _lower_hex(self.task_contract_sha256, _LOWER_SHA256_RE, "task_contract_sha256")
            _lower_hex(self.patch_sha256, _LOWER_SHA256_RE, "patch_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind.value,
            "evidence_ref": self.evidence_ref,
            "verified_commit": self.verified_commit,
            "base_sha": self.base_sha,
            "task_contract_sha256": self.task_contract_sha256,
            "patch_sha256": self.patch_sha256,
            "allowed_scope": list(self.allowed_scope),
            "summary": self.summary,
            "claims": dict(self.claims),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class EvidenceAdmissionDecision:
    schema_version: str
    candidate: DistilledEvidenceCandidate
    status: AdmissionStatus
    carry_authority: CarryAuthority
    admitted_to_application: bool
    admitted_to_session_memory: bool
    gold_with_carry_enabled: bool
    scope_expansion_allowed: bool
    raw_carry_authority_allowed: bool
    overclaim_detected: bool
    rejection_reasons: tuple[str, ...]
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_ADMISSION_SCHEMA_VERSION:
            raise ValueError("schema_version must equal EVIDENCE_ADMISSION_SCHEMA_VERSION")
        object.__setattr__(self, "status", _enum_value(self.status, AdmissionStatus, "status"))
        object.__setattr__(self, "carry_authority", _enum_value(self.carry_authority, CarryAuthority, "carry_authority"))
        for field_name in (
            "admitted_to_application",
            "admitted_to_session_memory",
            "gold_with_carry_enabled",
            "scope_expansion_allowed",
            "raw_carry_authority_allowed",
        ):
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false in C2-S3")
        if not isinstance(self.overclaim_detected, bool):
            raise ValueError("overclaim_detected must be a boolean")
        object.__setattr__(self, "rejection_reasons", tuple(_non_empty_string(item, "rejection_reasons") for item in self.rejection_reasons))
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate": self.candidate.to_dict(),
            "status": self.status.value,
            "carry_authority": self.carry_authority.value,
            "admitted_to_application": self.admitted_to_application,
            "admitted_to_session_memory": self.admitted_to_session_memory,
            "gold_with_carry_enabled": self.gold_with_carry_enabled,
            "scope_expansion_allowed": self.scope_expansion_allowed,
            "raw_carry_authority_allowed": self.raw_carry_authority_allowed,
            "overclaim_detected": self.overclaim_detected,
            "rejection_reasons": list(self.rejection_reasons),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class TelemetryGatewayCandidateValidation:
    schema_version: str
    status: TelemetryGatewayCandidateStatus
    record_count: int
    required_fields: tuple[str, ...]
    missing_fields_by_index: Mapping[int, tuple[str, ...]]
    alias_only_fields_by_index: Mapping[int, tuple[str, ...]]
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION:
            raise ValueError("schema_version must equal TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION")
        object.__setattr__(self, "status", _enum_value(self.status, TelemetryGatewayCandidateStatus, "status"))
        if not isinstance(self.record_count, int) or self.record_count < 0:
            raise ValueError("record_count must be >= 0")
        required = tuple(_non_empty_string(item, "required_fields") for item in self.required_fields)
        if "llm_call_accounting_category" not in required:
            raise ValueError("required_fields must include llm_call_accounting_category")
        object.__setattr__(self, "required_fields", required)
        object.__setattr__(self, "missing_fields_by_index", {int(index): tuple(fields) for index, fields in self.missing_fields_by_index.items()})
        object.__setattr__(self, "alias_only_fields_by_index", {int(index): tuple(fields) for index, fields in self.alias_only_fields_by_index.items()})
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "record_count": self.record_count,
            "required_fields": list(self.required_fields),
            "missing_fields_by_index": {index: list(fields) for index, fields in self.missing_fields_by_index.items()},
            "alias_only_fields_by_index": {index: list(fields) for index, fields in self.alias_only_fields_by_index.items()},
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class SuccessOnlyMeasurementOutput:
    schema_version: str
    pair_id: str
    source_pair_status: str
    task_id: str
    instance_id: str
    base_revision: str
    replicate_id: int
    baseline_run_id: str
    gold_run_id: str
    baseline_resolved: bool | None
    gold_resolved: bool | None
    baseline_infra_error: bool
    gold_infra_error: bool
    baseline_attempt_count: int
    gold_attempt_count: int
    oracle_config_fingerprint: str | None
    oracle_environment_fingerprint_alignment: str | None
    environment_fingerprint_alignment: str | None
    carry_state_baseline: str
    carry_state_gold: str
    measurement_label: MeasurementLabel
    telemetry_gateway_status: TelemetryGatewayStatus
    token_bearing: bool
    non_reusable_for_token_claims: bool
    non_reusable_for_cost_claims: bool
    non_reusable_for_wall_clock_claims: bool
    non_reusable_for_economic_calibration: bool
    performance_claim_allowed: bool
    gold_with_carry_allowed: bool
    admission_status: str | None = None
    admitted_to_application: bool = False
    admitted_to_session_memory: bool = False
    raw_carry_authority_allowed: bool = False
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_OUTPUT_SCHEMA_VERSION:
            raise ValueError("schema_version must equal MEASUREMENT_OUTPUT_SCHEMA_VERSION")
        object.__setattr__(self, "measurement_label", _enum_value(self.measurement_label, MeasurementLabel, "measurement_label"))
        object.__setattr__(self, "telemetry_gateway_status", _enum_value(self.telemetry_gateway_status, TelemetryGatewayStatus, "telemetry_gateway_status"))
        for field_name in ("pair_id", "source_pair_status", "task_id", "instance_id", "base_revision", "baseline_run_id", "gold_run_id", "carry_state_baseline", "carry_state_gold"):
            object.__setattr__(self, field_name, _non_empty_string(getattr(self, field_name), field_name))
        for field_name in ("replicate_id", "baseline_attempt_count", "gold_attempt_count"):
            if not isinstance(getattr(self, field_name), int) or getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")
        for field_name in ("baseline_infra_error", "gold_infra_error", "token_bearing"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"{field_name} must be a boolean")
        for field_name in (
            "non_reusable_for_token_claims",
            "non_reusable_for_cost_claims",
            "non_reusable_for_wall_clock_claims",
            "non_reusable_for_economic_calibration",
        ):
            if getattr(self, field_name) is not True:
                raise ValueError(f"{field_name} must remain true in C2-S3")
        for field_name in (
            "performance_claim_allowed",
            "gold_with_carry_allowed",
            "admitted_to_application",
            "admitted_to_session_memory",
            "raw_carry_authority_allowed",
        ):
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false in C2-S3")
        object.__setattr__(self, "diagnostics", _json_safe_mapping(self.diagnostics, "diagnostics"))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "pair_id": self.pair_id,
            "source_pair_status": self.source_pair_status,
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "base_revision": self.base_revision,
            "replicate_id": self.replicate_id,
            "baseline_run_id": self.baseline_run_id,
            "gold_run_id": self.gold_run_id,
            "baseline_resolved": self.baseline_resolved,
            "gold_resolved": self.gold_resolved,
            "baseline_infra_error": self.baseline_infra_error,
            "gold_infra_error": self.gold_infra_error,
            "baseline_attempt_count": self.baseline_attempt_count,
            "gold_attempt_count": self.gold_attempt_count,
            "oracle_config_fingerprint": self.oracle_config_fingerprint,
            "oracle_environment_fingerprint_alignment": self.oracle_environment_fingerprint_alignment,
            "environment_fingerprint_alignment": self.environment_fingerprint_alignment,
            "carry_state_baseline": self.carry_state_baseline,
            "carry_state_gold": self.carry_state_gold,
            "measurement_label": self.measurement_label.value,
            "telemetry_gateway_status": self.telemetry_gateway_status.value,
            "token_bearing": self.token_bearing,
            "non_reusable_for_token_claims": self.non_reusable_for_token_claims,
            "non_reusable_for_cost_claims": self.non_reusable_for_cost_claims,
            "non_reusable_for_wall_clock_claims": self.non_reusable_for_wall_clock_claims,
            "non_reusable_for_economic_calibration": self.non_reusable_for_economic_calibration,
            "performance_claim_allowed": self.performance_claim_allowed,
            "gold_with_carry_allowed": self.gold_with_carry_allowed,
            "admission_status": self.admission_status,
            "admitted_to_application": self.admitted_to_application,
            "admitted_to_session_memory": self.admitted_to_session_memory,
            "raw_carry_authority_allowed": self.raw_carry_authority_allowed,
            "diagnostics": dict(self.diagnostics),
        }


def candidate_from_gold_evidence(
    evidence: GoldEvidence,
    *,
    allowed_scope: Sequence[str],
    summary: str,
    claims: Mapping[str, object] | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> DistilledEvidenceCandidate:
    return DistilledEvidenceCandidate(
        source_kind=AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE,
        evidence_ref=evidence.evidence_ref,
        verified_commit=evidence.verified_commit,
        base_sha=evidence.base_sha,
        task_contract_sha256=evidence.task_contract_sha256,
        patch_sha256=evidence.patch_sha256,
        allowed_scope=_normalize_scope(allowed_scope, "allowed_scope"),
        summary=summary,
        claims=claims or {},
        diagnostics=diagnostics or {},
    )


def evaluate_evidence_admission(
    candidate: DistilledEvidenceCandidate,
    *,
    validation_ok: bool,
    allowed_scope: Sequence[str],
    application_integration_available: bool = False,
) -> EvidenceAdmissionDecision:
    allowed = set(_normalize_scope(allowed_scope, "allowed_scope"))
    reasons: list[str] = []
    status: AdmissionStatus
    if validation_ok is False:
        status = AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
        reasons.append("invalid_gold_evidence")
    elif candidate.source_kind is AdmissionSourceKind.RAW_BASELINE_CARRY:
        status = AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY
        reasons.append("raw_carry_is_not_authority")
    elif candidate.source_kind is not AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE:
        status = AdmissionStatus.REJECTED_INVALID_SOURCE
        reasons.append("invalid_source")
    elif not set(candidate.allowed_scope).issubset(allowed):
        status = AdmissionStatus.REJECTED_SCOPE_VIOLATION
        reasons.append("scope_expansion")
    else:
        forbidden = _find_forbidden_claims(candidate.summary, candidate.claims, candidate.diagnostics)
        if forbidden:
            status = AdmissionStatus.REJECTED_OVERCLAIM
            reasons.extend(f"overclaim:{term}" for term in forbidden)
        else:
            status = AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY

    carry_authority = (
        CarryAuthority.DISTILLED_EVIDENCE_CANDIDATE
        if status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY
        else CarryAuthority.DIAGNOSTIC_CONTEXT_ONLY
    )
    diagnostics = {
        "application_integration_available": application_integration_available,
        "candidate_scope": list(candidate.allowed_scope),
        "allowed_scope": sorted(allowed),
        "contract_only": status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY,
    }
    return EvidenceAdmissionDecision(
        schema_version=EVIDENCE_ADMISSION_SCHEMA_VERSION,
        candidate=candidate,
        status=status,
        carry_authority=carry_authority,
        admitted_to_application=False,
        admitted_to_session_memory=False,
        gold_with_carry_enabled=False,
        scope_expansion_allowed=False,
        raw_carry_authority_allowed=False,
        overclaim_detected=status is AdmissionStatus.REJECTED_OVERCLAIM,
        rejection_reasons=tuple(reasons),
        diagnostics=diagnostics,
    )


def validate_canonical_telemetry_gateway_candidate(
    records: Sequence[Mapping[str, object]],
) -> TelemetryGatewayCandidateValidation:
    required = TELEMETRY_GATEWAY_REQUIRED_FIELDS
    if len(records) == 0:
        return TelemetryGatewayCandidateValidation(
            schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
            status=TelemetryGatewayCandidateStatus.REJECTED_EMPTY_RECORDS,
            record_count=0,
            required_fields=required,
            missing_fields_by_index={},
            alias_only_fields_by_index={},
            diagnostics={"gateway_integrated": False},
        )

    missing: dict[int, tuple[str, ...]] = {}
    aliases: dict[int, tuple[str, ...]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            return TelemetryGatewayCandidateValidation(
                schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
                status=TelemetryGatewayCandidateStatus.REJECTED_NON_MAPPING_RECORD,
                record_count=len(records),
                required_fields=required,
                missing_fields_by_index={},
                alias_only_fields_by_index={},
                diagnostics={"non_mapping_record_index": index, "gateway_integrated": False},
            )
        missing_fields = tuple(field for field in required if field not in record)
        if missing_fields:
            missing[index] = missing_fields
        if "accounting_category" in record and "llm_call_accounting_category" not in record:
            aliases[index] = ("accounting_category",)

    status = TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE
    if aliases:
        status = TelemetryGatewayCandidateStatus.REJECTED_ALIAS_ONLY
    elif missing:
        status = TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD

    return TelemetryGatewayCandidateValidation(
        schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
        status=status,
        record_count=len(records),
        required_fields=required,
        missing_fields_by_index=missing,
        alias_only_fields_by_index=aliases,
        diagnostics={
            "gateway_integrated": False,
            "runtime_gateway_authority": False,
        },
    )


def build_success_only_measurement_output(
    pair: PairedMeasurementRecord,
    *,
    token_fields_present: bool = False,
    requested_claims: Mapping[str, object] | None = None,
    admission_decision: EvidenceAdmissionDecision | None = None,
    raw_carry_authority_claim: bool = False,
    target_gold_claim: bool = False,
    full_verification_claim: bool = False,
) -> SuccessOnlyMeasurementOutput:
    requested_claims = requested_claims or {}
    reasons: list[str] = []
    if pair.status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC:
        reasons.append("source_pair_not_success_only")
    forbidden = list(_find_forbidden_claims(requested_claims))
    if raw_carry_authority_claim:
        forbidden.append("raw_carry_authority_claim")
    if target_gold_claim:
        forbidden.append("target_gold_claim")
    if full_verification_claim:
        forbidden.append("full_verification_claim")
    if forbidden:
        reasons.extend(f"overclaim:{term}" for term in forbidden)

    if reasons:
        label = MeasurementLabel.INVALID_OVERCLAIM
    elif token_fields_present:
        label = MeasurementLabel.TOKEN_BEARING_NON_REUSABLE
    else:
        label = MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC

    telemetry_status = (
        TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED
        if token_fields_present
        else TelemetryGatewayStatus.MISSING
    )
    diagnostics: dict[str, object] = {
        "source_pair_not_success_only": "source_pair_not_success_only" in reasons,
        "overclaim_reasons": reasons,
        "token_fields_present": token_fields_present,
        "requested_claims": dict(requested_claims),
        "telemetry_gateway_integrated": False,
    }
    admission_status: str | None = None
    admitted_to_application = False
    admitted_to_session_memory = False
    raw_carry_allowed = False
    if admission_decision is not None:
        admission_status = admission_decision.status.value
        admitted_to_application = False
        admitted_to_session_memory = False
        raw_carry_allowed = False
        diagnostics["admission_status"] = admission_status

    return SuccessOnlyMeasurementOutput(
        schema_version=MEASUREMENT_OUTPUT_SCHEMA_VERSION,
        pair_id=pair.pair_id,
        source_pair_status=pair.status.value,
        task_id=pair.baseline.task_id,
        instance_id=pair.baseline.instance_id,
        base_revision=pair.baseline.base_revision,
        replicate_id=pair.baseline.replicate_id,
        baseline_run_id=pair.baseline.run_id,
        gold_run_id=pair.gold.run_id,
        baseline_resolved=pair.baseline.resolved,
        gold_resolved=pair.gold.resolved,
        baseline_infra_error=pair.baseline.infra_error,
        gold_infra_error=pair.gold.infra_error,
        baseline_attempt_count=pair.baseline.attempt_count,
        gold_attempt_count=pair.gold.attempt_count,
        oracle_config_fingerprint=pair.baseline.oracle_config_fingerprint,
        oracle_environment_fingerprint_alignment=pair.diagnostics.get("oracle_environment_fingerprint_alignment"),
        environment_fingerprint_alignment=pair.diagnostics.get("environment_fingerprint_alignment"),
        carry_state_baseline=pair.baseline.carry_state.value,
        carry_state_gold=pair.gold.carry_state.value,
        measurement_label=label,
        telemetry_gateway_status=telemetry_status,
        token_bearing=token_fields_present,
        non_reusable_for_token_claims=True,
        non_reusable_for_cost_claims=True,
        non_reusable_for_wall_clock_claims=True,
        non_reusable_for_economic_calibration=True,
        performance_claim_allowed=False,
        gold_with_carry_allowed=False,
        admission_status=admission_status,
        admitted_to_application=admitted_to_application,
        admitted_to_session_memory=admitted_to_session_memory,
        raw_carry_authority_allowed=raw_carry_allowed,
        diagnostics=diagnostics,
    )


def measurement_output_to_canonical_json(output: SuccessOnlyMeasurementOutput) -> str:
    return _canonical_json(output)


def evidence_admission_to_canonical_json(decision: EvidenceAdmissionDecision) -> str:
    return _canonical_json(decision)


def telemetry_gateway_validation_to_canonical_json(validation: TelemetryGatewayCandidateValidation) -> str:
    return _canonical_json(validation)
