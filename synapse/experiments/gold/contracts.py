"""Stage 4 Gold common value, identity, envelope, and authority contracts.

Identity factories in this module consume exact, already-canonical bytes.  This
module deliberately does not define a canonical encoding for domain objects.
It performs no I/O and does not implement the integrated Stage 4 runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import re
from typing import Any


IDENTITY_PROTOCOL_VERSION = "synapse.stage4.record-id/v1"
IDENTITY_PREIMAGE_PREFIX = b"synapse.stage4.record-id/v1\x00"
RECORD_ID_TEXT_SEPARATOR = ":"
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

_AUTHORITY_DECISION_BINDING_PROFILE = (
    "synapse.stage4.gold.authority-decision-proof-binding/v1"
)
_AUTHORITY_DECISION_BINDING_PREFIX = (
    _AUTHORITY_DECISION_BINDING_PROFILE.encode("utf-8") + b"\x00"
)
_IDENTITY_FRAME_LENGTH_BYTES = 8
AUTHORITY_CONFIGURATION_SCHEMA_V1 = "synapse.stage4.gold.authority-configuration/v1"
HISTORY_ANCHOR_SCHEMA_V1 = "synapse.stage4.gold.history-anchor/v1"
AUTHORITY_CONFIGURATION_IDENTITY_PROFILE_V1 = (
    "synapse.stage4.gold.authority-configuration-identity/v1"
)
HISTORY_LOG_ROOT_PROFILE_V1 = "synapse.stage4.gold.history-log-root/v1"
HISTORY_ANCHOR_IDENTITY_PROFILE_V1 = "synapse.stage4.gold.history-anchor-identity/v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_VERSIONED_RE = re.compile(
    r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*/v[1-9][0-9]*\Z"
)
_TRUSTED_SEAL = object()


class ContractFailureCode(str, Enum):
    """Closed registry of common Stage 4 contract failures."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    UNKNOWN_IDENTITY_DOMAIN = "UNKNOWN_IDENTITY_DOMAIN"
    UNKNOWN_AUTHORITY_ROLE = "UNKNOWN_AUTHORITY_ROLE"
    UNKNOWN_REASON_CODE = "UNKNOWN_REASON_CODE"
    MALFORMED_VERSION = "MALFORMED_VERSION"
    MALFORMED_IDENTITY = "MALFORMED_IDENTITY"
    MALFORMED_SHA256 = "MALFORMED_SHA256"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    MALFORMED_REPOSITORY_REVISION = "MALFORMED_REPOSITORY_REVISION"
    PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
    RECORD_ID_MISMATCH = "RECORD_ID_MISMATCH"
    DUPLICATE_LINEAGE_PARENT = "DUPLICATE_LINEAGE_PARENT"
    DUPLICATE_ACTOR = "DUPLICATE_ACTOR"
    AUTHORITY_AMBIGUITY = "AUTHORITY_AMBIGUITY"
    AUTHORITY_DERIVED_FROM_SUBJECT = "AUTHORITY_DERIVED_FROM_SUBJECT"
    UNPROVEN_INDEPENDENCE = "UNPROVEN_INDEPENDENCE"
    DELEGATION_CYCLE = "DELEGATION_CYCLE"
    DELEGATED_BACK_AUTHORITY = "DELEGATED_BACK_AUTHORITY"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    AUTHORITY_CONFIGURATION_MISMATCH = "AUTHORITY_CONFIGURATION_MISMATCH"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    HISTORY_ANCHOR_REQUIRED = "HISTORY_ANCHOR_REQUIRED"
    HISTORY_ROLLBACK = "HISTORY_ROLLBACK"


class ContractViolation(ValueError):
    """A typed, fail-closed contract error with non-payload detail."""

    def __init__(self, failure_code: ContractFailureCode, detail: str) -> None:
        if type(failure_code) is not ContractFailureCode:
            raise TypeError("failure_code must be an exact ContractFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


class SchemaVersion(str, Enum):
    AUTHORITY_CONFIGURATION_V1 = AUTHORITY_CONFIGURATION_SCHEMA_V1
    HISTORY_ANCHOR_V1 = HISTORY_ANCHOR_SCHEMA_V1
    COMMON_ENVELOPE_V1 = "synapse.stage4.gold.common-envelope/v1"
    INDEPENDENCE_PROOF_V1 = "synapse.stage4.gold.independence-proof/v1"
    BEHAVIOR_UNIT_V1 = "synapse.stage4.gold.behavior-unit/v1"
    BEHAVIOR_MANIFEST_V1 = "synapse.stage4.gold.behavior-manifest/v1"
    COMPILER_BINDING_V1 = "synapse.stage4.gold.compiler-binding/v1"
    MIGRATION_RELATION_V1 = "synapse.stage4.gold.migration-relation/v1"
    BEHAVIOR_ATTESTATION_V1 = "synapse.stage4.gold.behavior-attestation/v1"
    TAINT_PROFILE_V1 = "synapse.stage4.gold.taint-profile/v1"
    TAINT_DERIVATION_V1 = "synapse.stage4.gold.taint-derivation/v1"
    TAINT_AUTHORITY_DECISION_V1 = "synapse.stage4.gold.taint-authority-decision/v1"
    LIFECYCLE_RECORD_V1 = "synapse.stage4.gold.lifecycle-record/v1"
    LIFECYCLE_SNAPSHOT_V1 = "synapse.stage4.gold.lifecycle-snapshot/v1"
    SUPERSESSION_AUTHORITY_DECISION_V1 = (
        "synapse.stage4.gold.supersession-authority-decision/v1"
    )
    REVOCATION_AUTHORITY_DECISION_V1 = (
        "synapse.stage4.gold.revocation-authority-decision/v1"
    )


class IdentityDomain(str, Enum):
    AUTHORITY_CONFIGURATION = "synapse.stage4.gold.authority-configuration-record/v1"
    PROVENANCE_HISTORY_ANCHOR = "synapse.stage4.gold.provenance-history-anchor/v1"
    TAINT_HISTORY_ANCHOR = "synapse.stage4.gold.taint-history-anchor/v1"
    LIFECYCLE_HISTORY_ANCHOR = "synapse.stage4.gold.lifecycle-history-anchor/v1"
    COMMON_RECORD = "synapse.stage4.gold.common-record/v1"
    PROPOSAL = "synapse.stage4.gold.proposal/v1"
    AUTHORITY_DECISION = "synapse.stage4.gold.authority-decision/v1"
    EXECUTION = "synapse.stage4.gold.execution/v1"
    BEHAVIOR_MANIFEST = "synapse.stage4.gold.behavior-manifest-record/v1"
    COMPILER_BINDING = "synapse.stage4.gold.compiler-binding-record/v1"
    MIGRATION_RELATION = "synapse.stage4.gold.migration-relation-record/v1"
    BEHAVIOR_ATTESTATION = "synapse.stage4.gold.behavior-attestation-record/v1"
    TAINT_PROFILE = "synapse.stage4.gold.taint-profile-record/v1"
    TAINT_DERIVATION = "synapse.stage4.gold.taint-derivation-record/v1"
    LIFECYCLE_RECORD = "synapse.stage4.gold.lifecycle-record/v1"
    LIFECYCLE_SNAPSHOT = "synapse.stage4.gold.lifecycle-snapshot-record/v1"


class AuthorityRole(str, Enum):
    TAINT_REVIEWER = "TAINT_REVIEWER"
    SUPERSESSION_REVIEWER = "SUPERSESSION_REVIEWER"
    PLAN_REVIEWER = "PLAN_REVIEWER"
    PUBLICATION_REVIEWER = "PUBLICATION_REVIEWER"
    REVOCATION_REVIEWER = "REVOCATION_REVIEWER"
    GOVERNING_HUMAN = "GOVERNING_HUMAN"


class HistoryDomain(str, Enum):
    PROVENANCE = "PROVENANCE"
    TAINT = "TAINT"
    LIFECYCLE = "LIFECYCLE"


class ReasonCode(str, Enum):
    TAINT_REVIEW_INDEPENDENT = "TAINT_REVIEW_INDEPENDENT"
    SUPERSESSION_REVIEW_INDEPENDENT = "SUPERSESSION_REVIEW_INDEPENDENT"
    PLAN_REVIEW_INDEPENDENT = "PLAN_REVIEW_INDEPENDENT"
    PUBLICATION_REVIEW_INDEPENDENT = "PUBLICATION_REVIEW_INDEPENDENT"
    REVOCATION_REVIEW_INDEPENDENT = "REVOCATION_REVIEW_INDEPENDENT"
    GOVERNING_HUMAN_INDEPENDENT = "GOVERNING_HUMAN_INDEPENDENT"


class LifecycleReasonCode(str, Enum):
    """Closed machine-readable reasons for append-only lifecycle records."""

    PLATFORM_OBSERVATION = "PLATFORM_OBSERVATION"
    EXTRACTION_COMPLETED = "EXTRACTION_COMPLETED"
    DISTILLATION_COMPLETED = "DISTILLATION_COMPLETED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    ATTESTATION_BOUND = "ATTESTATION_BOUND"
    PUBLICATION_ADMITTED = "PUBLICATION_ADMITTED"
    INDEX_COMMITTED = "INDEX_COMMITTED"
    RETRIEVAL_SELECTED = "RETRIEVAL_SELECTED"
    REVALIDATION_PASSED = "REVALIDATION_PASSED"
    REPLAY_COMPLETED = "REPLAY_COMPLETED"
    CONSUMPTION_COMPLETED = "CONSUMPTION_COMPLETED"
    OUTCOME_LINKED = "OUTCOME_LINKED"
    POLICY_REJECTED = "POLICY_REJECTED"
    CONFLICT_DETECTED = "CONFLICT_DETECTED"
    SUPERSESSION_APPROVED = "SUPERSESSION_APPROVED"
    WITHDRAWAL_APPROVED = "WITHDRAWAL_APPROVED"
    STALE_CONTEXT = "STALE_CONTEXT"
    REVOCATION_APPROVED = "REVOCATION_APPROVED"
    CONTEXT_INCOMPATIBLE = "CONTEXT_INCOMPATIBLE"
    CORRUPTION_QUARANTINE = "CORRUPTION_QUARANTINE"
    REVALIDATION_RECOVERED = "REVALIDATION_RECOVERED"


class RepositoryRevisionKind(str, Enum):
    GIT_COMMIT = "GIT_COMMIT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LineageEdgeKind(str, Enum):
    DERIVED_FROM = "DERIVED_FROM"
    REFERENCES = "REFERENCES"
    SUPERSEDES = "SUPERSEDES"


_ROLE_REASON_MATRIX = {
    AuthorityRole.TAINT_REVIEWER: ReasonCode.TAINT_REVIEW_INDEPENDENT,
    AuthorityRole.SUPERSESSION_REVIEWER: ReasonCode.SUPERSESSION_REVIEW_INDEPENDENT,
    AuthorityRole.PLAN_REVIEWER: ReasonCode.PLAN_REVIEW_INDEPENDENT,
    AuthorityRole.PUBLICATION_REVIEWER: ReasonCode.PUBLICATION_REVIEW_INDEPENDENT,
    AuthorityRole.REVOCATION_REVIEWER: ReasonCode.REVOCATION_REVIEW_INDEPENDENT,
    AuthorityRole.GOVERNING_HUMAN: ReasonCode.GOVERNING_HUMAN_INDEPENDENT,
}


def _violation(code: ContractFailureCode, detail: str) -> ContractViolation:
    return ContractViolation(code, detail)


def _require_exact_dict(
    value: object,
    *,
    required: tuple[str, ...],
    field_name: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact dict")
    if any(type(key) is not str for key in value):
        raise _violation(ContractFailureCode.UNKNOWN_FIELD, f"{field_name} keys must be exact strings")
    missing = tuple(key for key in required if key not in value)
    if missing:
        raise _violation(
            ContractFailureCode.MISSING_REQUIRED_FIELD,
            f"{field_name} is missing field {missing[0]}",
        )
    unknown = tuple(sorted(key for key in value if key not in required))
    if unknown:
        raise _violation(
            ContractFailureCode.UNKNOWN_FIELD,
            f"{field_name} contains unknown field {unknown[0]}",
        )
    return value


def _require_exact_string(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            f"{field_name} must be a non-empty exact string",
        )
    return value


def _require_identifier(value: object, field_name: str) -> str:
    text = _require_exact_string(value, field_name)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            f"{field_name} has invalid identifier syntax",
        )
    return text


def _require_component(value: object, field_name: str) -> str:
    if type(value) is not str or _COMPONENT_RE.fullmatch(value) is None:
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            f"{field_name} has invalid component syntax",
        )
    return value


