"""Stage 4 §22 four authority gates and ConsumptionDecision.

Stage 4 admits knowledge through four independent decisions, never one boolean:

* **Ingestion** — may a candidate be extracted from this source at all?
* **Publication** — may a verified object be written into the library?
* **Retrieval** — is an object applicable to this consumer context?
* **Consumption** — the last check, immediately before replay or worker delivery.

Each gate has its own inputs, its own closed reason vocabulary and its own
decision record. A decision from one gate is never valid at another, and
publication admission never transfers into retrieval or consumption.

Four rules carry the security argument.

*Nothing passes by omission.* Every gate declares the dimensions it must check,
and a decision that does not record all of them is rejected rather than treated
as a pass. A dependency a port declares unreachable becomes a REJECT with a
dependency reason — the same reason a policy webhook configured to fail closed
turns an unreachable decision point into a refusal instead of an allow. There is
no path on which an error produces ADMIT.

Failures are classified rather than pooled. Only a declared
``GateDependencyUnavailable`` is an outage; a port that raises something else,
or answers with the wrong type, has broken its own contract and is reported as
that. Both outcomes block, so the safety property is identical either way — but
an operator reading the record can tell a store that was down from an adapter
that was wrong.

*Taint only ever loosens under independent authority.* Effective taint is
recomputed at consumption from the source, derivation and authority chain; a
stored, already-reduced profile without its complete chain is refused.
Successful execution, passing worker tests, a high retrieval score, a readable
summary or a fresh hash are never grounds for relaxation. This is the
declassification rule of decentralized information-flow control: a label is
monotone, and lowering it requires a privilege held by an independent principal
rather than by the data or by whoever produced it.

*The last gate sees the final state.* Consumption re-derives lifecycle,
compatibility, taint, scope and policy against the state that will actually be
used, because a verdict computed earlier describes an earlier world. The heads
it reads come from one observation, not several: a set mixing a fresh admission
anchor with a lifecycle anchor captured before the query would admit an object
that has since been revoked.

*An admitted verdict is durable, and it is the only door.* §22 requires
decisions to be immutable, persisted and linked in lineage, so a consumption
ADMIT is usable only once it is in the append-only journal and still there when
asked again. What it produces is an ``AdmittedKnowledgeHandle`` — the sole
carrier of consumable knowledge. A replay or a worker accepts a handle and
nothing else, which makes "no path bypasses the consumption gate" a property of
the types rather than of reviewer diligence.

What a holder must do with a handle at the moment of delivery — re-read the
world, and receive a record naming the knowledge it just revalidated — belongs
to ``point_of_use``, which attaches to this owner as an adapter and is imported
by it nowhere.

The module owns gate semantics only. Taint chains, lifecycle state,
compatibility evidence and snapshot boundaries arrive through injected ports, so
this owner imports neither the knowledge owner nor the stores it consults.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Callable, Mapping, Protocol, runtime_checkable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    ContractViolation,
    GateCheckedDimension,
    GateDecisionKind,
    GateKind,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    Stage4AuthorityHandle,
    compute_record_id,
    gate_requires_committed_boundary,
    gate_requires_consumer_context,
    gate_stage_index,
    require_stage4_authority_handle,
    validate_gate_progression,
    validate_record_id,
)

GATE_DECISION_V1 = SchemaVersion.GATE_DECISION_V1

_DECISION_SEAL = object()
_CONTROLLER_SEAL = object()
_CHAIN_SEAL = object()
_RECEIPT_SEAL = object()
_HEAD_SET_SEAL = object()
_HANDLE_SEAL = object()

#: Which authority role may decide which gate. §22 requires four independent
#: decisions with their own inputs and reason vocabularies; it does not require
#: four different authorities, and it does not permit one role to stand in for
#: another. An earlier revision of this module enforced the opposite of both —
#: it demanded that every decision in a chain carry the *same* identity, which
#: forbids the legitimate configuration of separate reviewers, while accepting
#: any role at any gate.
_ALLOWED_ROLES: dict[GateKind, frozenset[AuthorityRole]] = {
    GateKind.INGESTION: frozenset(
        {AuthorityRole.INGESTION_GATE_EVALUATOR, AuthorityRole.GOVERNING_HUMAN}
    ),
    GateKind.PUBLICATION: frozenset(
        {AuthorityRole.PUBLICATION_GATE_EVALUATOR, AuthorityRole.GOVERNING_HUMAN}
    ),
    GateKind.RETRIEVAL: frozenset(
        {AuthorityRole.RETRIEVAL_GATE_EVALUATOR, AuthorityRole.GOVERNING_HUMAN}
    ),
    GateKind.CONSUMPTION: frozenset(
        {AuthorityRole.CONSUMPTION_GATE_EVALUATOR, AuthorityRole.GOVERNING_HUMAN}
    ),
}


def allowed_authority_roles(gate: GateKind) -> frozenset[AuthorityRole]:
    """The closed set of roles that may decide this gate."""

    if type(gate) is not GateKind:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate must be an exact GateKind")
    return _ALLOWED_ROLES[gate]


def require_role_for_gate(role: AuthorityRole, *, gate: GateKind) -> AuthorityRole:
    """Refuse a decision signed by a role that has no standing at this gate."""

    if type(role) is not AuthorityRole:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "authority role must be exact")
    if role not in allowed_authority_roles(gate):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED,
            f"{role.value} may not decide the {gate.value} gate",
        )
    return role


#: The authority domains a consumption decision must observe coherently. §22
#: requires lifecycle, taint, admission, compatibility and the boundary to be
#: re-read at the point of use; provenance is included because a taint chain is
#: only as current as the derivation records behind it.
AUTHORITY_HEAD_DOMAINS = (
    "lifecycle",
    "provenance",
    "taint",
    "admission",
    "compatibility",
)

_MAX_SUBJECTS = 512
_MAX_REASONS = 32
_MAX_DIAGNOSTIC_KEYS = 32
_MAX_DIAGNOSTIC_TEXT = 256
_IDENTIFIER_MAX = 128

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class AdmissionFailureCode(str, Enum):
    """Closed, fail-closed vocabulary for §22 gate failures."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    WRONG_AUTHORITY_HANDLE = "WRONG_AUTHORITY_HANDLE"
    AUTHORITY_NOT_INDEPENDENT = "AUTHORITY_NOT_INDEPENDENT"
    GATE_KIND_MISMATCH = "GATE_KIND_MISMATCH"
    GATE_DECISION_REUSED = "GATE_DECISION_REUSED"
    GATE_SEQUENCE_VIOLATION = "GATE_SEQUENCE_VIOLATION"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    CONSUMER_CONTEXT_REQUIRED = "CONSUMER_CONTEXT_REQUIRED"
    CONSUMER_CONTEXT_FORBIDDEN = "CONSUMER_CONTEXT_FORBIDDEN"
    BOUNDARY_REQUIRED = "BOUNDARY_REQUIRED"
    DIMENSION_NOT_CHECKED = "DIMENSION_NOT_CHECKED"
    UNKNOWN_REASON_CODE = "UNKNOWN_REASON_CODE"
    REASON_CODES_REQUIRED = "REASON_CODES_REQUIRED"
    REASON_CODES_UNORDERED = "REASON_CODES_UNORDERED"
    CONTRADICTORY_DIAGNOSTICS = "CONTRADICTORY_DIAGNOSTICS"
    DIAGNOSTICS_NOT_STRICT_JSON = "DIAGNOSTICS_NOT_STRICT_JSON"
    DECISION_IDENTITY_MISMATCH = "DECISION_IDENTITY_MISMATCH"
    DUPLICATE_SUBJECT = "DUPLICATE_SUBJECT"
    UNORDERED_SUBJECT = "UNORDERED_SUBJECT"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    STALE_DECISION = "STALE_DECISION"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    ORACLE_EXPANSION = "ORACLE_EXPANSION"
    CAPABILITY_EXPANSION = "CAPABILITY_EXPANSION"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    NOT_ADMITTED = "NOT_ADMITTED"
    SEQUENCE_NOT_MONOTONIC = "SEQUENCE_NOT_MONOTONIC"
    DECISION_NOT_DURABLE = "DECISION_NOT_DURABLE"
    JOURNAL_UNAVAILABLE = "JOURNAL_UNAVAILABLE"
    HEAD_OBSERVATION_INCOMPLETE = "HEAD_OBSERVATION_INCOMPLETE"
    HEAD_OBSERVATION_STALE = "HEAD_OBSERVATION_STALE"
    AUTHORITY_ROLE_NOT_PERMITTED = "AUTHORITY_ROLE_NOT_PERMITTED"
    JOURNAL_ROLLED_BACK = "JOURNAL_ROLLED_BACK"
    PROBE_CONTRACT_VIOLATION = "PROBE_CONTRACT_VIOLATION"


