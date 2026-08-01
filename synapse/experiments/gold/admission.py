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

*An admitted verdict is durable, and it is a prerequisite at the only door.*
§22 requires decisions to be immutable, persisted and linked in lineage, so a
consumption ADMIT participates in use only once it is in the append-only journal
and still there when asked again. What it produces is an
``AdmittedKnowledgeHandle``: durable audit evidence for the gate chain, receipt
and fenced observation at mint. The handle is not transferable present-time
authority; replay or worker delivery must pass through ``point_of_use`` for a
fresh Stage 3 and Consumption evaluation immediately before use.

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
from .authority_config import (
    GateEvaluatorDeclaration,
    GateIndependenceProof,
    create_gate_independence_proof,
    declaration_digest,
    require_independent_evaluator,
    validate_gate_evaluator_declaration,
    validate_gate_independence_proof,
)
from .contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    CommonEnvelope,
    ContractViolation,
    GateCheckedDimension,
    GateDecisionKind,
    GateKind,
    IdentityDomain,
    RecordId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
    Stage4AuthorityHandle,
    compute_record_id,
    compute_envelope_binding_sha256,
    common_envelope_from_dict,
    create_common_envelope,
    envelope_bound_record_bytes,
    gate_requires_committed_boundary,
    gate_requires_consumer_context,
    gate_stage_index,
    require_stage4_authority_handle,
    validate_gate_progression,
    validate_record_id,
    validate_envelope_bound_record,
)

GATE_DECISION_V1 = SchemaVersion.GATE_DECISION_V1
GATE_DECISION_V2 = SchemaVersion.GATE_DECISION_V2

_DECISION_SEAL = object()
_CONTROLLER_SEAL = object()
_CHAIN_SEAL = object()
_RECEIPT_SEAL = object()
_HEAD_SET_SEAL = object()
_HANDLE_SEAL = object()
_FENCED_AUTHORITY_STATE_SEAL = object()

#: Which authority role may decide which gate. §22 requires four independent
#: decisions with their own inputs and reason vocabularies; it does not require
#: four different authorities, and it does not permit one role to stand in for
#: another. An earlier revision of this module enforced the opposite of both —
#: it demanded that every decision in a chain carry the *same* identity, which
#: forbids the legitimate configuration of separate reviewers, while accepting
#: any role at any gate.
_ALLOWED_ROLES: dict[GateKind, frozenset[AuthorityRole]] = {
    GateKind.INGESTION: frozenset({AuthorityRole.INGESTION_GATE_EVALUATOR}),
    GateKind.PUBLICATION: frozenset({AuthorityRole.PUBLICATION_GATE_EVALUATOR}),
    GateKind.RETRIEVAL: frozenset({AuthorityRole.RETRIEVAL_GATE_EVALUATOR}),
    GateKind.CONSUMPTION: frozenset({AuthorityRole.CONSUMPTION_GATE_EVALUATOR}),
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
    "admission_decision",
    "retrieval_causal",
    "compatibility",
    "boundary",
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
    COORDINATOR_MISMATCH = "COORDINATOR_MISMATCH"
    HEAD_OBSERVATION_INCOMPLETE = "HEAD_OBSERVATION_INCOMPLETE"
    HEAD_OBSERVATION_STALE = "HEAD_OBSERVATION_STALE"
    AUTHORITY_ROLE_NOT_PERMITTED = "AUTHORITY_ROLE_NOT_PERMITTED"
    JOURNAL_ROLLED_BACK = "JOURNAL_ROLLED_BACK"
    PROBE_CONTRACT_VIOLATION = "PROBE_CONTRACT_VIOLATION"
    CHAIN_NOT_DURABLE = "CHAIN_NOT_DURABLE"


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
# OD-P78-F01 is the accepted owner decision for OD-09. The four closed reason
# vocabularies below and QUARANTINE > REJECT > REQUIRE_REVIEW > ADMIT precedence
# are ratified without amendment; they are not an open proposal.
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
    GateDecisionKind.QUARANTINE: 3,
    GateDecisionKind.REJECT: 2,
    GateDecisionKind.REQUIRE_REVIEW: 1,
    GateDecisionKind.ADMIT: 0,
}

