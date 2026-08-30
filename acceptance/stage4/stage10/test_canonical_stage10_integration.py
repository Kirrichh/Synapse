from __future__ import annotations

from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage10.influence import (
    AcknowledgementKind,
    ConsumptionStage,
    WorkerConsumptionAcknowledgement,
    assess_context_influence,
)


def test_canonical_stage10_path_persists_revalidates_dispatches_and_separates_influence(
    stage10_delivery_world,
) -> None:
    world = stage10_delivery_world
    context = world.context
    receipt = world.dispatch.delivery_receipt
    delivered_body = decode_canonical(context.delivery_envelope.body_bytes)

    assert delivered_body["task_policy"]["allowed_scope"] == list(
        world.accepted_plan.candidate.allowed_scope.entries
    )
    assert "excluded_refs" not in delivered_body

    acknowledgement = WorkerConsumptionAcknowledgement(
        worker_actor=world.case.world.evaluator.consumer_actor,
        context_id=context.context_id,
        delivery_receipt_sha256=receipt.receipt_sha256,
        kind=AcknowledgementKind.REFERENCED,
        referenced_item_ids=("admitted-subject",),
    )
    assessment = assess_context_influence(
        receipt=receipt,
        acknowledgement=acknowledgement,
    )

    assert assessment.stage is ConsumptionStage.REFERENCED_CLAIMED
