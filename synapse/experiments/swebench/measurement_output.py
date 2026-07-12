"""Stage 3C C2-S3 measurement output boundary contracts.

This module labels already-built paired measurements and classifies
GoldEvidence-derived admission candidates. It does not implement telemetry,
carry runtime, application/session memory, controlled-change, or FULL
verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from synapse.experiments.swebench.gold_evidence import (
    EVIDENCE_REF_NAMESPACE,
    ValidatedGoldEvidence,
)
from synapse.experiments.swebench.paired_measurement import (
    CarryState,
    PairedMeasurementRecord,
    PairedMeasurementStatus,
)


MEASUREMENT_OUTPUT_SCHEMA_VERSION = "synapse.stage4.c2s3.measurement_output/v1"
EVIDENCE_ADMISSION_SCHEMA_VERSION = "synapse.stage4.c2s3.evidence_admission/v2"
TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION = "synapse.stage4.c2s3.telemetry_gateway_validation/v2"

_LOWER_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RE_ROI_CLAIM = re.compile(r"(?<![a-z])roi(?![a-z])", re.IGNORECASE)
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
    REJECTED_FORBIDDEN_ALIAS = "REJECTED_FORBIDDEN_ALIAS"
    REJECTED_FIELD_TYPE = "REJECTED_FIELD_TYPE"
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

CLAIM_VOCABULARY: frozenset[str] = frozenset({"note"})

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
    "gold_full_verified",
    "full_promotion",
    "token_savings",
    "token savings",
    "cost_savings",
    "cost savings",
    "percentage_savings",
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
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid {enum_type.__name__}") from exc


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


def _non_empty_string(value: Any, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _summary_text(value: str) -> str:
    if type(value) is not str:
        raise ValueError("summary must be a string")
    summary = value.strip()
    if not summary:
        raise ValueError("summary must be non-empty")
    if len(summary) > _SUMMARY_MAX_CHARS:
        raise ValueError("summary is too long")
    return summary


def _normalize_scope_entry(path: str) -> str:
    if type(path) is not str:
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
    if not isinstance(scope, Sequence) or isinstance(scope, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a sequence of paths")
    normalized = tuple(_normalize_scope_entry(item) for item in scope)
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    duplicates = sorted({item for item in normalized if normalized.count(item) > 1})
    if duplicates:
        raise ValueError(f"{field_name} contains duplicates: {', '.join(duplicates)}")
    return tuple(sorted(normalized))


def _lower_hex(value: str | None, pattern: re.Pattern[str], field_name: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} has invalid lowercase hex shape")
    return value


def _text_values(value: object, *, include_mapping_keys: bool) -> tuple[str, ...]:
    texts: list[str] = []
    if type(value) is str:
        texts.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if include_mapping_keys:
                texts.append(key)
            texts.extend(_text_values(item, include_mapping_keys=include_mapping_keys))
    elif type(value) in (tuple, list):
        for item in value:
            texts.extend(_text_values(item, include_mapping_keys=include_mapping_keys))
    return tuple(texts)


def _find_forbidden_claims(*values: object) -> tuple[str, ...]:
    texts: list[str] = []
    for value in values:
        texts.extend(_text_values(value, include_mapping_keys=True))
    lowered = tuple(text.lower() for text in texts)
    found = [term for term in _FORBIDDEN_CLAIMS if any(term in text for text in lowered)]
    if any(_RE_ROI_CLAIM.search(text) is not None for text in texts):
        found.append("roi")
    return tuple(dict.fromkeys(found))


def _claim_reasons(
    claims: Mapping[str, object],
    *content_values: object,
    scan_all_claim_values: bool = False,
) -> tuple[str, ...]:
    unknown = tuple(
        f"unknown_claim_key:{key}"
        for key in sorted(key for key in claims if key not in CLAIM_VOCABULARY)
    )
    scanned_values = tuple(
        claims[key]
        for key in claims
        if scan_all_claim_values or key in CLAIM_VOCABULARY
    )
    forbidden = _find_forbidden_claims(scanned_values, *content_values)
    return unknown + tuple(f"overclaim:{term}" for term in forbidden)


def _canonical_json(value_tree: object) -> str:
    return json.dumps(
        value_tree,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


@dataclass(frozen=True)
class DistilledEvidenceCandidate:
    source_kind: AdmissionSourceKind
    evidence_ref: str | None
    verified_commit: str | None
    base_sha: str | None
    task_contract_sha256: str | None
    patch_sha256: str | None
    source_evidence_identity_sha256: str | None
    allowed_scope: tuple[str, ...]
    summary: str
    claims: Mapping[str, object] = field(default_factory=dict)
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_kind = _enum_value(self.source_kind, AdmissionSourceKind, "source_kind")
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "allowed_scope", _normalize_scope(self.allowed_scope, "allowed_scope"))
        object.__setattr__(self, "summary", _summary_text(self.summary))
        object.__setattr__(self, "claims", _freeze_json_mapping(self.claims, "claims"))
        object.__setattr__(self, "diagnostics", _freeze_json_mapping(self.diagnostics, "diagnostics"))
        if source_kind is AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE:
            if not isinstance(self.evidence_ref, str) or not self.evidence_ref.startswith(EVIDENCE_REF_NAMESPACE):
                raise ValueError("evidence_ref must use the controlled-change evidence namespace")
            _lower_hex(self.verified_commit, _LOWER_COMMIT_RE, "verified_commit")
            _lower_hex(self.base_sha, _LOWER_COMMIT_RE, "base_sha")
            _lower_hex(self.task_contract_sha256, _LOWER_SHA256_RE, "task_contract_sha256")
            _lower_hex(self.patch_sha256, _LOWER_SHA256_RE, "patch_sha256")
            _lower_hex(
                self.source_evidence_identity_sha256,
                _LOWER_SHA256_RE,
                "source_evidence_identity_sha256",
            )
        else:
            for field_name in (
                "evidence_ref",
                "verified_commit",
                "base_sha",
                "task_contract_sha256",
                "patch_sha256",
                "source_evidence_identity_sha256",
            ):
                if getattr(self, field_name) is not None:
                    raise ValueError(f"{field_name} must be None for non-validated evidence sources")

    def to_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind.value,
            "evidence_ref": self.evidence_ref,
            "verified_commit": self.verified_commit,
            "base_sha": self.base_sha,
            "task_contract_sha256": self.task_contract_sha256,
            "patch_sha256": self.patch_sha256,
            "source_evidence_identity_sha256": self.source_evidence_identity_sha256,
            "allowed_scope": list(self.allowed_scope),
            "summary": self.summary,
            "claims": _thaw_json_tree(self.claims),
            "diagnostics": _thaw_json_tree(self.diagnostics),
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
        if type(self.candidate) is not DistilledEvidenceCandidate:
            raise ValueError("candidate must be an exact DistilledEvidenceCandidate")
        status = _enum_value(self.status, AdmissionStatus, "status")
        carry_authority = _enum_value(self.carry_authority, CarryAuthority, "carry_authority")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "carry_authority", carry_authority)
        for field_name in (
            "admitted_to_application",
            "admitted_to_session_memory",
            "gold_with_carry_enabled",
            "scope_expansion_allowed",
            "raw_carry_authority_allowed",
        ):
            if getattr(self, field_name) is not False:
                raise ValueError(f"{field_name} must remain false in C2-S3")
        if type(self.overclaim_detected) is not bool:
            raise ValueError("overclaim_detected must be a boolean")
        if not isinstance(self.rejection_reasons, Sequence) or isinstance(
            self.rejection_reasons, (str, bytes, bytearray)
        ):
            raise ValueError("rejection_reasons must be a sequence")
        reasons = tuple(
            _non_empty_string(item, "rejection_reasons")
            for item in self.rejection_reasons
        )
        object.__setattr__(self, "rejection_reasons", reasons)
        diagnostics = _freeze_json_mapping(self.diagnostics, "diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)

        if status is AdmissionStatus.ADMISSIBLE_CONTRACT_ONLY:
            if carry_authority is not CarryAuthority.DISTILLED_EVIDENCE_CANDIDATE:
                raise ValueError("carry_authority is inconsistent with admissible status")
            if self.overclaim_detected is not False:
                raise ValueError("overclaim_detected is inconsistent with admissible status")
            if reasons:
                raise ValueError("rejection_reasons is inconsistent with admissible status")
        else:
            if carry_authority is not CarryAuthority.DIAGNOSTIC_CONTEXT_ONLY:
                raise ValueError("carry_authority is inconsistent with rejected status")
            if not reasons:
                raise ValueError("rejection_reasons must be non-empty for rejected status")
        if self.overclaim_detected is not (status is AdmissionStatus.REJECTED_OVERCLAIM):
            raise ValueError("overclaim_detected is inconsistent with status")

        for field_name in (
            "admitted_to_application",
            "admitted_to_session_memory",
            "gold_with_carry_enabled",
            "scope_expansion_allowed",
            "raw_carry_authority_allowed",
        ):
            if field_name in diagnostics and diagnostics[field_name] is not getattr(self, field_name):
                raise ValueError(f"diagnostics.{field_name} is inconsistent with {field_name}")

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
            "diagnostics": _thaw_json_tree(self.diagnostics),
        }


@dataclass(frozen=True)
class TelemetryGatewayCandidateValidation:
    schema_version: str
    status: TelemetryGatewayCandidateStatus
    record_count: int
    required_fields: tuple[str, ...]
    missing_fields_by_index: Mapping[int, tuple[str, ...]]
    forbidden_alias_fields_by_index: Mapping[int, tuple[str, ...]]
    field_type_errors_by_index: Mapping[int, tuple[str, ...]]
    non_mapping_record_index: int | None
    diagnostics: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION:
            raise ValueError("schema_version must equal TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION")
        status = _enum_value(self.status, TelemetryGatewayCandidateStatus, "status")
        object.__setattr__(self, "status", status)
        if not _strict_int(self.record_count) or self.record_count < 0:
            raise ValueError("record_count must be >= 0")
        if type(self.required_fields) is not tuple or self.required_fields != TELEMETRY_GATEWAY_REQUIRED_FIELDS:
            raise ValueError("required_fields must equal TELEMETRY_GATEWAY_REQUIRED_FIELDS")

        def freeze_index_map(value: object, field_name: str) -> Mapping[int, tuple[str, ...]]:
            if not isinstance(value, Mapping):
                raise ValueError(f"{field_name} must be a mapping")
            result: dict[int, tuple[str, ...]] = {}
            for index, entries in value.items():
                if not _strict_int(index) or not 0 <= index < self.record_count:
                    raise ValueError(f"{field_name} contains invalid index")
                if type(entries) is not tuple or not entries:
                    raise ValueError(f"{field_name} values must be non-empty tuples")
                normalized = tuple(_non_empty_string(item, field_name) for item in entries)
                result[index] = normalized
            return MappingProxyType(result)

        missing = freeze_index_map(self.missing_fields_by_index, "missing_fields_by_index")
        aliases = freeze_index_map(
            self.forbidden_alias_fields_by_index,
            "forbidden_alias_fields_by_index",
        )
        type_errors = freeze_index_map(
            self.field_type_errors_by_index,
            "field_type_errors_by_index",
        )
        object.__setattr__(self, "missing_fields_by_index", missing)
        object.__setattr__(self, "forbidden_alias_fields_by_index", aliases)
        object.__setattr__(self, "field_type_errors_by_index", type_errors)
        if self.non_mapping_record_index is not None and (
            not _strict_int(self.non_mapping_record_index)
            or not 0 <= self.non_mapping_record_index < self.record_count
        ):
            raise ValueError("non_mapping_record_index must be an in-range strict integer")

        diagnostics = _freeze_json_mapping(self.diagnostics, "diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)
        for key in ("gateway_integrated", "runtime_gateway_authority"):
            if key not in diagnostics or diagnostics[key] is not False:
                raise ValueError(f"diagnostics.{key} must be exactly false")

        if status is TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE:
            valid = self.record_count > 0 and not missing and not aliases and not type_errors and self.non_mapping_record_index is None
        elif status is TelemetryGatewayCandidateStatus.REJECTED_EMPTY_RECORDS:
            valid = self.record_count == 0 and not missing and not aliases and not type_errors and self.non_mapping_record_index is None
        elif status is TelemetryGatewayCandidateStatus.REJECTED_NON_MAPPING_RECORD:
            valid = self.record_count > 0 and not missing and not aliases and not type_errors and self.non_mapping_record_index is not None
        elif status is TelemetryGatewayCandidateStatus.REJECTED_FORBIDDEN_ALIAS:
            valid = self.record_count > 0 and bool(aliases) and self.non_mapping_record_index is None
        elif status is TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD:
            valid = self.record_count > 0 and not aliases and bool(missing) and self.non_mapping_record_index is None
        else:
            valid = self.record_count > 0 and not aliases and not missing and bool(type_errors) and self.non_mapping_record_index is None
        if not valid:
            raise ValueError("telemetry validation structural fields are inconsistent with status")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "record_count": self.record_count,
            "required_fields": list(self.required_fields),
            "missing_fields_by_index": {str(index): list(fields) for index, fields in self.missing_fields_by_index.items()},
            "forbidden_alias_fields_by_index": {str(index): list(fields) for index, fields in self.forbidden_alias_fields_by_index.items()},
            "field_type_errors_by_index": {str(index): list(fields) for index, fields in self.field_type_errors_by_index.items()},
            "non_mapping_record_index": self.non_mapping_record_index,
            "diagnostics": _thaw_json_tree(self.diagnostics),
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
        measurement_label = _enum_value(self.measurement_label, MeasurementLabel, "measurement_label")
        telemetry_status = _enum_value(
            self.telemetry_gateway_status,
            TelemetryGatewayStatus,
            "telemetry_gateway_status",
        )
        source_status = _enum_value(
            self.source_pair_status,
            PairedMeasurementStatus,
            "source_pair_status",
        )
        baseline_carry = _enum_value(
            self.carry_state_baseline,
            CarryState,
            "carry_state_baseline",
        )
        gold_carry = _enum_value(
            self.carry_state_gold,
            CarryState,
            "carry_state_gold",
        )
        admission_status = None
        if self.admission_status is not None:
            admission_status = _enum_value(
                self.admission_status,
                AdmissionStatus,
                "admission_status",
            )
        object.__setattr__(self, "measurement_label", measurement_label)
        object.__setattr__(self, "telemetry_gateway_status", telemetry_status)
        object.__setattr__(self, "source_pair_status", source_status.value)
        object.__setattr__(self, "carry_state_baseline", baseline_carry.value)
        object.__setattr__(self, "carry_state_gold", gold_carry.value)
        object.__setattr__(
            self,
            "admission_status",
            None if admission_status is None else admission_status.value,
        )
        if measurement_label is MeasurementLabel.TOKEN_BEARING_REUSABLE_AFTER_GATEWAY:
            raise ValueError("TOKEN_BEARING_REUSABLE_AFTER_GATEWAY is reserved before canonical telemetry gateway")
        if telemetry_status not in (
            TelemetryGatewayStatus.MISSING,
            TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED,
        ):
            raise ValueError("telemetry_gateway_status must remain missing/not-implemented in C2-S3")
        for field_name in (
            "pair_id",
            "task_id",
            "instance_id",
            "base_revision",
            "baseline_run_id",
            "gold_run_id",
        ):
            object.__setattr__(self, field_name, _non_empty_string(getattr(self, field_name), field_name))
        for field_name in ("replicate_id", "baseline_attempt_count", "gold_attempt_count"):
            if not _strict_int(getattr(self, field_name)) or getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")
        for field_name in (
            "baseline_infra_error",
            "gold_infra_error",
            "token_bearing",
            "non_reusable_for_token_claims",
            "non_reusable_for_cost_claims",
            "non_reusable_for_wall_clock_claims",
            "non_reusable_for_economic_calibration",
            "performance_claim_allowed",
            "gold_with_carry_allowed",
            "admitted_to_application",
            "admitted_to_session_memory",
            "raw_carry_authority_allowed",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")
        for resolved_name, infra_name in (
            ("baseline_resolved", "baseline_infra_error"),
            ("gold_resolved", "gold_infra_error"),
        ):
            resolved = getattr(self, resolved_name)
            infra = getattr(self, infra_name)
            if infra is True and resolved is not None:
                raise ValueError(f"{resolved_name} must be None when {infra_name} is true")
            if infra is False and type(resolved) is not bool:
                raise ValueError(f"{resolved_name} must be an exact boolean when {infra_name} is false")
        for field_name in (
            "oracle_config_fingerprint",
            "oracle_environment_fingerprint_alignment",
            "environment_fingerprint_alignment",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not str or not value):
                raise ValueError(f"{field_name} must be None or a non-empty string")
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
        if (self.token_bearing is False) is not (telemetry_status is TelemetryGatewayStatus.MISSING):
            raise ValueError("token_bearing is inconsistent with telemetry_gateway_status")
        if (self.token_bearing is True) is not (
            telemetry_status is TelemetryGatewayStatus.CANONICAL_GATEWAY_NOT_IMPLEMENTED
        ):
            raise ValueError("token_bearing is inconsistent with telemetry_gateway_status")
        if measurement_label in (
            MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC,
            MeasurementLabel.TOKEN_BEARING_NON_REUSABLE,
        ) and source_status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC:
            raise ValueError("measurement_label requires a success-only source pair")
        if measurement_label is MeasurementLabel.SUCCESS_ONLY_DIAGNOSTIC and self.token_bearing is not False:
            raise ValueError("SUCCESS_ONLY_DIAGNOSTIC requires token_bearing false")
        if measurement_label is MeasurementLabel.TOKEN_BEARING_NON_REUSABLE and self.token_bearing is not True:
            raise ValueError("TOKEN_BEARING_NON_REUSABLE requires token_bearing true")

        diagnostics = _freeze_json_mapping(self.diagnostics, "diagnostics")
        object.__setattr__(self, "diagnostics", diagnostics)
        if measurement_label is MeasurementLabel.INVALID_OVERCLAIM:
            reasons = diagnostics.get("overclaim_reasons")
            if type(reasons) is not tuple or not reasons:
                raise ValueError("INVALID_OVERCLAIM requires non-empty diagnostics.overclaim_reasons")
            if (
                source_status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC
                and "source_pair_not_success_only" not in reasons
            ):
                raise ValueError("invalid source pair requires source_pair_not_success_only reason")
        for key in (
            "telemetry_gateway_integrated",
            "application_appended",
            "session_memory_appended",
            "raw_carry_authority",
            "gold_with_carry_enabled",
            "full_verified",
        ):
            if key in diagnostics and diagnostics[key] is not False:
                raise ValueError(f"diagnostics.{key} must be exactly false")
        for field_name in (
            "non_reusable_for_token_claims",
            "non_reusable_for_cost_claims",
            "non_reusable_for_wall_clock_claims",
            "non_reusable_for_economic_calibration",
            "performance_claim_allowed",
            "gold_with_carry_allowed",
            "admitted_to_application",
            "admitted_to_session_memory",
            "raw_carry_authority_allowed",
        ):
            if field_name in diagnostics and diagnostics[field_name] is not getattr(self, field_name):
                raise ValueError(f"diagnostics.{field_name} is inconsistent with {field_name}")

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
            "diagnostics": _thaw_json_tree(self.diagnostics),
        }


def candidate_from_validated_gold_evidence(
    proof: ValidatedGoldEvidence,
    *,
    allowed_scope: Sequence[str],
    summary: str,
    claims: Mapping[str, object] | None = None,
    diagnostics: Mapping[str, object] | None = None,
) -> DistilledEvidenceCandidate:
    if type(proof) is not ValidatedGoldEvidence:
        raise TypeError("proof must be an exact ValidatedGoldEvidence")
    return DistilledEvidenceCandidate(
        source_kind=AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE,
        evidence_ref=proof.evidence.evidence_ref,
        verified_commit=proof.verified_commit,
        base_sha=proof.base_sha,
        task_contract_sha256=proof.task_contract_sha256,
        patch_sha256=proof.patch_sha256,
        source_evidence_identity_sha256=proof.evidence_identity_sha256,
        allowed_scope=_normalize_scope(allowed_scope, "allowed_scope"),
        summary=summary,
        claims={} if claims is None else claims,
        diagnostics={} if diagnostics is None else diagnostics,
    )


def evaluate_evidence_admission(
    candidate: DistilledEvidenceCandidate,
    *,
    proof: ValidatedGoldEvidence | None,
    allowed_scope: Sequence[str],
    application_integration_available: bool = False,
) -> EvidenceAdmissionDecision:
    if type(candidate) is not DistilledEvidenceCandidate:
        raise TypeError("candidate must be an exact DistilledEvidenceCandidate")
    if proof is not None and type(proof) is not ValidatedGoldEvidence:
        raise TypeError("proof must be None or an exact ValidatedGoldEvidence")
    if type(application_integration_available) is not bool:
        raise TypeError("application_integration_available must be an exact boolean")
    allowed = set(_normalize_scope(allowed_scope, "allowed_scope"))
    reasons: list[str] = []
    status: AdmissionStatus
    if candidate.source_kind is AdmissionSourceKind.RAW_BASELINE_CARRY:
        status = AdmissionStatus.REJECTED_RAW_CARRY_AUTHORITY
        reasons.append("raw_carry_is_not_authority")
    elif candidate.source_kind is not AdmissionSourceKind.VALIDATED_GOLD_EVIDENCE:
        status = AdmissionStatus.REJECTED_INVALID_SOURCE
        reasons.append("invalid_source")
    elif proof is None:
        status = AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
        reasons.append("missing_validation_proof")
    else:
        proof_reason: str | None = None
        try:
            recomputed_identity = proof.recompute_identity_sha256()
        except (TypeError, ValueError):
            recomputed_identity = None
        if recomputed_identity != proof.evidence_identity_sha256:
            proof_reason = "evidence_identity_mismatch:proof_identity"
        elif proof.verified_commit != proof.evidence.verified_commit:
            proof_reason = "evidence_identity_mismatch:proof_verified_commit"
        elif proof.base_sha != proof.evidence.base_sha:
            proof_reason = "evidence_identity_mismatch:proof_base_sha"
        elif proof.task_contract_sha256 != proof.evidence.task_contract_sha256:
            proof_reason = "evidence_identity_mismatch:proof_task_contract_sha256"
        elif proof.patch_sha256 != proof.evidence.patch_sha256:
            proof_reason = "evidence_identity_mismatch:proof_patch_sha256"

        candidate_mismatch: str | None = None
        for field_name, candidate_value, proof_value in (
            ("evidence_ref", candidate.evidence_ref, proof.evidence.evidence_ref),
            ("verified_commit", candidate.verified_commit, proof.verified_commit),
            ("base_sha", candidate.base_sha, proof.base_sha),
            (
                "task_contract_sha256",
                candidate.task_contract_sha256,
                proof.task_contract_sha256,
            ),
            ("patch_sha256", candidate.patch_sha256, proof.patch_sha256),
            (
                "source_evidence_identity_sha256",
                candidate.source_evidence_identity_sha256,
                proof.evidence_identity_sha256,
            ),
        ):
            if candidate_value != proof_value:
                candidate_mismatch = f"evidence_identity_mismatch:{field_name}"
                break

        if proof_reason is not None:
            status = AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
            reasons.append(proof_reason)
        elif candidate_mismatch is not None:
            status = AdmissionStatus.REJECTED_INVALID_GOLD_EVIDENCE
            reasons.append(candidate_mismatch)
        elif not set(candidate.allowed_scope).issubset(allowed):
            status = AdmissionStatus.REJECTED_SCOPE_VIOLATION
            reasons.append("scope_expansion")
        else:
            claim_reasons = _claim_reasons(
                candidate.claims,
                candidate.summary,
                candidate.diagnostics,
            )
            if claim_reasons:
                status = AdmissionStatus.REJECTED_OVERCLAIM
                reasons.extend(claim_reasons)
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
        rejection_reasons=tuple(dict.fromkeys(reasons)),
        diagnostics=diagnostics,
    )


def validate_canonical_telemetry_gateway_candidate(
    records: Sequence[Mapping[str, object]],
) -> TelemetryGatewayCandidateValidation:
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise ValueError("records must be a sequence")
    required = TELEMETRY_GATEWAY_REQUIRED_FIELDS
    if len(records) == 0:
        return TelemetryGatewayCandidateValidation(
            schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
            status=TelemetryGatewayCandidateStatus.REJECTED_EMPTY_RECORDS,
            record_count=0,
            required_fields=required,
            missing_fields_by_index={},
            forbidden_alias_fields_by_index={},
            field_type_errors_by_index={},
            non_mapping_record_index=None,
            diagnostics={
                "gateway_integrated": False,
                "runtime_gateway_authority": False,
            },
        )

    missing: dict[int, tuple[str, ...]] = {}
    aliases: dict[int, tuple[str, ...]] = {}
    type_errors: dict[int, tuple[str, ...]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            return TelemetryGatewayCandidateValidation(
                schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
                status=TelemetryGatewayCandidateStatus.REJECTED_NON_MAPPING_RECORD,
                record_count=len(records),
                required_fields=required,
                missing_fields_by_index={},
                forbidden_alias_fields_by_index={},
                field_type_errors_by_index={},
                non_mapping_record_index=index,
                diagnostics={
                    "gateway_integrated": False,
                    "runtime_gateway_authority": False,
                },
            )
        missing_fields = tuple(field for field in required if field not in record)
        if missing_fields:
            missing[index] = missing_fields
        if "accounting_category" in record:
            aliases[index] = ("accounting_category",)

        errors: list[str] = []
        for field_name in required:
            if field_name not in record:
                continue
            value = record[field_name]
            if field_name in (
                "llm_call_id",
                "llm_call_accounting_category",
                "provider_id",
                "model_id",
                "service_tier",
                "usage_source",
                "usage_consistency",
                "primary_metric_status",
            ):
                if type(value) is not str or not value.strip():
                    errors.append(f"{field_name}:expected_non_empty_string")
            elif field_name in ("input_tokens", "output_tokens", "mixed_unallocated_tokens"):
                if not _strict_int(value) or value < 0:
                    errors.append(f"{field_name}:expected_non_negative_int")
            elif field_name in (
                "cached_read_input_tokens_if_reported",
                "cache_write_input_tokens_if_reported",
            ):
                if value is not None and (not _strict_int(value) or value < 0):
                    errors.append(f"{field_name}:expected_optional_non_negative_int")
            elif value is not None and not (
                (_strict_int(value) and value >= 0)
                or (type(value) is float and math.isfinite(value) and value >= 0)
            ):
                errors.append(
                    f"{field_name}:expected_optional_non_negative_finite_number"
                )

        if any(type(key) is not str for key in record):
            errors.append("record_key:expected_string")
        additional_names = sorted(
            key
            for key in record
            if type(key) is str
            and key not in required
            and key != "accounting_category"
        )
        for field_name in additional_names:
            try:
                _freeze_json_tree(record[field_name], field_name)
            except ValueError:
                errors.append(f"{field_name}:unsupported_json_value")
        if errors:
            type_errors[index] = tuple(dict.fromkeys(errors))

    status = TelemetryGatewayCandidateStatus.VALID_CANONICAL_CANDIDATE
    if aliases:
        status = TelemetryGatewayCandidateStatus.REJECTED_FORBIDDEN_ALIAS
    elif missing:
        status = TelemetryGatewayCandidateStatus.REJECTED_MISSING_REQUIRED_FIELD
    elif type_errors:
        status = TelemetryGatewayCandidateStatus.REJECTED_FIELD_TYPE

    return TelemetryGatewayCandidateValidation(
        schema_version=TELEMETRY_GATEWAY_VALIDATION_SCHEMA_VERSION,
        status=status,
        record_count=len(records),
        required_fields=required,
        missing_fields_by_index=missing,
        forbidden_alias_fields_by_index=aliases,
        field_type_errors_by_index=type_errors,
        non_mapping_record_index=None,
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
    if type(pair) is not PairedMeasurementRecord:
        raise TypeError("pair must be an exact PairedMeasurementRecord")
    for field_name, value in (
        ("token_fields_present", token_fields_present),
        ("raw_carry_authority_claim", raw_carry_authority_claim),
        ("target_gold_claim", target_gold_claim),
        ("full_verification_claim", full_verification_claim),
    ):
        if type(value) is not bool:
            raise TypeError(f"{field_name} must be an exact boolean")
    if admission_decision is not None and type(admission_decision) is not EvidenceAdmissionDecision:
        raise TypeError("admission_decision must be None or an exact EvidenceAdmissionDecision")
    frozen_claims = _freeze_json_mapping(
        {} if requested_claims is None else requested_claims,
        "requested_claims",
    )
    reasons: list[str] = []
    if pair.status is not PairedMeasurementStatus.PAIRED_SUCCESS_ONLY_DIAGNOSTIC:
        reasons.append("source_pair_not_success_only")
    reasons.extend(_claim_reasons(frozen_claims, scan_all_claim_values=True))
    if raw_carry_authority_claim:
        reasons.append("overclaim:raw_carry_authority_claim")
    if target_gold_claim:
        reasons.append("overclaim:target_gold_claim")
    if full_verification_claim:
        reasons.append("overclaim:full_verification_claim")
    reasons = list(dict.fromkeys(reasons))

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
        "requested_claims": _thaw_json_tree(frozen_claims),
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
    if type(output) is not SuccessOnlyMeasurementOutput:
        raise TypeError("output must be an exact SuccessOnlyMeasurementOutput")
    return _canonical_json(output.to_dict())


def evidence_admission_to_canonical_json(decision: EvidenceAdmissionDecision) -> str:
    if type(decision) is not EvidenceAdmissionDecision:
        raise TypeError("decision must be an exact EvidenceAdmissionDecision")
    return _canonical_json(decision.to_dict())


def telemetry_gateway_validation_to_canonical_json(validation: TelemetryGatewayCandidateValidation) -> str:
    if type(validation) is not TelemetryGatewayCandidateValidation:
        raise TypeError("validation must be an exact TelemetryGatewayCandidateValidation")
    return _canonical_json(validation.to_dict())