# OD-P78-F02: REQUIRE_REVIEW is terminal and non-admitting in Patch 7+8. It
# creates no handle, write admission or executable snapshot. No existing
# automated actor and no use of the GOVERNING_HUMAN role may convert it to ADMIT;
# time cannot promote it.
# Changed evidence, policy or authority state requires a new gate evaluation.
# A future positive human resolution needs a separate typed, durable and
# independently authorised record; that record and workflow do not exist here.

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
            GateCheckedDimension.CAPABILITIES,
            GateCheckedDimension.ORACLE,
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
            GateCheckedDimension.CAPABILITIES,
            GateCheckedDimension.ORACLE,
            GateCheckedDimension.ENVIRONMENT,
            GateCheckedDimension.TOOLS,
            GateCheckedDimension.CONFLICTS,
        }
    ),
    # Consumption is the last barrier and checks everything.
    #
    # An earlier revision removed TOOLS here, arguing that declaring a dimension
    # checked while no probe answered for it is an overclaim. The argument was
    # sound and it answered the wrong question: §22 puts tools in the mandatory
    # vocabulary and forbids treating an absent dimension as passed, so neither
    # declaring it unchecked nor declaring it checked-without-evidence complies.
    #
    # The evidence was there and merely unclaimed. The compatibility owner
    # evaluates ENVIRONMENT_AND_TOOLCHAIN as one dimension over separately
    # carried ``tool_inputs``, with TOOLCHAIN_MISMATCH and ENVIRONMENT_MISMATCH as
    # distinct reasons — so one compatibility finding genuinely settles both, and
    # ``DIMENSION_SOURCE`` now says so. The fix was to claim the evidence that
    # exists, not to keep quiet about a dimension the spec requires.
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
    outcomes = []
    for reason in reasons:
        if reason in admitting:
            outcomes.append(GateDecisionKind.ADMIT)
        elif reason in _QUARANTINE_REASONS:
            outcomes.append(GateDecisionKind.QUARANTINE)
        elif reason in _REVIEW_REASONS:
            outcomes.append(GateDecisionKind.REQUIRE_REVIEW)
        else:
            outcomes.append(GateDecisionKind.REJECT)
    # The table is the only ordering authority.  Keeping an if/elif ladder here
    # would create a second precedence that could drift from the declared one.
    return max(outcomes, key=_DECISION_PRECEDENCE.__getitem__)


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
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    gate_kind: GateKind
    subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef | None
    boundary_ref: HashBoundRef | None
    frozen_candidate_set_ref: HashBoundRef | None
    decision_kind: GateDecisionKind
    reason_codes: tuple[str, ...]
    policy_version: str
    checked_dimensions: tuple[str, ...]
    dimension_evidence: tuple["DimensionEvidence", ...]
    diagnostics: dict[str, str]
    authority_identity: AuthorityIdentity
    authority_role: AuthorityRole
    configuration_digest: str
    evaluator_declaration_digest: str
    independence_proof_digest: str
    decided_at_utc: datetime
    predecessor_decision_digest: str | None
    sequence: int
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> GateDecision:
        raise TypeError("GateDecision is produced only by a configured gate controller")

    def to_dict(self) -> dict[str, object]:
        validate_gate_decision(self)
        if self.schema_version is GATE_DECISION_V1:
            return {**_decision_payload(self), "gate_decision_id": self.gate_decision_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _decision_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_gate_decision(self)
        if self.schema_version is GATE_DECISION_V1:
            return _canonical(_decision_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_decision_payload(self),
        )

    @property
    def admitted(self) -> bool:
        validate_gate_decision(self)
        return self.decision_kind is GateDecisionKind.ADMIT

    def subject_keys(self) -> tuple[str, ...]:
        validate_gate_decision(self)
        return tuple(_subject_key(item) for item in self.subject_refs)


def _decision_payload(value: GateDecision) -> dict[str, object]:
    payload = {
        "schema_version": value.schema_version.value,
        "gate_kind": value.gate_kind.value,
        "subject_refs": [item.to_dict() for item in value.subject_refs],
        "consumer_context_ref": None if value.consumer_context_ref is None else value.consumer_context_ref.to_dict(),
        "boundary_ref": None if value.boundary_ref is None else value.boundary_ref.to_dict(),
        "decision_kind": value.decision_kind.value,
        "reason_codes": list(value.reason_codes),
        "policy_version": value.policy_version,
        "checked_dimensions": list(value.checked_dimensions),
        "dimension_evidence": [item.to_dict() for item in value.dimension_evidence],
        "diagnostics": dict(value.diagnostics),
        "authority_identity": value.authority_identity.to_dict(),
        "authority_role": value.authority_role.value,
        "configuration_digest": value.configuration_digest,
        "evaluator_declaration_digest": value.evaluator_declaration_digest,
        "independence_proof_digest": value.independence_proof_digest,
        "decided_at_utc": value.decided_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
        "predecessor_decision_digest": value.predecessor_decision_digest,
        "sequence": value.sequence,
    }
    if value.schema_version is GATE_DECISION_V2:
        payload["frozen_candidate_set_ref"] = (
            None
            if value.frozen_candidate_set_ref is None
            else value.frozen_candidate_set_ref.to_dict()
        )
    return payload


def validate_gate_decision(value: GateDecision) -> None:
    if type(value) is not GateDecision or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate decision is not controller sealed")
    if value.schema_version not in {GATE_DECISION_V1, GATE_DECISION_V2}:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "gate decision schema is unknown")
    if type(value.gate_kind) is not GateKind or type(value.decision_kind) is not GateDecisionKind:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision enums are invalid")
    require_role_for_gate(value.authority_role, gate=value.gate_kind)
    if type(value.authority_identity) is not AuthorityIdentity or type(value.authority_role) is not AuthorityRole:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision authority is invalid")
    # §22 puts the independence proof inside the decision's identity. These three
    # digests are how it gets there: they name the authority configuration, the
    # evaluator registration and the proof this verdict rests on, so a restored
    # decision states which entitlement it claims instead of only asserting a
    # role. Whether that entitlement holds is settled by
    # require_entitled_decision, against copies the verifier holds itself.
    _sha256_text(value.configuration_digest, "configuration_digest")
    _sha256_text(value.evaluator_declaration_digest, "evaluator_declaration_digest")
    _sha256_text(value.independence_proof_digest, "independence_proof_digest")
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
    if value.schema_version is GATE_DECISION_V2 and value.gate_kind is GateKind.RETRIEVAL:
        if (
            type(value.frozen_candidate_set_ref) is not HashBoundRef
            or value.frozen_candidate_set_ref.kind is not RefKind.ARTIFACT
            or value.frozen_candidate_set_ref.schema_id
            != SchemaVersion.FROZEN_CANDIDATE_SET_V2.value
        ):
            raise _fail(
                AdmissionFailureCode.TYPE_MISMATCH,
                "retrieval requires the exact v2 frozen candidate set ref",
            )
    elif value.frozen_candidate_set_ref is not None:
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            f"{value.gate_kind.value} must not carry a frozen candidate set ref",
        )
    if value.predecessor_decision_digest is not None:
        digest = value.predecessor_decision_digest
        if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "predecessor digest is invalid")
    _require_no_contradiction(value.gate_kind, value.decision_kind, value.reason_codes)
    try:
        if value.schema_version is GATE_DECISION_V1:
            validate_record_id(value.gate_decision_id, canonical_bytes=_canonical(_decision_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 decision envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_decision_payload(value)),
                expected_identity_domain=IdentityDomain.GATE_DECISION_V2,
            )
            if value.gate_decision_id != value.envelope.record_id:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "decision identity differs from envelope")
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
    wrapped = set(value) == {"envelope", "envelope_binding_sha256", "payload"}
    raw_record = value
    value = value["payload"] if wrapped else value
    if type(value) is not dict:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "gate decision domain payload is invalid")
    try:
        schema = SchemaVersion(value.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "gate decision schema is unknown") from exc
    required = (
        "schema_version", "gate_kind", "subject_refs", "consumer_context_ref", "boundary_ref",
        "decision_kind", "reason_codes", "policy_version", "checked_dimensions",
        "dimension_evidence", "diagnostics",
        "authority_identity", "authority_role", "configuration_digest",
        "evaluator_declaration_digest", "independence_proof_digest", "decided_at_utc",
        "predecessor_decision_digest", "sequence",
    )
    if schema is GATE_DECISION_V2:
        required = (*required, "frozen_candidate_set_ref")
    if set(value) != set(required) or any(type(key) is not str for key in value):
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "gate decision payload field set is incomplete or unknown",
        )
    if schema not in {GATE_DECISION_V1, GATE_DECISION_V2} or wrapped != (schema is GATE_DECISION_V2):
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "gate decision schema/wrapper is unknown")
    if expected_ref.schema_id != schema.value:
        raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "expected ref names another decision schema")
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
    try:
        restored_dimensions = tuple(GateCheckedDimension(item) for item in raw_dimensions)
    except (TypeError, ValueError) as exc:
        raise _fail(AdmissionFailureCode.DIMENSION_NOT_CHECKED, "checked dimension is unknown") from exc
    _dimensions(gate, restored_dimensions)
    result = object.__new__(GateDecision)
    object.__setattr__(result, "schema_version", schema)
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
    object.__setattr__(
        result,
        "frozen_candidate_set_ref",
        None
        if schema is GATE_DECISION_V1 or value["frozen_candidate_set_ref"] is None
        else HashBoundRef.from_dict(value["frozen_candidate_set_ref"]),
    )
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "reason_codes", tuple(raw_reasons))
    object.__setattr__(result, "policy_version", value["policy_version"])
    object.__setattr__(result, "checked_dimensions", tuple(raw_dimensions))
    raw_evidence = value["dimension_evidence"]
    if type(raw_evidence) is not list:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "dimension_evidence must be an exact list")
    object.__setattr__(
        result,
        "dimension_evidence",
        tuple(
            DimensionEvidence(
                dimension=item["dimension"],
                probe=item["probe"],
                outcome=EvidenceOutcome(item["outcome"]),
                subject_ref_key=item["subject_ref_key"],
                consumer_context_key=item["consumer_context_key"],
                evidence_sha256=item["evidence_sha256"],
            )
            for item in raw_evidence
        ),
    )
    object.__setattr__(result, "diagnostics", _diagnostics(value["diagnostics"]))
    object.__setattr__(result, "authority_identity", AuthorityIdentity.from_dict(value["authority_identity"]))
    object.__setattr__(result, "authority_role", role)
    # Restored, not re-derived. A consumer that wants to know the evaluator was
    # entitled re-checks the named declaration and proof against its own copies;
    # what restoration guarantees is that the decision names them exactly.
    object.__setattr__(result, "configuration_digest", value["configuration_digest"])
    object.__setattr__(result, "evaluator_declaration_digest", value["evaluator_declaration_digest"])
    object.__setattr__(result, "independence_proof_digest", value["independence_proof_digest"])
    object.__setattr__(result, "decided_at_utc", decided)
    object.__setattr__(result, "predecessor_decision_digest", value["predecessor_decision_digest"])
    object.__setattr__(result, "sequence", value["sequence"])
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    if schema is GATE_DECISION_V2:
        try:
            envelope = common_envelope_from_dict(
                raw_record["envelope"],
                canonical_payload_bytes=_canonical(_decision_payload(result)),
            )
        except ContractViolation as exc:
            raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "decision envelope is invalid") from exc
        object.__setattr__(result, "envelope", envelope)
        object.__setattr__(result, "envelope_binding_sha256", raw_record["envelope_binding_sha256"])
        object.__setattr__(result, "gate_decision_id", envelope.record_id)
    else:
        object.__setattr__(result, "envelope", None)
        object.__setattr__(result, "envelope_binding_sha256", None)
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
        schema_id=value.schema_version.value,
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
        if kwargs.pop("_seal", None) is not _CONTROLLER_SEAL or kwargs or len(args) != 19:
            raise TypeError("ConfiguredGateController is factory-created")
        (
            self._authority_handle,
            self._declaration,
            self._independence_proof,
            self._authority_identity,
            self._authority_roles,
            self._policy_version,
            self._run_id,
            self._attempt_id,
            self._repository_revision,
            self._environment_profile_id,
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

    @property
    def _source_actors(self) -> tuple[ActorIdentity, ...]:
        """The actor set every independence re-check is run against.

        Rebuilt from the frozen participant list rather than stored twice, so
        the set the proof is verified against cannot drift from the set the
        controller was configured with.
        """

        return tuple(ActorIdentity(value=item) for item in self._participants)

    @property
    def declaration(self) -> GateEvaluatorDeclaration:
        return self._declaration

    @property
    def independence_proof(self) -> GateIndependenceProof:
        return self._independence_proof

    def role_for(self, gate: GateKind) -> AuthorityRole:
        """The role this authority signs the given gate with.

        Derived from the declaration and re-checked against the independence
        proof on every call. An earlier revision read it out of a mapping the
        caller supplied, which made "may this identity decide this gate" a
        statement the caller got to make about itself.
        """

        role = require_independent_evaluator(
            self._independence_proof,
            declaration=self._declaration,
            gate=gate,
            source_actors=self._source_actors,
        )
        return require_role_for_gate(role, gate=gate)

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def run_id(self) -> RunId:
        return self._run_id

    @property
    def attempt_id(self) -> AttemptId:
        return self._attempt_id

    @property
    def repository_revision(self) -> str:
        return self._repository_revision

    @property
    def environment_profile_id(self) -> str:
        return self._environment_profile_id


def configure_gate_controller(
    *,
    declaration: GateEvaluatorDeclaration,
    policy_version: str,
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: str,
    environment_profile_id: str,
    trusted_clock: Callable[[], datetime],
    taint_probe: Callable[[HashBoundRef], "TaintFinding"],
    provenance_probe: Callable[[HashBoundRef], bool],
    lifecycle_probe: Callable[[HashBoundRef], bool],
    compatibility_probe: Callable[[HashBoundRef, HashBoundRef], "CompatibilityFinding"],
    boundary_probe: Callable[[HashBoundRef], bool],
    grant_probe: Callable[[], "GrantEnvelope"],
    head_reader: Callable[[], Mapping[str, str]],
    producer_actor: ActorIdentity,
    retriever_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
) -> ConfiguredGateController:
    validate_gate_evaluator_declaration(declaration)
    authority_handle = declaration._authority_handle
    authority_identity = declaration.evaluator_identity
    # The four roles come from the registration, not from the caller. Reading
    # them out of a supplied mapping let whoever configured the controller state
    # its own entitlement; a declaration is a fact about the configuration.
    roles = {gate: require_role_for_gate(declaration.role_for(gate), gate=gate) for gate in GateKind}
    if declaration.policy_version != _identifier(policy_version, "policy_version"):
        raise _fail(
            AdmissionFailureCode.POLICY_VERSION_MISMATCH,
            "the controller policy version is not the one the evaluator was declared under",
        )
    if type(run_id) is not RunId or type(attempt_id) is not AttemptId:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "controller execution identity is invalid")
    try:
        parsed_revision = RepositoryRevision.git_commit(repository_revision)
        assert parsed_revision.git_sha is not None
        revision = parsed_revision.git_sha
    except ContractViolation as exc:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "controller repository revision is invalid") from exc
    environment = _identifier(environment_profile_id, "environment_profile_id")
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
    proof = create_gate_independence_proof(
        declaration=declaration,
        source_actors=(producer_actor, retriever_actor, consumer_actor),
    )
    return ConfiguredGateController(
        authority_handle,
        declaration,
        proof,
        authority_identity,
        roles,
        policy_version,
        run_id,
        attempt_id,
        revision,
        environment,
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


def _controller_with_required_actor(
    controller: ConfiguredGateController,
    actor: ActorIdentity,
) -> ConfiguredGateController:
    """Return a sealed controller whose proof includes one mandatory actor.

    The write adapter uses this to make the configured publisher part of both
    pre-boundary gate decisions.  It is intentionally private: ordinary gate
    callers cannot append convenient actors after a decision has been made.
    """

    require_configured_gate_controller(controller)
    if type(actor) is not ActorIdentity:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "required gate actor must be exact")
    participants = tuple(sorted(set(controller._participants + (actor.value,))))
    actors = tuple(ActorIdentity(value=item) for item in participants)
    proof = create_gate_independence_proof(
        declaration=controller.declaration,
        source_actors=actors,
    )
    return ConfiguredGateController(
        controller._authority_handle,
        controller.declaration,
        proof,
        controller.authority_identity,
        controller._authority_roles,
        controller.policy_version,
        controller.run_id,
        controller.attempt_id,
        controller.repository_revision,
        controller.environment_profile_id,
        controller._trusted_clock,
        controller._taint_probe,
        controller._provenance_probe,
        controller._lifecycle_probe,
        controller._compatibility_probe,
        controller._boundary_probe,
        controller._grant_probe,
        controller._head_reader,
        participants,
        _seal=_CONTROLLER_SEAL,
    )


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
    """What a compatibility port reports about one subject in this context.

    ``subject_ref`` and ``consumer_context_ref`` are part of the finding, not
    context the gate remembers separately. An earlier revision left them out and
    called the port with the subject alone, which made the central compatibility
    claim unverifiable: the controller could be configured with a closure bound
    to consumer context A, the gate would record consumer context B on the
    decision, and nothing in the types could tell the two apart. §22 requires
    compatibility to be established against the exact frozen consumer context —
    a finding that cannot say which context it was computed for does not
    establish it.

    So the finding states what it is about, and the gate refuses one that
    answers about a different subject or a different context than it asked
    about. A port that ignores the context it was handed now produces a typed
    contract violation instead of a silently mismatched admission.
    """

    compatible: bool
    evidence_complete: bool
    drifted: bool
    conflicts_unresolved: bool
    subject_ref: HashBoundRef
    consumer_context_ref: HashBoundRef

    def __post_init__(self) -> None:
        for field_name in ("subject_ref", "consumer_context_ref"):
            if type(getattr(self, field_name)) is not HashBoundRef:
                raise _fail(
                    AdmissionFailureCode.TYPE_MISMATCH,
                    f"compatibility finding {field_name} must be an exact HashBoundRef",
                )
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