class AdmissionViolation(ValueError):
    """A typed, fail-closed gate error carrying no subject payload."""

    def __init__(self, failure_code: AdmissionFailureCode, detail: str) -> None:
        if type(failure_code) is not AdmissionFailureCode:
            raise TypeError("failure_code must be an exact AdmissionFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: AdmissionFailureCode, detail: str) -> AdmissionViolation:
    return AdmissionViolation(code, detail)


# ---------------------------------------------------------------------------
# Per-gate reason vocabularies
#
# OD-09 ("four gate reason vocabularies and decision precedence") is an open
# decision in the specification. What follows is this patch's proposed closed
# vocabulary and precedence; it is deliberately narrow, machine-readable and
# free-text-free, and it requires human ratification before it counts as frozen.
# ---------------------------------------------------------------------------


class IngestionReason(str, Enum):
    SOURCE_CLASSIFIED = "SOURCE_CLASSIFIED"
    SOURCE_UNCLASSIFIED = "SOURCE_UNCLASSIFIED"
    SECRET_LIKE_CONTENT = "SECRET_LIKE_CONTENT"
    EXECUTABLE_CONTENT_UNVERIFIED = "EXECUTABLE_CONTENT_UNVERIFIED"
    INSTRUCTION_LIKE_CONTENT_NOT_ISOLATED = "INSTRUCTION_LIKE_CONTENT_NOT_ISOLATED"
    PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"
    SOURCE_QUARANTINED = "SOURCE_QUARANTINED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class PublicationReason(str, Enum):
    EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED"
    ATTESTATION_MISSING = "ATTESTATION_MISSING"
    LIFECYCLE_NOT_ATTESTED = "LIFECYCLE_NOT_ATTESTED"
    TAINT_BLOCKS_PUBLICATION = "TAINT_BLOCKS_PUBLICATION"
    SCOPE_EXPANSION_REQUESTED = "SCOPE_EXPANSION_REQUESTED"
    SUBJECT_QUARANTINED = "SUBJECT_QUARANTINED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class RetrievalReason(str, Enum):
    CONTEXT_COMPATIBLE = "CONTEXT_COMPATIBLE"
    COMPATIBILITY_INCOMPLETE = "COMPATIBILITY_INCOMPLETE"
    COMPATIBILITY_REJECTED = "COMPATIBILITY_REJECTED"
    LIFECYCLE_NOT_CONSUMABLE = "LIFECYCLE_NOT_CONSUMABLE"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
    TAINT_BLOCKS_RETRIEVAL = "TAINT_BLOCKS_RETRIEVAL"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    SUBJECT_QUARANTINED = "SUBJECT_QUARANTINED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class ConsumptionReason(str, Enum):
    REVALIDATION_PASSED = "REVALIDATION_PASSED"
    COMPATIBILITY_DRIFT = "COMPATIBILITY_DRIFT"
    LIFECYCLE_CHANGED = "LIFECYCLE_CHANGED"
    TAINT_CHAIN_INCOMPLETE = "TAINT_CHAIN_INCOMPLETE"
    TAINT_BLOCKS_CONSUMPTION = "TAINT_BLOCKS_CONSUMPTION"
    SNAPSHOT_BOUNDARY_INVALID = "SNAPSHOT_BOUNDARY_INVALID"
    POLICY_VERSION_CHANGED = "POLICY_VERSION_CHANGED"
    SCOPE_EXPANSION = "SCOPE_EXPANSION"
    ORACLE_EXPANSION = "ORACLE_EXPANSION"
    CAPABILITY_EXPANSION = "CAPABILITY_EXPANSION"
    SUBJECT_QUARANTINED = "SUBJECT_QUARANTINED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


_GATE_REASON_VOCABULARY: dict[GateKind, type[Enum]] = {
    GateKind.INGESTION: IngestionReason,
    GateKind.PUBLICATION: PublicationReason,
    GateKind.RETRIEVAL: RetrievalReason,
    GateKind.CONSUMPTION: ConsumptionReason,
}

# Reasons that admit. Every other reason in a vocabulary is blocking, so a new
# reason is blocking by default rather than silently permissive.
_ADMITTING_REASONS: dict[GateKind, frozenset[str]] = {
    GateKind.INGESTION: frozenset({IngestionReason.SOURCE_CLASSIFIED.value}),
    GateKind.PUBLICATION: frozenset({PublicationReason.EVIDENCE_VALIDATED.value}),
    GateKind.RETRIEVAL: frozenset({RetrievalReason.CONTEXT_COMPATIBLE.value}),
    GateKind.CONSUMPTION: frozenset({ConsumptionReason.REVALIDATION_PASSED.value}),
}

_QUARANTINE_REASONS: frozenset[str] = frozenset(
    {
        IngestionReason.SOURCE_QUARANTINED.value,
        PublicationReason.SUBJECT_QUARANTINED.value,
        RetrievalReason.SUBJECT_QUARANTINED.value,
        ConsumptionReason.SUBJECT_QUARANTINED.value,
    }
)

_REVIEW_REASONS: frozenset[str] = frozenset(
    {
        IngestionReason.HUMAN_REVIEW_REQUIRED.value,
        PublicationReason.HUMAN_REVIEW_REQUIRED.value,
        RetrievalReason.HUMAN_REVIEW_REQUIRED.value,
        ConsumptionReason.HUMAN_REVIEW_REQUIRED.value,
    }
)

# Decision precedence: a blocking finding always dominates an admitting one, so
# a gate that observed both never resolves to ADMIT.
_DECISION_PRECEDENCE: dict[GateDecisionKind, int] = {
    GateDecisionKind.REJECT: 3,
    GateDecisionKind.QUARANTINE: 2,
    GateDecisionKind.REQUIRE_REVIEW: 1,
    GateDecisionKind.ADMIT: 0,
}

# Dimensions each gate must record as checked. A decision missing one of these
# is refused: an unchecked dimension is never a passed dimension.
_REQUIRED_DIMENSIONS: dict[GateKind, frozenset[GateCheckedDimension]] = {
    GateKind.INGESTION: frozenset(
        {GateCheckedDimension.SOURCE_TAINT, GateCheckedDimension.PROVENANCE}
    ),
    GateKind.PUBLICATION: frozenset(
        {
            GateCheckedDimension.SOURCE_TAINT,
            GateCheckedDimension.PROVENANCE,
            GateCheckedDimension.LIFECYCLE,
            GateCheckedDimension.SCOPE,
        }
    ),
    GateKind.RETRIEVAL: frozenset(
        {
            GateCheckedDimension.SOURCE_TAINT,
            GateCheckedDimension.PROVENANCE,
            GateCheckedDimension.BINDING,
            GateCheckedDimension.REVISION,
            GateCheckedDimension.LIFECYCLE,
            GateCheckedDimension.SCOPE,
            GateCheckedDimension.ENVIRONMENT,
            GateCheckedDimension.CONFLICTS,
        }
    ),
    # Consumption is the last barrier and checks every declared dimension.
    GateKind.CONSUMPTION: frozenset(GateCheckedDimension),
}


def gate_reason_vocabulary(gate: GateKind) -> type[Enum]:
    """Return the closed reason vocabulary owned by ``gate``."""

    gate_stage_index(gate)
    return _GATE_REASON_VOCABULARY[gate]


def required_dimensions(gate: GateKind) -> frozenset[GateCheckedDimension]:
    """Return the dimensions ``gate`` must record as checked."""

    gate_stage_index(gate)
    return _REQUIRED_DIMENSIONS[gate]


def resolve_decision_kind(gate: GateKind, reasons: tuple[str, ...]) -> GateDecisionKind:
    """Resolve the decision kind implied by an exact reason set.

    Precedence is fixed: any blocking reason outranks an admitting one, an
    unknown reason cannot be weighed and is refused, and an empty reason set is
    never an admission.
    """

    gate_stage_index(gate)
    if type(reasons) is not tuple or not reasons:
        raise _fail(AdmissionFailureCode.REASON_CODES_REQUIRED, "a decision requires at least one reason")
    vocabulary = {item.value for item in _GATE_REASON_VOCABULARY[gate]}
    unknown = [item for item in reasons if item not in vocabulary]
    if unknown:
        raise _fail(AdmissionFailureCode.UNKNOWN_REASON_CODE, "reason code is outside the gate vocabulary")
    admitting = _ADMITTING_REASONS[gate]
    blocking = [item for item in reasons if item not in admitting]
    if not blocking:
        return GateDecisionKind.ADMIT
    if any(item in _QUARANTINE_REASONS for item in blocking):
        return GateDecisionKind.QUARANTINE
    if all(item in _REVIEW_REASONS for item in blocking):
        return GateDecisionKind.REQUIRE_REVIEW
    return GateDecisionKind.REJECT


# ---------------------------------------------------------------------------
# Exact-value helpers
# ---------------------------------------------------------------------------


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID
    )


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(AdmissionFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is invalid")
    if value.strip() != value:
        raise _fail(AdmissionFailureCode.MALFORMED_IDENTIFIER, f"{field_name} has padding")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise _fail(AdmissionFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    if value.utcoffset() != timezone.utc.utcoffset(None):
        raise _fail(AdmissionFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    return value


def _subject_key(value: HashBoundRef) -> str:
    return f"{value.kind.value}\x00{value.ref_id}\x00{value.sha256}"


def _subjects(value: object) -> tuple[HashBoundRef, ...]:
    if type(value) is not tuple or not value:
        raise _fail(AdmissionFailureCode.SUBJECT_MISMATCH, "subject_refs must be a non-empty exact tuple")
    if len(value) > _MAX_SUBJECTS:
        raise _fail(AdmissionFailureCode.RESOURCE_LIMIT_EXCEEDED, "subject_refs exceeds the gate limit")
    for item in value:
        if type(item) is not HashBoundRef:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "subject_refs must contain exact HashBoundRef")
    keys = [_subject_key(item) for item in value]
    if len(set(keys)) != len(keys):
        raise _fail(AdmissionFailureCode.DUPLICATE_SUBJECT, "subject_refs contains a duplicate")
    if keys != sorted(keys):
        raise _fail(AdmissionFailureCode.UNORDERED_SUBJECT, "subject_refs is not canonically ordered")
    return value


def _reason_tuple(gate: GateKind, value: object) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise _fail(AdmissionFailureCode.REASON_CODES_REQUIRED, "reason_codes must be a non-empty exact tuple")
    if len(value) > _MAX_REASONS:
        raise _fail(AdmissionFailureCode.RESOURCE_LIMIT_EXCEEDED, "reason_codes exceeds the gate limit")
    vocabulary = {item.value for item in _GATE_REASON_VOCABULARY[gate]}
    codes: list[str] = []
    for item in value:
        text = item.value if isinstance(item, Enum) else item
        if type(text) is not str or text not in vocabulary:
            raise _fail(AdmissionFailureCode.UNKNOWN_REASON_CODE, "reason code is outside the gate vocabulary")
        codes.append(text)
    if len(set(codes)) != len(codes):
        raise _fail(AdmissionFailureCode.REASON_CODES_UNORDERED, "reason_codes contains a duplicate")
    if codes != sorted(codes):
        raise _fail(AdmissionFailureCode.REASON_CODES_UNORDERED, "reason_codes is not canonically ordered")
    return tuple(codes)


def _dimensions(gate: GateKind, value: object) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions must be an exact tuple")
    names: list[str] = []
    for item in value:
        if type(item) is not GateCheckedDimension:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions must contain exact members")
        names.append(item.value)
    if len(set(names)) != len(names):
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions contains a duplicate")
    if names != sorted(names):
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions is not canonically ordered")
    missing = {item.value for item in _REQUIRED_DIMENSIONS[gate]} - set(names)
    if missing:
        raise _fail(
            AdmissionFailureCode.DIMENSION_NOT_CHECKED,
            f"{gate.value} decision omits {len(missing)} required dimension(s)",
        )
    return tuple(names)


def _diagnostics(value: object) -> dict[str, str]:
    """Return strict, non-authoritative diagnostics.

    Diagnostics are flat string pairs. They explain a decision, never carry it,
    and never echo subject payload, so they cannot become a side channel for
    content the gate refused to admit.
    """

    if type(value) is not dict:
        raise _fail(AdmissionFailureCode.DIAGNOSTICS_NOT_STRICT_JSON, "diagnostics must be an exact dict")
    if len(value) > _MAX_DIAGNOSTIC_KEYS:
        raise _fail(AdmissionFailureCode.RESOURCE_LIMIT_EXCEEDED, "diagnostics exceeds the key limit")
    result: dict[str, str] = {}
    for key in sorted(value):
        if type(key) is not str or not key or len(key) > 64:
            raise _fail(AdmissionFailureCode.DIAGNOSTICS_NOT_STRICT_JSON, "diagnostic key is invalid")
        item = value[key]
        if type(item) is not str or len(item) > _MAX_DIAGNOSTIC_TEXT:
            raise _fail(AdmissionFailureCode.DIAGNOSTICS_NOT_STRICT_JSON, "diagnostic value must be bounded text")
        result[key] = item
    return result


def _require_no_contradiction(
    gate: GateKind, decision_kind: GateDecisionKind, reasons: tuple[str, ...]
) -> None:
    """Refuse a decision that does not follow from its own reasons."""

    implied = resolve_decision_kind(gate, reasons)
    if implied is not decision_kind:
        raise _fail(
            AdmissionFailureCode.CONTRADICTORY_DIAGNOSTICS,
            f"{gate.value} decision {decision_kind.value} contradicts its reason codes",
        )


# ---------------------------------------------------------------------------
# GateDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class GateDecision:
    """One immutable, identity-bound verdict from exactly one gate."""

    schema_version: SchemaVersion
    gate_decision_id: RecordId
    gate_kind: GateKind
    subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef | None
    boundary_ref: HashBoundRef | None
    decision_kind: GateDecisionKind
    reason_codes: tuple[str, ...]
    policy_version: str
    checked_dimensions: tuple[str, ...]
    diagnostics: dict[str, str]
    authority_identity: AuthorityIdentity
    authority_role: AuthorityRole
    decided_at_utc: datetime
    predecessor_decision_digest: str | None
    sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> GateDecision:
        raise TypeError("GateDecision is produced only by a configured gate controller")

    def to_dict(self) -> dict[str, object]:
        validate_gate_decision(self)
        return {**_decision_payload(self), "gate_decision_id": self.gate_decision_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_gate_decision(self)
        return _canonical(_decision_payload(self))

    @property
    def admitted(self) -> bool:
        validate_gate_decision(self)
        return self.decision_kind is GateDecisionKind.ADMIT

    def subject_keys(self) -> tuple[str, ...]:
        validate_gate_decision(self)
        return tuple(_subject_key(item) for item in self.subject_refs)


def _decision_payload(value: GateDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "gate_kind": value.gate_kind.value,
        "subject_refs": [item.to_dict() for item in value.subject_refs],
        "consumer_context_ref": None if value.consumer_context_ref is None else value.consumer_context_ref.to_dict(),
        "boundary_ref": None if value.boundary_ref is None else value.boundary_ref.to_dict(),
        "decision_kind": value.decision_kind.value,
        "reason_codes": list(value.reason_codes),
        "policy_version": value.policy_version,
        "checked_dimensions": list(value.checked_dimensions),
        "diagnostics": dict(value.diagnostics),
        "authority_identity": value.authority_identity.to_dict(),
        "authority_role": value.authority_role.value,
        "decided_at_utc": value.decided_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        "predecessor_decision_digest": value.predecessor_decision_digest,
        "sequence": value.sequence,
    }


def validate_gate_decision(value: GateDecision) -> None:
    if type(value) is not GateDecision or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate decision is not controller sealed")
    if value.schema_version is not GATE_DECISION_V1:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "gate decision schema is unknown")
    if type(value.gate_kind) is not GateKind or type(value.decision_kind) is not GateDecisionKind:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision enums are invalid")
    require_role_for_gate(value.authority_role, gate=value.gate_kind)
    if type(value.authority_identity) is not AuthorityIdentity or type(value.authority_role) is not AuthorityRole:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision authority is invalid")
    _subjects(value.subject_refs)
    _reason_tuple(value.gate_kind, value.reason_codes)
    if type(value.checked_dimensions) is not tuple:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions must be an exact tuple")
    try:
        typed_dimensions = tuple(GateCheckedDimension(item) for item in value.checked_dimensions)
    except (TypeError, ValueError) as exc:
        raise _fail(
            AdmissionFailureCode.DIMENSION_NOT_CHECKED,
            "checked_dimensions contains a value outside the closed vocabulary",
        ) from exc
    _dimensions(value.gate_kind, typed_dimensions)
    _identifier(value.policy_version, "policy_version")
    _diagnostics(value.diagnostics)
    _timestamp(value.decided_at_utc, "decided_at_utc")
    if type(value.sequence) is not int or isinstance(value.sequence, bool) or value.sequence < 1:
        raise _fail(AdmissionFailureCode.SEQUENCE_NOT_MONOTONIC, "gate decision sequence is invalid")
    if gate_requires_consumer_context(value.gate_kind):
        if type(value.consumer_context_ref) is not HashBoundRef:
            raise _fail(
                AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
                f"{value.gate_kind.value} requires an exact consumer context ref",
            )
    elif value.consumer_context_ref is not None:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_FORBIDDEN,
            f"{value.gate_kind.value} must not carry a consumer context ref",
        )
    if gate_requires_committed_boundary(value.gate_kind):
        if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
            raise _fail(
                AdmissionFailureCode.BOUNDARY_REQUIRED,
                "consumption requires an exact committed boundary ref",
            )
    elif value.boundary_ref is not None:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            f"{value.gate_kind.value} must not carry a boundary ref",
        )
    if value.predecessor_decision_digest is not None:
        digest = value.predecessor_decision_digest
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "predecessor digest is invalid")
    _require_no_contradiction(value.gate_kind, value.decision_kind, value.reason_codes)
    try:
        validate_record_id(value.gate_decision_id, canonical_bytes=_canonical(_decision_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "gate_decision_id does not match its payload",
        ) from exc


def gate_decision_from_dict(value: object, *, expected_ref: HashBoundRef) -> GateDecision:
    """Restore a decision and bind it to an independently held identity.

    §22 names low-level deserialization as a bypass path. Recomputing the
    identity from the payload is *not* sufficient defence: an attacker who edits
    a stored verdict simply recomputes its hash, and a self-consistent forgery
    would restore cleanly. The identity must therefore come from somewhere the
    forger does not control.

    ``expected_ref`` is that anchor — the hash-bound reference a committed
    snapshot boundary or lineage record already holds for this decision. The
    payload must hash to it exactly. A verdict whose reasons or decision kind
    were edited no longer matches the reference and is refused, and a verdict
    restored under another decision's reference is refused as well.
    """

    if type(expected_ref) is not HashBoundRef or expected_ref.kind is not RefKind.GATE_DECISION:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "restoration requires an exact GATE_DECISION reference",
        )
    if type(value) is not dict:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision payload must be an exact dict")
    required = (
        "schema_version", "gate_kind", "subject_refs", "consumer_context_ref", "boundary_ref",
        "decision_kind", "reason_codes", "policy_version", "checked_dimensions", "diagnostics",
        "authority_identity", "authority_role", "decided_at_utc",
        "predecessor_decision_digest", "sequence",
    )
    if set(value) != set(required) or any(type(key) is not str for key in value):
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "gate decision payload field set is incomplete or unknown",
        )
    if value["schema_version"] != GATE_DECISION_V1.value:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "gate decision schema is unknown")
    try:
        gate = GateKind(value["gate_kind"])
        decision_kind = GateDecisionKind(value["decision_kind"])
        role = AuthorityRole(value["authority_role"])
    except (TypeError, ValueError) as exc:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision enum value is unknown") from exc
    raw_time = value["decided_at_utc"]
    if type(raw_time) is not str:
        raise _fail(AdmissionFailureCode.MALFORMED_TIMESTAMP, "decided_at_utc is invalid")
    try:
        decided = datetime.strptime(raw_time, UTC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise _fail(AdmissionFailureCode.MALFORMED_TIMESTAMP, "decided_at_utc is unparseable") from exc
    raw_subjects = value["subject_refs"]
    if type(raw_subjects) is not list:
        raise _fail(AdmissionFailureCode.SUBJECT_MISMATCH, "subject_refs must be an exact list")
    raw_reasons = value["reason_codes"]
    if type(raw_reasons) is not list:
        raise _fail(AdmissionFailureCode.REASON_CODES_REQUIRED, "reason_codes must be an exact list")
    raw_dimensions = value["checked_dimensions"]
    if type(raw_dimensions) is not list:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "checked_dimensions must be an exact list")
    result = object.__new__(GateDecision)
    object.__setattr__(result, "schema_version", GATE_DECISION_V1)
    object.__setattr__(result, "gate_kind", gate)
    object.__setattr__(result, "subject_refs", tuple(HashBoundRef.from_dict(item) for item in raw_subjects))
    object.__setattr__(
        result, "consumer_context_ref",
        None if value["consumer_context_ref"] is None else HashBoundRef.from_dict(value["consumer_context_ref"]),
    )
    object.__setattr__(
        result, "boundary_ref",
        None if value["boundary_ref"] is None else HashBoundRef.from_dict(value["boundary_ref"]),
    )
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "reason_codes", tuple(raw_reasons))
    object.__setattr__(result, "policy_version", value["policy_version"])
    object.__setattr__(result, "checked_dimensions", tuple(raw_dimensions))
    object.__setattr__(result, "diagnostics", _diagnostics(value["diagnostics"]))
    object.__setattr__(result, "authority_identity", AuthorityIdentity.from_dict(value["authority_identity"]))
    object.__setattr__(result, "authority_role", role)
    object.__setattr__(result, "decided_at_utc", decided)
    object.__setattr__(result, "predecessor_decision_digest", value["predecessor_decision_digest"])
    object.__setattr__(result, "sequence", value["sequence"])
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    object.__setattr__(
        result,
        "gate_decision_id",
        compute_record_id(
            domain=IdentityDomain.GATE_DECISION,
            canonical_bytes=_canonical(_decision_payload(result)),
        ),
    )
    validate_gate_decision(result)
    payload = result.canonical_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_ref.sha256 or len(payload) != expected_ref.byte_length:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "restored decision does not match its expected reference",
        )
    if expected_ref.ref_id != result.gate_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "expected reference names another decision",
        )
    return result


