"""Stage 4 OD-10 — the authority that decides whether a recorded result may be used.

§22 governs the *knowledge* a replay runs over. It has no vocabulary for the
question this module answers, and that is not an oversight: "may this particular
recorded external result be injected into this particular replay" is a different
question with different inputs, and the four gate roles have no standing to
answer it. A gate decision is about a published library subject with a
descriptor, a manifest, an attestation and a lifecycle; a recorded activity has
none of those and never will.

So OD-10 gives the question its own authority, and the shape is the one Stage 4
uses everywhere an authority exists:

*A declaration* states who the evaluator is, which component and version it runs
as, under which policy version, and — this is the part that matters — **what the
policy says**. The disposition an activity receives is read off the declaration,
never off the call. A caller that could pass a disposition would be approving its
own result, which is the self-approval §2 forbids in as many words.

*An actor set* names every actor the decision concerns: the producer of the
result, whoever recorded it, the worker, the model, the replay executor, the CVM
adapter and the consumer. *An independence proof* is the machine-checkable
statement that the evaluator is none of them, recomputed by the consumer rather
than trusted from the producer's copy.

*A decision* binds everything that could change the answer — the activity kind,
the complete inputs, the exact result hash and the reference its bytes live
behind, the policy version, the consumer context, the committed boundary, the run
and attempt, the environment and capability profile, and the durable anchors of
the lifecycle and taint histories at the moment it was taken.

That last group is what makes the decision perishable in the right way. Revoke,
quarantine, taint escalation and supersession are all appends to those
histories, so any of them moves an anchor, and a consumer comparing the anchors
it reads now against the ones the decision recorded sees the change without
needing a vocabulary for each cause. Drift in policy, environment, tool or
configuration is caught the same way: by comparison, not by trust.

Two members of the vocabulary are refusals and stay refusals.
``FORBIDDEN_IN_REPLAY`` is answered before any effect is reached.
``REQUIRES_FRESH_AUTHORITY`` means a live call would be needed, which during a
replay is exactly what may not happen — it never ripens into consumable because
time passed or because a caller asked twice.

The module decides. It performs no effect, stores nothing and holds no bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Protocol, runtime_checkable
import hashlib
import json

from .activities import (
    _RECORDER_ENTITLEMENT_SEAL,
    ActivityDisposition,
    ActivityKind,
    ActivityRecorderEntitlement,
    RecordedActivity,
    issue_recorder_entitlement,
    validate_recorded_activity,
)
from .authority_config import (
    Stage4AuthorityHandle,
    require_stage4_authority_handle,
)
from .canonicalization import HashBoundRef, RefKind
from .contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    CommonEnvelope,
    ContractViolation,
    IdentityDomain,
    RecordId,
    RunId,
    SchemaVersion,
    common_envelope_from_dict,
    compute_envelope_binding_sha256,
    compute_record_id,
    create_common_envelope,
    envelope_bound_record_bytes,
    record_id_reference_from_dict,
    validate_envelope_bound_record,
    validate_record_id,
)

ACTIVITY_POLICY_PRODUCER_COMPONENT_V1 = "synapse.stage4.gold.activity-policy.v1"

#: The independence statement this module can actually prove: the evaluator
#: identity appears in none of the actor roles the decision concerns.
INDEPENDENCE_REASON_DISJOINT_ACTIVITY_ACTORS = "EVALUATOR_DISJOINT_FROM_ACTIVITY_ACTORS"

_DECLARATION_SEAL = object()
_ACTOR_SET_SEAL = object()
_PRODUCTION_PROVENANCE_SEAL = object()
_CONSUMPTION_PROVENANCE_SEAL = object()
_PROOF_SEAL = object()
_DECISION_SEAL = object()
_EVALUATOR_SEAL = object()

_IDENTIFIER_MAX = 128
_SHA256_LENGTH = 64
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

#: Every actor an activity decision concerns. The list is exhaustive on purpose:
#: an evaluator that merely differed from the *producer* would still be allowed
#: to be the worker that asked for the call, or the executor that will consume
#: its result, and either of those is self-approval wearing another name.
#: The four roles that exist when an effect is recorded, and the three that
#: exist when a replay serves it. The union is the seven §9.4 names, split at
#: the point in time where half of them do not exist yet.
_PRODUCTION_ACTOR_FIELDS = (
    "producer_actor",
    "recorder_actor",
    "worker_actor",
    "model_actor",
)
_CONSUMPTION_ACTOR_FIELDS = (
    "replay_executor_actor",
    "machine_adapter_actor",
    "consumer_actor",
)
_ACTOR_FIELDS = (
    "producer_actor",
    "recorder_actor",
    "worker_actor",
    "model_actor",
    "replay_executor_actor",
    "machine_adapter_actor",
    "consumer_actor",
)


class ActivityPolicyFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA_VERSION = "UNKNOWN_SCHEMA_VERSION"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    MALFORMED_SHA256 = "MALFORMED_SHA256"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    TRUSTED_OBJECT_FORGED = "TRUSTED_OBJECT_FORGED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    EVALUATOR_ROLE_INVALID = "EVALUATOR_ROLE_INVALID"
    EVALUATOR_NOT_INDEPENDENT = "EVALUATOR_NOT_INDEPENDENT"
    EVALUATOR_NOT_DECLARED = "EVALUATOR_NOT_DECLARED"
    POLICY_INCOMPLETE = "POLICY_INCOMPLETE"
    ACTOR_SET_MISMATCH = "ACTOR_SET_MISMATCH"
    DECISION_SUBJECT_MISMATCH = "DECISION_SUBJECT_MISMATCH"
    DECISION_CONTEXT_MISMATCH = "DECISION_CONTEXT_MISMATCH"
    DECISION_STATE_DRIFTED = "DECISION_STATE_DRIFTED"
    NOT_CONSUMABLE = "NOT_CONSUMABLE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"


class ActivityPolicyViolation(ValueError):
    """A typed, fail-closed activity-policy error carrying no payload."""

    def __init__(self, failure_code: ActivityPolicyFailureCode, detail: str) -> None:
        if type(failure_code) is not ActivityPolicyFailureCode:
            raise TypeError("failure_code must be an exact ActivityPolicyFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a non-empty safe string up to 256 characters")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ActivityPolicyFailureCode, detail: str) -> ActivityPolicyViolation:
    return ActivityPolicyViolation(code, detail)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > _IDENTIFIER_MAX:
        raise _fail(ActivityPolicyFailureCode.MALFORMED_IDENTIFIER, f"{field} is not a safe identifier")
    return value


def _sha256(value: object, field: str) -> str:
    if type(value) is not str or len(value) != _SHA256_LENGTH:
        raise _fail(ActivityPolicyFailureCode.MALFORMED_SHA256, f"{field} is not a sha256 digest")
    int(value, 16)
    return value.lower()


def _timestamp(value: object, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise _fail(ActivityPolicyFailureCode.MALFORMED_TIMESTAMP, f"{field} must be aware UTC")
    return value


def _ref(value: object, field: str) -> HashBoundRef:
    if type(value) is not HashBoundRef:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{field} must be an exact HashBoundRef")
    return value


@runtime_checkable
class AuthorityAnchorPort(Protocol):
    """The one question this module asks a durable history: where is its head."""

    def current_anchor(self) -> object: ...


# ---------------------------------------------------------------------------
# Declaration — who the evaluator is and what its policy says
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class ActivityPolicyDeclaration:
    """The evaluator, and the closed mapping its policy assigns to each kind.

    The mapping lives here rather than at the call site because it *is* the
    policy. A declaration that named an evaluator but left the answer to whoever
    asked would be an authority in name only: the decision would still be made
    by the party holding the result.
    """

    schema_version: SchemaVersion
    declaration_id: RecordId
    configuration_id: RecordId
    evaluator_identity: AuthorityIdentity
    evaluator_component_id: str
    evaluator_component_version: str
    policy_version: str
    authority_role: AuthorityRole
    #: One disposition per activity kind, total over ``ActivityKind``.
    dispositions: tuple[tuple[str, str], ...]
    created_at_utc: datetime
    _authority_handle: Stage4AuthorityHandle
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityPolicyDeclaration:
        raise TypeError("ActivityPolicyDeclaration is produced only by its factory")

    def disposition_for(self, kind: ActivityKind) -> ActivityDisposition:
        validate_activity_policy_declaration(self)
        if type(kind) is not ActivityKind:
            raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "activity kind must be exact")
        for name, disposition in self.dispositions:
            if name == kind.value:
                return ActivityDisposition(disposition)
        raise _fail(
            ActivityPolicyFailureCode.POLICY_INCOMPLETE,
            "the declared policy assigns no disposition to this activity kind",
        )

    def to_dict(self) -> dict[str, object]:
        validate_activity_policy_declaration(self)
        return _declaration_payload(self) | {"declaration_id": self.declaration_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_activity_policy_declaration(self)
        return _canonical(_declaration_payload(self))


def _declaration_payload(value: ActivityPolicyDeclaration) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "evaluator_identity": value.evaluator_identity.value,
        "evaluator_component_id": value.evaluator_component_id,
        "evaluator_component_version": value.evaluator_component_version,
        "policy_version": value.policy_version,
        "authority_role": value.authority_role.value,
        "dispositions": [list(item) for item in value.dispositions],
        "created_at_utc": value.created_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_activity_policy_declaration(value: ActivityPolicyDeclaration) -> None:
    if (
        type(value) is not ActivityPolicyDeclaration
        or getattr(value, "_trusted_seal", None) is not _DECLARATION_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity policy declaration is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_POLICY_DECLARATION_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity policy declaration schema is unknown",
        )
    if value.authority_role is not AuthorityRole.ACTIVITY_POLICY_EVALUATOR:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_ROLE_INVALID,
            "an activity policy decision requires the activity policy evaluator role",
        )
    _identifier(value.evaluator_component_id, "evaluator_component_id")
    _identifier(value.evaluator_component_version, "evaluator_component_version")
    _identifier(value.policy_version, "policy_version")
    _timestamp(value.created_at_utc, "created_at_utc")
    declared = {name for name, _ in value.dispositions}
    if declared != {item.value for item in ActivityKind}:
        raise _fail(
            ActivityPolicyFailureCode.POLICY_INCOMPLETE,
            "the declared policy is not total over the activity kind vocabulary",
        )
    if len(declared) != len(value.dispositions):
        raise _fail(
            ActivityPolicyFailureCode.POLICY_INCOMPLETE,
            "the declared policy assigns one kind twice",
        )
    for _name, disposition in value.dispositions:
        if disposition not in {item.value for item in ActivityDisposition}:
            raise _fail(
                ActivityPolicyFailureCode.TYPE_MISMATCH,
                "the declared policy names a disposition outside the closed vocabulary",
            )
    try:
        validate_record_id(
            value.declaration_id, canonical_bytes=_canonical(_declaration_payload(value))
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "declaration_id does not match its payload",
        ) from exc


def create_activity_policy_declaration(
    *,
    authority_handle: Stage4AuthorityHandle,
    evaluator_identity: AuthorityIdentity,
    evaluator_component_id: str,
    evaluator_component_version: str,
    policy_version: str,
    dispositions: dict[ActivityKind, ActivityDisposition],
    trusted_clock: Callable[[], datetime],
) -> ActivityPolicyDeclaration:
    """Declare the evaluator and freeze the mapping it will apply."""

    configuration = require_stage4_authority_handle(authority_handle)
    if not callable(trusted_clock):
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    if type(dispositions) is not dict or not dispositions:
        raise _fail(
            ActivityPolicyFailureCode.POLICY_INCOMPLETE,
            "an activity policy must declare its dispositions",
        )
    entries: list[tuple[str, str]] = []
    for kind, disposition in dispositions.items():
        if type(kind) is not ActivityKind or type(disposition) is not ActivityDisposition:
            raise _fail(
                ActivityPolicyFailureCode.TYPE_MISMATCH,
                "an activity policy maps exact kinds to exact dispositions",
            )
        entries.append((kind.value, disposition.value))
    result = object.__new__(ActivityPolicyDeclaration)
    object.__setattr__(result, "schema_version", SchemaVersion.ACTIVITY_POLICY_DECLARATION_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    if type(evaluator_identity) is not AuthorityIdentity:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "evaluator_identity must be exact")
    object.__setattr__(result, "evaluator_identity", evaluator_identity)
    object.__setattr__(result, "evaluator_component_id", _identifier(evaluator_component_id, "evaluator_component_id"))
    object.__setattr__(
        result, "evaluator_component_version", _identifier(evaluator_component_version, "evaluator_component_version")
    )
    object.__setattr__(result, "policy_version", _identifier(policy_version, "policy_version"))
    object.__setattr__(result, "authority_role", AuthorityRole.ACTIVITY_POLICY_EVALUATOR)
    object.__setattr__(result, "dispositions", tuple(sorted(entries)))
    object.__setattr__(result, "created_at_utc", _timestamp(trusted_clock(), "created_at_utc"))
    object.__setattr__(result, "_authority_handle", authority_handle)
    object.__setattr__(result, "_trusted_seal", _DECLARATION_SEAL)
    object.__setattr__(
        result,
        "declaration_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_POLICY_DECLARATION,
            canonical_bytes=_canonical(_declaration_payload(result)),
        ),
    )
    validate_activity_policy_declaration(result)
    return result


# ---------------------------------------------------------------------------
# Actor set and independence proof
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class ActivityPolicyActorSet:
    """Every actor an activity decision concerns, bound to one configuration."""

    schema_version: SchemaVersion
    actor_set_id: RecordId
    configuration_id: RecordId
    producer_actor: ActorIdentity
    recorder_actor: ActorIdentity
    worker_actor: ActorIdentity
    model_actor: ActorIdentity
    replay_executor_actor: ActorIdentity
    machine_adapter_actor: ActorIdentity
    consumer_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityPolicyActorSet:
        raise TypeError("ActivityPolicyActorSet is produced only by its factory")

    def actors(self) -> tuple[ActorIdentity, ...]:
        validate_activity_policy_actor_set(self)
        return tuple(getattr(self, name) for name in _ACTOR_FIELDS)

    def to_dict(self) -> dict[str, object]:
        validate_activity_policy_actor_set(self)
        return _actor_set_payload(self) | {"actor_set_id": self.actor_set_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_activity_policy_actor_set(self)
        return _canonical(_actor_set_payload(self))


def _actor_set_payload(value: ActivityPolicyActorSet) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        **{name: getattr(value, name).value for name in _ACTOR_FIELDS},
    }


def validate_activity_policy_actor_set(value: ActivityPolicyActorSet) -> None:
    if (
        type(value) is not ActivityPolicyActorSet
        or getattr(value, "_trusted_seal", None) is not _ACTOR_SET_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity policy actor set is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_POLICY_ACTOR_SET_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity policy actor set schema is unknown",
        )
    for name in _ACTOR_FIELDS:
        if type(getattr(value, name)) is not ActorIdentity:
            raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    try:
        validate_record_id(
            value.actor_set_id, canonical_bytes=_canonical(_actor_set_payload(value))
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "actor_set_id does not match its payload",
        ) from exc


def create_activity_policy_actor_set(
    *,
    authority_handle: Stage4AuthorityHandle,
    producer_actor: ActorIdentity,
    recorder_actor: ActorIdentity,
    worker_actor: ActorIdentity,
    model_actor: ActorIdentity,
    replay_executor_actor: ActorIdentity,
    machine_adapter_actor: ActorIdentity,
    consumer_actor: ActorIdentity,
) -> ActivityPolicyActorSet:
    configuration = require_stage4_authority_handle(authority_handle)
    result = object.__new__(ActivityPolicyActorSet)
    object.__setattr__(result, "schema_version", SchemaVersion.ACTIVITY_POLICY_ACTOR_SET_V1)
    object.__setattr__(result, "configuration_id", configuration.configuration_id)
    supplied = {
        "producer_actor": producer_actor,
        "recorder_actor": recorder_actor,
        "worker_actor": worker_actor,
        "model_actor": model_actor,
        "replay_executor_actor": replay_executor_actor,
        "machine_adapter_actor": machine_adapter_actor,
        "consumer_actor": consumer_actor,
    }
    for name, actor in supplied.items():
        object.__setattr__(result, name, actor)
    object.__setattr__(result, "_trusted_seal", _ACTOR_SET_SEAL)
    object.__setattr__(
        result,
        "actor_set_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_POLICY_ACTOR_SET,
            canonical_bytes=_canonical(_actor_set_payload(result)),
        ),
    )
    validate_activity_policy_actor_set(result)
    return result


@dataclass(frozen=True, init=False)
class ActivityPolicyIndependenceProof:
    """The evaluator is none of the actors this decision concerns."""

    schema_version: SchemaVersion
    proof_id: RecordId
    declaration_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    evaluator_identity: AuthorityIdentity
    independence_reason: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityPolicyIndependenceProof:
        raise TypeError("ActivityPolicyIndependenceProof is produced only by its factory")

    def to_dict(self) -> dict[str, object]:
        validate_activity_policy_independence_proof(self)
        return _proof_payload(self) | {"proof_id": self.proof_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_activity_policy_independence_proof(self)
        return _canonical(_proof_payload(self))


def _proof_payload(value: ActivityPolicyIndependenceProof) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "declaration_id": value.declaration_id.to_dict(),
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        "evaluator_identity": value.evaluator_identity.value,
        "independence_reason": value.independence_reason,
    }


def validate_activity_policy_independence_proof(
    value: ActivityPolicyIndependenceProof,
) -> None:
    if (
        type(value) is not ActivityPolicyIndependenceProof
        or getattr(value, "_trusted_seal", None) is not _PROOF_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity policy independence proof is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_POLICY_INDEPENDENCE_PROOF_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity policy independence proof schema is unknown",
        )
    if value.independence_reason != INDEPENDENCE_REASON_DISJOINT_ACTIVITY_ACTORS:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the independence reason is not the one this module can prove",
        )
    try:
        validate_record_id(value.proof_id, canonical_bytes=_canonical(_proof_payload(value)))
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "proof_id does not match its payload",
        ) from exc


def create_activity_policy_independence_proof(
    *,
    declaration: ActivityPolicyDeclaration,
    actor_set: ActivityPolicyActorSet,
) -> ActivityPolicyIndependenceProof:
    validate_activity_policy_declaration(declaration)
    validate_activity_policy_actor_set(actor_set)
    if declaration.configuration_id != actor_set.configuration_id:
        raise _fail(
            ActivityPolicyFailureCode.CONFIGURATION_MISMATCH,
            "declaration and actor set use different configurations",
        )
    if declaration.evaluator_identity.value in {item.value for item in actor_set.actors()}:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the activity policy evaluator collides with an actor it decides about",
        )
    result = object.__new__(ActivityPolicyIndependenceProof)
    object.__setattr__(result, "schema_version", SchemaVersion.ACTIVITY_POLICY_INDEPENDENCE_PROOF_V1)
    object.__setattr__(result, "declaration_id", declaration.declaration_id)
    object.__setattr__(result, "configuration_id", declaration.configuration_id)
    object.__setattr__(result, "actor_set_id", actor_set.actor_set_id)
    object.__setattr__(result, "evaluator_identity", declaration.evaluator_identity)
    object.__setattr__(result, "independence_reason", INDEPENDENCE_REASON_DISJOINT_ACTIVITY_ACTORS)
    object.__setattr__(result, "_trusted_seal", _PROOF_SEAL)
    object.__setattr__(
        result,
        "proof_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_POLICY_INDEPENDENCE_PROOF,
            canonical_bytes=_canonical(_proof_payload(result)),
        ),
    )
    validate_activity_policy_independence_proof(result)
    return result


def require_activity_policy_entitlement(
    proof: ActivityPolicyIndependenceProof,
    *,
    declaration: ActivityPolicyDeclaration,
    actor_set: ActivityPolicyActorSet,
) -> None:
    """Recompute the separation from the consumer's own copies.

    The proof travels with the decision, and a proof checked only by the party
    that made it proves nothing to anybody else. So the consumer recomputes it:
    the declaration it holds, the actor set it holds, and the collision test
    itself — not the proof's word that the test once passed.
    """

    validate_activity_policy_declaration(declaration)
    validate_activity_policy_actor_set(actor_set)
    validate_activity_policy_independence_proof(proof)
    if (
        proof.declaration_id != declaration.declaration_id
        or proof.evaluator_identity != declaration.evaluator_identity
    ):
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_DECLARED,
            "the proof names another declaration",
        )
    if (
        proof.configuration_id != declaration.configuration_id
        or proof.configuration_id != actor_set.configuration_id
    ):
        raise _fail(
            ActivityPolicyFailureCode.CONFIGURATION_MISMATCH,
            "the entitlement mixes configurations",
        )
    if proof.actor_set_id != actor_set.actor_set_id:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_DECLARED,
            "the proof names another actor set",
        )
    if declaration.evaluator_identity.value in {item.value for item in actor_set.actors()}:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the activity policy evaluator collides with an actor it decides about",
        )


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, init=False)
class ActivityPolicyDecision:
    """One evaluator answer about one recorded result in one execution context."""

    schema_version: SchemaVersion
    envelope: CommonEnvelope
    envelope_binding_sha256: str
    decision_id: RecordId
    disposition: ActivityDisposition
    activity_kind: ActivityKind
    activity_lookup_key: str
    activity_identity: str
    inputs_digest: str
    result_sha256: str
    result_ref: HashBoundRef
    #: The two phases of §9.4 provenance this decision was made against. Stored
    #: on the decision because the decision is the first moment both exist.
    production_provenance_ref: HashBoundRef
    consumption_provenance_ref: HashBoundRef
    activity_policy_version: str
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    run_id: RunId
    attempt_id: AttemptId
    environment_profile_id: str
    capability_profile_digest: str
    declaration_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    proof_id: RecordId
    lifecycle_anchor_sha256: str
    taint_anchor_sha256: str
    decided_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityPolicyDecision:
        raise TypeError("ActivityPolicyDecision is produced only by evaluate_activity_policy")

    def to_dict(self) -> dict[str, object]:
        validate_activity_policy_decision(self)
        return {
            "envelope": self.envelope.to_dict(),
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "payload": _decision_payload(self),
        }

    def canonical_bytes(self) -> bytes:
        validate_activity_policy_decision(self)
        return envelope_bound_record_bytes(
            envelope=self.envelope,
            envelope_binding_sha256=self.envelope_binding_sha256,
            domain_payload=_decision_payload(self),
        )


def _decision_payload(value: ActivityPolicyDecision) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "disposition": value.disposition.value,
        "activity_kind": value.activity_kind.value,
        "activity_lookup_key": value.activity_lookup_key,
        "activity_identity": value.activity_identity,
        "inputs_digest": value.inputs_digest,
        "result_sha256": value.result_sha256,
        "result_ref": value.result_ref.to_dict(),
        "production_provenance_ref": value.production_provenance_ref.to_dict(),
        "consumption_provenance_ref": value.consumption_provenance_ref.to_dict(),
        "activity_policy_version": value.activity_policy_version,
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "run_id": value.run_id.to_dict(),
        "attempt_id": value.attempt_id.to_dict(),
        "environment_profile_id": value.environment_profile_id,
        "capability_profile_digest": value.capability_profile_digest,
        "declaration_id": value.declaration_id.to_dict(),
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        "proof_id": value.proof_id.to_dict(),
        "lifecycle_anchor_sha256": value.lifecycle_anchor_sha256,
        "taint_anchor_sha256": value.taint_anchor_sha256,
        "decided_at_utc": value.decided_at_utc.strftime(UTC_TIMESTAMP_FORMAT),
    }


def validate_activity_policy_decision(value: ActivityPolicyDecision) -> None:
    if (
        type(value) is not ActivityPolicyDecision
        or getattr(value, "_trusted_seal", None) is not _DECISION_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity policy decision is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_POLICY_DECISION_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity policy decision schema is unknown",
        )
    if type(value.disposition) is not ActivityDisposition or type(value.activity_kind) is not ActivityKind:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "decision enums are invalid")
    for field in ("activity_lookup_key", "activity_identity", "inputs_digest", "result_sha256",
                  "capability_profile_digest", "lifecycle_anchor_sha256", "taint_anchor_sha256"):
        _sha256(getattr(value, field), field)
    _identifier(value.activity_policy_version, "activity_policy_version")
    _identifier(value.environment_profile_id, "environment_profile_id")
    for field in (
        "result_ref",
        "production_provenance_ref",
        "consumption_provenance_ref",
        "consumer_context_ref",
        "boundary_ref",
    ):
        _ref(getattr(value, field), field)
    _timestamp(value.decided_at_utc, "decided_at_utc")
    try:
        payload = _canonical(_decision_payload(value))
        validate_envelope_bound_record(
            envelope=value.envelope,
            envelope_binding_sha256=value.envelope_binding_sha256,
            canonical_domain_payload_bytes=payload,
            expected_identity_domain=IdentityDomain.ACTIVITY_POLICY_DECISION,
            expected_run_id=value.run_id,
            expected_attempt_id=value.attempt_id,
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "decision envelope does not match its payload",
        ) from exc
    if value.decision_id != value.envelope.record_id:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "decision_id is not the envelope record identity",
        )
    if (
        value.envelope.created_at_utc != value.decided_at_utc
        or value.envelope.policy_version != value.activity_policy_version
        or value.envelope.environment_profile_id != value.environment_profile_id
        or value.envelope.producer_component != ACTIVITY_POLICY_PRODUCER_COMPONENT_V1
    ):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH,
            "decision envelope context does not match its payload",
        )


class ConfiguredActivityPolicyEvaluator:
    """The evaluator, holding what it needs to answer and nothing that answers for it."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.pop("_seal", None) is not _EVALUATOR_SEAL or kwargs or len(args) != 6:
            raise TypeError(
                "ConfiguredActivityPolicyEvaluator is created only by configure_activity_policy_evaluator"
            )
        (
            self._declaration,
            self._actor_set,
            self._proof,
            self._lifecycle_store,
            self._taint_store,
            self._trusted_clock,
        ) = args
        self._trusted_seal = _EVALUATOR_SEAL

    @property
    def declaration(self) -> ActivityPolicyDeclaration:
        return self._declaration

    @property
    def actor_set(self) -> ActivityPolicyActorSet:
        return self._actor_set

    @property
    def independence_proof(self) -> ActivityPolicyIndependenceProof:
        return self._proof