def _require_finding_about(
    finding: object,
    *,
    subject_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
) -> CompatibilityFinding:
    """Refuse a compatibility answer that is about something else.

    ``_probe`` establishes that the port returned the right *type*. This
    establishes that it returned an answer to the right *question*, which is a
    separate claim and the one §22 actually rests on: compatibility is a
    property of a subject in a frozen consumer context, so an answer that names
    a different subject or a different context does not support a decision about
    this one, however well-formed it is.

    Without this check the port could be a closure bound to any context and the
    gate would have no way to notice — it would record the context it was given
    and the finding computed for another, and the resulting decision would look
    entirely valid.
    """

    assert isinstance(finding, CompatibilityFinding)
    if _subject_key(finding.subject_ref) != _subject_key(subject_ref):
        raise _fail(
            AdmissionFailureCode.PROBE_CONTRACT_VIOLATION,
            "the compatibility port answered about a different subject",
        )
    if _subject_key(finding.consumer_context_ref) != _subject_key(consumer_context_ref):
        raise _fail(
            AdmissionFailureCode.PROBE_CONTRACT_VIOLATION,
            "the compatibility port answered about a different consumer context",
        )
    return finding


# ---------------------------------------------------------------------------
# Per-dimension evidence — a declaration is not a proof
# ---------------------------------------------------------------------------