def gate_decision_ref(value: GateDecision) -> HashBoundRef:
    """Return the hash-bound reference a snapshot or lineage record stores."""

    validate_gate_decision(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.GATE_DECISION,
        ref_id=value.gate_decision_id.digest_sha256,
        schema_id=GATE_DECISION_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# ConfiguredGateController
# ---------------------------------------------------------------------------


class ConfiguredGateController:
    """Write-once capability object holding one gate authority and its ports.

    The gate authority is independent by construction: it may not coincide with
    the producer, the retriever, the worker or the consumer whose request it
    decides. Every external fact arrives through an injected probe, so this
    owner consults stores without importing them.
    """

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_configuration_frozen", False):
            raise AttributeError("configured gate controller is write-once")
        object.__setattr__(self, name, value)

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _CONTROLLER_SEAL or kwargs or len(args) != 13:
            raise TypeError("ConfiguredGateController is factory-created")
        (
            self._authority_handle,
            self._authority_identity,
            self._authority_roles,
            self._policy_version,
            self._trusted_clock,
            self._taint_probe,
            self._provenance_probe,
            self._lifecycle_probe,
            self._compatibility_probe,
            self._boundary_probe,
            self._grant_probe,
            self._head_reader,
            self._participants,
        ) = args
        self._configuration_frozen = True

    @property
    def authority_identity(self) -> AuthorityIdentity:
        return self._authority_identity

    @property
    def authority_roles(self) -> Mapping[GateKind, AuthorityRole]:
        return dict(self._authority_roles)

    def role_for(self, gate: GateKind) -> AuthorityRole:
        """The role this authority signs the given gate with."""

        return require_role_for_gate(self._authority_roles[gate], gate=gate)

    @property
    def policy_version(self) -> str:
        return self._policy_version


def configure_gate_controller(
    *,
    authority_handle: Stage4AuthorityHandle,
    authority_identity: AuthorityIdentity,
    authority_roles: Mapping[GateKind, AuthorityRole],
    policy_version: str,
    trusted_clock: Callable[[], datetime],
    taint_probe: Callable[[HashBoundRef], "TaintFinding"],
    provenance_probe: Callable[[HashBoundRef], bool],
    lifecycle_probe: Callable[[HashBoundRef], bool],
    compatibility_probe: Callable[[HashBoundRef], "CompatibilityFinding"],
    boundary_probe: Callable[[HashBoundRef], bool],
    grant_probe: Callable[[], "GrantEnvelope"],
    head_reader: Callable[[], Mapping[str, str]],
    producer_actor: ActorIdentity,
    retriever_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
) -> ConfiguredGateController:
    require_stage4_authority_handle(authority_handle)
    if type(authority_identity) is not AuthorityIdentity:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate authority identity is invalid")
    if not isinstance(authority_roles, Mapping) or set(authority_roles) != set(GateKind):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "one authority role must be declared for each of the four gates",
        )
    roles = {gate: require_role_for_gate(authority_roles[gate], gate=gate) for gate in GateKind}
    _identifier(policy_version, "policy_version")
    for probe in (
        trusted_clock, taint_probe, provenance_probe, lifecycle_probe,
        compatibility_probe, boundary_probe, grant_probe, head_reader,
    ):
        if not callable(probe):
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate probes must be callable")
    participants: list[str] = []
    for actor, name in (
        (producer_actor, "producer_actor"),
        (retriever_actor, "retriever_actor"),
        (consumer_actor, "consumer_actor"),
    ):
        if type(actor) is not ActorIdentity:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"{name} must be an exact ActorIdentity")
        participants.append(actor.value)
    if len(set(participants)) != len(participants):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "producer, retriever and consumer must be distinct actors",
        )
    if authority_identity.value in set(participants):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "gate authority cannot be a participating actor",
        )
    return ConfiguredGateController(
        authority_handle,
        authority_identity,
        roles,
        policy_version,
        trusted_clock,
        taint_probe,
        provenance_probe,
        lifecycle_probe,
        compatibility_probe,
        boundary_probe,
        grant_probe,
        head_reader,
        tuple(sorted(participants)),
        _seal=_CONTROLLER_SEAL,
    )