def require_activity_policy_evaluator(
    value: object,
) -> ConfiguredActivityPolicyEvaluator:
    if (
        type(value) is not ConfiguredActivityPolicyEvaluator
        or getattr(value, "_trusted_seal", None) is not _EVALUATOR_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity policy evaluator is not factory configured",
        )
    require_activity_policy_entitlement(
        value._proof, declaration=value._declaration, actor_set=value._actor_set
    )
    return value


def require_activity_policy_execution_entitlement(
    evaluator: ConfiguredActivityPolicyEvaluator,
    *,
    executor_actor: ActorIdentity,
    production: ActivityProductionProvenance | None = None,
    consumption: ActivityConsumptionProvenance | None = None,
) -> None:
    """Prove every actor a decision concerns is declared and independent.

    The first revision of this checked one actor — the replay executor — because
    that is the one the executing party supplies. The other six were compared
    only against the actor set that declared them, which is a set comparing
    itself: a configuration naming seven strangers proved nothing about who
    actually produced, recorded, asked for or will consume the result.

    What is checkable from here is the part that does not depend on who is
    calling: the seven identities must be seven, and none of them may be the
    evaluator. A configuration that names the evaluator twice under two roles, or
    that quietly reuses one name for producer and consumer, is a configuration in
    which independence is a spelling rather than a fact.

    The other half — that the *actual* producer and recorder are these — is bound
    where the work happens: ``issue_activity_recorder_entitlement`` refuses to
    entitle an actor the set does not name, ``record_activity`` takes the two
    identities off that entitlement rather than from its caller, and
    ``require_consumable_activity_decision`` re-checks the record against the set
    and against the evaluator. Worker, model and machine adapter are named by the
    set and are not resolvable from a replay at all, which is a limit worth
    stating plainly: a replay consumes a record, and the worker that once asked
    for the effect is not present at consumption to be identified.
    """

    require_activity_policy_evaluator(evaluator)
    if type(executor_actor) is not ActorIdentity:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "executor_actor must be exact")
    evaluator_name = evaluator.declaration.evaluator_identity.value
    if executor_actor.value == evaluator_name:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the activity policy evaluator is the real replay executor",
        )
    if executor_actor != evaluator.actor_set.replay_executor_actor:
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            "the real replay executor differs from the sealed actor set",
        )
    actors = evaluator.actor_set.actors()
    names = [item.value for item in actors]
    # Deliberately not a uniqueness check. An earlier revision required the seven
    # names to be seven distinct names, which is a rule §9.4 does not contain:
    # what it requires is that the *evaluator* be independent of each actual
    # actor, not that no actor may legitimately hold two of the other roles. A
    # deployment where the worker and the consumer are the same service is not
    # one where independence has failed, and refusing it was refusing a lawful
    # configuration for a property nobody asked for.
    if evaluator_name in set(names):
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            "the activity policy evaluator is one of the actors it decides about",
        )

    # The declared set has now been checked against itself, which is all a set
    # can do. What §9.4 actually requires is independence from the *actual*
    # parties, and those are known only from provenance — so when either phase
    # is supplied, the evaluator is checked against the union of what actually
    # happened rather than against what was declared about it.
    actual: list[ActorIdentity] = []
    if production is not None:
        validate_activity_production_provenance(production)
        actual.extend(production.actors())
    if consumption is not None:
        validate_activity_consumption_provenance(consumption)
        actual.extend(consumption.actors())
    for actor in actual:
        if actor.value == evaluator_name:
            raise _fail(
                ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
                "the activity policy evaluator is one of the actual parties",
            )


