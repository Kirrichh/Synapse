"""Production supplier of one attempt's Stage 3/7/8/10 input set (§26; plan Этап 11).

One responsibility: sequence the existing owners into one coherent
``PreparedAttemptInputs`` for one attempt of one run. Nothing here decides
policy — the compatibility evaluator, the gate controller, the plan authority
and the retrieval stack each keep their own authority, and this module only
puts their calls in the one order §26 requires and reports a typed absence
when a step produces no record.

The ordering is the point, and it is why this belongs to production rather
than to a fixture: fresh Stage 3 evidence is minted for *this* attempt, the
authority binding is built over that exact evidence, retrieval is gated before
anything becomes selectable, and only then is a point-of-use admission request
created. An attempt assembled in any other order can look valid and be about
the wrong thing.

What this module deliberately does not own: the durable world it reads
(``attempt_environment``), the declared plan (``attempt_plan``), governed
replay and durable retrieval — Stage 9's and Stage 8's owners, reached through
the ports declared here and bound by the run composition root, which is the
only place allowed to know every side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import HashBoundRef, content_key_digest
from synapse.experiments.gold.retrieval import retrieval_causal_record_ref
from synapse.experiments.gold.run_compatibility import (
    CompatibilityEvaluatorBindings,
    mint_compatibility_evidence,
)
from synapse.experiments.gold.stage10.context import (
    ContextSizeBudget,
    ExcludedKnowledgeRef,
    ExclusionReason,
)
from synapse.experiments.gold.stage10.plan_revalidation import CurrentPlanState

from .attempt_environment import GoldAttemptEnvironment, require_gold_attempt_environment
from .attempt_inputs import (
    AttemptInputAvailability,
    KnowledgeDependencyUnavailable,
    NoNewKnowledge,
    PreparedAttemptInputs,
)
from .attempt_plan import GoldAttemptPlanProfile, accept_attempt_plan
from .models import GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


#: Attempt indices are one-based; the run's own budget bounds them from above,
#: so the only thing refused here is an index that could name no attempt.
_MIN_ATTEMPT_INDEX = 1


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@runtime_checkable
class AttemptReplayPort(Protocol):
    """Governed replay of this attempt's admitted behaviors (Stage 9's owner)."""

    def replay_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int
    ) -> object: ...


@runtime_checkable
class AttemptRetrievalPort(Protocol):
    """Durable, gated retrieval for this attempt (Stage 8's owner).

    The returned value carries the gate decision that admitted the candidates
    and the retrieval result whose causal record names that exact decision.
    """

    def retrieve_for_attempt(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        evaluator: object,
        compatibility_context: object,
    ) -> object: ...


@runtime_checkable
class AttemptWorktreePort(Protocol):
    """Materialize the isolated worktree this attempt's worker will edit."""

    def worktree_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int
    ) -> Path: ...


@runtime_checkable
class AttemptWorldPort(Protocol):
    """Build the world one attempt consumes from (the run composition root's).

    One world per attempt, not one per run. The snapshot an attempt plans
    against, the Stage 3 evidence its authority rests on, the gate chain that
    admitted its subjects and the one-shot retrieval that gated them are all
    facts about *that* attempt; a second attempt handed the first one's world
    reports decisions taken before its own predecessor had run.
    """

    def world_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int, previous_context: object | None
    ) -> object: ...