def require_configured_gate_controller(value: ConfiguredGateController) -> ConfiguredGateController:
    if type(value) is not ConfiguredGateController or not getattr(value, "_configuration_frozen", False):
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate controller is not factory configured")
    return value


# ---------------------------------------------------------------------------
# Typed probe results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaintFinding:
    """What a taint port reports about one subject.

    ``chain_complete`` is separate from ``consumable`` on purpose: a profile that
    looks permissive but cannot present its full source/derivation/authority
    chain is refused rather than believed.
    """

    consumable: bool
    chain_complete: bool
    quarantined: bool
    blocks_publication: bool

    def __post_init__(self) -> None:
        for field_name in ("consumable", "chain_complete", "quarantined", "blocks_publication"):
            if type(getattr(self, field_name)) is not bool:
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"taint finding {field_name} must be exact bool")


@dataclass(frozen=True)
class CompatibilityFinding:
    """What a compatibility port reports about one subject in this context."""

    compatible: bool
    evidence_complete: bool
    drifted: bool
    conflicts_unresolved: bool

    def __post_init__(self) -> None:
        for field_name in ("compatible", "evidence_complete", "drifted", "conflicts_unresolved"):
            if type(getattr(self, field_name)) is not bool:
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"compatibility finding {field_name} must be exact bool")


@dataclass(frozen=True)
class GrantEnvelope:
    """The exact scope, capabilities and oracle a request has been granted."""

    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    oracles: tuple[str, ...]
    policy_version: str

    def __post_init__(self) -> None:
        for field_name in ("scopes", "capabilities", "oracles"):
            value = getattr(self, field_name)
            if type(value) is not tuple or any(type(item) is not str or not item for item in value):
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"grant {field_name} must be an exact string tuple")
            if list(value) != sorted(set(value)):
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"grant {field_name} must be sorted and unique")
        _identifier(self.policy_version, "grant.policy_version")


@dataclass(frozen=True)
class RequestedEnvelope:
    """What a subject asks to use, compared against its grant."""

    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    oracles: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in ("scopes", "capabilities", "oracles"):
            value = getattr(self, field_name)
            if type(value) is not tuple or any(type(item) is not str or not item for item in value):
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"request {field_name} must be an exact string tuple")
            if list(value) != sorted(set(value)):
                raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"request {field_name} must be sorted and unique")


def detect_expansion(requested: RequestedEnvelope, *, granted: GrantEnvelope) -> tuple[str, ...]:
    """Return the expansion kinds a request attempts beyond its grant."""

    if type(requested) is not RequestedEnvelope or type(granted) is not GrantEnvelope:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "expansion check requires exact envelopes")
    found: list[str] = []
    if not set(requested.scopes) <= set(granted.scopes):
        found.append("SCOPE")
    if not set(requested.capabilities) <= set(granted.capabilities):
        found.append("CAPABILITIES")
    if not set(requested.oracles) <= set(granted.oracles):
        found.append("ORACLE")
    return tuple(found)


# ---------------------------------------------------------------------------
# Probe invocation — an error is never an admission
# ---------------------------------------------------------------------------


class GateDependencyUnavailable(Exception):
    """The declared way for a port to report that it could not answer.

    An adapter that cannot reach its store raises this, and the gate turns it
    into a blocking dependency reason. Everything else is *not* unavailability:
    a ``TypeError`` from a broken adapter, an identity substitution, a malformed
    contract or a programming defect all describe something other than a store
    being down, and folding them into ``DEPENDENCY_UNAVAILABLE`` would erase
    exactly the distinction an incident analysis needs.

    So the responsibility sits with the adapter: convert expected infrastructure
    failures into this type, and let anything unexpected propagate. A gate that
    swallowed everything would still be fail-closed — the outcome stays
    restrictive — but it would be fail-closed and blind.
    """

    def __init__(self, detail: str = "gate dependency unavailable") -> None:
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.detail = detail
        super().__init__(detail)


class _ProbeUnavailable(Exception):
    """Internal marker: a declared dependency could not be reached."""


def _probe(call: Callable[[], object], expected: type) -> object:
    """Invoke a port and demand an exact result.

    Three outcomes, and they are deliberately not merged. A declared
    ``GateDependencyUnavailable`` is an outage and becomes a blocking dependency
    reason. An ``AdmissionViolation`` keeps its own code. Anything else — an
    unexpected exception, or an answer of the wrong exact type — is the port
    failing its own contract, and is reported as that.

    A malformed return used to be filed as unavailability too. It is not: a
    probe that answers ``None`` where a ``TaintFinding`` was required is a
    broken adapter, and calling it an outage sends an incident analysis looking
    for a store that was never down.
    """

    try:
        result = call()
    except AdmissionViolation:
        raise
    except GateDependencyUnavailable as exc:
        raise _ProbeUnavailable() from exc
    if type(result) is not expected:
        raise _fail(
            AdmissionFailureCode.PROBE_CONTRACT_VIOLATION,
            f"a gate probe returned {type(result).__name__} where {expected.__name__} was required",
        )
    return result