def issue_activity_recorder_entitlement(
    evaluator: ConfiguredActivityPolicyEvaluator,
    *,
    production: ActivityProductionProvenance,
) -> ActivityRecorderEntitlement:
    """Entitle the recorder named by a production provenance, or refuse. §9.4.

    It takes a provenance record and not two identities, and that is the whole
    point of the signature. Two ``ActorIdentity`` arguments are two names a
    caller chose: the authority could check them against the sealed set, but the
    set is a declaration too, so the strongest thing the old shape could
    establish was that one declaration agreed with another. §9.4 asks for actor
    identities resolved from trusted execution provenance, and a provenance
    record is what that resolution produces — sealed, hash-bound to the
    configuration and to the actor set, and made durable before it is used.

    ``require_activity_policy_evaluator`` re-checks the entitlement chain first,
    so an evaluator whose independence proof no longer holds cannot issue.

    Nothing in this package calls this, and that is the shape of the thing rather
    than an omission: the party it entitles is the live recorder, which performs
    effects and lives outside a replay — the same party ``record_activity`` says
    hands it the exact result bytes. What this package contains is the authority
    that entitles that party and the replay that later consumes what it wrote.
    """

    require_activity_policy_evaluator(evaluator)
    validate_activity_production_provenance(production)
    actors = evaluator.actor_set
    if (
        production.configuration_id.digest_sha256
        != evaluator.declaration.configuration_id.digest_sha256
        or production.actor_set_id.digest_sha256 != actors.actor_set_id.digest_sha256
    ):
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            "this production provenance was taken under another configuration",
        )
    # Re-derived rather than trusted: the provenance is sealed, but the rule it
    # has to satisfy belongs to the authority issuing now, not to whoever sealed
    # it. Both checks are the ones the factory made, made again.
    for name in _PRODUCTION_ACTOR_FIELDS:
        _require_declared_actor(evaluator, getattr(production, name), role=name)
    return issue_recorder_entitlement(
        producer_actor=production.producer_actor,
        recorder_actor=production.recorder_actor,
        actor_set_id=actors.actor_set_id,
        configuration_id=evaluator.declaration.configuration_id,
        production_provenance_ref=activity_provenance_ref(production),
        _seal=_RECORDER_ENTITLEMENT_SEAL,
    )