#: Which port answers which dimension. A gate that merely listed dimension names
#: was asserting coverage; this says who was actually asked. One probe can carry
#: several dimensions — a compatibility finding settles binding, revision,
#: environment and conflicts together — and that is stated here rather than left
#: to a reader to infer.
DIMENSION_SOURCE: dict[str, tuple[GateCheckedDimension, ...]] = {
    "taint": (GateCheckedDimension.SOURCE_TAINT,),
    "provenance": (GateCheckedDimension.PROVENANCE,),
    "lifecycle": (GateCheckedDimension.LIFECYCLE,),
    # ENVIRONMENT and TOOLS both come from this one probe because the
    # compatibility owner evaluates them as one dimension —
    # ``ENVIRONMENT_AND_TOOLCHAIN`` — over ``environment_inputs`` and
    # ``tool_inputs`` carried separately in the context, and it distinguishes the
    # two in its reasons. Listing only ENVIRONMENT understated what was actually
    # asked and left a §22-mandatory dimension looking unanswerable.
    "compatibility": (
        GateCheckedDimension.BINDING,
        GateCheckedDimension.REVISION,
        GateCheckedDimension.ENVIRONMENT,
        GateCheckedDimension.TOOLS,
        GateCheckedDimension.CONFLICTS,
    ),
    # A GrantEnvelope carries scopes, capabilities and oracles — and no tools.
    # An earlier draft of this map listed TOOLS here too, which would have been
    # the same overclaim in a new place.
    "grant": (
        GateCheckedDimension.SCOPE,
        GateCheckedDimension.CAPABILITIES,
        GateCheckedDimension.ORACLE,
    ),
}


class EvidenceOutcome(str, Enum):
    """What a probe's answer established for one dimension. Closed on purpose."""

    PASS = "PASS"
    BLOCK = "BLOCK"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class DimensionEvidence:
    """What was actually consulted for one dimension, and about what.

    ``checked_dimensions`` used to be written from a constant map, so every
    decision declared every required dimension checked no matter what evidence
    existed. For a REJECT that is at least fail-closed; for an ADMIT it means the
    audit trail cannot say which exact evidence supported each pass. §22 asks a
    decision to record the dimensions it checked, and a name is not a record.

    Each entry names the dimension, the port that answered, the subject and
    consumer context the answer was about, the outcome, and a digest of the
    finding itself — so a later reader can tell a pass backed by a fresh taint
    finding from a pass backed by nothing.
    """

    dimension: str
    probe: str
    outcome: EvidenceOutcome
    subject_ref_key: str | None
    consumer_context_key: str | None
    evidence_sha256: str

    def __post_init__(self) -> None:
        if self.dimension not in {item.value for item in GateCheckedDimension}:
            raise _fail(AdmissionFailureCode.DIMENSION_NOT_CHECKED, "unknown checked dimension")
        if self.probe not in DIMENSION_SOURCE:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "unknown evidence source")
        if type(self.outcome) is not EvidenceOutcome:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "evidence outcome must be exact")
        _sha256_text(self.evidence_sha256, "evidence_sha256")

    def to_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "probe": self.probe,
            "outcome": self.outcome.value,
            "subject_ref_key": self.subject_ref_key,
            "consumer_context_key": self.consumer_context_key,
            "evidence_sha256": self.evidence_sha256,
        }


def _evidence_digest(value: object) -> str:
    """A stable digest of the exact answer a port gave."""

    if value is None:
        return hashlib.sha256(b"unavailable").hexdigest()
    if type(value) is bool:
        return hashlib.sha256(b"true" if value else b"false").hexdigest()
    payload = {
        name: getattr(value, name)
        for name in sorted(vars(value))
        if not name.startswith("_")
    }
    return hashlib.sha256(_canonical(_plain(payload))).hexdigest()


def _plain(value: object) -> object:
    """Reduce a finding to canonical-safe primitives.

    Tuples and enums are ordinary in these records and unrepresentable in the
    canonical profile, so they are lowered here rather than at the call site —
    the digest has to cover the whole answer, not the part that happened to
    serialise.
    """

    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    return value


class _EvidenceLog:
    """Collects per-dimension evidence while a gate evaluates."""

    def __init__(self) -> None:
        self._entries: list[DimensionEvidence] = []

    def record(
        self,
        probe: str,
        *,
        answer: object,
        blocked: bool,
        subject_ref: HashBoundRef | None = None,
        consumer_context_ref: HashBoundRef | None = None,
    ) -> None:
        outcome = EvidenceOutcome.BLOCK if blocked else EvidenceOutcome.PASS
        for dimension in DIMENSION_SOURCE[probe]:
            self._entries.append(
                DimensionEvidence(
                    dimension=dimension.value,
                    probe=probe,
                    outcome=outcome,
                    subject_ref_key=None if subject_ref is None else _subject_key(subject_ref),
                    consumer_context_key=(
                        None if consumer_context_ref is None else _subject_key(consumer_context_ref)
                    ),
                    evidence_sha256=_evidence_digest(answer),
                )
            )

    def unavailable(self, probe: str, *, subject_ref: HashBoundRef | None = None) -> None:
        for dimension in DIMENSION_SOURCE[probe]:
            self._entries.append(
                DimensionEvidence(
                    dimension=dimension.value,
                    probe=probe,
                    outcome=EvidenceOutcome.UNAVAILABLE,
                    subject_ref_key=None if subject_ref is None else _subject_key(subject_ref),
                    consumer_context_key=None,
                    evidence_sha256=_evidence_digest(None),
                )
            )

    def build(self) -> tuple[DimensionEvidence, ...]:
        return tuple(
            sorted(
                self._entries,
                key=lambda item: (
                    item.dimension,
                    item.subject_ref_key or "",
                    item.probe,
                    item.outcome.value,
                    item.evidence_sha256,
                ),
            )
        )


def require_dimension_evidence(value: GateDecision) -> tuple[DimensionEvidence, ...]:
    """Refuse a decision whose declared dimensions are not all evidenced.

    This is the check that turns ``checked_dimensions`` from a declaration into
    a claim with something behind it: every dimension the gate declares must
    have at least one entry naming the port that answered for it, and no entry
    may cover a dimension the gate does not require.
    """

    validate_gate_decision(value)
    declared = set(value.checked_dimensions)
    evidenced = {item.dimension for item in value.dimension_evidence}
    missing = sorted(declared - evidenced)
    if missing:
        raise _fail(
            AdmissionFailureCode.DIMENSION_NOT_CHECKED,
            f"declared dimensions without evidence: {', '.join(missing[:3])}",
        )
    extra = sorted(evidenced - declared)
    if extra:
        raise _fail(
            AdmissionFailureCode.DIMENSION_NOT_CHECKED,
            f"evidence for undeclared dimensions: {', '.join(extra[:3])}",
        )
    return value.dimension_evidence