def _make_decision(
    controller: ConfiguredGateController,
    *,
    gate: GateKind,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef | None,
    boundary_ref: HashBoundRef | None,
    reasons: tuple[str, ...],
    dimensions: frozenset[GateCheckedDimension],
    diagnostics: Mapping[str, str],
    sequence: int,
    predecessor: GateDecision | None,
) -> GateDecision:
    ordered_reasons = tuple(sorted(set(reasons)))
    decision_kind = resolve_decision_kind(gate, ordered_reasons)
    ordered_dimensions = tuple(sorted(item.value for item in dimensions))
    result = object.__new__(GateDecision)
    object.__setattr__(result, "schema_version", GATE_DECISION_V1)
    object.__setattr__(result, "gate_kind", gate)
    object.__setattr__(result, "subject_refs", _subjects(subject_refs))
    object.__setattr__(result, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(result, "boundary_ref", boundary_ref)
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "reason_codes", ordered_reasons)
    object.__setattr__(result, "policy_version", controller.policy_version)
    object.__setattr__(result, "checked_dimensions", ordered_dimensions)
    object.__setattr__(result, "diagnostics", _diagnostics(dict(diagnostics)))
    object.__setattr__(result, "authority_identity", controller.authority_identity)
    object.__setattr__(result, "authority_role", controller.role_for(gate))
    object.__setattr__(
        result, "decided_at_utc", _timestamp(controller._trusted_clock(), "decided_at_utc")
    )
    object.__setattr__(
        result,
        "predecessor_decision_digest",
        None if predecessor is None else predecessor.gate_decision_id.digest_sha256,
    )
    object.__setattr__(result, "sequence", sequence)
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    object.__setattr__(
        result,
        "gate_decision_id",
        compute_record_id(
            domain=IdentityDomain.GATE_DECISION,
            canonical_bytes=_canonical(_decision_payload(result)),
        ),
    )
    validate_gate_decision(result)
    return result


# ---------------------------------------------------------------------------
# The four gates
# ---------------------------------------------------------------------------


def evaluate_ingestion_gate(
    controller: ConfiguredGateController,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    sequence: int = 1,
) -> GateDecision:
    """Decide whether a candidate may be extracted from its source at all."""

    require_configured_gate_controller(controller)
    reasons: list[str] = []
    diagnostics: dict[str, str] = {}
    unavailable = False
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
        except _ProbeUnavailable:
            unavailable = True
            continue
        assert isinstance(taint, TaintFinding)
        if taint.quarantined:
            reasons.append(IngestionReason.SOURCE_QUARANTINED.value)
        if not taint.chain_complete:
            reasons.append(IngestionReason.SOURCE_UNCLASSIFIED.value)
        if not provenance:
            reasons.append(IngestionReason.PROVENANCE_INCOMPLETE.value)
    if unavailable:
        reasons.append(IngestionReason.DEPENDENCY_UNAVAILABLE.value)
        diagnostics["dependency"] = "a required ingestion probe produced no exact answer"
    if not reasons:
        reasons.append(IngestionReason.SOURCE_CLASSIFIED.value)
    return _make_decision(
        controller,
        gate=GateKind.INGESTION,
        subject_refs=subject_refs,
        consumer_context_ref=None,
        boundary_ref=None,
        reasons=tuple(reasons),
        dimensions=_REQUIRED_DIMENSIONS[GateKind.INGESTION],
        diagnostics=diagnostics,
        sequence=sequence,
        predecessor=None,
    )


def evaluate_publication_gate(
    controller: ConfiguredGateController,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    requested: RequestedEnvelope,
    predecessor: GateDecision,
    sequence: int = 2,
) -> GateDecision:
    """Decide whether a verified object may be written into the library."""

    require_configured_gate_controller(controller)
    require_gate_predecessor(predecessor, expected_gate=GateKind.INGESTION, subject_refs=subject_refs)
    reasons: list[str] = []
    diagnostics: dict[str, str] = {}
    unavailable = False
    if not predecessor.admitted:
        reasons.append(PublicationReason.LIFECYCLE_NOT_ATTESTED.value)
        diagnostics["predecessor"] = "ingestion did not admit this subject set"
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            lifecycle = _probe(lambda ref=ref: controller._lifecycle_probe(ref), bool)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
        except _ProbeUnavailable:
            unavailable = True
            continue
        assert isinstance(taint, TaintFinding)
        if taint.quarantined:
            reasons.append(PublicationReason.SUBJECT_QUARANTINED.value)
        if taint.blocks_publication or not taint.chain_complete:
            reasons.append(PublicationReason.TAINT_BLOCKS_PUBLICATION.value)
        if not lifecycle:
            reasons.append(PublicationReason.LIFECYCLE_NOT_ATTESTED.value)
        if not provenance:
            reasons.append(PublicationReason.ATTESTATION_MISSING.value)
    try:
        granted = _probe(controller._grant_probe, GrantEnvelope)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
    if granted is not None:
        assert isinstance(granted, GrantEnvelope)
        if detect_expansion(requested, granted=granted):
            reasons.append(PublicationReason.SCOPE_EXPANSION_REQUESTED.value)
    if unavailable:
        reasons.append(PublicationReason.DEPENDENCY_UNAVAILABLE.value)
        diagnostics["dependency"] = "a required publication probe produced no exact answer"
    if not reasons:
        reasons.append(PublicationReason.EVIDENCE_VALIDATED.value)
    return _make_decision(
        controller,
        gate=GateKind.PUBLICATION,
        subject_refs=subject_refs,
        consumer_context_ref=None,
        boundary_ref=None,
        reasons=tuple(reasons),
        dimensions=_REQUIRED_DIMENSIONS[GateKind.PUBLICATION],
        diagnostics=diagnostics,
        sequence=sequence,
        predecessor=predecessor,
    )


def evaluate_retrieval_gate(
    controller: ConfiguredGateController,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    requested: RequestedEnvelope,
    predecessor: GateDecision,
    sequence: int = 3,
) -> GateDecision:
    """Decide whether an object is applicable to this consumer context.

    Publication admission does not carry into retrieval: an object that was
    lawfully published can still be inapplicable, stale, revoked or in conflict
    here, so every dimension is checked again against this context.
    """

    require_configured_gate_controller(controller)
    require_gate_predecessor(predecessor, expected_gate=GateKind.PUBLICATION, subject_refs=subject_refs)
    if type(consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "retrieval requires an exact consumer context ref",
        )
    reasons: list[str] = []
    diagnostics: dict[str, str] = {}
    unavailable = False
    if not predecessor.admitted:
        reasons.append(RetrievalReason.COMPATIBILITY_REJECTED.value)
        diagnostics["predecessor"] = "publication did not admit this subject set"
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            lifecycle = _probe(lambda ref=ref: controller._lifecycle_probe(ref), bool)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
            compatibility = _probe(
                lambda ref=ref: controller._compatibility_probe(ref), CompatibilityFinding
            )
        except _ProbeUnavailable:
            unavailable = True
            continue
        assert isinstance(taint, TaintFinding)
        assert isinstance(compatibility, CompatibilityFinding)
        if taint.quarantined:
            reasons.append(RetrievalReason.SUBJECT_QUARANTINED.value)
        if not taint.consumable or not taint.chain_complete:
            reasons.append(RetrievalReason.TAINT_BLOCKS_RETRIEVAL.value)
        if not lifecycle:
            reasons.append(RetrievalReason.LIFECYCLE_NOT_CONSUMABLE.value)
        if not provenance:
            reasons.append(RetrievalReason.COMPATIBILITY_INCOMPLETE.value)
        if not compatibility.evidence_complete:
            reasons.append(RetrievalReason.COMPATIBILITY_INCOMPLETE.value)
        elif not compatibility.compatible:
            reasons.append(RetrievalReason.COMPATIBILITY_REJECTED.value)
        if compatibility.conflicts_unresolved:
            reasons.append(RetrievalReason.CONFLICT_UNRESOLVED.value)
    try:
        granted = _probe(controller._grant_probe, GrantEnvelope)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
    if granted is not None:
        assert isinstance(granted, GrantEnvelope)
        if detect_expansion(requested, granted=granted):
            reasons.append(RetrievalReason.SCOPE_MISMATCH.value)
    if unavailable:
        reasons.append(RetrievalReason.DEPENDENCY_UNAVAILABLE.value)
        diagnostics["dependency"] = "a required retrieval probe produced no exact answer"
    if not reasons:
        reasons.append(RetrievalReason.CONTEXT_COMPATIBLE.value)
    return _make_decision(
        controller,
        gate=GateKind.RETRIEVAL,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=None,
        reasons=tuple(reasons),
        dimensions=_REQUIRED_DIMENSIONS[GateKind.RETRIEVAL],
        diagnostics=diagnostics,
        sequence=sequence,
        predecessor=predecessor,
    )