def configure_activity_policy_evaluator(
    *,
    declaration: ActivityPolicyDeclaration,
    actor_set: ActivityPolicyActorSet,
    independence_proof: ActivityPolicyIndependenceProof,
    lifecycle_store: AuthorityAnchorPort,
    taint_store: AuthorityAnchorPort,
    trusted_clock: Callable[[], datetime],
) -> ConfiguredActivityPolicyEvaluator:
    """Bind the evaluator to its entitlement and to the histories it must read.

    The two stores are here rather than passed per call for the same reason the
    policy is on the declaration: an evaluator told what the world looks like is
    not reading the world. It reads the anchors itself, at decision time.
    """

    require_activity_policy_entitlement(
        independence_proof, declaration=declaration, actor_set=actor_set
    )
    for store, name in ((lifecycle_store, "lifecycle_store"), (taint_store, "taint_store")):
        if not isinstance(store, AuthorityAnchorPort):
            raise _fail(
                ActivityPolicyFailureCode.TYPE_MISMATCH,
                f"{name} must expose a durable anchor",
            )
    if not callable(trusted_clock):
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "trusted_clock must be callable")
    return require_activity_policy_evaluator(
        ConfiguredActivityPolicyEvaluator(
            declaration, actor_set, independence_proof,
            lifecycle_store, taint_store, trusted_clock,
            _seal=_EVALUATOR_SEAL,
        )
    )


