"""Build exact durable prefixes at Stage 11 external-effect boundaries.

The helpers execute the real preparation, subprocess delivery, and C1 owners.
They stop only by omitting the next run-record publication, which models a
process disappearing after an already durable boundary without introducing a
second candidate or recovery implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from synapse.experiments.gold.runner.attempt_knowledge_store import basis_record_key
from synapse.experiments.gold.runner.c1_boundary import (
    C1AttemptExecution,
    c1_authority_receipt_bytes,
    c1_authority_receipt_ref,
    run_c1_attempt,
)
from synapse.experiments.gold.runner.completed_delivery_codec import (
    completed_worker_delivery_bytes,
    completed_worker_delivery_ref,
)
from synapse.experiments.gold.runner.delivery import (
    CompletedWorkerDelivery,
    PreparedWorkerDelivery,
    dispatch_prepared_attempt,
    prepare_attempt_delivery,
)
from synapse.experiments.gold.runner.models import AttemptPhaseRefs, GoldAttemptContext
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.run_progress import (
    AttemptProgress,
    AttemptProgressPhase,
    progress_key,
)
from synapse.experiments.gold.runner.run_recovery import PendingRunRecord

from acceptance.stage4.stage11._builders import RunWorld


@dataclass
class DurableAttemptPrefix:
    """Real objects and last publication for one deliberately stopped attempt."""

    world: RunWorld
    prepared: PreparedWorkerDelivery
    context: GoldAttemptContext
    latest: AttemptProgress | None = None
    delivery: CompletedWorkerDelivery | None = None
    c1_execution: C1AttemptExecution | None = None


def _durable_phase_refs(
    prepared: PreparedWorkerDelivery,
    *,
    plan_semantic_sha256: str,
) -> AttemptPhaseRefs:
    """Promote delivery-internal refs to the complete V4 durable context shape."""

    refs = prepared.phase_refs
    return AttemptPhaseRefs(
        knowledge_snapshot_ref=refs.knowledge_snapshot_ref,
        retrieval_ref=refs.retrieval_ref,
        replay_ref=refs.replay_ref,
        intent_ref=refs.intent_ref,
        plan_ref=refs.plan_ref,
        worker_context_id=refs.worker_context_id,
        worker_context_audit_sha256=refs.worker_context_audit_sha256,
        knowledge_basis_sha256=refs.knowledge_basis_sha256,
        plan_semantic_sha256=plan_semantic_sha256,
    )


def begin_attempt(world: RunWorld) -> DurableAttemptPrefix:
    """Persist manifest, basis and context after real cross-stage preparation."""

    inputs = world.attempt_inputs.prepare(
        manifest=world.manifest,
        attempt_index=1,
        previous_context=None,
    )
    prepared = prepare_attempt_delivery(
        manifest=world.manifest,
        attempt_index=1,
        inputs=inputs,
        record_store=world.stage10_composition.record_store,
        worker_adapter=world.stage10_composition.worker_adapter,
    )
    if type(prepared) is not PreparedWorkerDelivery:
        raise RuntimeError("crash prefix requires an admitted worker delivery")
    basis = prepared.upstream.knowledge_basis
    if basis is None:
        raise RuntimeError("crash prefix requires the attempt's durable knowledge basis")
    context = GoldAttemptContext.create(
        manifest=world.manifest,
        attempt_index=1,
        phase_refs=_durable_phase_refs(
            prepared,
            plan_semantic_sha256=inputs.plan_semantic_sha256,
        ),
    )
    with world.composition.record_recovery.session() as session:
        session.put_many(
            (
                PendingRunRecord(
                    kind=RecordKind.MANIFEST,
                    key="manifest",
                    payload=world.manifest.stored_dict(),
                ),
                PendingRunRecord(
                    kind=RecordKind.ATTEMPT_KNOWLEDGE_BASIS,
                    key=basis_record_key(1),
                    payload=basis.payload(),
                ),
                PendingRunRecord(
                    kind=RecordKind.ATTEMPT_CONTEXT,
                    key="1",
                    payload=context.stored_dict(),
                ),
            )
        )
    return DurableAttemptPrefix(world=world, prepared=prepared, context=context)


def publish_delivery_started(prefix: DurableAttemptPrefix) -> AttemptProgress:
    return _publish(prefix, AttemptProgressPhase.DELIVERY_STARTED)


def dispatch_and_publish_worker(prefix: DurableAttemptPrefix) -> CompletedWorkerDelivery:
    """Run the external worker once and durably bind its exact completed bytes."""

    delivery = dispatch_prepared_attempt(
        prepared=prefix.prepared,
        record_store=prefix.world.stage10_composition.record_store,
        worker_adapter=prefix.world.stage10_composition.worker_adapter,
    )
    prefix.delivery = delivery
    _publish(
        prefix,
        AttemptProgressPhase.WORKER_COMPLETED,
        payload_ref=completed_worker_delivery_ref(delivery),
        payload_bytes=completed_worker_delivery_bytes(delivery),
    )
    return delivery


def publish_c1_started(prefix: DurableAttemptPrefix) -> AttemptProgress:
    if prefix.delivery is None:
        raise RuntimeError("C1 cannot start without a completed worker delivery")
    return _publish(prefix, AttemptProgressPhase.C1_STARTED)


def invoke_c1_without_completion_checkpoint(
    prefix: DurableAttemptPrefix,
) -> C1AttemptExecution:
    """Let C1 publish its authority row, but omit the controller checkpoint."""

    if prefix.delivery is None:
        raise RuntimeError("C1 cannot run without a completed worker delivery")
    execution = run_c1_attempt(
        prefix.world.boundary,
        gold_run_id=prefix.world.manifest.gold_run_id,
        attempt_id=prefix.context.attempt_id.value,
        delivery=prefix.delivery,
        run_root=prefix.world.run_root,
    )
    prefix.c1_execution = execution
    return execution


def publish_c1_completed(prefix: DurableAttemptPrefix) -> AttemptProgress:
    if prefix.c1_execution is None:
        raise RuntimeError("C1 completion requires its durable authority row")
    authority = prefix.c1_execution.authority
    return _publish(
        prefix,
        AttemptProgressPhase.C1_COMPLETED,
        payload_ref=c1_authority_receipt_ref(authority),
        payload_bytes=c1_authority_receipt_bytes(authority),
    )


def _publish(
    prefix: DurableAttemptPrefix,
    phase: AttemptProgressPhase,
    *,
    payload_ref=None,
    payload_bytes=None,
) -> AttemptProgress:
    progress = AttemptProgress.create(
        manifest=prefix.world.manifest,
        context=prefix.context,
        phase=phase,
        predecessor=prefix.latest,
        payload_ref=payload_ref,
        payload_bytes=payload_bytes,
    )
    with prefix.world.composition.record_recovery.session() as session:
        session.put(
            PendingRunRecord(
                kind=RecordKind.ATTEMPT_PROGRESS,
                key=progress_key(1, phase),
                payload=progress.stored_dict(),
            )
        )
    prefix.latest = progress
    return progress