def mint_attempt_authority(
    *, environment: GoldAttemptEnvironment, moment: datetime
) -> "AttemptAuthority":
    """Mint this attempt's own Stage 3 evidence and bind authority to it.

    A binding reused from an earlier attempt would let a later attempt rest on
    evidence gathered before the world changed, which is exactly the drift the
    §22 revalidation exists to catch. The evidence itself is minted by the
    platform sequence in ``run_compatibility``; what belongs to the run is
    deciding that this attempt needs its own.

    A module function rather than a method because two parties need it and must
    not each have their own: the input source mints the authority an attempt is
    admitted under, and the world factory mints the further admissions a
    governed replay binds through. Two copies would drift the day either side
    gained a field.

    ``moment`` is the caller's because only the caller knows which admission
    this is. Stage 3 records carry no clock of their own, so two admissions
    minted at one instant produce byte-identical revalidations and the
    append-only history refuses the second -- correctly: they would be one
    statement recorded twice, not two admissions.
    """
    minted = mint_compatibility_evidence(
        authority_handle=environment.authority_handle,
        bindings=CompatibilityEvaluatorBindings(
            declaration=environment.declaration,
            observation=environment.observation,
            observation_provider=environment.observation_provider,
            evidence_resolver=environment.evidence_resolver,
            conflict_assessor=environment.conflict_assessor,
            binding_repo_root=environment.repo_root,
            retriever_actor=environment.retriever_actor,
            consumer_actor=environment.consumer_actor,
            score_provider_actor=environment.score_provider_actor,
        ),
        library=environment.library,
        library_snapshot=environment.library.current_snapshot().snapshot,
        lifecycle_store=environment.lifecycle_store,
        attestation_store=environment.attestation_store,
        taint_store=environment.taint_store,
        compatibility_history=environment.compatibility_history,
        candidates=environment.supported,
        trusted_clock=lambda: moment,
    )
    authority_binding = P.create_production_authority_binding(
        controller=environment.controller,
        lifecycle_store=environment.lifecycle_store,
        attestation_store=environment.attestation_store,
        taint_store=environment.taint_store,
        admission_journal=environment.admission_journal,
        admission_causal_history=environment.admission_causal_history,
        compatibility_history=environment.compatibility_history,
        compatibility_probe=minted.durable_revalidation_probe,
        knowledge_store=environment.knowledge_store,
        snapshot_attempt_id=environment.snapshot_attempt_id,
        snapshot_evaluator_declaration=environment.snapshot_evaluator_declaration,
        snapshot_actor_set=environment.snapshot_actor_set,
        snapshot_independence_proof=environment.snapshot_independence_proof,
    )
    return AttemptAuthority(
        evaluator=minted.evaluator,
        context=minted.context,
        authority_binding=authority_binding,
    )


class GoldAttemptInputSource:
    """The production ``AttemptInputsPort``: one attempt, one coherent input set."""

    def __init__(
        self,
        *,
        worlds: AttemptWorldPort,
        plan_profile: GoldAttemptPlanProfile,
        worktrees: AttemptWorktreePort,
        context_budget: ContextSizeBudget | None = None,
    ) -> None:
        if type(plan_profile) is not GoldAttemptPlanProfile:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan profile must be exact")
        for name, port, protocol in (
            ("worlds", worlds, AttemptWorldPort),
            ("worktrees", worktrees, AttemptWorktreePort),
        ):
            if not isinstance(port, protocol):
                raise _fail(
                    GoldRunFailureCode.TYPE_MISMATCH,
                    f"{name} does not implement its declared port",
                )
        budget = ContextSizeBudget() if context_budget is None else context_budget
        if type(budget) is not ContextSizeBudget:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "context budget must be exact")
        self._worlds = worlds
        self._plan_profile = plan_profile
        self._worktrees = worktrees
        self._context_budget = budget

    def prepare(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        previous_context: object | None,
    ) -> AttemptInputAvailability:
        """Assemble this attempt's inputs, or report a typed absence.

        The order is the contract: this attempt's own world, then fresh Stage 3
        evidence, then the authority binding over that evidence, then gated
        retrieval, then the admission request, then the accepted plan. A later
        step never re-decides an earlier one, and no step is skipped because an
        earlier attempt already produced a record that looks the same.

        ``previous_context`` decides whether there is anything to attempt at
        all. A continued attempt runs its own retrieval over its own snapshot,
        and if that retrieval reaches the *same* causal record the attempt
        before it did, nothing was admitted or revalidated in between: the
        second attempt would consume exactly what the first one already had, so
        it is reported as a typed absence rather than run.
        """

        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        if type(attempt_index) is not int or attempt_index < _MIN_ATTEMPT_INDEX:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt index must be a one-based integer",
            )

        world = self._worlds.world_for_attempt(
            manifest=manifest,
            attempt_index=attempt_index,
            previous_context=previous_context,
        )
        environment = require_gold_attempt_environment(
            getattr(world, "environment", None)
        )
        retrieval = _attempt_port(world, "retrieval", AttemptRetrievalPort)
        replay = _attempt_port(world, "replay", AttemptReplayPort)

        minted = mint_attempt_authority(
            environment=environment,
            moment=environment.trusted_clock() + timedelta(seconds=attempt_index),
        )
        retrieved = retrieval.retrieve_for_attempt(
            manifest=manifest,
            attempt_index=attempt_index,
            evaluator=minted.evaluator,
            compatibility_context=minted.context,
        )
        gate_decision = getattr(retrieved, "gate_decision", None)
        causal_record = getattr(getattr(retrieved, "result", None), "causal_record", None)
        if gate_decision is None or causal_record is None:
            return KnowledgeDependencyUnavailable(
                attempt_index=attempt_index,
                detail_code="retrieval_produced_no_durable_causal_record",
            )
        absence = self._no_new_knowledge(
            attempt_index=attempt_index,
            previous_context=previous_context,
            causal_record=causal_record,
        )
        if absence is not None:
            return absence

        replay_result = replay.replay_for_attempt(
            manifest=manifest, attempt_index=attempt_index
        )
        admission_request = P.create_point_of_use_admission_request(
            handle=environment.admitted_handle,
            binding=minted.authority_binding,
            chain=environment.chain,
            evidence=environment.chain_evidence,
            entitlements=environment.entitlements,
            requested=environment.requested,
        )
        plan = accept_attempt_plan(
            profile=self._plan_profile,
            repository_revision_sha256=manifest.config.base_revision,
            knowledge_snapshot_ref=environment.knowledge_snapshot_ref,
        )
        return PreparedAttemptInputs(
            admission_request=admission_request,
            retrieval_gate_decision=gate_decision,
            retrieval_causal_record=causal_record,
            replay_result=replay_result,
            intent=plan.intent,
            accepted_plan=plan.accepted,
            plan_authority=plan.authority,
            knowledge_items=(),
            excluded_refs=_excluded_refs(environment, replay_result),
            context_budget=self._context_budget,
            worker_worktree=self._worktrees.worktree_for_attempt(
                manifest=manifest, attempt_index=attempt_index
            ),
            current_plan_state_reader=_CurrentPlanStateReader(
                compatibility_probe=minted.authority_binding.compatibility_probe,
                repository_revision=manifest.config.base_revision,
                knowledge_snapshot_ref=environment.knowledge_snapshot_ref,
                policy_sha256=plan.authority.policy.sha256,
            ),
        )
    def _no_new_knowledge(
        self,
        *,
        attempt_index: int,
        previous_context: object | None,
        causal_record: object,
    ) -> NoNewKnowledge | None:
        """Report the typed absence when this attempt retrieved what the last did.

        The comparison is between *causal records*, not between candidate sets:
        a retrieval names the exact decision it made durable, so two attempts
        reaching the same record reached the same decision over the same frozen
        set. Comparing anything looser would call a run continuable because two
        candidate lists happened to print the same.
        """

        if previous_context is None:
            return None
        previous_ref = getattr(
            getattr(previous_context, "phase_refs", None), "retrieval_ref", None
        )
        if type(previous_ref) is not HashBoundRef:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "the previous attempt context names no retrieval",
            )
        if retrieval_causal_record_ref(causal_record).to_dict() != previous_ref.to_dict():
            return None
        return NoNewKnowledge(
            attempt_index=attempt_index, previous_retrieval_ref=previous_ref
        )