def _anchor_digest(store: AuthorityAnchorPort, field: str) -> str:
    anchor = store.current_anchor()
    text = getattr(anchor, "ordered_log_root_sha256", anchor)
    if type(text) is not str:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{field} anchor is not a digest")
    return _sha256(text, field)


def evaluate_activity_policy(
    evaluator: ConfiguredActivityPolicyEvaluator,
    *,
    activity: RecordedActivity,
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    run_id: RunId,
    attempt_id: AttemptId,
    environment_profile_id: str,
    capability_profile_digest: str,
    consumption: ActivityConsumptionProvenance,
) -> ActivityPolicyDecision:
    """Decide, from the declared policy and the world as it is now.

    Note what is absent from the signature: a disposition. The caller states the
    activity and the context it will be used in, and receives an answer it did
    not choose. That is the whole difference between an authority and a field.

    ``consumption`` is the other half of §9.4 provenance. The production phase
    arrives on the record — the activity carries the reference to the provenance
    its recorder was entitled by — and the consumption phase is what the party
    asking for this decision brings. The decision therefore names both, and the
    evaluator's independence is checked against the union of what actually
    happened rather than against the seven names its configuration declares.
    """

    require_activity_policy_evaluator(evaluator)
    validate_recorded_activity(activity)
    validate_activity_consumption_provenance(consumption)
    if (
        consumption.configuration_id.digest_sha256
        != evaluator.declaration.configuration_id.digest_sha256
        or consumption.actor_set_id.digest_sha256
        != evaluator.actor_set.actor_set_id.digest_sha256
    ):
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            "this consumption provenance was taken under another configuration",
        )
    evaluator_name = evaluator.declaration.evaluator_identity.value
    for actor in consumption.actors():
        if actor.value == evaluator_name:
            raise _fail(
                ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
                "the activity policy evaluator is one of the actual consuming parties",
            )
    declaration = evaluator._declaration
    disposition = declaration.disposition_for(activity.kind)
    result = object.__new__(ActivityPolicyDecision)
    object.__setattr__(result, "schema_version", SchemaVersion.ACTIVITY_POLICY_DECISION_V1)
    object.__setattr__(result, "disposition", disposition)
    object.__setattr__(result, "activity_kind", activity.kind)
    object.__setattr__(result, "activity_lookup_key", activity.lookup_key)
    object.__setattr__(result, "activity_identity", activity.activity_identity)
    object.__setattr__(result, "inputs_digest", activity.inputs.digest())
    object.__setattr__(result, "result_sha256", activity.result_sha256)
    object.__setattr__(result, "result_ref", _ref(activity.result_ref, "result_ref"))
    object.__setattr__(
        result,
        "production_provenance_ref",
        _ref(activity.production_provenance_ref, "production_provenance_ref"),
    )
    object.__setattr__(
        result, "consumption_provenance_ref", activity_provenance_ref(consumption)
    )
    object.__setattr__(result, "activity_policy_version", declaration.policy_version)
    object.__setattr__(result, "consumer_context_ref", _ref(consumer_context_ref, "consumer_context_ref"))
    object.__setattr__(result, "boundary_ref", _ref(boundary_ref, "boundary_ref"))
    if type(run_id) is not RunId or type(attempt_id) is not AttemptId:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "run and attempt identities must be exact")
    object.__setattr__(result, "run_id", run_id)
    object.__setattr__(result, "attempt_id", attempt_id)
    object.__setattr__(result, "environment_profile_id", _identifier(environment_profile_id, "environment_profile_id"))
    object.__setattr__(result, "capability_profile_digest", _sha256(capability_profile_digest, "capability_profile_digest"))
    object.__setattr__(result, "declaration_id", declaration.declaration_id)
    object.__setattr__(result, "configuration_id", declaration.configuration_id)
    object.__setattr__(result, "actor_set_id", evaluator._actor_set.actor_set_id)
    object.__setattr__(result, "proof_id", evaluator._proof.proof_id)
    object.__setattr__(result, "lifecycle_anchor_sha256", _anchor_digest(evaluator._lifecycle_store, "lifecycle_anchor_sha256"))
    object.__setattr__(result, "taint_anchor_sha256", _anchor_digest(evaluator._taint_store, "taint_anchor_sha256"))
    decided_at = _timestamp(evaluator._trusted_clock(), "decided_at_utc")
    if decided_at.utcoffset() is None or decided_at.utcoffset().total_seconds() != 0:
        raise _fail(ActivityPolicyFailureCode.MALFORMED_TIMESTAMP, "decided_at_utc must be UTC")
    decided_at = decided_at.astimezone(timezone.utc)
    object.__setattr__(result, "decided_at_utc", decided_at)
    payload = _canonical(_decision_payload(result))
    envelope = create_common_envelope(
        schema_version=SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=IdentityDomain.ACTIVITY_POLICY_DECISION,
        canonical_payload_bytes=payload,
        run_id=run_id,
        attempt_id=attempt_id,
        created_at_utc=decided_at,
        producer_component=ACTIVITY_POLICY_PRODUCER_COMPONENT_V1,
        repository_revision=activity.envelope.repository_revision,
        policy_version=declaration.policy_version,
        environment_profile_id=environment_profile_id,
        lineage_parent_ids=(),
    )
    object.__setattr__(result, "envelope", envelope)
    object.__setattr__(result, "envelope_binding_sha256", compute_envelope_binding_sha256(envelope))
    object.__setattr__(
        result,
        "decision_id",
        envelope.record_id,
    )
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    validate_activity_policy_decision(result)
    return result