def _require_versioned(value: object, field_name: str) -> str:
    if type(value) is not str or _VERSIONED_RE.fullmatch(value) is None:
        raise _violation(
            ContractFailureCode.MALFORMED_VERSION,
            f"{field_name} must be a non-empty versioned identifier",
        )
    return value


def _require_sha256(value: object, field_name: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise _violation(
            ContractFailureCode.MALFORMED_SHA256,
            f"{field_name} must be an exact lowercase SHA-256",
        )
    return value


def _parse_enum(
    value: object,
    enum_type: type[Enum],
    failure_code: ContractFailureCode,
    field_name: str,
) -> Enum:
    if type(value) is not str:
        raise _violation(failure_code, f"{field_name} must be an exact known string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _violation(failure_code, f"{field_name} is unknown") from exc


def _require_exact_bytes(value: object, field_name: str) -> bytes:
    if type(value) is not bytes:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            f"{field_name} must be exact already-canonical bytes",
        )
    return value


def _require_trusted_seal(value: object, field_name: str) -> None:
    if getattr(value, "_trusted_seal", None) is not _TRUSTED_SEAL:
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            f"{field_name} was not created by its trusted factory",
        )


@dataclass(frozen=True)
class ClaimedRecordId:
    """Untrusted identity supplied by a worker or external source."""

    value: str

    def __post_init__(self) -> None:
        _require_exact_string(self.value, "claimed_record_id")

    def to_dict(self) -> dict[str, str]:
        _require_exact_string(self.value, "claimed_record_id")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> ClaimedRecordId:
        data = _require_exact_dict(value, required=("value",), field_name="claimed_record_id")
        return cls(value=data["value"])


@dataclass(frozen=True, init=False)
class RecordId:
    """Trusted platform-computed, domain-separated content identity."""

    domain: IdentityDomain
    digest_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> RecordId:
        raise TypeError("RecordId is created only from exact canonical bytes")

    @property
    def value(self) -> str:
        _validate_record_id_consistency(self)
        return f"{self.domain.value}{RECORD_ID_TEXT_SEPARATOR}{self.digest_sha256}"

    def to_dict(self) -> dict[str, str]:
        _validate_record_id_consistency(self)
        return {"domain": self.domain.value, "digest_sha256": self.digest_sha256}

    @classmethod
    def from_dict(cls, value: object, *, canonical_bytes: bytes) -> RecordId:
        return record_id_from_dict(value, canonical_bytes=canonical_bytes)


def _make_record_id(domain: IdentityDomain, digest_sha256: str) -> RecordId:
    record_id = object.__new__(RecordId)
    object.__setattr__(record_id, "domain", domain)
    object.__setattr__(record_id, "digest_sha256", digest_sha256)
    object.__setattr__(record_id, "_trusted_seal", _TRUSTED_SEAL)
    _validate_record_id_consistency(record_id)
    return record_id


def _validate_record_id_consistency(record_id: RecordId) -> None:
    if type(record_id) is not RecordId:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "record_id must be an exact RecordId")
    _require_trusted_seal(record_id, "record_id")
    if type(record_id.domain) is not IdentityDomain:
        raise _violation(
            ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
            "record_id domain must be an exact IdentityDomain",
        )
    _require_sha256(record_id.digest_sha256, "record_id.digest_sha256")


def compute_payload_sha256(*, canonical_bytes: bytes) -> str:
    payload = _require_exact_bytes(canonical_bytes, "canonical_bytes")
    return hashlib.sha256(payload).hexdigest()


def compute_record_id(*, domain: IdentityDomain, canonical_bytes: bytes) -> RecordId:
    if type(domain) is not IdentityDomain:
        raise _violation(
            ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
            "domain must be an exact IdentityDomain",
        )
    payload = _require_exact_bytes(canonical_bytes, "canonical_bytes")
    preimage = IDENTITY_PREIMAGE_PREFIX + domain.value.encode("utf-8") + b"\x00" + payload
    return _make_record_id(domain, hashlib.sha256(preimage).hexdigest())


def validate_record_id(record_id: RecordId, *, canonical_bytes: bytes) -> None:
    _validate_record_id_consistency(record_id)
    expected = compute_record_id(domain=record_id.domain, canonical_bytes=canonical_bytes)
    if record_id.digest_sha256 != expected.digest_sha256:
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "record_id does not match the supplied exact canonical bytes",
        )


def record_id_from_dict(value: object, *, canonical_bytes: bytes) -> RecordId:
    data = _require_exact_dict(
        value,
        required=("domain", "digest_sha256"),
        field_name="record_id",
    )
    domain = _parse_enum(
        data["domain"],
        IdentityDomain,
        ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
        "record_id.domain",
    )
    assert isinstance(domain, IdentityDomain)
    supplied_digest = _require_sha256(data["digest_sha256"], "record_id.digest_sha256")
    expected = compute_record_id(domain=domain, canonical_bytes=canonical_bytes)
    if supplied_digest != expected.digest_sha256:
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "record_id does not match the supplied exact canonical bytes",
        )
    return expected


def record_id_from_text(value: object, *, canonical_bytes: bytes) -> RecordId:
    text = _require_exact_string(value, "record_id_text")
    if text.count(RECORD_ID_TEXT_SEPARATOR) != 1:
        raise _violation(ContractFailureCode.MALFORMED_IDENTITY, "record_id_text has invalid shape")
    domain_text, digest = text.split(RECORD_ID_TEXT_SEPARATOR)
    return record_id_from_dict(
        {"domain": domain_text, "digest_sha256": digest},
        canonical_bytes=canonical_bytes,
    )


@dataclass(frozen=True)
class RunId:
    value: str

    def __post_init__(self) -> None:
        _require_identifier(self.value, "run_id")

    def to_dict(self) -> dict[str, str]:
        _require_identifier(self.value, "run_id")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> RunId:
        data = _require_exact_dict(value, required=("value",), field_name="run_id")
        return cls(value=data["value"])


@dataclass(frozen=True)
class AttemptId:
    """An exact attempt identity; lifecycle-wide uniqueness is store-enforced later."""

    value: str

    def __post_init__(self) -> None:
        _require_identifier(self.value, "attempt_id")

    def to_dict(self) -> dict[str, str]:
        _require_identifier(self.value, "attempt_id")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> AttemptId:
        data = _require_exact_dict(value, required=("value",), field_name="attempt_id")
        return cls(value=data["value"])


@dataclass(frozen=True)
class ActorIdentity:
    value: str

    def __post_init__(self) -> None:
        _validate_actor_identity(self, "actor_identity")

    def to_dict(self) -> dict[str, str]:
        _validate_actor_identity(self, "actor_identity")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> ActorIdentity:
        data = _require_exact_dict(value, required=("value",), field_name="actor_identity")
        return cls(value=data["value"])


@dataclass(frozen=True)
class AuthorityIdentity:
    value: str

    def __post_init__(self) -> None:
        _validate_authority_identity(self)

    def to_dict(self) -> dict[str, str]:
        _validate_authority_identity(self)
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> AuthorityIdentity:
        data = _require_exact_dict(value, required=("value",), field_name="authority_identity")
        return cls(value=data["value"])


def _validate_actor_identity(value: ActorIdentity, field_name: str) -> None:
    if type(value) is not ActorIdentity:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            f"{field_name} must be an exact ActorIdentity",
        )
    _require_identifier(value.value, field_name)


def _validate_authority_identity(value: AuthorityIdentity) -> None:
    if type(value) is not AuthorityIdentity:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "authority_identity must be an exact AuthorityIdentity",
        )
    _require_identifier(value.value, "authority_identity")


@dataclass(frozen=True, init=False)
class ProposalId:
    record_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ProposalId:
        raise TypeError("ProposalId is created only from exact canonical bytes")

    def to_dict(self) -> dict[str, object]:
        _validate_proposal_id(self)
        return {"record_id": self.record_id.to_dict()}

    @classmethod
    def from_dict(cls, value: object, *, canonical_bytes: bytes) -> ProposalId:
        data = _require_exact_dict(value, required=("record_id",), field_name="proposal_id")
        record_id = record_id_from_dict(data["record_id"], canonical_bytes=canonical_bytes)
        return _make_proposal_id(record_id)