def _attempt_port(world: object, name: str, protocol: type) -> object:
    """Take one of this attempt's ports off the world that built them together.

    Read from the world rather than from the run's construction, because a port
    supplied once per run would answer every attempt with the first attempt's
    decision -- which is exactly what a one-shot retrieval owner refuses, and
    what a governed replay cannot refuse because its record looks valid.
    """

    port = getattr(world, name, None)
    if not isinstance(port, protocol):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            f"the attempt world supplies no {name} implementing its declared port",
        )
    return port


def _excluded_refs(
    environment: GoldAttemptEnvironment, replay_result: object
) -> tuple[ExcludedKnowledgeRef, ...]:
    """Every frozen candidate the replay did not deliver is a stated exclusion.

    Absence is recorded rather than implied: a candidate that was in the
    universe and did not reach the worker carries a reason, so a later reader
    cannot mistake "not selected" for "never existed".
    """

    delivered = {
        content_key_digest(observation.behavior_content_key)
        for observation in getattr(replay_result, "observations", ())
    }
    return tuple(
        ExcludedKnowledgeRef(ref=reference, reason=ExclusionReason.NOT_SELECTED_FOR_TASK)
        for reference in environment.subjects
        if reference.ref_id not in delivered
    )


@dataclass(frozen=True)
class AttemptAuthority:
    """The evaluator, its context and the authority binding minted together."""

    evaluator: object
    context: object
    authority_binding: object


@dataclass(frozen=True)
class _CurrentPlanStateReader:
    """Read the current plan state from this attempt's own fresh revalidation."""

    compatibility_probe: object
    repository_revision: str
    knowledge_snapshot_ref: HashBoundRef
    policy_sha256: str

    def read_current_plan_state(self, *, admitted_knowledge: object) -> CurrentPlanState:
        records = self.compatibility_probe.records
        if not records:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "a fresh admission produced no compatibility revalidation to read",
            )
        return CurrentPlanState(
            repository_revision_sha256=self.repository_revision,
            knowledge_snapshot_ref=self.knowledge_snapshot_ref,
            policy_sha256=self.policy_sha256,
            admitted_knowledge=admitted_knowledge,
            compatibility_revalidation=records[0],
        )


__all__ = [
    "AttemptReplayPort",
    "AttemptRetrievalPort",
    "AttemptWorktreePort",
    "GoldAttemptInputSource",
]