def _decision_payload_from_dict(value: object) -> dict[str, object]:
    fields = (
        "schema_version",
        "disposition",
        "activity_kind",
        "activity_lookup_key",
        "activity_identity",
        "inputs_digest",
        "result_sha256",
        "result_ref",
        "production_provenance_ref",
        "consumption_provenance_ref",
        "activity_policy_version",
        "consumer_context_ref",
        "boundary_ref",
        "run_id",
        "attempt_id",
        "environment_profile_id",
        "capability_profile_digest",
        "declaration_id",
        "configuration_id",
        "actor_set_id",
        "proof_id",
        "lifecycle_anchor_sha256",
        "taint_anchor_sha256",
        "decided_at_utc",
    )
    if type(value) is not dict or set(value) != set(fields):
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "decision payload has an invalid shape")
    return value


def activity_policy_decision_from_dict(
    value: object,
    *,
    evaluator: ConfiguredActivityPolicyEvaluator,
) -> ActivityPolicyDecision:
    """Restore one decision only against the exact configured authority."""

    require_activity_policy_evaluator(evaluator)
    if type(value) is not dict or set(value) != {"envelope", "envelope_binding_sha256", "payload"}:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "decision record has an invalid shape")
    payload = _decision_payload_from_dict(value["payload"])
    canonical_payload = _canonical(payload)
    try:
        envelope = common_envelope_from_dict(
            value["envelope"],
            canonical_payload_bytes=canonical_payload,
        )
        schema = SchemaVersion(payload["schema_version"])
        disposition = ActivityDisposition(payload["disposition"])
        kind = ActivityKind(payload["activity_kind"])
        result_ref = HashBoundRef.from_dict(payload["result_ref"])
        production_provenance_ref = HashBoundRef.from_dict(payload["production_provenance_ref"])
        consumption_provenance_ref = HashBoundRef.from_dict(
            payload["consumption_provenance_ref"]
        )
        consumer_context_ref = HashBoundRef.from_dict(payload["consumer_context_ref"])
        boundary_ref = HashBoundRef.from_dict(payload["boundary_ref"])
        run_id = RunId.from_dict(payload["run_id"])
        attempt_id = AttemptId.from_dict(payload["attempt_id"])
        declaration_id = record_id_reference_from_dict(payload["declaration_id"])
        configuration_id = record_id_reference_from_dict(payload["configuration_id"])
        actor_set_id = record_id_reference_from_dict(payload["actor_set_id"])
        proof_id = record_id_reference_from_dict(payload["proof_id"])
        decided_at = datetime.strptime(payload["decided_at_utc"], UTC_TIMESTAMP_FORMAT).replace(
            tzinfo=timezone.utc
        )
    except (ContractViolation, ValueError, TypeError, KeyError) as exc:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, "decision transport is invalid") from exc
    if schema is not SchemaVersion.ACTIVITY_POLICY_DECISION_V1:
        raise _fail(ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION, "decision schema is unknown")
    result = object.__new__(ActivityPolicyDecision)
    for name, item in (
        ("schema_version", schema),
        ("envelope", envelope),
        ("envelope_binding_sha256", value["envelope_binding_sha256"]),
        ("decision_id", envelope.record_id),
        ("disposition", disposition),
        ("activity_kind", kind),
        ("activity_lookup_key", payload["activity_lookup_key"]),
        ("activity_identity", payload["activity_identity"]),
        ("inputs_digest", payload["inputs_digest"]),
        ("result_sha256", payload["result_sha256"]),
        ("result_ref", result_ref),
        ("production_provenance_ref", production_provenance_ref),
        ("consumption_provenance_ref", consumption_provenance_ref),
        ("activity_policy_version", payload["activity_policy_version"]),
        ("consumer_context_ref", consumer_context_ref),
        ("boundary_ref", boundary_ref),
        ("run_id", run_id),
        ("attempt_id", attempt_id),
        ("environment_profile_id", payload["environment_profile_id"]),
        ("capability_profile_digest", payload["capability_profile_digest"]),
        ("declaration_id", declaration_id),
        ("configuration_id", configuration_id),
        ("actor_set_id", actor_set_id),
        ("proof_id", proof_id),
        ("lifecycle_anchor_sha256", payload["lifecycle_anchor_sha256"]),
        ("taint_anchor_sha256", payload["taint_anchor_sha256"]),
        ("decided_at_utc", decided_at),
    ):
        object.__setattr__(result, name, item)
    object.__setattr__(result, "_trusted_seal", _DECISION_SEAL)
    validate_activity_policy_decision(result)
    if (
        result.declaration_id != evaluator.declaration.declaration_id
        or result.configuration_id != evaluator.declaration.configuration_id
        or result.actor_set_id != evaluator.actor_set.actor_set_id
        or result.proof_id != evaluator.independence_proof.proof_id
    ):
        raise _fail(ActivityPolicyFailureCode.CONFIGURATION_MISMATCH, "decision names another authority")
    return result


