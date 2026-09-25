"""Legitimacy of admitted learned-habit behaviors over time.

Admission is Gold's: a learned behavior may execute only while its publication
stays in the consumable lifecycle state for its lifecycle context, and only
under the tool binding that context was admitted for. A successor born by the
memory court replaces its predecessor through Gold's own supersession
authority (``SUPERSESSION_REVIEWER``): a SUPERSEDE proposal, the configured
reviewer's decision, and a SUPERSEDED lifecycle record. The court proposes;
it never writes Gold's lifecycle itself. The governing operator ends a
behavior's life the same way with a WITHDRAW decision.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from .admission_journal import FileSnapshotFence
from .canonicalization import HashBoundRef, RefKind
from .contracts import ActorIdentity, LifecycleReasonCode
from .learned_habit_profile import (CONSOLIDATION_JUDGE, LEARNED_HABIT_LIFECYCLE_POLICY_V1, LEARNED_HABIT_PROFILE_V1,
                                    LEARNED_HABIT_VERIFIER)
from .lifecycle import (
    LifecycleAuthorityAction,
    LifecycleContext,
    LifecycleState,
    SupersessionDecision,
    SupersessionDecisionKind,
    configure_lifecycle_authority_evaluator,
    create_lifecycle_authority_proposal,
)
from .persistence import store_transaction
from .source_verification import canonical, source_ref

CONSUMABLE = frozenset({LifecycleState.INDEXED})
_PUBLICATION_FIELDS = {"transaction_id", "result_ref", "attestation_ref", "lifecycle_context", "verification_ref",
                       "tool_binding_sha256", "habit_id"}


def _publication(value) -> dict:
    if type(value) is not dict or set(value) != _PUBLICATION_FIELDS:
        raise ValueError("learned habit publication has an unknown contract")
    return value


def learned_habit_status(project, publication, *, tool_binding_sha256: str) -> dict:
    """Whether a published learned behavior is admitted now, and for the current tool binding."""
    publication = _publication(publication)
    state = project.lifecycle_store.current_state(subject_ref=HashBoundRef.from_dict(publication["attestation_ref"]),
                                                  context=LifecycleContext.from_dict(publication["lifecycle_context"]))
    compatible = publication["tool_binding_sha256"] == tool_binding_sha256
    return {"admitted": state in CONSUMABLE and compatible, "compatible": compatible,
            "state": None if state is None else state.value}


def _head(lifecycle, subject, context):
    records = [item for item in lifecycle.records()
               if item.subject_ref == subject and item.context.to_dict() == context.to_dict()]
    if not records:
        raise ValueError("a superseded behavior has no lifecycle history")
    return records[-1]


def _persisted(lifecycle, subject, context, kind):
    """A persisted decision of this kind still waiting for its transition (a retry after a crash)."""
    waiting = [item for item in lifecycle.authority_decisions()
               if type(item) is SupersessionDecision and item.decision_kind is kind
               and item.proposal.subject_ref == subject and item.proposal.context == context]
    return waiting[-1] if waiting else None


def _end(project, publication, *, action, replacement, evidence, proposer, reason_code) -> str:
    """End a published behavior's consumable life by one decision of Gold's supersession authority; idempotent."""
    subject = HashBoundRef.from_dict(publication["attestation_ref"])
    context = LifecycleContext.from_dict(publication["lifecycle_context"])
    lifecycle, handle = project.lifecycle_store, project.authority_handle
    head = _head(lifecycle, subject, context)
    if head.to_state is LifecycleState.SUPERSEDED:
        return head.record_id.value
    kind = SupersessionDecisionKind(action.value)
    decision = _persisted(lifecycle, subject, context, kind)
    if decision is None:
        policy = source_ref(canonical({"profile": LEARNED_HABIT_PROFILE_V1, "action": action.value}),
                            LEARNED_HABIT_PROFILE_V1, RefKind.CONTRACT_CONDITION)
        proposal = create_lifecycle_authority_proposal(
            action=action, subject_ref=subject, replacement_ref=replacement, context=context,
            proposer_identity=proposer, producer_actor_ids=(proposer, project.declaration.identities.lifecycle_writer_actor),
            source_actor_ids=(LEARNED_HABIT_VERIFIER,), evidence_refs=(evidence,), compatibility_refs=(),
            policy_refs=(policy,), reason_codes=(reason_code,), predecessor_decision_id=None, decision_sequence=1)
        evaluator = configure_lifecycle_authority_evaluator(authority_handle=handle,
                                                            policy_version=LEARNED_HABIT_LIFECYCLE_POLICY_V1,
                                                            trusted_clock=lambda: datetime.now(timezone.utc))
        decision = evaluator.decide_supersession(authority_handle=handle, proposal=proposal, decision_kind=kind)
        # A positive authority decision is persisted before the transition it authorizes.
        lifecycle.persist_authority_decision(authority_handle=handle, decision=decision)
    reason = (LifecycleReasonCode.SUPERSESSION_APPROVED if kind is SupersessionDecisionKind.SUPERSEDE
              else LifecycleReasonCode.WITHDRAWAL_APPROVED)
    fence: FileSnapshotFence = project.fence
    with fence.exclusive() as guard, store_transaction(fence, guard=guard) as ticket:
        record = lifecycle.append(authority_handle=handle, subject_ref=subject, context=context,
                                  to_state=LifecycleState.SUPERSEDED, reason_code=reason,
                                  evidence_refs=tuple(decision.proposal.evidence_refs),
                                  expected_predecessor_record_id=head.record_id.value,
                                  expected_subject_sequence=head.subject_sequence + 1,
                                  supersession_decision=decision, mutation_ticket=ticket)
    return record.record_id.value


def supersede_learned_habit(project, predecessor, successor) -> str:
    """SUPERSEDE a predecessor behavior by its admitted successor; idempotent."""
    predecessor, successor = _publication(predecessor), _publication(successor)
    evidence = replace(HashBoundRef.from_dict(successor["verification_ref"]), kind=RefKind.SOURCE_EVIDENCE)
    return _end(project, predecessor, action=LifecycleAuthorityAction.SUPERSEDE,
                replacement=HashBoundRef.from_dict(successor["attestation_ref"]), evidence=evidence,
                proposer=CONSOLIDATION_JUDGE, reason_code="LEARNED_HABIT_BOUNDARY_SUCCESSOR")


def withdraw_learned_habit(project, publication, *, operator: ActorIdentity, reason: str) -> str:
    """The governing operator WITHDRAWs a published behavior; no learned session loads it again."""
    publication = _publication(publication)
    if type(reason) is not str or not reason:
        raise ValueError("a withdrawal states its reason")
    evidence = source_ref(canonical({"withdrawn": publication["habit_id"], "reason": reason}),
                          LEARNED_HABIT_PROFILE_V1, RefKind.SOURCE_EVIDENCE)
    return _end(project, publication, action=LifecycleAuthorityAction.WITHDRAW, replacement=None,
                evidence=evidence, proposer=operator, reason_code="LEARNED_HABIT_WITHDRAWN_BY_OPERATOR")