def evaluate_consumption_gate(
    controller: ConfiguredGateController,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    requested: RequestedEnvelope,
    predecessor: GateDecision,
    sequence: int = 4,
) -> GateDecision:
    """The last barrier before replay or worker delivery.

    Nothing here is taken from an earlier verdict. Lifecycle, compatibility,
    taint, scope, policy and the committed snapshot boundary are all re-derived
    against the state that will actually be used, because an object can be
    revoked, drift out of compatibility or lose its taint chain between
    selection and use.
    """

    require_configured_gate_controller(controller)
    require_gate_predecessor(predecessor, expected_gate=GateKind.RETRIEVAL, subject_refs=subject_refs)
    if type(consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "consumption requires an exact consumer context ref",
        )
    if type(boundary_ref) is not HashBoundRef or boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            "consumption requires an exact committed boundary ref",
        )
    if predecessor.consumer_context_ref is None or _subject_key(
        predecessor.consumer_context_ref
    ) != _subject_key(consumer_context_ref):
        raise _fail(
            AdmissionFailureCode.STALE_DECISION,
            "retrieval decision was made against a different consumer context",
        )
    reasons: list[str] = []
    diagnostics: dict[str, str] = {}
    unavailable = False
    if not predecessor.admitted:
        reasons.append(ConsumptionReason.COMPATIBILITY_DRIFT.value)
        diagnostics["predecessor"] = "retrieval did not admit this subject set"
    try:
        committed = _probe(lambda: controller._boundary_probe(boundary_ref), bool)
    except _ProbeUnavailable:
        unavailable = True
        committed = None
    if committed is False:
        reasons.append(ConsumptionReason.SNAPSHOT_BOUNDARY_INVALID.value)
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            lifecycle = _probe(lambda ref=ref: controller._lifecycle_probe(ref), bool)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
            compatibility = _probe(
                lambda ref=ref: controller._compatibility_probe(ref), CompatibilityFinding
            )
        except _ProbeUnavailable:
            unavailable = True
            continue
        assert isinstance(taint, TaintFinding)
        assert isinstance(compatibility, CompatibilityFinding)
        if taint.quarantined:
            reasons.append(ConsumptionReason.SUBJECT_QUARANTINED.value)
        if not taint.chain_complete:
            reasons.append(ConsumptionReason.TAINT_CHAIN_INCOMPLETE.value)
        elif not taint.consumable:
            reasons.append(ConsumptionReason.TAINT_BLOCKS_CONSUMPTION.value)
        if not lifecycle:
            reasons.append(ConsumptionReason.LIFECYCLE_CHANGED.value)
        if not provenance:
            reasons.append(ConsumptionReason.TAINT_CHAIN_INCOMPLETE.value)
        if compatibility.drifted or not compatibility.compatible or not compatibility.evidence_complete:
            reasons.append(ConsumptionReason.COMPATIBILITY_DRIFT.value)
        if compatibility.conflicts_unresolved:
            reasons.append(ConsumptionReason.COMPATIBILITY_DRIFT.value)
    try:
        granted = _probe(controller._grant_probe, GrantEnvelope)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
    if granted is not None:
        assert isinstance(granted, GrantEnvelope)
        if granted.policy_version != controller.policy_version:
            reasons.append(ConsumptionReason.POLICY_VERSION_CHANGED.value)
        for kind in detect_expansion(requested, granted=granted):
            reasons.append(
                {
                    "SCOPE": ConsumptionReason.SCOPE_EXPANSION.value,
                    "CAPABILITIES": ConsumptionReason.CAPABILITY_EXPANSION.value,
                    "ORACLE": ConsumptionReason.ORACLE_EXPANSION.value,
                }[kind]
            )
    if unavailable:
        reasons.append(ConsumptionReason.DEPENDENCY_UNAVAILABLE.value)
        diagnostics["dependency"] = "a required consumption probe produced no exact answer"
    if not reasons:
        reasons.append(ConsumptionReason.REVALIDATION_PASSED.value)
    return _make_decision(
        controller,
        gate=GateKind.CONSUMPTION,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        reasons=tuple(reasons),
        dimensions=_REQUIRED_DIMENSIONS[GateKind.CONSUMPTION],
        diagnostics=diagnostics,
        sequence=sequence,
        predecessor=predecessor,
    )


# ---------------------------------------------------------------------------
# Chain and the final barrier
# ---------------------------------------------------------------------------


def require_gate_predecessor(
    value: GateDecision,
    *,
    expected_gate: GateKind,
    subject_refs: tuple[HashBoundRef, ...],
) -> GateDecision:
    """Fail closed unless ``value`` is this gate's immediate predecessor.

    A decision belongs to exactly one gate and one subject set. Presenting an
    earlier gate's verdict at a later gate, or a verdict about other subjects,
    is refused rather than reused.
    """

    validate_gate_decision(value)
    if value.gate_kind is not expected_gate:
        raise _fail(
            AdmissionFailureCode.GATE_DECISION_REUSED,
            f"expected a {expected_gate.value} decision, received {value.gate_kind.value}",
        )
    if value.subject_keys() != tuple(_subject_key(item) for item in _subjects(subject_refs)):
        raise _fail(
            AdmissionFailureCode.SUBJECT_MISMATCH,
            "predecessor decision describes a different subject set",
        )
    return value


@dataclass(frozen=True, init=False)
class GateDecisionChain:
    """The ordered four-gate record for one subject set."""

    ingestion: GateDecision
    publication: GateDecision
    retrieval: GateDecision
    consumption: GateDecision
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> GateDecisionChain:
        raise TypeError("GateDecisionChain is produced only by build_gate_decision_chain")

    def decisions(self) -> tuple[GateDecision, ...]:
        return (self.ingestion, self.publication, self.retrieval, self.consumption)

    @property
    def admitted(self) -> bool:
        return all(item.admitted for item in self.decisions())

    def blocking_reasons(self) -> tuple[str, ...]:
        blocked: list[str] = []
        for decision in self.decisions():
            if decision.admitted:
                continue
            admitting = _ADMITTING_REASONS[decision.gate_kind]
            blocked.extend(item for item in decision.reason_codes if item not in admitting)
        return tuple(sorted(set(blocked)))


def build_gate_decision_chain(
    *,
    ingestion: GateDecision,
    publication: GateDecision,
    retrieval: GateDecision,
    consumption: GateDecision,
) -> GateDecisionChain:
    """Bind four decisions into one validated, strictly ordered chain."""

    ordered = (ingestion, publication, retrieval, consumption)
    prior: GateDecision | None = None
    subjects: tuple[str, ...] | None = None
    for decision in ordered:
        validate_gate_decision(decision)
        validate_gate_progression(
            decision.gate_kind, prior=None if prior is None else prior.gate_kind
        )
        if subjects is None:
            subjects = decision.subject_keys()
        elif decision.subject_keys() != subjects:
            raise _fail(
                AdmissionFailureCode.SUBJECT_MISMATCH,
                "chain decisions describe different subject sets",
            )
        if prior is not None:
            if decision.predecessor_decision_digest != prior.gate_decision_id.digest_sha256:
                raise _fail(
                    AdmissionFailureCode.GATE_DECISION_REUSED,
                    f"{decision.gate_kind.value} does not follow the recorded predecessor",
                )
            if decision.sequence <= prior.sequence:
                raise _fail(
                    AdmissionFailureCode.SEQUENCE_NOT_MONOTONIC,
                    "chain sequence does not advance",
                )
            if decision.policy_version != prior.policy_version:
                raise _fail(
                    AdmissionFailureCode.POLICY_VERSION_MISMATCH,
                    "chain decisions were made under different policy versions",
                )
        prior = decision
    if ingestion.predecessor_decision_digest is not None:
        raise _fail(
            AdmissionFailureCode.GATE_SEQUENCE_VIOLATION,
            "the first gate cannot declare a predecessor",
        )
    result = object.__new__(GateDecisionChain)
    object.__setattr__(result, "ingestion", ingestion)
    object.__setattr__(result, "publication", publication)
    object.__setattr__(result, "retrieval", retrieval)
    object.__setattr__(result, "consumption", consumption)
    object.__setattr__(result, "_trusted_seal", _CHAIN_SEAL)
    return result


def require_consumption_admitted(
    value: GateDecision,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
) -> GateDecision:
    """The single barrier every path to replay or the worker must cross.

    It re-checks the decision's own identity, gate, subjects, context, boundary
    and policy against what is about to be used. A decision that was valid for a
    different context, boundary or policy version does not carry over.
    """

    validate_gate_decision(value)
    if value.gate_kind is not GateKind.CONSUMPTION:
        raise _fail(
            AdmissionFailureCode.GATE_KIND_MISMATCH,
            "only a consumption decision admits replay or worker delivery",
        )
    if value.subject_keys() != tuple(_subject_key(item) for item in _subjects(subject_refs)):
        raise _fail(AdmissionFailureCode.SUBJECT_MISMATCH, "decision describes a different subject set")
    if value.consumer_context_ref is None or _subject_key(value.consumer_context_ref) != _subject_key(
        consumer_context_ref
    ):
        raise _fail(AdmissionFailureCode.STALE_DECISION, "decision belongs to another consumer context")
    if value.boundary_ref is None or _subject_key(value.boundary_ref) != _subject_key(boundary_ref):
        raise _fail(AdmissionFailureCode.STALE_DECISION, "decision belongs to another snapshot boundary")
    if value.policy_version != _identifier(policy_version, "policy_version"):
        raise _fail(AdmissionFailureCode.POLICY_VERSION_MISMATCH, "decision was made under another policy version")
    if not value.admitted:
        raise _fail(
            AdmissionFailureCode.NOT_ADMITTED,
            f"consumption decision is {value.decision_kind.value}",
        )
    return value


def admitted_subject_refs(
    chain: GateDecisionChain,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
) -> tuple[HashBoundRef, ...]:
    """Return the refs a chain admits, or none at all.

    Admission is all-or-nothing for a subject set: a chain that blocks yields an
    empty tuple, so a rejected object cannot reach a prompt or a replay input by
    surviving in a partially admitted list.
    """

    if type(chain) is not GateDecisionChain or getattr(chain, "_trusted_seal", None) is not _CHAIN_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate chain is not builder sealed")
    if not chain.admitted:
        return ()
    require_consumption_admitted(
        chain.consumption,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy_version=policy_version,
    )
    return chain.consumption.subject_refs


# ---------------------------------------------------------------------------
# §22 durability — "Decisions immutable, persisted and linked in lineage"
# ---------------------------------------------------------------------------