def require_consumable_activity_decision(
    decision: ActivityPolicyDecision,
    *,
    evaluator: ConfiguredActivityPolicyEvaluator,
    activity: RecordedActivity,
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    run_id: RunId,
    attempt_id: AttemptId,
    environment_profile_id: str,
    capability_profile_digest: str,
    consumption: ActivityConsumptionProvenance,
) -> None:
    """Consumer-side re-check. A decision is valid for exactly what it decided about.

    Every binding is compared rather than assumed, because each of them can move
    on its own and each of them changes the question. The two anchors cover a
    whole family at once: revoke, quarantine, taint escalation and supersession
    are appends to those histories, so any of them shifts an anchor and the
    decision stops matching the world it was taken in — without this module
    needing a separate name for each cause.

    Only ``RECORDED_CONSUMABLE`` passes. ``REQUIRES_FRESH_AUTHORITY`` is refused
    here and not somewhere later, because "a live call would be required" is a
    refusal during replay and never a weaker form of permission.
    """

    require_activity_policy_evaluator(evaluator)
    validate_activity_policy_decision(decision)
    validate_recorded_activity(activity)
    require_activity_policy_entitlement(
        evaluator._proof, declaration=evaluator._declaration, actor_set=evaluator._actor_set
    )
    if decision.declaration_id != evaluator._declaration.declaration_id:
        raise _fail(
            ActivityPolicyFailureCode.DECISION_STATE_DRIFTED,
            "the decision was taken under another evaluator declaration",
        )
    if decision.actor_set_id != evaluator._actor_set.actor_set_id or decision.proof_id != evaluator._proof.proof_id:
        raise _fail(
            ActivityPolicyFailureCode.DECISION_STATE_DRIFTED,
            "the decision was taken under another entitlement",
        )
    if decision.activity_policy_version != evaluator._declaration.policy_version:
        raise _fail(
            ActivityPolicyFailureCode.DECISION_STATE_DRIFTED,
            "the activity policy version has drifted since the decision",
        )
    if (
        decision.activity_identity != activity.activity_identity
        or decision.activity_lookup_key != activity.lookup_key
        or decision.result_sha256 != activity.result_sha256
        or decision.result_ref.to_dict() != activity.result_ref.to_dict()
        or decision.inputs_digest != activity.inputs.digest()
        or decision.activity_kind is not activity.kind
        or decision.production_provenance_ref.to_dict()
        != activity.production_provenance_ref.to_dict()
    ):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_SUBJECT_MISMATCH,
            "the decision was taken about another activity or another result",
        )
    if (
        decision.consumer_context_ref.to_dict() != _ref(consumer_context_ref, "consumer_context_ref").to_dict()
        or decision.boundary_ref.to_dict() != _ref(boundary_ref, "boundary_ref").to_dict()
        or decision.run_id != run_id
        or decision.attempt_id != attempt_id
        or decision.environment_profile_id != environment_profile_id
        or decision.capability_profile_digest != capability_profile_digest
    ):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH,
            "the decision was taken for another execution context",
        )
    # The consuming half of §9.4, re-checked the same way the producing half is:
    # a decision taken for one executor, adapter and consumer is not a decision
    # about a run performed by another set of them.
    validate_activity_consumption_provenance(consumption)
    if (
        decision.consumption_provenance_ref.to_dict()
        != activity_provenance_ref(consumption).to_dict()
    ):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH,
            "the decision was taken for another consuming party",
        )
    if decision.lifecycle_anchor_sha256 != _anchor_digest(evaluator._lifecycle_store, "lifecycle_anchor_sha256"):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_STATE_DRIFTED,
            "the lifecycle history moved after this decision was taken",
        )
    if decision.taint_anchor_sha256 != _anchor_digest(evaluator._taint_store, "taint_anchor_sha256"):
        raise _fail(
            ActivityPolicyFailureCode.DECISION_STATE_DRIFTED,
            "the taint history moved after this decision was taken",
        )
    if decision.disposition is not ActivityDisposition.RECORDED_CONSUMABLE:
        raise _fail(
            ActivityPolicyFailureCode.NOT_CONSUMABLE,
            f"the activity policy answered {decision.disposition.value}",
        )
    # Independence, re-checked against who actually did the work rather than
    # against who a configuration says did it. OD-10/V1 §9.4.
    #
    # The record carries its producer and recorder as identities the policy
    # authority resolved when it issued the recorder's entitlement, so the two
    # comparisons below are not the same comparison twice. The first says the
    # record was made by the actors this evaluator's sealed set names; the second
    # says the evaluator is neither of them. Before the record carried actors at
    # all, an evaluator could be the real recorder while the actor set named
    # somebody else, and nothing anywhere could see it — the set and the work had
    # no point of contact.
    actors = evaluator._actor_set
    # The record names the actor set it was made under, so an entitlement issued
    # by another configuration produces a record no other evaluator accepts. That
    # is what stops one entitlement from working everywhere — comparing actor
    # *names* alone would let two configurations that happen to spell their
    # actors the same way consume each other's records.
    if activity.actor_set_id != actors.actor_set_id:
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            "the record was made under another actor set",
        )
    if (
        activity.producer_actor != actors.producer_actor
        or activity.recorder_actor != actors.recorder_actor
    ):
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            "the record was produced by actors this evaluator's set does not name",
        )
    evaluator_name = evaluator._declaration.evaluator_identity.value
    for actor, role in (
        (activity.producer_actor, "producer"),
        (activity.recorder_actor, "recorder"),
    ):
        if getattr(actor, "value", None) == evaluator_name:
            raise _fail(
                ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
                f"the activity policy evaluator is the real {role} of this record",
            )


# ---------------------------------------------------------------------------
# §9.4 provenance, in the two phases it actually happens in
# ---------------------------------------------------------------------------
#
# The seven roles §9.4 names do not all exist at the same moment. When an effect
# is recorded there is a producer, a recorder, a worker that asked for it and a
# model that answered; there is no replay executor, no machine adapter and no
# consumer, because the replay that will consume the record has not been asked
# for yet. Writing all seven into one entitlement at recording time would be
# writing four facts and three predictions, and a prediction signed by an
# authority is still a prediction.
#
# So provenance is taken twice. The production phase records who was actually
# there when the effect happened. The consumption phase records who is actually
# there when a replay serves it — including the *exact* machine adapter, which
# is a property of the running system and not a name anybody declares. The
# evaluator's independence is then checked against the union of the two, which
# is the set of parties a decision actually concerns.


@dataclass(frozen=True, init=False)
class ActivityProductionProvenance:
    """Who was actually there when the effect was recorded. Phase one."""

    schema_version: SchemaVersion
    provenance_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    producer_actor: ActorIdentity
    recorder_actor: ActorIdentity
    worker_actor: ActorIdentity
    model_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityProductionProvenance:
        raise TypeError("ActivityProductionProvenance is produced only by its factory")

    def actors(self) -> tuple[ActorIdentity, ...]:
        validate_activity_production_provenance(self)
        return tuple(getattr(self, name) for name in _PRODUCTION_ACTOR_FIELDS)

    def to_dict(self) -> dict[str, object]:
        validate_activity_production_provenance(self)
        return _production_provenance_payload(self) | {
            "provenance_id": self.provenance_id.to_dict()
        }

    def canonical_bytes(self) -> bytes:
        validate_activity_production_provenance(self)
        return _canonical(_production_provenance_payload(self))


@dataclass(frozen=True, init=False)
class ActivityConsumptionProvenance:
    """Who is actually there when a replay serves the record. Phase two.

    ``machine_adapter_id`` is not an actor name. The party that runs a behaviour
    during a replay is a specific adapter over a specific machine, and what
    identifies it is what it *is* rather than what anybody calls it — so this
    field carries the exact adapter identity the executor built, and the actor
    set's ``machine_adapter_actor`` is checked against it as a declaration about
    that same party.
    """

    schema_version: SchemaVersion
    provenance_id: RecordId
    configuration_id: RecordId
    actor_set_id: RecordId
    replay_executor_actor: ActorIdentity
    machine_adapter_actor: ActorIdentity
    machine_adapter_id: str
    consumer_actor: ActorIdentity
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ActivityConsumptionProvenance:
        raise TypeError("ActivityConsumptionProvenance is produced only by its factory")

    def actors(self) -> tuple[ActorIdentity, ...]:
        validate_activity_consumption_provenance(self)
        return tuple(getattr(self, name) for name in _CONSUMPTION_ACTOR_FIELDS)

    def to_dict(self) -> dict[str, object]:
        validate_activity_consumption_provenance(self)
        return _consumption_provenance_payload(self) | {
            "provenance_id": self.provenance_id.to_dict()
        }

    def canonical_bytes(self) -> bytes:
        validate_activity_consumption_provenance(self)
        return _canonical(_consumption_provenance_payload(self))


def _production_provenance_payload(value: ActivityProductionProvenance) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        **{name: getattr(value, name).value for name in _PRODUCTION_ACTOR_FIELDS},
    }


def _consumption_provenance_payload(value: ActivityConsumptionProvenance) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "configuration_id": value.configuration_id.to_dict(),
        "actor_set_id": value.actor_set_id.to_dict(),
        "machine_adapter_id": value.machine_adapter_id,
        **{name: getattr(value, name).value for name in _CONSUMPTION_ACTOR_FIELDS},
    }


def validate_activity_production_provenance(value: object) -> None:
    if (
        type(value) is not ActivityProductionProvenance
        or getattr(value, "_trusted_seal", None) is not _PRODUCTION_PROVENANCE_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity production provenance is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity production provenance schema is unknown",
        )
    for name in _PRODUCTION_ACTOR_FIELDS:
        if type(getattr(value, name)) is not ActorIdentity:
            raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    try:
        validate_record_id(
            value.provenance_id,
            canonical_bytes=_canonical(_production_provenance_payload(value)),
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "provenance_id does not match its payload",
        ) from exc


