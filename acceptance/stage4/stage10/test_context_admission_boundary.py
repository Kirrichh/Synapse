from __future__ import annotations

import hashlib

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import ActorIdentity
from synapse.experiments.gold.stage10.context import (
    AdmittedKnowledgeItem,
    ContextFailureCode,
    ContextViolation,
    ExcludedKnowledgeRef,
    ExclusionReason,
    build_worker_context,
)
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage10.retrieval_adapter import (
    context_knowledge_selection,
)


def _rebuild(
    world,
    *,
    knowledge_items,
    excluded_refs=(),
    replay_observations=(),
):
    current = world.context
    return build_worker_context(
        intent=current.intent,
        accepted_plan=current.accepted_plan,
        attempt_id=current.attempt_id,
        admitted_knowledge=current.admitted_knowledge,
        knowledge_selection=current.knowledge_selection,
        knowledge_items=knowledge_items,
        replay_observations=replay_observations,
        excluded_refs=excluded_refs,
    )


def _excluded(selection) -> tuple[ExcludedKnowledgeRef, ...]:
    return tuple(
        ExcludedKnowledgeRef(
            ref=item,
            reason=ExclusionReason.NOT_SELECTED_FOR_TASK,
        )
        for item in selection.candidate_refs
    )


def _permissive_gate_controller(case):
    return A.configure_gate_controller(
        declaration=case.controller.declaration,
        policy_version=case.controller.policy_version,
        run_id=case.controller.run_id,
        attempt_id=case.controller.attempt_id,
        repository_revision=case.controller.repository_revision,
        environment_profile_id=case.controller.environment_profile_id,
        trusted_clock=lambda: case.now[0],
        taint_probe=lambda _subject: A.TaintFinding(
            consumable=True,
            chain_complete=True,
            quarantined=False,
            blocks_publication=False,
        ),
        provenance_probe=lambda _subject: True,
        lifecycle_probe=lambda _subject: True,
        compatibility_probe=lambda subject, consumer: A.CompatibilityFinding(
            compatible=True,
            evidence_complete=True,
            drifted=False,
            conflicts_unresolved=False,
            subject_ref=subject,
            consumer_context_ref=consumer,
        ),
        boundary_probe=lambda _boundary: True,
        grant_probe=lambda: A.GrantEnvelope(
            scopes=("repo:x",),
            capabilities=("read",),
            oracles=("swebench",),
            policy_version=case.controller.policy_version,
        ),
        head_reader=lambda: {},
        producer_actor=ActorIdentity("stage10-selection-producer"),
        retriever_actor=ActorIdentity("stage10-selection-retriever"),
        consumer_actor=ActorIdentity("stage10-selection-consumer"),
    )


def _retrieval_decision(
    case,
    *,
    controller,
    subject_refs,
    consumer_context_ref,
    requested,
):
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subject_refs)
    publication = A.evaluate_publication_gate(
        controller,
        subject_refs=subject_refs,
        requested=case.requested,
        predecessor=ingestion,
    )
    return A.evaluate_retrieval_gate(
        controller,
        subject_refs=subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=case.boundary_ref,
        frozen_candidate_set_ref=case.chain.retrieval.frozen_candidate_set_ref,
        requested=requested,
        predecessor=publication,
    )


def test_empty_delivery_is_explicitly_complete_and_does_not_expose_exclusions(
    stage10_delivery_world,
) -> None:
    selection = stage10_delivery_world.context.knowledge_selection
    context = _rebuild(
        stage10_delivery_world,
        knowledge_items=(),
        excluded_refs=_excluded(selection),
    )
    delivered = decode_canonical(context.delivery_envelope.body_bytes)

    assert context.knowledge_items == ()
    assert delivered["admitted_items"] == []
    assert "candidate_refs" not in delivered["admission"]
    assert "excluded_refs" not in delivered


def test_candidate_omission_and_foreign_exclusion_fail_closed(
    stage10_delivery_world,
) -> None:
    with pytest.raises(ContextViolation) as omitted:
        _rebuild(stage10_delivery_world, knowledge_items=())
    assert omitted.value.failure_code is ContextFailureCode.IDENTITY_MISMATCH

    outside_bytes = b"not a retrieval candidate"
    outside_digest = hashlib.sha256(outside_bytes).hexdigest()
    outside_ref = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=outside_digest,
        schema_id="acceptance.foreign-knowledge/v1",
        sha256=outside_digest,
        byte_length=len(outside_bytes),
        media_type="application/octet-stream",
    )
    with pytest.raises(ContextViolation) as foreign:
        _rebuild(
            stage10_delivery_world,
            knowledge_items=stage10_delivery_world.context.knowledge_items,
            excluded_refs=(
                ExcludedKnowledgeRef(
                    ref=outside_ref,
                    reason=ExclusionReason.REJECTED_BY_GATE,
                ),
            ),
        )
    assert foreign.value.failure_code is ContextFailureCode.KNOWLEDGE_NOT_ADMITTED


