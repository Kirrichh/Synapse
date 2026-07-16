"""Monotone source-taint contracts and independent relaxation authority.

Source classification and derivation only retain or add taint.  Removing a
class requires a proposal, per-class verification evidence, and an independent
authority decision bound to the proposal.  Success, summarisation, hashing,
distillation, or reformatting never remove taint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    AuthorityDecisionId,
    AuthorityIdentity,
    AuthorityRole,
    IdentityDomain,
    IndependenceProof,
    ProposalId,
    RecordId,
    SchemaVersion,
    authority_decision_id_from_dict,
    compute_authority_decision_id,
    compute_proposal_id,
    compute_record_id,
    independence_proof_from_dict,
    record_id_from_dict,
    validate_independence_proof,
    validate_record_id,
)


TAINT_AUTHORITY_PROPOSAL_V1 = "synapse.stage4.gold.taint-authority-proposal/v1"
TAINT_REMOVAL_VERIFICATION_V1 = "synapse.stage4.gold.taint-removal-verification/v1"
TAINT_PROFILE_MEDIA_TYPE_V1 = "application/vnd.synapse.stage4.taint-profile+json"
_AUTHORITY_ID_PREFIX = IdentityDomain.AUTHORITY_DECISION.value + ":"
_TRUSTED_SEAL = object()
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


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


class TaintRemovalReason(str, Enum):
    VERIFIED_NOT_INSTRUCTION = "VERIFIED_NOT_INSTRUCTION"
    VERIFIED_NOT_SECRET = "VERIFIED_NOT_SECRET"
    VERIFIED_NOT_EXECUTABLE = "VERIFIED_NOT_EXECUTABLE"
    VERIFIED_TRUSTED_SOURCE = "VERIFIED_TRUSTED_SOURCE"
    VERIFIED_CLAIM = "VERIFIED_CLAIM"
    POLICY_AUTHORIZED_RELAXATION = "POLICY_AUTHORIZED_RELAXATION"


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


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
    def from_dict(cls, value: object, *, expected_subject_ref: HashBoundRef) -> SourceTaintProfile:
        return source_taint_profile_from_dict(value, expected_subject_ref=expected_subject_ref)


def _profile_payload(value: SourceTaintProfile) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "subject_ref": value.subject_ref.to_dict(),
        "taint_classes": [item.value for item in value.taint_classes],
        "classifier_identity": value.classifier_identity.to_dict(),
        "producer_actor_ids": [item.to_dict() for item in value.producer_actor_ids],
        "source_actor_ids": [item.to_dict() for item in value.source_actor_ids],
        "admission_actor_ids": [item.to_dict() for item in value.admission_actor_ids],
        "consumer_actor_ids": [item.to_dict() for item in value.consumer_actor_ids],
    }


def classify_source_taint(
    *,
    subject_ref: HashBoundRef,
    taint_classes: object,
    classifier_identity: AuthorityIdentity,
    producer_actor_ids: object,
    source_actor_ids: object,
    admission_actor_ids: object,
    consumer_actor_ids: object,
) -> SourceTaintProfile:
    subject = HashBoundRef.from_dict(subject_ref.to_dict()) if type(subject_ref) is HashBoundRef else None
    if subject is None:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "subject_ref must be HashBoundRef")
    classes = _classes(taint_classes, nonempty=True, name="taint_classes")
    classifier = _authority(classifier_identity)
    producers = _actors(producer_actor_ids, "producer_actor_ids", nonempty=True)
    sources = _actors(source_actor_ids, "source_actor_ids", nonempty=True)
    admissions = _actors(admission_actor_ids, "admission_actor_ids", nonempty=False)
    consumers = _actors(consumer_actor_ids, "consumer_actor_ids", nonempty=False)
    participants = {item.value for group in (producers, sources, admissions, consumers) for item in group}
    if classifier.value in participants:
        raise _fail(TaintFailureCode.CLASSIFIER_NOT_INDEPENDENT, "classifier must be separate from producer, source, admission, and consumer")
    result = object.__new__(SourceTaintProfile)
    object.__setattr__(result, "schema_version", SchemaVersion.TAINT_PROFILE_V1)
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


def validate_source_taint_profile(value: SourceTaintProfile, *, expected_subject_ref: HashBoundRef | None = None) -> None:
    if type(value) is not SourceTaintProfile or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint profile is not factory sealed")
    if value.schema_version is not SchemaVersion.TAINT_PROFILE_V1:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint profile schema is unknown")
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


def source_taint_profile_from_dict(value: object, *, expected_subject_ref: HashBoundRef) -> SourceTaintProfile:
    fields = (
        "schema_version", "subject_ref", "taint_classes", "classifier_identity", "producer_actor_ids",
        "source_actor_ids", "admission_actor_ids", "consumer_actor_ids", "profile_id",
    )
    data = _exact_dict(value, fields, "source_taint_profile")
    if data["schema_version"] != SchemaVersion.TAINT_PROFILE_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint profile schema is unknown")
    subject = HashBoundRef.from_dict(data["subject_ref"])
    if subject != HashBoundRef.from_dict(expected_subject_ref.to_dict()):
        raise _fail(TaintFailureCode.SUBJECT_MISMATCH, "transport subject differs from consumer subject")
    result = classify_source_taint(
        subject_ref=subject,
        taint_classes=_classes_from_transport(data["taint_classes"], "taint_classes", nonempty=True),
        classifier_identity=AuthorityIdentity.from_dict(data["classifier_identity"]),
        producer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["producer_actor_ids"], "producer_actor_ids")),
        source_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["source_actor_ids"], "source_actor_ids")),
        admission_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["admission_actor_ids"], "admission_actor_ids")),
        consumer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in _exact_list(data["consumer_actor_ids"], "consumer_actor_ids")),
    )
    supplied = _record_id(data["profile_id"], IdentityDomain.TAINT_PROFILE, _canonical(_profile_payload(result)), "profile_id")
    if supplied != result.profile_id:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "profile identity changed during parsing")
    return result


@dataclass(frozen=True, init=False)
class TaintDerivationRecord:
    schema_version: SchemaVersion
    subject_ref: HashBoundRef
    source_profile_ids: tuple[RecordId, ...]
    transformation_actor: ActorIdentity
    transformation_labels: tuple[TaintClass, ...]
    effective_taint_classes: tuple[TaintClass, ...]
    derivation_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> TaintDerivationRecord:
        raise TypeError("TaintDerivationRecord is created only from validated source profiles")

    def to_dict(self, *, source_profiles: object) -> dict[str, object]:
        validate_taint_derivation(self, source_profiles=source_profiles)
        return {**_derivation_payload(self), "derivation_id": self.derivation_id.to_dict()}

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        source_profiles: object,
        expected_subject_ref: HashBoundRef,
    ) -> TaintDerivationRecord:
        return taint_derivation_from_dict(
            value,
            source_profiles=source_profiles,
            expected_subject_ref=expected_subject_ref,
        )


def _derivation_payload(value: TaintDerivationRecord) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "subject_ref": value.subject_ref.to_dict(),
        "source_profile_ids": [item.to_dict() for item in value.source_profile_ids],
        "transformation_actor": value.transformation_actor.to_dict(),
        "transformation_labels": [item.value for item in value.transformation_labels],
        "effective_taint_classes": [item.value for item in value.effective_taint_classes],
    }


def create_taint_derivation(
    *,
    subject_ref: HashBoundRef,
    source_profiles: object,
    transformation_actor: ActorIdentity,
    transformation_labels: object,
) -> TaintDerivationRecord:
    if type(source_profiles) not in (tuple, list) or not source_profiles:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "source_profiles must be non-empty")
    profiles = tuple(source_profiles)
    for profile in profiles:
        validate_source_taint_profile(profile)
    if len({profile.profile_id.value for profile in profiles}) != len(profiles):
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "source_profiles contains duplicates")
    labels = _classes(transformation_labels, nonempty=False, name="transformation_labels")
    effective = _classes(
        {item for profile in profiles for item in profile.taint_classes} | set(labels),
        nonempty=True,
        name="effective_taint_classes",
    )
    if type(subject_ref) is not HashBoundRef:
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "subject_ref must be HashBoundRef")
    result = object.__new__(TaintDerivationRecord)
    object.__setattr__(result, "schema_version", SchemaVersion.TAINT_DERIVATION_V1)
    object.__setattr__(result, "subject_ref", HashBoundRef.from_dict(subject_ref.to_dict()))
    object.__setattr__(result, "source_profile_ids", tuple(sorted((profile.profile_id for profile in profiles), key=lambda item: item.value)))
    object.__setattr__(result, "transformation_actor", _actor(transformation_actor, "transformation_actor"))
    object.__setattr__(result, "transformation_labels", labels)
    object.__setattr__(result, "effective_taint_classes", effective)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    payload = _canonical(_derivation_payload(result))
    object.__setattr__(result, "derivation_id", compute_record_id(domain=IdentityDomain.TAINT_DERIVATION, canonical_bytes=payload))
    validate_taint_derivation(result, source_profiles=profiles)
    return result


def validate_taint_derivation(value: TaintDerivationRecord, *, source_profiles: object) -> None:
    if type(value) is not TaintDerivationRecord or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint derivation is not factory sealed")
    if value.schema_version is not SchemaVersion.TAINT_DERIVATION_V1:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint derivation schema is unknown")
    if type(source_profiles) not in (tuple, list) or not source_profiles:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "source_profiles must be non-empty")
    profiles = tuple(source_profiles)
    for profile in profiles:
        validate_source_taint_profile(profile)
    expected_ids = tuple(sorted((profile.profile_id for profile in profiles), key=lambda item: item.value))
    if value.source_profile_ids != expected_ids:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "derivation source identities changed")
    HashBoundRef.from_dict(value.subject_ref.to_dict())
    _actor(value.transformation_actor, "transformation_actor")
    labels = _classes(value.transformation_labels, nonempty=False, name="transformation_labels")
    expected = _classes({item for profile in profiles for item in profile.taint_classes} | set(labels), nonempty=True, name="effective_taint_classes")
    actual = _classes(value.effective_taint_classes, nonempty=True, name="effective_taint_classes")
    if actual != expected:
        raise _fail(TaintFailureCode.TAINT_REMOVAL_FORBIDDEN, "derivation failed to retain the monotone source union")
    payload = _canonical(_derivation_payload(value))
    if type(value.derivation_id) is not RecordId or value.derivation_id.domain is not IdentityDomain.TAINT_DERIVATION:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity domain is invalid")
    try:
        validate_record_id(value.derivation_id, canonical_bytes=payload)
    except ValueError as exc:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity does not match content") from exc


def taint_derivation_from_dict(
    value: object,
    *,
    source_profiles: object,
    expected_subject_ref: HashBoundRef,
) -> TaintDerivationRecord:
    fields = (
        "schema_version", "subject_ref", "source_profile_ids", "transformation_actor",
        "transformation_labels", "effective_taint_classes", "derivation_id",
    )
    data = _exact_dict(value, fields, "taint_derivation")
    if data["schema_version"] != SchemaVersion.TAINT_DERIVATION_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint derivation schema is unknown")
    subject = HashBoundRef.from_dict(data["subject_ref"])
    if subject != HashBoundRef.from_dict(expected_subject_ref.to_dict()):
        raise _fail(TaintFailureCode.SUBJECT_MISMATCH, "derivation subject differs from consumer subject")
    result = create_taint_derivation(
        subject_ref=subject,
        source_profiles=source_profiles,
        transformation_actor=ActorIdentity.from_dict(data["transformation_actor"]),
        transformation_labels=_classes_from_transport(data["transformation_labels"], "transformation_labels", nonempty=False),
    )
    if data["source_profile_ids"] != [item.to_dict() for item in result.source_profile_ids]:
        raise _fail(TaintFailureCode.DERIVATION_SOURCE_MISMATCH, "transport source profile identities changed")
    if data["effective_taint_classes"] != [item.value for item in result.effective_taint_classes]:
        raise _fail(TaintFailureCode.TAINT_REMOVAL_FORBIDDEN, "transport derivation removed or changed taint")
    supplied = _record_id(data["derivation_id"], IdentityDomain.TAINT_DERIVATION, _canonical(_derivation_payload(result)), "derivation_id")
    if supplied != result.derivation_id:
        raise _fail(TaintFailureCode.IDENTITY_MISMATCH, "derivation identity changed during parsing")
    return result


@dataclass(frozen=True, init=False)
class TaintChangeProposal:
    schema_version: str
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
    def from_dict(cls, value: object, *, current_profile: SourceTaintProfile) -> TaintChangeProposal:
        return taint_change_proposal_from_dict(value, current_profile=current_profile)


def _profile_sha256(value: SourceTaintProfile) -> str:
    validate_source_taint_profile(value)
    return hashlib.sha256(_canonical(value.to_dict())).hexdigest()


def _proposal_payload(value: TaintChangeProposal) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
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
    current_profile: SourceTaintProfile,
    proposed_taint_classes: object,
    proposer_identity: ActorIdentity,
    predecessor_decision_id: str | None,
    decision_sequence: int,
    current_effective_taint_classes: object | None = None,
) -> TaintChangeProposal:
    validate_source_taint_profile(current_profile)
    proposed = _classes(proposed_taint_classes, nonempty=False, name="proposed_taint_classes")
    current_effective = (
        tuple(current_profile.taint_classes)
        if current_effective_taint_classes is None
        else _classes(current_effective_taint_classes, nonempty=False, name="current_effective_taint_classes")
    )
    if type(decision_sequence) is not int or decision_sequence < 1:
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision_sequence must be a positive exact integer")
    predecessor = _decision_id_text(predecessor_decision_id, "predecessor_decision_id", allow_none=True)
    if (decision_sequence == 1) != (predecessor is None):
        raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "first decision alone has no predecessor")
    source_actors = _actors(
        (*current_profile.source_actor_ids, ActorIdentity(current_profile.classifier_identity.value)),
        "proposal.source_actor_ids",
        nonempty=True,
    )
    result = object.__new__(TaintChangeProposal)
    object.__setattr__(result, "schema_version", TAINT_AUTHORITY_PROPOSAL_V1)
    object.__setattr__(result, "subject_ref", HashBoundRef.from_dict(current_profile.subject_ref.to_dict()))
    object.__setattr__(result, "current_profile_id", current_profile.profile_id)
    object.__setattr__(result, "current_profile_sha256", _profile_sha256(current_profile))
    object.__setattr__(result, "current_taint_classes", current_effective)
    object.__setattr__(result, "proposed_taint_classes", proposed)
    object.__setattr__(result, "producer_actor_ids", tuple(current_profile.producer_actor_ids))
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
    HashBoundRef.from_dict(value.subject_ref.to_dict())
    if type(value.current_profile_id) is not RecordId or value.current_profile_id.domain is not IdentityDomain.TAINT_PROFILE:
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
        validate_source_taint_profile(current_profile)
        if (
            value.subject_ref != current_profile.subject_ref
            or value.current_profile_id != current_profile.profile_id
            or value.current_profile_sha256 != _profile_sha256(current_profile)
        ):
            raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "proposal does not bind the current profile")


def taint_change_proposal_from_dict(value: object, *, current_profile: SourceTaintProfile) -> TaintChangeProposal:
    fields = (
        "schema_version", "subject_ref", "current_profile_id", "current_profile_sha256",
        "current_taint_classes", "proposed_taint_classes", "producer_actor_ids", "source_actor_ids",
        "proposer_identity", "predecessor_decision_id", "decision_sequence", "proposal_id",
    )
    data = _exact_dict(value, fields, "taint_change_proposal")
    if data["schema_version"] != TAINT_AUTHORITY_PROPOSAL_V1 or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint proposal schema is unknown")
    result = create_taint_change_proposal(
        current_profile=current_profile,
        current_effective_taint_classes=_classes_from_transport(data["current_taint_classes"], "current_taint_classes", nonempty=False),
        proposed_taint_classes=_classes_from_transport(data["proposed_taint_classes"], "proposed_taint_classes", nonempty=False),
        proposer_identity=ActorIdentity.from_dict(data["proposer_identity"]),
        predecessor_decision_id=data["predecessor_decision_id"],
        decision_sequence=data["decision_sequence"],
    )
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
    decision_kind: TaintDecisionKind
    proposal: TaintChangeProposal
    effective_taint_classes: tuple[TaintClass, ...]
    removal_verifications: tuple[TaintRemovalVerification, ...]
    policy_refs: tuple[HashBoundRef, ...]
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
    def from_dict(cls, value: object, *, current_profile: SourceTaintProfile) -> TaintAuthorityDecision:
        return taint_authority_decision_from_dict(value, current_profile=current_profile)


def _decision_payload(value: TaintAuthorityDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "decision_kind": value.decision_kind.value,
        "proposal_id": value.proposal.proposal_id.to_dict(),
        "effective_taint_classes": [item.value for item in value.effective_taint_classes],
        "removal_verifications": [item.to_dict() for item in value.removal_verifications],
        "policy_refs": [item.to_dict() for item in value.policy_refs],
        "predecessor_decision_id": value.proposal.predecessor_decision_id,
        "decision_sequence": value.proposal.decision_sequence,
    }


def create_taint_authority_decision(
    *,
    proposal: TaintChangeProposal,
    decision_kind: TaintDecisionKind,
    removal_verifications: object,
    policy_refs: object,
    independence_proof: IndependenceProof,
) -> TaintAuthorityDecision:
    validate_taint_change_proposal(proposal)
    if type(decision_kind) is not TaintDecisionKind:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "decision kind is unknown")
    if type(removal_verifications) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "removal_verifications must be tuple or list")
    removals = tuple(TaintRemovalVerification.from_dict(item.to_dict()) if type(item) is TaintRemovalVerification else None for item in removal_verifications)
    if any(item is None for item in removals):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "removal verification entry must be exact")
    removal_items = tuple(item for item in removals if item is not None)
    policies = _refs(policy_refs, RefKind.CONTRACT_CONDITION, "policy_refs", nonempty=True)
    validate_independence_proof(independence_proof)
    if independence_proof.authority_role is not AuthorityRole.TAINT_REVIEWER:
        raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "taint decision requires TAINT_REVIEWER")
    if independence_proof.subject_proposal_id != proposal.proposal_id:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "independence proof is bound to another proposal")
    if independence_proof.proposer_identity != proposal.proposer_identity:
        raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "proof proposer differs from proposal")
    if independence_proof.producer_actor_ids != proposal.producer_actor_ids or independence_proof.source_actor_ids != proposal.source_actor_ids:
        raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "proof actor coverage differs from proposal")
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
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "proposal", proposal)
    object.__setattr__(result, "effective_taint_classes", _classes(effective, nonempty=False, name="effective_taint_classes"))
    object.__setattr__(result, "removal_verifications", tuple(sorted(removal_items, key=lambda item: item.taint_class.value)))
    object.__setattr__(result, "policy_refs", policies)
    object.__setattr__(result, "independence_proof", independence_proof)
    object.__setattr__(result, "_trusted_seal", _TRUSTED_SEAL)
    identity_bytes = _canonical(_decision_payload(result))
    object.__setattr__(result, "decision_id", compute_authority_decision_id(canonical_bytes=identity_bytes, independence_proof=independence_proof))
    validate_taint_authority_decision(result)
    return result


def validate_taint_authority_decision(value: TaintAuthorityDecision) -> None:
    if type(value) is not TaintAuthorityDecision or getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _fail(TaintFailureCode.TRUSTED_OBJECT_FORGED, "taint decision is not authority sealed")
    if value.schema_version is not SchemaVersion.TAINT_AUTHORITY_DECISION_V1 or type(value.decision_kind) is not TaintDecisionKind:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint decision schema or kind is unknown")
    validate_taint_change_proposal(value.proposal)
    validate_independence_proof(value.independence_proof)
    if value.independence_proof.authority_role is not AuthorityRole.TAINT_REVIEWER or value.independence_proof.subject_proposal_id != value.proposal.proposal_id:
        raise _fail(TaintFailureCode.AUTHORITY_NOT_INDEPENDENT, "taint decision proof is invalid")
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


def taint_authority_decision_from_dict(value: object, *, current_profile: SourceTaintProfile) -> TaintAuthorityDecision:
    fields = (
        "schema_version", "decision_kind", "proposal_id", "effective_taint_classes",
        "removal_verifications", "policy_refs", "predecessor_decision_id", "decision_sequence",
        "proposal", "independence_proof", "decision_id",
    )
    data = _exact_dict(value, fields, "taint_authority_decision")
    if data["schema_version"] != SchemaVersion.TAINT_AUTHORITY_DECISION_V1.value or type(data["schema_version"]) is not str:
        raise _fail(TaintFailureCode.UNKNOWN_SCHEMA_VERSION, "taint decision schema is unknown")
    try:
        kind = TaintDecisionKind(data["decision_kind"])
    except (TypeError, ValueError) as exc:
        raise _fail(TaintFailureCode.DECISION_KIND_MISMATCH, "taint decision kind is unknown") from exc
    proposal = taint_change_proposal_from_dict(data["proposal"], current_profile=current_profile)
    proposal_bytes = _canonical(_proposal_payload(proposal))
    proof = independence_proof_from_dict(data["independence_proof"], proposal_canonical_bytes=proposal_bytes)
    result = create_taint_authority_decision(
        proposal=proposal,
        decision_kind=kind,
        removal_verifications=tuple(TaintRemovalVerification.from_dict(item) for item in _exact_list(data["removal_verifications"], "removal_verifications")),
        policy_refs=tuple(HashBoundRef.from_dict(item) for item in _exact_list(data["policy_refs"], "policy_refs")),
        independence_proof=proof,
    )
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


def reconstruct_effective_taint(
    *,
    source_profile: SourceTaintProfile,
    decisions: object,
) -> EffectiveTaint:
    validate_source_taint_profile(source_profile)
    if type(decisions) not in (tuple, list):
        raise _fail(TaintFailureCode.TYPE_MISMATCH, "decisions must be tuple or list")
    current = tuple(source_profile.taint_classes)
    predecessor: str | None = None
    expected_sequence = 1
    quarantined = False
    for decision in decisions:
        validate_taint_authority_decision(decision)
        proposal = decision.proposal
        if proposal.current_profile_id != source_profile.profile_id or proposal.current_profile_sha256 != _profile_sha256(source_profile):
            raise _fail(TaintFailureCode.PROPOSAL_MISMATCH, "decision belongs to another source profile")
        if proposal.current_taint_classes != current:
            raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision current taint does not match reconstructed state")
        if proposal.predecessor_decision_id != predecessor or proposal.decision_sequence != expected_sequence:
            raise _fail(TaintFailureCode.DECISION_CHAIN_MISMATCH, "decision predecessor or sequence is invalid")
        current = decision.effective_taint_classes
        quarantined = decision.decision_kind is TaintDecisionKind.QUARANTINE
        predecessor = decision.decision_id.record_id.value
        expected_sequence += 1
    return EffectiveTaint(current, quarantined, predecessor, expected_sequence - 1)


__all__ = (
    "TAINT_AUTHORITY_PROPOSAL_V1", "TAINT_REMOVAL_VERIFICATION_V1", "TaintFailureCode",
    "TaintViolation", "TaintClass", "TaintDecisionKind", "TaintRemovalReason",
    "SourceTaintProfile", "classify_source_taint", "validate_source_taint_profile",
    "source_taint_profile_from_dict", "TaintDerivationRecord", "create_taint_derivation",
    "validate_taint_derivation", "taint_derivation_from_dict", "TaintChangeProposal", "create_taint_change_proposal",
    "validate_taint_change_proposal", "taint_change_proposal_from_dict", "TaintRemovalVerification", "TaintAuthorityDecision",
    "create_taint_authority_decision", "validate_taint_authority_decision",
    "taint_authority_decision_from_dict", "EffectiveTaint", "reconstruct_effective_taint",
)