def validate_activity_consumption_provenance(value: object) -> None:
    if (
        type(value) is not ActivityConsumptionProvenance
        or getattr(value, "_trusted_seal", None) is not _CONSUMPTION_PROVENANCE_SEAL
    ):
        raise _fail(
            ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED,
            "activity consumption provenance is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1:
        raise _fail(
            ActivityPolicyFailureCode.UNKNOWN_SCHEMA_VERSION,
            "activity consumption provenance schema is unknown",
        )
    for name in _CONSUMPTION_ACTOR_FIELDS:
        if type(getattr(value, name)) is not ActorIdentity:
            raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    if type(value.machine_adapter_id) is not str or not value.machine_adapter_id:
        raise _fail(
            ActivityPolicyFailureCode.TYPE_MISMATCH,
            "the exact machine adapter identity must be a non-empty string",
        )
    try:
        validate_record_id(
            value.provenance_id,
            canonical_bytes=_canonical(_consumption_provenance_payload(value)),
        )
    except ContractViolation as exc:
        raise _fail(
            ActivityPolicyFailureCode.IDENTITY_MISMATCH,
            "provenance_id does not match its payload",
        ) from exc


def _require_declared_actor(
    evaluator: ConfiguredActivityPolicyEvaluator, actor: object, *, role: str
) -> ActorIdentity:
    """One actual identity, checked against the role the sealed set declares.

    Two separate things, and both are needed. The set must name this actor for
    this role, or a party nobody declared would be recorded as having done the
    work. And the actor must not be the evaluator, whatever the set says — an
    authority ruling on its own work has approved itself, and a set that spells
    the two names differently does not change what happened.
    """

    if type(actor) is not ActorIdentity:
        raise _fail(ActivityPolicyFailureCode.TYPE_MISMATCH, f"{role} must be exact")
    # Independence first, and the order is the point. A set that names someone
    # else for this role while the evaluator is the party actually doing the work
    # is precisely the arrangement §9.4 exists to catch, and reporting it as a
    # set mismatch would name the smaller of the two problems.
    if actor.value == evaluator.declaration.evaluator_identity.value:
        raise _fail(
            ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT,
            f"the activity policy evaluator is the real {role}",
        )
    if actor != getattr(evaluator.actor_set, role):
        raise _fail(
            ActivityPolicyFailureCode.ACTOR_SET_MISMATCH,
            f"the real {role} differs from the sealed actor set",
        )
    return actor


def record_activity_production_provenance(
    evaluator: ConfiguredActivityPolicyEvaluator,
    *,
    producer_actor: ActorIdentity,
    recorder_actor: ActorIdentity,
    worker_actor: ActorIdentity,
    model_actor: ActorIdentity,
) -> ActivityProductionProvenance:
    """Take the production phase of §9.4 provenance, or refuse."""

    require_activity_policy_evaluator(evaluator)
    resolved = {
        name: _require_declared_actor(evaluator, actor, role=name)
        for name, actor in (
            ("producer_actor", producer_actor),
            ("recorder_actor", recorder_actor),
            ("worker_actor", worker_actor),
            ("model_actor", model_actor),
        )
    }
    payload = object.__new__(ActivityProductionProvenance)
    object.__setattr__(
        payload, "schema_version", SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1
    )
    object.__setattr__(payload, "configuration_id", evaluator.declaration.configuration_id)
    object.__setattr__(payload, "actor_set_id", evaluator.actor_set.actor_set_id)
    for name, actor in resolved.items():
        object.__setattr__(payload, name, actor)
    object.__setattr__(payload, "_trusted_seal", _PRODUCTION_PROVENANCE_SEAL)
    object.__setattr__(
        payload,
        "provenance_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_PRODUCTION_PROVENANCE,
            canonical_bytes=_canonical(_production_provenance_payload(payload)),
        ),
    )
    validate_activity_production_provenance(payload)
    return payload


def record_activity_consumption_provenance(
    evaluator: ConfiguredActivityPolicyEvaluator,
    *,
    replay_executor_actor: ActorIdentity,
    machine_adapter_actor: ActorIdentity,
    machine_adapter_id: str,
    consumer_actor: ActorIdentity,
) -> ActivityConsumptionProvenance:
    """Take the consumption phase of §9.4 provenance, or refuse."""

    require_activity_policy_evaluator(evaluator)
    resolved = {
        name: _require_declared_actor(evaluator, actor, role=name)
        for name, actor in (
            ("replay_executor_actor", replay_executor_actor),
            ("machine_adapter_actor", machine_adapter_actor),
            ("consumer_actor", consumer_actor),
        )
    }
    if type(machine_adapter_id) is not str or not machine_adapter_id:
        raise _fail(
            ActivityPolicyFailureCode.TYPE_MISMATCH,
            "the exact machine adapter identity must be a non-empty string",
        )
    payload = object.__new__(ActivityConsumptionProvenance)
    object.__setattr__(
        payload, "schema_version", SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1
    )
    object.__setattr__(payload, "configuration_id", evaluator.declaration.configuration_id)
    object.__setattr__(payload, "actor_set_id", evaluator.actor_set.actor_set_id)
    object.__setattr__(payload, "machine_adapter_id", machine_adapter_id)
    for name, actor in resolved.items():
        object.__setattr__(payload, name, actor)
    object.__setattr__(payload, "_trusted_seal", _CONSUMPTION_PROVENANCE_SEAL)
    object.__setattr__(
        payload,
        "provenance_id",
        compute_record_id(
            domain=IdentityDomain.ACTIVITY_CONSUMPTION_PROVENANCE,
            canonical_bytes=_canonical(_consumption_provenance_payload(payload)),
        ),
    )
    validate_activity_consumption_provenance(payload)
    return payload


def activity_provenance_ref(value: object) -> HashBoundRef:
    """The hash-bound reference a durable record stores for one provenance phase."""

    if type(value) is ActivityProductionProvenance:
        validate_activity_production_provenance(value)
        schema = SchemaVersion.ACTIVITY_PRODUCTION_PROVENANCE_V1
    elif type(value) is ActivityConsumptionProvenance:
        validate_activity_consumption_provenance(value)
        schema = SchemaVersion.ACTIVITY_CONSUMPTION_PROVENANCE_V1
    else:
        raise _fail(
            ActivityPolicyFailureCode.TYPE_MISMATCH,
            "an exact activity provenance record is required",
        )
    payload = value.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=value.provenance_id.digest_sha256,
        schema_id=schema.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def activity_policy_decision_ref(decision: ActivityPolicyDecision) -> HashBoundRef:
    """The hash-bound reference a replay request stores for this decision."""

    validate_activity_policy_decision(decision)
    payload = decision.canonical_bytes()
    return HashBoundRef(
        kind=RefKind.GATE_DECISION,
        ref_id=decision.decision_id.digest_sha256,
        schema_id=SchemaVersion.ACTIVITY_POLICY_DECISION_V1.value,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


__all__ = [
    "ACTIVITY_POLICY_PRODUCER_COMPONENT_V1",
    "INDEPENDENCE_REASON_DISJOINT_ACTIVITY_ACTORS",
    "ActivityPolicyActorSet",
    "ActivityPolicyDecision",
    "ActivityPolicyDeclaration",
    "ActivityPolicyFailureCode",
    "ActivityPolicyIndependenceProof",
    "ActivityPolicyViolation",
    "AuthorityAnchorPort",
    "ConfiguredActivityPolicyEvaluator",
    "activity_policy_decision_ref",
    "activity_policy_decision_from_dict",
    "configure_activity_policy_evaluator",
    "create_activity_policy_actor_set",
    "create_activity_policy_declaration",
    "create_activity_policy_independence_proof",
    "evaluate_activity_policy",
    "issue_activity_recorder_entitlement",
    "require_activity_policy_entitlement",
    "require_activity_policy_evaluator",
    "require_activity_policy_execution_entitlement",
    "require_consumable_activity_decision",
    "validate_activity_policy_actor_set",
    "validate_activity_policy_decision",
    "validate_activity_policy_declaration",
    "validate_activity_policy_independence_proof",
]