def test_nonadmitted_hash_bound_content_cannot_enter_worker_context(
    stage10_delivery_world,
) -> None:
    content = b"hash-bound but not admitted"
    digest = hashlib.sha256(content).hexdigest()
    item = AdmittedKnowledgeItem(
        item_id="foreign-item",
        ref=HashBoundRef(
            kind=RefKind.ARTIFACT,
            ref_id=digest,
            schema_id="acceptance.foreign-knowledge/v1",
            sha256=digest,
            byte_length=len(content),
            media_type="application/octet-stream",
        ),
        content=content,
        taint_classes=(),
        failed_hypothesis=False,
    )

    with pytest.raises(ContextViolation) as rejected:
        _rebuild(stage10_delivery_world, knowledge_items=(item,))
    assert rejected.value.failure_code is ContextFailureCode.KNOWLEDGE_NOT_ADMITTED


def test_selection_refuses_non_retrieval_and_blocked_gate_decisions(
    stage10_delivery_world,
) -> None:
    case = stage10_delivery_world.case
    admitted = stage10_delivery_world.context.admitted_knowledge

    with pytest.raises(ContextViolation) as wrong_gate:
        context_knowledge_selection(
            retrieval_decision=case.chain.publication,
            admitted_knowledge=admitted,
        )
    assert wrong_gate.value.failure_code is ContextFailureCode.KNOWLEDGE_NOT_ADMITTED

    blocked = A.evaluate_retrieval_gate(
        case.controller,
        subject_refs=case.subjects,
        consumer_context_ref=case.context_ref,
        boundary_ref=case.boundary_ref,
        frozen_candidate_set_ref=case.chain.retrieval.frozen_candidate_set_ref,
        requested=A.RequestedEnvelope(
            scopes=("repo:x", "repo:y"),
            capabilities=("read",),
            oracles=("swebench",),
        ),
        predecessor=case.chain.publication,
        sequence=30,
    )
    with pytest.raises(ContextViolation) as blocked_gate:
        context_knowledge_selection(
            retrieval_decision=blocked,
            admitted_knowledge=admitted,
        )
    assert blocked_gate.value.failure_code is ContextFailureCode.KNOWLEDGE_NOT_ADMITTED


def test_selection_requires_the_current_point_of_use_gate_lineage(
    stage10_delivery_world,
) -> None:
    case = stage10_delivery_world.case
    admitted = stage10_delivery_world.context.admitted_knowledge
    separately_decided = A.evaluate_retrieval_gate(
        case.controller,
        subject_refs=case.subjects,
        consumer_context_ref=case.context_ref,
        boundary_ref=case.boundary_ref,
        frozen_candidate_set_ref=case.chain.retrieval.frozen_candidate_set_ref,
        requested=case.requested,
        predecessor=case.chain.publication,
        sequence=31,
    )

    with pytest.raises(ContextViolation) as absent:
        context_knowledge_selection(
            retrieval_decision=separately_decided,
            admitted_knowledge=admitted,
        )
    assert absent.value.failure_code is ContextFailureCode.AUTHORIZATION_MISMATCH


def test_selection_refuses_foreign_binding_and_admission_outside_candidates(
    stage10_delivery_world,
) -> None:
    case = stage10_delivery_world.case
    admitted = stage10_delivery_world.context.admitted_knowledge
    controller = _permissive_gate_controller(case)
    foreign_context_bytes = b"foreign consumer context"
    foreign_context_digest = hashlib.sha256(foreign_context_bytes).hexdigest()
    foreign_context = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=foreign_context_digest,
        schema_id="acceptance.foreign-consumer-context/v1",
        sha256=foreign_context_digest,
        byte_length=len(foreign_context_bytes),
        media_type="application/json",
    )
    foreign_binding = _retrieval_decision(
        case,
        controller=controller,
        subject_refs=case.subjects,
        consumer_context_ref=foreign_context,
        requested=case.requested,
    )
    with pytest.raises(ContextViolation) as mismatched_binding:
        context_knowledge_selection(
            retrieval_decision=foreign_binding,
            admitted_knowledge=admitted,
        )
    assert mismatched_binding.value.failure_code is ContextFailureCode.AUTHORIZATION_MISMATCH

    candidate_bytes = b"different retrieval candidate"
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
    other_candidate = HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=candidate_digest,
        schema_id="acceptance.other-retrieval-candidate/v1",
        sha256=candidate_digest,
        byte_length=len(candidate_bytes),
        media_type="application/json",
    )
    outside_candidates = _retrieval_decision(
        case,
        controller=controller,
        subject_refs=(other_candidate,),
        consumer_context_ref=case.context_ref,
        requested=case.requested,
    )
    with pytest.raises(ContextViolation) as outside:
        context_knowledge_selection(
            retrieval_decision=outside_candidates,
            admitted_knowledge=admitted,
        )
    assert outside.value.failure_code is ContextFailureCode.AUTHORIZATION_MISMATCH
