"""Who is allowed to decide a §22 gate, and the proof that they are independent.

§22 requires an independence proof to be part of a decision's identity and to be
re-checkable by the consumer. An earlier revision of the gates carried only an
``authority_identity`` and an ``authority_role`` on each decision, which is not
that: both are strings the decision states about itself. Once such a decision
had been serialised and restored, nothing in it could say which configuration
authorised the evaluator, which component version produced the verdict, or that
the evaluator was not the producer of the very subject it admitted. A
self-consistent record naming an independent-looking authority passed
restoration unchallenged, because a digest proves bytes, not entitlement.

Two records close that.

``GateEvaluatorDeclaration`` is the registration: an evaluator identity, its
component id and version, the roles it holds at each gate, and the authority
configuration it was declared under. An identity that is not declared cannot
evaluate, so holding the right ``AuthorityRole`` enum value is no longer enough.

``GateIndependenceProof`` is the per-decision claim, and it is machine-checkable
rather than asserted: it names the exact actor set the decision was about and
the reason the evaluator stands apart from it, and it recomputes to the same
identity only if that separation actually holds. NR-08's rule — producer,
source, worker, planner, retriever, indexer, publisher and executor do not raise
the authority of their own output — becomes something a consumer verifies
instead of something a record claims.

This module is an adapter of the common contracts owner. It depends on
``contracts`` and ``canonicalization`` only: the gates import it, it imports no
gate. That direction is what lets an evaluator's entitlement be established
before, and independently of, any decision that relies on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Callable

from .canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from .contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    ContractViolation,
    GateKind,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    Stage4AuthorityHandle,
    compute_record_id,
    require_stage4_authority_handle,
    validate_record_id,
)

UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
_IDENTIFIER_MAX = 200

_DECLARATION_SEAL = object()
_PROOF_SEAL = object()


class AuthorityConfigFailureCode(str, Enum):
    """Closed, fail-closed vocabulary for evaluator entitlement failures."""

    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    EVALUATOR_NOT_DECLARED = "EVALUATOR_NOT_DECLARED"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    ROLE_NOT_DECLARED = "ROLE_NOT_DECLARED"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    COMPONENT_MISMATCH = "COMPONENT_MISMATCH"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PROOF_SUBJECT_MISMATCH = "PROOF_SUBJECT_MISMATCH"


class AuthorityConfigViolation(ValueError):
    """A typed, fail-closed entitlement error carrying no subject payload."""

    def __init__(self, failure_code: AuthorityConfigFailureCode, detail: str) -> None:
        if type(failure_code) is not AuthorityConfigFailureCode:
            raise TypeError("failure_code must be an exact AuthorityConfigFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: AuthorityConfigFailureCode, detail: str) -> AuthorityConfigViolation:
    return AuthorityConfigViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value, profile_id=STAGE4_CANONICAL_PROFILE_V1, codec_id=STABLE_CANONICAL_CODEC_ID
    )


def _identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(AuthorityConfigFailureCode.MALFORMED_IDENTIFIER, f"{field_name} is invalid")
    if value.strip() != value:
        raise _fail(AuthorityConfigFailureCode.MALFORMED_IDENTIFIER, f"{field_name} has padding")
    return value


def _timestamp(value: object, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is not timezone.utc:
        raise _fail(AuthorityConfigFailureCode.MALFORMED_TIMESTAMP, f"{field_name} must be exact UTC")
    return value


def _authority(value: object, field_name: str) -> AuthorityIdentity:
    if type(value) is not AuthorityIdentity:
        raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, f"{field_name} must be an exact AuthorityIdentity")
    _identifier(value.value, field_name)
    return value


def configured_authority_values(handle: Stage4AuthorityHandle) -> frozenset[str]:
    """Every identity the authority configuration already assigns a role to.

    An evaluator drawn from this set would be reviewing work it is party to, so
    it is the set a gate evaluator must stand outside of.
    """

    configuration = require_stage4_authority_handle(handle)
    values = {
        configuration.platform_attester_actor.value,
        configuration.builder_actor.value,
        configuration.taint_classifier_authority.value,
        configuration.taint_reviewer_authority.value,
        configuration.supersession_reviewer_authority.value,
        configuration.revocation_reviewer_authority.value,
        configuration.lifecycle_writer_actor.value,
    }
    if configuration.governing_human_authority is not None:
        values.add(configuration.governing_human_authority.value)
    return frozenset(values)


# ---------------------------------------------------------------------------
# GateEvaluatorDeclaration — an identity is not an entitlement
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class GateEvaluatorDeclaration:
    """The registration that makes an identity able to decide a gate at all.

    Holding an ``AuthorityRole`` enum value is a claim; being declared is a
    fact about the configuration. This record carries both, plus the component
    identity and version that will produce the verdicts, so a decision can name
    the exact software and configuration it came from rather than only a string.
    """

    schema_version: SchemaVersion
    declaration_id: RecordId
    configuration_id: RecordId
    evaluator_identity: AuthorityIdentity
    evaluator_component_id: str
    evaluator_component_version: str
    gate_roles: tuple[tuple[str, str], ...]
    policy_version: str
    created_at_utc: datetime
    _authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> GateEvaluatorDeclaration:
        raise TypeError("GateEvaluatorDeclaration is produced only by its factory")

    def role_for(self, gate: GateKind) -> AuthorityRole:
        """The role this evaluator was declared to hold at ``gate``."""

        validate_gate_evaluator_declaration(self)
        if type(gate) is not GateKind:
            raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, "gate must be an exact GateKind")
        for name, role in self.gate_roles:
            if name == gate.value:
                return AuthorityRole(role)
        raise _fail(
            AuthorityConfigFailureCode.ROLE_NOT_DECLARED,
            f"the evaluator holds no declared role at the {gate.value} gate",
        )

    def to_dict(self) -> dict[str, object]:
        validate_gate_evaluator_declaration(self)
        return _declaration_payload(self) | {"declaration_id": self.declaration_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_gate_evaluator_declaration(self)
        return _canonical(_declaration_payload(self))


def _declaration_payload(value: GateEvaluatorDeclaration) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "evaluator_identity": value.evaluator_identity.to_dict(),
        "evaluator_component_id": value.evaluator_component_id,
        "evaluator_component_version": value.evaluator_component_version,
        "gate_roles": [list(item) for item in value.gate_roles],
        "policy_version": value.policy_version,
        "created_at_utc": value.created_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_gate_evaluator_declaration(value: GateEvaluatorDeclaration) -> None:
    if type(value) is not GateEvaluatorDeclaration or getattr(value, "_trusted_seal", None) is not _DECLARATION_SEAL:
        raise _fail(AuthorityConfigFailureCode.TRUSTED_OBJECT_FORGED, "declaration is not factory sealed")
    if value.schema_version is not SchemaVersion.GATE_EVALUATOR_DECLARATION_V1:
        raise _fail(AuthorityConfigFailureCode.UNKNOWN_SCHEMA_VERSION, "declaration schema is unknown")
    _authority(value.evaluator_identity, "evaluator_identity")
    _identifier(value.evaluator_component_id, "evaluator_component_id")
    _identifier(value.evaluator_component_version, "evaluator_component_version")
    _identifier(value.policy_version, "policy_version")
    _timestamp(value.created_at_utc, "created_at_utc")
    if type(value.gate_roles) is not tuple or not value.gate_roles:
        raise _fail(AuthorityConfigFailureCode.ROLE_NOT_DECLARED, "a declaration must hold at least one gate role")
    seen: set[str] = set()
    for item in value.gate_roles:
        if type(item) is not tuple or len(item) != 2:
            raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, "gate role entries must be exact pairs")
        name, role = item
        if name in seen:
            raise _fail(AuthorityConfigFailureCode.ROLE_NOT_DECLARED, "a gate is declared twice")
        seen.add(name)
        try:
            GateKind(name)
            AuthorityRole(role)
        except ValueError as exc:
            raise _fail(AuthorityConfigFailureCode.ROLE_NOT_DECLARED, "gate or role is outside the vocabulary") from exc
    if list(value.gate_roles) != sorted(value.gate_roles):
        raise _fail(AuthorityConfigFailureCode.ROLE_NOT_DECLARED, "gate roles must be canonically ordered")
    configuration = require_stage4_authority_handle(value._authority_handle)
    if configuration.configuration_id != value.configuration_id:
        raise _fail(
            AuthorityConfigFailureCode.CONFIGURATION_MISMATCH,
            "declaration names another authority configuration",
        )
    if value.evaluator_identity.value in configured_authority_values(value._authority_handle):
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the evaluator already holds a role in this authority configuration",
        )
    try:
        validate_record_id(value.declaration_id, canonical_bytes=_canonical(_declaration_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            AuthorityConfigFailureCode.IDENTITY_MISMATCH,
            "declaration_id does not match its payload",
        ) from exc


def create_gate_evaluator_declaration(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator_identity: AuthorityIdentity,
    evaluator_component_id: str,
    evaluator_component_version: str,
    gate_roles: dict[GateKind, AuthorityRole],
    policy_version: str,
    trusted_clock: Callable[[], datetime],
) -> GateEvaluatorDeclaration:
    """Register an evaluator, or refuse.

    The independence check happens here rather than at decision time because it
    is a property of the configuration, not of any one verdict: an evaluator
    that already attests, builds, classifies taint, reviews supersession or
    writes lifecycle in this configuration would be reviewing work it is party
    to, whichever gate it later sat at.
    """

    configuration = require_stage4_authority_handle(authority_handle)
    evaluator = _authority(evaluator_identity, "evaluator_identity")
    if evaluator.value in configured_authority_values(authority_handle):
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the evaluator already holds a role in this authority configuration",
        )
    if not isinstance(gate_roles, dict) or not gate_roles:
        raise _fail(AuthorityConfigFailureCode.ROLE_NOT_DECLARED, "at least one gate role must be declared")
    pairs: list[tuple[str, str]] = []
    for gate, role in gate_roles.items():
        if type(gate) is not GateKind or type(role) is not AuthorityRole:
            raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, "gate roles must be exact enum members")
        pairs.append((gate.value, role.value))
    if not callable(trusted_clock):
        raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")

    result = object.__new__(GateEvaluatorDeclaration)
    object.__setattr__(result, "schema_version", SchemaVersion.GATE_EVALUATOR_DECLARATION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    object.__setattr__(result, "evaluator_identity", evaluator)
    object.__setattr__(result, "evaluator_component_id", _identifier(evaluator_component_id, "evaluator_component_id"))
    object.__setattr__(
        result, "evaluator_component_version", _identifier(evaluator_component_version, "evaluator_component_version")
    )
    object.__setattr__(result, "gate_roles", tuple(sorted(pairs)))
    object.__setattr__(result, "policy_version", _identifier(policy_version, "policy_version"))
    object.__setattr__(result, "created_at_utc", _timestamp(trusted_clock(), "created_at_utc"))
    object.__setattr__(result, "_authority_handle", authority_handle)
    object.__setattr__(result, "_trusted_seal", _DECLARATION_SEAL)
    object.__setattr__(
        result,
        "declaration_id",
        compute_record_id(
            domain=IdentityDomain.GATE_EVALUATOR_DECLARATION,
            canonical_bytes=_canonical(_declaration_payload(result)),
        ),
    )
    validate_gate_evaluator_declaration(result)
    return result


# ---------------------------------------------------------------------------
# GateIndependenceProof — checkable, not asserted
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class GateIndependenceProof:
    """Evidence that the evaluator is not a party to what it decided.

    The proof names the exact actor set the decision concerned — producer,
    retriever, consumer — and recomputes to this identity only if the evaluator
    is outside both that set and the configured authority set. A consumer
    re-runs the same computation from the stored fields, so the claim is
    verified rather than believed.
    """

    schema_version: SchemaVersion
    proof_id: RecordId
    declaration_id: RecordId
    configuration_id: RecordId
    evaluator_identity: AuthorityIdentity
    source_actor_ids: tuple[str, ...]
    independence_reason: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> GateIndependenceProof:
        raise TypeError("GateIndependenceProof is produced only by its factory")

    def to_dict(self) -> dict[str, object]:
        validate_gate_independence_proof(self)
        return _proof_payload(self) | {"proof_id": self.proof_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_gate_independence_proof(self)
        return _canonical(_proof_payload(self))


def _proof_payload(value: GateIndependenceProof) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "declaration_id": value.declaration_id.to_dict(),
        "configuration_id": value.configuration_id.to_dict(),
        "evaluator_identity": value.evaluator_identity.to_dict(),
        "source_actor_ids": list(value.source_actor_ids),
        "independence_reason": value.independence_reason,
    }


#: The one reason this implementation can currently establish. It is a closed
#: vocabulary rather than free text because §22 forbids prose authority: a
#: reason a machine cannot check is a reason a reviewer has to take on trust.
INDEPENDENCE_REASON_DISJOINT_ACTOR_SETS = "EVALUATOR_DISJOINT_FROM_SUBJECT_AND_CONFIGURED_ACTORS"

_INDEPENDENCE_REASONS = frozenset({INDEPENDENCE_REASON_DISJOINT_ACTOR_SETS})


def validate_gate_independence_proof(value: GateIndependenceProof) -> None:
    if type(value) is not GateIndependenceProof or getattr(value, "_trusted_seal", None) is not _PROOF_SEAL:
        raise _fail(AuthorityConfigFailureCode.TRUSTED_OBJECT_FORGED, "independence proof is not factory sealed")
    if value.schema_version is not SchemaVersion.GATE_INDEPENDENCE_PROOF_V1:
        raise _fail(AuthorityConfigFailureCode.UNKNOWN_SCHEMA_VERSION, "independence proof schema is unknown")
    _authority(value.evaluator_identity, "evaluator_identity")
    if value.independence_reason not in _INDEPENDENCE_REASONS:
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "independence reason is outside the closed vocabulary",
        )
    if type(value.source_actor_ids) is not tuple or not value.source_actor_ids:
        raise _fail(
            AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH,
            "a proof must name the actor set it stands apart from",
        )
    for item in value.source_actor_ids:
        _identifier(item, "source_actor_id")
    if list(value.source_actor_ids) != sorted(set(value.source_actor_ids)):
        raise _fail(
            AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH,
            "source actor ids must be sorted and unique",
        )
    if value.evaluator_identity.value in value.source_actor_ids:
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the evaluator is one of the actors it claims to stand apart from",
        )
    try:
        validate_record_id(value.proof_id, canonical_bytes=_canonical(_proof_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            AuthorityConfigFailureCode.IDENTITY_MISMATCH,
            "proof_id does not match its payload",
        ) from exc


def create_gate_independence_proof(
    *,
    declaration: GateEvaluatorDeclaration,
    source_actors: tuple[ActorIdentity, ...],
) -> GateIndependenceProof:
    """Establish independence for one decision's actor set, or refuse."""

    validate_gate_evaluator_declaration(declaration)
    if type(source_actors) is not tuple or not source_actors:
        raise _fail(
            AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH,
            "independence is established against a non-empty actor set",
        )
    values: list[str] = []
    for actor in source_actors:
        if type(actor) is not ActorIdentity:
            raise _fail(AuthorityConfigFailureCode.TYPE_MISMATCH, "source actors must be exact ActorIdentity")
        values.append(_identifier(actor.value, "source_actor_id"))
    if len(set(values)) != len(values):
        raise _fail(
            AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH,
            "the actor set contains a duplicate identity",
        )
    evaluator = declaration.evaluator_identity
    if evaluator.value in values:
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the evaluator is one of the actors it would be deciding about",
        )
    if evaluator.value in configured_authority_values(declaration._authority_handle):
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the evaluator already holds a role in this authority configuration",
        )

    result = object.__new__(GateIndependenceProof)
    object.__setattr__(result, "schema_version", SchemaVersion.GATE_INDEPENDENCE_PROOF_V1)
    object.__setattr__(result, "declaration_id", declaration.declaration_id)
    object.__setattr__(result, "configuration_id", declaration.configuration_id)
    object.__setattr__(result, "evaluator_identity", evaluator)
    object.__setattr__(result, "source_actor_ids", tuple(sorted(set(values))))
    object.__setattr__(result, "independence_reason", INDEPENDENCE_REASON_DISJOINT_ACTOR_SETS)
    object.__setattr__(result, "_trusted_seal", _PROOF_SEAL)
    object.__setattr__(
        result,
        "proof_id",
        compute_record_id(
            domain=IdentityDomain.GATE_INDEPENDENCE_PROOF,
            canonical_bytes=_canonical(_proof_payload(result)),
        ),
    )
    validate_gate_independence_proof(result)
    return result


