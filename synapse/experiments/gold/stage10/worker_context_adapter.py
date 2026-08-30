"""Narrow adapter from a persisted Stage 10 context to a worker transport."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
import hashlib

from .context import (
    ContextPersistenceEvidence,
    WorkerContextRecord,
    validate_context_persistence_evidence,
    validate_worker_context,
)
from .context_codec import encode_canonical
from .delivery_verification import DeliveryReceipt, verify_delivery
from .plan_revalidation import (
    PlanPersistenceEvidence,
    SideEffectAuthorization,
    validate_plan_persistence_evidence,
    validate_side_effect_authorization,
)
from .worker_transport import WorkerCandidateResult, WorkerInvocation


@runtime_checkable
class WorkerTransportPort(Protocol):
    def run(
        self,
        worktree_path: str | Path,
        invocation: WorkerInvocation,
    ) -> WorkerCandidateResult: ...


@dataclass(frozen=True)
class WorkerDispatchResult:
    invocation: WorkerInvocation
    worker_result: WorkerCandidateResult
    delivery_receipt: DeliveryReceipt


def create_worker_invocation(
    *,
    context: WorkerContextRecord,
    persistence: ContextPersistenceEvidence,
    plan_persistence: PlanPersistenceEvidence,
    authorization: SideEffectAuthorization,
) -> WorkerInvocation:
    validate_worker_context(context)
    validate_context_persistence_evidence(persistence, context=context)
    validate_plan_persistence_evidence(
        plan_persistence,
        intent=context.intent,
        accepted_plan=context.accepted_plan,
    )
    validate_side_effect_authorization(authorization)
    if (
        authorization.context_id != context.context_id
        or authorization.context_audit_sha256 != context.audit_sha256
        or authorization.delivery_envelope_sha256
        != context.delivery_envelope.envelope_sha256
        or authorization.plan_bundle_sha256 != plan_persistence.bundle_sha256
        or authorization.attempt_id != context.attempt_id
        or authorization.accepted_plan_id != context.accepted_plan.accepted_plan_id
        or authorization.admitted_knowledge_id != context.admitted_knowledge.knowledge_id
    ):
        raise ValueError("fresh authorization differs from persisted worker context")
    envelope = context.delivery_envelope
    identity_payload = {
        "attempt_id": context.attempt_id.to_dict(),
        "context_id": context.context_id,
        "envelope_sha256": envelope.envelope_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "audit_store_ref": persistence.audit_store_ref.to_dict(),
        "delivery_store_ref": persistence.delivery_store_ref.to_dict(),
        "plan_bundle_sha256": plan_persistence.bundle_sha256,
    }
    invocation_id = "inv_" + hashlib.sha256(encode_canonical(identity_payload)).hexdigest()
    return WorkerInvocation(
        invocation_id=invocation_id,
        attempt_id=authorization.attempt_id.value,
        context_id=context.context_id,
        payload_text=envelope.prompt_text,
        payload_sha256=envelope.prompt_sha256,
        payload_byte_length=envelope.prompt_byte_length,
        envelope_sha256=envelope.envelope_sha256,
        allowed_scope=authorization.allowed_scope,
        capabilities=authorization.capabilities,
    )


class Stage10WorkerContextAdapter:
    """Translate, invoke one configured transport, and verify exact delivery."""

    def __init__(self, transport: WorkerTransportPort) -> None:
        if not isinstance(transport, WorkerTransportPort):
            raise TypeError("transport must implement WorkerTransportPort")
        self._transport = transport

    @property
    def transport_binding(self) -> WorkerTransportPort:
        return self._transport

    def dispatch(
        self,
        *,
        worktree_path: str | Path,
        context: WorkerContextRecord,
        persistence: ContextPersistenceEvidence,
        plan_persistence: PlanPersistenceEvidence,
        authorization: SideEffectAuthorization,
    ) -> WorkerDispatchResult:
        invocation = create_worker_invocation(
            context=context,
            persistence=persistence,
            plan_persistence=plan_persistence,
            authorization=authorization,
        )
        worker_result = self._transport.run(worktree_path, invocation)
        if type(worker_result) is not WorkerCandidateResult:
            raise TypeError("worker transport returned an invalid result")
        if worker_result.delivery_evidence is None:
            raise ValueError("worker transport returned no delivery evidence")
        receipt = verify_delivery(
            context=context,
            invocation=invocation,
            evidence=worker_result.delivery_evidence,
        )
        return WorkerDispatchResult(invocation, worker_result, receipt)