@dataclass(frozen=True, init=False)
class AuthorityDecisionId:
    record_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AuthorityDecisionId:
        raise TypeError("AuthorityDecisionId is created only by the authority decision factory")

    def to_dict(self) -> dict[str, object]:
        _validate_authority_decision_id(self)
        return {"record_id": self.record_id.to_dict()}


@dataclass(frozen=True, init=False)
class ExecutionId:
    record_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ExecutionId:
        raise TypeError("ExecutionId is created only by the execution factory")

    def to_dict(self) -> dict[str, object]:
        _validate_execution_id(self)
        return {"record_id": self.record_id.to_dict()}


def _make_proposal_id(record_id: RecordId) -> ProposalId:
    proposal_id = object.__new__(ProposalId)
    object.__setattr__(proposal_id, "record_id", record_id)
    object.__setattr__(proposal_id, "_trusted_seal", _TRUSTED_SEAL)
    _validate_proposal_id(proposal_id)
    return proposal_id


def _make_authority_decision_id(record_id: RecordId) -> AuthorityDecisionId:
    decision_id = object.__new__(AuthorityDecisionId)
    object.__setattr__(decision_id, "record_id", record_id)
    object.__setattr__(decision_id, "_trusted_seal", _TRUSTED_SEAL)
    _validate_authority_decision_id(decision_id)
    return decision_id


def _make_execution_id(record_id: RecordId) -> ExecutionId:
    execution_id = object.__new__(ExecutionId)
    object.__setattr__(execution_id, "record_id", record_id)
    object.__setattr__(execution_id, "_trusted_seal", _TRUSTED_SEAL)
    _validate_execution_id(execution_id)
    return execution_id


def _validate_proposal_id(value: ProposalId) -> None:
    if type(value) is not ProposalId:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "proposal_id must be an exact ProposalId")
    _require_trusted_seal(value, "proposal_id")
    _validate_record_id_consistency(value.record_id)
    if value.record_id.domain is not IdentityDomain.PROPOSAL:
        raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "proposal_id uses the wrong domain")


def _validate_authority_decision_id(value: AuthorityDecisionId) -> None:
    if type(value) is not AuthorityDecisionId:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "authority_decision_id must be an exact AuthorityDecisionId",
        )
    _require_trusted_seal(value, "authority_decision_id")
    _validate_record_id_consistency(value.record_id)
    if value.record_id.domain is not IdentityDomain.AUTHORITY_DECISION:
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "authority_decision_id uses the wrong domain",
        )


def _validate_execution_id(value: ExecutionId) -> None:
    if type(value) is not ExecutionId:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "execution_id must be an exact ExecutionId")
    _require_trusted_seal(value, "execution_id")
    _validate_record_id_consistency(value.record_id)
    if value.record_id.domain is not IdentityDomain.EXECUTION:
        raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "execution_id uses the wrong domain")


def compute_proposal_id(*, canonical_bytes: bytes) -> ProposalId:
    return _make_proposal_id(
        compute_record_id(domain=IdentityDomain.PROPOSAL, canonical_bytes=canonical_bytes)
    )


@dataclass(frozen=True)
class RepositoryRevision:
    kind: RepositoryRevisionKind
    git_sha: str | None

    def __post_init__(self) -> None:
        _validate_repository_revision(self)

    @classmethod
    def git_commit(cls, git_sha: str) -> RepositoryRevision:
        return cls(kind=RepositoryRevisionKind.GIT_COMMIT, git_sha=git_sha)

    @classmethod
    def not_applicable(cls) -> RepositoryRevision:
        return cls(kind=RepositoryRevisionKind.NOT_APPLICABLE, git_sha=None)

    def to_dict(self) -> dict[str, str | None]:
        _validate_repository_revision(self)
        return {"kind": self.kind.value, "git_sha": self.git_sha}

    @classmethod
    def from_dict(cls, value: object) -> RepositoryRevision:
        data = _require_exact_dict(
            value,
            required=("kind", "git_sha"),
            field_name="repository_revision",
        )
        kind = _parse_enum(
            data["kind"],
            RepositoryRevisionKind,
            ContractFailureCode.MALFORMED_REPOSITORY_REVISION,
            "repository_revision.kind",
        )
        assert isinstance(kind, RepositoryRevisionKind)
        return cls(kind=kind, git_sha=data["git_sha"])


def _validate_repository_revision(value: RepositoryRevision) -> None:
    if type(value) is not RepositoryRevision:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "repository_revision must be an exact RepositoryRevision",
        )
    if type(value.kind) is not RepositoryRevisionKind:
        raise _violation(
            ContractFailureCode.MALFORMED_REPOSITORY_REVISION,
            "repository_revision.kind must be exact",
        )
    if value.kind is RepositoryRevisionKind.GIT_COMMIT:
        if type(value.git_sha) is not str or _GIT_SHA_RE.fullmatch(value.git_sha) is None:
            raise _violation(
                ContractFailureCode.MALFORMED_REPOSITORY_REVISION,
                "git revision must be a full lowercase 40-character SHA",
            )
    elif value.git_sha is not None:
        raise _violation(
            ContractFailureCode.MALFORMED_REPOSITORY_REVISION,
            "not-applicable revision must carry exact null git_sha",
        )


@dataclass(frozen=True)
class LineageParentRef:
    parent_record_id: RecordId
    edge_kind: LineageEdgeKind

    def __post_init__(self) -> None:
        _validate_lineage_parent_ref(self)

    def to_dict(self) -> dict[str, object]:
        _validate_lineage_parent_ref(self)
        return {
            "parent_record_id": self.parent_record_id.to_dict(),
            "edge_kind": self.edge_kind.value,
        }

    @classmethod
    def from_dict(cls, value: object, *, parent_canonical_bytes: bytes) -> LineageParentRef:
        data = _require_exact_dict(
            value,
            required=("parent_record_id", "edge_kind"),
            field_name="lineage_parent_ref",
        )
        parent_record_id = record_id_from_dict(
            data["parent_record_id"],
            canonical_bytes=parent_canonical_bytes,
        )
        edge_kind = _parse_enum(
            data["edge_kind"],
            LineageEdgeKind,
            ContractFailureCode.MALFORMED_IDENTITY,
            "lineage_parent_ref.edge_kind",
        )
        assert isinstance(edge_kind, LineageEdgeKind)
        return cls(parent_record_id=parent_record_id, edge_kind=edge_kind)


def _validate_lineage_parent_ref(value: LineageParentRef) -> None:
    if type(value) is not LineageParentRef:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "lineage parent must be an exact LineageParentRef",
        )
    _validate_record_id_consistency(value.parent_record_id)
    if type(value.edge_kind) is not LineageEdgeKind:
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            "lineage edge kind must be exact",
        )


@dataclass(frozen=True)
class DelegationStep:
    delegator: ActorIdentity
    delegate: ActorIdentity

    def __post_init__(self) -> None:
        _validate_delegation_step(self)

    def to_dict(self) -> dict[str, object]:
        _validate_delegation_step(self)
        return {"delegator": self.delegator.to_dict(), "delegate": self.delegate.to_dict()}

    @classmethod
    def from_dict(cls, value: object) -> DelegationStep:
        data = _require_exact_dict(
            value,
            required=("delegator", "delegate"),
            field_name="delegation_step",
        )
        return cls(
            delegator=ActorIdentity.from_dict(data["delegator"]),
            delegate=ActorIdentity.from_dict(data["delegate"]),
        )


def _validate_delegation_step(value: DelegationStep) -> None:
    if type(value) is not DelegationStep:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "delegation step must be exact")
    _validate_actor_identity(value.delegator, "delegation_step.delegator")
    _validate_actor_identity(value.delegate, "delegation_step.delegate")
    if value.delegator.value == value.delegate.value:
        raise _violation(ContractFailureCode.DELEGATION_CYCLE, "delegation self-cycle is forbidden")


@dataclass(frozen=True, init=False)
class IndependenceProof:
    schema_version: SchemaVersion
    subject_proposal_id: ProposalId
    authority_identity: AuthorityIdentity
    authority_role: AuthorityRole
    reason_code: ReasonCode
    producer_actor_ids: tuple[ActorIdentity, ...]
    source_actor_ids: tuple[ActorIdentity, ...]
    proposer_identity: ActorIdentity
    executor_identity: ActorIdentity | None
    subject_derived_actor_ids: tuple[ActorIdentity, ...]
    delegation_chain: tuple[DelegationStep, ...]
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> IndependenceProof:
        raise TypeError("IndependenceProof is created only by the validated factory")

    def to_dict(self) -> dict[str, object]:
        validate_independence_proof(self)
        return {
            "schema_version": self.schema_version.value,
            "subject_proposal_id": self.subject_proposal_id.to_dict(),
            "authority_identity": self.authority_identity.to_dict(),
            "authority_role": self.authority_role.value,
            "reason_code": self.reason_code.value,
            "producer_actor_ids": [item.to_dict() for item in self.producer_actor_ids],
            "source_actor_ids": [item.to_dict() for item in self.source_actor_ids],
            "proposer_identity": self.proposer_identity.to_dict(),
            "executor_identity": (
                None if self.executor_identity is None else self.executor_identity.to_dict()
            ),
            "subject_derived_actor_ids": [
                item.to_dict() for item in self.subject_derived_actor_ids
            ],
            "delegation_chain": [item.to_dict() for item in self.delegation_chain],
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        proposal_canonical_bytes: bytes,
    ) -> IndependenceProof:
        return independence_proof_from_dict(
            value,
            proposal_canonical_bytes=proposal_canonical_bytes,
        )


