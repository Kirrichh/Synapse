"""Cross-phase authority coherence for one immutable Gold attempt."""

from __future__ import annotations

from synapse.experiments.gold.canonicalization import HashBoundRef

from .c1_boundary import C1AuthorityReceipt, require_c1_authority_receipt
from .delivery import (
    AttemptDeliveryRefusal,
    AttemptDeliveryUnavailable,
    CompletedWorkerDelivery,
    require_attempt_delivery_refusal,
    require_attempt_delivery_unavailable,
    require_completed_worker_delivery,
)
from .models import GoldAttemptContext, GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _same_ref(left: HashBoundRef, right: HashBoundRef) -> bool:
    return left.to_dict() == right.to_dict()


def require_delivery_failure_authority(
    *,
    context: GoldAttemptContext,
    failure: AttemptDeliveryRefusal | AttemptDeliveryUnavailable,
) -> AttemptDeliveryRefusal | AttemptDeliveryUnavailable:
    """Bind a terminal delivery failure to its exact persisted context."""

    checked = (
        require_attempt_delivery_refusal(failure)
        if type(failure) is AttemptDeliveryRefusal
        else require_attempt_delivery_unavailable(failure)
    )
    refs = context.phase_refs
    upstream = checked.upstream
    if (
        refs.worker_context_id is not None
        or refs.worker_context_audit_sha256 is not None
        or not _same_ref(refs.knowledge_snapshot_ref, upstream.knowledge_snapshot_ref)
        or not _same_ref(refs.retrieval_ref, upstream.retrieval_ref)
        or not _same_ref(refs.replay_ref, upstream.replay_ref)
        or not _same_ref(refs.intent_ref, upstream.intent_ref)
        or not _same_ref(refs.plan_ref, upstream.plan_ref)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "delivery failure differs from the durable attempt context",
        )
    return checked


def require_completed_delivery_authority(
    *,
    context: GoldAttemptContext,
    completed: CompletedWorkerDelivery,
) -> CompletedWorkerDelivery:
    """Bind a completed worker delivery to its exact persisted context."""

    checked = require_completed_worker_delivery(completed)
    refs = context.phase_refs
    if (
        checked.invocation.attempt_id != context.attempt_id.value
        or refs.worker_context_id != checked.worker_context_id
        or refs.worker_context_audit_sha256 != checked.worker_context_audit_sha256
        or not _same_ref(refs.knowledge_snapshot_ref, checked.upstream.knowledge_snapshot_ref)
        or not _same_ref(refs.retrieval_ref, checked.upstream.retrieval_ref)
        or not _same_ref(refs.replay_ref, checked.upstream.replay_ref)
        or not _same_ref(refs.intent_ref, checked.upstream.intent_ref)
        or not _same_ref(refs.plan_ref, checked.upstream.plan_ref)
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "completed worker delivery differs from the durable attempt context",
        )
    return checked


def require_c1_receipt_authority(
    *,
    manifest: GoldRunManifest,
    context: GoldAttemptContext,
    worker_delivery: CompletedWorkerDelivery,
    receipt: C1AuthorityReceipt,
) -> C1AuthorityReceipt:
    """Bind C1 authority to the manifest, context, and exact worker delivery."""

    require_completed_delivery_authority(
        context=context,
        completed=worker_delivery,
    )
    checked = require_c1_authority_receipt(receipt)
    if (
        checked.gold_run_id != manifest.gold_run_id
        or checked.attempt_id != context.attempt_id.value
    ):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "C1 authority receipt belongs to another run attempt",
        )
    return checked


__all__ = [
    "require_c1_receipt_authority",
    "require_completed_delivery_authority",
    "require_delivery_failure_authority",
]