def require_independent_evaluator(
    proof: GateIndependenceProof,
    *,
    declaration: GateEvaluatorDeclaration,
    gate: GateKind,
    source_actors: tuple[ActorIdentity, ...],
) -> AuthorityRole:
    """The consumer-side re-check, and the point of the whole module.

    A stored proof is only worth what a consumer can re-derive from it, so this
    recomputes the independence claim from the actor set actually in play now —
    not from the one the proof was written against — and refuses if the two
    differ. It then confirms the evaluator is the declared one and holds a
    declared role at this gate, and returns that role.
    """

    validate_gate_independence_proof(proof)
    validate_gate_evaluator_declaration(declaration)
    if proof.declaration_id != declaration.declaration_id:
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_DECLARED,
            "the proof was written for another evaluator declaration",
        )
    if proof.configuration_id != declaration.configuration_id:
        raise _fail(
            AuthorityConfigFailureCode.CONFIGURATION_MISMATCH,
            "the proof names another authority configuration",
        )
    if proof.evaluator_identity != declaration.evaluator_identity:
        raise _fail(
            AuthorityConfigFailureCode.EVALUATOR_NOT_DECLARED,
            "the proof names another evaluator identity",
        )
    recomputed = create_gate_independence_proof(declaration=declaration, source_actors=source_actors)
    if recomputed.proof_id != proof.proof_id:
        raise _fail(
            AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH,
            "the proof does not describe the actors this decision is about",
        )
    return declaration.role_for(gate)


def declaration_digest(value: GateEvaluatorDeclaration) -> str:
    """The digest a decision stores to name the declaration it rests on."""

    return hashlib.sha256(value.canonical_bytes()).hexdigest()


__all__ = [
    "INDEPENDENCE_REASON_DISJOINT_ACTOR_SETS",
    "AuthorityConfigFailureCode",
    "AuthorityConfigViolation",
    "GateEvaluatorDeclaration",
    "GateIndependenceProof",
    "configured_authority_values",
    "create_gate_evaluator_declaration",
    "create_gate_independence_proof",
    "declaration_digest",
    "require_independent_evaluator",
    "validate_gate_evaluator_declaration",
    "validate_gate_independence_proof",
]