def _require_exact_tuple(value: object, field_name: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact tuple")
    return value


def _validate_actor_tuple(value: object, field_name: str, *, non_empty: bool) -> tuple[ActorIdentity, ...]:
    items = _require_exact_tuple(value, field_name)
    if non_empty and not items:
        raise _violation(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            f"{field_name} must cover at least one actor",
        )
    seen: set[str] = set()
    for item in items:
        _validate_actor_identity(item, f"{field_name} entry")
        if item.value in seen:
            raise _violation(ContractFailureCode.DUPLICATE_ACTOR, f"{field_name} contains a duplicate")
        seen.add(item.value)
    return items


def _delegation_has_cycle(steps: tuple[DelegationStep, ...]) -> bool:
    graph: dict[str, set[str]] = {}
    for step in steps:
        graph.setdefault(step.delegator.value, set()).add(step.delegate.value)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in graph.get(node, set()):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in tuple(graph))


def _delegation_reachable_nodes(
    steps: tuple[DelegationStep, ...],
    *,
    origin: str,
) -> set[str]:
    graph: dict[str, set[str]] = {}
    for step in steps:
        graph.setdefault(step.delegator.value, set()).add(step.delegate.value)
    reachable = {origin}
    pending = [origin]
    while pending:
        node = pending.pop()
        for child in graph.get(node, set()):
            if child not in reachable:
                reachable.add(child)
                pending.append(child)
    return reachable


def create_independence_proof(
    *,
    schema_version: SchemaVersion,
    subject_proposal_id: ProposalId,
    authority_identity: AuthorityIdentity,
    authority_role: AuthorityRole,
    reason_code: ReasonCode,
    producer_actor_ids: tuple[ActorIdentity, ...],
    source_actor_ids: tuple[ActorIdentity, ...],
    proposer_identity: ActorIdentity,
    executor_identity: ActorIdentity | None,
    subject_derived_actor_ids: tuple[ActorIdentity, ...],
    delegation_chain: tuple[DelegationStep, ...],
) -> IndependenceProof:
    proof = object.__new__(IndependenceProof)
    object.__setattr__(proof, "schema_version", schema_version)
    object.__setattr__(proof, "subject_proposal_id", subject_proposal_id)
    object.__setattr__(proof, "authority_identity", authority_identity)
    object.__setattr__(proof, "authority_role", authority_role)
    object.__setattr__(proof, "reason_code", reason_code)
    object.__setattr__(proof, "producer_actor_ids", producer_actor_ids)
    object.__setattr__(proof, "source_actor_ids", source_actor_ids)
    object.__setattr__(proof, "proposer_identity", proposer_identity)
    object.__setattr__(proof, "executor_identity", executor_identity)
    object.__setattr__(proof, "subject_derived_actor_ids", subject_derived_actor_ids)
    object.__setattr__(proof, "delegation_chain", delegation_chain)
    object.__setattr__(proof, "_trusted_seal", _TRUSTED_SEAL)
    validate_independence_proof(proof)
    return proof


def validate_independence_proof(proof: IndependenceProof) -> None:
    if type(proof) is not IndependenceProof:
        raise _violation(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            "independence proof must be an exact IndependenceProof",
        )
    _require_trusted_seal(proof, "independence_proof")
    if proof.schema_version is not SchemaVersion.INDEPENDENCE_PROOF_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "independence proof schema is unknown",
        )
    _validate_proposal_id(proof.subject_proposal_id)
    _validate_authority_identity(proof.authority_identity)
    if type(proof.authority_role) is not AuthorityRole:
        raise _violation(
            ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
            "authority_role must be an exact AuthorityRole",
        )
    if type(proof.reason_code) is not ReasonCode:
        raise _violation(
            ContractFailureCode.UNKNOWN_REASON_CODE,
            "reason_code must be an exact ReasonCode",
        )
    if _ROLE_REASON_MATRIX[proof.authority_role] is not proof.reason_code:
        raise _violation(
            ContractFailureCode.UNKNOWN_REASON_CODE,
            "reason_code does not match the exact authority role",
        )
    producers = _validate_actor_tuple(proof.producer_actor_ids, "producer_actor_ids", non_empty=True)
    sources = _validate_actor_tuple(proof.source_actor_ids, "source_actor_ids", non_empty=True)
    derived = _validate_actor_tuple(
        proof.subject_derived_actor_ids,
        "subject_derived_actor_ids",
        non_empty=False,
    )
    _validate_actor_identity(proof.proposer_identity, "proposer_identity")
    if proof.executor_identity is not None:
        _validate_actor_identity(proof.executor_identity, "executor_identity")
    authority = proof.authority_identity.value
    role_actors = {
        *(item.value for item in producers),
        *(item.value for item in sources),
        proof.proposer_identity.value,
    }
    if proof.executor_identity is not None:
        role_actors.add(proof.executor_identity.value)
    if authority in role_actors:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "authority must be separate from producer, source, proposer, and executor",
        )
    if authority in {item.value for item in derived}:
        raise _violation(
            ContractFailureCode.AUTHORITY_DERIVED_FROM_SUBJECT,
            "authority must not be derived from the subject",
        )
    steps = _require_exact_tuple(proof.delegation_chain, "delegation_chain")
    edge_keys: set[tuple[str, str]] = set()
    for step in steps:
        _validate_delegation_step(step)
        key = (step.delegator.value, step.delegate.value)
        if key in edge_keys:
            raise _violation(
                ContractFailureCode.DUPLICATE_ACTOR,
                "delegation_chain contains a duplicate edge",
            )
        edge_keys.add(key)
    if _delegation_has_cycle(steps):
        raise _violation(ContractFailureCode.DELEGATION_CYCLE, "delegation_chain contains a cycle")
    reachable = _delegation_reachable_nodes(steps, origin=authority)
    if any(step.delegator.value not in reachable for step in steps):
        raise _violation(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            "every delegation edge must belong to the authority-rooted graph",
        )
    delegated_back_targets = {
        *(item.value for item in producers),
        *(item.value for item in sources),
        *(item.value for item in derived),
        proof.proposer_identity.value,
    }
    if proof.executor_identity is not None:
        delegated_back_targets.add(proof.executor_identity.value)
    if (reachable - {authority}) & delegated_back_targets:
        raise _violation(
            ContractFailureCode.DELEGATED_BACK_AUTHORITY,
            "authority delegation returns control to a participating actor",
        )


def independence_proof_from_dict(
    value: object,
    *,
    proposal_canonical_bytes: bytes,
) -> IndependenceProof:
    fields = (
        "schema_version",
        "subject_proposal_id",
        "authority_identity",
        "authority_role",
        "reason_code",
        "producer_actor_ids",
        "source_actor_ids",
        "proposer_identity",
        "executor_identity",
        "subject_derived_actor_ids",
        "delegation_chain",
    )
    data = _require_exact_dict(value, required=fields, field_name="independence_proof")
    schema = _parse_enum(
        data["schema_version"],
        SchemaVersion,
        ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
        "independence_proof.schema_version",
    )
    if schema is not SchemaVersion.INDEPENDENCE_PROOF_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "independence proof schema is unknown",
        )
    role = _parse_enum(
        data["authority_role"],
        AuthorityRole,
        ContractFailureCode.UNKNOWN_AUTHORITY_ROLE,
        "authority_role",
    )
    reason = _parse_enum(
        data["reason_code"],
        ReasonCode,
        ContractFailureCode.UNKNOWN_REASON_CODE,
        "reason_code",
    )
    for sequence_field in (
        "producer_actor_ids",
        "source_actor_ids",
        "subject_derived_actor_ids",
        "delegation_chain",
    ):
        if type(data[sequence_field]) is not list:
            raise _violation(
                ContractFailureCode.TYPE_MISMATCH,
                f"{sequence_field} transport value must be an exact list",
            )
    executor_data = data["executor_identity"]
    executor = None if executor_data is None else ActorIdentity.from_dict(executor_data)
    assert isinstance(schema, SchemaVersion)
    assert isinstance(role, AuthorityRole)
    assert isinstance(reason, ReasonCode)
    return create_independence_proof(
        schema_version=schema,
        subject_proposal_id=ProposalId.from_dict(
            data["subject_proposal_id"],
            canonical_bytes=proposal_canonical_bytes,
        ),
        authority_identity=AuthorityIdentity.from_dict(data["authority_identity"]),
        authority_role=role,
        reason_code=reason,
        producer_actor_ids=tuple(ActorIdentity.from_dict(item) for item in data["producer_actor_ids"]),
        source_actor_ids=tuple(ActorIdentity.from_dict(item) for item in data["source_actor_ids"]),
        proposer_identity=ActorIdentity.from_dict(data["proposer_identity"]),
        executor_identity=executor,
        subject_derived_actor_ids=tuple(
            ActorIdentity.from_dict(item) for item in data["subject_derived_actor_ids"]
        ),
        delegation_chain=tuple(DelegationStep.from_dict(item) for item in data["delegation_chain"]),
    )


def _frame_identity_bytes(value: bytes) -> bytes:
    if type(value) is not bytes:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "identity frame must contain exact bytes",
        )
    return len(value).to_bytes(_IDENTITY_FRAME_LENGTH_BYTES, "big") + value


def _frame_identity_text(value: str) -> bytes:
    if type(value) is not str:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "identity text frame must contain an exact string",
        )
    return _frame_identity_bytes(value.encode("utf-8"))


def _frame_identity_count(value: int) -> bytes:
    if type(value) is not int or value < 0:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "identity collection count must be a non-negative exact integer",
        )
    return value.to_bytes(_IDENTITY_FRAME_LENGTH_BYTES, "big")


_AUTHORITY_HANDLE_SEAL = object()
_AUTHORITY_CONFIGURATION_SEAL = object()
_HISTORY_ANCHOR_SEAL = object()


@dataclass(frozen=True, init=False)
class Stage4AuthorityConfiguration:
    schema_version: SchemaVersion
    platform_attester_actor: ActorIdentity
    builder_actor: ActorIdentity
    taint_classifier_authority: AuthorityIdentity
    taint_reviewer_authority: AuthorityIdentity
    supersession_reviewer_authority: AuthorityIdentity
    revocation_reviewer_authority: AuthorityIdentity
    lifecycle_writer_actor: ActorIdentity
    governing_human_authority: AuthorityIdentity | None
    configuration_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> Stage4AuthorityConfiguration:
        raise TypeError("Stage4AuthorityConfiguration is created only by its factory")

    def to_dict(self) -> dict[str, object]:
        validate_stage4_authority_configuration(self)
        return {
            "schema_version": self.schema_version.value,
            "platform_attester_actor": self.platform_attester_actor.to_dict(),
            "builder_actor": self.builder_actor.to_dict(),
            "taint_classifier_authority": self.taint_classifier_authority.to_dict(),
            "taint_reviewer_authority": self.taint_reviewer_authority.to_dict(),
            "supersession_reviewer_authority": self.supersession_reviewer_authority.to_dict(),
            "revocation_reviewer_authority": self.revocation_reviewer_authority.to_dict(),
            "lifecycle_writer_actor": self.lifecycle_writer_actor.to_dict(),
            "governing_human_authority": (
                None
                if self.governing_human_authority is None
                else self.governing_human_authority.to_dict()
            ),
            "configuration_id": self.configuration_id.to_dict(),
        }