def _make_decision(
    controller: ConfiguredGateController,
    *,
    gate: GateKind,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef | None,
    boundary_ref: HashBoundRef | None,
    frozen_candidate_set_ref: HashBoundRef | None,
    reasons: tuple[str, ...],
    dimensions: frozenset[GateCheckedDimension],
    evidence: "_EvidenceLog",
    diagnostics: Mapping[str, str],
    sequence: int,
    predecessor: GateDecision | None,
) -> GateDecision:
    ordered_reasons = tuple(sorted(set(reasons)))
    decision_kind = resolve_decision_kind(gate, ordered_reasons)
    ordered_dimensions = tuple(sorted(item.value for item in dimensions))
    result = object.__new__(GateDecision)
    object.__setattr__(result, "schema_version", GATE_DECISION_V2)
    object.__setattr__(result, "gate_kind", gate)
    object.__setattr__(result, "subject_refs", _subjects(subject_refs))
    object.__setattr__(result, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(result, "boundary_ref", boundary_ref)
    object.__setattr__(result, "frozen_candidate_set_ref", frozen_candidate_set_ref)
    object.__setattr__(result, "decision_kind", decision_kind)
    object.__setattr__(result, "reason_codes", ordered_reasons)
    object.__setattr__(result, "policy_version", controller.policy_version)
    object.__setattr__(result, "checked_dimensions", ordered_dimensions)
    object.__setattr__(result, "dimension_evidence", evidence.build())
    object.__setattr__(result, "diagnostics", _diagnostics(dict(diagnostics)))
    object.__setattr__(result, "authority_identity", controller.authority_identity)
    # role_for re-runs the independence check, so a decision cannot be produced
    # by an evaluator whose entitlement no longer verifies.
    object.__setattr__(result, "authority_role", controller.role_for(gate))
    object.__setattr__(result, "configuration_digest", controller.declaration.configuration_id.digest_sha256)
    object.__setattr__(result, "evaluator_declaration_digest", declaration_digest(controller.declaration))
    object.__setattr__(result, "independence_proof_digest", controller.independence_proof.proof_id.digest_sha256)
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
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.GATE_DECISION_V2,
        canonical_payload_bytes=_canonical(_decision_payload(result)),
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        created_at_utc=result.decided_at_utc,
        producer_component=controller.declaration.evaluator_component_id,
        repository_revision=RepositoryRevision.git_commit(controller.repository_revision),
        policy_version=controller.policy_version,
        environment_profile_id=controller.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(result, "gate_decision_id", envelope.record_id)
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
    evidence = _EvidenceLog()
    unavailable = False
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
        except _ProbeUnavailable:
            unavailable = True
            evidence.unavailable("taint", subject_ref=ref)
            evidence.unavailable("provenance", subject_ref=ref)
            continue
        evidence.record("taint", answer=taint, blocked=not taint.consumable, subject_ref=ref)
        evidence.record("provenance", answer=provenance, blocked=not provenance, subject_ref=ref)
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
        frozen_candidate_set_ref=None,
        reasons=tuple(reasons),
        evidence=evidence,
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
    evidence = _EvidenceLog()
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
            for name in ("taint", "lifecycle", "provenance"):
                evidence.unavailable(name, subject_ref=ref)
            continue
        evidence.record("taint", answer=taint, blocked=taint.blocks_publication, subject_ref=ref)
        evidence.record("lifecycle", answer=lifecycle, blocked=not lifecycle, subject_ref=ref)
        evidence.record("provenance", answer=provenance, blocked=not provenance, subject_ref=ref)
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
        evidence.record("grant", answer=granted, blocked=False)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
        evidence.unavailable("grant")
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
        frozen_candidate_set_ref=None,
        reasons=tuple(reasons),
        evidence=evidence,
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
    boundary_ref: HashBoundRef,
    frozen_candidate_set_ref: HashBoundRef,
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
    if type(boundary_ref) is not HashBoundRef or boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            "retrieval requires an exact committed boundary ref",
        )
    if (
        type(frozen_candidate_set_ref) is not HashBoundRef
        or frozen_candidate_set_ref.kind is not RefKind.ARTIFACT
        or frozen_candidate_set_ref.schema_id != SchemaVersion.FROZEN_CANDIDATE_SET_V2.value
    ):
        raise _fail(
            AdmissionFailureCode.TYPE_MISMATCH,
            "retrieval requires an exact v2 frozen candidate set ref",
        )
    reasons: list[str] = []
    diagnostics: dict[str, str] = {}
    evidence = _EvidenceLog()
    unavailable = False
    try:
        boundary_current = _probe(lambda: controller._boundary_probe(boundary_ref), bool)
    except _ProbeUnavailable:
        boundary_current = False
        unavailable = True
    if not boundary_current:
        reasons.append(RetrievalReason.COMPATIBILITY_REJECTED.value)
        diagnostics["boundary"] = "retrieval boundary is not the exact committed authority"
    if not predecessor.admitted:
        reasons.append(RetrievalReason.COMPATIBILITY_REJECTED.value)
        diagnostics["predecessor"] = "publication did not admit this subject set"
    for ref in _subjects(subject_refs):
        try:
            taint = _probe(lambda ref=ref: controller._taint_probe(ref), TaintFinding)
            lifecycle = _probe(lambda ref=ref: controller._lifecycle_probe(ref), bool)
            provenance = _probe(lambda ref=ref: controller._provenance_probe(ref), bool)
            compatibility = _require_finding_about(
                _probe(
                    lambda ref=ref: controller._compatibility_probe(ref, consumer_context_ref),
                    CompatibilityFinding,
                ),
                subject_ref=ref,
                consumer_context_ref=consumer_context_ref,
            )
        except _ProbeUnavailable:
            unavailable = True
            for name in ("taint", "lifecycle", "provenance", "compatibility"):
                evidence.unavailable(name, subject_ref=ref)
            continue
        evidence.record("taint", answer=taint, blocked=not taint.consumable, subject_ref=ref)
        evidence.record("lifecycle", answer=lifecycle, blocked=not lifecycle, subject_ref=ref)
        evidence.record("provenance", answer=provenance, blocked=not provenance, subject_ref=ref)
        evidence.record(
            "compatibility",
            answer=compatibility,
            blocked=not compatibility.compatible,
            subject_ref=ref,
            consumer_context_ref=consumer_context_ref,
        )
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
        evidence.record("grant", answer=granted, blocked=False)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
        evidence.unavailable("grant")
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
        boundary_ref=boundary_ref,
        frozen_candidate_set_ref=frozen_candidate_set_ref,
        reasons=tuple(reasons),
        evidence=evidence,
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
    evidence = _EvidenceLog()
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
            compatibility = _require_finding_about(
                _probe(
                    lambda ref=ref: controller._compatibility_probe(ref, consumer_context_ref),
                    CompatibilityFinding,
                ),
                subject_ref=ref,
                consumer_context_ref=consumer_context_ref,
            )
        except _ProbeUnavailable:
            unavailable = True
            for name in ("taint", "lifecycle", "provenance", "compatibility"):
                evidence.unavailable(name, subject_ref=ref)
            continue
        evidence.record("taint", answer=taint, blocked=not taint.consumable, subject_ref=ref)
        evidence.record("lifecycle", answer=lifecycle, blocked=not lifecycle, subject_ref=ref)
        evidence.record("provenance", answer=provenance, blocked=not provenance, subject_ref=ref)
        evidence.record(
            "compatibility",
            answer=compatibility,
            blocked=not compatibility.compatible,
            subject_ref=ref,
            consumer_context_ref=consumer_context_ref,
        )
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
        evidence.record("grant", answer=granted, blocked=False)
    except _ProbeUnavailable:
        unavailable = True
        granted = None
        evidence.unavailable("grant")
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
        frozen_candidate_set_ref=None,
        reasons=tuple(reasons),
        evidence=evidence,
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



def derive_independence_proof(declaration: object, source_actors: tuple[ActorIdentity, ...]):
    """Re-run the independence computation from the verifier's own inputs.

    Taking a ready-made proof as an argument would let a caller supply one that
    matches the decision it is meant to judge, which checks that two records agree
    and nothing else. Deriving it here means the verifier computes independence
    over the actor set it believes is in play, and a decision made under any other
    set fails to name the result.
    """

    from .authority_config import create_gate_independence_proof

    if type(source_actors) is not tuple or not source_actors:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "entitlement is established against a non-empty source actor set",
        )
    return create_gate_independence_proof(
        declaration=declaration, source_actors=source_actors
    )


def build_gate_decision_chain(
    *,
    ingestion: GateDecision,
    publication: GateDecision,
    retrieval: GateDecision,
    consumption: GateDecision,
    entitlements: object,
) -> GateDecisionChain:
    """Bind four decisions into one validated, strictly ordered chain.

    ``entitlements`` carries the *verifier's own* declaration and source actor set
    for each gate, and it is required. Every check below this line reads digests
    the decisions carry about themselves, which is precisely the self-approval
    NR-08 forbids: a decision naming an independent-looking authority proves only
    that its bytes were not edited. Entitlement is re-established here from copies
    the caller holds, and the independence computation is re-run rather than
    trusted — which is why the proof is derived from each declaration and actor
    set instead of being accepted ready-made.

    **Per gate, not per chain.** A first revision took one declaration for all
    four and broke `test_four_separate_authorities_may_decide_one_chain`. §22 asks
    for four independent decisions; it never says one organisation signs all four,
    and requiring that would rule out the separation of duties the section exists
    for. One authority holding several gate-specific roles stays equally legal —
    the mapping simply names the same declaration four times.

    `require_entitled_decision` existed for this and had no production caller at
    all; the audit found it by grep. A barrier nothing invokes is documentation.
    """

    ordered = (ingestion, publication, retrieval, consumption)
    if not isinstance(entitlements, Mapping):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "a chain requires the verifier's entitlement for each gate",
        )
    for decision in ordered:
        held = entitlements.get(decision.gate_kind)
        if held is None:
            raise _fail(
                AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
                f"no verifier entitlement was supplied for the {decision.gate_kind.value} gate",
            )
        declaration, source_actors = held
        require_entitled_decision(
            decision,
            declaration=declaration,
            proof=derive_independence_proof(declaration, source_actors),
            source_actors=source_actors,
        )
    prior: GateDecision | None = None
    subjects: tuple[str, ...] | None = None
    for decision in ordered:
        validate_gate_decision(decision)
        # Dimension evidence is checked here, on the mandatory path, rather than
        # left to a caller who might remember. Both this and the entitlement
        # check existed as public helpers with no call site anywhere in
        # production - a barrier nothing crosses is not a barrier, it is a
        # function that happens to be correct.
        require_dimension_evidence(decision)
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


