"""Immutable Stage 4 Patch 2 Behavior domain contracts.

Behavior content is not verification, admission, execution, publication, or
economic authority.  This module provides the only public compiler facade for
a complete, recursively revalidated :class:`SynapseBehaviorUnit`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Any

from synapse.version import LANGUAGE_VERSION

from .canonicalization import (
    BEHAVIOR_BLOB_SCHEMA_V1,
    BEHAVIOR_CORE_SCHEMA_V1,
    CANONICAL_PROGRAM_IR_V1,
    COMPILER_ADAPTER_PROFILE_V1,
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    CanonicalBehaviorCore,
    CanonicalizationFailureCode,
    CanonicalizationViolation,
    CompilerBinding,
    ContentKey,
    HashBoundRef,
    ProgramForm,
    RefKind,
    _bind_compiler_evidence,
    _compile_validated_behavior_core,
    _compiler_binding_from_dict,
    _compiler_binding_to_dict,
    _make_canonical_behavior_core,
    _normalize_canonical_program,
    _validate_compiler_binding,
    canonical_base64url,
    canonicalize_stage4_payload,
    decode_canonical_base64url,
    decode_canonical_program_ir,
    validate_canonical_behavior_core,
    validate_ref_collection,
)
from .contracts import (
    ActorIdentity,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_record_id,
    record_id_from_dict,
    validate_record_id,
)


_TRUSTED_SEAL = object()
_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_CAPABILITY_RE = re.compile(r"[a-z][a-z0-9._:-]{0,127}\Z")
_CLAIM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSIONED_RE = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z"
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {"password", "passwd", "secret", "token", "api_key", "private_key", "transcript", "raw_python", "source_code"}
)


class BehaviorFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNKNOWN_BEHAVIOR_KIND = "UNKNOWN_BEHAVIOR_KIND"
    UNKNOWN_VALUE_TYPE = "UNKNOWN_VALUE_TYPE"
    INVALID_DEFAULT = "INVALID_DEFAULT"
    INVALID_ABSENCE_POLICY = "INVALID_ABSENCE_POLICY"
    INVALID_ABSENCE_DETAIL = "INVALID_ABSENCE_DETAIL"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_VERIFICATION_CONTRACT = "MISSING_VERIFICATION_CONTRACT"
    INVALID_VERIFICATION_CONTRACT = "INVALID_VERIFICATION_CONTRACT"
    INVALID_REPLAY_CONTRACT = "INVALID_REPLAY_CONTRACT"
    CAPABILITY_WILDCARD = "CAPABILITY_WILDCARD"
    DUPLICATE_CAPABILITY = "DUPLICATE_CAPABILITY"
    CAPABILITY_MISMATCH = "CAPABILITY_MISMATCH"
    INVALID_GRANULARITY = "INVALID_GRANULARITY"
    RAW_PAYLOAD_FORBIDDEN = "RAW_PAYLOAD_FORBIDDEN"
    REF_KIND_MISMATCH = "REF_KIND_MISMATCH"
    BLOB_MISMATCH = "BLOB_MISMATCH"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    FAILED_HYPOTHESIS_RELABEL = "FAILED_HYPOTHESIS_RELABEL"


class BehaviorViolation(ValueError):
    def __init__(self, failure_code: BehaviorFailureCode, detail: str) -> None:
        if type(failure_code) is not BehaviorFailureCode:
            raise TypeError("failure_code must be exact BehaviorFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: BehaviorFailureCode, detail: str) -> BehaviorViolation:
    return BehaviorViolation(code, detail)


def _exact_dict(value: object, required: tuple[str, ...], name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} must be exact dict")
    if any(type(key) is not str for key in value):
        raise _fail(BehaviorFailureCode.UNKNOWN_FIELD, f"{name} keys must be exact strings")
    missing = [field for field in required if field not in value]
    if missing:
        raise _fail(BehaviorFailureCode.MISSING_REQUIRED_FIELD, f"{name} is missing field {missing[0]}")
    unknown = sorted(field for field in value if field not in required)
    if unknown:
        raise _fail(BehaviorFailureCode.UNKNOWN_FIELD, f"{name} has unknown field {unknown[0]}")
    return value


def _exact_list(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} must be exact list")
    return value


def _text(value: object, name: str, pattern: re.Pattern[str] | None = None) -> str:
    if type(value) is not str or not value:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} must be non-empty exact string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, f"{name} contains lone surrogate")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, f"{name} is not valid UTF-8") from exc
    if pattern is not None and pattern.fullmatch(value) is None:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} has invalid syntax")
    return value


def _enum(value: object, enum_type: type[Enum], code: BehaviorFailureCode, name: str) -> Enum:
    if type(value) is not str:
        raise _fail(code, f"{name} must be exact known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail(code, f"{name} is unknown") from exc


def _versioned(value: object, name: str) -> str:
    return _text(value, name, _VERSIONED_RE)


def _check_no_secret_or_large_inline(value: object, *, path: str = "$", depth: int = 0) -> None:
    if depth > 64:
        raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, "inline typed value is too deep")
    if value is None or type(value) in (bool, int, float):
        return
    if type(value) is str:
        _text(value, path)
        if len(value.encode("utf-8")) > 4096:
            raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, "large inline string must be a hash-bound ref")
        return
    if type(value) is list:
        if len(value) > 1024:
            raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, "large inline list must be a hash-bound ref")
        for index, item in enumerate(value):
            _check_no_secret_or_large_inline(item, path=f"{path}[{index}]", depth=depth + 1)
        return
    if type(value) is dict:
        if len(value) > 1024:
            raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, "large inline mapping must be a hash-bound ref")
        for key, item in value.items():
            if type(key) is not str:
                raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "typed value keys must be strings")
            if key.lower() in _FORBIDDEN_PAYLOAD_KEYS:
                raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, f"secret/raw field {key} must be a hash-bound ref")
            _check_no_secret_or_large_inline(item, path=f"{path}.{key}", depth=depth + 1)
        return
    raise _fail(BehaviorFailureCode.RAW_PAYLOAD_FORBIDDEN, f"unsupported inline type {type(value).__name__}")


class BehaviorKind(str, Enum):
    PROCEDURE = "procedure"
    REPOSITORY_FACT_CHECK = "repository_fact_check"
    FAILURE_REPRODUCTION = "failure_reproduction"
    REJECTED_HYPOTHESIS_GUARD = "rejected_hypothesis_guard"
    VERIFICATION_RECIPE = "verification_recipe"
    OPERATION_RECIPE = "operation_recipe"


class ValueType(str, Enum):
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    LIST = "list"
    RECORD = "record"
    CONTENT_KEY = "content_key"
    HASH_REF = "hash_ref"


class AbsencePolicy(str, Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL_ABSENT_ALLOWED = "OPTIONAL_ABSENT_ALLOWED"
    OPTIONAL_NULL_ALLOWED = "OPTIONAL_NULL_ALLOWED"
    DEFAULTED = "DEFAULTED"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    REDACTED = "REDACTED"


class DefaultKind(str, Enum):
    ABSENT = "ABSENT"
    NULL = "NULL"
    VALUE = "VALUE"


class AbsenceDetailKind(str, Enum):
    NONE = "NONE"
    UNKNOWN_REASON = "UNKNOWN_REASON"
    UNAVAILABLE_DETAIL = "UNAVAILABLE_DETAIL"
    NOT_APPLICABLE_DETAIL = "NOT_APPLICABLE_DETAIL"
    REDACTION_DETAIL = "REDACTION_DETAIL"


class AbsenceReasonCode(str, Enum):
    UNKNOWN_AT_CAPTURE = "UNKNOWN_AT_CAPTURE"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    DOMAIN_NOT_APPLICABLE = "DOMAIN_NOT_APPLICABLE"
    POLICY_REDACTION = "POLICY_REDACTION"


@dataclass(frozen=True)
class DefaultValue:
    kind: DefaultKind
    value: object = None

    def __post_init__(self) -> None:
        _validate_default(self)

    def to_dict(self) -> dict[str, object]:
        _validate_default(self)
        if self.kind is DefaultKind.VALUE:
            return {"kind": self.kind.value, "value": self.value}
        return {"kind": self.kind.value}

    @classmethod
    def from_dict(cls, value: object) -> DefaultValue:
        if type(value) is not dict or type(value.get("kind")) is not str:
            raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "default must be exact tagged dict")
        kind = _enum(value["kind"], DefaultKind, BehaviorFailureCode.INVALID_DEFAULT, "default.kind")
        assert isinstance(kind, DefaultKind)
        if kind is DefaultKind.VALUE:
            data = _exact_dict(value, ("kind", "value"), "default")
            return cls(kind, data["value"])
        _exact_dict(value, ("kind",), "default")
        return cls(kind)


def _validate_default(value: DefaultValue) -> None:
    if type(value) is not DefaultValue or type(value.kind) is not DefaultKind:
        raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "default must be exact DefaultValue")
    if value.kind is DefaultKind.VALUE:
        _check_no_secret_or_large_inline(value.value)
    elif value.value is not None:
        raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "ABSENT/NULL default cannot carry value")


@dataclass(frozen=True)
class EvaluatorDescriptor:
    evaluator_id: str
    evaluator_version: str

    def __post_init__(self) -> None:
        _versioned(self.evaluator_id, "evaluator_id")
        _versioned(self.evaluator_version, "evaluator_version")

    def to_dict(self) -> dict[str, str]:
        _versioned(self.evaluator_id, "evaluator_id")
        _versioned(self.evaluator_version, "evaluator_version")
        return {"evaluator_id": self.evaluator_id, "evaluator_version": self.evaluator_version}

    @classmethod
    def from_dict(cls, value: object) -> EvaluatorDescriptor:
        data = _exact_dict(value, ("evaluator_id", "evaluator_version"), "evaluator_descriptor")
        return cls(data["evaluator_id"], data["evaluator_version"])


@dataclass(frozen=True)
class AbsenceDetail:
    kind: AbsenceDetailKind
    reason_code: AbsenceReasonCode | None = None
    provenance_ref: HashBoundRef | None = None
    behavior_kind: BehaviorKind | None = None
    evaluator: EvaluatorDescriptor | None = None
    redaction_authority: ActorIdentity | None = None

    def __post_init__(self) -> None:
        _validate_absence_detail(self)

    def to_dict(self) -> dict[str, object]:
        _validate_absence_detail(self)
        result: dict[str, object] = {"kind": self.kind.value}
        if self.kind is AbsenceDetailKind.UNKNOWN_REASON:
            result["reason_code"] = self.reason_code.value  # type: ignore[union-attr]
        elif self.kind is AbsenceDetailKind.UNAVAILABLE_DETAIL:
            result.update({"reason_code": self.reason_code.value, "provenance_ref": self.provenance_ref.to_dict()})  # type: ignore[union-attr]
        elif self.kind is AbsenceDetailKind.NOT_APPLICABLE_DETAIL:
            result.update({"behavior_kind": self.behavior_kind.value, "evaluator": self.evaluator.to_dict(), "reason_code": self.reason_code.value})  # type: ignore[union-attr]
        elif self.kind is AbsenceDetailKind.REDACTION_DETAIL:
            result.update({"redaction_authority": self.redaction_authority.to_dict(), "reason_code": self.reason_code.value})  # type: ignore[union-attr]
        return result

    @classmethod
    def from_dict(cls, value: object) -> AbsenceDetail:
        if type(value) is not dict or type(value.get("kind")) is not str:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence detail must be exact tagged dict")
        kind = _enum(value["kind"], AbsenceDetailKind, BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence_detail.kind")
        assert isinstance(kind, AbsenceDetailKind)
        if kind is AbsenceDetailKind.NONE:
            _exact_dict(value, ("kind",), "absence_detail")
            return cls(kind)
        if kind is AbsenceDetailKind.UNKNOWN_REASON:
            data = _exact_dict(value, ("kind", "reason_code"), "absence_detail")
            reason = _enum(data["reason_code"], AbsenceReasonCode, BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence reason")
            assert isinstance(reason, AbsenceReasonCode)
            return cls(kind, reason_code=reason)
        if kind is AbsenceDetailKind.UNAVAILABLE_DETAIL:
            data = _exact_dict(value, ("kind", "reason_code", "provenance_ref"), "absence_detail")
            reason = _enum(data["reason_code"], AbsenceReasonCode, BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence reason")
            assert isinstance(reason, AbsenceReasonCode)
            return cls(kind, reason_code=reason, provenance_ref=HashBoundRef.from_dict(data["provenance_ref"]))
        if kind is AbsenceDetailKind.NOT_APPLICABLE_DETAIL:
            data = _exact_dict(value, ("kind", "behavior_kind", "evaluator", "reason_code"), "absence_detail")
            reason = _enum(data["reason_code"], AbsenceReasonCode, BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence reason")
            behavior_kind = _enum(data["behavior_kind"], BehaviorKind, BehaviorFailureCode.UNKNOWN_BEHAVIOR_KIND, "absence behavior_kind")
            assert isinstance(reason, AbsenceReasonCode)
            assert isinstance(behavior_kind, BehaviorKind)
            return cls(kind, reason_code=reason, behavior_kind=behavior_kind, evaluator=EvaluatorDescriptor.from_dict(data["evaluator"]))
        data = _exact_dict(value, ("kind", "redaction_authority", "reason_code"), "absence_detail")
        reason = _enum(data["reason_code"], AbsenceReasonCode, BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence reason")
        assert isinstance(reason, AbsenceReasonCode)
        return cls(kind, reason_code=reason, redaction_authority=ActorIdentity.from_dict(data["redaction_authority"]))


def _validate_absence_detail(value: AbsenceDetail) -> None:
    if type(value) is not AbsenceDetail or type(value.kind) is not AbsenceDetailKind:
        raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence detail must be exact typed value")
    fields = (value.reason_code, value.provenance_ref, value.behavior_kind, value.evaluator, value.redaction_authority)
    if value.kind is AbsenceDetailKind.NONE:
        if any(item is not None for item in fields):
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "NONE detail cannot carry data")
        return
    if type(value.reason_code) is not AbsenceReasonCode:
        raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence reason code is required")
    if value.kind is AbsenceDetailKind.UNKNOWN_REASON:
        if any(item is not None for item in fields[1:]) or value.reason_code is not AbsenceReasonCode.UNKNOWN_AT_CAPTURE:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "UNKNOWN detail fields mismatch")
    elif value.kind is AbsenceDetailKind.UNAVAILABLE_DETAIL:
        if type(value.provenance_ref) is not HashBoundRef or value.provenance_ref.kind is not RefKind.ABSENCE_PROVENANCE:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "UNAVAILABLE requires absence provenance ref")
        value.provenance_ref.to_dict()
        if any(item is not None for item in fields[2:]) or value.reason_code is not AbsenceReasonCode.UPSTREAM_UNAVAILABLE:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "UNAVAILABLE detail fields mismatch")
    elif value.kind is AbsenceDetailKind.NOT_APPLICABLE_DETAIL:
        if type(value.behavior_kind) is not BehaviorKind or type(value.evaluator) is not EvaluatorDescriptor:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "NOT_APPLICABLE requires kind and evaluator")
        value.evaluator.to_dict()
        if value.provenance_ref is not None or value.redaction_authority is not None or value.reason_code is not AbsenceReasonCode.DOMAIN_NOT_APPLICABLE:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "NOT_APPLICABLE detail fields mismatch")
    else:
        if type(value.redaction_authority) is not ActorIdentity:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "REDACTED requires exact ActorIdentity")
        value.redaction_authority.to_dict()
        if value.provenance_ref is not None or value.behavior_kind is not None or value.evaluator is not None or value.reason_code is not AbsenceReasonCode.POLICY_REDACTION:
            raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "REDACTED detail fields mismatch")


def _value_matches_type(value: object, value_type: ValueType) -> bool:
    if value_type is ValueType.NULL:
        return value is None
    if value_type is ValueType.BOOLEAN:
        return type(value) is bool
    if value_type is ValueType.INTEGER:
        return type(value) is int and -(2**53 - 1) <= value <= 2**53 - 1
    if value_type is ValueType.FLOAT:
        return type(value) is float
    if value_type is ValueType.STRING:
        return type(value) is str
    if value_type is ValueType.LIST:
        return type(value) is list
    if value_type is ValueType.RECORD:
        return type(value) is dict
    if value_type is ValueType.CONTENT_KEY:
        return type(value) is str and value.startswith("synapse.stage4.gold.content-key/v1:")
    if value_type is ValueType.HASH_REF:
        try:
            HashBoundRef.from_dict(value)
        except (CanonicalizationViolation, BehaviorViolation):
            return False
        return True
    return False


@dataclass(frozen=True)
class ContractField:
    name: str
    value_type: ValueType
    absence_policy: AbsencePolicy
    default: DefaultValue
    absence_detail: AbsenceDetail

    def __post_init__(self) -> None:
        _validate_contract_field(self)

    def to_dict(self) -> dict[str, object]:
        _validate_contract_field(self)
        return {
            "name": self.name,
            "value_type": self.value_type.value,
            "absence_policy": self.absence_policy.value,
            "default": self.default.to_dict(),
            "absence_detail": self.absence_detail.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> ContractField:
        data = _exact_dict(value, ("name", "value_type", "absence_policy", "default", "absence_detail"), "contract_field")
        value_type = _enum(data["value_type"], ValueType, BehaviorFailureCode.UNKNOWN_VALUE_TYPE, "field.value_type")
        policy = _enum(data["absence_policy"], AbsencePolicy, BehaviorFailureCode.INVALID_ABSENCE_POLICY, "field.absence_policy")
        assert isinstance(value_type, ValueType)
        assert isinstance(policy, AbsencePolicy)
        return cls(
            name=data["name"],
            value_type=value_type,
            absence_policy=policy,
            default=DefaultValue.from_dict(data["default"]),
            absence_detail=AbsenceDetail.from_dict(data["absence_detail"]),
        )


def _validate_contract_field(value: ContractField) -> None:
    if type(value) is not ContractField:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "contract field must be exact ContractField")
    _text(value.name, "field.name", _FIELD_RE)
    if type(value.value_type) is not ValueType:
        raise _fail(BehaviorFailureCode.UNKNOWN_VALUE_TYPE, "field value type is unknown")
    if type(value.absence_policy) is not AbsencePolicy:
        raise _fail(BehaviorFailureCode.INVALID_ABSENCE_POLICY, "field absence policy is unknown")
    _validate_default(value.default)
    _validate_absence_detail(value.absence_detail)
    expected_detail = {
        AbsencePolicy.UNKNOWN: AbsenceDetailKind.UNKNOWN_REASON,
        AbsencePolicy.UNAVAILABLE: AbsenceDetailKind.UNAVAILABLE_DETAIL,
        AbsencePolicy.NOT_APPLICABLE: AbsenceDetailKind.NOT_APPLICABLE_DETAIL,
        AbsencePolicy.REDACTED: AbsenceDetailKind.REDACTION_DETAIL,
    }.get(value.absence_policy, AbsenceDetailKind.NONE)
    if value.absence_detail.kind is not expected_detail:
        raise _fail(BehaviorFailureCode.INVALID_ABSENCE_DETAIL, "absence policy/detail mismatch")
    if value.absence_policy is AbsencePolicy.DEFAULTED:
        if value.default.kind not in (DefaultKind.NULL, DefaultKind.VALUE):
            raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "DEFAULTED requires NULL or VALUE default")
        if value.default.kind is DefaultKind.NULL and value.value_type is not ValueType.NULL:
            raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "NULL default requires null value type")
        if value.default.kind is DefaultKind.VALUE and not _value_matches_type(value.default.value, value.value_type):
            raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "default value does not match field type")
    elif value.default.kind is not DefaultKind.ABSENT:
        raise _fail(BehaviorFailureCode.INVALID_DEFAULT, "non-DEFAULTED policy requires exact ABSENT default")


@dataclass(frozen=True)
class ConditionRef:
    condition_id: str
    condition_schema_id: str
    sha256: str
    byte_length: int
    media_type: str

    def __post_init__(self) -> None:
        _condition_as_ref(self)

    def to_dict(self) -> dict[str, object]:
        ref = _condition_as_ref(self)
        return {
            "condition_id": self.condition_id,
            "condition_schema_id": self.condition_schema_id,
            "sha256": ref.sha256,
            "byte_length": ref.byte_length,
            "media_type": ref.media_type,
        }

    @classmethod
    def from_dict(cls, value: object) -> ConditionRef:
        data = _exact_dict(
            value,
            ("condition_id", "condition_schema_id", "sha256", "byte_length", "media_type"),
            "condition_ref",
        )
        return cls(
            condition_id=data["condition_id"],
            condition_schema_id=data["condition_schema_id"],
            sha256=data["sha256"],
            byte_length=data["byte_length"],
            media_type=data["media_type"],
        )


def _condition_as_ref(value: ConditionRef) -> HashBoundRef:
    if type(value) is not ConditionRef:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "condition must be exact ConditionRef")
    try:
        return HashBoundRef(
            kind=RefKind.CONTRACT_CONDITION,
            ref_id=value.condition_id,
            schema_id=value.condition_schema_id,
            sha256=value.sha256,
            byte_length=value.byte_length,
            media_type=value.media_type,
        )
    except CanonicalizationViolation as exc:
        raise _fail(BehaviorFailureCode.REF_KIND_MISMATCH, "condition reference is malformed") from exc


def _normalize_fields(value: object, name: str) -> tuple[ContractField, ...]:
    if type(value) not in (list, tuple):
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} fields must be list or tuple")
    fields: list[ContractField] = []
    seen: set[str] = set()
    for item in value:
        field = item if type(item) is ContractField else ContractField.from_dict(item)
        _validate_contract_field(field)
        if field.name in seen:
            raise _fail(BehaviorFailureCode.DUPLICATE_FIELD, f"{name} has duplicate field {field.name}")
        seen.add(field.name)
        fields.append(field)
    fields.sort(key=lambda item: item.name)
    return tuple(fields)


def _normalize_conditions(value: object, name: str) -> tuple[ConditionRef, ...]:
    if type(value) not in (list, tuple):
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, f"{name} must be list or tuple")
    refs: list[ConditionRef] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for item in value:
        ref = item if type(item) is ConditionRef else ConditionRef.from_dict(item)
        _condition_as_ref(ref)
        if ref.condition_id in seen_ids or ref.sha256 in seen_hashes:
            raise _fail(BehaviorFailureCode.REF_KIND_MISMATCH, f"{name} has duplicate condition")
        seen_ids.add(ref.condition_id)
        seen_hashes.add(ref.sha256)
        refs.append(ref)
    refs.sort(key=lambda item: item.condition_id)
    return tuple(refs)


@dataclass(frozen=True)
class InputContract:
    fields: tuple[ContractField, ...]
    preconditions: tuple[ConditionRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _normalize_fields(self.fields, "input_contract"))
        object.__setattr__(self, "preconditions", _normalize_conditions(self.preconditions, "preconditions"))

    def to_dict(self) -> dict[str, object]:
        fields = _normalize_fields(self.fields, "input_contract")
        conditions = _normalize_conditions(self.preconditions, "preconditions")
        return {"fields": [field.to_dict() for field in fields], "preconditions": [ref.to_dict() for ref in conditions]}

    @classmethod
    def from_dict(cls, value: object) -> InputContract:
        data = _exact_dict(value, ("fields", "preconditions"), "input_contract")
        return cls(tuple(_exact_list(data["fields"], "input_contract.fields")), tuple(_exact_list(data["preconditions"], "input_contract.preconditions")))


@dataclass(frozen=True)
class OutputContract:
    fields: tuple[ContractField, ...]
    postconditions: tuple[ConditionRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", _normalize_fields(self.fields, "output_contract"))
        object.__setattr__(self, "postconditions", _normalize_conditions(self.postconditions, "postconditions"))

    def to_dict(self) -> dict[str, object]:
        fields = _normalize_fields(self.fields, "output_contract")
        conditions = _normalize_conditions(self.postconditions, "postconditions")
        return {"fields": [field.to_dict() for field in fields], "postconditions": [ref.to_dict() for ref in conditions]}

    @classmethod
    def from_dict(cls, value: object) -> OutputContract:
        data = _exact_dict(value, ("fields", "postconditions"), "output_contract")
        return cls(tuple(_exact_list(data["fields"], "output_contract.fields")), tuple(_exact_list(data["postconditions"], "output_contract.postconditions")))


class VerificationResultClass(str, Enum):
    CONTRACT_SATISFIED = "CONTRACT_SATISFIED"
    BEHAVIOR_REJECTED = "BEHAVIOR_REJECTED"
    OBSERVATION_MATCH = "OBSERVATION_MATCH"


@dataclass(frozen=True)
class VerificationContract:
    profile_id: str
    expected_result_class: VerificationResultClass
    expected_claims: tuple[str, ...]
    evidence_requirements: tuple[HashBoundRef, ...]
    oracle_requirements: tuple[HashBoundRef, ...]

    def __post_init__(self) -> None:
        _validate_verification_contract(self)

    def to_dict(self) -> dict[str, object]:
        claims, evidence, oracles = _validate_verification_contract(self)
        return {
            "profile_id": self.profile_id,
            "expected_result_class": self.expected_result_class.value,
            "expected_claims": list(claims),
            "evidence_requirements": [ref.to_dict() for ref in evidence],
            "oracle_requirements": [ref.to_dict() for ref in oracles],
        }

    @classmethod
    def from_dict(cls, value: object) -> VerificationContract:
        data = _exact_dict(
            value,
            ("profile_id", "expected_result_class", "expected_claims", "evidence_requirements", "oracle_requirements"),
            "verification_contract",
        )
        result_class = _enum(data["expected_result_class"], VerificationResultClass, BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification result class")
        assert isinstance(result_class, VerificationResultClass)
        return cls(
            profile_id=data["profile_id"],
            expected_result_class=result_class,
            expected_claims=tuple(_exact_list(data["expected_claims"], "verification.expected_claims")),
            evidence_requirements=tuple(_exact_list(data["evidence_requirements"], "verification.evidence_requirements")),
            oracle_requirements=tuple(_exact_list(data["oracle_requirements"], "verification.oracle_requirements")),
        )


def _validate_verification_contract(value: VerificationContract) -> tuple[tuple[str, ...], tuple[HashBoundRef, ...], tuple[HashBoundRef, ...]]:
    if type(value) is not VerificationContract:
        raise _fail(BehaviorFailureCode.MISSING_VERIFICATION_CONTRACT, "verification contract must be exact typed value")
    _versioned(value.profile_id, "verification.profile_id")
    if type(value.expected_result_class) is not VerificationResultClass:
        raise _fail(BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification result class is unknown")
    if type(value.expected_claims) is not tuple or not value.expected_claims:
        raise _fail(BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification expected claims must be non-empty tuple")
    claims: list[str] = []
    seen: set[str] = set()
    for claim in value.expected_claims:
        _text(claim, "verification claim", _CLAIM_RE)
        if claim in seen:
            raise _fail(BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification claims contain duplicate")
        seen.add(claim)
        claims.append(claim)
    claims.sort()
    try:
        evidence = validate_ref_collection(value.evidence_requirements, expected_kind=RefKind.SOURCE_EVIDENCE, field_name="verification evidence")
        oracles = validate_ref_collection(value.oracle_requirements, expected_kind=RefKind.ARTIFACT, field_name="verification oracles")
    except CanonicalizationViolation as exc:
        raise _fail(BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification reference requirement is invalid") from exc
    if not evidence and not oracles:
        raise _fail(BehaviorFailureCode.INVALID_VERIFICATION_CONTRACT, "verification contract cannot be empty")
    return tuple(claims), evidence, oracles


class ReplayResultClass(str, Enum):
    MATCH = "MATCH"
    REJECTED = "REJECTED"
    DIVERGED = "DIVERGED"


@dataclass(frozen=True)
class ReplayContract:
    profile_id: str
    expected_transition_ids: tuple[str, ...]
    expected_observation_ids: tuple[str, ...]
    expected_activity_ids: tuple[str, ...]
    allowed_result_classes: tuple[ReplayResultClass, ...]

    def __post_init__(self) -> None:
        _validate_replay_contract(self)

    def to_dict(self) -> dict[str, object]:
        transitions, observations, activities, results = _validate_replay_contract(self)
        return {
            "profile_id": self.profile_id,
            "expected_transition_ids": list(transitions),
            "expected_observation_ids": list(observations),
            "expected_activity_ids": list(activities),
            "allowed_result_classes": [item.value for item in results],
        }

    @classmethod
    def from_dict(cls, value: object) -> ReplayContract:
        data = _exact_dict(
            value,
            ("profile_id", "expected_transition_ids", "expected_observation_ids", "expected_activity_ids", "allowed_result_classes"),
            "replay_contract",
        )
        results: list[ReplayResultClass] = []
        for item in _exact_list(data["allowed_result_classes"], "replay.allowed_result_classes"):
            parsed = _enum(item, ReplayResultClass, BehaviorFailureCode.INVALID_REPLAY_CONTRACT, "replay result class")
            assert isinstance(parsed, ReplayResultClass)
            results.append(parsed)
        return cls(
            profile_id=data["profile_id"],
            expected_transition_ids=tuple(_exact_list(data["expected_transition_ids"], "replay.expected_transition_ids")),
            expected_observation_ids=tuple(_exact_list(data["expected_observation_ids"], "replay.expected_observation_ids")),
            expected_activity_ids=tuple(_exact_list(data["expected_activity_ids"], "replay.expected_activity_ids")),
            allowed_result_classes=tuple(results),
        )


def _normalize_id_tuple(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(BehaviorFailureCode.INVALID_REPLAY_CONTRACT, f"{name} must be tuple")
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        _text(item, name, _CLAIM_RE)
        if item in seen:
            raise _fail(BehaviorFailureCode.INVALID_REPLAY_CONTRACT, f"{name} contains duplicate")
        seen.add(item)
        items.append(item)
    items.sort()
    return tuple(items)


def _validate_replay_contract(value: ReplayContract) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[ReplayResultClass, ...]]:
    if type(value) is not ReplayContract:
        raise _fail(BehaviorFailureCode.INVALID_REPLAY_CONTRACT, "replay contract must be exact typed value")
    _versioned(value.profile_id, "replay.profile_id")
    transitions = _normalize_id_tuple(value.expected_transition_ids, "replay transition")
    observations = _normalize_id_tuple(value.expected_observation_ids, "replay observation")
    activities = _normalize_id_tuple(value.expected_activity_ids, "replay activity")
    if type(value.allowed_result_classes) is not tuple or not value.allowed_result_classes:
        raise _fail(BehaviorFailureCode.INVALID_REPLAY_CONTRACT, "replay result classes must be non-empty tuple")
    if any(type(item) is not ReplayResultClass for item in value.allowed_result_classes) or len(set(value.allowed_result_classes)) != len(value.allowed_result_classes):
        raise _fail(BehaviorFailureCode.INVALID_REPLAY_CONTRACT, "replay result classes are invalid or duplicate")
    results = tuple(sorted(value.allowed_result_classes, key=lambda item: item.value))
    return transitions, observations, activities, results


@dataclass(frozen=True)
class InlineProgram:
    ir_bytes: bytes

    def __post_init__(self) -> None:
        decode_canonical_program_ir(self.ir_bytes)

    def to_dict(self) -> dict[str, object]:
        return {"form": ProgramForm.INLINE_IR_V1.value, "ir": decode_canonical_program_ir(self.ir_bytes)}

    @classmethod
    def from_dict(cls, value: object) -> InlineProgram:
        normalized, ir_bytes, artifact = _normalize_canonical_program(value)
        if normalized["form"] != ProgramForm.INLINE_IR_V1.value or type(ir_bytes) is not bytes or artifact is not None:
            raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "program is not inline IR v1")
        return cls(ir_bytes)


@dataclass(frozen=True)
class ArtifactProgram:
    artifact_ref: HashBoundRef

    def __post_init__(self) -> None:
        normalized, ir_bytes, artifact = _normalize_canonical_program(
            {"form": ProgramForm.ARTIFACT_REF_V1.value, "artifact_ref": self.artifact_ref.to_dict()}
        )
        if normalized["form"] != ProgramForm.ARTIFACT_REF_V1.value or ir_bytes is not None or artifact != self.artifact_ref:
            raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "program artifact reference is invalid")

    def to_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {"form": ProgramForm.ARTIFACT_REF_V1.value, "artifact_ref": self.artifact_ref.to_dict()}

    @classmethod
    def from_dict(cls, value: object) -> ArtifactProgram:
        normalized, ir_bytes, artifact = _normalize_canonical_program(value)
        if normalized["form"] != ProgramForm.ARTIFACT_REF_V1.value or ir_bytes is not None or type(artifact) is not HashBoundRef:
            raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "program is not artifact ref v1")
        return cls(artifact)


def _program_from_dict(value: object) -> InlineProgram | ArtifactProgram:
    if type(value) is not dict or type(value.get("form")) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "canonical program must be exact tagged dict")
    if value["form"] == ProgramForm.INLINE_IR_V1.value:
        return InlineProgram.from_dict(value)
    if value["form"] == ProgramForm.ARTIFACT_REF_V1.value:
        return ArtifactProgram.from_dict(value)
    raise _fail(BehaviorFailureCode.INVALID_GRANULARITY, "unknown or implicit-merge program form")


def _normalize_capabilities(value: object) -> tuple[str, ...]:
    if type(value) not in (tuple, list):
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "capability requirements must be tuple or list")
    capabilities: list[str] = []
    seen: set[str] = set()
    for capability in value:
        if type(capability) is not str or not capability or any(char in capability for char in "*?[]"):
            raise _fail(BehaviorFailureCode.CAPABILITY_WILDCARD, "capability wildcard/empty value is forbidden")
        if _CAPABILITY_RE.fullmatch(capability) is None:
            raise _fail(BehaviorFailureCode.CAPABILITY_WILDCARD, "capability is not an exact identifier")
        if capability in seen:
            raise _fail(BehaviorFailureCode.DUPLICATE_CAPABILITY, "duplicate capability requirement")
        seen.add(capability)
        capabilities.append(capability)
    capabilities.sort()
    return tuple(capabilities)


def _refs(value: object, kind: RefKind, name: str) -> tuple[HashBoundRef, ...]:
    try:
        return validate_ref_collection(value, expected_kind=kind, field_name=name)
    except CanonicalizationViolation as exc:
        raise _fail(BehaviorFailureCode.REF_KIND_MISMATCH, f"{name} contains invalid or substituted ref") from exc


@dataclass(frozen=True, init=False)
class BehaviorCore:
    schema_version: str
    behavior_kind: BehaviorKind
    language_version: str
    canonical_program: InlineProgram | ArtifactProgram
    input_contract: InputContract
    output_contract: OutputContract
    capability_requirements: tuple[str, ...]
    replay_contract: ReplayContract
    verification_contract: VerificationContract
    binding_refs: tuple[HashBoundRef, ...]
    source_evidence_refs: tuple[HashBoundRef, ...]
    artifact_refs: tuple[HashBoundRef, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorCore:
        raise TypeError("BehaviorCore is created only by create_behavior_unit or strict transport")

    def to_dict(self) -> dict[str, object]:
        return _behavior_core_payload(self)

    @classmethod
    def from_dict(cls, value: object) -> BehaviorCore:
        return _behavior_core_from_dict(value)


def _make_behavior_core(
    *,
    behavior_kind: BehaviorKind,
    canonical_program: InlineProgram | ArtifactProgram,
    input_contract: InputContract,
    output_contract: OutputContract,
    capability_requirements: object,
    replay_contract: ReplayContract,
    verification_contract: VerificationContract,
    binding_refs: object,
    source_evidence_refs: object,
    artifact_refs: object,
) -> BehaviorCore:
    core = object.__new__(BehaviorCore)
    object.__setattr__(core, "schema_version", BEHAVIOR_CORE_SCHEMA_V1)
    object.__setattr__(core, "behavior_kind", behavior_kind)
    object.__setattr__(core, "language_version", LANGUAGE_VERSION)
    object.__setattr__(core, "canonical_program", canonical_program)
    object.__setattr__(core, "input_contract", input_contract)
    object.__setattr__(core, "output_contract", output_contract)
    object.__setattr__(core, "capability_requirements", _normalize_capabilities(capability_requirements))
    object.__setattr__(core, "replay_contract", replay_contract)
    object.__setattr__(core, "verification_contract", verification_contract)
    object.__setattr__(core, "binding_refs", _refs(binding_refs, RefKind.BINDING, "binding_refs"))
    object.__setattr__(core, "source_evidence_refs", _refs(source_evidence_refs, RefKind.SOURCE_EVIDENCE, "source_evidence_refs"))
    object.__setattr__(core, "artifact_refs", _refs(artifact_refs, RefKind.ARTIFACT, "artifact_refs"))
    object.__setattr__(core, "_trusted_seal", _TRUSTED_SEAL)
    _validate_behavior_core(core)
    return core


def _behavior_core_payload(value: BehaviorCore) -> dict[str, object]:
    _validate_behavior_core(value)
    return {
        "schema_version": value.schema_version,
        "behavior_kind": value.behavior_kind.value,
        "language_version": value.language_version,
        "canonical_program": value.canonical_program.to_dict(),
        "input_contract": value.input_contract.to_dict(),
        "output_contract": value.output_contract.to_dict(),
        "capability_requirements": list(value.capability_requirements),
        "replay_contract": value.replay_contract.to_dict(),
        "verification_contract": value.verification_contract.to_dict(),
        "binding_refs": [ref.to_dict() for ref in value.binding_refs],
        "source_evidence_refs": [ref.to_dict() for ref in value.source_evidence_refs],
        "artifact_refs": [ref.to_dict() for ref in value.artifact_refs],
    }


def _validate_behavior_core(value: BehaviorCore) -> None:
    if type(value) is not BehaviorCore:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "behavior core must be exact BehaviorCore")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "behavior core is not factory sealed")
    if value.schema_version != BEHAVIOR_CORE_SCHEMA_V1 or type(value.schema_version) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "behavior core schema is unknown")
    if type(value.behavior_kind) is not BehaviorKind:
        raise _fail(BehaviorFailureCode.UNKNOWN_BEHAVIOR_KIND, "behavior kind is unknown")
    if value.language_version != LANGUAGE_VERSION or type(value.language_version) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "behavior language version is unknown")
    if type(value.canonical_program) is InlineProgram:
        value.canonical_program.to_dict()
        if _normalize_capabilities(value.capability_requirements):
            raise _fail(BehaviorFailureCode.CAPABILITY_MISMATCH, "pure inline IR derives an empty capability set")
    elif type(value.canonical_program) is ArtifactProgram:
        value.canonical_program.to_dict()
        _normalize_capabilities(value.capability_requirements)
    else:
        raise _fail(BehaviorFailureCode.INVALID_GRANULARITY, "behavior must have exactly one canonical program")
    if type(value.input_contract) is not InputContract or type(value.output_contract) is not OutputContract:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "input/output contracts must be exact typed values")
    value.input_contract.to_dict()
    value.output_contract.to_dict()
    _validate_replay_contract(value.replay_contract)
    _validate_verification_contract(value.verification_contract)
    if value.verification_contract is None:
        raise _fail(BehaviorFailureCode.MISSING_VERIFICATION_CONTRACT, "verification contract is mandatory")
    _refs(value.binding_refs, RefKind.BINDING, "binding_refs")
    _refs(value.source_evidence_refs, RefKind.SOURCE_EVIDENCE, "source_evidence_refs")
    _refs(value.artifact_refs, RefKind.ARTIFACT, "artifact_refs")
    if value.behavior_kind is BehaviorKind.REJECTED_HYPOTHESIS_GUARD:
        # Its exact enum survives every transport path; there is no fact/recipe alias.
        if value.behavior_kind.value != "rejected_hypothesis_guard":
            raise _fail(BehaviorFailureCode.FAILED_HYPOTHESIS_RELABEL, "rejected hypothesis was relabeled")


def _behavior_core_from_dict(value: object) -> BehaviorCore:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "behavior_kind",
            "language_version",
            "canonical_program",
            "input_contract",
            "output_contract",
            "capability_requirements",
            "replay_contract",
            "verification_contract",
            "binding_refs",
            "source_evidence_refs",
            "artifact_refs",
        ),
        "behavior_core",
    )
    if data["schema_version"] != BEHAVIOR_CORE_SCHEMA_V1 or type(data["schema_version"]) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "behavior core schema is unknown")
    if data["language_version"] != LANGUAGE_VERSION or type(data["language_version"]) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "behavior language version is unknown")
    kind = _enum(data["behavior_kind"], BehaviorKind, BehaviorFailureCode.UNKNOWN_BEHAVIOR_KIND, "behavior_kind")
    assert isinstance(kind, BehaviorKind)
    if data["verification_contract"] is None:
        raise _fail(BehaviorFailureCode.MISSING_VERIFICATION_CONTRACT, "verification contract is mandatory")
    return _make_behavior_core(
        behavior_kind=kind,
        canonical_program=_program_from_dict(data["canonical_program"]),
        input_contract=InputContract.from_dict(data["input_contract"]),
        output_contract=OutputContract.from_dict(data["output_contract"]),
        capability_requirements=_exact_list(data["capability_requirements"], "capability_requirements"),
        replay_contract=ReplayContract.from_dict(data["replay_contract"]),
        verification_contract=VerificationContract.from_dict(data["verification_contract"]),
        binding_refs=_exact_list(data["binding_refs"], "binding_refs"),
        source_evidence_refs=_exact_list(data["source_evidence_refs"], "source_evidence_refs"),
        artifact_refs=_exact_list(data["artifact_refs"], "artifact_refs"),
    )


@dataclass(frozen=True, init=False)
class SynapseBehaviorUnit:
    schema_version: SchemaVersion
    core: BehaviorCore
    canonical_core: CanonicalBehaviorCore
    content_key: ContentKey
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SynapseBehaviorUnit:
        raise TypeError("SynapseBehaviorUnit is created only by create_behavior_unit or strict transport")

    def to_dict(self) -> dict[str, object]:
        validate_behavior_unit(self)
        return {
            "schema_version": self.schema_version.value,
            "core": self.core.to_dict(),
            "content_key": self.content_key.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> SynapseBehaviorUnit:
        return behavior_unit_from_dict(value)


def _seal_unit(core: BehaviorCore) -> SynapseBehaviorUnit:
    payload = _behavior_core_payload(core)
    canonical_core = _make_canonical_behavior_core(payload)
    unit = object.__new__(SynapseBehaviorUnit)
    object.__setattr__(unit, "schema_version", SchemaVersion.BEHAVIOR_UNIT_V1)
    object.__setattr__(unit, "core", core)
    object.__setattr__(unit, "canonical_core", canonical_core)
    object.__setattr__(unit, "content_key", canonical_core.content_key)
    object.__setattr__(unit, "_trusted_seal", _TRUSTED_SEAL)
    validate_behavior_unit(unit)
    return unit


def create_behavior_unit(
    *,
    behavior_kind: BehaviorKind,
    canonical_program: InlineProgram | ArtifactProgram | dict[str, object],
    input_contract: InputContract,
    output_contract: OutputContract,
    capability_requirements: tuple[str, ...] | list[str],
    replay_contract: ReplayContract,
    verification_contract: VerificationContract,
    binding_refs: tuple[HashBoundRef, ...] | list[HashBoundRef],
    source_evidence_refs: tuple[HashBoundRef, ...] | list[HashBoundRef],
    artifact_refs: tuple[HashBoundRef, ...] | list[HashBoundRef],
) -> SynapseBehaviorUnit:
    if type(behavior_kind) is not BehaviorKind:
        raise _fail(BehaviorFailureCode.UNKNOWN_BEHAVIOR_KIND, "behavior kind must be exact BehaviorKind")
    program = canonical_program if type(canonical_program) in (InlineProgram, ArtifactProgram) else _program_from_dict(canonical_program)
    core = _make_behavior_core(
        behavior_kind=behavior_kind,
        canonical_program=program,
        input_contract=input_contract,
        output_contract=output_contract,
        capability_requirements=capability_requirements,
        replay_contract=replay_contract,
        verification_contract=verification_contract,
        binding_refs=binding_refs,
        source_evidence_refs=source_evidence_refs,
        artifact_refs=artifact_refs,
    )
    return _seal_unit(core)


def validate_behavior_unit(value: SynapseBehaviorUnit) -> None:
    if type(value) is not SynapseBehaviorUnit:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "unit must be exact SynapseBehaviorUnit")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "unit is not factory sealed")
    if value.schema_version is not SchemaVersion.BEHAVIOR_UNIT_V1:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "unit schema is unknown")
    _validate_behavior_core(value.core)
    validate_canonical_behavior_core(value.canonical_core)
    expected = _make_canonical_behavior_core(_behavior_core_payload(value.core))
    if (
        value.canonical_core.canonical_bytes != expected.canonical_bytes
        or value.canonical_core.payload_sha256 != expected.payload_sha256
        or value.canonical_core.content_key.value != expected.content_key.value
    ):
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "unit core and canonical bundle mismatch")
    if type(value.content_key) is not ContentKey or value.content_key.value != expected.content_key.value:
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "unit content key mismatch")


def behavior_unit_from_dict(value: object) -> SynapseBehaviorUnit:
    data = _exact_dict(value, ("schema_version", "core", "content_key"), "behavior_unit")
    if data["schema_version"] != SchemaVersion.BEHAVIOR_UNIT_V1.value or type(data["schema_version"]) is not str:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "unit schema is unknown")
    unit = _seal_unit(_behavior_core_from_dict(data["core"]))
    if data["content_key"] != unit.content_key.to_dict():
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "claimed unit content key mismatch")
    return unit


def _unit_context_sha256(value: SynapseBehaviorUnit) -> str:
    validate_behavior_unit(value)
    payload = {
        "schema_version": value.schema_version.value,
        "core": value.core.to_dict(),
        "content_key": value.content_key.value,
    }
    encoded = canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, init=False)
class BehaviorBlob:
    schema_version: str
    canonical_core_bytes: bytes
    payload_sha256: str
    content_key: ContentKey
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorBlob:
        raise TypeError("BehaviorBlob is created only from a complete validated Behavior Unit")

    def to_dict(self, *, unit: SynapseBehaviorUnit) -> dict[str, object]:
        validate_behavior_blob(self, unit=unit)
        return {
            "schema_version": self.schema_version,
            "canonical_core_base64url": canonical_base64url(self.canonical_core_bytes),
            "payload_sha256": self.payload_sha256,
            "content_key": self.content_key.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object, *, unit: SynapseBehaviorUnit) -> BehaviorBlob:
        return behavior_blob_from_dict(value, unit=unit)


def create_behavior_blob(unit: SynapseBehaviorUnit) -> BehaviorBlob:
    validate_behavior_unit(unit)
    blob = object.__new__(BehaviorBlob)
    object.__setattr__(blob, "schema_version", BEHAVIOR_BLOB_SCHEMA_V1)
    object.__setattr__(blob, "canonical_core_bytes", unit.canonical_core.canonical_bytes)
    object.__setattr__(blob, "payload_sha256", unit.canonical_core.payload_sha256)
    object.__setattr__(blob, "content_key", unit.content_key)
    object.__setattr__(blob, "_trusted_seal", _TRUSTED_SEAL)
    validate_behavior_blob(blob, unit=unit)
    return blob


def validate_behavior_blob(value: BehaviorBlob, *, unit: SynapseBehaviorUnit) -> None:
    validate_behavior_unit(unit)
    if type(value) is not BehaviorBlob:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "blob must be exact BehaviorBlob")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "blob is not factory sealed")
    if value.schema_version != BEHAVIOR_BLOB_SCHEMA_V1 or type(value.schema_version) is not str:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob schema is unknown")
    if type(value.canonical_core_bytes) is not bytes:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob core bytes must be exact bytes")
    actual_hash = hashlib.sha256(value.canonical_core_bytes).hexdigest()
    if type(value.payload_sha256) is not str or _SHA256_RE.fullmatch(value.payload_sha256) is None or value.payload_sha256 != actual_hash:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob payload hash mismatch")
    if type(value.content_key) is not ContentKey:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob content key type mismatch")
    if (
        value.canonical_core_bytes != unit.canonical_core.canonical_bytes
        or value.payload_sha256 != unit.canonical_core.payload_sha256
        or value.content_key.value != unit.content_key.value
    ):
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob bytes/hash/key do not match Unit")


def behavior_blob_from_dict(value: object, *, unit: SynapseBehaviorUnit) -> BehaviorBlob:
    data = _exact_dict(
        value,
        ("schema_version", "canonical_core_base64url", "payload_sha256", "content_key"),
        "behavior_blob",
    )
    if data["schema_version"] != BEHAVIOR_BLOB_SCHEMA_V1 or type(data["schema_version"]) is not str:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob schema is unknown")
    raw = decode_canonical_base64url(data["canonical_core_base64url"])
    validate_behavior_unit(unit)
    if raw != unit.canonical_core.canonical_bytes:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob substitution detected")
    blob = create_behavior_blob(unit)
    if data["payload_sha256"] != blob.payload_sha256 or type(data["payload_sha256"]) is not str:
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob payload hash mismatch")
    if data["content_key"] != blob.content_key.to_dict():
        raise _fail(BehaviorFailureCode.BLOB_MISMATCH, "blob claimed key mismatch")
    return blob


def compile_behavior_unit(unit: SynapseBehaviorUnit) -> CompilerBinding:
    """Compile one full, recursively revalidated Unit without executing it."""

    validate_behavior_unit(unit)
    if unit.core.capability_requirements:
        raise _fail(BehaviorFailureCode.CAPABILITY_MISMATCH, "declared capabilities differ from pure IR derived empty set")
    evidence = _compile_validated_behavior_core(unit.canonical_core)
    return _bind_compiler_evidence(evidence, unit_context_sha256=_unit_context_sha256(unit))


def validate_compiler_binding_for_unit(unit: SynapseBehaviorUnit, binding: CompilerBinding) -> None:
    validate_behavior_unit(unit)
    if unit.core.capability_requirements:
        raise _fail(BehaviorFailureCode.CAPABILITY_MISMATCH, "declared capabilities differ from derived empty set")
    _validate_compiler_binding(
        binding,
        core=unit.canonical_core,
        unit_context_sha256=_unit_context_sha256(unit),
    )


def compiler_binding_to_dict_for_unit(unit: SynapseBehaviorUnit, binding: CompilerBinding) -> dict[str, object]:
    validate_compiler_binding_for_unit(unit, binding)
    return _compiler_binding_to_dict(
        binding,
        core=unit.canonical_core,
        unit_context_sha256=_unit_context_sha256(unit),
    )


def compiler_binding_from_dict_for_unit(unit: SynapseBehaviorUnit, value: object) -> CompilerBinding:
    validate_behavior_unit(unit)
    binding = _compiler_binding_from_dict(
        value,
        core=unit.canonical_core,
        unit_context_sha256=_unit_context_sha256(unit),
    )
    validate_compiler_binding_for_unit(unit, binding)
    return binding


def _manifest_identity_payload(
    *,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    compiler_binding: CompilerBinding | None,
) -> dict[str, object]:
    validate_behavior_unit(unit)
    validate_behavior_blob(blob, unit=unit)
    binding_descriptor: dict[str, object] | None = None
    if compiler_binding is not None:
        validate_compiler_binding_for_unit(unit, compiler_binding)
        binding_descriptor = {
            "binding_id": compiler_binding.binding_id.to_dict(),
            "actual_program_hash": compiler_binding.actual_program_hash,
        }
    return {
        "schema_version": SchemaVersion.BEHAVIOR_MANIFEST_V1.value,
        "behavior_kind": unit.core.behavior_kind.value,
        "content_key": unit.content_key.value,
        "blob_schema_version": blob.schema_version,
        "blob_payload_sha256": blob.payload_sha256,
        "compiler_binding": binding_descriptor,
        "binding_refs": [ref.to_dict() for ref in unit.core.binding_refs],
        "source_evidence_refs": [ref.to_dict() for ref in unit.core.source_evidence_refs],
        "artifact_refs": [ref.to_dict() for ref in unit.core.artifact_refs],
    }


@dataclass(frozen=True, init=False)
class BehaviorManifest:
    schema_version: SchemaVersion
    behavior_kind: BehaviorKind
    content_key: ContentKey
    blob_schema_version: str
    blob_payload_sha256: str
    compiler_binding: CompilerBinding | None
    binding_refs: tuple[HashBoundRef, ...]
    source_evidence_refs: tuple[HashBoundRef, ...]
    artifact_refs: tuple[HashBoundRef, ...]
    manifest_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> BehaviorManifest:
        raise TypeError("BehaviorManifest is created only from validated Unit/Blob/binding")

    def to_dict(self, *, unit: SynapseBehaviorUnit, blob: BehaviorBlob) -> dict[str, object]:
        _validate_manifest(self, unit=unit, blob=blob)
        return {**_manifest_identity_payload(unit=unit, blob=blob, compiler_binding=self.compiler_binding), "manifest_id": self.manifest_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        unit: SynapseBehaviorUnit,
        blob: BehaviorBlob,
        compiler_binding: CompilerBinding | None,
    ) -> BehaviorManifest:
        return behavior_manifest_from_dict(value, unit=unit, blob=blob, compiler_binding=compiler_binding)


def create_behavior_manifest(
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    *,
    compiler_binding: CompilerBinding | None,
) -> BehaviorManifest:
    payload = _manifest_identity_payload(unit=unit, blob=blob, compiler_binding=compiler_binding)
    identity_bytes = canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    manifest = object.__new__(BehaviorManifest)
    object.__setattr__(manifest, "schema_version", SchemaVersion.BEHAVIOR_MANIFEST_V1)
    object.__setattr__(manifest, "behavior_kind", unit.core.behavior_kind)
    object.__setattr__(manifest, "content_key", unit.content_key)
    object.__setattr__(manifest, "blob_schema_version", blob.schema_version)
    object.__setattr__(manifest, "blob_payload_sha256", blob.payload_sha256)
    object.__setattr__(manifest, "compiler_binding", compiler_binding)
    object.__setattr__(manifest, "binding_refs", unit.core.binding_refs)
    object.__setattr__(manifest, "source_evidence_refs", unit.core.source_evidence_refs)
    object.__setattr__(manifest, "artifact_refs", unit.core.artifact_refs)
    object.__setattr__(manifest, "manifest_id", compute_record_id(domain=IdentityDomain.BEHAVIOR_MANIFEST, canonical_bytes=identity_bytes))
    object.__setattr__(manifest, "_trusted_seal", _TRUSTED_SEAL)
    _validate_manifest(manifest, unit=unit, blob=blob)
    return manifest


def _validate_manifest(value: BehaviorManifest, *, unit: SynapseBehaviorUnit, blob: BehaviorBlob) -> None:
    validate_behavior_unit(unit)
    validate_behavior_blob(blob, unit=unit)
    if type(value) is not BehaviorManifest:
        raise _fail(BehaviorFailureCode.TYPE_MISMATCH, "manifest must be exact BehaviorManifest")
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(BehaviorFailureCode.TRUSTED_OBJECT_FORGED, "manifest is not factory sealed")
    if value.schema_version is not SchemaVersion.BEHAVIOR_MANIFEST_V1:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest schema is unknown")
    if value.behavior_kind is not unit.core.behavior_kind:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest behavior kind mismatch")
    if value.behavior_kind is BehaviorKind.REJECTED_HYPOTHESIS_GUARD and value.behavior_kind.value != "rejected_hypothesis_guard":
        raise _fail(BehaviorFailureCode.FAILED_HYPOTHESIS_RELABEL, "rejected hypothesis was relabeled")
    if value.content_key.value != unit.content_key.value:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest content key mismatch")
    if value.blob_schema_version != blob.schema_version or value.blob_payload_sha256 != blob.payload_sha256:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest blob descriptor mismatch")
    if value.compiler_binding is not None:
        validate_compiler_binding_for_unit(unit, value.compiler_binding)
    if value.binding_refs != _refs(unit.core.binding_refs, RefKind.BINDING, "binding_refs"):
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest binding refs mismatch")
    if value.source_evidence_refs != _refs(unit.core.source_evidence_refs, RefKind.SOURCE_EVIDENCE, "source_evidence_refs"):
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest source refs mismatch")
    if value.artifact_refs != _refs(unit.core.artifact_refs, RefKind.ARTIFACT, "artifact_refs"):
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest artifact refs mismatch")
    payload = _manifest_identity_payload(unit=unit, blob=blob, compiler_binding=value.compiler_binding)
    identity_bytes = canonicalize_stage4_payload(
        payload,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    if type(value.manifest_id) is not RecordId or value.manifest_id.domain is not IdentityDomain.BEHAVIOR_MANIFEST:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest identity domain mismatch")
    try:
        validate_record_id(value.manifest_id, canonical_bytes=identity_bytes)
    except ValueError as exc:
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest identity mismatch") from exc


def behavior_manifest_from_dict(
    value: object,
    *,
    unit: SynapseBehaviorUnit,
    blob: BehaviorBlob,
    compiler_binding: CompilerBinding | None,
) -> BehaviorManifest:
    data = _exact_dict(
        value,
        (
            "schema_version",
            "behavior_kind",
            "content_key",
            "blob_schema_version",
            "blob_payload_sha256",
            "compiler_binding",
            "binding_refs",
            "source_evidence_refs",
            "artifact_refs",
            "manifest_id",
        ),
        "behavior_manifest",
    )
    expected = create_behavior_manifest(unit, blob, compiler_binding=compiler_binding)
    expected_payload = expected.to_dict(unit=unit, blob=blob)
    if data != expected_payload:
        payload_without_id = _manifest_identity_payload(unit=unit, blob=blob, compiler_binding=compiler_binding)
        identity_bytes = canonicalize_stage4_payload(
            payload_without_id,
            profile_id=STAGE4_CANONICAL_PROFILE_V1,
            codec_id=STABLE_CANONICAL_CODEC_ID,
        )
        try:
            record_id_from_dict(data["manifest_id"], canonical_bytes=identity_bytes)
        except ValueError as exc:
            raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest transport mismatch") from exc
        raise _fail(BehaviorFailureCode.MANIFEST_MISMATCH, "manifest fields mismatch")
    return expected


__all__ = [
    "AbsenceDetail",
    "AbsenceDetailKind",
    "AbsencePolicy",
    "AbsenceReasonCode",
    "ArtifactProgram",
    "BehaviorBlob",
    "BehaviorCore",
    "BehaviorFailureCode",
    "BehaviorKind",
    "BehaviorManifest",
    "BehaviorViolation",
    "ConditionRef",
    "ContractField",
    "DefaultKind",
    "DefaultValue",
    "EvaluatorDescriptor",
    "InlineProgram",
    "InputContract",
    "OutputContract",
    "ReplayContract",
    "ReplayResultClass",
    "SynapseBehaviorUnit",
    "ValueType",
    "VerificationContract",
    "VerificationResultClass",
    "behavior_blob_from_dict",
    "behavior_manifest_from_dict",
    "behavior_unit_from_dict",
    "compile_behavior_unit",
    "compiler_binding_from_dict_for_unit",
    "compiler_binding_to_dict_for_unit",
    "create_behavior_blob",
    "create_behavior_manifest",
    "create_behavior_unit",
    "validate_behavior_blob",
    "validate_behavior_unit",
    "validate_compiler_binding_for_unit",
]
