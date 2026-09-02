"""What knowledge one attempt admitted, and whether the next one has any new.

A run continues only when the attempt about to start can consume something the
attempt before it could not. Deciding that is this module's whole job, and the
decision turns entirely on *which subjects were admitted*, not on how the
admission was reached.

That distinction is the reason this module exists. The obvious comparison --
"did retrieval reach a different causal record?" -- cannot answer the question,
because a ``RetrievalCausalRecord`` carries the attempt's own envelope, its
boundary and its position in the causal history. Two attempts differ in all
three by construction, so that comparison reports "new" every time and a run
would continue forever on knowledge it already had. The same objection rules out
the frozen candidate set ref, which is per-attempt *and* describes the permitted
universe rather than what was admitted from it, and the retrieval result's
selected candidate ids, which identify audit rows rather than knowledge.

What is stable is the subject reference itself: content-addressed, and identical
across attempts when the same object is admitted again. So the criterion is set
difference over full subject identity, and a fresh revalidation of the same
subjects adds nothing -- it is what makes existing knowledge usable now, not new
knowledge. Treating it as new would let every attempt authorise the next one.

The basis a comparison needs outlives the attempt that produced it, so it is a
durable record rather than a value passed along: an attempt admitted subjects
even when its delivery never happened, and the attempt after it still has to
know what they were.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Protocol, runtime_checkable

from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef

from .models import canonical_run_bytes
from .vocabulary import GoldRunFailureCode, GoldRunViolation


ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1 = "synapse.stage4.gold.attempt-knowledge-basis/v1"
KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1 = (
    "synapse.stage4.gold.knowledge-continuation-evidence/v1"
)
PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V1 = "synapse.stage4.gold.prior-attempt-evidence/v1"

_BASIS_SEAL = object()
_EVIDENCE_SEAL = object()
_PRIOR_SEAL = object()


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


class ContinuationOutcome(str, Enum):
    """Closed vocabulary for what a continuation comparison concluded."""

    CONTINUATION_BASIS = "CONTINUATION_BASIS"
    NO_CONTINUATION_BASIS = "NO_CONTINUATION_BASIS"


@dataclass(frozen=True, init=False)
class AttemptKnowledgeBasis:
    """The subjects one attempt was admitted to consume, made durable.

    Sealed because it is the thing a later attempt's decision rests on. A basis
    a caller could assemble would let an attempt claim its predecessor admitted
    whatever set makes the next continuation come out the way it wants.
    """

    schema_version: str
    run_id: str
    attempt_id: str
    attempt_index: int
    admitted_subject_refs: tuple[HashBoundRef, ...]
    retrieval_gate_decision_ref: HashBoundRef
    consumer_context_ref: HashBoundRef
    boundary_ref: HashBoundRef
    policy_version: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> AttemptKnowledgeBasis:
        raise TypeError("AttemptKnowledgeBasis is produced only by create_attempt_knowledge_basis")

    def payload(self) -> dict[str, object]:
        """The canonical form the record store holds."""

        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "attempt_index": self.attempt_index,
            "admitted_subject_refs": [item.to_dict() for item in self.admitted_subject_refs],
            "retrieval_gate_decision_ref": self.retrieval_gate_decision_ref.to_dict(),
            "consumer_context_ref": self.consumer_context_ref.to_dict(),
            "boundary_ref": self.boundary_ref.to_dict(),
            "policy_version": self.policy_version,
        }

    def digest(self) -> str:
        """This basis's identity: the digest of the bytes the store will hold.

        Computed from the content, not returned by the write, so a caller can
        decide continuation before anyone has a mutation interval open. The
        store recomputes the same digest when the record is published, so the
        two cannot disagree.
        """

        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def admitted_set(self) -> frozenset[HashBoundRef]:
        """This attempt's admitted subjects as a set.

        A ``HashBoundRef`` is a frozen dataclass of exactly the six fields that
        identify it, so set membership already *is* full stable identity. A key
        function here would be a third one in this package -- ``admission`` and
        ``stage10.context`` each have their own -- and a third opinion about
        when two subjects are the same is exactly how two parties come to
        disagree about whether a set changed.
        """

        return frozenset(self.admitted_subject_refs)


class ContinuationBasisKind(str, Enum):
    """What authorised the next attempt to run at all.

    Two independent sources, because §24 asks for two different things. Library
    knowledge answers "is there something new to consume"; run-local evidence
    answers "did the last attempt establish something that changes the plan".
    A run may continue on either, and a run that continues on neither is
    repeating itself with a fresh id.
    """

    NEW_ADMITTED_KNOWLEDGE = "NEW_ADMITTED_KNOWLEDGE"
    NEW_VERIFIED_ATTEMPT_EVIDENCE = "NEW_VERIFIED_ATTEMPT_EVIDENCE"
    NONE = "NONE"


@dataclass(frozen=True, init=False)
class PriorAttemptEvidence:
    """What the attempt before this one verifiably established.

    Only authority-issued references are carried. A worker's own summary, a
    narrative, or raw oracle output are not continuation authority: they are the
    attempt's account of itself, and a run that continued on them would be
    deciding progress from the thing being judged.

    ``c1_result_ref`` is what makes the evidence verifiable. An attempt that
    reached the C1 boundary established something checkable even when it
    produced no patch -- a hypothesis was tried and refused, which is exactly
    the kind of progress §24 names.
    """

    schema_version: str
    attempt_index: int
    attempt_result_sha256: str
    worker_result_ref: HashBoundRef | None
    c1_result_ref: HashBoundRef | None
    oracle_result_ref: HashBoundRef | None
    oracle_invoked: bool
    oracle_resolved: bool | None
    accepted_plan_ref: HashBoundRef | None
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> PriorAttemptEvidence:
        raise TypeError("PriorAttemptEvidence is produced only by create_prior_attempt_evidence")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempt_index": self.attempt_index,
            "attempt_result_sha256": self.attempt_result_sha256,
            "worker_result_ref": _ref_or_none(self.worker_result_ref),
            "c1_result_ref": _ref_or_none(self.c1_result_ref),
            "oracle_result_ref": _ref_or_none(self.oracle_result_ref),
            "oracle_invoked": self.oracle_invoked,
            "oracle_resolved": self.oracle_resolved,
            "accepted_plan_ref": _ref_or_none(self.accepted_plan_ref),
        }

    def digest(self) -> str:
        return hashlib.sha256(canonical_run_bytes(self.payload())).hexdigest()

    def is_verified(self) -> bool:
        """Whether this evidence can authorise another attempt.

        The C1 result is the whole of it. Without it the attempt never reached
        the boundary that produces a checkable verdict, so whatever it produced
        is an account rather than a finding.
        """

        return self.c1_result_ref is not None


def _ref_or_none(value: HashBoundRef | None) -> dict[str, object] | None:
    return None if value is None else value.to_dict()


def create_prior_attempt_evidence(
    *,
    attempt_index: int,
    attempt_result_sha256: str,
    worker_result_ref: HashBoundRef | None,
    c1_result_ref: HashBoundRef | None,
    oracle_result_ref: HashBoundRef | None,
    oracle_invoked: bool,
    oracle_resolved: bool | None,
    accepted_plan_ref: HashBoundRef | None,
) -> PriorAttemptEvidence:
    """Record what one finished attempt established, in authority-issued refs."""

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    if type(oracle_invoked) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_invoked must be an exact bool")
    if oracle_resolved is not None and type(oracle_resolved) is not bool:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "oracle_resolved must be a bool or absent")
    for name, ref in (
        ("worker_result_ref", worker_result_ref),
        ("c1_result_ref", c1_result_ref),
        ("oracle_result_ref", oracle_result_ref),
        ("accepted_plan_ref", accepted_plan_ref),
    ):
        if ref is not None and type(ref) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact ref or absent")

    value = object.__new__(PriorAttemptEvidence)
    for name, item in (
        ("schema_version", PRIOR_ATTEMPT_EVIDENCE_SCHEMA_V1),
        ("attempt_index", attempt_index),
        ("attempt_result_sha256", attempt_result_sha256),
        ("worker_result_ref", worker_result_ref),
        ("c1_result_ref", c1_result_ref),
        ("oracle_result_ref", oracle_result_ref),
        ("oracle_invoked", oracle_invoked),
        ("oracle_resolved", oracle_resolved),
        ("accepted_plan_ref", accepted_plan_ref),
        ("_trusted_seal", _PRIOR_SEAL),
    ):
        object.__setattr__(value, name, item)
    return value


def create_attempt_knowledge_basis(
    *,
    run_id: str,
    attempt_id: str,
    attempt_index: int,
    admitted_subject_refs: tuple[HashBoundRef, ...],
    retrieval_gate_decision_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    policy_version: str,
) -> AttemptKnowledgeBasis:
    """Record what this attempt was admitted to consume.

    The subject order is the one §22 already publishes -- ``canonical_subject_refs``
    -- rather than a second ordering invented here. Two canonical orders for one
    kind of thing is how two parties come to disagree about whether a set
    changed.
    """

    if type(attempt_index) is not int or attempt_index < 1:
        raise _fail(GoldRunFailureCode.MALFORMED_IDENTITY, "attempt index must be one-based")
    for name, value in (
        ("run_id", run_id),
        ("attempt_id", attempt_id),
        ("policy_version", policy_version),
    ):
        if type(value) is not str or not value:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")
    for name, ref in (
        ("retrieval_gate_decision_ref", retrieval_gate_decision_ref),
        ("consumer_context_ref", consumer_context_ref),
        ("boundary_ref", boundary_ref),
    ):
        if type(ref) is not HashBoundRef:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be exact")
    ordered = A.canonical_subject_refs(admitted_subject_refs)

    value = object.__new__(AttemptKnowledgeBasis)
    object.__setattr__(value, "schema_version", ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1)
    object.__setattr__(value, "run_id", run_id)
    object.__setattr__(value, "attempt_id", attempt_id)
    object.__setattr__(value, "attempt_index", attempt_index)
    object.__setattr__(value, "admitted_subject_refs", ordered)
    object.__setattr__(value, "retrieval_gate_decision_ref", retrieval_gate_decision_ref)
    object.__setattr__(value, "consumer_context_ref", consumer_context_ref)
    object.__setattr__(value, "boundary_ref", boundary_ref)
    object.__setattr__(value, "policy_version", policy_version)
    object.__setattr__(value, "_trusted_seal", _BASIS_SEAL)
    return value


def basis_from_payload(payload: dict[str, object]) -> AttemptKnowledgeBasis:
    """Rebuild a basis from the bytes the store holds.

    Rebuilt through the same factory as a fresh one, so a stored record that no
    longer satisfies the rules cannot be read back into something that does.
    """

    if type(payload) is not dict:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "stored basis payload must be a mapping")
    if payload.get("schema_version") != ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH, "stored basis names an unknown schema"
        )
    try:
        return create_attempt_knowledge_basis(
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            attempt_index=payload["attempt_index"],
            admitted_subject_refs=tuple(
                HashBoundRef.from_dict(item) for item in payload["admitted_subject_refs"]
            ),
            retrieval_gate_decision_ref=HashBoundRef.from_dict(
                payload["retrieval_gate_decision_ref"]
            ),
            consumer_context_ref=HashBoundRef.from_dict(payload["consumer_context_ref"]),
            boundary_ref=HashBoundRef.from_dict(payload["boundary_ref"]),
            policy_version=payload["policy_version"],
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH, "stored basis is not readable"
        ) from exc


@dataclass(frozen=True, init=False)
class KnowledgeContinuationEvidence:
    """Why a run continued, or stopped: both sets and the difference between them.

    Sealed and kept whichever way the comparison came out. A stop that recorded
    only "no new knowledge" would be a verdict with no way to check it; this
    names the two bases compared and the subjects that were added, so a reader
    can recompute the answer instead of trusting it.
    """

    schema_version: str
    previous_basis_sha256: str
    next_basis_sha256: str
    previous_subject_refs: tuple[HashBoundRef, ...]
    next_subject_refs: tuple[HashBoundRef, ...]
    added_subject_refs: tuple[HashBoundRef, ...]
    outcome: ContinuationOutcome
    #: Which of the two independent sources authorised this attempt. Recorded
    #: rather than inferred: "the run continued" and "the run continued because
    #: the last attempt refuted a hypothesis" are different facts, and only the
    #: second one survives being asked why.
    basis_kinds: tuple[str, ...]
    prior_evidence_sha256: str | None
    policy_version: str
    _trusted_seal: object

    def __new__(cls, *args: object, **kwargs: object) -> KnowledgeContinuationEvidence:
        raise TypeError("KnowledgeContinuationEvidence is produced only by decide_continuation")

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "previous_basis_sha256": self.previous_basis_sha256,
            "next_basis_sha256": self.next_basis_sha256,
            "previous_subject_refs": [item.to_dict() for item in self.previous_subject_refs],
            "next_subject_refs": [item.to_dict() for item in self.next_subject_refs],
            "added_subject_refs": [item.to_dict() for item in self.added_subject_refs],
            "outcome": self.outcome.value,
            "policy_version": self.policy_version,
        }


def decide_continuation(
    *,
    previous: AttemptKnowledgeBasis,
    previous_basis_sha256: str,
    nxt: AttemptKnowledgeBasis,
    next_basis_sha256: str,
    prior_evidence: PriorAttemptEvidence | None = None,
    previous_prior_evidence_sha256: str | None = None,
) -> KnowledgeContinuationEvidence:
    """Decide whether the next attempt has a basis the previous one lacked.

    Two independent sources, and either is enough (§24)::

        added   = next_admitted_subjects - previous_admitted_subjects
        learned = the predecessor reached C1 and established something new

    Subjects that went away do not count: a run that lost knowledge has not
    gained any. Neither does a fresh revalidation of the same subjects --
    membership is decided by the reference itself, which says nothing about the
    occasion on which it was admitted.

    The second source is what makes a multi-attempt run possible at all while a
    run's library cannot grow. An attempt that reached the C1 boundary produced
    a checkable verdict even when it produced no patch: the hypothesis was tried
    and refused, and the next attempt plans knowing that. What does not count is
    the attempt's own account of itself -- a worker summary or raw oracle output
    is the thing being judged, not a finding about it.

    A run that continues on neither source is repeating itself under a new id,
    which is the hidden retry this refuses.
    """

    for name, value in (("previous", previous), ("next", nxt)):
        if type(value) is not AttemptKnowledgeBasis or getattr(value, "_trusted_seal", None) is not _BASIS_SEAL:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} basis is not sealed")
    if previous.policy_version != nxt.policy_version:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "the two attempts were admitted under different policy versions",
        )

    previously_admitted = previous.admitted_set()
    added = tuple(
        item for item in nxt.admitted_subject_refs if item not in previously_admitted
    )
    kinds: list[str] = []
    if added:
        kinds.append(ContinuationBasisKind.NEW_ADMITTED_KNOWLEDGE.value)
    prior_digest = None
    if prior_evidence is not None:
        if type(prior_evidence) is not PriorAttemptEvidence or (
            getattr(prior_evidence, "_trusted_seal", None) is not _PRIOR_SEAL
        ):
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "prior attempt evidence is not sealed")
        prior_digest = prior_evidence.digest()
        #: New *and* verified. Evidence identical to what the attempt before it
        #: already established is the same finding recorded twice, and a run
        #: that continued on it would loop on one refuted hypothesis forever.
        if prior_evidence.is_verified() and prior_digest != previous_prior_evidence_sha256:
            kinds.append(ContinuationBasisKind.NEW_VERIFIED_ATTEMPT_EVIDENCE.value)
    outcome = (
        ContinuationOutcome.CONTINUATION_BASIS
        if kinds
        else ContinuationOutcome.NO_CONTINUATION_BASIS
    )

    value = object.__new__(KnowledgeContinuationEvidence)
    object.__setattr__(value, "schema_version", KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1)
    object.__setattr__(value, "previous_basis_sha256", previous_basis_sha256)
    object.__setattr__(value, "next_basis_sha256", next_basis_sha256)
    object.__setattr__(value, "previous_subject_refs", previous.admitted_subject_refs)
    object.__setattr__(value, "next_subject_refs", nxt.admitted_subject_refs)
    object.__setattr__(value, "added_subject_refs", added)
    object.__setattr__(value, "outcome", outcome)
    object.__setattr__(value, "basis_kinds", tuple(kinds) or (ContinuationBasisKind.NONE.value,))
    object.__setattr__(value, "prior_evidence_sha256", prior_digest)
    object.__setattr__(value, "policy_version", nxt.policy_version)
    object.__setattr__(value, "_trusted_seal", _EVIDENCE_SEAL)
    return value


def prior_attempt_evidence_from_result(
    result: object, *, attempt_index: int, accepted_plan_ref: HashBoundRef | None = None
) -> PriorAttemptEvidence:
    """Take the authority-issued refs out of a finished attempt's result.

    A reader of the result rather than a second source of truth: every field is
    copied from what the attempt already recorded. Nothing is derived, so this
    cannot disagree with the record it reads.
    """

    return create_prior_attempt_evidence(
        attempt_index=attempt_index,
        attempt_result_sha256=result.result_sha256,
        worker_result_ref=result.worker_result_ref,
        c1_result_ref=result.c1_result_ref,
        oracle_result_ref=result.oracle_result_ref,
        oracle_invoked=result.oracle_invoked,
        oracle_resolved=result.oracle_resolved,
        accepted_plan_ref=accepted_plan_ref,
    )


@dataclass(frozen=True)
class PreviousAttemptBinding:
    """Everything the next attempt is entitled to know about the one before it.

    Three things travel together because a decision needs all three and none of
    them can stand in for another: the context proves snapshot lineage, the
    basis says what was admitted, and the evidence says what was established.
    Passing only the context -- which is what the controller used to do -- meant
    continuation could be decided from identities alone, and identities differ
    between attempts no matter what happened.
    """

    context: object
    basis_sha256: str | None
    prior_evidence: PriorAttemptEvidence | None
    prior_evidence_sha256: str | None


@runtime_checkable
class AttemptKnowledgeBasisPort(Protocol):
    """Where an attempt's admitted set is made durable and read back.

    A port because the store belongs to the run's composition, not to the owner
    that decides continuation: this module states what a basis is and what it
    means for one to follow another, and knows nothing about where bytes live.
    """

    def put_basis(self, basis: AttemptKnowledgeBasis, *, ticket: object) -> str: ...

    def get_basis(self, *, attempt_index: int) -> tuple[AttemptKnowledgeBasis, str] | None: ...


__all__ = [
    "ATTEMPT_KNOWLEDGE_BASIS_SCHEMA_V1",
    "KNOWLEDGE_CONTINUATION_EVIDENCE_SCHEMA_V1",
    "AttemptKnowledgeBasis",
    "AttemptKnowledgeBasisPort",
    "ContinuationOutcome",
    "KnowledgeContinuationEvidence",
    "basis_from_payload",
    "create_attempt_knowledge_basis",
    "decide_continuation",
]