@runtime_checkable
class DecisionJournalPort(Protocol):
    """The append-only journal a gate decision is committed to.

    Declared here and implemented elsewhere. This owner consults stores through
    injected ports and imports none of them, which is what keeps the §21 and
    §22 owners free of each other; durability is no different. The journal deals
    in opaque bytes and knows nothing about gates, so a generic append-only log
    satisfies it — ``persistence.append_journal_payload`` and its recovery scan
    are exactly such a log.
    """

    def append_record(self, payload: bytes) -> None:
        """Append one canonical decision payload durably, or raise."""

    def contains_record(self, digest: str) -> bool:
        """Whether a payload with this sha256 is in the committed prefix."""

    def current_anchor(self) -> str:
        """A digest over the committed prefix, changing on every append."""

    def extends(self, anchor: str) -> bool:
        """Whether ``anchor`` is a confirmed ancestor of the current anchor.

        Equality would be wrong. The journal legitimately grows: every later
        decision moves the anchor forward while every earlier decision stays
        durable. What must hold is that the anchor a receipt recorded is still
        a prefix of the committed history — that is what makes a rollback
        detectable while an ordinary append stays valid.
        """


def require_decision_journal(value: object) -> DecisionJournalPort:
    missing = [
        name
        for name in ("append_record", "contains_record", "current_anchor", "extends")
        if not callable(getattr(value, name, None))
    ]
    if missing:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            f"decision journal is missing {', '.join(missing)}",
        )
    return value  # type: ignore[return-value]


@dataclass(frozen=True, init=False)
class DecisionCommitReceipt:
    """Evidence that one gate decision reached durable storage.

    A receipt is not a claim a caller can make. It exists only after an append
    returned, and it carries the anchor the journal reported at that moment, so
    a decision that was evaluated but never committed cannot be presented as if
    it had been.
    """

    schema_version: SchemaVersion
    gate_decision_id: RecordId
    decision_digest: str
    journal_anchor: str
    committed_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> DecisionCommitReceipt:
        raise TypeError("DecisionCommitReceipt is produced only by commit_gate_decision")

    def to_dict(self) -> dict[str, object]:
        validate_commit_receipt(self)
        return {
            "schema_version": self.schema_version.value,
            "gate_decision_id": self.gate_decision_id.to_dict(),
            "decision_digest": self.decision_digest,
            "journal_anchor": self.journal_anchor,
            "committed_at_utc": self.committed_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        }


def _sha256_text(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"{field_name} must be a sha256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, f"{field_name} is not lowercase hex")
    return value


def validate_commit_receipt(value: DecisionCommitReceipt) -> None:
    if type(value) is not DecisionCommitReceipt or getattr(value, "_trusted_seal", None) is not _RECEIPT_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "commit receipt is not factory sealed")
    if value.schema_version is not SchemaVersion.DECISION_COMMIT_RECEIPT_V1:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "commit receipt schema is unknown")
    _sha256_text(value.decision_digest, "decision_digest")
    _sha256_text(value.journal_anchor, "journal_anchor")
    _timestamp(value.committed_at_utc, "committed_at_utc")


def commit_gate_decision(
    decision: GateDecision,
    *,
    journal: DecisionJournalPort,
    trusted_clock: Callable[[], datetime],
) -> DecisionCommitReceipt:
    """Append one decision to the durable journal and return its receipt.

    The decision is written in its canonical form, so the digest a later reader
    recomputes is the digest that was committed. An append that raises produces
    no receipt at all rather than a receipt describing a write that did not
    happen — the same fail-closed rule the gates themselves follow.
    """

    validate_gate_decision(decision)
    require_decision_journal(journal)
    if not callable(trusted_clock):
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    payload = decision.canonical_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    try:
        journal.append_record(payload)
        anchor = journal.current_anchor()
    except AdmissionViolation:
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "the decision journal refused or failed the append",
        ) from exc
    if not journal.contains_record(digest):
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the journal does not report the decision in its committed prefix",
        )
    receipt = object.__new__(DecisionCommitReceipt)
    object.__setattr__(receipt, "schema_version", SchemaVersion.DECISION_COMMIT_RECEIPT_V1)
    object.__setattr__(receipt, "gate_decision_id", decision.gate_decision_id)
    object.__setattr__(receipt, "decision_digest", digest)
    object.__setattr__(receipt, "journal_anchor", _sha256_text(anchor, "journal_anchor"))
    object.__setattr__(receipt, "committed_at_utc", _timestamp(trusted_clock(), "committed_at_utc"))
    object.__setattr__(receipt, "_trusted_seal", _RECEIPT_SEAL)
    validate_commit_receipt(receipt)
    return receipt


def require_committed_decision(
    receipt: DecisionCommitReceipt,
    *,
    decision: GateDecision,
    journal: DecisionJournalPort,
) -> None:
    """Refuse a receipt that does not describe this decision in this journal.

    Three independent checks, because each defeats a different substitution: the
    digest must be this decision's canonical bytes, the record id must be this
    decision's, and the journal must still report the payload as committed. A
    receipt from another decision, or one whose journal has since been rolled
    back, does not admit anything.
    """

    validate_commit_receipt(receipt)
    validate_gate_decision(decision)
    require_decision_journal(journal)
    if receipt.decision_digest != hashlib.sha256(decision.canonical_bytes()).hexdigest():
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the receipt describes another decision payload",
        )
    if receipt.gate_decision_id.digest_sha256 != decision.gate_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the receipt belongs to another gate decision",
        )
    if not journal.contains_record(receipt.decision_digest):
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the journal no longer contains the committed decision",
        )
    if not journal.extends(receipt.journal_anchor):
        raise _fail(
            AdmissionFailureCode.JOURNAL_ROLLED_BACK,
            "the committed history no longer extends the anchor this receipt saw",
        )


# ---------------------------------------------------------------------------
# §22 current authority heads — read at the point of use, in one observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthorityHeadObservation:
    """One authority domain as the trusted reader saw it.

    A bare digest is not enough. Two stores can produce the same-looking
    sha256 string, and a caller who may pass one can pass another domain's.
    Binding the domain and the store sequence into the record means a
    substituted observation is a different record rather than an equal one.
    """

    domain: str
    anchor_sha256: str
    sequence: int

    def __post_init__(self) -> None:
        if self.domain not in AUTHORITY_HEAD_DOMAINS:
            raise _fail(
                AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
                f"{self.domain} is not a declared authority domain",
            )
        _sha256_text(self.anchor_sha256, f"{self.domain} anchor")
        if type(self.sequence) is not int or isinstance(self.sequence, bool) or self.sequence < 0:
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                f"{self.domain} store sequence must be a natural number",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "anchor_sha256": self.anchor_sha256,
            "sequence": self.sequence,
        }


@dataclass(frozen=True, init=False)
class AuthorityHeadSet:
    """One coherent observation of every authority head a consumption sees.

    §22 requires current lifecycle, taint, admission, compatibility and boundary
    state at the point of use. *Coherent* is the load-bearing word: heads read
    at different moments describe different worlds, and a set that mixes a fresh
    admission anchor with a lifecycle anchor captured before the query can admit
    an object that has since been revoked.

    The whole set — including the current committed boundary — comes from one
    trusted reader call, and the caller supplies none of it. A caller who could
    hand over "the current boundary" could hand over a stale one, and the
    comparison that follows would then be a value checked against itself.

    **This is not yet a fenced capture.** One call is one *call*, not one
    instant: a reader may still read six stores at six moments internally.
    Making that impossible needs a lease or an epoch shared with the stores,
    which belongs to the coordination owner. Until it exists, this record
    carries the sequences it observed so a fenced reader can be dropped in
    without changing the contract.
    """

    schema_version: SchemaVersion
    head_set_id: RecordId
    boundary_ref: HashBoundRef
    observations: tuple[AuthorityHeadObservation, ...]
    observed_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AuthorityHeadSet:
        raise TypeError("AuthorityHeadSet is produced only by capture_authority_heads")

    def observation(self, domain: str) -> AuthorityHeadObservation:
        for item in self.observations:
            if item.domain == domain:
                return item
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
            f"{domain} was not observed",
        )

    def anchor(self, domain: str) -> str:
        return self.observation(domain).anchor_sha256

    def to_dict(self) -> dict[str, object]:
        validate_authority_head_set(self)
        return _head_set_payload(self) | {"head_set_id": self.head_set_id.to_dict()}


