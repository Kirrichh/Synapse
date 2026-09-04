"""Fresh pre-side-effect revalidation of accepted Stage 10 plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re
from typing import Callable

from ..canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    canonicalize_stage4_payload,
)
from ..compatibility import (
    CompatibilityRevalidationRecord,
    RevalidationStage,
    require_revalidation_passed,
)
from ..contracts import AuthorityDecisionId, AttemptId, ExecutionId, RecordId
from ..point_of_use import CurrentAdmittedKnowledge, validate_current_admitted_knowledge
from .intent import IntentCandidate, validate_intent_candidate
from .plan_authority import (
    AcceptedOperationPlan,
    ConfiguredPlanAuthority,
    validate_accepted_operation_plan,
    validate_decision_against_inputs,
    require_human_approval,
)


SIDE_EFFECT_AUTHORIZATION_SCHEMA_V1 = (
    "synapse.stage4.gold.stage10.side-effect-authorization/v1"
)
_AUTHORIZATION_SEAL = object()
_PLAN_PERSISTENCE_SEAL = object()

ADAPTER_PRIVATE_EXPORTS = {
    "synapse.experiments.gold.stage10.record_store": frozenset(
        {"_make_plan_persistence_evidence"}
    ),
}


class RevalidationFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    STALE_REPOSITORY = "STALE_REPOSITORY"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    STALE_POLICY = "STALE_POLICY"
    ADMISSION_MISMATCH = "ADMISSION_MISMATCH"
    COMPATIBILITY_FAILED = "COMPATIBILITY_FAILED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class PlanRevalidationViolation(ValueError):
    def __init__(self, failure_code: RevalidationFailureCode, detail: str) -> None:
        if type(failure_code) is not RevalidationFailureCode:
            raise TypeError("failure_code must be an exact RevalidationFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: RevalidationFailureCode, detail: str) -> PlanRevalidationViolation:
    return PlanRevalidationViolation(code, detail)


def _canonical(value: object) -> bytes:
    return canonicalize_stage4_payload(
        value,
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )


@dataclass(frozen=True)
class CurrentPlanState:
    repository_revision_sha256: str
    knowledge_snapshot_ref: HashBoundRef
    policy_sha256: str
    admitted_knowledge: CurrentAdmittedKnowledge
    compatibility_revalidation: CompatibilityRevalidationRecord

    def __post_init__(self) -> None:
        if type(self.repository_revision_sha256) is not str or re.fullmatch(
            r"[0-9a-f]{40,64}", self.repository_revision_sha256
        ) is None:
            raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "repository revision is malformed")
        if type(self.knowledge_snapshot_ref) is not HashBoundRef:
            raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "snapshot ref must be exact")
        if type(self.policy_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", self.policy_sha256) is None:
            raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "policy hash is malformed")
        validate_current_admitted_knowledge(self.admitted_knowledge)
        try:
            require_revalidation_passed(
                self.compatibility_revalidation,
                expected_stage=RevalidationStage.BEFORE_CONSUMPTION,
            )
        except ValueError as exc:
            raise _fail(
                RevalidationFailureCode.COMPATIBILITY_FAILED,
                "fresh before-consumption compatibility revalidation did not pass",
            ) from exc


@dataclass(frozen=True, init=False)
class SideEffectAuthorization:
    schema_version: str
    authorization_sha256: str
    attempt_id: AttemptId
    accepted_plan_id: ExecutionId
    decision_id: AuthorityDecisionId
    repository_revision_sha256: str
    knowledge_snapshot_ref: HashBoundRef
    admitted_knowledge_id: RecordId
    compatibility_revalidation_id: RecordId
    policy_sha256: str
    allowed_scope: tuple[str, ...]
    capabilities: tuple[str, ...]
    context_id: str
    context_audit_sha256: str
    delivery_envelope_sha256: str
    plan_bundle_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> SideEffectAuthorization:
        raise TypeError("SideEffectAuthorization is created only by fresh revalidation")

    def canonical_bytes(self) -> bytes:
        validate_side_effect_authorization(self)
        return _canonical(_authorization_payload(self))

    def to_dict(self) -> dict[str, object]:
        return {
            "authorization_sha256": self.authorization_sha256,
            "payload": _authorization_payload(self),
        }


def _authorization_payload(value: SideEffectAuthorization) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "attempt_id": value.attempt_id.to_dict(),
        "accepted_plan_id": value.accepted_plan_id.to_dict(),
        "decision_id": value.decision_id.to_dict(),
        "repository_revision_sha256": value.repository_revision_sha256,
        "knowledge_snapshot_ref": value.knowledge_snapshot_ref.to_dict(),
        "admitted_knowledge_id": value.admitted_knowledge_id.to_dict(),
        "compatibility_revalidation_id": value.compatibility_revalidation_id.to_dict(),
        "policy_sha256": value.policy_sha256,
        "allowed_scope": list(value.allowed_scope),
        "capabilities": list(value.capabilities),
        "context_id": value.context_id,
        "context_audit_sha256": value.context_audit_sha256,
        "delivery_envelope_sha256": value.delivery_envelope_sha256,
        "plan_bundle_sha256": value.plan_bundle_sha256,
    }


def authorize_first_side_effect(
    *,
    accepted_plan: AcceptedOperationPlan,
    intent: IntentCandidate,
    authority: ConfiguredPlanAuthority,
    attempt_id: AttemptId,
    current_state_reader: Callable[[], CurrentPlanState],
    admission_freshness_validator: Callable[[CurrentAdmittedKnowledge], None],
    context_id: str,
    context_audit_sha256: str,
    delivery_envelope_sha256: str,
    plan_bundle_sha256: str,
) -> SideEffectAuthorization:
    validate_accepted_operation_plan(accepted_plan)
    validate_intent_candidate(intent)
    validate_decision_against_inputs(
        accepted_plan.decision,
        plan=accepted_plan.candidate,
        intent=intent,
        authority=authority,
    )
    if type(attempt_id) is not AttemptId or not callable(current_state_reader):
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "attempt and state reader must be exact")
    if not callable(admission_freshness_validator):
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "admission freshness validator must be callable")
    if type(context_id) is not str or re.fullmatch(r"ctx_[0-9a-f]{64}", context_id) is None:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "context id is malformed")
    for name, digest in (
        ("context audit", context_audit_sha256),
        ("delivery envelope", delivery_envelope_sha256),
        ("plan bundle", plan_bundle_sha256),
    ):
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise _fail(RevalidationFailureCode.TYPE_MISMATCH, f"{name} digest is malformed")
    try:
        current = current_state_reader()
    except Exception as exc:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "current plan state is unavailable") from exc
    if type(current) is not CurrentPlanState:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "state reader returned an invalid state")
    CurrentPlanState(**current.__dict__)
    try:
        admission_freshness_validator(current.admitted_knowledge)
    except Exception as exc:
        raise _fail(RevalidationFailureCode.ADMISSION_MISMATCH, "current admission is no longer fresh") from exc
    candidate = accepted_plan.candidate
    if current.repository_revision_sha256 != candidate.repository_revision_sha256:
        raise _fail(RevalidationFailureCode.STALE_REPOSITORY, "repository changed after plan acceptance")
    if current.knowledge_snapshot_ref != candidate.knowledge_snapshot_ref:
        raise _fail(RevalidationFailureCode.STALE_SNAPSHOT, "knowledge snapshot changed after plan acceptance")
    if current.policy_sha256 != authority.policy.sha256 or current.policy_sha256 != accepted_plan.decision.policy_sha256:
        raise _fail(RevalidationFailureCode.STALE_POLICY, "plan authority policy changed")
    if accepted_plan.decision.human_approval_ref is not None:
        require_human_approval(
            authority=authority, plan=candidate, intent=intent,
            executor=accepted_plan.decision.independence_proof.executor_identity,
            approval_ref=accepted_plan.decision.human_approval_ref, current=True,
        )
    admitted = current.admitted_knowledge
    fields = dict(
        schema_version=SIDE_EFFECT_AUTHORIZATION_SCHEMA_V1,
        attempt_id=attempt_id,
        accepted_plan_id=accepted_plan.accepted_plan_id,
        decision_id=accepted_plan.decision.decision_id,
        repository_revision_sha256=current.repository_revision_sha256,
        knowledge_snapshot_ref=current.knowledge_snapshot_ref,
        admitted_knowledge_id=admitted.knowledge_id,
        compatibility_revalidation_id=current.compatibility_revalidation.revalidation_id,
        policy_sha256=current.policy_sha256,
        allowed_scope=candidate.allowed_scope.entries,
        capabilities=candidate.capability_profile,
        context_id=context_id,
        context_audit_sha256=context_audit_sha256,
        delivery_envelope_sha256=delivery_envelope_sha256,
        plan_bundle_sha256=plan_bundle_sha256,
    )
    provisional = object.__new__(SideEffectAuthorization)
    for name, item in fields.items():
        object.__setattr__(provisional, name, item)
    object.__setattr__(provisional, "authorization_sha256", "0" * 64)
    object.__setattr__(provisional, "_trusted_seal", _AUTHORIZATION_SEAL)
    digest = hashlib.sha256(_canonical(_authorization_payload(provisional))).hexdigest()
    result = object.__new__(SideEffectAuthorization)
    for name, item in fields.items():
        object.__setattr__(result, name, item)
    object.__setattr__(result, "authorization_sha256", digest)
    object.__setattr__(result, "_trusted_seal", _AUTHORIZATION_SEAL)
    validate_side_effect_authorization(result)
    return result


def validate_side_effect_authorization(value: SideEffectAuthorization) -> None:
    if type(value) is not SideEffectAuthorization or getattr(value, "_trusted_seal", None) is not _AUTHORIZATION_SEAL:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization must be exact")
    if value.schema_version != SIDE_EFFECT_AUTHORIZATION_SCHEMA_V1:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization schema is unknown")
    if type(value.attempt_id) is not AttemptId:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization attempt is invalid")
    if (
        type(value.accepted_plan_id) is not ExecutionId
        or type(value.decision_id) is not AuthorityDecisionId
        or type(value.admitted_knowledge_id) is not RecordId
        or type(value.compatibility_revalidation_id) is not RecordId
    ):
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization identities are invalid")
    if type(value.allowed_scope) is not tuple or not value.allowed_scope:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization scope is invalid")
    if type(value.capabilities) is not tuple or not value.capabilities:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization capabilities are invalid")
    if type(value.context_id) is not str or re.fullmatch(r"ctx_[0-9a-f]{64}", value.context_id) is None:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization context id is invalid")
    for digest in (
        value.context_audit_sha256,
        value.delivery_envelope_sha256,
        value.plan_bundle_sha256,
    ):
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "authorization binding digest is invalid")
    if type(value.authorization_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", value.authorization_sha256) is None:
        raise _fail(RevalidationFailureCode.IDENTITY_MISMATCH, "authorization digest is malformed")
    expected = hashlib.sha256(_canonical(_authorization_payload(value))).hexdigest()
    if value.authorization_sha256 != expected:
        raise _fail(RevalidationFailureCode.IDENTITY_MISMATCH, "authorization digest does not match payload")


@dataclass(frozen=True, init=False)
class PlanPersistenceEvidence:
    intent_store_ref: HashBoundRef
    plan_store_ref: HashBoundRef
    decision_store_ref: HashBoundRef
    accepted_plan_store_ref: HashBoundRef
    bundle_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PlanPersistenceEvidence:
        raise TypeError("PlanPersistenceEvidence is produced only by durable read-back")


def _plan_bundle_payloads(
    *,
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
) -> tuple[bytes, bytes, bytes, bytes]:
    validate_intent_candidate(intent)
    validate_accepted_operation_plan(accepted_plan)
    return (
        _canonical(intent.to_dict()),
        _canonical(accepted_plan.candidate.to_dict()),
        _canonical(accepted_plan.decision.to_dict()),
        _canonical(accepted_plan.to_dict()),
    )


def _plan_bundle_sha256(payloads: tuple[bytes, bytes, bytes, bytes]) -> str:
    framed = [
        {"sha256": hashlib.sha256(item).hexdigest(), "byte_length": len(item)}
        for item in payloads
    ]
    return hashlib.sha256(_canonical({"members": framed})).hexdigest()


def _make_plan_persistence_evidence(
    *,
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
    store_refs: tuple[HashBoundRef, HashBoundRef, HashBoundRef, HashBoundRef],
    restored_payloads: tuple[bytes, bytes, bytes, bytes],
) -> PlanPersistenceEvidence:
    expected = _plan_bundle_payloads(
        intent=intent,
        accepted_plan=accepted_plan,
    )
    if type(store_refs) is not tuple or len(store_refs) != 4 or any(
        type(item) is not HashBoundRef for item in store_refs
    ):
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "plan persistence refs are invalid")
    if type(restored_payloads) is not tuple or restored_payloads != expected:
        raise _fail(RevalidationFailureCode.IDENTITY_MISMATCH, "restored plan bundle differs")
    result = object.__new__(PlanPersistenceEvidence)
    for name, item in zip(
        (
            "intent_store_ref",
            "plan_store_ref",
            "decision_store_ref",
            "accepted_plan_store_ref",
        ),
        store_refs,
    ):
        object.__setattr__(result, name, item)
    object.__setattr__(result, "bundle_sha256", _plan_bundle_sha256(expected))
    object.__setattr__(result, "_trusted_seal", _PLAN_PERSISTENCE_SEAL)
    validate_plan_persistence_evidence(
        result,
        intent=intent,
        accepted_plan=accepted_plan,
    )
    return result


def validate_plan_persistence_evidence(
    value: PlanPersistenceEvidence,
    *,
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
) -> None:
    if type(value) is not PlanPersistenceEvidence or getattr(value, "_trusted_seal", None) is not _PLAN_PERSISTENCE_SEAL:
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "plan persistence evidence is not store-produced")
    refs = (
        value.intent_store_ref,
        value.plan_store_ref,
        value.decision_store_ref,
        value.accepted_plan_store_ref,
    )
    if any(type(item) is not HashBoundRef for item in refs):
        raise _fail(RevalidationFailureCode.TYPE_MISMATCH, "plan persistence ref is invalid")
    payloads = _plan_bundle_payloads(
        intent=intent,
        accepted_plan=accepted_plan,
    )
    if value.bundle_sha256 != _plan_bundle_sha256(payloads):
        raise _fail(RevalidationFailureCode.IDENTITY_MISMATCH, "plan persistence bundle hash differs")