def _require_decision_journal_coordinator_id(value: object) -> str:
    try:
        mutation_fence = getattr(value, "mutation_fence")
        coordinator_id = getattr(mutation_fence, "coordinator_id")
    except Exception as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "decision journal has no readable mutation coordinator",
        ) from exc
    if not callable(coordinator_id):
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "decision journal mutation coordinator is not callable",
        )
    try:
        result = coordinator_id()
    except Exception as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "decision journal mutation coordinator is unavailable",
        ) from exc
    if type(result) is not str or not result:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "decision journal mutation coordinator needs an exact non-empty identity",
        )
    return result


def require_decision_journal(value: object) -> DecisionJournalPort:
    try:
        missing = [
            name
            for name in ("append_record", "contains_record", "current_anchor", "extends")
            if not callable(getattr(value, name, None))
        ]
    except Exception as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "decision journal contract is unavailable",
        ) from exc
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
    receipt_id: RecordId | None
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
    gate_decision_id: RecordId
    decision_digest: str
    journal_anchor: str
    committed_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> DecisionCommitReceipt:
        raise TypeError("DecisionCommitReceipt is produced only by commit_gate_decision")

    def to_dict(self) -> dict[str, object]:
        validate_commit_receipt(self)
        payload = _receipt_payload(self)
        if self.schema_version is SchemaVersion.DECISION_COMMIT_RECEIPT_V1:
            return payload
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": payload,
        }

    def canonical_bytes(self) -> bytes:
        validate_commit_receipt(self)
        if self.schema_version is SchemaVersion.DECISION_COMMIT_RECEIPT_V1:
            return _canonical(_receipt_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_receipt_payload(self),
        )


def _receipt_payload(value: DecisionCommitReceipt) -> dict[str, object]:
    return {
            "schema_version": value.schema_version.value,
            "gate_decision_id": value.gate_decision_id.to_dict(),
            "decision_digest": value.decision_digest,
            "journal_anchor": value.journal_anchor,
            "committed_at_utc": value.committed_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
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
    if value.schema_version not in {
        SchemaVersion.DECISION_COMMIT_RECEIPT_V1,
        SchemaVersion.DECISION_COMMIT_RECEIPT_V2,
    }:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "commit receipt schema is unknown")
    _sha256_text(value.decision_digest, "decision_digest")
    _sha256_text(value.journal_anchor, "journal_anchor")
    _timestamp(value.committed_at_utc, "committed_at_utc")
    if value.schema_version is SchemaVersion.DECISION_COMMIT_RECEIPT_V2:
        if value.receipt_id is None or value.envelope is None or value.envelope_binding_sha256 is None:
            raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 receipt envelope is absent")
        try:
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_receipt_payload(value)),
                expected_identity_domain=IdentityDomain.DECISION_COMMIT_RECEIPT_V2,
            )
        except ContractViolation as exc:
            raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "receipt envelope is invalid") from exc
        if value.receipt_id != value.envelope.record_id:
            raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "receipt identity differs from envelope")


