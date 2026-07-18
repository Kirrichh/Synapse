"""Monotone source-taint contracts and independent relaxation authority.

Source classification and derivation only retain or add taint.  Removing a
class requires a proposal, per-class verification evidence, and an independent
authority decision bound to the proposal.  Success, summarisation, hashing,
distillation, or reformatting never remove taint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from pathlib import Path
import re
from typing import Callable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
    decode_stage4_canonical_bytes,
)
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    AuthorityIdentity,
    AuthorityRole,
    ContractViolation,
    HistoryAnchor,
    HistoryDomain,
    IdentityDomain,
    IndependenceProof,
    ProposalId,
    RecordId,
    ReasonCode,
    SchemaVersion,
    Stage4AuthorityHandle,
    authority_decision_id_from_dict,
    compute_authority_decision_id,
    compute_proposal_id,
    compute_record_id,
    create_history_anchor,
    create_independence_proof,
    independence_proof_from_dict,
    record_id_from_dict,
    require_stage4_authority_handle,
    validate_history_anchor_extension,
    validate_independence_proof,
    validate_record_id,
)
from .persistence import ExclusiveStoreLock, append_journal_payload, ensure_directory, initialize_journal, scan_journal


TAINT_AUTHORITY_PROPOSAL_V1 = "synapse.stage4.gold.taint-authority-proposal/v1"
TAINT_REMOVAL_VERIFICATION_V1 = "synapse.stage4.gold.taint-removal-verification/v1"
TAINT_PROFILE_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.taint-profile+json"
_AUTHORITY_ID_PREFIX = IdentityDomain.AUTHORITY_DECISION.value + ":"
_TRUSTED_SEAL = object()
_TRUSTED_CLASSIFIER_SEAL = object()
_TRUSTED_EVALUATOR_SEAL = object()
_TRUSTED_STORE_SEAL = object()
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VERSION_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z")
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
TAINT_HISTORY_JOURNAL_NAME_V1 = "taint-history-v1.journal"
TAINT_HISTORY_LOCK_NAME_V1 = "taint-history-v1.lock"


class TaintFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_TAINT_CLASS = "UNKNOWN_TAINT_CLASS"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    DUPLICATE_TAINT_CLASS = "DUPLICATE_TAINT_CLASS"
    DUPLICATE_ACTOR = "DUPLICATE_ACTOR"
    CLASSIFIER_NOT_INDEPENDENT = "CLASSIFIER_NOT_INDEPENDENT"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TAINT_REMOVAL_FORBIDDEN = "TAINT_REMOVAL_FORBIDDEN"
    DERIVATION_SOURCE_MISMATCH = "DERIVATION_SOURCE_MISMATCH"
    PROPOSAL_MISMATCH = "PROPOSAL_MISMATCH"
    AUTHORITY_NOT_INDEPENDENT = "AUTHORITY_NOT_INDEPENDENT"
    DECISION_KIND_MISMATCH = "DECISION_KIND_MISMATCH"
    REMOVAL_EVIDENCE_MISSING = "REMOVAL_EVIDENCE_MISSING"
    REMOVAL_EVIDENCE_DUPLICATE = "REMOVAL_EVIDENCE_DUPLICATE"
    DECISION_CHAIN_MISMATCH = "DECISION_CHAIN_MISMATCH"
    REF_KIND_MISMATCH = "REF_KIND_MISMATCH"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    HISTORY_ANCHOR_REQUIRED = "HISTORY_ANCHOR_REQUIRED"
    HISTORY_ROLLBACK = "HISTORY_ROLLBACK"
    DERIVATION_CHAIN_INCOMPLETE = "DERIVATION_CHAIN_INCOMPLETE"
    STICKY_QUARANTINE = "STICKY_QUARANTINE"
    AUTHORITY_HISTORY_FORK = "AUTHORITY_HISTORY_FORK"
    JOURNAL_CORRUPT = "JOURNAL_CORRUPT"


class TaintViolation(ValueError):
    def __init__(self, failure_code: TaintFailureCode, detail: str) -> None:
        if type(failure_code) is not TaintFailureCode:
            raise TypeError("failure_code must be exact TaintFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: TaintFailureCode, detail: str) -> TaintViolation:
    return TaintViolation(code, detail)


class TaintClass(str, Enum):
    TRUSTED_PLATFORM_DERIVED = "TRUSTED_PLATFORM_DERIVED"
    HUMAN_APPROVED_POLICY = "HUMAN_APPROVED_POLICY"
    ORACLE_DERIVED = "ORACLE_DERIVED"
    TOOL_GENERATED = "TOOL_GENERATED"
    REPOSITORY_CONTENT = "REPOSITORY_CONTENT"
    DOCUMENT_CONTENT = "DOCUMENT_CONTENT"
    EXTERNAL_USER_CONTENT = "EXTERNAL_USER_CONTENT"
    WORKER_GENERATED = "WORKER_GENERATED"
    LLM_GENERATED = "LLM_GENERATED"
    UNVERIFIED_CODE = "UNVERIFIED_CODE"
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"
    CONTAINS_INSTRUCTION_LIKE_TEXT = "CONTAINS_INSTRUCTION_LIKE_TEXT"
    CONTAINS_SECRET_LIKE_DATA = "CONTAINS_SECRET_LIKE_DATA"
    CONTAINS_EXECUTABLE_CONTENT = "CONTAINS_EXECUTABLE_CONTENT"


class TaintDecisionKind(str, Enum):
    RETAIN_TAINT = "RETAIN_TAINT"
    ADD_TAINT = "ADD_TAINT"
    RELAX_TAINT = "RELAX_TAINT"
    REJECT_RELAXATION = "REJECT_RELAXATION"
    QUARANTINE = "QUARANTINE"


class TaintBasisKind(str, Enum):
    SOURCE_PROFILE = "SOURCE_PROFILE"
    DERIVATION = "DERIVATION"


class TaintRemovalReason(str, Enum):
    VERIFIED_NOT_INSTRUCTION = "VERIFIED_NOT_INSTRUCTION"
    VERIFIED_NOT_SECRET = "VERIFIED_NOT_SECRET"
    VERIFIED_NOT_EXECUTABLE = "VERIFIED_NOT_EXECUTABLE"
    VERIFIED_TRUSTED_SOURCE = "VERIFIED_TRUSTED_SOURCE"
    VERIFIED_CLAIM = "VERIFIED_CLAIM"
    POLICY_AUTHORIZED_RELAXATION = "POLICY_AUTHORIZED_RELAXATION"


class TaintDecisionReason(str, Enum):
    RETENTION_CONFIRMED = "RETENTION_CONFIRMED"
    MONOTONE_ADDITION_CONFIRMED = "MONOTONE_ADDITION_CONFIRMED"
    VERIFIED_RELAXATION_APPROVED = "VERIFIED_RELAXATION_APPROVED"
    RELAXATION_REJECTED = "RELAXATION_REJECTED"
    QUARANTINE_REQUIRED = "QUARANTINE_REQUIRED"


_TAINT_DECISION_REASONS = {
    TaintDecisionKind.RETAIN_TAINT: TaintDecisionReason.RETENTION_CONFIRMED,
    TaintDecisionKind.ADD_TAINT: TaintDecisionReason.MONOTONE_ADDITION_CONFIRMED,
    TaintDecisionKind.RELAX_TAINT: TaintDecisionReason.VERIFIED_RELAXATION_APPROVED,
    TaintDecisionKind.REJECT_RELAXATION: TaintDecisionReason.RELAXATION_REJECTED,
    TaintDecisionKind.QUARANTINE: TaintDecisionReason.QUARANTINE_REQUIRED,
}


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


def _decode(value: bytes) -> object:
    return decode_stage4_canonical_bytes(value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID)


def _handle(value: Stage4AuthorityHandle, *, expected: Stage4AuthorityHandle | None = None):
    try:
        return require_stage4_authority_handle(value, expected_handle=expected)
    except ContractViolation as exc:
        raise _fail(TaintFailureCode.WRONG_AUTHORITY_HANDLE, "authority handle is invalid") from exc


def _configuration_id(value: object) -> RecordId:
    if type(value) is not RecordId or value.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "configuration identity is invalid")
    return value


def _version(value: object, name: str) -> str:
    if type(value) is not str or _VERSION_RE.fullmatch(value) is None:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _format_timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "created_at must be timezone-aware UTC")
    return value.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "created_at must be an exact string")
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "created_at is not canonical UTC") from exc
    if _format_timestamp(parsed) != value:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "created_at is not canonical UTC")
    return parsed


def _exact_dict(value: object, fields: tuple[str, ...], name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be an exact dict")
    if any(type(key) is not str for key in value) or set(value) != set(fields):
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, f"{name} fields are invalid")
    return value


def _exact_list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be an exact list")
    return value


def _safe_id(value: object, name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, f"{name} is invalid")
    return value


def _actor(value: object, name: str) -> ActorIdentity:
    if type(value) is not ActorIdentity:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be ActorIdentity")
    try:
        return ActorIdentity.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, f"{name} is invalid") from exc


def _authority(value: object) -> AuthorityIdentity:
    if type(value) is not AuthorityIdentity:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "classifier identity must be AuthorityIdentity")
    try:
        return AuthorityIdentity.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "classifier identity is invalid") from exc


def _actors(value: object, name: str, *, nonempty: bool) -> tuple[ActorIdentity, ...]:
    if type(value) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    result = tuple(_actor(item, name) for item in value)
    if nonempty and not result:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must not be empty")
    if len({item.value for item in result}) != len(result):
        raise _fail(TaintFailureCode.DUPLICATE_ACTOR, f"{name} contains duplicates")
    return tuple(sorted(result, key=lambda item: item.value))


def _classes(value: object, *, nonempty: bool, name: str) -> tuple[TaintClass, ...]:
    if type(value) not in (tuple, list, set, frozenset):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be an exact collection")
    result: list[TaintClass] = []
    for item in value:
        if type(item) is not TaintClass:
            raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, f"{name} contains an unknown class")
        result.append(item)
    if nonempty and not result:
        raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise _fail(TaintFailureCode.DUPLICATE_TAINT_CLASS, f"{name} contains duplicates")
    return tuple(sorted(result, key=lambda item: item.value))


def _classes_from_transport(value: object, name: str, *, nonempty: bool) -> tuple[TaintClass, ...]:
    items = _exact_list(value, name)
    parsed: list[TaintClass] = []
    for item in items:
        if type(item) is not str:
            raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, f"{name} contains a non-string")
        try:
            parsed.append(TaintClass(item))
        except ValueError as exc:
            raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, f"{name} contains an unknown class") from exc
    return _classes(tuple(parsed), nonempty=nonempty, name=name)


def _ref(value: object, expected_kind: RefKind, name: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be HashBoundRef")
    try:
        result = HashBoundRef.from_dict(value.to_dict())
    except ValueError as exc:
        raise _fail(TaintFailureCode.REF_KIND_MISMATCH, f"{name} is invalid") from exc
    if result.kind is not expected_kind:
        raise _fail(TaintFailureCode.REF_KIND_MISMATCH, f"{name} has the wrong kind")
    return result


def _refs(value: object, expected_kind: RefKind, name: str, *, nonempty: bool) -> tuple[HashBoundRef, ...]:
    if type(value) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, f"{name} must be tuple or list")
    result = tuple(_ref(item, expected_kind, name) for item in value)
    if nonempty and not result:
        raise _fail(TaintFailureCode.REF_KIND_MISMATCH, f"{name} must not be empty")
    if len({item.ref_id for item in result}) != len(result) or len({item.sha256 for item in result}) != len(result):
        raise _fail(TaintFailureCode.REF_KIND_MISMATCH, f"{name} contains duplicates")
    return tuple(sorted(result, key=lambda item: item.ref_id))


def _record_id(value: object, domain: IdentityDomain, canonical_bytes: bytes, name: str) -> RecordId:
    try:
        result = record_id_from_dict(value, canonical_bytes=canonical_bytes)
    except ValueError as exc:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, f"{name} is invalid") from exc
    if result.domain is not domain:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, f"{name} uses the wrong domain")
    return result


@dataclass(frozen=True, init=False)
class SourceTaintProfile:
    schema_version: SchemaVersion
    configuration_id: RecordId
    subject_ref: HashBoundRef
    taint_classes: tuple[TaintClass, ...]
    classifier_identity: AuthorityIdentity
    producer_actor_ids: tuple[ActorIdentity, ...]
    source_actor_ids: tuple[ActorIdentity, ...]
    admission_actor_ids: tuple[ActorIdentity, ...]
    consumer_actor_ids: tuple[ActorIdentity, ...]
    profile_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SourceTaintProfile:
        raise TypeError("SourceTaintProfile is created only by classify_source_taint")

    def to_dict(self) -> dict[str, object]:
        validate_source_taint_profile(self)
        return {**_profile_payload(self), "profile_id": self.profile_id.to_dict()}

    @classmethod
    def from_dict(cls, value: object, *, authority_handle: Stage4AuthorityHandle, expected_subject_ref: HashBoundRef) -> SourceTaintProfile:
        return source_taint_profile_from_dict(value, authority_handle=authority_handle, expected_subject_ref=expected_subject_ref)


def _profile_payload(value: SourceTaintProfile) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "subject_ref": value.subject_ref.to_dict(),
        "taint_classes": [item.value for item in value.taint_classes],
        "classifier_identity": value.classifier_identity.to_dict(),
        "producer_actor_ids": [item.to_dict() for item in value.producer_actor_ids],
        "source_actor_ids": [item.to_dict() for item in value.source_actor_ids],
        "admission_actor_ids": [item.to_dict() for item in value.admission_actor_ids],
        "consumer_actor_ids": [item.to_dict() for item in value.consumer_actor_ids],
    }


class ConfiguredTaintClassifier:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_CLASSIFIER_SEAL or kwargs or len(args) != 1:
            raise TypeError("ConfiguredTaintClassifier is created only by its factory")
        self._authority_handle = args[0]
        _handle(self._authority_handle)

    def classify(self, *, authority_handle: Stage4AuthorityHandle, **values: object) -> SourceTaintProfile:
        _handle(authority_handle, expected=self._authority_handle)
        return classify_source_taint(authority_handle=authority_handle, **values)


def configure_taint_classifier(*, authority_handle: Stage4AuthorityHandle) -> ConfiguredTaintClassifier:
    _handle(authority_handle)
    return ConfiguredTaintClassifier(authority_handle, _seal=_TRUSTED_CLASSIFIER_SEAL)


def classify_source_taint(
    *,
    authority_handle: Stage4AuthorityHandle,
    subject_ref: HashBoundRef,
    taint_classes: object,
    producer_actor_ids: object,
    source_actor_ids: object,
    admission_actor_ids: object,
    consumer_actor_ids: object,
) -> SourceTaintProfile:
    configuration = _handle(authority_handle)
    subject = HashBoundRef.from_dict(subject_ref.to_dict()) if type(subject_ref) is HashBoundRef else None
    if subject is None:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "subject_ref must be HashBoundRef")
    classes = _classes(taint_classes, nonempty=True, name="taint_classes")
    classifier = AuthorityIdentity.from_dict(configuration.taint_classifier_authority.to_dict())
    producers = _actors(producer_actor_ids, "producer_actor_ids", nonempty=True)
    sources = _actors(source_actor_ids, "source_actor_ids", nonempty=True)
    admissions = _actors(admission_actor_ids, "admission_actor_ids", nonempty=False)
    consumers = _actors(consumer_actor_ids, "consumer_actor_ids", nonempty=False)
    participants = {item.value for group in (producers, sources, admissions, consumers) for item in group}
    if classifier.value in participants:
        raise _fail(TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT, "classifier must be separate from producer, source, admission, and consumer")
    result = object.__new__(SourceTaintProfile)
    object.__setattr__(result, "schema_version", SchemaVersion.TAINT_PROFILE_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "subject_ref", subject)
    object.__setattr__(result, "taint_classes", classes)
    object.__setattr__(result, "classifier_identity", classifier)
    object.__setattr__(result, "producer_actor_ids", producers)
    object.__setattr__(result, "source_actor_ids", sources)
    object.__setattr__(result, "admission_actor_ids", admissions)
    object.__setattr__(result, "consumer_actor_ids", consumers)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_profile_payload(result))
    object.__setattr__(result, "profile_id", compute_record_id(domain=IdentityDomain.TAINT_PROFILE, canonical_bytes=payload))
    validate_source_taint_profile(result)
    return result


def validate_source_taint_profile(
    value: SourceTaintProfile,
    *,
    expected_configuration_id: RecordId | None = None,
    expected_classifier_identity: AuthorityIdentity | None = None,
    expected_subject_ref: HashBoundRef | None = None,
) -> None:
    if type(value) is not SourceTaintProfile or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint profile is not factory sealed")
    if value.schema_version is not SchemaVersion.TAINT_PROFILE_V1:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint profile schema is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    HashBoundRef.from_dict(value.subject_ref.to_dict())
    _classes(value.taint_classes, nonempty=True, name="taint_classes")
    classifier = _authority(value.classifier_identity)
    producers = _actors(value.producer_actor_ids, "producer_actor_ids", nonempty=True)
    sources = _actors(value.source_actor_ids, "source_actor_ids", nonempty=True)
    admissions = _actors(value.admission_actor_ids, "admission_actor_ids", nonempty=False)
    consumers = _actors(value.consumer_actor_ids, "consumer_actor_ids", nonempty=False)
    if classifier.value in {item.value for group in (producers, sources, admissions, consumers) for item in group}:
        raise _fail(TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT, "classifier is a participating actor")
    payload = _canonical(_profile_payload(value))
    if type(value.profile_id) is not RecordId or value.profile_id.domain is not IdentityDomain.TAINT_PROFILE:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "profile identity domain is invalid")
    try:
        validate_record_id(value.profile_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "profile identity does not match content") from exc
    if expected_subject_ref is not None and value.subject_ref != HashBoundRef.from_dict(expected_subject_ref.to_dict()):
        raise _fail(TaintFailureCode.SUBJECT_MISMATCH, "profile subject differs from consumer subject")
    if expected_configuration_id is not None and configuration_id != _configuration_id(expected_configuration_id):
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "profile configuration differs")
    if expected_classifier_identity is not None and classifier != _authority(expected_classifier_identity):
        raise _fail(TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT, "profile classifier differs from configured classifier")


def source_taint_profile_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    expected_subject_ref: HashBoundRef,
) -> SourceTaintProfile:
    configuration = _handle(authority_handle)
    fields = (
        "schema_version", "configuration_id", "subject_ref", "taint_classes", "classifier_identity", "producer_actor_ids",
        "source_actor_ids", "admission_actor_ids", "consumer_actor_ids", "profile_id",
    )
    data = _exact_dict(value, fields, "source_taint_profile")
    if data["schema_version"] != SchemaVersion.TAINT_PROFILE_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint profile schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport profile configuration differs")
    subject = HashBoundRef.from_dict(data["subject_ref"])
    if subject != HashBoundRef.from_dict(expected_subject_ref.to_dict()):
        raise _fail(TaintFailureCode.SUBJECT_MISMATCH, "transport subject differs from consumer subject")
    result = classify_source_taint(
        authority_handle=authority_handle,
        subject_ref=subject,
        taint_classes=_classes_from_transport(data["taint_classes"], "taint_classes", nonempty=True),
        producer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["producer_actor_ids"], "producer_actor_ids")),
        source_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["source_actor_ids"], "source_actor_ids")),
        admission_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["admission_actor_ids"], "admission_actor_ids")),
        consumer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["consumer_actor_ids"], "consumer_actor_ids")),
    )
    supplied = _record_id(data["profile_id"], IdentityDomain.TAINT_PROFILE, _canonical(_profile_payload(result)), "profile_id")
    if supplied != result.profile_id:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "profile identity changed during parsing")
    if data["classifier_identity"] != configuration.taint_classifier_authority.to_dict():
        raise _fail(TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT, "transport classifier is not configured")
    return result


@dataclass(frozen=True, init=False)
class TaintDerivationRecord:
    schema_version: SchemaVersion
    configuration_id: RecordId
    subject_ref: HashBoundRef
    source_profile_ids: tuple[RecordId, ...]
    source_derivation_ids: tuple[RecordId, ...]
    transformation_actor: ActorIdentity
    transformation_labels: tuple[TaintClass, ...]
    effective_taint_classes: tuple[TaintClass, ...]
    derivation_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> TaintDerivationRecord:
        raise TypeError("TaintDerivationRecord is created only from validated source profiles")

    def to_dict(self, *, source_profiles: object, source_derivations: object = ()) -> dict[str, object]:
        validate_taint_derivation(self, source_profiles=source_profiles, source_derivations=source_derivations)
        return {**_derivation_payload(self), "derivation_id": self.derivation_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        authority_handle: Stage4AuthorityHandle,
        source_profiles: object,
        source_derivations: object = (),
        expected_subject_ref: HashBoundRef,
    ) -> TaintDerivationRecord:
        return taint_derivation_from_dict(
            value,
            authority_handle=authority_handle,
            source_profiles=source_profiles,
            source_derivations=source_derivations,
            expected_subject_ref=expected_subject_ref,
        )


def _derivation_payload(value: TaintDerivationRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "subject_ref": value.subject_ref.to_dict(),
        "source_profile_ids": [item.to_dict() for item in value.source_profile_ids],
        "source_derivation_ids": [item.to_dict() for item in value.source_derivation_ids],
        "transformation_actor": value.transformation_actor.to_dict(),
        "transformation_labels": [item.value for item in value.transformation_labels],
        "effective_taint_classes": [item.value for item in value.effective_taint_classes],
    }


def create_taint_derivation(
    *,
    authority_handle: Stage4AuthorityHandle,
    subject_ref: HashBoundRef,
    source_profiles: object,
    source_derivations: object = (),
    transformation_actor: ActorIdentity,
    transformation_labels: object,
) -> TaintDerivationRecord:
    configuration = _handle(authority_handle)
    if type(source_profiles) not in (tuple, list) or type(source_derivations) not in (tuple, list):
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation sources must be lists or tuples")
    profiles = tuple(source_profiles)
    derivations = tuple(source_derivations)
    if not profiles and not derivations:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation sources must be non-empty")
    for profile in profiles:
        validate_source_taint_profile(profile, expected_configuration_id=configuration.configuration_id)
    for derivation in derivations:
        _validate_taint_derivation_identity(derivation, expected_configuration_id=configuration.configuration_id)
    if len({profile.profile_id.value for profile in profiles}) != len(profiles):
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "source_profiles contains duplicates")
    labels = _classes(transformation_labels, nonempty=False, name="transformation_labels")
    effective = _classes(
        {item for profile in profiles for item in profile.taint_classes}
        | {item for derivation in derivations for item in derivation.effective_taint_classes}
        | set(labels),
        nonempty=True,
        name="effective_taint_classes",
    )
    if type(subject_ref) is not HashBoundRef:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "subject_ref must be HashBoundRef")
    result = object.__new__(TaintDerivationRecord)
    object.__setattr__(result, "schema_version", SchemaVersion.TAINT_DERIVATION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "subject_ref", HashBoundRef.from_dict(subject_ref.to_dict()))
    object.__setattr__(result, "source_profile_ids", tuple(sorted((profile.profile_id for profile in profiles), key=lambda item: item.value)))
    object.__setattr__(result, "source_derivation_ids", tuple(sorted((item.derivation_id for item in derivations), key=lambda item: item.value)))
    object.__setattr__(result, "transformation_actor", _actor(transformation_actor, "transformation_actor"))
    object.__setattr__(result, "transformation_labels", labels)
    object.__setattr__(result, "effective_taint_classes", effective)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_derivation_payload(result))
    object.__setattr__(result, "derivation_id", compute_record_id(domain=IdentityDomain.TAINT_DERIVATION, canonical_bytes=payload))
    validate_taint_derivation(result, source_profiles=profiles, source_derivations=derivations)
    return result


def _validate_taint_derivation_identity(
    value: TaintDerivationRecord,
    *,
    expected_configuration_id: RecordId,
) -> None:
    if type(value) is not TaintDerivationRecord or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint derivation is not factory sealed")
    if value.schema_version is not SchemaVersion.TAINT_DERIVATION_V1:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint derivation schema is unknown")
    if _configuration_id(value.configuration_id) != _configuration_id(expected_configuration_id):
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "derivation configuration differs")
    payload = _canonical(_derivation_payload(value))
    if type(value.derivation_id) is not RecordId or value.derivation_id.domain is not IdentityDomain.TAINT_DERIVATION:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity domain is invalid")
    try:
        validate_record_id(value.derivation_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity does not match content") from exc


def validate_taint_derivation(
    value: TaintDerivationRecord,
    *,
    source_profiles: object,
    source_derivations: object = (),
) -> None:
    if type(source_profiles) not in (tuple, list) or type(source_derivations) not in (tuple, list):
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation sources must be lists or tuples")
    if not source_profiles and not source_derivations:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation sources must be non-empty")
    profiles = tuple(source_profiles)
    derivations = tuple(source_derivations)
    for profile in profiles:
        validate_source_taint_profile(profile, expected_configuration_id=value.configuration_id)
    for derivation in derivations:
        _validate_taint_derivation_identity(derivation, expected_configuration_id=value.configuration_id)
    expected_ids = tuple(sorted((profile.profile_id for profile in profiles), key=lambda item: item.value))
    if value.source_profile_ids != expected_ids:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation source identities changed")
    expected_derivation_ids = tuple(sorted((item.derivation_id for item in derivations), key=lambda item: item.value))
    if value.source_derivation_ids != expected_derivation_ids:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation parent identities changed")
    HashBoundRef.from_dict(value.subject_ref.to_dict())
    _actor(value.transformation_actor, "transformation_actor")
    labels = _classes(value.transformation_labels, nonempty=False, name="transformation_labels")
    expected = _classes(
        {item for profile in profiles for item in profile.taint_classes}
        | {item for derivation in derivations for item in derivation.effective_taint_classes}
        | set(labels),
        nonempty=True,
        name="effective_taint_classes",
    )
    actual = _classes(value.effective_taint_classes, nonempty=True, name="effective_taint_classes")
    if actual != expected:
        raise _fail(TaintFailureCode.TAINT_REMOVAL_FORBIDDEN, "derivation failed to retain the monotone source union")
    _validate_taint_derivation_identity(value, expected_configuration_id=value.configuration_id)


def taint_derivation_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    source_profiles: object,
    source_derivations: object = (),
    expected_subject_ref: HashBoundRef,
) -> TaintDerivationRecord:
    fields = (
        "schema_version", "configuration_id", "subject_ref", "source_profile_ids", "source_derivation_ids", "transformation_actor",
        "transformation_labels", "effective_taint_classes", "derivation_id",
    )
    data = _exact_dict(value, fields, "taint_derivation")
    if data["schema_version"] != SchemaVersion.TAINT_DERIVATION_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint derivation schema is unknown")
    configuration = _handle(authority_handle)
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport derivation configuration differs")
    subject = HashBoundRef.from_dict(data["subject_ref"])
    if subject != HashBoundRef.from_dict(expected_subject_ref.to_dict()):
        raise _fail(TaintFailureCode.SUBJECT_MISMATCH, "derivation subject differs from consumer subject")
    result = create_taint_derivation(
        authority_handle=authority_handle,
        subject_ref=subject,
        source_profiles=source_profiles,
        source_derivations=source_derivations,
        transformation_actor=ActorIdentity.from_dict(data["transformation_actor"]),
        transformation_labels=_classes_from_transport(data["transformation_labels"], "transformation_labels", nonempty=False),
    )
    if data["source_profile_ids"] != [item.to_dict() for item in result.source_profile_ids]:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "transport source profile identities changed")
    if data["source_derivation_ids"] != [item.to_dict() for item in result.source_derivation_ids]:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "transport source derivation identities changed")
    if data["effective_taint_classes"] != [item.value for item in result.effective_taint_classes]:
        raise _fail(TaintFailureCode.TAINT_REMOVAL_FORBIDDEN, "transport derivation removed or changed taint")
    supplied = _record_id(data["derivation_id"], IdentityDomain.TAINT_DERIVATION, _canonical(_derivation_payload(result)), "derivation_id")
    if supplied != result.derivation_id:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity changed during parsing")
    return result


@dataclass(frozen=True, init=False)
class TaintChangeProposal:
    schema_version: str
    configuration_id: RecordId
    basis_kind: TaintBasisKind
    subject_ref: HashBoundRef
    current_profile_id: RecordId
    current_profile_sha256: str
    current_taint_classes: tuple[TaintClass, ...]
    proposed_taint_classes: tuple[TaintClass, ...]
    producer_actor_ids: tuple[ActorIdentity, ...]
    source_actor_ids: tuple[ActorIdentity, ...]
    proposer_identity: ActorIdentity
    predecessor_decision_id: str | None
    decision_sequence: int
    proposal_id: ProposalId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> TaintChangeProposal:
        raise TypeError("TaintChangeProposal is created only from a validated current profile")

    def to_dict(self) -> dict[str, object]:
        validate_taint_change_proposal(self)
        return {**_proposal_payload(self), "proposal_id": self.proposal_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        authority_handle: Stage4AuthorityHandle,
        current_profile: SourceTaintProfile | TaintDerivationRecord,
        source_profiles: object = (),
        source_derivations: object = (),
        prior_decisions: object = (),
    ) -> TaintChangeProposal:
        return taint_change_proposal_from_dict(
            value,
            authority_handle=authority_handle,
            current_profile=current_profile,
            source_profiles=source_profiles,
            source_derivations=source_derivations,
            prior_decisions=prior_decisions,
        )


def _basis_sha256(value: SourceTaintProfile | TaintDerivationRecord) -> str:
    if type(value) is SourceTaintProfile:
        validate_source_taint_profile(value)
        payload = value.to_dict()
    elif type(value) is TaintDerivationRecord:
        _validate_taint_derivation_identity(value, expected_configuration_id=value.configuration_id)
        payload = {**_derivation_payload(value), "derivation_id": value.derivation_id.to_dict()}
    else:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "taint basis is invalid")
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _proposal_payload(value: TaintChangeProposal) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "configuration_id": value.configuration_id.to_dict(),
        "basis_kind": value.basis_kind.value,
        "subject_ref": value.subject_ref.to_dict(),
        "current_profile_id": value.current_profile_id.to_dict(),
        "current_profile_sha256": value.current_profile_sha256,
        "current_taint_classes": [item.value for item in value.current_taint_classes],
        "proposed_taint_classes": [item.value for item in value.proposed_taint_classes],
        "producer_actor_ids": [item.to_dict() for item in value.producer_actor_ids],
        "source_actor_ids": [item.to_dict() for item in value.source_actor_ids],
        "proposer_identity": value.proposer_identity.to_dict(),
        "predecessor_decision_id": value.predecessor_decision_id,
        "decision_sequence": value.decision_sequence,
    }


def _decision_id_text(value: object, name: str, *, allow_none: bool) -> str | None:
    if allow_none and value is None:
        return None
    if type(value) is not str or not value.startswith(_AUTHORITY_ID_PREFIX) or len(value) != len(_AUTHORITY_ID_PREFIX) + 64:
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, f"{name} is invalid")
    if re.fullmatch(r"[0-9a-f]{64}", value[len(_AUTHORITY_ID_PREFIX):]) is None:
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, f"{name} is invalid")
    return value


def create_taint_change_proposal(
    *,
    authority_handle: Stage4AuthorityHandle,
    current_profile: SourceTaintProfile | TaintDerivationRecord,
    proposed_taint_classes: object,
    proposer_identity: ActorIdentity,
    predecessor_decision_id: str | None,
    decision_sequence: int,
    source_profiles: object = (),
    source_derivations: object = (),
    prior_decisions: object = (),
) -> TaintChangeProposal:
    configuration = _handle(authority_handle)
    if type(current_profile) is SourceTaintProfile:
        validate_source_taint_profile(current_profile, expected_configuration_id=configuration.configuration_id)
        basis_kind = TaintBasisKind.SOURCE_PROFILE
        basis_id = current_profile.profile_id
        current_effective = tuple(current_profile.taint_classes)
        profiles = (current_profile,)
        derivations: tuple[TaintDerivationRecord, ...] = ()
    elif type(current_profile) is TaintDerivationRecord:
        profiles = tuple(source_profiles) if type(source_profiles) in (tuple, list) else ()
        derivations = tuple(source_derivations) if type(source_derivations) in (tuple, list) else ()
        _reconstruct_derivation_closure(
            authority_handle=authority_handle,
            source_profiles=profiles,
            derivations=derivations,
            root_derivation=current_profile,
        )
        basis_kind = TaintBasisKind.DERIVATION
        basis_id = current_profile.derivation_id
        current_effective = tuple(current_profile.effective_taint_classes)
    else:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "current taint basis is invalid")
    if type(prior_decisions) not in (tuple, list):
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "prior_decisions must be a complete tuple or list")
    if prior_decisions:
        reconstructed = reconstruct_effective_taint(
            authority_handle=authority_handle,
            root_basis=current_profile,
            source_profiles=source_profiles,
            derivations=source_derivations,
            decisions=prior_decisions,
        )
        current_effective = reconstructed.taint_classes
        if predecessor_decision_id != reconstructed.last_decision_id or decision_sequence != reconstructed.decision_sequence + 1:
            raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "proposal predecessor does not match complete prior decisions")
    proposed = _classes(proposed_taint_classes, nonempty=False, name="proposed_taint_classes")
    if type(decision_sequence) is not int or decision_sequence < 1:
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision_sequence must be a positive exact integer")
    predecessor = _decision_id_text(predecessor_decision_id, "predecessor_decision_id", allow_none=True)
    if (decision_sequence == 1) != (predecessor is None):
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "first decision alone has no predecessor")
    source_actor_map = {
        actor.value: actor
        for profile in profiles
        for actor in (*profile.source_actor_ids, ActorIdentity(profile.classifier_identity.value))
    }
    source_actors = _actors(
        tuple(source_actor_map[key] for key in sorted(source_actor_map)),
        "proposal.source_actor_ids",
        nonempty=True,
    )
    producer_actor_map = {
        actor.value: actor for profile in profiles for actor in profile.producer_actor_ids
    }
    producer_actors = _actors(
        tuple(producer_actor_map[key] for key in sorted(producer_actor_map)),
        "proposal.producer_actor_ids",
        nonempty=True,
    )
    result = object.__new__(TaintChangeProposal)
    object.__setattr__(result, "schema_version", TAINT_AUTHORITY_PROPOSAL_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "basis_kind", basis_kind)
    object.__setattr__(result, "subject_ref", HashBoundRef.from_dict(current_profile.subject_ref.to_dict()))
    object.__setattr__(result, "current_profile_id", basis_id)
    object.__setattr__(result, "current_profile_sha256", _basis_sha256(current_profile))
    object.__setattr__(result, "current_taint_classes", current_effective)
    object.__setattr__(result, "proposed_taint_classes", proposed)
    object.__setattr__(result, "producer_actor_ids", producer_actors)
    object.__setattr__(result, "source_actor_ids", source_actors)
    object.__setattr__(result, "proposer_identity", _actor(proposer_identity, "proposer_identity"))
    object.__setattr__(result, "predecessor_decision_id", predecessor)
    object.__setattr__(result, "decision_sequence", decision_sequence)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    object.__setattr__(result, "proposal_id", compute_proposal_id(canonical_bytes=_canonical(_proposal_payload(result))))
    validate_taint_change_proposal(result, current_profile=current_profile)
    return result


def validate_taint_change_proposal(value: TaintChangeProposal, *, current_profile: SourceTaintProfile | None = None) -> None:
    if type(value) is not TaintChangeProposal or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint proposal is not factory sealed")
    if value.schema_version != TAINT_AUTHORITY_PROPOSAL_V1 or type(value.schema_version) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint proposal schema is unknown")
    _configuration_id(value.configuration_id)
    if type(value.basis_kind) is not TaintBasisKind:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "taint basis kind is invalid")
    HashBoundRef.from_dict(value.subject_ref.to_dict())
    expected_domain = IdentityDomain.TAINT_PROFILE if value.basis_kind is TaintBasisKind.SOURCE_PROFILE else IdentityDomain.TAINT_DERIVATION
    if type(value.current_profile_id) is not RecordId or value.current_profile_id.domain is not expected_domain:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "current profile identity is invalid")
    if type(value.current_profile_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.current_profile_sha256) is None:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "current profile hash is invalid")
    _classes(value.current_taint_classes, nonempty=True, name="current_taint_classes")
    _classes(value.proposed_taint_classes, nonempty=False, name="proposed_taint_classes")
    _actors(value.producer_actor_ids, "producer_actor_ids", nonempty=True)
    _actors(value.source_actor_ids, "source_actor_ids", nonempty=True)
    _actor(value.proposer_identity, "proposer_identity")
    predecessor = _decision_id_text(value.predecessor_decision_id, "predecessor_decision_id", allow_none=True)
    if type(value.decision_sequence) is not int or value.decision_sequence < 1 or ((value.decision_sequence == 1) != (predecessor is None)):
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "proposal decision chain is invalid")
    expected = compute_proposal_id(canonical_bytes=_canonical(_proposal_payload(value)))
    if type(value.proposal_id) is not ProposalId or value.proposal_id != expected:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "proposal identity does not match content")
    if current_profile is not None:
        if type(current_profile) is SourceTaintProfile:
            validate_source_taint_profile(current_profile, expected_configuration_id=value.configuration_id)
            expected_kind = TaintBasisKind.SOURCE_PROFILE
            expected_id = current_profile.profile_id
        elif type(current_profile) is TaintDerivationRecord:
            _validate_taint_derivation_identity(current_profile, expected_configuration_id=value.configuration_id)
            expected_kind = TaintBasisKind.DERIVATION
            expected_id = current_profile.derivation_id
        else:
            raise _fail(TaintFailureCode.TYPE_MISMATCH, "current taint basis is invalid")
        if (
            value.subject_ref != current_profile.subject_ref
            or value.basis_kind is not expected_kind
            or value.current_profile_id != expected_id
            or value.current_profile_sha256 != _basis_sha256(current_profile)
        ):
            raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "proposal does not bind the current profile")


def taint_change_proposal_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    current_profile: SourceTaintProfile | TaintDerivationRecord,
    source_profiles: object = (),
    source_derivations: object = (),
    prior_decisions: object = (),
) -> TaintChangeProposal:
    configuration = _handle(authority_handle)
    fields = (
        "schema_version", "configuration_id", "basis_kind", "subject_ref", "current_profile_id", "current_profile_sha256",
        "current_taint_classes", "proposed_taint_classes", "producer_actor_ids", "source_actor_ids",
        "proposer_identity", "predecessor_decision_id", "decision_sequence", "proposal_id",
    )
    data = _exact_dict(value, fields, "taint_change_proposal")
    if data["schema_version"] != TAINT_AUTHORITY_PROPOSAL_V1 or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint proposal schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport proposal configuration differs")
    result = create_taint_change_proposal(
        authority_handle=authority_handle,
        current_profile=current_profile,
        proposed_taint_classes=_classes_from_transport(data["proposed_taint_classes"], "proposed_taint_classes", nonempty=False),
        proposer_identity=ActorIdentity.from_dict(data["proposer_identity"]),
        predecessor_decision_id=data["predecessor_decision_id"],
        decision_sequence=data["decision_sequence"],
        source_profiles=source_profiles,
        source_derivations=source_derivations,
        prior_decisions=prior_decisions,
    )
    if data["basis_kind"] != result.basis_kind.value:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "transport basis kind differs")
    if data["current_taint_classes"] != [item.value for item in result.current_taint_classes]:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "transport current taint was supplied instead of reconstructed")
    for field, expected in (
        ("subject_ref", result.subject_ref.to_dict()),
        ("current_profile_id", result.current_profile_id.to_dict()),
        ("current_profile_sha256", result.current_profile_sha256),
        ("producer_actor_ids", [item.to_dict() for item in result.producer_actor_ids]),
        ("source_actor_ids", [item.to_dict() for item in result.source_actor_ids]),
        ("proposal_id", result.proposal_id.to_dict()),
    ):
        if data[field] != expected:
            raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, f"transport {field} differs from current profile")
    return result


@dataclass(frozen=True)
class TaintRemovalVerification:
    schema_version: str
    taint_class: TaintClass
    verification_contract_ref: HashBoundRef
    verification_result_ref: HashBoundRef
    reason_code: TaintRemovalReason

    def __post_init__(self) -> None:
        _validate_removal_verification(self)

    def to_dict(self) -> dict[str, object]:
        _validate_removal_verification(self)
        return {
            "schema_version": self.schema_version,
            "taint_class": self.taint_class.value,
            "verification_contract_ref": self.verification_contract_ref.to_dict(),
            "verification_result_ref": self.verification_result_ref.to_dict(),
            "reason_code": self.reason_code.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> TaintRemovalVerification:
        data = _exact_dict(value, ("schema_version", "taint_class", "verification_contract_ref", "verification_result_ref", "reason_code"), "taint_removal_verification")
        try:
            taint_class = TaintClass(data["taint_class"])
            reason = TaintRemovalReason(data["reason_code"])
        except (TypeError, ValueError) as exc:
            raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, "removal verification enum is unknown") from exc
        return cls(data["schema_version"], taint_class, HashBoundRef.from_dict(data["verification_contract_ref"]), HashBoundRef.from_dict(data["verification_result_ref"]), reason)


def _validate_removal_verification(value: TaintRemovalVerification) -> None:
    if type(value) is not TaintRemovalVerification:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "removal verification must be exact")
    if value.schema_version != TAINT_REMOVAL_VERIFICATION_V1 or type(value.schema_version) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "removal verification schema is unknown")
    if type(value.taint_class) is not TaintClass or type(value.reason_code) is not TaintRemovalReason:
        raise _fail(TaintFailureCode.UNKNOWN_TAINT_CLASS, "removal verification enum is invalid")
    _ref(value.verification_contract_ref, RefKind.CONTRACT_CONDITION, "verification_contract_ref")
    _ref(value.verification_result_ref, RefKind.SOURCE_EVIDENCE, "verification_result_ref")


@dataclass(frozen=True, init=False)
class TaintAuthorityDecision:
    schema_version: SchemaVersion
    configuration_id: RecordId
    decision_kind: TaintDecisionKind
    proposal: TaintChangeProposal
    effective_taint_classes: tuple[TaintClass, ...]
    removal_verifications: tuple[TaintRemovalVerification, ...]
    policy_refs: tuple[HashBoundRef, ...]
    policy_version: str
    reason_code: TaintDecisionReason
    created_at: datetime
    independence_proof: IndependenceProof
    decision_id: AuthorityDecisionId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> TaintAuthorityDecision:
        raise TypeError("TaintAuthorityDecision is created only by independent authority evaluation")

    def to_dict(self) -> dict[str, object]:
        validate_taint_authority_decision(self)
        return {
            **_decision_payload(self),
            "proposal": self.proposal.to_dict(),
            "independence_proof": self.independence_proof.to_dict(),
            "decision_id": self.decision_id.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        authority_handle: Stage4AuthorityHandle,
        evaluator: ConfiguredTaintAuthorityEvaluator,
        current_profile: SourceTaintProfile | TaintDerivationRecord,
        source_profiles: object = (),
        source_derivations: object = (),
    ) -> TaintAuthorityDecision:
        return taint_authority_decision_from_dict(
            value,
            authority_handle=authority_handle,
            evaluator=evaluator,
            current_profile=current_profile,
            source_profiles=source_profiles,
            source_derivations=source_derivations,
        )


def _decision_payload(value: TaintAuthorityDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "decision_kind": value.decision_kind.value,
        "proposal_id": value.proposal.proposal_id.to_dict(),
        "effective_taint_classes": [item.value for item in value.effective_taint_classes],
        "removal_verifications": [item.to_dict() for item in value.removal_verifications],
        "policy_refs": [item.to_dict() for item in value.policy_refs],
        "policy_version": value.policy_version,
        "reason_code": value.reason_code.value,
        "created_at": _format_timestamp(value.created_at),
        "predecessor_decision_id": value.proposal.predecessor_decision_id,
        "decision_sequence": value.proposal.decision_sequence,
    }


class ConfiguredTaintAuthorityEvaluator:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_EVALUATOR_SEAL or kwargs or len(args) != 3:
            raise TypeError("ConfiguredTaintAuthorityEvaluator is created only by its factory")
        authority_handle, policy_version, trusted_clock = args
        _handle(authority_handle)
        self._authority_handle = authority_handle
        self._policy_version = _version(policy_version, "policy_version")
        if not callable(trusted_clock):
            raise _fail(TaintFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
        self._trusted_clock = trusted_clock

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    def evaluate(self, *, authority_handle: Stage4AuthorityHandle, **values: object) -> TaintAuthorityDecision:
        self.require_handle(authority_handle)
        return _create_taint_authority_decision(
            authority_handle=authority_handle,
            evaluator=self,
            created_at=self._trusted_clock(),
            **values,
        )


def configure_taint_authority_evaluator(
    *,
    authority_handle: Stage4AuthorityHandle,
    policy_version: str,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredTaintAuthorityEvaluator:
    _handle(authority_handle)
    return ConfiguredTaintAuthorityEvaluator(
        authority_handle,
        policy_version,
        trusted_clock,
        _seal=_TRUSTED_EVALUATOR_SEAL,
    )


def create_taint_authority_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredTaintAuthorityEvaluator,
    proposal: TaintChangeProposal,
    decision_kind: TaintDecisionKind,
    removal_verifications: object,
    policy_refs: object,
    executor_identity: ActorIdentity | None = None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...] = (),
) -> TaintAuthorityDecision:
    if type(evaluator) is not ConfiguredTaintAuthorityEvaluator:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "taint evaluator is invalid")
    evaluator.require_handle(authority_handle)
    return evaluator.evaluate(
        authority_handle=authority_handle,
        proposal=proposal,
        decision_kind=decision_kind,
        removal_verifications=removal_verifications,
        policy_refs=policy_refs,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
    )


def _create_taint_authority_decision(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredTaintAuthorityEvaluator,
    created_at: datetime,
    proposal: TaintChangeProposal,
    decision_kind: TaintDecisionKind,
    removal_verifications: object,
    policy_refs: object,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
) -> TaintAuthorityDecision:
    configuration = _handle(authority_handle)
    evaluator.require_handle(authority_handle)
    validate_taint_change_proposal(proposal)
    if proposal.configuration_id != configuration.configuration_id:
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "proposal configuration differs from evaluator")
    if type(decision_kind) is not TaintDecisionKind:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "decision kind is unknown")
    if type(removal_verifications) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "removal_verifications must be tuple or list")
    removals = tuple(TaintRemovalVerification.from_dict(item.to_dict()) if type(item) is TaintRemovalVerification else None for item in removal_verifications)
    if any(item is None for item in removals):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "removal verification entry must be exact")
    removal_items = tuple(item for item in removals if item is not None)
    policies = _refs(policy_refs, RefKind.CONTRACT_CONDITION, "policy_refs", nonempty=True)
    independence_proof = create_independence_proof(
        schema_version=SchemaVersion.INDEPENDENCE_PROOF_V1,
        subject_proposal_id=proposal.proposal_id,
        authority_identity=configuration.taint_reviewer_authority,
        authority_role=AuthorityRole.TAINT_REVIEWER,
        reason_code=ReasonCode.TAINT_REVIEW_INDEPENDENT,
        producer_actor_ids=proposal.producer_actor_ids,
        source_actor_ids=proposal.source_actor_ids,
        proposer_identity=proposal.proposer_identity,
        executor_identity=executor_identity,
        subject_derived_actor_ids=subject_derived_actor_ids,
        delegation_chain=(),
    )
    current = set(proposal.current_taint_classes)
    proposed = set(proposal.proposed_taint_classes)
    removed = current - proposed
    added = proposed - current
    if decision_kind is TaintDecisionKind.RETAIN_TAINT:
        if proposed != current:
            raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "RETAIN_TAINT requires unchanged classes")
        effective = current
    elif decision_kind is TaintDecisionKind.ADD_TAINT:
        if removed or not added:
            raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "ADD_TAINT requires a strict monotone addition")
        effective = proposed
    elif decision_kind is TaintDecisionKind.RELAX_TAINT:
        if added or not removed:
            raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "RELAX_TAINT requires a strict removal without additions")
        effective = proposed
    elif decision_kind is TaintDecisionKind.REJECT_RELAXATION:
        if not removed:
            raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "REJECT_RELAXATION requires a removal proposal")
        effective = current
    else:
        if proposed != current:
            raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "QUARANTINE does not rewrite taint classes")
        effective = current
    evidence_classes = [item.taint_class for item in removal_items]
    if len(set(evidence_classes)) != len(evidence_classes):
        raise _fail(TaintFailureCode.REMOVAL_EVIDENCE_DUPLICATE, "removed class has duplicate verification")
    expected_evidence = removed if decision_kind is TaintDecisionKind.RELAX_TAINT else set()
    if set(evidence_classes) != expected_evidence:
        raise _fail(TaintFailureCode.REMOVAL_EVIDENCE_MISSING, "each and only each removed class needs verification")
    result = object.__new__(TaintAuthorityDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.TAINT_AUTHORITY_DECISION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "proposal", proposal)
    object.__setattr__(result, "effective_taint_classes", _classes(effective, nonempty=False, name="effective_taint_classes"))
    object.__setattr__(result, "removal_verifications", tuple(sorted(removal_items, key=lambda item: item.taint_class.value)))
    object.__setattr__(result, "policy_refs", policies)
    object.__setattr__(result, "policy_version", evaluator._policy_version)
    object.__setattr__(result, "reason_code", _TAINT_DECISION_REASONS[decision_kind])
    object.__setattr__(result, "created_at", _parse_timestamp(_format_timestamp(created_at)))
    object.__setattr__(result, "independence_proof", independence_proof)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    identity_bytes = _canonical(_decision_payload(result))
    object.__setattr__(result, "decision_id", compute_authority_decision_id(canonical_bytes=identity_bytes, independence_proof=independence_proof))
    validate_taint_authority_decision(result)
    return result


def validate_taint_authority_decision(
    value: TaintAuthorityDecision,
    *,
    authority_handle: Stage4AuthorityHandle | None = None,
) -> None:
    if type(value) is not TaintAuthorityDecision or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint decision is not authority sealed")
    if value.schema_version is not SchemaVersion.TAINT_AUTHORITY_DECISION_V1 or type(value.decision_kind) is not TaintDecisionKind:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint decision schema or kind is unknown")
    configuration_id = _configuration_id(value.configuration_id)
    if authority_handle is not None:
        configuration = _handle(authority_handle)
        if configuration_id != configuration.configuration_id:
            raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "decision configuration differs")
        if value.independence_proof.authority_identity != configuration.taint_reviewer_authority:
            raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "decision reviewer is not configured")
    validate_taint_change_proposal(value.proposal)
    validate_independence_proof(value.independence_proof)
    if value.independence_proof.authority_role is not AuthorityRole.TAINT_REVIEWER or value.independence_proof.subject_proposal_id != value.proposal.proposal_id:
        raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "taint decision proof is invalid")
    if value.proposal.configuration_id != configuration_id:
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "decision/proposal configuration mismatch")
    _version(value.policy_version, "policy_version")
    if type(value.reason_code) is not TaintDecisionReason or value.reason_code is not _TAINT_DECISION_REASONS[value.decision_kind]:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "decision reason does not match decision kind")
    _parse_timestamp(_format_timestamp(value.created_at))
    for item in value.removal_verifications:
        _validate_removal_verification(item)
    _refs(value.policy_refs, RefKind.CONTRACT_CONDITION, "policy_refs", nonempty=True)
    current = set(value.proposal.current_taint_classes)
    proposed = set(value.proposal.proposed_taint_classes)
    effective = set(value.effective_taint_classes)
    removed = current - proposed
    evidence = {item.taint_class for item in value.removal_verifications}
    if len(evidence) != len(value.removal_verifications):
        raise _fail(TaintFailureCode.REMOVAL_EVIDENCE_DUPLICATE, "removed class has duplicate verification")
    if value.decision_kind is TaintDecisionKind.RETAIN_TAINT and (proposed != current or effective != current or evidence):
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "RETAIN_TAINT was altered")
    if value.decision_kind is TaintDecisionKind.ADD_TAINT and (removed or not proposed - current or effective != proposed or evidence):
        raise _fail(TaintFailureCode.TAINT_REMOVAL_FORBIDDEN, "ADD_TAINT is not monotone")
    if value.decision_kind is TaintDecisionKind.RELAX_TAINT and (proposed - current or not removed or effective != proposed or evidence != removed):
        raise _fail(TaintFailureCode.REMOVAL_EVIDENCE_MISSING, "RELAX_TAINT lacks exact per-class evidence")
    if value.decision_kind is TaintDecisionKind.REJECT_RELAXATION and (not removed or effective != current or evidence):
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "rejected relaxation changed effective taint")
    if value.decision_kind is TaintDecisionKind.QUARANTINE and (proposed != current or effective != current or evidence):
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "quarantine changed effective taint")
    identity_bytes = _canonical(_decision_payload(value))
    expected = compute_authority_decision_id(canonical_bytes=identity_bytes, independence_proof=value.independence_proof)
    if type(value.decision_id) is not AuthorityDecisionId or value.decision_id != expected:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "taint decision identity does not bind decision and proof")


def taint_authority_decision_from_dict(
    value: object,
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator: ConfiguredTaintAuthorityEvaluator,
    current_profile: SourceTaintProfile | TaintDerivationRecord,
    source_profiles: object = (),
    source_derivations: object = (),
) -> TaintAuthorityDecision:
    configuration = _handle(authority_handle)
    evaluator.require_handle(authority_handle)
    fields = (
        "schema_version", "configuration_id", "decision_kind", "proposal_id", "effective_taint_classes",
        "removal_verifications", "policy_refs", "policy_version", "reason_code", "created_at", "predecessor_decision_id", "decision_sequence",
        "proposal", "independence_proof", "decision_id",
    )
    data = _exact_dict(value, fields, "taint_authority_decision")
    if data["schema_version"] != SchemaVersion.TAINT_AUTHORITY_DECISION_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint decision schema is unknown")
    if data["configuration_id"] != configuration.configuration_id.to_dict():
        raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "transport decision configuration differs")
    if data["policy_version"] != evaluator._policy_version:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "transport policy differs from configured evaluator")
    try:
        kind = TaintDecisionKind(data["decision_kind"])
    except (TypeError, ValueError) as exc:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "taint decision kind is unknown") from exc
    proposal = taint_change_proposal_from_dict(
        data["proposal"],
        authority_handle=authority_handle,
        current_profile=current_profile,
        source_profiles=source_profiles,
        source_derivations=source_derivations,
    )
    proposal_bytes = _canonical(_proposal_payload(proposal))
    proof = independence_proof_from_dict(data["independence_proof"], proposal_canonical_bytes=proposal_bytes)
    result = _create_taint_authority_decision(
        authority_handle=authority_handle,
        evaluator=evaluator,
        created_at=_parse_timestamp(data["created_at"]),
        proposal=proposal,
        decision_kind=kind,
        removal_verifications=tuple(TaintRemovalVerification.from_dict(item) for item in _exact_list(data["removal_verifications"], "removal_verifications")),
        policy_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["policy_refs"], "policy_refs")),
        executor_identity=proof.executor_identity,
        subject_derived_actor_ids=proof.subject_derived_actor_ids,
    )
    if data["independence_proof"] != result.independence_proof.to_dict():
        raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "transport proof is not configured")
    if data["reason_code"] != result.reason_code.value:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "transport decision reason differs")
    for field, expected in (
        ("proposal_id", result.proposal.proposal_id.to_dict()),
        ("effective_taint_classes", [item.value for item in result.effective_taint_classes]),
        ("predecessor_decision_id", result.proposal.predecessor_decision_id),
        ("decision_sequence", result.proposal.decision_sequence),
    ):
        if data[field] != expected:
            raise _fail(TaintFailureCode.DECISION_MISMATCH, f"transport {field} differs from evaluated decision")
    payload = _canonical(_decision_payload(result))
    try:
        supplied = authority_decision_id_from_dict(data["decision_id"], canonical_bytes=payload, independence_proof=proof)
    except ValueError as exc:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "transport taint decision identity is invalid") from exc
    if supplied != result.decision_id:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "transport taint decision identity changed")
    return result


@dataclass(frozen=True)
class EffectiveTaint:
    taint_classes: tuple[TaintClass, ...]
    quarantined: bool
    last_decision_id: str | None
    decision_sequence: int


def _reconstruct_derivation_closure(
    *,
    authority_handle: Stage4AuthorityHandle,
    source_profiles: object,
    derivations: object,
    root_derivation: TaintDerivationRecord,
) -> tuple[TaintClass, ...]:
    configuration = _handle(authority_handle)
    if type(source_profiles) not in (tuple, list) or type(derivations) not in (tuple, list):
        raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation closure must be complete tuples or lists")
    profiles = tuple(source_profiles)
    nodes = tuple(derivations)
    profile_map: dict[str, SourceTaintProfile] = {}
    for profile in profiles:
        validate_source_taint_profile(profile, expected_configuration_id=configuration.configuration_id)
        if profile.profile_id.value in profile_map:
            raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation closure contains duplicate profile identity")
        profile_map[profile.profile_id.value] = profile
    node_map: dict[str, TaintDerivationRecord] = {}
    for node in nodes:
        if type(node) is not TaintDerivationRecord or getattr(node, "_trusted_seal", None) is not _TRUSTED_SEAL:
            raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "derivation closure node is not sealed")
        if node.schema_version is not SchemaVersion.TAINT_DERIVATION_V1 or node.configuration_id != configuration.configuration_id:
            raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "derivation closure node configuration differs")
        if type(node.derivation_id) is not RecordId or node.derivation_id.domain is not IdentityDomain.TAINT_DERIVATION:
            raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation closure node identity is invalid")
        if node.derivation_id.value in node_map:
            raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation closure contains duplicate node identity")
        node_map[node.derivation_id.value] = node
    _validate_taint_derivation_identity(root_derivation, expected_configuration_id=configuration.configuration_id)
    if node_map.get(root_derivation.derivation_id.value) is not root_derivation:
        raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "root derivation is absent or substituted")
    visiting: set[str] = set()
    visited: set[str] = set()
    reachable_profiles: set[str] = set()

    def visit(node: TaintDerivationRecord) -> None:
        node_id = node.derivation_id.value
        if node_id in visiting:
            raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation graph contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        direct_profiles: list[SourceTaintProfile] = []
        for profile_id in node.source_profile_ids:
            profile = profile_map.get(profile_id.value)
            if profile is None:
                raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation source profile is missing")
            direct_profiles.append(profile)
            reachable_profiles.add(profile_id.value)
        direct_derivations: list[TaintDerivationRecord] = []
        for derivation_id in node.source_derivation_ids:
            parent = node_map.get(derivation_id.value)
            if parent is None:
                raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation parent is missing")
            direct_derivations.append(parent)
            visit(parent)
        validate_taint_derivation(
            node,
            source_profiles=tuple(direct_profiles),
            source_derivations=tuple(direct_derivations),
        )
        visiting.remove(node_id)
        visited.add(node_id)

    visit(root_derivation)
    if visited != set(node_map) or reachable_profiles != set(profile_map):
        raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "derivation closure contains a disconnected node")
    return tuple(root_derivation.effective_taint_classes)


def reconstruct_effective_taint(
    *,
    authority_handle: Stage4AuthorityHandle,
    root_basis: SourceTaintProfile | TaintDerivationRecord,
    source_profiles: object = (),
    derivations: object = (),
    decisions: object,
) -> EffectiveTaint:
    configuration = _handle(authority_handle)
    if type(root_basis) is SourceTaintProfile:
        validate_source_taint_profile(root_basis, expected_configuration_id=configuration.configuration_id)
        current = tuple(root_basis.taint_classes)
        basis_id = root_basis.profile_id
        basis_hash = _basis_sha256(root_basis)
        if source_profiles not in ((), [], (root_basis,), [root_basis]) or derivations not in ((), []):
            raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "source-profile basis received an ambiguous closure")
    elif type(root_basis) is TaintDerivationRecord:
        current = _reconstruct_derivation_closure(
            authority_handle=authority_handle,
            source_profiles=source_profiles,
            derivations=derivations,
            root_derivation=root_basis,
        )
        basis_id = root_basis.derivation_id
        basis_hash = _basis_sha256(root_basis)
    else:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "root taint basis is invalid")
    if type(decisions) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "decisions must be tuple or list")
    predecessor: str | None = None
    expected_sequence = 1
    quarantined = False
    for decision in decisions:
        validate_taint_authority_decision(decision, authority_handle=authority_handle)
        proposal = decision.proposal
        if proposal.current_profile_id != basis_id or proposal.current_profile_sha256 != basis_hash:
            raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "decision belongs to another taint basis")
        if proposal.current_taint_classes != current:
            raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision current taint does not match reconstructed state")
        if proposal.predecessor_decision_id != predecessor or proposal.decision_sequence != expected_sequence:
            raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision predecessor or sequence is invalid")
        current = decision.effective_taint_classes
        quarantined = quarantined or decision.decision_kind is TaintDecisionKind.QUARANTINE
        predecessor = decision.decision_id.record_id.value
        expected_sequence += 1
    return EffectiveTaint(current, quarantined, predecessor, expected_sequence - 1)


def require_taint_consumable(
    *,
    authority_handle: Stage4AuthorityHandle,
    root_basis: SourceTaintProfile | TaintDerivationRecord,
    source_profiles: object = (),
    derivations: object = (),
    decisions: object,
    history_store: TaintHistoryStore,
) -> EffectiveTaint:
    if type(history_store) is not TaintHistoryStore:
        raise _fail(
            TaintFailureCode.HISTORY_ANCHOR_REQUIRED,
            "taint consumption requires an exact anchored history store",
        )
    history_store.require_handle(authority_handle)
    result = reconstruct_effective_taint(
        authority_handle=authority_handle,
        root_basis=root_basis,
        source_profiles=source_profiles,
        derivations=derivations,
        decisions=decisions,
    )
    history_store.require_complete_history(
        authority_handle=authority_handle,
        root_basis=root_basis,
        source_profiles=source_profiles,
        derivations=derivations,
        decisions=decisions,
    )
    if result.quarantined:
        raise _fail(TaintFailureCode.STICKY_QUARANTINE, "subject taint history is quarantined")
    return result


_TAINT_ENTRY_FIELDS = ("kind", "configuration_id", "subject", "entry_id", "payload")


def _transport_record_text(value: object) -> str | None:
    if type(value) is not dict:
        return None
    domain = value.get("domain")
    digest = value.get("digest_sha256")
    if type(domain) is not str or type(digest) is not str:
        return None
    return f"{domain}:{digest}"


def _taint_entry_metadata(payload: bytes, configuration_id: RecordId) -> tuple[str, str, str, str, str | None, int | None]:
    try:
        raw = _exact_dict(_decode(payload), _TAINT_ENTRY_FIELDS, "taint_history_entry")
        kind = raw["kind"]
        if kind not in ("SOURCE_PROFILE", "DERIVATION", "AUTHORITY_DECISION"):
            raise _fail(TaintFailureCode.JOURNAL_CORRUPT, "taint history entry kind is unknown")
        if raw["configuration_id"] != configuration_id.to_dict():
            raise _fail(TaintFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "taint history configuration differs")
        subject = raw["subject"]
        if type(subject) is not str or not subject or len(subject) > 512 or "\x00" in subject:
            raise _fail(TaintFailureCode.INVALID_IDENTIFIER, "taint history subject is invalid")
        entry_id = raw["entry_id"]
        if type(entry_id) is not str or not entry_id or len(entry_id) > 256:
            raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "taint history entry identity is invalid")
        entry_payload = raw["payload"]
        if type(entry_payload) is not dict:
            raise _fail(TaintFailureCode.JOURNAL_CORRUPT, "taint history payload is invalid")
        if kind == "SOURCE_PROFILE":
            nested_id = _transport_record_text(entry_payload.get("profile_id"))
            predecessor, sequence = None, None
        elif kind == "DERIVATION":
            nested_id = _transport_record_text(entry_payload.get("derivation_id"))
            predecessor, sequence = None, None
        else:
            decision_raw = entry_payload.get("decision_id")
            record_raw = decision_raw.get("record_id") if type(decision_raw) is dict else None
            nested_id = _transport_record_text(record_raw)
            predecessor = entry_payload.get("predecessor_decision_id")
            sequence = entry_payload.get("decision_sequence")
            if predecessor is not None and type(predecessor) is not str:
                raise _fail(TaintFailureCode.AUTHORITY_HISTORY_FORK, "taint decision predecessor is invalid")
            if type(sequence) is not int or sequence < 1:
                raise _fail(TaintFailureCode.AUTHORITY_HISTORY_FORK, "taint decision sequence is invalid")
        if nested_id != entry_id:
            raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "taint history wrapper identity differs from payload")
        return kind, subject, entry_id, hashlib.sha256(payload).hexdigest(), predecessor, sequence
    except TaintViolation:
        raise
    except Exception as exc:
        raise _fail(TaintFailureCode.JOURNAL_CORRUPT, "taint history entry is malformed") from exc


def _validate_taint_entry_history(entries: tuple[tuple[str, str, str, str, str | None, int | None], ...]) -> None:
    seen: set[str] = set()
    decision_heads: dict[str, tuple[str, int]] = {}
    for kind, subject, entry_id, _, predecessor, sequence in entries:
        if entry_id in seen:
            raise _fail(TaintFailureCode.AUTHORITY_HISTORY_FORK, "taint history identity is duplicated")
        seen.add(entry_id)
        if kind == "AUTHORITY_DECISION":
            previous = decision_heads.get(subject)
            expected_predecessor = None if previous is None else previous[0]
            expected_sequence = 1 if previous is None else previous[1] + 1
            if predecessor != expected_predecessor or sequence != expected_sequence:
                raise _fail(TaintFailureCode.AUTHORITY_HISTORY_FORK, "taint authority history has a fork")
            decision_heads[subject] = (entry_id, sequence)


def _taint_heads(entries: tuple[tuple[str, str, str, str, str | None, int | None], ...]) -> tuple[str, ...]:
    heads: dict[tuple[str, str], str] = {}
    for kind, subject, entry_id, _, _, _ in entries:
        heads[(kind, subject)] = entry_id
    return tuple(f"{kind}|{subject}|{entry_id}" for (kind, subject), entry_id in sorted(heads.items()))


class TaintHistoryStore:
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _TRUSTED_STORE_SEAL or kwargs or len(args) != 4:
            raise TypeError("TaintHistoryStore is opened only by open_taint_history_store")
        root, authority_handle, trusted_anchor, allow_genesis = args
        if not isinstance(root, Path) or type(allow_genesis) is not bool:
            raise _fail(TaintFailureCode.TYPE_MISMATCH, "taint store configuration is invalid")
        self._root = root
        self._authority_handle = authority_handle
        self._configuration_id = _handle(authority_handle).configuration_id
        self._trusted_anchor = None
        ensure_directory(root)
        initialize_journal(self._journal_path)
        entries = self._entries()
        if entries and trusted_anchor is None:
            raise _fail(TaintFailureCode.HISTORY_ANCHOR_REQUIRED, "non-empty taint history requires a trusted anchor")
        if not entries and trusted_anchor is None and not allow_genesis:
            raise _fail(TaintFailureCode.HISTORY_ANCHOR_REQUIRED, "empty taint history requires explicit genesis")
        self._trusted_anchor = trusted_anchor
        if trusted_anchor is not None:
            self._validate_anchor(entries, trusted_anchor)

    @property
    def _journal_path(self) -> Path:
        return self._root / TAINT_HISTORY_JOURNAL_NAME_V1

    @property
    def _lock_path(self) -> Path:
        return self._root / TAINT_HISTORY_LOCK_NAME_V1

    def require_handle(self, authority_handle: Stage4AuthorityHandle) -> None:
        _handle(authority_handle, expected=self._authority_handle)

    def _entries(self):
        scan = scan_journal(self._journal_path)
        if scan.torn_tail:
            raise _fail(TaintFailureCode.JOURNAL_CORRUPT, "taint journal has a torn tail")
        entries = tuple(_taint_entry_metadata(frame.payload, self._configuration_id) for frame in scan.frames)
        _validate_taint_entry_history(entries)
        if self._trusted_anchor is not None:
            self._validate_anchor(entries, self._trusted_anchor)
        return entries

    def _validate_anchor(self, entries, anchor: HistoryAnchor) -> None:
        try:
            prefix = entries[: anchor.entry_count]
            validate_history_anchor_extension(
                trusted_anchor=anchor,
                history_domain=HistoryDomain.TAINT,
                configuration_id=self._configuration_id,
                entry_sha256s=tuple(item[3] for item in entries),
                prefix_domain_heads=_taint_heads(prefix),
            )
        except ContractViolation as exc:
            raise _fail(TaintFailureCode.HISTORY_ROLLBACK, "taint history is not an exact trusted extension") from exc

    def current_anchor(self) -> HistoryAnchor:
        entries = self._entries()
        return create_history_anchor(
            history_domain=HistoryDomain.TAINT,
            configuration_id=self._configuration_id,
            entry_sha256s=tuple(item[3] for item in entries),
            domain_heads=_taint_heads(entries),
        )

    def _append(self, *, authority_handle: Stage4AuthorityHandle, kind: str, subject: str, entry_id: str, payload: dict[str, object]) -> HistoryAnchor:
        self.require_handle(authority_handle)
        wrapper = {
            "kind": kind,
            "configuration_id": self._configuration_id.to_dict(),
            "subject": subject,
            "entry_id": entry_id,
            "payload": payload,
        }
        with ExclusiveStoreLock(self._lock_path):
            entries = self._entries()
            if entry_id in {item[2] for item in entries}:
                raise _fail(TaintFailureCode.AUTHORITY_HISTORY_FORK, "taint history identity already exists")
            candidate = (*entries, _taint_entry_metadata(_canonical(wrapper), self._configuration_id))
            _validate_taint_entry_history(candidate)
            append_journal_payload(self._journal_path, _canonical(wrapper))
            anchor = self.current_anchor()
            self._trusted_anchor = anchor
            return anchor

    def append_profile(self, *, authority_handle: Stage4AuthorityHandle, profile: SourceTaintProfile) -> HistoryAnchor:
        configuration = _handle(authority_handle, expected=self._authority_handle)
        validate_source_taint_profile(
            profile,
            expected_configuration_id=configuration.configuration_id,
            expected_classifier_identity=configuration.taint_classifier_authority,
        )
        return self._append(
            authority_handle=authority_handle,
            kind="SOURCE_PROFILE",
            subject=f"{profile.subject_ref.ref_id}:{profile.subject_ref.sha256}",
            entry_id=profile.profile_id.value,
            payload=profile.to_dict(),
        )

    def append_derivation(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        derivation: TaintDerivationRecord,
        source_profiles: object,
        source_derivations: object = (),
    ) -> HistoryAnchor:
        self.require_handle(authority_handle)
        validate_taint_derivation(derivation, source_profiles=source_profiles, source_derivations=source_derivations)
        return self._append(
            authority_handle=authority_handle,
            kind="DERIVATION",
            subject=f"{derivation.subject_ref.ref_id}:{derivation.subject_ref.sha256}",
            entry_id=derivation.derivation_id.value,
            payload=derivation.to_dict(source_profiles=source_profiles, source_derivations=source_derivations),
        )

    def append_decision(self, *, authority_handle: Stage4AuthorityHandle, decision: TaintAuthorityDecision) -> HistoryAnchor:
        validate_taint_authority_decision(decision, authority_handle=authority_handle)
        return self._append(
            authority_handle=authority_handle,
            kind="AUTHORITY_DECISION",
            subject=f"{decision.proposal.subject_ref.ref_id}:{decision.proposal.subject_ref.sha256}",
            entry_id=decision.decision_id.record_id.value,
            payload=decision.to_dict(),
        )

    def require_complete_history(
        self,
        *,
        authority_handle: Stage4AuthorityHandle,
        root_basis: SourceTaintProfile | TaintDerivationRecord,
        source_profiles: object,
        derivations: object,
        decisions: object,
    ) -> None:
        self.require_handle(authority_handle)
        if self._trusted_anchor is None:
            raise _fail(
                TaintFailureCode.HISTORY_ANCHOR_REQUIRED,
                "taint consumption requires an externally anchored history",
            )
        entries = self._entries()
        present = {item[2] for item in entries}
        required: set[str] = set()
        if type(root_basis) is SourceTaintProfile:
            required.add(root_basis.profile_id.value)
            subject_ref = root_basis.subject_ref
        elif type(root_basis) is TaintDerivationRecord:
            required.add(root_basis.derivation_id.value)
            subject_ref = root_basis.subject_ref
        else:
            raise _fail(TaintFailureCode.TYPE_MISMATCH, "root taint basis is invalid")
        profiles = tuple(source_profiles)
        derivation_items = tuple(derivations)
        decision_items = tuple(decisions)
        required.update(item.profile_id.value for item in profiles)
        required.update(item.derivation_id.value for item in derivation_items)
        if not required <= present:
            raise _fail(TaintFailureCode.DERIVATION_CHAIN_INCOMPLETE, "anchored taint history omits a required node")
        subject = f"{subject_ref.ref_id}:{subject_ref.sha256}"
        authoritative_chain = tuple(
            (entry_id, predecessor, sequence)
            for kind, entry_subject, entry_id, _, predecessor, sequence in entries
            if kind == "AUTHORITY_DECISION" and entry_subject == subject
        )
        supplied_chain = tuple(
            (
                decision.decision_id.record_id.value,
                decision.proposal.predecessor_decision_id,
                decision.proposal.decision_sequence,
            )
            for decision in decision_items
        )
        if len(supplied_chain) < len(authoritative_chain) and authoritative_chain[: len(supplied_chain)] == supplied_chain:
            raise _fail(
                TaintFailureCode.HISTORY_ROLLBACK,
                "supplied taint authority history omits the current anchored tail",
            )
        if supplied_chain != authoritative_chain:
            raise _fail(
                TaintFailureCode.AUTHORITY_HISTORY_FORK,
                "supplied taint authority history differs from the anchored current chain",
            )


def open_taint_history_store(
    *,
    root: Path,
    authority_handle: Stage4AuthorityHandle,
    trusted_anchor: HistoryAnchor | None = None,
    allow_genesis: bool = False,
) -> TaintHistoryStore:
    _handle(authority_handle)
    return TaintHistoryStore(root, authority_handle, trusted_anchor, allow_genesis, _seal=_TRUSTED_STORE_SEAL)


__all__ = (
    "TAINT_AUTHORITY_PROPOSAL_V1", "TAINT_REMOVAL_VERIFICATION_V1", "TaintFailureCode",
    "TaintViolation", "TaintClass", "TaintDecisionKind", "TaintBasisKind", "TaintRemovalReason", "TaintDecisionReason",
    "SourceTaintProfile", "classify_source_taint", "validate_source_taint_profile",
    "source_taint_profile_from_dict", "ConfiguredTaintClassifier", "configure_taint_classifier",
    "TaintDerivationRecord", "create_taint_derivation",
    "validate_taint_derivation", "taint_derivation_from_dict", "TaintChangeProposal", "create_taint_change_proposal",
    "validate_taint_change_proposal", "taint_change_proposal_from_dict", "TaintRemovalVerification", "TaintAuthorityDecision",
    "ConfiguredTaintAuthorityEvaluator", "configure_taint_authority_evaluator",
    "create_taint_authority_decision", "validate_taint_authority_decision",
    "taint_authority_decision_from_dict", "EffectiveTaint", "reconstruct_effective_taint",
    "require_taint_consumable", "TaintHistoryStore", "open_taint_history_store",
)