def _authority_configuration_preimage(
    *,
    platform_attester_actor: ActorIdentity,
    builder_actor: ActorIdentity,
    taint_classifier_authority: AuthorityIdentity,
    taint_reviewer_authority: AuthorityIdentity,
    supersession_reviewer_authority: AuthorityIdentity,
    revocation_reviewer_authority: AuthorityIdentity,
    lifecycle_writer_actor: ActorIdentity,
    governing_human_authority: AuthorityIdentity | None,
) -> bytes:
    parts = [
        _frame_identity_text(AUTHORITY_CONFIGURATION_IDENTITY_PROFILE_V1),
        _frame_identity_text(AUTHORITY_CONFIGURATION_SCHEMA_V1),
        _frame_identity_text(platform_attester_actor.value),
        _frame_identity_text(builder_actor.value),
        _frame_identity_text(taint_classifier_authority.value),
        _frame_identity_text(taint_reviewer_authority.value),
        _frame_identity_text(supersession_reviewer_authority.value),
        _frame_identity_text(revocation_reviewer_authority.value),
        _frame_identity_text(lifecycle_writer_actor.value),
    ]
    if governing_human_authority is None:
        parts.append(b"\x00")
    else:
        parts.extend((b"\x01", _frame_identity_text(governing_human_authority.value)))
    return b"".join(parts)


def _snapshot_actor(value: ActorIdentity, field_name: str) -> ActorIdentity:
    _validate_actor_identity(value, field_name)
    return ActorIdentity.from_dict(value.to_dict())


def _snapshot_authority(value: AuthorityIdentity, field_name: str) -> AuthorityIdentity:
    _validate_authority_identity(value)
    return AuthorityIdentity.from_dict(value.to_dict())


def create_stage4_authority_configuration(
    *,
    platform_attester_actor: ActorIdentity,
    builder_actor: ActorIdentity,
    taint_classifier_authority: AuthorityIdentity,
    taint_reviewer_authority: AuthorityIdentity,
    supersession_reviewer_authority: AuthorityIdentity,
    revocation_reviewer_authority: AuthorityIdentity,
    lifecycle_writer_actor: ActorIdentity,
    governing_human_authority: AuthorityIdentity | None,
) -> Stage4AuthorityConfiguration:
    attester = _snapshot_actor(platform_attester_actor, "platform_attester_actor")
    builder = _snapshot_actor(builder_actor, "builder_actor")
    classifier = _snapshot_authority(taint_classifier_authority, "taint_classifier_authority")
    taint_reviewer = _snapshot_authority(taint_reviewer_authority, "taint_reviewer_authority")
    supersession = _snapshot_authority(
        supersession_reviewer_authority,
        "supersession_reviewer_authority",
    )
    revocation = _snapshot_authority(
        revocation_reviewer_authority,
        "revocation_reviewer_authority",
    )
    writer = _snapshot_actor(lifecycle_writer_actor, "lifecycle_writer_actor")
    human = (
        None
        if governing_human_authority is None
        else _snapshot_authority(governing_human_authority, "governing_human_authority")
    )
    automated_reviewers = {
        taint_reviewer.value,
        supersession.value,
        revocation.value,
    }
    if len(automated_reviewers) != 3:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "trust-elevating reviewer authorities must be pairwise distinct",
        )
    if attester.value == classifier.value or {
        attester.value,
        classifier.value,
    } & automated_reviewers:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "attester, classifier, and trust-elevating reviewers must be separate",
        )
    if writer.value in automated_reviewers:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "lifecycle writer must be separate from decision reviewers",
        )
    automated = automated_reviewers | {attester.value, classifier.value, writer.value}
    if human is not None and human.value in automated:
        raise _violation(
            ContractFailureCode.AUTHORITY_AMBIGUITY,
            "governing human must be separate from automated authorities",
        )
    preimage = _authority_configuration_preimage(
        platform_attester_actor=attester,
        builder_actor=builder,
        taint_classifier_authority=classifier,
        taint_reviewer_authority=taint_reviewer,
        supersession_reviewer_authority=supersession,
        revocation_reviewer_authority=revocation,
        lifecycle_writer_actor=writer,
        governing_human_authority=human,
    )
    result = object.__new__(Stage4AuthorityConfiguration)
    object.__setattr__(result, "schema_version", SchemaVersion.AUTHORITY_CONFIGURATION_V1)
    object.__setattr__(result, "platform_attester_actor", attester)
    object.__setattr__(result, "builder_actor", builder)
    object.__setattr__(result, "taint_classifier_authority", classifier)
    object.__setattr__(result, "taint_reviewer_authority", taint_reviewer)
    object.__setattr__(result, "supersession_reviewer_authority", supersession)
    object.__setattr__(result, "revocation_reviewer_authority", revocation)
    object.__setattr__(result, "lifecycle_writer_actor", writer)
    object.__setattr__(result, "governing_human_authority", human)
    object.__setattr__(
        result,
        "configuration_id",
        compute_record_id(
            domain=IdentityDomain.AUTHORITY_CONFIGURATION,
            canonical_bytes=preimage,
        ),
    )
    object.__setattr__(result, "_trusted_seal", _AUTHORITY_CONFIGURATION_SEAL)
    validate_stage4_authority_configuration(result)
    return result


def validate_stage4_authority_configuration(value: Stage4AuthorityConfiguration) -> None:
    if (
        type(value) is not Stage4AuthorityConfiguration
        or getattr(value, "_trusted_seal", None) is not _AUTHORITY_CONFIGURATION_SEAL
    ):
        raise _violation(
            ContractFailureCode.TRUSTED_OBJECT_FORGED,
            "authority configuration is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.AUTHORITY_CONFIGURATION_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "authority configuration schema is unknown",
        )
    preimage = _authority_configuration_preimage(
        platform_attester_actor=_snapshot_actor(value.platform_attester_actor, "platform_attester_actor"),
        builder_actor=_snapshot_actor(value.builder_actor, "builder_actor"),
        taint_classifier_authority=_snapshot_authority(value.taint_classifier_authority, "taint_classifier_authority"),
        taint_reviewer_authority=_snapshot_authority(value.taint_reviewer_authority, "taint_reviewer_authority"),
        supersession_reviewer_authority=_snapshot_authority(value.supersession_reviewer_authority, "supersession_reviewer_authority"),
        revocation_reviewer_authority=_snapshot_authority(value.revocation_reviewer_authority, "revocation_reviewer_authority"),
        lifecycle_writer_actor=_snapshot_actor(value.lifecycle_writer_actor, "lifecycle_writer_actor"),
        governing_human_authority=(
            None
            if value.governing_human_authority is None
            else _snapshot_authority(value.governing_human_authority, "governing_human_authority")
        ),
    )
    if (
        type(value.configuration_id) is not RecordId
        or value.configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION
    ):
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "configuration identity domain is invalid",
        )
    try:
        validate_record_id(value.configuration_id, canonical_bytes=preimage)
    except ContractViolation as exc:
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "configuration identity does not match configured actors",
        ) from exc
    # Repeat the separation checks without trusting construction-time state.
    reviewers = {
        value.taint_reviewer_authority.value,
        value.supersession_reviewer_authority.value,
        value.revocation_reviewer_authority.value,
    }
    if len(reviewers) != 3:
        raise _violation(ContractFailureCode.AUTHORITY_AMBIGUITY, "reviewers overlap")
    if value.platform_attester_actor.value == value.taint_classifier_authority.value:
        raise _violation(ContractFailureCode.AUTHORITY_AMBIGUITY, "attester and classifier overlap")
    if {value.platform_attester_actor.value, value.taint_classifier_authority.value} & reviewers:
        raise _violation(ContractFailureCode.AUTHORITY_AMBIGUITY, "automated authorities overlap")
    if value.lifecycle_writer_actor.value in reviewers:
        raise _violation(ContractFailureCode.AUTHORITY_AMBIGUITY, "writer and reviewer overlap")
    if value.governing_human_authority is not None and value.governing_human_authority.value in (
        reviewers
        | {
            value.platform_attester_actor.value,
            value.taint_classifier_authority.value,
            value.lifecycle_writer_actor.value,
        }
    ):
        raise _violation(ContractFailureCode.AUTHORITY_AMBIGUITY, "governing human overlaps automated authority")


def stage4_authority_configuration_from_dict(value: object) -> Stage4AuthorityConfiguration:
    data = _require_exact_dict(
        value,
        required=(
            "schema_version",
            "platform_attester_actor",
            "builder_actor",
            "taint_classifier_authority",
            "taint_reviewer_authority",
            "supersession_reviewer_authority",
            "revocation_reviewer_authority",
            "lifecycle_writer_actor",
            "governing_human_authority",
            "configuration_id",
        ),
        field_name="stage4_authority_configuration",
    )
    if data["schema_version"] != AUTHORITY_CONFIGURATION_SCHEMA_V1:
        raise _violation(ContractFailureCode.UNKNOWN_SCHEMA_VERSION, "authority configuration schema is unknown")
    human_raw = data["governing_human_authority"]
    result = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity.from_dict(data["platform_attester_actor"]),
        builder_actor=ActorIdentity.from_dict(data["builder_actor"]),
        taint_classifier_authority=AuthorityIdentity.from_dict(data["taint_classifier_authority"]),
        taint_reviewer_authority=AuthorityIdentity.from_dict(data["taint_reviewer_authority"]),
        supersession_reviewer_authority=AuthorityIdentity.from_dict(data["supersession_reviewer_authority"]),
        revocation_reviewer_authority=AuthorityIdentity.from_dict(data["revocation_reviewer_authority"]),
        lifecycle_writer_actor=ActorIdentity.from_dict(data["lifecycle_writer_actor"]),
        governing_human_authority=(
            None if human_raw is None else AuthorityIdentity.from_dict(human_raw)
        ),
    )
    if data["configuration_id"] != result.configuration_id.to_dict():
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "transport configuration identity changed",
        )
    return result