def _head_set_payload(value: AuthorityHeadSet) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "boundary_ref": value.boundary_ref.to_dict(),
        "observations": [item.to_dict() for item in value.observations],
        "observed_at_utc": value.observed_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_authority_head_set(value: AuthorityHeadSet) -> None:
    if type(value) is not AuthorityHeadSet or getattr(value, "_trusted_seal", None) is not _HEAD_SET_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "head set is not factory sealed")
    if value.schema_version is not SchemaVersion.AUTHORITY_HEAD_SET_V1:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "head set schema is unknown")
    if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(AdmissionFailureCode.BOUNDARY_REQUIRED, "a head set requires an exact committed boundary ref")
    if type(value.observations) is not tuple:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "observations must be an exact tuple")
    observed = []
    for item in value.observations:
        if type(item) is not AuthorityHeadObservation:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "observations must be exact records")
        item.__post_init__()
        observed.append(item.domain)
    if tuple(observed) != AUTHORITY_HEAD_DOMAINS:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
            "a head set must observe exactly the declared authority domains, in order",
        )
    _timestamp(value.observed_at_utc, "observed_at_utc")
    try:
        validate_record_id(value.head_set_id, canonical_bytes=_canonical(_head_set_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "head_set_id does not match its observation",
        ) from exc


def capture_authority_heads(controller: ConfiguredGateController) -> AuthorityHeadSet:
    """Read the current committed boundary and every authority head, once.

    The reader supplies the boundary too. An earlier revision took it from the
    caller, which made the later comparison meaningless: the caller handed in a
    boundary, the capture stored it unchanged, and the check compared that value
    against itself. Whether the boundary is still the committed one is a fact
    about the store, so only the store may state it.
    """

    require_configured_gate_controller(controller)
    try:
        observed = controller._head_reader()
    except AdmissionViolation:
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(
            AdmissionFailureCode.DEPENDENCY_UNAVAILABLE,
            "the authority head reader was unavailable",
        ) from exc
    if not isinstance(observed, Mapping) or set(observed) != {"boundary_ref", "heads"}:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
            "the head reader must return the current boundary and the head map",
        )
    boundary_ref = observed["boundary_ref"]
    if type(boundary_ref) is not HashBoundRef or boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            "the head reader did not report an exact committed boundary",
        )
    heads = observed["heads"]
    if not isinstance(heads, Mapping) or set(heads) != set(AUTHORITY_HEAD_DOMAINS):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
            "the head reader did not return exactly the declared authority domains",
        )
    observations = []
    for name in AUTHORITY_HEAD_DOMAINS:
        entry = heads[name]
        if not isinstance(entry, Mapping) or set(entry) != {"anchor_sha256", "sequence"}:
            raise _fail(
                AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
                f"the {name} head is not an exact anchor/sequence observation",
            )
        observations.append(
            AuthorityHeadObservation(
                domain=name,
                anchor_sha256=entry["anchor_sha256"],
                sequence=entry["sequence"],
            )
        )
    payload = object.__new__(AuthorityHeadSet)
    object.__setattr__(payload, "schema_version", SchemaVersion.AUTHORITY_HEAD_SET_V1)
    object.__setattr__(payload, "boundary_ref", boundary_ref)
    object.__setattr__(payload, "observations", tuple(observations))
    object.__setattr__(payload, "observed_at_utc", _timestamp(controller._trusted_clock(), "observed_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _HEAD_SET_SEAL)
    object.__setattr__(
        payload,
        "head_set_id",
        compute_record_id(
            domain=IdentityDomain.AUTHORITY_HEAD_SET,
            canonical_bytes=_canonical(_head_set_payload(payload)),
        ),
    )
    validate_authority_head_set(payload)
    return payload


def require_current_heads(
    value: AuthorityHeadSet,
    *,
    controller: ConfiguredGateController,
) -> AuthorityHeadSet:
    """Refuse a head set that no longer describes the current world.

    Re-reads everything and compares, including the committed boundary. A set
    captured before a revoke, a taint escalation, a new admission or a new
    boundary is exactly what §22 forbids using at the point of consumption, and
    the only way to know is to look again. The fresh observation is returned so
    a caller can record what it actually saw.
    """

    validate_authority_head_set(value)
    fresh = capture_authority_heads(controller)
    if _subject_key(fresh.boundary_ref) != _subject_key(value.boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the current committed boundary is not the one this observation saw",
        )
    # The pair, not the digest. A store can advance its sequence while its
    # materialized root stays byte-identical — a rebuild, a re-included head, or
    # an adapter returning a stale anchor beside a fresh sequence. Comparing
    # anchors alone would read all three as "nothing changed".
    drifted = sorted(
        name
        for name in AUTHORITY_HEAD_DOMAINS
        if fresh.observation(name).to_dict() != value.observation(name).to_dict()
    )
    if drifted:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            f"authority heads changed since the observation: {', '.join(drifted[:3])}",
        )
    return fresh


# ---------------------------------------------------------------------------
# AdmittedKnowledgeHandle — the only carrier of consumable knowledge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class AdmittedKnowledgeHandle:
    """The capability a consumer must hold to use admitted knowledge.

    This exists so that "no path to replay or a worker bypasses the consumption
    gate" is a property of the type system rather than of reviewer diligence.
    A replay accepts a handle; nothing else can be turned into one; and a handle
    can only be minted by ``admit_for_consumption``, which requires a durable
    ADMIT and a fresh coherent head observation.

    Legacy retrieval paths that predate §22 remain audit-only for exactly this
    reason: whatever they return, they cannot produce this object.
    """

    schema_version: SchemaVersion
    handle_id: RecordId
    subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    consumption_decision_id: RecordId
    commit_receipt: DecisionCommitReceipt
    head_set: AuthorityHeadSet
    admitted_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AdmittedKnowledgeHandle:
        raise TypeError("AdmittedKnowledgeHandle is produced only by admit_for_consumption")

    def to_dict(self) -> dict[str, object]:
        validate_admitted_handle(self)
        return _handle_payload(self) | {"handle_id": self.handle_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_admitted_handle(self)
        return _canonical(_handle_payload(self))


def _handle_payload(value: AdmittedKnowledgeHandle) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "subject_refs": [item.to_dict() for item in value.subject_refs],
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "policy_version": value.policy_version,
        "consumption_decision_id": value.consumption_decision_id.to_dict(),
        "commit_receipt": value.commit_receipt.to_dict(),
        "head_set": value.head_set.to_dict(),
        "admitted_at_utc": value.admitted_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_admitted_handle(value: AdmittedKnowledgeHandle) -> None:
    if type(value) is not AdmittedKnowledgeHandle or getattr(value, "_trusted_seal", None) is not _HANDLE_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "handle is not factory sealed")
    if value.schema_version is not SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "handle schema is unknown")
    _subjects(value.subject_refs)
    if type(value.consumer_context_ref) is not HashBoundRef:
        raise _fail(AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED, "a handle requires an exact consumer context")
    if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(AdmissionFailureCode.BOUNDARY_REQUIRED, "a handle requires an exact committed boundary ref")
    _identifier(value.policy_version, "policy_version")
    validate_commit_receipt(value.commit_receipt)
    validate_authority_head_set(value.head_set)
    _timestamp(value.admitted_at_utc, "admitted_at_utc")
    if _subject_key(value.head_set.boundary_ref) != _subject_key(value.boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the handle's head observation belongs to another boundary",
        )
    if value.commit_receipt.gate_decision_id.digest_sha256 != value.consumption_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the handle's receipt belongs to another consumption decision",
        )
    try:
        validate_record_id(value.handle_id, canonical_bytes=_canonical(_handle_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "handle_id does not match its payload",
        ) from exc


def admit_for_consumption(
    chain: GateDecisionChain,
    *,
    controller: ConfiguredGateController,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
    receipt: DecisionCommitReceipt,
    head_set: AuthorityHeadSet,
    journal: DecisionJournalPort,
) -> AdmittedKnowledgeHandle:
    """Mint the consumable capability, or refuse. The last step of §22.

    Four independent conditions, in this order, and every one of them is a
    barrier rather than a formality:

    1. the four-gate chain admits this exact subject set, context, boundary and
       policy — the check ``require_consumption_admitted`` already performs;
    2. the consumption decision is durably committed, proven by a receipt that
       must still be in the journal now, not merely when it was issued;
    3. the head observation is still current — re-read, not trusted;
    4. only then does a handle exist.

    A blocked chain yields no handle at all, not a handle over the surviving
    subjects: admission is all-or-nothing for a subject set, so a rejected
    object cannot reach a consumer by surviving in a partially admitted list.
    """

    if type(chain) is not GateDecisionChain or getattr(chain, "_trusted_seal", None) is not _CHAIN_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate chain is not builder sealed")
    require_configured_gate_controller(controller)
    if not chain.admitted:
        raise _fail(
            AdmissionFailureCode.NOT_ADMITTED,
            "a blocked gate chain admits nothing for consumption",
        )
    require_consumption_admitted(
        chain.consumption,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy_version=policy_version,
    )
    require_committed_decision(receipt, decision=chain.consumption, journal=journal)
    if _subject_key(head_set.boundary_ref) != _subject_key(boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the observation was taken against another committed boundary",
        )
    require_current_heads(head_set, controller=controller)

    payload = object.__new__(AdmittedKnowledgeHandle)
    object.__setattr__(payload, "schema_version", SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1)
    object.__setattr__(payload, "subject_refs", chain.consumption.subject_refs)
    object.__setattr__(payload, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(payload, "boundary_ref", boundary_ref)
    object.__setattr__(payload, "policy_version", policy_version)
    object.__setattr__(payload, "consumption_decision_id", chain.consumption.gate_decision_id)
    object.__setattr__(payload, "commit_receipt", receipt)
    object.__setattr__(payload, "head_set", head_set)
    object.__setattr__(payload, "admitted_at_utc", _timestamp(controller._trusted_clock(), "admitted_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _HANDLE_SEAL)
    object.__setattr__(
        payload,
        "handle_id",
        compute_record_id(
            domain=IdentityDomain.ADMITTED_KNOWLEDGE_HANDLE,
            canonical_bytes=_canonical(_handle_payload(payload)),
        ),
    )
    validate_admitted_handle(payload)
    return payload


def admitted_handle_ref(value: AdmittedKnowledgeHandle) -> HashBoundRef:
    """Return the hash-bound reference a replay request or lineage record stores."""

    validate_admitted_handle(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.handle_id.digest_sha256,
        schema_id=SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


__all__ = [
    "AUTHORITY_HEAD_DOMAINS",
    "GATE_DECISION_V1",
    "AdmissionFailureCode",
    "AdmissionViolation",
    "AdmittedKnowledgeHandle",
    "AuthorityHeadObservation",
    "AuthorityHeadSet",
    "CompatibilityFinding",
    "ConfiguredGateController",
    "ConsumptionReason",
    "DecisionCommitReceipt",
    "DecisionJournalPort",
    "GateDecision",
    "GateDecisionChain",
    "GateDependencyUnavailable",
    "GrantEnvelope",
    "IngestionReason",
    "PublicationReason",
    "RequestedEnvelope",
    "RetrievalReason",
    "TaintFinding",
    "admit_for_consumption",
    "admitted_handle_ref",
    "allowed_authority_roles",
    "admitted_subject_refs",
    "build_gate_decision_chain",
    "capture_authority_heads",
    "commit_gate_decision",
    "configure_gate_controller",
    "detect_expansion",
    "evaluate_consumption_gate",
    "evaluate_ingestion_gate",
    "evaluate_publication_gate",
    "evaluate_retrieval_gate",
    "gate_decision_from_dict",
    "gate_decision_ref",
    "gate_reason_vocabulary",
    "require_committed_decision",
    "require_configured_gate_controller",
    "require_consumption_admitted",
    "require_current_heads",
    "require_decision_journal",
    "require_role_for_gate",
    "require_gate_predecessor",
    "required_dimensions",
    "resolve_decision_kind",
    "validate_admitted_handle",
    "validate_authority_head_set",
    "validate_commit_receipt",
    "validate_gate_decision",
]
