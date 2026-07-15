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
    COMMON_ENVELOPE_V1 = "synapse.stage4.gold.common-envelope/v1"
    INDEPENDENCE_PROOF_V1 = "synapse.stage4.gold.independence-proof/v1"


class IdentityDomain(str, Enum):
    COMMON_RECORD = "synapse.stage4.gold.common-record/v1"
    PROPOSAL = "synapse.stage4.gold.proposal/v1"
    AUTHORITY_DECISION = "synapse.stage4.gold.authority-decision/v1"
    EXECUTION = "synapse.stage4.gold.execution/v1"


class AuthorityRole(str, Enum):
    TAINT_REVIEWER = "TAINT_REVIEWER"
    SUPERSESSION_REVIEWER = "SUPERSESSION_REVIEWER"
    PLAN_REVIEWER = "PLAN_REVIEWER"
    PUBLICATION_REVIEWER = "PUBLICATION_REVIEWER"


class ReasonCode(str, Enum):
    TAINT_REVIEW_INDEPENDENT = "TAINT_REVIEW_INDEPENDENT"
    SUPERSESSION_REVIEW_INDEPENDENT = "SUPERSESSION_REVIEW_INDEPENDENT"
    PLAN_REVIEW_INDEPENDENT = "PLAN_REVIEW_INDEPENDENT"
    PUBLICATION_REVIEW_INDEPENDENT = "PUBLICATION_REVIEW_INDEPENDENT"


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
        _require_identifier(self.value, "actor_identity")

    def to_dict(self) -> dict[str, str]:
        _require_identifier(self.value, "actor_identity")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> ActorIdentity:
        data = _require_exact_dict(value, required=("value",), field_name="actor_identity")
        return cls(value=data["value"])


@dataclass(frozen=True)
class AuthorityIdentity:
    value: str

    def __post_init__(self) -> None:
        _require_identifier(self.value, "authority_identity")

    def to_dict(self) -> dict[str, str]:
        _require_identifier(self.value, "authority_identity")
        return {"value": self.value}

    @classmethod
    def from_dict(cls, value: object) -> AuthorityIdentity:
        data = _require_exact_dict(value, required=("value",), field_name="authority_identity")
        return cls(value=data["value"])


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
    if type(value.delegator) is not ActorIdentity or type(value.delegate) is not ActorIdentity:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "delegation identities must be exact ActorIdentity values",
        )
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
        if type(item) is not ActorIdentity:
            raise _violation(
                ContractFailureCode.TYPE_MISMATCH,
                f"{field_name} entries must be exact ActorIdentity values",
            )
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


def _delegation_reaches(
    steps: tuple[DelegationStep, ...],
    *,
    origin: str,
    forbidden: set[str],
) -> bool:
    graph: dict[str, set[str]] = {}
    for step in steps:
        graph.setdefault(step.delegator.value, set()).add(step.delegate.value)
    pending = list(graph.get(origin, set()))
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node in forbidden:
            return True
        if node not in visited:
            visited.add(node)
            pending.extend(graph.get(node, set()))
    return False


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
    if type(proof.authority_identity) is not AuthorityIdentity:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "authority_identity must be an exact AuthorityIdentity",
        )
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
    if type(proof.proposer_identity) is not ActorIdentity:
        raise _violation(
            ContractFailureCode.UNPROVEN_INDEPENDENCE,
            "proposer_identity must be an exact ActorIdentity",
        )
    if proof.executor_identity is not None and type(proof.executor_identity) is not ActorIdentity:
        raise _violation(
            ContractFailureCode.TYPE_MISMATCH,
            "executor_identity must be None or an exact ActorIdentity",
        )
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
    delegated_back_targets = {
        *(item.value for item in producers),
        proof.proposer_identity.value,
    }
    if _delegation_reaches(steps, origin=authority, forbidden=delegated_back_targets):
        raise _violation(
            ContractFailureCode.DELEGATED_BACK_AUTHORITY,
            "authority delegation returns control to a producer or proposer",
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


def compute_authority_decision_id(
    *,
    canonical_bytes: bytes,
    independence_proof: IndependenceProof,
) -> AuthorityDecisionId:
    validate_independence_proof(independence_proof)
    return _make_authority_decision_id(
        compute_record_id(
            domain=IdentityDomain.AUTHORITY_DECISION,
            canonical_bytes=canonical_bytes,
        )
    )


def authority_decision_id_from_dict(
    value: object,
    *,
    canonical_bytes: bytes,
    independence_proof: IndependenceProof,
) -> AuthorityDecisionId:
    validate_independence_proof(independence_proof)
    data = _require_exact_dict(value, required=("record_id",), field_name="authority_decision_id")
    record_id = record_id_from_dict(data["record_id"], canonical_bytes=canonical_bytes)
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
    "ContractFailureCode",
    "ContractViolation",
    "SchemaVersion",
    "IdentityDomain",
    "AuthorityRole",
    "ReasonCode",
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
)