@dataclass(frozen=True, init=False)
class Stage4AuthorityHandle:
    _configuration: Stage4AuthorityConfiguration
    _instance_token: object
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> Stage4AuthorityHandle:
        raise TypeError("Stage4AuthorityHandle is process-local and factory-created")

    @property
    def configuration(self) -> Stage4AuthorityConfiguration:
        require_stage4_authority_handle(self)
        return stage4_authority_configuration_from_dict(self._configuration.to_dict())

    @property
    def configuration_id(self) -> RecordId:
        require_stage4_authority_handle(self)
        return record_id_from_dict(
            self._configuration.configuration_id.to_dict(),
            canonical_bytes=_authority_configuration_preimage(
                platform_attester_actor=self._configuration.platform_attester_actor,
                builder_actor=self._configuration.builder_actor,
                taint_classifier_authority=self._configuration.taint_classifier_authority,
                taint_reviewer_authority=self._configuration.taint_reviewer_authority,
                supersession_reviewer_authority=self._configuration.supersession_reviewer_authority,
                revocation_reviewer_authority=self._configuration.revocation_reviewer_authority,
                lifecycle_writer_actor=self._configuration.lifecycle_writer_actor,
                governing_human_authority=self._configuration.governing_human_authority,
            ),
        )


def create_stage4_authority_handle(
    configuration: Stage4AuthorityConfiguration,
) -> Stage4AuthorityHandle:
    validate_stage4_authority_configuration(configuration)
    result = object.__new__(Stage4AuthorityHandle)
    object.__setattr__(
        result,
        "_configuration",
        stage4_authority_configuration_from_dict(configuration.to_dict()),
    )
    object.__setattr__(result, "_instance_token", object())
    object.__setattr__(result, "_trusted_seal", _AUTHORITY_HANDLE_SEAL)
    require_stage4_authority_handle(result)
    return result


def require_stage4_authority_handle(
    value: Stage4AuthorityHandle,
    *,
    expected_handle: Stage4AuthorityHandle | None = None,
) -> Stage4AuthorityConfiguration:
    if (
        type(value) is not Stage4AuthorityHandle
        or getattr(value, "_trusted_seal", None) is not _AUTHORITY_HANDLE_SEAL
        or type(getattr(value, "_instance_token", None)) is not object
    ):
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "authority handle is not a configured process-local capability",
        )
    if expected_handle is not None and value is not expected_handle:
        raise _violation(
            ContractFailureCode.WRONG_AUTHORITY_HANDLE,
            "write boundary requires the exact opened authority handle",
        )
    validate_stage4_authority_configuration(value._configuration)
    return value._configuration


_HISTORY_DOMAIN_IDENTITY = {
    HistoryDomain.PROVENANCE: IdentityDomain.PROVENANCE_HISTORY_ANCHOR,
    HistoryDomain.TAINT: IdentityDomain.TAINT_HISTORY_ANCHOR,
    HistoryDomain.LIFECYCLE: IdentityDomain.LIFECYCLE_HISTORY_ANCHOR,
}


def compute_ordered_history_roots(
    *,
    history_domain: HistoryDomain,
    configuration_id: RecordId,
    entry_sha256s: tuple[str, ...],
) -> tuple[str, ...]:
    if type(history_domain) is not HistoryDomain:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "history domain is invalid")
    _validate_record_id_consistency(configuration_id)
    if configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _violation(ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "history configuration is invalid")
    if type(entry_sha256s) is not tuple:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "history entries must be an exact tuple")
    prefix = HISTORY_LOG_ROOT_PROFILE_V1.encode("utf-8") + b"\x00"
    root = hashlib.sha256(
        prefix
        + _frame_identity_text(history_domain.value)
        + _frame_identity_text(configuration_id.value)
    ).digest()
    roots: list[str] = []
    for digest in entry_sha256s:
        if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            raise _violation(ContractFailureCode.MALFORMED_SHA256, "history entry digest is invalid")
        root = hashlib.sha256(prefix + root + bytes.fromhex(digest)).digest()
        roots.append(root.hex())
    return tuple(roots)


def _history_anchor_preimage(
    *,
    history_domain: HistoryDomain,
    configuration_id: RecordId,
    entry_count: int,
    ordered_log_root_sha256: str,
    domain_heads: tuple[str, ...],
) -> bytes:
    parts = [
        _frame_identity_text(HISTORY_ANCHOR_IDENTITY_PROFILE_V1),
        _frame_identity_text(HISTORY_ANCHOR_SCHEMA_V1),
        _frame_identity_text(history_domain.value),
        _frame_identity_text(configuration_id.value),
        _frame_identity_count(entry_count),
        _frame_identity_text(ordered_log_root_sha256),
        _frame_identity_count(len(domain_heads)),
    ]
    parts.extend(_frame_identity_text(head) for head in domain_heads)
    return b"".join(parts)


@dataclass(frozen=True, init=False)
class HistoryAnchor:
    schema_version: SchemaVersion
    history_domain: HistoryDomain
    configuration_id: RecordId
    entry_count: int
    ordered_log_root_sha256: str
    domain_heads: tuple[str, ...]
    anchor_id: RecordId
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> HistoryAnchor:
        raise TypeError("HistoryAnchor is computed from complete validated history")

    def to_dict(self) -> dict[str, object]:
        validate_history_anchor(self)
        return {
            "schema_version": self.schema_version.value,
            "history_domain": self.history_domain.value,
            "configuration_id": self.configuration_id.to_dict(),
            "entry_count": self.entry_count,
            "ordered_log_root_sha256": self.ordered_log_root_sha256,
            "domain_heads": list(self.domain_heads),
            "anchor_id": self.anchor_id.to_dict(),
        }


def create_history_anchor(
    *,
    history_domain: HistoryDomain,
    configuration_id: RecordId,
    entry_sha256s: tuple[str, ...],
    domain_heads: tuple[str, ...],
) -> HistoryAnchor:
    if type(domain_heads) is not tuple or any(
        type(head) is not str or not head or len(head) > 512 or "\x00" in head
        for head in domain_heads
    ):
        raise _violation(ContractFailureCode.MALFORMED_IDENTITY, "history domain heads are invalid")
    if len(set(domain_heads)) != len(domain_heads):
        raise _violation(ContractFailureCode.MALFORMED_IDENTITY, "history domain heads contain duplicates")
    roots = compute_ordered_history_roots(
        history_domain=history_domain,
        configuration_id=configuration_id,
        entry_sha256s=entry_sha256s,
    )
    if roots:
        root = roots[-1]
    else:
        prefix = HISTORY_LOG_ROOT_PROFILE_V1.encode("utf-8") + b"\x00"
        root = hashlib.sha256(
            prefix
            + _frame_identity_text(history_domain.value)
            + _frame_identity_text(configuration_id.value)
        ).hexdigest()
    preimage = _history_anchor_preimage(
        history_domain=history_domain,
        configuration_id=configuration_id,
        entry_count=len(entry_sha256s),
        ordered_log_root_sha256=root,
        domain_heads=domain_heads,
    )
    result = object.__new__(HistoryAnchor)
    object.__setattr__(result, "schema_version", SchemaVersion.HISTORY_ANCHOR_V1)
    object.__setattr__(result, "history_domain", history_domain)
    object.__setattr__(result, "configuration_id", configuration_id)
    object.__setattr__(result, "entry_count", len(entry_sha256s))
    object.__setattr__(result, "ordered_log_root_sha256", root)
    object.__setattr__(result, "domain_heads", domain_heads)
    object.__setattr__(
        result,
        "anchor_id",
        compute_record_id(domain=_HISTORY_DOMAIN_IDENTITY[history_domain], canonical_bytes=preimage),
    )
    object.__setattr__(result, "_trusted_seal", _HISTORY_ANCHOR_SEAL)
    validate_history_anchor(result)
    return result


def validate_history_anchor(value: HistoryAnchor) -> None:
    if type(value) is not HistoryAnchor or getattr(value, "_trusted_seal", None) is not _HISTORY_ANCHOR_SEAL:
        raise _violation(ContractFailureCode.TRUSTED_OBJECT_FORGED, "history anchor is not factory sealed")
    if value.schema_version is not SchemaVersion.HISTORY_ANCHOR_V1 or type(value.history_domain) is not HistoryDomain:
        raise _violation(ContractFailureCode.UNKNOWN_SCHEMA_VERSION, "history anchor schema/domain is unknown")
    _validate_record_id_consistency(value.configuration_id)
    if value.configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _violation(ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "anchor configuration is invalid")
    if type(value.entry_count) is not int or value.entry_count < 0:
        raise _violation(ContractFailureCode.HISTORY_ROLLBACK, "anchor entry count is invalid")
    if type(value.ordered_log_root_sha256) is not str or _SHA256_RE.fullmatch(value.ordered_log_root_sha256) is None:
        raise _violation(ContractFailureCode.MALFORMED_SHA256, "anchor root is invalid")
    if type(value.domain_heads) is not tuple or any(
        type(head) is not str or not head or len(head) > 512 or "\x00" in head
        for head in value.domain_heads
    ):
        raise _violation(ContractFailureCode.MALFORMED_IDENTITY, "anchor heads are invalid")
    if len(set(value.domain_heads)) != len(value.domain_heads):
        raise _violation(ContractFailureCode.MALFORMED_IDENTITY, "anchor heads contain duplicates")
    preimage = _history_anchor_preimage(
        history_domain=value.history_domain,
        configuration_id=value.configuration_id,
        entry_count=value.entry_count,
        ordered_log_root_sha256=value.ordered_log_root_sha256,
        domain_heads=value.domain_heads,
    )
    if type(value.anchor_id) is not RecordId or value.anchor_id.domain is not _HISTORY_DOMAIN_IDENTITY[value.history_domain]:
        raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "anchor identity domain is invalid")
    validate_record_id(value.anchor_id, canonical_bytes=preimage)