def commit_gate_decision(
    decision: GateDecision,
    *,
    journal: DecisionJournalPort,
    trusted_clock: Callable[[], datetime],
    ticket: object = None,
) -> DecisionCommitReceipt:
    """Append one decision to the durable journal and return its receipt.

    The decision is written in its canonical form, so the digest a later reader
    recomputes is the digest that was committed. An append that raises produces
    no receipt at all rather than a receipt describing a write that did not
    happen — the same fail-closed rule the gates themselves follow.

    ``ticket`` is passed straight through to the journal and is never inspected
    here. This owner has no coordinator and must not acquire one: the ticket is
    an opaque capability that belongs to whichever transaction opened it, and the
    journal adapter is the party that can check it against its own coordinator.
    Without one the append is its own transaction, which is what every caller
    outside a larger transaction wants.
    """

    validate_gate_decision(decision)
    if decision.schema_version is not GATE_DECISION_V2 or decision.envelope is None:
        raise _fail(AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION, "current commit requires a v2 gate decision")
    require_decision_journal(journal)
    if not callable(trusted_clock):
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    payload = decision.canonical_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    try:
        if ticket is None:
            journal.append_record(payload)
        else:
            journal.append_record(payload, ticket=ticket)
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
    object.__setattr__(receipt, "schema_version", SchemaVersion.DECISION_COMMIT_RECEIPT_V2)
    object.__setattr__(receipt, "gate_decision_id", decision.gate_decision_id)
    object.__setattr__(receipt, "decision_digest", digest)
    object.__setattr__(receipt, "journal_anchor", _sha256_text(anchor, "journal_anchor"))
    object.__setattr__(receipt, "committed_at_utc", _timestamp(trusted_clock(), "committed_at_utc"))
    object.__setattr__(receipt, "_trusted_seal", _RECEIPT_SEAL)
    receipt_envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.DECISION_COMMIT_RECEIPT_V2,
        canonical_payload_bytes=_canonical(_receipt_payload(receipt)),
        run_id=decision.envelope.run_id,
        attempt_id=decision.envelope.attempt_id,
        created_at_utc=receipt.committed_at_utc,
        producer_component="admission-journal",
        repository_revision=decision.envelope.repository_revision,
        policy_version=decision.envelope.policy_version,
        environment_profile_id=decision.envelope.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(receipt, "envelope", receipt_envelope)
    object.__setattr__(receipt, "envelope_binding_sha256", compute_envelope_binding_sha256(receipt_envelope))
    object.__setattr__(receipt, "receipt_id", receipt_envelope.record_id)
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

    validate_gate_decision(decision)
    require_decision_journal(journal)
    if not isinstance(receipt, DecisionCommitReceipt):
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "receipt has the wrong runtime type",
        )
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
    validate_commit_receipt(receipt)
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
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
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
        payload = _head_set_payload(self)
        if self.schema_version is SchemaVersion.AUTHORITY_HEAD_SET_V1:
            return payload | {"head_set_id": self.head_set_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": payload,
        }

    def canonical_bytes(self) -> bytes:
        validate_authority_head_set(self)
        if self.schema_version is SchemaVersion.AUTHORITY_HEAD_SET_V1:
            return _canonical(_head_set_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_head_set_payload(self),
        )


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
    if value.schema_version not in {
        SchemaVersion.AUTHORITY_HEAD_SET_V1,
        SchemaVersion.AUTHORITY_HEAD_SET_V2,
    }:
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
        if value.schema_version is SchemaVersion.AUTHORITY_HEAD_SET_V1:
            validate_record_id(value.head_set_id, canonical_bytes=_canonical(_head_set_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 head set envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_head_set_payload(value)),
                expected_identity_domain=IdentityDomain.AUTHORITY_HEAD_SET_V2,
            )
            if value.head_set_id != value.envelope.record_id:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "head set identity differs from envelope")
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
    object.__setattr__(payload, "schema_version", SchemaVersion.AUTHORITY_HEAD_SET_V2)
    object.__setattr__(payload, "boundary_ref", boundary_ref)
    object.__setattr__(payload, "observations", tuple(observations))
    object.__setattr__(payload, "observed_at_utc", _timestamp(controller._trusted_clock(), "observed_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _HEAD_SET_SEAL)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.AUTHORITY_HEAD_SET_V2,
        canonical_payload_bytes=_canonical(_head_set_payload(payload)),
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        created_at_utc=payload.observed_at_utc,
        producer_component=controller.declaration.evaluator_component_id,
        repository_revision=RepositoryRevision.git_commit(controller.repository_revision),
        policy_version=controller.policy_version,
        environment_profile_id=controller.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(payload, "head_set_id", envelope.record_id)
    validate_authority_head_set(payload)
    return payload


def _require_audit_heads_unchanged(
    value: AuthorityHeadSet,
    *,
    observed: AuthorityHeadSet,
) -> AuthorityHeadSet:
    """Compare two audit observations without creating current authority.

    The caller is responsible for how ``observed`` was obtained. In particular,
    ``point_of_use`` supplies the result of its epoch-bracketed coordinated read.
    Equality proves only that the two audit observations match; it is not a
    capability and cannot establish freshness for later use.
    """

    validate_authority_head_set(value)
    validate_authority_head_set(observed)
    if _subject_key(observed.boundary_ref) != _subject_key(value.boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the compared committed boundary is not the one this audit observation saw",
        )
    # The pair, not the digest. A store can advance its sequence while its
    # materialized root stays byte-identical — a rebuild, a re-included head, or
    # an adapter returning a stale anchor beside a fresh sequence. Comparing
    # anchors alone would read all three as "nothing changed".
    drifted = sorted(
        name
        for name in AUTHORITY_HEAD_DOMAINS
        if (
            observed.observation(name).anchor_sha256,
            observed.observation(name).sequence,
        )
        != (
            value.observation(name).anchor_sha256,
            value.observation(name).sequence,
        )
    )
    if drifted:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            f"authority heads changed since the observation: {', '.join(drifted[:3])}",
        )
    return observed


# ---------------------------------------------------------------------------
# AdmittedKnowledgeHandle — durable prerequisite, never present-time authority
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class AdmittedKnowledgeHandle:
    """Durable audit prerequisite for a later point-of-use evaluation.

    The record proves that all four gates admitted the exact subjects, context,
    boundary and policy, that their decisions were durable, and that a valid
    fenced observation existed at mint. It cannot prove that the world remains
    unchanged after mint and does not authorize execution, replay or worker
    delivery by itself. Only ``point_of_use.admit_for_use_now`` can perform the
    fresh Stage 3 and Consumption evaluation required immediately before use.

    Legacy retrieval paths that predate §22 remain audit-only for exactly this
    reason: whatever they return, they cannot produce this object.
    """

    schema_version: SchemaVersion
    handle_id: RecordId
    envelope: CommonEnvelope | None
    envelope_binding_sha256: str | None
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
        payload = _handle_payload(self)
        if self.schema_version is SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1:
            return payload | {"handle_id": self.handle_id.to_dict()}
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": payload,
        }

    def canonical_bytes(self) -> bytes:
        validate_admitted_handle(self)
        if self.schema_version is SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1:
            return _canonical(_handle_payload(self))
        assert self.envelope is not None and self.envelope_binding_sha256 is not None
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_handle_payload(self),
        )


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
    if value.schema_version not in {
        SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1,
        SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V2,
    }:
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
        if value.schema_version is SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1:
            validate_record_id(value.handle_id, canonical_bytes=_canonical(_handle_payload(value)))
        else:
            if value.envelope is None or value.envelope_binding_sha256 is None:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 handle envelope is absent")
            validate_envelope_bound_record(
                envelope=value.envelope,
                envelope_binding_sha256=value.envelope_binding_sha256,
                canonical_domain_payload_bytes=_canonical(_handle_payload(value)),
                expected_identity_domain=IdentityDomain.ADMITTED_KNOWLEDGE_HANDLE_V2,
            )
            if value.handle_id != value.envelope.record_id:
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "handle identity differs from envelope")
            if (
                value.head_set.schema_version is not SchemaVersion.AUTHORITY_HEAD_SET_V2
                or value.commit_receipt.schema_version is not SchemaVersion.DECISION_COMMIT_RECEIPT_V2
                or value.head_set.envelope is None
                or value.commit_receipt.envelope is None
                or value.envelope.run_id != value.head_set.envelope.run_id
                or value.envelope.attempt_id != value.head_set.envelope.attempt_id
                or value.envelope.run_id != value.commit_receipt.envelope.run_id
                or value.envelope.attempt_id != value.commit_receipt.envelope.attempt_id
            ):
                raise _fail(AdmissionFailureCode.DECISION_IDENTITY_MISMATCH, "v2 handle authority records belong to another run or attempt")
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "handle_id does not match its payload",
        ) from exc



def require_entitled_chain(chain: GateDecisionChain, *, entitlements: object) -> None:
    """Re-establish, per gate, that each decision's evaluator was entitled to make it."""

    if not isinstance(entitlements, Mapping):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "the verifier's entitlement is required for each gate",
        )
    for decision in (chain.ingestion, chain.publication, chain.retrieval, chain.consumption):
        held = entitlements.get(decision.gate_kind)
        if held is None:
            raise _fail(
                AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
                f"no verifier entitlement was supplied for the {decision.gate_kind.value} gate",
            )
        declaration, source_actors = held
        require_entitled_decision(
            decision,
            declaration=declaration,
            proof=derive_independence_proof(declaration, source_actors),
            source_actors=source_actors,
        )


def _require_fenced_authority_state(
    value: object,
    *,
    journal: DecisionJournalPort,
) -> AuthorityHeadSet:
    """Accept only the sealed result of the coordination adapter.

    ``coordination.py`` is an adapter of this owner, so the dependency may only
    point from that adapter to this module.  The owner therefore verifies the
    adapter's opaque result at its boundary without importing the adapter back:
    exact nominal origin, the admission-owned non-public seal on the state and
    its read window, and the invariants that make the observation one settled
    epoch. The journal must also belong to that window's coordinator. The
    admission-owned head set is then validated by its own seal as usual.
    """

    value_type = type(value)
    if (
        value_type.__module__ != f"{__package__}.coordination"
        or value_type.__qualname__ != "FencedAuthorityState"
    ):
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "admission requires the exact coordination-produced fenced state",
        )
    window = getattr(value, "window", None)
    head_set = getattr(value, "head_set", None)
    exit_epoch = getattr(value, "exit_epoch", None)
    entry_epoch = getattr(window, "entry_epoch", None)
    window_coordinator_id = getattr(window, "coordinator_id", None)
    if (
        type(window).__module__ != f"{__package__}.coordination"
        or type(window).__qualname__ != "CoordinatedReadWindow"
        or getattr(value, "_trusted_seal", None) is not _FENCED_AUTHORITY_STATE_SEAL
        or getattr(window, "_trusted_seal", None) is not _FENCED_AUTHORITY_STATE_SEAL
        or type(entry_epoch) is not int
        or type(exit_epoch) is not int
        or exit_epoch < 0
        or exit_epoch % 2
        or entry_epoch != exit_epoch
        or type(window_coordinator_id) is not str
        or not window_coordinator_id
    ):
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "fenced authority state is not a sealed settled observation",
        )
    validate_authority_head_set(head_set)
    validated_journal = require_decision_journal(journal)
    journal_coordinator_id = _require_decision_journal_coordinator_id(validated_journal)
    if window_coordinator_id != journal_coordinator_id:
        raise _fail(
            AdmissionFailureCode.COORDINATOR_MISMATCH,
            "fenced authority state belongs to another journal coordinator",
        )
    return head_set


def require_consumption_before_compilation(
    value: GateDecision,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
    compiled: bool,
) -> GateDecision:
    """The §22 barrier as it applies to a replay: gate first, compile second.

    Patch 9 puts the consumption gate ahead of compilation and ahead of the
    first transition, and the ordering is a safety property rather than a
    convention. Compiling first means an object the gate has not yet judged has
    already influenced what will run; by the time a rejection arrives, the
    program it was meant to prevent exists. The bytecode is then one careless
    call away from executing.

    ``compiled`` is what the caller asserts about its own state, and it is a
    negative assertion by design: a replay owner passes ``False`` because it has
    not compiled yet. A caller that has already compiled cannot obtain a
    consumption admission here at all, so the ordering cannot be satisfied
    after the fact by calling this function late.
    """

    if type(compiled) is not bool:
        raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "compiled must be an exact bool")
    if compiled:
        raise _fail(
            AdmissionFailureCode.GATE_SEQUENCE_VIOLATION,
            "the consumption gate must be crossed before the program is compiled",
        )
    return require_consumption_admitted(
        value,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy_version=policy_version,
    )


