"""Typed, admitted-only Stage 10 worker context and audit record."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import re

from ..canonicalization import HashBoundRef, RefKind
from ..contracts import AttemptId
from ..point_of_use import CurrentAdmittedKnowledge, validate_current_admitted_knowledge
from ..replay import ReplayObservation, validate_replay_observation
from .context_codec import (
    WorkerDeliveryEnvelope,
    create_worker_delivery_envelope,
    encode_base64url,
    encode_canonical,
    render_worker_prompt,
    validate_worker_delivery_envelope,
)
from .intent import IntentCandidate, validate_intent_candidate
from .plan_authority import AcceptedOperationPlan, validate_accepted_operation_plan
from .planning import validate_operation_plan_against_intent


WORKER_CONTEXT_RECORD_SCHEMA_V1 = "synapse.stage4.gold.stage10.worker-context-record/v1"
WORKER_DELIVERY_BODY_SCHEMA_V1 = "synapse.stage4.gold.stage10.worker-delivery-body/v1"
_CONTEXT_PREFIX = b"synapse.stage4.gold.stage10.worker-context-id/v1\x00"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PERSISTENCE_EVIDENCE_SEAL = object()

ADAPTER_PRIVATE_EXPORTS = {
    "synapse.experiments.gold.stage10.record_store": frozenset(
        {"_make_context_persistence_evidence"}
    ),
}


class ContextFailureCode(str, Enum):
    TYPE_MISMATCH = "TYPE_MISMATCH"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    MALFORMED_IDENTIFIER = "MALFORMED_IDENTIFIER"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    CONTENT_LENGTH_MISMATCH = "CONTENT_LENGTH_MISMATCH"
    KNOWLEDGE_NOT_ADMITTED = "KNOWLEDGE_NOT_ADMITTED"
    DUPLICATE = "DUPLICATE"
    TASK_BINDING_MISMATCH = "TASK_BINDING_MISMATCH"
    PLAN_BINDING_MISMATCH = "PLAN_BINDING_MISMATCH"
    AUTHORIZATION_MISMATCH = "AUTHORIZATION_MISMATCH"
    SIZE_BUDGET_EXCEEDED = "SIZE_BUDGET_EXCEEDED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class ContextViolation(ValueError):
    def __init__(self, failure_code: ContextFailureCode, detail: str) -> None:
        if type(failure_code) is not ContextFailureCode:
            raise TypeError("failure_code must be an exact ContextFailureCode")
        if type(detail) is not str or not detail or len(detail) > 256:
            raise TypeError("detail must be a bounded non-empty string")
        self.failure_code = failure_code
        self.detail = detail
        super().__init__(f"{failure_code.value}: {detail}")


def _fail(code: ContextFailureCode, detail: str) -> ContextViolation:
    return ContextViolation(code, detail)


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise _fail(ContextFailureCode.MALFORMED_IDENTIFIER, f"{field} must be a safe identifier")
    return value


def _ref_key(value: HashBoundRef) -> tuple[str, str, str, str, int, str]:
    if type(value) is not HashBoundRef:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "reference must be exact")
    return (
        value.kind.value,
        value.ref_id,
        value.schema_id,
        value.sha256,
        value.byte_length,
        value.media_type,
    )


class ExclusionReason(str, Enum):
    REJECTED_BY_GATE = "REJECTED_BY_GATE"
    REVOKED = "REVOKED"
    TAINT_WITHHELD = "TAINT_WITHHELD"
    RAW_TRANSCRIPT = "RAW_TRANSCRIPT"
    FAILED_HYPOTHESIS_WITHHELD = "FAILED_HYPOTHESIS_WITHHELD"
    NOT_SELECTED_FOR_TASK = "NOT_SELECTED_FOR_TASK"


@dataclass(frozen=True)
class ExcludedKnowledgeRef:
    ref: HashBoundRef
    reason: ExclusionReason

    def __post_init__(self) -> None:
        if type(self.ref) is not HashBoundRef or type(self.reason) is not ExclusionReason:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "excluded ref fields must be exact")

    def to_dict(self) -> dict[str, object]:
        return {"ref": self.ref.to_dict(), "reason": self.reason.value}


@dataclass(frozen=True)
class AdmittedKnowledgeItem:
    item_id: str
    ref: HashBoundRef
    content: bytes
    taint_classes: tuple[str, ...]
    failed_hypothesis: bool

    def __post_init__(self) -> None:
        _identifier(self.item_id, "item_id")
        if type(self.ref) is not HashBoundRef:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "knowledge ref must be exact")
        if type(self.content) is not bytes or not self.content:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "knowledge content must be non-empty bytes")
        if hashlib.sha256(self.content).hexdigest() != self.ref.sha256:
            raise _fail(ContextFailureCode.CONTENT_HASH_MISMATCH, "knowledge content does not match its ref")
        if len(self.content) != self.ref.byte_length:
            raise _fail(ContextFailureCode.CONTENT_LENGTH_MISMATCH, "knowledge content length does not match its ref")
        if type(self.taint_classes) is not tuple:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "taint classes must be a tuple")
        checked = tuple(_identifier(item, "taint class") for item in self.taint_classes)
        if checked != tuple(sorted(set(checked))):
            raise _fail(ContextFailureCode.DUPLICATE, "taint classes must be sorted and unique")
        if type(self.failed_hypothesis) is not bool:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "failed_hypothesis must be exact bool")

    def delivery_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "ref": self.ref.to_dict(),
            "content_base64url": encode_base64url(self.content),
            "taint_classes": list(self.taint_classes),
            "failed_hypothesis": self.failed_hypothesis,
        }

    def fingerprint_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "ref": self.ref.to_dict(),
            "taint_classes": list(self.taint_classes),
            "failed_hypothesis": self.failed_hypothesis,
        }


@dataclass(frozen=True)
class ContextSizeBudget:
    maximum_items: int = 256
    maximum_item_bytes: int = 2 * 1024 * 1024
    maximum_body_bytes: int = 8 * 1024 * 1024
    maximum_prompt_bytes: int = 9 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if type(value) is not int or value <= 0:
                raise _fail(ContextFailureCode.TYPE_MISMATCH, f"{name} must be a positive integer")
        if self.maximum_item_bytes > self.maximum_body_bytes:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "item budget cannot exceed body budget")
        if self.maximum_body_bytes > self.maximum_prompt_bytes:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "body budget cannot exceed prompt budget")


@dataclass(frozen=True)
class WorkerContextRecord:
    schema_version: str
    context_id: str
    audit_sha256: str
    intent: IntentCandidate
    accepted_plan: AcceptedOperationPlan
    attempt_id: AttemptId
    admitted_knowledge: CurrentAdmittedKnowledge
    knowledge_items: tuple[AdmittedKnowledgeItem, ...]
    replay_observations: tuple[ReplayObservation, ...]
    excluded_refs: tuple[ExcludedKnowledgeRef, ...]
    delivery_envelope: WorkerDeliveryEnvelope

    def canonical_bytes(self) -> bytes:
        validate_worker_context(self)
        return encode_canonical(
            {
                "context_id": self.context_id,
                "audit_sha256": self.audit_sha256,
                "payload": _audit_payload(self),
            }
        )

    def to_dict(self) -> dict[str, object]:
        validate_worker_context(self)
        return {
            "context_id": self.context_id,
            "audit_sha256": self.audit_sha256,
            "payload": _audit_payload(self),
        }


@dataclass(frozen=True, init=False)
class ContextPersistenceEvidence:
    """Read-back evidence that both audit and delivery bytes are durable."""

    context_id: str
    audit_store_ref: HashBoundRef
    delivery_store_ref: HashBoundRef
    audit_payload_sha256: str
    delivery_payload_sha256: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> ContextPersistenceEvidence:
        raise TypeError("ContextPersistenceEvidence is produced only by durable read-back")


def _make_context_persistence_evidence(
    *,
    context: WorkerContextRecord,
    audit_store_ref: HashBoundRef,
    delivery_store_ref: HashBoundRef,
    restored_audit_payload: bytes,
    restored_delivery_payload: bytes,
) -> ContextPersistenceEvidence:
    validate_worker_context(context)
    if type(audit_store_ref) is not HashBoundRef or type(delivery_store_ref) is not HashBoundRef:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "persistence refs must be exact")
    if restored_audit_payload != context.canonical_bytes():
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "restored context audit bytes differ")
    if restored_delivery_payload != context.delivery_envelope.canonical_bytes():
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "restored delivery envelope bytes differ")
    result = object.__new__(ContextPersistenceEvidence)
    object.__setattr__(result, "context_id", context.context_id)
    object.__setattr__(result, "audit_store_ref", audit_store_ref)
    object.__setattr__(result, "delivery_store_ref", delivery_store_ref)
    object.__setattr__(
        result,
        "audit_payload_sha256",
        hashlib.sha256(restored_audit_payload).hexdigest(),
    )
    object.__setattr__(
        result,
        "delivery_payload_sha256",
        hashlib.sha256(restored_delivery_payload).hexdigest(),
    )
    object.__setattr__(result, "_trusted_seal", _PERSISTENCE_EVIDENCE_SEAL)
    validate_context_persistence_evidence(result, context=context)
    return result


def validate_context_persistence_evidence(
    value: ContextPersistenceEvidence,
    *,
    context: WorkerContextRecord,
) -> None:
    if (
        type(value) is not ContextPersistenceEvidence
        or getattr(value, "_trusted_seal", None) is not _PERSISTENCE_EVIDENCE_SEAL
    ):
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "persistence evidence must be exact")
    validate_worker_context(context)
    if value.context_id != context.context_id:
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "persistence evidence belongs to another context")
    if type(value.audit_store_ref) is not HashBoundRef or type(value.delivery_store_ref) is not HashBoundRef:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "persistence evidence refs must be exact")
    if value.audit_payload_sha256 != hashlib.sha256(context.canonical_bytes()).hexdigest():
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "persisted audit hash differs")
    if value.delivery_payload_sha256 != hashlib.sha256(context.delivery_envelope.canonical_bytes()).hexdigest():
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "persisted delivery hash differs")


def _task_policy_payload(
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
    attempt_id: AttemptId,
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id.to_dict(),
        "task_statement": intent.task_statement,
        "intent_proposal_id": intent.proposal_id.to_dict(),
        "accepted_plan_id": accepted_plan.accepted_plan_id.to_dict(),
        "plan_authority_decision_id": accepted_plan.decision.decision_id.to_dict(),
        "repository_revision_sha256": intent.repository_revision_sha256,
        "knowledge_snapshot_ref": intent.knowledge_snapshot_ref.to_dict(),
        "allowed_scope": list(accepted_plan.candidate.allowed_scope.entries),
        "capabilities": list(accepted_plan.candidate.capability_profile),
        "policy_sha256": accepted_plan.decision.policy_sha256,
    }


def _replay_delivery(value: ReplayObservation) -> dict[str, object]:
    validate_replay_observation(value)
    return {
        "observation_id": value.observation_id.to_dict(),
        "behavior_content_key": value.behavior_content_key,
        "program_hash": value.program_hash,
        "host_abi_version": value.host_abi_version,
        "terminal_snapshot_digest": value.terminal_snapshot_digest,
        "terminal_snapshot_ref": value.terminal_snapshot_ref.to_dict(),
        "steps_executed": value.steps_executed,
        "gas_consumed": value.gas_consumed,
        "transcript_matched": value.transcript_matched,
        "first_unexpected_index": value.first_unexpected_index,
        "failure_reason": None if value.failure_reason is None else value.failure_reason.value,
    }


def _delivery_body(
    *,
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
    attempt_id: AttemptId,
    admitted_knowledge: CurrentAdmittedKnowledge,
    knowledge_items: tuple[AdmittedKnowledgeItem, ...],
    replay_observations: tuple[ReplayObservation, ...],
) -> dict[str, object]:
    return {
        "schema_version": WORKER_DELIVERY_BODY_SCHEMA_V1,
        "task_policy": _task_policy_payload(intent, accepted_plan, attempt_id),
        "accepted_plan": {
            "accepted_plan_id": accepted_plan.accepted_plan_id.to_dict(),
            "execution_order": list(accepted_plan.candidate.execution_order),
            "operations": [item.to_dict() for item in accepted_plan.candidate.operations],
        },
        "admission": {
            "current_admitted_knowledge_id": admitted_knowledge.knowledge_id.to_dict(),
            "boundary_ref": admitted_knowledge.boundary_ref.to_dict(),
            "policy_version": admitted_knowledge.policy_version,
        },
        "admitted_items": [item.delivery_dict() for item in knowledge_items],
        "replay_observations": [_replay_delivery(item) for item in replay_observations],
    }


def _audit_payload(value: WorkerContextRecord) -> dict[str, object]:
    envelope = value.delivery_envelope
    return {
        "schema_version": value.schema_version,
        "task_policy": _task_policy_payload(value.intent, value.accepted_plan, value.attempt_id),
        "current_admitted_knowledge_id": value.admitted_knowledge.knowledge_id.to_dict(),
        "admitted_item_fingerprints": [item.fingerprint_dict() for item in value.knowledge_items],
        "replay_observation_ids": [item.observation_id.to_dict() for item in value.replay_observations],
        "excluded_refs": [item.to_dict() for item in value.excluded_refs],
        "delivery_body_sha256": envelope.body_sha256,
        "delivery_body_byte_length": envelope.body_byte_length,
        "prompt_sha256": envelope.prompt_sha256,
        "prompt_byte_length": envelope.prompt_byte_length,
    }


def _context_id_from_audit(payload: dict[str, object]) -> tuple[str, str]:
    audit_bytes = encode_canonical(payload)
    audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()
    return "ctx_" + hashlib.sha256(_CONTEXT_PREFIX + audit_bytes).hexdigest(), audit_sha256


def build_worker_context(
    *,
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
    attempt_id: AttemptId,
    admitted_knowledge: CurrentAdmittedKnowledge,
    knowledge_items: tuple[AdmittedKnowledgeItem, ...],
    replay_observations: tuple[ReplayObservation, ...] = (),
    excluded_refs: tuple[ExcludedKnowledgeRef, ...] = (),
    budget: ContextSizeBudget = ContextSizeBudget(),
) -> WorkerContextRecord:
    validate_intent_candidate(intent)
    validate_accepted_operation_plan(accepted_plan)
    if type(attempt_id) is not AttemptId:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "context attempt must be exact")
    validate_current_admitted_knowledge(admitted_knowledge)
    if type(budget) is not ContextSizeBudget:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "context budget must be exact")
    ContextSizeBudget(**budget.__dict__)
    _validate_bindings(intent, accepted_plan, attempt_id, admitted_knowledge)
    _validate_context_items(
        admitted_knowledge=admitted_knowledge,
        knowledge_items=knowledge_items,
        replay_observations=replay_observations,
        excluded_refs=excluded_refs,
        budget=budget,
    )
    body = _delivery_body(
        intent=intent,
        accepted_plan=accepted_plan,
        attempt_id=attempt_id,
        admitted_knowledge=admitted_knowledge,
        knowledge_items=knowledge_items,
        replay_observations=replay_observations,
    )
    body_bytes = encode_canonical(body)
    if len(body_bytes) > budget.maximum_body_bytes:
        raise _fail(ContextFailureCode.SIZE_BUDGET_EXCEEDED, "worker context body exceeds budget")
    provisional_envelope = create_worker_delivery_envelope(context_id="ctx_" + "0" * 64, body_bytes=body_bytes)
    audit_shell = WorkerContextRecord(
        schema_version=WORKER_CONTEXT_RECORD_SCHEMA_V1,
        context_id="ctx_" + "0" * 64,
        audit_sha256="0" * 64,
        intent=intent,
        accepted_plan=accepted_plan,
        attempt_id=attempt_id,
        admitted_knowledge=admitted_knowledge,
        knowledge_items=knowledge_items,
        replay_observations=replay_observations,
        excluded_refs=excluded_refs,
        delivery_envelope=provisional_envelope,
    )
    context_id, audit_sha256 = _context_id_from_audit(_audit_payload(audit_shell))
    envelope = create_worker_delivery_envelope(context_id=context_id, body_bytes=body_bytes)
    if len(envelope.prompt_text.encode("utf-8")) > budget.maximum_prompt_bytes:
        raise _fail(ContextFailureCode.SIZE_BUDGET_EXCEEDED, "rendered worker prompt exceeds budget")
    result = WorkerContextRecord(
        schema_version=WORKER_CONTEXT_RECORD_SCHEMA_V1,
        context_id=context_id,
        audit_sha256=audit_sha256,
        intent=intent,
        accepted_plan=accepted_plan,
        attempt_id=attempt_id,
        admitted_knowledge=admitted_knowledge,
        knowledge_items=knowledge_items,
        replay_observations=replay_observations,
        excluded_refs=excluded_refs,
        delivery_envelope=envelope,
    )
    validate_worker_context(result)
    return result


def _validate_bindings(
    intent: IntentCandidate,
    accepted_plan: AcceptedOperationPlan,
    attempt_id: AttemptId,
    admitted_knowledge: CurrentAdmittedKnowledge,
) -> None:
    validate_operation_plan_against_intent(accepted_plan.candidate, intent=intent)
    if accepted_plan.candidate.intent_proposal_id.to_dict() != intent.proposal_id.to_dict():
        raise _fail(ContextFailureCode.TASK_BINDING_MISMATCH, "accepted plan belongs to another intent")
    if type(attempt_id) is not AttemptId:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "context attempt must be exact")
    if admitted_knowledge.envelope is None or admitted_knowledge.envelope.attempt_id != attempt_id:
        raise _fail(ContextFailureCode.AUTHORIZATION_MISMATCH, "current admission belongs to another attempt")


def _validate_context_items(
    *,
    admitted_knowledge: CurrentAdmittedKnowledge,
    knowledge_items: tuple[AdmittedKnowledgeItem, ...],
    replay_observations: tuple[ReplayObservation, ...],
    excluded_refs: tuple[ExcludedKnowledgeRef, ...],
    budget: ContextSizeBudget,
) -> None:
    if type(knowledge_items) is not tuple or type(replay_observations) is not tuple or type(excluded_refs) is not tuple:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "context collections must be tuples")
    if len(knowledge_items) + len(replay_observations) + len(excluded_refs) > budget.maximum_items:
        raise _fail(ContextFailureCode.SIZE_BUDGET_EXCEEDED, "context item count exceeds budget")
    admitted_keys = {_ref_key(item) for item in admitted_knowledge.subject_refs}
    delivered_keys: set[tuple[str, str, str, str, int, str]] = set()
    item_ids: set[str] = set()
    for item in knowledge_items:
        if type(item) is not AdmittedKnowledgeItem:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "knowledge item must be exact")
        AdmittedKnowledgeItem(**item.__dict__)
        key = _ref_key(item.ref)
        if key not in admitted_keys:
            raise _fail(ContextFailureCode.KNOWLEDGE_NOT_ADMITTED, "worker item was not admitted for current use")
        if key in delivered_keys or item.item_id in item_ids:
            raise _fail(ContextFailureCode.DUPLICATE, "worker knowledge item is duplicated")
        if len(item.content) > budget.maximum_item_bytes:
            raise _fail(ContextFailureCode.SIZE_BUDGET_EXCEEDED, "worker knowledge item exceeds budget")
        delivered_keys.add(key)
        item_ids.add(item.item_id)
    replay_ids: set[str] = set()
    for item in replay_observations:
        validate_replay_observation(item)
        key = item.observation_id.value
        if key in replay_ids:
            raise _fail(ContextFailureCode.DUPLICATE, "replay observation is duplicated")
        replay_ids.add(key)
    excluded_keys: set[tuple[str, str, str, str, int, str]] = set()
    for item in excluded_refs:
        if type(item) is not ExcludedKnowledgeRef:
            raise _fail(ContextFailureCode.TYPE_MISMATCH, "excluded ref must be exact")
        ExcludedKnowledgeRef(**item.__dict__)
        key = _ref_key(item.ref)
        if key in excluded_keys or key in delivered_keys:
            raise _fail(ContextFailureCode.DUPLICATE, "ref is duplicated or both delivered and excluded")
        excluded_keys.add(key)


def validate_worker_context(value: WorkerContextRecord) -> None:
    if type(value) is not WorkerContextRecord:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "worker context must be exact")
    if value.schema_version != WORKER_CONTEXT_RECORD_SCHEMA_V1:
        raise _fail(ContextFailureCode.UNKNOWN_SCHEMA, "worker context schema is unknown")
    validate_intent_candidate(value.intent)
    validate_accepted_operation_plan(value.accepted_plan)
    if type(value.attempt_id) is not AttemptId:
        raise _fail(ContextFailureCode.TYPE_MISMATCH, "context attempt must be exact")
    validate_current_admitted_knowledge(value.admitted_knowledge)
    validate_worker_delivery_envelope(value.delivery_envelope)
    _validate_bindings(value.intent, value.accepted_plan, value.attempt_id, value.admitted_knowledge)
    _validate_context_items(
        admitted_knowledge=value.admitted_knowledge,
        knowledge_items=value.knowledge_items,
        replay_observations=value.replay_observations,
        excluded_refs=value.excluded_refs,
        budget=ContextSizeBudget(
            maximum_items=max(
                1,
                len(value.knowledge_items)
                + len(value.replay_observations)
                + len(value.excluded_refs),
            ),
            maximum_item_bytes=max(1, *(len(item.content) for item in value.knowledge_items)),
            maximum_body_bytes=max(1, value.delivery_envelope.body_byte_length),
            maximum_prompt_bytes=max(1, value.delivery_envelope.prompt_byte_length),
        ),
    )
    expected_body = encode_canonical(
        _delivery_body(
            intent=value.intent,
            accepted_plan=value.accepted_plan,
            attempt_id=value.attempt_id,
            admitted_knowledge=value.admitted_knowledge,
            knowledge_items=value.knowledge_items,
            replay_observations=value.replay_observations,
        )
    )
    if expected_body != value.delivery_envelope.body_bytes:
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "delivery body differs from typed context")
    expected_id, expected_audit = _context_id_from_audit(_audit_payload(value))
    if value.context_id != expected_id or value.audit_sha256 != expected_audit:
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "worker context identity does not match audit payload")
    if value.delivery_envelope.context_id != value.context_id:
        raise _fail(ContextFailureCode.IDENTITY_MISMATCH, "delivery envelope belongs to another context")
