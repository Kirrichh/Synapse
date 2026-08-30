"""Translate one sealed retrieval verdict into a bound Stage 10 selection."""

from __future__ import annotations

from ..admission import (
    GateDecision,
    GateKind,
    gate_decision_ref,
    validate_gate_decision,
)
from ..point_of_use import (
    CurrentAdmittedKnowledge,
    validate_current_admitted_knowledge,
)
from .context import (
    ContextFailureCode,
    ContextKnowledgeSelection,
    ContextViolation,
    _make_context_knowledge_selection,
)


ADAPTER_PRIVATE_SEAM = {
    "synapse.experiments.gold.stage10.context": frozenset(
        {"_make_context_knowledge_selection"}
    ),
}


def _fail(code: ContextFailureCode, detail: str) -> ContextViolation:
    return ContextViolation(code, detail)


def context_knowledge_selection(
    *,
    retrieval_decision: GateDecision,
    admitted_knowledge: CurrentAdmittedKnowledge,
) -> ContextKnowledgeSelection:
    """Bind the retrieval decision already present in point-of-use lineage."""

    try:
        validate_gate_decision(retrieval_decision)
        validate_current_admitted_knowledge(admitted_knowledge)
    except (TypeError, ValueError) as exc:
        raise _fail(
            ContextFailureCode.TYPE_MISMATCH,
            "knowledge selection requires sealed retrieval and point-of-use records",
        ) from exc

    if retrieval_decision.gate_kind is not GateKind.RETRIEVAL:
        raise _fail(
            ContextFailureCode.KNOWLEDGE_NOT_ADMITTED,
            "knowledge selection requires a retrieval gate decision",
        )
    if not retrieval_decision.admitted:
        raise _fail(
            ContextFailureCode.KNOWLEDGE_NOT_ADMITTED,
            "a blocked retrieval decision cannot select knowledge",
        )
    if retrieval_decision.frozen_candidate_set_ref is None:
        raise _fail(
            ContextFailureCode.KNOWLEDGE_NOT_ADMITTED,
            "retrieval decision does not bind a frozen candidate set",
        )

    if (
        retrieval_decision.consumer_context_ref is None
        or retrieval_decision.consumer_context_ref.to_dict()
        != admitted_knowledge.consumer_context_ref.to_dict()
        or retrieval_decision.boundary_ref is None
        or retrieval_decision.boundary_ref.to_dict()
        != admitted_knowledge.boundary_ref.to_dict()
        or retrieval_decision.policy_version != admitted_knowledge.policy_version
    ):
        raise _fail(
            ContextFailureCode.AUTHORIZATION_MISMATCH,
            "retrieval decision bindings differ from current admitted knowledge",
        )

    decision_subjects = {
        (
            item.kind.value,
            item.ref_id,
            item.schema_id,
            item.sha256,
            item.byte_length,
            item.media_type,
        )
        for item in retrieval_decision.subject_refs
    }
    admitted_subjects = {
        (
            item.kind.value,
            item.ref_id,
            item.schema_id,
            item.sha256,
            item.byte_length,
            item.media_type,
        )
        for item in admitted_knowledge.subject_refs
    }
    if not admitted_subjects or not admitted_subjects <= decision_subjects:
        raise _fail(
            ContextFailureCode.AUTHORIZATION_MISMATCH,
            "current admitted knowledge is outside the retrieval candidate set",
        )

    decision_ref = gate_decision_ref(retrieval_decision).to_dict()
    if not any(
        item.to_dict() == decision_ref
        for item in admitted_knowledge.gate_chain_decision_refs
    ):
        raise _fail(
            ContextFailureCode.AUTHORIZATION_MISMATCH,
            "retrieval decision is absent from point-of-use gate lineage",
        )

    return _make_context_knowledge_selection(
        candidate_refs=retrieval_decision.subject_refs,
        admitted_refs=admitted_knowledge.subject_refs,
        consumer_context_ref=admitted_knowledge.consumer_context_ref,
        boundary_ref=admitted_knowledge.boundary_ref,
        frozen_candidate_set_ref=retrieval_decision.frozen_candidate_set_ref,
    )