def history_anchor_from_dict(
    data: object,
    *,
    expected_history_domain: HistoryDomain,
    expected_configuration_id: RecordId,
) -> HistoryAnchor:
    if type(expected_history_domain) is not HistoryDomain:
        raise _violation(
            ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
            "expected history domain is invalid",
        )
    _validate_record_id_consistency(expected_configuration_id)
    if expected_configuration_id.domain is not IdentityDomain.AUTHORITY_CONFIGURATION:
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "expected history configuration is invalid",
        )
    fields = (
        "schema_version",
        "history_domain",
        "configuration_id",
        "entry_count",
        "ordered_log_root_sha256",
        "domain_heads",
        "anchor_id",
    )
    raw = _require_exact_dict(data, required=fields, field_name="history_anchor")
    if raw["schema_version"] != HISTORY_ANCHOR_SCHEMA_V1 or type(raw["schema_version"]) is not str:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "history anchor schema is unknown",
        )
    parsed_domain = _parse_enum(
        raw["history_domain"],
        HistoryDomain,
        ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
        "history_anchor.history_domain",
    )
    if parsed_domain is not expected_history_domain:
        raise _violation(
            ContractFailureCode.UNKNOWN_IDENTITY_DOMAIN,
            "history anchor domain differs from the expected contour",
        )
    configuration_data = _require_exact_dict(
        raw["configuration_id"],
        required=("domain", "digest_sha256"),
        field_name="history_anchor.configuration_id",
    )
    if configuration_data != expected_configuration_id.to_dict():
        raise _violation(
            ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH,
            "history anchor configuration differs from the expected contour",
        )
    entry_count = raw["entry_count"]
    if type(entry_count) is not int or entry_count < 0:
        raise _violation(
            ContractFailureCode.HISTORY_ROLLBACK,
            "history anchor entry count is invalid",
        )
    ordered_root = _require_sha256(
        raw["ordered_log_root_sha256"],
        "history_anchor.ordered_log_root_sha256",
    )
    heads_data = raw["domain_heads"]
    if type(heads_data) is not list:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "history_anchor.domain_heads must be an exact list",
        )
    domain_heads = tuple(heads_data)
    if any(
        type(head) is not str or not head or len(head) > 512 or "\x00" in head
        for head in domain_heads
    ):
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            "history anchor heads are invalid",
        )
    if len(set(domain_heads)) != len(domain_heads):
        raise _violation(
            ContractFailureCode.MALFORMED_IDENTITY,
            "history anchor heads contain duplicates",
        )
    configuration_id = _make_record_id(
        expected_configuration_id.domain,
        expected_configuration_id.digest_sha256,
    )
    preimage = _history_anchor_preimage(
        history_domain=expected_history_domain,
        configuration_id=configuration_id,
        entry_count=entry_count,
        ordered_log_root_sha256=ordered_root,
        domain_heads=domain_heads,
    )
    anchor_id = record_id_from_dict(raw["anchor_id"], canonical_bytes=preimage)
    if anchor_id.domain is not _HISTORY_DOMAIN_IDENTITY[expected_history_domain]:
        raise _violation(
            ContractFailureCode.RECORD_ID_MISMATCH,
            "history anchor identity domain is invalid",
        )
    result = object.__new__(HistoryAnchor)
    object.__setattr__(result, "schema_version", SchemaVersion.HISTORY_ANCHOR_V1)
    object.__setattr__(result, "history_domain", expected_history_domain)
    object.__setattr__(result, "configuration_id", configuration_id)
    object.__setattr__(result, "entry_count", entry_count)
    object.__setattr__(result, "ordered_log_root_sha256", ordered_root)
    object.__setattr__(result, "domain_heads", domain_heads)
    object.__setattr__(result, "anchor_id", anchor_id)
    object.__setattr__(result, "_trusted_seal", _HISTORY_ANCHOR_SEAL)
    validate_history_anchor(result)
    return result


def validate_history_anchor_extension(
    *,
    trusted_anchor: HistoryAnchor,
    history_domain: HistoryDomain,
    configuration_id: RecordId,
    entry_sha256s: tuple[str, ...],
    prefix_domain_heads: tuple[str, ...],
) -> None:
    validate_history_anchor(trusted_anchor)
    if trusted_anchor.history_domain is not history_domain or trusted_anchor.configuration_id != configuration_id:
        raise _violation(ContractFailureCode.AUTHORITY_CONFIGURATION_MISMATCH, "trusted anchor belongs to another contour")
    if len(entry_sha256s) < trusted_anchor.entry_count:
        raise _violation(ContractFailureCode.HISTORY_ROLLBACK, "durable history is shorter than trusted anchor")
    roots = compute_ordered_history_roots(
        history_domain=history_domain,
        configuration_id=configuration_id,
        entry_sha256s=entry_sha256s[: trusted_anchor.entry_count],
    )
    if trusted_anchor.entry_count:
        prefix_root = roots[-1]
    else:
        prefix_root = create_history_anchor(
            history_domain=history_domain,
            configuration_id=configuration_id,
            entry_sha256s=(),
            domain_heads=(),
        ).ordered_log_root_sha256
    if prefix_root != trusted_anchor.ordered_log_root_sha256 or prefix_domain_heads != trusted_anchor.domain_heads:
        raise _violation(ContractFailureCode.HISTORY_ROLLBACK, "trusted anchor is not an exact history prefix")


def _authority_decision_binding_preimage(
    *,
    independence_proof: IndependenceProof,
    decision_canonical_bytes: bytes,
) -> bytes:
    """Bind a complete validated proof to exact decision bytes.

    This is a private, versioned binary identity profile, not a general-purpose
    domain-object serializer or transport representation.
    """

    validate_independence_proof(independence_proof)
    decision_bytes = _require_exact_bytes(
        decision_canonical_bytes,
        "decision_canonical_bytes",
    )
    proof = independence_proof
    parts = [
        _AUTHORITY_DECISION_BINDING_PREFIX,
        _frame_identity_text(proof.schema_version.value),
        _frame_identity_text(proof.subject_proposal_id.record_id.value),
        _frame_identity_text(proof.authority_identity.value),
        _frame_identity_text(proof.authority_role.value),
        _frame_identity_text(proof.reason_code.value),
        _frame_identity_count(len(proof.producer_actor_ids)),
    ]
    parts.extend(_frame_identity_text(actor.value) for actor in proof.producer_actor_ids)
    parts.append(_frame_identity_count(len(proof.source_actor_ids)))
    parts.extend(_frame_identity_text(actor.value) for actor in proof.source_actor_ids)
    parts.append(_frame_identity_text(proof.proposer_identity.value))
    if proof.executor_identity is None:
        parts.append(b"\x00")
    else:
        parts.extend((b"\x01", _frame_identity_text(proof.executor_identity.value)))
    parts.append(_frame_identity_count(len(proof.subject_derived_actor_ids)))
    parts.extend(
        _frame_identity_text(actor.value) for actor in proof.subject_derived_actor_ids
    )
    parts.append(_frame_identity_count(len(proof.delegation_chain)))
    for step in proof.delegation_chain:
        parts.append(_frame_identity_text(step.delegator.value))
        parts.append(_frame_identity_text(step.delegate.value))
    parts.append(_frame_identity_bytes(decision_bytes))
    return b"".join(parts)


def compute_authority_decision_id(
    *,
    canonical_bytes: bytes,
    independence_proof: IndependenceProof,
) -> AuthorityDecisionId:
    binding_preimage = _authority_decision_binding_preimage(
        independence_proof=independence_proof,
        decision_canonical_bytes=canonical_bytes,
    )
    return _make_authority_decision_id(
        compute_record_id(
            domain=IdentityDomain.AUTHORITY_DECISION,
            canonical_bytes=binding_preimage,
        )
    )


def authority_decision_id_from_dict(
    value: object,
    *,
    canonical_bytes: bytes,
    independence_proof: IndependenceProof,
) -> AuthorityDecisionId:
    data = _require_exact_dict(value, required=("record_id",), field_name="authority_decision_id")
    binding_preimage = _authority_decision_binding_preimage(
        independence_proof=independence_proof,
        decision_canonical_bytes=canonical_bytes,
    )
    record_id = record_id_from_dict(data["record_id"], canonical_bytes=binding_preimage)
    return _make_authority_decision_id(record_id)


def compute_execution_id(
    *,
    canonical_bytes: bytes,
    authority_decision_id: AuthorityDecisionId,
) -> ExecutionId:
    _validate_authority_decision_id(authority_decision_id)
    return _make_execution_id(
        compute_record_id(domain=IdentityDomain.EXECUTION, canonical_bytes=canonical_bytes)
    )


def execution_id_from_dict(
    value: object,
    *,
    canonical_bytes: bytes,
    authority_decision_id: AuthorityDecisionId,
) -> ExecutionId:
    _validate_authority_decision_id(authority_decision_id)
    data = _require_exact_dict(value, required=("record_id",), field_name="execution_id")
    record_id = record_id_from_dict(data["record_id"], canonical_bytes=canonical_bytes)
    return _make_execution_id(record_id)


def _format_utc_timestamp(value: datetime) -> str:
    _validate_utc_timestamp(value)
    return value.strftime(UTC_TIMESTAMP_FORMAT)


