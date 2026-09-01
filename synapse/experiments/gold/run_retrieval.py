"""Durable, gated retrieval for one attempt of one run (§20; §22 stage 3).

Stage 8 owns every step taken here. What did not exist in production was the
order they go in for a run, and that order is not free. Retrieval enumerates
from the *live* index, so the set it may consider is frozen from the committed
snapshot first; the enumerated candidates are gated before any of them becomes
selectable; and the load that follows names the gate decision that admitted
them. A caller free to reorder these can produce a causal record about a
decision that gated a different set.

This module is also the one place that knows a run's admission chain and the
attempt that consumes it are talking about the *same* retrieval. It answers as
both: it is the callable ``assemble_run_environment`` invokes for the retrieval
gate, and afterwards it is the ``AttemptRetrievalPort`` that reports what that
invocation decided. Two objects here would let an attempt's causal record name
a decision the chain never made.

What the caller declares is only what a deployment knows: how a candidate is
scored, where the ranking input came from, how many may be selected, which
conflicts anyone claims, and the two durable histories retrieval writes to.
Everything else is derived from the run's own declared candidate universe, so
it cannot disagree with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import AttemptId
from synapse.experiments.gold.retrieval import (
    RANKING_PROFILE_V1,
    RETRIEVAL_POLICY_V1,
    RetrievalAdmission,
    RetrievalResult,
    configure_durable_retrieval_persistence,
    configure_ranking_feature_provider,
    configure_retriever,
    create_retrieval_query,
    enumerate_retrieval_candidates_durably,
    gate_selectable_candidates,
    select_and_load_durably,
)

from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


def _no_proposed_conflicts(context, decisions, descriptors) -> tuple:
    """No party proposes a conflict between these candidates.

    This does not skip the conflict scan: §20's scan compares every pair of
    selected descriptors on its own and reports ``UNRESOLVED_CONFLICT`` from
    what it finds. Proposals are *additional* external claims, and an
    unresolvable one makes the whole scan incomplete, which blocks the run —
    so an empty set is the honest default rather than a permissive one.
    """

    return ()


@dataclass(frozen=True)
class RunRetrievalBindings:
    """What one deployment declares about ranking and conflict proposals.

    The score provider's actor is deliberately absent: §20 requires the
    scorer to be independent of evaluator, retriever and consumer, and the
    configured evaluator already carries the identity that satisfies it. Taking
    it from there rather than from a caller removes the one way the ranking
    provider could be configured under an actor the evaluator never vouched
    for.
    """

    ranking_component_id: str
    ranking_component_version: str
    scorer: Callable[..., int]
    input_ref_resolver: Callable[..., HashBoundRef]
    conflict_proposal_resolver: Callable[..., tuple] = _no_proposed_conflicts
    required_binding_targets: tuple = ()
    #: ``None`` means the run's whole declared candidate universe; a smaller
    #: number is a task-level limit on how much knowledge one attempt loads.
    selected_set_limit: int | None = None


@dataclass(frozen=True)
class DurableRunRetrieval:
    """The gate decision that admitted the candidates, and the load naming it."""

    admission: RetrievalAdmission
    result: RetrievalResult


@dataclass(frozen=True)
class RetrievedForAttempt:
    """What one attempt reads back: its retrieval gate decision and its result."""

    gate_decision: object
    result: RetrievalResult


class RunRetrieval:
    """One attempt's retrieval: the gate's decision, and the port that reports it."""

    def __init__(
        self,
        *,
        retrieval_journal: object,
        retrieval_compatibility_history: object,
        knowledge_store: object,
        library: object,
        authority_handle: object,
        admission_causal_history: object,
        candidates: tuple[tuple[object, object, object], ...],
        attempt_id: AttemptId,
        bindings: RunRetrievalBindings,
        trusted_clock: Callable[[], datetime],
        frozen_at_utc: datetime,
    ) -> None:
        if type(bindings) is not RunRetrievalBindings:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "retrieval bindings must be exact")
        if type(candidates) is not tuple or not candidates:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "retrieval needs the run's non-empty candidate universe",
            )
        if type(attempt_id) is not AttemptId:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "attempt id must be exact")
        if not callable(trusted_clock) or type(frozen_at_utc) is not datetime:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "retrieval clocks are invalid")
        self._journal = retrieval_journal
        self._retrieval_history = retrieval_compatibility_history
        self._knowledge_store = knowledge_store
        self._library = library
        self._handle = authority_handle
        self._causal_history = admission_causal_history
        self._candidates = candidates
        self._attempt_id = attempt_id
        self._bindings = bindings
        self._trusted_clock = trusted_clock
        self._frozen_at_utc = frozen_at_utc
        self._retrieved: DurableRunRetrieval | None = None
        self._decided_context: object | None = None

    @property
    def retrieved(self) -> DurableRunRetrieval | None:
        """The retrieval this attempt decided, or ``None`` before the gate ran."""

        return self._retrieved

    def __call__(self, **bound) -> object:
        """Decide the retrieval gate durably, for ``admit_run_knowledge``.

        Called once, from inside the admission chain, with the evidence and the
        snapshot authority that chain is using. Deciding twice would leave the
        chain naming one decision and the attempt consuming another, so a
        second call is refused rather than quietly re-deciding.
        """

        if self._retrieved is not None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "an attempt's retrieval gate may be decided only once",
            )
        evidence = bound["evidence"]
        #: Frozen from the committed snapshot, not from the live index: an
        #: object published after the snapshot froze is compatible, gate-able
        #: and invisible to the boundary probe, which checks only that the
        #: boundary is intact.
        frozen = GF.frozen_candidates_from_snapshot(
            knowledge_store=self._knowledge_store,
            attempt_id=self._attempt_id,
            expected_context=self._knowledge_store.open_current().manifest.context,
            frozen_at_utc=self._frozen_at_utc,
            evaluator_declaration=bound["snapshot_evaluator_declaration"],
            evaluator_actor_set=bound["snapshot_actor_set"],
            evaluator_independence_proof=bound["snapshot_independence_proof"],
        )
        retrieved = self._retrieve(
            evaluator=evidence.evaluator,
            context=evidence.context,
            controller=bound["controller"],
            frozen=frozen,
            consumer_context_ref=bound["consumer_context_ref"],
            boundary_ref=bound["boundary_ref"],
            requested=bound["requested"],
            publication_decision=bound["publication_decision"],
            entitlements=bound["entitlements"],
        )
        self._retrieved = retrieved
        self._decided_context = evidence.context
        return retrieved.admission.decision

    def retrieve_for_attempt(
        self, *, manifest, attempt_index: int, evaluator, compatibility_context
    ) -> RetrievedForAttempt:
        """Report the retrieval this attempt's admission chain decided.

        The attempt mints its own Stage 3 evidence immediately before asking,
        and that evidence is not a licence to retrieve again: the chain already
        carries one retrieval decision and the consumption gate names it as
        predecessor. What the fresh evidence is good for is catching staleness
        — if the library moved between the chain and this attempt, the decision
        held here gated a generation that no longer exists, and consuming it is
        the §21 failure this stack exists to prevent.
        """

        #: Deliberately unused. The evaluator that decided this retrieval is the
        #: chain's, and re-deciding under the attempt's own would produce a
        #: second decision the chain does not name.
        del manifest, attempt_index, evaluator

        retrieved = self._retrieved
        if retrieved is None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "this attempt's retrieval gate has not been decided",
            )
        decided = self._decided_context
        if type(compatibility_context) is not type(decided):
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "the attempt's compatibility context is not the type retrieval decided under",
            )
        if compatibility_context.library_snapshot_sha256 != decided.library_snapshot_sha256:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the attempt's evidence names a library generation retrieval did not gate",
            )
        return RetrievedForAttempt(
            gate_decision=retrieved.admission.decision, result=retrieved.result
        )

    def _retrieve(
        self,
        *,
        evaluator,
        context,
        controller,
        frozen,
        consumer_context_ref,
        boundary_ref,
        requested,
        publication_decision,
        entitlements,
    ) -> DurableRunRetrieval:
        """The §20 order, once: rank, query, enumerate, gate, then load."""

        descriptor_by_key = {
            entry.content_key: descriptor for _, descriptor, entry in self._candidates
        }
        provider = configure_ranking_feature_provider(
            component_id=self._bindings.ranking_component_id,
            component_version=self._bindings.ranking_component_version,
            scoring_profile=RANKING_PROFILE_V1,
            scorer=self._bindings.scorer,
            input_ref_resolver=self._bindings.input_ref_resolver,
            actor_identity=evaluator.score_provider_actor,
        )
        retriever = configure_retriever(
            authority_handle=self._handle,
            evaluator=evaluator,
            evaluator_declaration=evaluator.declaration,
            retrieval_policy=RETRIEVAL_POLICY_V1,
            trusted_clock=self._trusted_clock,
            descriptor_resolver=lambda entry: descriptor_by_key[entry.content_key],
            conflict_proposal_resolver=self._bindings.conflict_proposal_resolver,
            ranking_provider=provider,
            library=self._library,
        )
        limit = self._bindings.selected_set_limit
        query = create_retrieval_query(
            retriever=retriever,
            context=context,
            requested_behavior_kinds=self._requested_kinds(),
            required_binding_targets=self._bindings.required_binding_targets,
            selected_set_limit=len(self._candidates) if limit is None else limit,
        )
        persistence = configure_durable_retrieval_persistence(
            #: Retrieval's own compatibility records, not the run's history:
            #: it evaluates the live index, which holds entries the run's
            #: evidence never named. Merging the two would make that evidence
            #: appear to have covered candidates it never saw. Whether the two
            #: share one mutation fence is checked by the call itself.
            compatibility_history=self._retrieval_history,
            admission_causal_history=self._causal_history,
        )
        enumeration = enumerate_retrieval_candidates_durably(
            retriever=retriever,
            context=context,
            query=query,
            frozen=frozen,
            persistence=persistence,
        )
        admission = gate_selectable_candidates(
            controller=controller,
            candidates=enumeration.subject_refs,
            consumer_context_ref=consumer_context_ref,
            boundary_ref=boundary_ref,
            frozen=frozen,
            requested=requested,
            publication_decision=publication_decision,
            entitlements=entitlements,
            journal=self._journal,
            trusted_clock=self._trusted_clock,
        )
        result = select_and_load_durably(
            retriever=retriever,
            context=context,
            query=query,
            enumeration=enumeration,
            admission=admission,
            persistence=persistence,
        )
        if result.causal_record is None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "durable retrieval produced no causal record",
            )
        return DurableRunRetrieval(admission=admission, result=result)

    def _requested_kinds(self) -> tuple:
        """Ask for what the run declared, in the one order the query accepts."""

        return tuple(
            sorted(
                {unit.core.behavior_kind for unit, _, _ in self._candidates},
                key=lambda item: item.value,
            )
        )


__all__ = [
    "DurableRunRetrieval",
    "RetrievedForAttempt",
    "RunRetrieval",
    "RunRetrievalBindings",
]