def canonical_subject_refs(refs: tuple[HashBoundRef, ...]) -> tuple[HashBoundRef, ...]:
    """Return a subject set in the exact order every gate entry point expects.

    The gates require subject refs to be canonically ordered so that one subject
    set has one representation and a decision cannot be made to describe a
    different set by permuting it. The ordering rule is a gate concern, so it is
    published here rather than reimplemented — and guessed at — by each caller
    that assembles a subject set.
    """

    if type(refs) is not tuple:
        raise _fail(AdmissionFailureCode.SUBJECT_MISMATCH, "subject_refs must be an exact tuple")
    for item in refs:
        if type(item) is not HashBoundRef:
            raise _fail(AdmissionFailureCode.TYPE_MISMATCH, "subject_refs must contain exact HashBoundRef")
    return _subjects(tuple(sorted(refs, key=_subject_key)))


def admit_for_consumption(
    chain: GateDecisionChain,
    *,
    controller: ConfiguredGateController,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
    receipts: tuple[DecisionCommitReceipt, ...],
    fenced_state: object,
    journal: DecisionJournalPort,
    entitlements: object,
) -> AdmittedKnowledgeHandle:
    """Record a durable admitted chain and its mint-time fenced observation.

    Four independent conditions, in this order, and every one of them is a
    barrier rather than a formality:

    1. the four-gate chain admits this exact subject set, context, boundary and
       policy — the check ``require_consumption_admitted`` already performs;
    2. the consumption decision is durably committed, proven by a receipt that
       must still be in the journal now, not merely when it was issued;
    3. the supplied observation is the exact sealed result of a settled fenced
       read and names this boundary;
    4. only then does the audit prerequisite exist.

    No second, sequential store read is performed here. The handle records the
    fenced observation at mint but makes no freshness claim about later use;
    only the canonical point-of-use path may create present-time authority.

    A blocked chain yields no handle at all, not a handle over the surviving
    subjects: admission is all-or-nothing for a subject set, so a rejected
    object cannot reach a consumer by surviving in a partially admitted list.
    """

    if type(chain) is not GateDecisionChain or getattr(chain, "_trusted_seal", None) is not _CHAIN_SEAL:
        raise _fail(AdmissionFailureCode.TRUSTED_OBJECT_FORGED, "gate chain is not builder sealed")
    require_configured_gate_controller(controller)
    head_set = _require_fenced_authority_state(fenced_state, journal=journal)
    # Re-established here rather than inherited from the chain's construction, and
    # that is not the same rule twice. The party that builds a chain and the party
    # that mints a handle from it need not be the same, and entitlement is a claim
    # about *whose* copies were consulted. A verifier that never checked with its
    # own declaration has accepted the builder's word for it.
    require_entitled_chain(chain, entitlements=entitlements)
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
    # All four, not just the last. A consumption ADMIT is meaningful because
    # three earlier verdicts led to it, so a handle minted while those are absent
    # from the durable record rests on a lineage that cannot be reconstructed.
    # An earlier revision asked for the consumption receipt alone, which made the
    # four-decision chain a fact about memory rather than about storage.
    if type(receipts) is not tuple or len(receipts) != 4:
        raise _fail(
            AdmissionFailureCode.CHAIN_NOT_DURABLE,
            "a handle requires one durable receipt per gate",
        )
    decisions = (chain.ingestion, chain.publication, chain.retrieval, chain.consumption)
    for decision, item in zip(decisions, receipts):
        require_committed_decision(item, decision=decision, journal=journal)
    receipt = receipts[-1]
    if _subject_key(head_set.boundary_ref) != _subject_key(boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the observation was taken against another committed boundary",
        )
    payload = object.__new__(AdmittedKnowledgeHandle)
    object.__setattr__(payload, "schema_version", SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V2)
    object.__setattr__(payload, "subject_refs", chain.consumption.subject_refs)
    object.__setattr__(payload, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(payload, "boundary_ref", boundary_ref)
    object.__setattr__(payload, "policy_version", policy_version)
    object.__setattr__(payload, "consumption_decision_id", chain.consumption.gate_decision_id)
    object.__setattr__(payload, "commit_receipt", receipt)
    object.__setattr__(payload, "head_set", head_set)
    object.__setattr__(payload, "admitted_at_utc", _timestamp(controller._trusted_clock(), "admitted_at_utc"))
    object.__setattr__(payload, "_trusted_seal", _HANDLE_SEAL)
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.ADMITTED_KNOWLEDGE_HANDLE_V2,
        canonical_payload_bytes=_canonical(_handle_payload(payload)),
        run_id=controller.run_id,
        attempt_id=controller.attempt_id,
        created_at_utc=payload.admitted_at_utc,
        producer_component=controller.declaration.evaluator_component_id,
        repository_revision=RepositoryRevision.git_commit(controller.repository_revision),
        policy_version=controller.policy_version,
        environment_profile_id=controller.environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(payload, "envelope", envelope)
    object.__setattr__(payload, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(payload, "handle_id", envelope.record_id)
    validate_admitted_handle(payload)
    return payload


def admitted_handle_ref(value: AdmittedKnowledgeHandle) -> HashBoundRef:
    """Return the hash-bound reference a replay request or lineage record stores."""

    validate_admitted_handle(value)
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.handle_id.digest_sha256,
        schema_id=value.schema_version.value,
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
    "DIMENSION_SOURCE",
    "DecisionCommitReceipt",
    "DimensionEvidence",
    "EvidenceOutcome",
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
    "build_gate_decision_chain",
    "canonical_subject_refs",
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
    "require_consumption_before_compilation",
    "require_decision_journal",
    "require_dimension_evidence",
    "derive_independence_proof",
    "require_entitled_chain",
    "require_entitled_decision",
    "require_role_for_gate",
    "require_gate_predecessor",
    "required_dimensions",
    "resolve_decision_kind",
    "validate_admitted_handle",
    "validate_authority_head_set",
    "validate_commit_receipt",
    "validate_gate_decision",
]


def require_entitled_decision(
    decision: GateDecision,
    *,
    declaration: GateEvaluatorDeclaration,
    proof: GateIndependenceProof,
    source_actors: tuple[ActorIdentity, ...],
) -> AuthorityRole:
    """Re-establish, from the consumer's own copies, that the evaluator could decide this.

    The decision names three digests; it cannot prove them, because a record
    proving its own entitlement is exactly the self-approval NR-08 forbids. So
    the verifier brings its own declaration and proof, checks that the decision
    names those and not others, and re-runs the independence computation against
    the actor set actually in play.

    What this rules out is the substitution the digests exist to catch: a
    self-consistent decision naming an independent-looking authority, restored
    under a matching reference. The reference proves the bytes were not edited;
    only this proves the evaluator was entitled to write them.
    """

    validate_gate_decision(decision)
    validate_gate_evaluator_declaration(declaration)
    validate_gate_independence_proof(proof)
    if decision.configuration_digest != declaration.configuration_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "the decision was made under another authority configuration",
        )
    if decision.evaluator_declaration_digest != declaration_digest(declaration):
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "the decision names another evaluator declaration",
        )
    if decision.independence_proof_digest != proof.proof_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "the decision names another independence proof",
        )
    if decision.authority_identity != declaration.evaluator_identity:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT,
            "the decision authority is not the declared evaluator",
        )
    role = require_independent_evaluator(
        proof, declaration=declaration, gate=decision.gate_kind, source_actors=source_actors
    )
    if role is not decision.authority_role:
        raise _fail(
            AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED,
            "the decision was signed with a role the evaluator does not hold at this gate",
        )
    return role
