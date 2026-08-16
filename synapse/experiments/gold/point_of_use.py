"""Stage 4 §22 point of use — revalidating an admission at the moment of delivery.

``admission`` owns the four gate decisions, the durable journal and the
``AdmittedKnowledgeHandle`` a completed chain mints. This module owns the last
step: what happens when a consumer is about to *act* on that handle.

It exists as a separate owner for two reasons.

The first is the repository's size rule. ``admission.py`` had reached the point
where further nodes had to attach through an adapter rather than grow the file,
and this node is a clean seam: everything here runs after a handle exists, and
nothing in the gate evaluators depends on it. The dependency runs one way — this
module imports ``admission``, ``admission`` imports nothing from here — so the
seam cannot become a cycle.

The second is what the node actually asserts. A handle records that an admission
happened; it cannot record that the admission is *still true*, and a consumer
holding one has no obligation in the type system to look again. So the barrier
is placed at the point of use, and its result is a sealed
``CurrentAdmittedKnowledge`` that names the subject set, consumer context,
boundary, policy, decision, receipt and journal position it just verified. A
delivery owner that accepts this object cannot be handed knowledge no gate
admitted, and cannot substitute other refs afterwards, because the refs it is
permitted to use are the ones the record carries.

The adapter surface is declared rather than discovered: ``ADAPTER_PRIVATE_SEAM``
below lists every non-public name this module takes from ``admission``, and a
tripwire fails if the list and the imports drift apart. Sharing those validators
is deliberate — reimplementing digest, timestamp and subject checks here is how
two owners end up disagreeing about what a valid record is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .canonicalization import HashBoundRef, RefKind
from .contracts import (
    ContractViolation,
    IdentityDomain,
    RecordId,
    SchemaVersion,
    compute_record_id,
    validate_record_id,
)
from .admission import (
    AdmissionFailureCode,
    AdmissionViolation,
    AdmittedKnowledgeHandle,
    AuthorityHeadSet,
    ConfiguredGateController,
    DecisionCommitReceipt,
    DecisionJournalPort,
    GateDecision,
    GateDecisionChain,
    GateDependencyUnavailable,
    require_committed_decision,
    require_entitled_chain,
    require_configured_gate_controller,
    require_consumption_admitted,
    require_current_heads,
    validate_admitted_handle,
    validate_authority_head_set,
    validate_commit_receipt,
    validate_gate_decision,
)
from .admission import (
    UTC_TIMESTAMP_FORMAT as _UTC_TIMESTAMP_FORMAT,
    _canonical,
    _fail,
    _identifier,
    _sha256_text,
    _subject_key,
    _subjects,
    _timestamp,
)

#: The exact non-public surface this adapter is permitted to take from the
#: admission owner. Shared so the two owners cannot disagree about what a valid
#: digest, timestamp, identifier or subject tuple is; declared so the seam
#: cannot widen unnoticed. ``tests/test_stage4_gold_dependency_direction.py``
#: fails if the imports above and this tuple stop matching.
ADAPTER_PRIVATE_SEAM = (
    "UTC_TIMESTAMP_FORMAT",
    "_canonical",
    "_fail",
    "_identifier",
    "_sha256_text",
    "_subject_key",
    "_subjects",
    "_timestamp",
)

_CURRENT_KNOWLEDGE_SEAL = object()


@dataclass(frozen=True, init=False)
class CurrentAdmittedKnowledge:
    """A revalidation result that names the knowledge it revalidated.

    The distinction this type exists to make is narrow and load-bearing. An
    earlier revision had ``require_current_admitted_handle`` return only the
    fresh ``AuthorityHeadSet``, and the module claimed on that basis that a
    consumer contract taking the *result* could not bypass the gate. It could:
    a head set says "the world had not moved at time T" and says nothing at all
    about *which* subjects were admitted, for which consumer, under which
    boundary or decision. A caller could revalidate one handle and then compile,
    replay or execute over an entirely different subject set, and every type in
    the signature would still be satisfied.

    So the revalidation result carries the binding as well as the freshness:
    the exact subject set, consumer context, boundary and policy version that
    were checked; the consumption decision and durable receipt they rest on; the
    journal anchor observed at the moment of the check; and the fresh head
    observation. A consumer that accepts this object cannot be handed knowledge
    that no gate admitted, and cannot silently substitute other refs afterwards,
    because the refs it is allowed to use are the ones stored here.
    """

    schema_version: SchemaVersion
    knowledge_id: RecordId
    handle_id: RecordId
    subject_refs: tuple[HashBoundRef, ...]
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    consumption_decision_id: RecordId
    commit_receipt: DecisionCommitReceipt
    observed_head_set: AuthorityHeadSet
    journal_anchor_sha256: str
    #: The coordinator epoch this result describes: even, and settled *after* any
    #: append this revalidation made itself. Recorded rather than implied, because
    #: "the world had not moved" and "the world had moved by exactly my own write"
    #: are different claims and only the second is true of `admit_for_use_now`.
    observed_epoch: int
    verified_at_utc: datetime
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> CurrentAdmittedKnowledge:
        raise TypeError(
            "CurrentAdmittedKnowledge is produced only by require_current_admitted_handle"
        )

    def to_dict(self) -> dict[str, object]:
        validate_current_admitted_knowledge(self)
        return _current_knowledge_payload(self) | {"knowledge_id": self.knowledge_id.to_dict()}

    def canonical_bytes(self) -> bytes:
        validate_current_admitted_knowledge(self)
        return _canonical(_current_knowledge_payload(self))


def _current_knowledge_payload(value: CurrentAdmittedKnowledge) -> dict[str, object]:
    return {
        "schema_version": value.schema_version.value,
        "handle_id": value.handle_id.to_dict(),
        "subject_refs": [item.to_dict() for item in value.subject_refs],
        "consumer_context_ref": value.consumer_context_ref.to_dict(),
        "boundary_ref": value.boundary_ref.to_dict(),
        "policy_version": value.policy_version,
        "consumption_decision_id": value.consumption_decision_id.to_dict(),
        "commit_receipt": value.commit_receipt.to_dict(),
        "observed_head_set": value.observed_head_set.to_dict(),
        "journal_anchor_sha256": value.journal_anchor_sha256,
        "observed_epoch": value.observed_epoch,
        "verified_at_utc": value.verified_at_utc.strftime(_UTC_TIMESTAMP_FORMAT),
    }


def validate_current_admitted_knowledge(value: CurrentAdmittedKnowledge) -> None:
    """Refuse anything that is not a sealed, self-consistent revalidation result."""

    if (
        type(value) is not CurrentAdmittedKnowledge
        or getattr(value, "_trusted_seal", None) is not _CURRENT_KNOWLEDGE_SEAL
    ):
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "current admitted knowledge is not factory sealed",
        )
    if value.schema_version is not SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1:
        raise _fail(
            AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION,
            "current admitted knowledge schema is unknown",
        )
    _subjects(value.subject_refs)
    if type(value.consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "revalidated knowledge requires an exact consumer context",
        )
    if type(value.boundary_ref) is not HashBoundRef or value.boundary_ref.kind is not RefKind.ATOMIC_BOUNDARY:
        raise _fail(
            AdmissionFailureCode.BOUNDARY_REQUIRED,
            "revalidated knowledge requires an exact committed boundary ref",
        )
    _identifier(value.policy_version, "policy_version")
    validate_commit_receipt(value.commit_receipt)
    validate_authority_head_set(value.observed_head_set)
    _sha256_text(value.journal_anchor_sha256, "journal_anchor_sha256")
    if type(value.observed_epoch) is not int or value.observed_epoch < 0 or value.observed_epoch % 2:
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "a revalidation result binds to an exact settled coordinator epoch",
        )
    _timestamp(value.verified_at_utc, "verified_at_utc")
    if _subject_key(value.observed_head_set.boundary_ref) != _subject_key(value.boundary_ref):
        raise _fail(
            AdmissionFailureCode.HEAD_OBSERVATION_STALE,
            "the fresh observation belongs to another boundary",
        )
    if value.commit_receipt.gate_decision_id.digest_sha256 != value.consumption_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the revalidation receipt belongs to another consumption decision",
        )
    try:
        validate_record_id(
            value.knowledge_id, canonical_bytes=_canonical(_current_knowledge_payload(value))
        )
    except ContractViolation as exc:
        raise _fail(
            AdmissionFailureCode.DECISION_IDENTITY_MISMATCH,
            "knowledge_id does not match its payload",
        ) from exc


def require_admitted_subjects(
    value: CurrentAdmittedKnowledge,
    *,
    subject_refs: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
) -> tuple[HashBoundRef, ...]:
    """Confirm that what a consumer is about to use is what was revalidated.

    Holding a valid revalidation result is not the same as acting on it. A
    consumer that receives ``CurrentAdmittedKnowledge`` for subjects A and B and
    then compiles over B and C has satisfied every type in its signature while
    using an object no gate admitted. This is the call that closes that gap, and
    it returns the admitted refs so a caller can use those rather than its own.
    """

    validate_current_admitted_knowledge(value)
    if type(consumer_context_ref) is not HashBoundRef:
        raise _fail(
            AdmissionFailureCode.CONSUMER_CONTEXT_REQUIRED,
            "a use site must name the exact consumer context it is acting for",
        )
    if _subject_key(consumer_context_ref) != _subject_key(value.consumer_context_ref):
        raise _fail(
            AdmissionFailureCode.STALE_DECISION,
            "the consumer context is not the one this knowledge was admitted for",
        )
    if tuple(_subject_key(item) for item in _subjects(subject_refs)) != tuple(
        _subject_key(item) for item in value.subject_refs
    ):
        raise _fail(
            AdmissionFailureCode.SUBJECT_MISMATCH,
            "the subjects about to be used are not the admitted subject set",
        )
    return value.subject_refs


def require_current_admitted_handle(
    handle: AdmittedKnowledgeHandle,
    *,
    controller: ConfiguredGateController,
    journal: DecisionJournalPort,
    consumption_decision: GateDecision,
    fence: object,
) -> CurrentAdmittedKnowledge:
    """The point-of-use barrier. Call this immediately before acting on a handle.

    ``admit_for_consumption`` checks the world at the moment of minting, and
    that is all it can check. Between minting and use the behavior can be
    revoked, its taint escalated, its admission superseded or the boundary
    replaced — and a handle that were merely *structurally* valid afterwards
    would be a cached boolean in a typed wrapper, which is precisely the defect
    §22's time-of-use requirement exists to prevent.

    So everything is re-asserted here against the world as it is now: the
    handle's own identity, the exact decision it names, that decision's durable
    inclusion and un-rolled-back history, and a fresh coherent observation of
    the boundary and every authority head.

    What comes back is a sealed ``CurrentAdmittedKnowledge`` rather than the
    fresh head set alone. The distinction matters: a head set proves only that
    the world had not moved, which a consumer could satisfy while acting on
    subjects this handle never covered. The sealed result carries the admitted
    subject set, consumer context, boundary, policy, decision, receipt and the
    journal anchor observed here, so a consumer contract that accepts it is
    bound to the knowledge that was actually revalidated.

    ``fence`` is required here for the same reason it is required next door.
    This function's claim is "a fresh *coherent* observation of the boundary and
    every authority head", and it used to establish coherence by reading six
    stores through one call — which is one call, not one instant. The epoch it
    now brackets the read with is the same epoch the result binds to, so the
    record states which moment it describes instead of implying one.

    Whoever holds a handle must call this. Nothing in the type stops a caller
    from skipping it, which is why the consumer contract — a replay request, a
    worker context — takes the *result* of this call rather than the handle.
    """

    from .coordination import (
        read_current_authority_state,
        require_snapshot_fence,
        settle_after_own_mutation,
    )

    validate_admitted_handle(handle)
    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    validate_gate_decision(consumption_decision)
    if consumption_decision.gate_decision_id.digest_sha256 != handle.consumption_decision_id.digest_sha256:
        raise _fail(
            AdmissionFailureCode.DECISION_NOT_DURABLE,
            "the supplied decision is not the one this handle was minted from",
        )
    require_consumption_admitted(
        consumption_decision,
        subject_refs=handle.subject_refs,
        consumer_context_ref=handle.consumer_context_ref,
        boundary_ref=handle.boundary_ref,
        policy_version=handle.policy_version,
    )
    require_committed_decision(
        handle.commit_receipt, decision=consumption_decision, journal=journal
    )
    fenced = read_current_authority_state(controller, fence=fence, participants=(journal,))
    observed = require_current_heads(handle.head_set, controller=controller)
    # Nothing here writes, so "settled after zero of my own intervals" is the
    # plain no-movement question — asked through the same function as the writing
    # path so the two cannot drift into disagreeing about what settled means.
    observed_epoch = settle_after_own_mutation(fenced, fence=fence, own_intervals=0)
    try:
        anchor = journal.current_anchor()
    except AdmissionViolation:
        raise
    except GateDependencyUnavailable as exc:
        raise _fail(
            AdmissionFailureCode.JOURNAL_UNAVAILABLE,
            "the decision journal could not report its current anchor",
        ) from exc

    return _mint_current_knowledge(
        handle=handle,
        decision=consumption_decision,
        receipt=handle.commit_receipt,
        observed=observed,
        journal=journal,
        controller=controller,
        observed_epoch=observed_epoch,
        anchor=anchor,
    )


def _mint_current_knowledge(
    *,
    handle: AdmittedKnowledgeHandle,
    decision: GateDecision,
    receipt: DecisionCommitReceipt,
    observed: AuthorityHeadSet,
    journal: DecisionJournalPort,
    controller: ConfiguredGateController,
    observed_epoch: int,
    anchor: str | None = None,
) -> CurrentAdmittedKnowledge:
    """Seal one revalidation result.

    Shared by both point-of-use paths so they cannot disagree about what a
    revalidation record contains. The two differ in exactly one field and it is
    the important one: ``require_current_admitted_handle`` names the decision the
    handle was minted from, and ``admit_for_use_now`` names the decision it just
    took. Everything else about the record is identical, which is why it is
    built in one place.
    """

    if anchor is None:
        try:
            anchor = journal.current_anchor()
        except AdmissionViolation:
            raise
        except GateDependencyUnavailable as exc:
            raise _fail(
                AdmissionFailureCode.JOURNAL_UNAVAILABLE,
                "the decision journal could not report its current anchor",
            ) from exc

    knowledge = object.__new__(CurrentAdmittedKnowledge)
    object.__setattr__(knowledge, "schema_version", SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V1)
    object.__setattr__(knowledge, "handle_id", handle.handle_id)
    object.__setattr__(knowledge, "subject_refs", handle.subject_refs)
    object.__setattr__(knowledge, "consumer_context_ref", handle.consumer_context_ref)
    object.__setattr__(knowledge, "boundary_ref", handle.boundary_ref)
    object.__setattr__(knowledge, "policy_version", handle.policy_version)
    object.__setattr__(knowledge, "consumption_decision_id", decision.gate_decision_id)
    object.__setattr__(knowledge, "commit_receipt", receipt)
    object.__setattr__(knowledge, "observed_head_set", observed)
    object.__setattr__(knowledge, "journal_anchor_sha256", _sha256_text(anchor, "journal_anchor"))
    object.__setattr__(knowledge, "observed_epoch", observed_epoch)
    object.__setattr__(
        knowledge,
        "verified_at_utc",
        _timestamp(controller._trusted_clock(), "verified_at_utc"),
    )
    object.__setattr__(knowledge, "_trusted_seal", _CURRENT_KNOWLEDGE_SEAL)
    object.__setattr__(
        knowledge,
        "knowledge_id",
        compute_record_id(
            domain=IdentityDomain.CURRENT_ADMITTED_KNOWLEDGE,
            canonical_bytes=_canonical(_current_knowledge_payload(knowledge)),
        ),
    )
    validate_current_admitted_knowledge(knowledge)
    return knowledge


__all__ = [
    "ADAPTER_PRIVATE_SEAM",
    "CurrentAdmittedKnowledge",
    "admit_for_use_now",
    "require_admitted_subjects",
    "require_current_admitted_handle",
    "validate_current_admitted_knowledge",
]


# ---------------------------------------------------------------------------
# Re-deciding at the point of use, rather than re-reading anchors
# ---------------------------------------------------------------------------



def _require_chain_is_this_handles(
    handle: AdmittedKnowledgeHandle, *, chain: GateDecisionChain, evidence: object
) -> None:
    """Prove the chain and evidence are the ones that produced *this* handle.

    Everything downstream checked that the chain was internally valid, durably
    committed, and about the same subjects, consumer context, boundary and policy
    as the handle. All of those are *values*, and values are exactly what a second
    legitimate chain shares. So a handle minted from chain A could be presented
    with a valid chain B from another authority and another journal: the audit
    reproduced it, the original history was deleted, and a fresh admission came
    out resting on B.

    Identity is what separates them, and it is already recorded on both sides —
    the handle carries the consumption decision id it was minted from and the
    receipt that committed it. Comparing those is the whole check, and it runs
    before the durable recovery so that a substituted chain cannot append its
    fresh verdict to journal B on the way to being refused.
    """

    if type(chain) is not GateDecisionChain:
        raise _fail(
            AdmissionFailureCode.TRUSTED_OBJECT_FORGED,
            "the point of use requires an exact gate decision chain",
        )
    # There is deliberately no separate comparison of the chain's consumption
    # decision id against the handle's. A campaign mutant removed one and nothing
    # failed, which is what an unenforceable rule looks like from outside — and it
    # is unenforceable because it is implied. The receipt below ties the evidence
    # to the handle, and `recover_chain_evidence` further down ties the evidence
    # to the chain; a chain that is not the handle's therefore cannot survive both.
    # Keeping the comparison would leave two statements of one fact, and removing
    # either would still look guarded in review.
    receipts = getattr(evidence, "receipts", None)
    if type(receipts) is not tuple or not receipts:
        raise _fail(
            AdmissionFailureCode.CHAIN_NOT_DURABLE,
            "the point of use requires the commit evidence of this handle's chain",
        )
    final = receipts[-1]
    validate_commit_receipt(final)
    if (
        final.decision_digest != handle.commit_receipt.decision_digest
        or final.gate_decision_id.value != handle.commit_receipt.gate_decision_id.value
    ):
        raise _fail(
            AdmissionFailureCode.CHAIN_NOT_DURABLE,
            "the commit evidence does not end in the receipt this handle carries",
        )
    # The chain's own consumption verdict must still be an ADMIT about exactly the
    # handle's subjects, context, boundary and policy. That is the value half of
    # the binding, kept because identity alone would not notice a handle whose
    # decision was later re-read against a different context.
    require_consumption_admitted(
        chain.consumption,
        subject_refs=handle.subject_refs,
        consumer_context_ref=handle.consumer_context_ref,
        boundary_ref=handle.boundary_ref,
        policy_version=handle.policy_version,
    )


def admit_for_use_now(
    handle: AdmittedKnowledgeHandle,
    *,
    controller: ConfiguredGateController,
    chain: GateDecisionChain,
    evidence: object,
    entitlements: object,
    journal: DecisionJournalPort,
    fence: object,
    requested: object,
) -> CurrentAdmittedKnowledge:
    """Re-run the consumption gate here, now, and admit only on the fresh verdict.

    ``require_current_admitted_handle`` re-reads the authority heads and proves
    the stored decision is still durable. That is necessary and it is not
    sufficient, and the gap is narrow enough to be worth stating exactly.

    An authority head answers "has this store moved". Applicability answers "is
    this object usable in this exact frozen context *now*", and the two are not
    the same question. Compatibility depends on the live environment, tool and
    policy observation as much as on stored records: a compiler upgrade or a
    changed environment version makes an admitted behavior inapplicable without
    writing anything to the compatibility store, so its head anchor is unmoved
    and a head comparison sees a quiet world. §22 is explicit that a stored
    compatibility status does not substitute for a fresh check, and comparing an
    anchor *is* relying on the stored status — one indirection removed.

    So this does not compare; it decides. The world is captured under a fence,
    the consumption gate is evaluated again from scratch — which re-runs the
    compatibility probe against the exact consumer context, along with taint,
    lifecycle, provenance, boundary and grant — the fresh verdict is committed
    durably, and the returned knowledge names *that* decision. The earlier
    consumption ADMIT is not carried forward; it is superseded.

    A blocked fresh verdict yields nothing. There is no path here that falls
    back on the older decision, because "it was admissible ten minutes ago" is
    the precise claim §22's time-of-use requirement exists to refuse.

    **All of it is one transaction of one coordinator.** Six ordered steps —
    bind, read, decide, append, re-settle, mint — under this coordinator's
    exclusive lock, because the sequence reads the world and then writes to it.
    Detection alone cannot carry a read-decide-write: it can report interference
    forever without the work ever completing, and worse, between the fresh verdict
    and the append another writer can land the very change that would have blocked
    it.

    Two consequences worth stating, because both were wrong before. The append is
    made with the transaction's *own* ticket rather than by opening a second
    interval, so the epoch does not fall back to even in the middle of the
    transaction. And the result binds to the **final** even epoch, after that
    append: the entry epoch describes a world this function has since changed
    itself, and binding to it would attest a moment that no longer exists. What
    the closing check refuses is movement that is *not* this transaction's own.
    """

    from .admission import (
        GateDecisionKind,
        commit_gate_decision,
        evaluate_consumption_gate,
        require_gate_predecessor,
    )
    from .admission_store import recover_chain_evidence, require_admission_history
    from .coordination import (
        read_current_authority_state,
        require_snapshot_fence,
        settle_after_own_mutation,
    )
    from .contracts import GateKind
    from .persistence import store_transaction

    validate_admitted_handle(handle)
    require_configured_gate_controller(controller)
    require_snapshot_fence(fence)
    _require_chain_is_this_handles(handle, chain=chain, evidence=evidence)
    # The consumer is the last verifier and the one with the most to lose, so it
    # re-establishes entitlement from its own copies rather than inheriting the
    # builder's or the minter's conclusion. Same computation, different holder —
    # which is the whole content of the claim.
    require_entitled_chain(chain, entitlements=entitlements)

    # The chain that justified this handle must still be in the durable record,
    # and this is where that is asked. Minting demanded four committed receipts;
    # nothing re-asked at the moment of use, so deleting every decision from the
    # journal left this function happily minting `CurrentAdmittedKnowledge` behind
    # which history held only the consumption record it had just written itself.
    #
    # `recover_chain_evidence` rather than four `require_committed_decision`
    # calls: the latter asks `contains_record`, and membership is the check a
    # reordered history defeats while keeping every record — the repository has a
    # test saying exactly that. This asks for membership, each link to its
    # predecessor, contiguous positions and the witnessed anchor still being a
    # prefix of committed history.
    recover_chain_evidence(evidence, chain=chain, store=require_admission_history(journal))

    with fence.exclusive():
        # One coherent read of the world, or nothing. Everything below decides
        # against this observation, so a torn one must not reach the evaluation.
        fenced = read_current_authority_state(
            controller, fence=fence, participants=(journal,)
        )

        require_gate_predecessor(
            chain.retrieval, expected_gate=GateKind.RETRIEVAL, subject_refs=handle.subject_refs
        )
        fresh = evaluate_consumption_gate(
            controller,
            subject_refs=handle.subject_refs,
            consumer_context_ref=handle.consumer_context_ref,
            boundary_ref=handle.boundary_ref,
            requested=requested,
            predecessor=chain.retrieval,
        )
        if fresh.decision_kind is not GateDecisionKind.ADMIT:
            raise _fail(
                AdmissionFailureCode.NOT_ADMITTED,
                f"the fresh consumption verdict is {fresh.decision_kind.value}",
            )

        # One interval, opened here and passed down. The journal would otherwise
        # open its own, which would take the epoch back to even in the middle of
        # this transaction — a reader arriving at that instant would see a settled
        # world holding a decision this function has not yet finished making.
        with store_transaction(fence) as ticket:
            receipt = commit_gate_decision(
                fresh,
                journal=journal,
                trusted_clock=controller._trusted_clock,
                ticket=ticket,
            )

        # The world must still be the one that was decided against, plus exactly
        # this transaction's own append and nothing else.
        final_epoch = settle_after_own_mutation(fenced, fence=fence, own_intervals=1)
        head_set = fenced.head_set
        require_current_heads(head_set, controller=controller)

        minted = _mint_current_knowledge(
            handle=handle,
            decision=fresh,
            receipt=receipt,
            observed=head_set,
            journal=journal,
            controller=controller,
            observed_epoch=final_epoch,
        )
    return minted
