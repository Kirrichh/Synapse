from __future__ import annotations

from pathlib import Path

from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    canonicalize_stage4_payload,
)
from synapse.experiments.gold.persistence import store_transaction
from synapse.experiments.gold.stage10.context import (
    AdmittedKnowledgeItem,
    build_worker_context,
)
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage10.influence import (
    AcknowledgementKind,
    ConsumptionStage,
    WorkerConsumptionAcknowledgement,
    assess_context_influence,
)
from synapse.experiments.gold.stage10.plan_revalidation import (
    CurrentPlanState,
    authorize_first_side_effect,
)
from synapse.experiments.gold.stage10.record_store import FileStage10RecordStore
from synapse.experiments.gold.stage10.worker_context_adapter import Stage10WorkerContextAdapter
from synapse.experiments.gold.stage10.worker_transport import (
    WorkerCandidateReport,
    WorkerCandidateResult,
    WorkerCandidateStatus,
    WorkerCandidateUsage,
    WorkerDeliveryEvidence,
    WorkerDeliveryStatus,
    WorkerTokenStatus,
)
from tests.gold_store_fence import fence_for
from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

from acceptance.stage4.stage10._builders import plan_world


class _ObservedTransport:
    def run(self, worktree_path, invocation):
        del worktree_path
        return WorkerCandidateResult(
            status=WorkerCandidateStatus.NO_PATCH,
            diff_text=None,
            touched_files=(),
            usage=WorkerCandidateUsage(
                token_status=WorkerTokenStatus.UNAVAILABLE,
                input_tokens=None,
                output_tokens=None,
                thinking_tokens=None,
                total_tokens=None,
                thinking_included=False,
            ),
            diagnostics={},
            report=WorkerCandidateReport(summary="worker claims it used the context"),
            delivery_evidence=WorkerDeliveryEvidence(
                invocation_id=invocation.invocation_id,
                context_id=invocation.context_id,
                payload_sha256=invocation.payload_sha256,
                payload_byte_length=invocation.payload_byte_length,
                envelope_sha256=invocation.envelope_sha256,
                status=WorkerDeliveryStatus.PROCESS_STARTED,
                transport_name="acceptance-observed-transport/v1",
            ),
        )


def test_canonical_stage10_path_persists_revalidates_dispatches_and_separates_influence(
    tmp_path: Path,
) -> None:
    case = production_point_of_use_case(tmp_path / "point-of-use")
    intent, _plan, _policy, authority, _decision, accepted = plan_world(
        snapshot_ref=case.boundary.manifest_ref
    )
    stage10_root = tmp_path / "stage10-store"
    stage10_root.mkdir()
    store_fence = fence_for(stage10_root)
    store = FileStage10RecordStore(stage10_root / "records")
    with store_transaction(store_fence) as ticket:
        plan_persistence = store.persist_plan_bundle(
            intent=intent,
            accepted_plan=accepted,
            ticket=ticket,
        )

    admitted = P.admit_for_use_now(
        case.handle,
        binding=case.binding,
        chain=case.chain,
        evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
    )
    descriptor = case.supported[0][1]
    subject_bytes = canonicalize_stage4_payload(
        {
            "content_key": descriptor.content_key.value,
            "manifest_id": descriptor.manifest_id.value,
        },
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    knowledge_item = AdmittedKnowledgeItem(
        item_id="admitted-subject",
        ref=case.subject,
        content=subject_bytes,
        taint_classes=(),
        failed_hypothesis=False,
    )
    context = build_worker_context(
        intent=intent,
        accepted_plan=accepted,
        attempt_id=admitted.envelope.attempt_id,
        admitted_knowledge=admitted,
        knowledge_items=(knowledge_item,),
    )
    with store_transaction(store_fence) as ticket:
        context_persistence = store.persist_worker_context(context, ticket=ticket)

    current = CurrentPlanState(
        repository_revision_sha256=intent.repository_revision_sha256,
        knowledge_snapshot_ref=intent.knowledge_snapshot_ref,
        policy_sha256=authority.policy.sha256,
        admitted_knowledge=admitted,
        compatibility_revalidation=case.compatibility_probe.records[-1],
    )
    authorization = authorize_first_side_effect(
        accepted_plan=accepted,
        intent=intent,
        authority=authority,
        attempt_id=context.attempt_id,
        current_state_reader=lambda: current,
        admission_freshness_validator=lambda value: P.require_current_point_of_use_evidence(
            value,
            binding=case.binding,
        ),
        context_id=context.context_id,
        context_audit_sha256=context.audit_sha256,
        delivery_envelope_sha256=context.delivery_envelope.envelope_sha256,
        plan_bundle_sha256=plan_persistence.bundle_sha256,
    )
    dispatch = Stage10WorkerContextAdapter(_ObservedTransport()).dispatch(
        worktree_path=tmp_path,
        context=context,
        persistence=context_persistence,
        plan_persistence=plan_persistence,
        authorization=authorization,
    )
    with store_transaction(store_fence) as ticket:
        store.persist_delivery_receipt(dispatch.delivery_receipt, ticket=ticket)

    delivered_body = decode_canonical(context.delivery_envelope.body_bytes)
    assert delivered_body["task_policy"]["allowed_scope"] == list(accepted.candidate.allowed_scope.entries)
    assert "excluded_refs" not in delivered_body
    acknowledgement = WorkerConsumptionAcknowledgement(
        worker_actor=case.world.evaluator.consumer_actor,
        context_id=context.context_id,
        delivery_receipt_sha256=dispatch.delivery_receipt.receipt_sha256,
        kind=AcknowledgementKind.REFERENCED,
        referenced_item_ids=("admitted-subject",),
    )
    assessment = assess_context_influence(
        receipt=dispatch.delivery_receipt,
        acknowledgement=acknowledgement,
    )
    assert assessment.stage is ConsumptionStage.REFERENCED_CLAIMED