def _parse_utc_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise _violation(
            ContractFailureCode.MALFORMED_TIMESTAMP,
            "created_at_utc must be an exact UTC timestamp string",
        )
    try:
        parsed = datetime.strptime(value, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _violation(
            ContractFailureCode.MALFORMED_TIMESTAMP,
            "created_at_utc must use YYYY-MM-DDTHH:MM:SS.ffffffZ",
        ) from exc
    if parsed.strftime(UTC_TIMESTAMP_FORMAT) != value:
        raise _violation(
            ContractFailureCode.MALFORMED_TIMESTAMP,
            "created_at_utc must use the exact UTC transport representation",
        )
    return parsed


def _validate_utc_timestamp(value: object) -> None:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise _violation(
            ContractFailureCode.MALFORMED_TIMESTAMP,
            "created_at_utc must be an exact timezone.utc datetime",
        )
    if value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise _violation(ContractFailureCode.MALFORMED_TIMESTAMP, "created_at_utc must be UTC")


@dataclass(frozen=True, init=False)
class CommonEnvelope:
    schema_version: SchemaVersion
    record_id: RecordId
    run_id: RunId
    attempt_id: AttemptId
    created_at_utc: datetime
    producer_component: str
    repository_revision: RepositoryRevision
    policy_version: str
    environment_profile_id: str
    lineage_parent_ids: tuple[LineageParentRef, ...]
    payload_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CommonEnvelope:
        raise TypeError("CommonEnvelope is created only from exact canonical payload bytes")

    def to_dict(self) -> dict[str, object]:
        _validate_common_envelope_structure(self)
        return {
            "schema_version": self.schema_version.value,
            "record_id": self.record_id.to_dict(),
            "run_id": self.run_id.to_dict(),
            "attempt_id": self.attempt_id.to_dict(),
            "created_at_utc": _format_utc_timestamp(self.created_at_utc),
            "producer_component": self.producer_component,
            "repository_revision": self.repository_revision.to_dict(),
            "policy_version": self.policy_version,
            "environment_profile_id": self.environment_profile_id,
            "lineage_parent_ids": [item.to_dict() for item in self.lineage_parent_ids],
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_dict(
        cls,
        value: object,
        *,
        canonical_payload_bytes: bytes,
        lineage_parent_canonical_bytes: tuple[bytes, ...] = (),
    ) -> CommonEnvelope:
        return common_envelope_from_dict(
            value,
            canonical_payload_bytes=canonical_payload_bytes,
            lineage_parent_canonical_bytes=lineage_parent_canonical_bytes,
        )


def _validate_lineage_tuple(value: object) -> tuple[LineageParentRef, ...]:
    items = _require_exact_tuple(value, "lineage_parent_ids")
    seen: set[str] = set()
    for item in items:
        _validate_lineage_parent_ref(item)
        identity = item.parent_record_id.value
        if identity in seen:
            raise _violation(
                ContractFailureCode.DUPLICATE_LINEAGE_PARENT,
                "lineage_parent_ids contains a duplicate parent identity",
            )
        seen.add(identity)
    return items


def _validate_common_envelope_structure(envelope: CommonEnvelope) -> None:
    if type(envelope) is not CommonEnvelope:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "common_envelope must be an exact CommonEnvelope",
        )
    _require_trusted_seal(envelope, "common_envelope")
    if envelope.schema_version is not SchemaVersion.COMMON_ENVELOPE_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "common envelope schema is unknown",
        )
    _validate_record_id_consistency(envelope.record_id)
    if type(envelope.run_id) is not RunId:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "run_id must be an exact RunId")
    envelope.run_id.to_dict()
    if type(envelope.attempt_id) is not AttemptId:
        raise _violation(ContractFailureCode.TYPE_MISMATCH, "attempt_id must be an exact AttemptId")
    envelope.attempt_id.to_dict()
    _validate_utc_timestamp(envelope.created_at_utc)
    _require_component(envelope.producer_component, "producer_component")
    _validate_repository_revision(envelope.repository_revision)
    _require_versioned(envelope.policy_version, "policy_version")
    _require_versioned(envelope.environment_profile_id, "environment_profile_id")
    _validate_lineage_tuple(envelope.lineage_parent_ids)
    _require_sha256(envelope.payload_sha256, "payload_sha256")


def create_common_envelope(
    *,
    schema_version: SchemaVersion,
    identity_domain: IdentityDomain,
    canonical_payload_bytes: bytes,
    run_id: RunId,
    attempt_id: AttemptId,
    created_at_utc: datetime,
    producer_component: str,
    repository_revision: RepositoryRevision,
    policy_version: str,
    environment_profile_id: str,
    lineage_parent_ids: tuple[LineageParentRef, ...],
) -> CommonEnvelope:
    if schema_version is not SchemaVersion.COMMON_ENVELOPE_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "common envelope schema is unknown",
        )
    payload = _require_exact_bytes(canonical_payload_bytes, "canonical_payload_bytes")
    envelope = object.__new__(CommonEnvelope)
    object.__setattr__(envelope, "schema_version", schema_version)
    object.__setattr__(
        envelope,
        "record_id",
        compute_record_id(domain=identity_domain, canonical_bytes=payload),
    )
    object.__setattr__(envelope, "run_id", run_id)
    object.__setattr__(envelope, "attempt_id", attempt_id)
    object.__setattr__(envelope, "created_at_utc", created_at_utc)
    object.__setattr__(envelope, "producer_component", producer_component)
    object.__setattr__(envelope, "repository_revision", repository_revision)
    object.__setattr__(envelope, "policy_version", policy_version)
    object.__setattr__(envelope, "environment_profile_id", environment_profile_id)
    object.__setattr__(envelope, "lineage_parent_ids", lineage_parent_ids)
    object.__setattr__(envelope, "payload_sha256", compute_payload_sha256(canonical_bytes=payload))
    object.__setattr__(envelope, "_trusted_seal", _TRUSTED_SEAL)
    validate_common_envelope(envelope, canonical_payload_bytes=payload)
    return envelope


def validate_common_envelope(
    envelope: CommonEnvelope,
    *,
    canonical_payload_bytes: bytes,
) -> None:
    _validate_common_envelope_structure(envelope)
    payload = _require_exact_bytes(canonical_payload_bytes, "canonical_payload_bytes")
    expected_payload_sha = compute_payload_sha256(canonical_bytes=payload)
    if envelope.payload_sha256 != expected_payload_sha:
        raise _violation(
            ContractFailureCode.PAYLOAD_HASH_MISMATCH,
            "payload_sha256 does not match the supplied exact canonical payload bytes",
        )
    validate_record_id(envelope.record_id, canonical_bytes=payload)


def common_envelope_from_dict(
    value: object,
    *,
    canonical_payload_bytes: bytes,
    lineage_parent_canonical_bytes: tuple[bytes, ...] = (),
) -> CommonEnvelope:
    fields = (
        "schema_version",
        "record_id",
        "run_id",
        "attempt_id",
        "created_at_utc",
        "producer_component",
        "repository_revision",
        "policy_version",
        "environment_profile_id",
        "lineage_parent_ids",
        "payload_sha256",
    )
    data = _require_exact_dict(value, required=fields, field_name="common_envelope")
    schema = _parse_enum(
        data["schema_version"],
        SchemaVersion,
        ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
        "common_envelope.schema_version",
    )
    if schema is not SchemaVersion.COMMON_ENVELOPE_V1:
        raise _violation(
            ContractFailureCode.UNKNOWN_SCHEMA_VERSION,
            "common envelope schema is unknown",
        )
    payload = _require_exact_bytes(canonical_payload_bytes, "canonical_payload_bytes")
    if type(data["lineage_parent_ids"]) is not list:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "lineage_parent_ids transport value must be an exact list",
        )
    parent_bytes = _require_exact_tuple(
        lineage_parent_canonical_bytes,
        "lineage_parent_canonical_bytes",
    )
    if len(parent_bytes) != len(data["lineage_parent_ids"]):
        raise _violation(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            "each lineage parent requires exact canonical bytes for recomputation",
        )
    lineage = tuple(
        LineageParentRef.from_dict(item, parent_canonical_bytes=canonical_parent)
        for item, canonical_parent in zip(data["lineage_parent_ids"], parent_bytes)
    )
    supplied_record = record_id_from_dict(data["record_id"], canonical_bytes=payload)
    supplied_payload_sha = _require_sha256(data["payload_sha256"], "payload_sha256")
    expected_payload_sha = compute_payload_sha256(canonical_bytes=payload)
    if supplied_payload_sha != expected_payload_sha:
        raise _violation(
            ContractFailureCode.PAYLOAD_HASH_MISMATCH,
            "payload_sha256 does not match the supplied exact canonical payload bytes",
        )
    assert isinstance(schema, SchemaVersion)
    envelope = create_common_envelope(
        schema_version=schema,
        identity_domain=supplied_record.domain,
        canonical_payload_bytes=payload,
        run_id=RunId.from_dict(data["run_id"]),
        attempt_id=AttemptId.from_dict(data["attempt_id"]),
        created_at_utc=_parse_utc_timestamp(data["created_at_utc"]),
        producer_component=data["producer_component"],
        repository_revision=RepositoryRevision.from_dict(data["repository_revision"]),
        policy_version=data["policy_version"],
        environment_profile_id=data["environment_profile_id"],
        lineage_parent_ids=lineage,
    )
    if envelope.record_id != supplied_record:
        raise _violation(ContractFailureCode.RECORD_ID_MISMATCH, "record_id changed during parsing")
    return envelope


__all__ = (
    "IDENTITY_PROTOCOL_VERSION",
    "IDENTITY_PREIMAGE_PREFIX",
    "RECORD_ID_TEXT_SEPARATOR",
    "UTC_TIMESTAMP_FORMAT",
    "AUTHORITY_CONFIGURATION_SCHEMA_V1",
    "HISTORY_ANCHOR_SCHEMA_V1",
    "ContractFailureCode",
    "ContractViolation",
    "SchemaVersion",
    "IdentityDomain",
    "AuthorityRole",
    "HistoryDomain",
    "ReasonCode",
    "LifecycleReasonCode",
    "RepositoryRevisionKind",
    "LineageEdgeKind",
    "ClaimedRecordId",
    "RecordId",
    "RunId",
    "AttemptId",
    "ProposalId",
    "AuthorityDecisionId",
    "ExecutionId",
    "ActorIdentity",
    "AuthorityIdentity",
    "RepositoryRevision",
    "LineageParentRef",
    "DelegationStep",
    "IndependenceProof",
    "CommonEnvelope",
    "Stage4AuthorityConfiguration",
    "Stage4AuthorityHandle",
    "HistoryAnchor",
    "compute_payload_sha256",
    "compute_record_id",
    "validate_record_id",
    "record_id_from_dict",
    "record_id_from_text",
    "compute_proposal_id",
    "create_independence_proof",
    "validate_independence_proof",
    "independence_proof_from_dict",
    "compute_authority_decision_id",
    "authority_decision_id_from_dict",
    "compute_execution_id",
    "execution_id_from_dict",
    "create_common_envelope",
    "validate_common_envelope",
    "common_envelope_from_dict",
    "create_stage4_authority_configuration",
    "validate_stage4_authority_configuration",
    "stage4_authority_configuration_from_dict",
    "create_stage4_authority_handle",
    "require_stage4_authority_handle",
    "compute_ordered_history_roots",
    "create_history_anchor",
    "validate_history_anchor",
    "history_anchor_from_dict",
    "validate_history_anchor_extension",
)
