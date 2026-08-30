from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.stage10.delivery_verification import (
    DeliveryFailureCode,
    DeliveryReceipt,
    DeliveryViolation,
    validate_delivery_receipt,
    verify_delivery,
)
from synapse.experiments.gold.stage10.influence import (
    AcknowledgementKind,
    ConsumptionStage,
    InfluenceAssessment,
    PlatformInfluenceEvidence,
    WorkerConsumptionAcknowledgement,
    assess_context_influence,
    validate_influence_assessment,
)
from synapse.experiments.gold.stage10.worker_transport import WorkerDeliveryStatus


def _acknowledgement(world, kind: AcknowledgementKind) -> WorkerConsumptionAcknowledgement:
    return WorkerConsumptionAcknowledgement(
        worker_actor=world.case.world.evaluator.consumer_actor,
        context_id=world.context.context_id,
        delivery_receipt_sha256=world.dispatch.delivery_receipt.receipt_sha256,
        kind=kind,
        referenced_item_ids=("admitted-subject",)
        if kind is AcknowledgementKind.REFERENCED
        else (),
    )


def _platform_evidence(
    world,
    acknowledgement: WorkerConsumptionAcknowledgement,
) -> PlatformInfluenceEvidence:
    digest = hashlib.sha256(b"stage10-influence").hexdigest()
    return PlatformInfluenceEvidence(
        context_id=world.context.context_id,
        delivery_receipt_sha256=world.dispatch.delivery_receipt.receipt_sha256,
        worker_actor=acknowledgement.worker_actor,
        observer_actor=ActorIdentity("independent-platform-observer"),
        output_artifact_ref=HashBoundRef(
            kind=RefKind.ARTIFACT,
            ref_id=digest,
            schema_id="acceptance.stage10.output/v1",
            sha256=digest,
            byte_length=1,
            media_type="application/octet-stream",
        ),
        evidence_ref=HashBoundRef(
            kind=RefKind.SOURCE_EVIDENCE,
            ref_id=digest,
            schema_id="acceptance.stage10.influence/v1",
            sha256=digest,
            byte_length=1,
            media_type="application/json",
        ),
    )


def test_delivery_store_accepts_only_a_verified_factory_receipt(stage10_delivery_world) -> None:
    world = stage10_delivery_world
    receipt = world.dispatch.delivery_receipt
    validate_delivery_receipt(receipt)
    assert receipt.delivery_status is WorkerDeliveryStatus.PROCESS_STARTED
    with pytest.raises(TypeError):
        DeliveryReceipt()

    forged = object.__new__(DeliveryReceipt)
    for name, item in receipt.__dict__.items():
        if name != "_trusted_seal":
            object.__setattr__(forged, name, item)
    with store_transaction(world.store_fence) as ticket:
        with pytest.raises(ValueError):
            world.store.persist_delivery_receipt(forged, ticket=ticket)


def test_delivery_refuses_a_foreign_attempt_before_minting_receipt(
    stage10_delivery_world,
) -> None:
    world = stage10_delivery_world
    foreign_invocation = replace(
        world.dispatch.invocation,
        attempt_id="foreign-attempt",
    )

    with pytest.raises(DeliveryViolation) as raised:
        verify_delivery(
            context=world.context,
            invocation=foreign_invocation,
            evidence=world.dispatch.worker_result.delivery_evidence,
        )

    assert raised.value.failure_code is DeliveryFailureCode.INVOCATION_MISMATCH


def test_influence_stage_is_derived_from_exact_bound_evidence(stage10_delivery_world) -> None:
    world = stage10_delivery_world
    receipt = world.dispatch.delivery_receipt
    parsed_ack = _acknowledgement(world, AcknowledgementKind.PARSED)
    referenced_ack = _acknowledgement(world, AcknowledgementKind.REFERENCED)

    assert assess_context_influence(receipt=receipt).stage is ConsumptionStage.DELIVERED
    assert assess_context_influence(
        receipt=receipt,
        acknowledgement=parsed_ack,
    ).stage is ConsumptionStage.PARSED_CLAIMED
    assert assess_context_influence(
        receipt=receipt,
        acknowledgement=referenced_ack,
    ).stage is ConsumptionStage.REFERENCED_CLAIMED
    assert assess_context_influence(
        receipt=receipt,
        acknowledgement=referenced_ack,
        platform_evidence=_platform_evidence(world, referenced_ack),
    ).stage is ConsumptionStage.INFLUENCED_PROVEN

    wrong_context = WorkerConsumptionAcknowledgement(
        worker_actor=referenced_ack.worker_actor,
        context_id="ctx_" + "f" * 64,
        delivery_receipt_sha256=receipt.receipt_sha256,
        kind=AcknowledgementKind.REFERENCED,
        referenced_item_ids=("admitted-subject",),
    )
    with pytest.raises(ValueError):
        assess_context_influence(receipt=receipt, acknowledgement=wrong_context)


def test_influence_store_rejects_changed_stage_and_unsealed_assessment(
    stage10_delivery_world,
) -> None:
    world = stage10_delivery_world
    assessment = assess_context_influence(
        receipt=world.dispatch.delivery_receipt,
        acknowledgement=_acknowledgement(world, AcknowledgementKind.REFERENCED),
    )
    changed_stage = object.__new__(InfluenceAssessment)
    for name, item in assessment.__dict__.items():
        object.__setattr__(changed_stage, name, item)
    object.__setattr__(changed_stage, "stage", ConsumptionStage.DELIVERED)
    with pytest.raises(ValueError):
        validate_influence_assessment(changed_stage)
    with pytest.raises(TypeError):
        InfluenceAssessment()

    forged = object.__new__(InfluenceAssessment)
    for name, item in assessment.__dict__.items():
        if name != "_trusted_seal":
            object.__setattr__(forged, name, item)
    with store_transaction(world.store_fence) as ticket:
        with pytest.raises(ValueError):
            world.store.persist_influence_assessment(forged, ticket=ticket)
