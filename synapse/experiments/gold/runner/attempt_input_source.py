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

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import HashBoundRef, content_key_digest
from synapse.experiments.gold.compatibility import (
    configure_compatibility_evaluator,
    create_compatibility_context,
    evaluate_compatibility,
    evaluate_conflicts,
    revalidate_before_loading,
)
from synapse.experiments.gold.compatibility_store import (
    CompatibilityStoreFailureCode,
    CompatibilityStoreViolation,
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


class GoldAttemptInputSource:
    """The production ``AttemptInputsPort``: one attempt, one coherent input set."""

    def __init__(
        self,
        *,
        environment: GoldAttemptEnvironment,
        plan_profile: GoldAttemptPlanProfile,
        replay: AttemptReplayPort,
        retrieval: AttemptRetrievalPort,
        worktrees: AttemptWorktreePort,
        context_budget: ContextSizeBudget | None = None,
    ) -> None:
        self._environment = require_gold_attempt_environment(environment)
        if type(plan_profile) is not GoldAttemptPlanProfile:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "plan profile must be exact")
        for name, port, protocol in (
            ("replay", replay, AttemptReplayPort),
            ("retrieval", retrieval, AttemptRetrievalPort),
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
        self._plan_profile = plan_profile
        self._replay = replay
        self._retrieval = retrieval
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

        The order is the contract: fresh Stage 3 evidence, then the authority
        binding over that evidence, then gated retrieval, then the admission
        request, then the accepted plan. A later step never re-decides an
        earlier one, and no step is skipped because an earlier attempt already
        produced a record that looks the same.
        """

        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        if type(attempt_index) is not int or attempt_index < _MIN_ATTEMPT_INDEX:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt index must be a one-based integer",
            )

        environment = require_gold_attempt_environment(self._environment)
        minted = self._mint_authority_binding(attempt_index=attempt_index)
        retrieved = self._retrieval.retrieve_for_attempt(
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

        replay_result = self._replay.replay_for_attempt(
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
            excluded_refs=self._excluded_refs(replay_result),
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

    def _mint_authority_binding(self, *, attempt_index: int) -> "_AttemptAuthorityBinding":
        """Mint this attempt's own Stage 3 evidence and bind authority to it.

        A binding reused from an earlier attempt would let a later attempt rest
        on evidence gathered before the world changed, which is exactly the
        drift the §22 revalidation exists to catch.
        """

        environment = self._environment
        moment = environment.trusted_clock() + timedelta(seconds=attempt_index)
        evaluator = configure_compatibility_evaluator(
            authority_handle=environment.authority_handle,
            declaration=environment.declaration,
            evaluator_component_id=environment.declaration.evaluator_component_id,
            evaluator_component_version=environment.declaration.evaluator_component_version,
            trusted_clock=lambda: moment,
            platform_observation_provider=environment.observation_provider,
            library=environment.library,
            lifecycle_store=environment.lifecycle_store,
            attestation_store=environment.attestation_store,
            taint_store=environment.taint_store,
            evidence_resolver=environment.evidence_resolver,
            binding_repo_root=environment.repo_root,
            conflict_assessor=environment.conflict_assessor,
            retriever_actor=environment.retriever_actor,
            consumer_actor=environment.consumer_actor,
            score_provider_actor=environment.score_provider_actor,
        )
        context = create_compatibility_context(
            evaluator=evaluator,
            authority_handle=environment.authority_handle,
            observation=environment.observation,
            library_snapshot=environment.library.current_snapshot().snapshot,
            lifecycle_snapshot=environment.lifecycle_store.snapshot(),
            consumer_actor=evaluator.consumer_actor,
        )
        supported = environment.supported
        decisions = tuple(
            evaluate_compatibility(
                evaluator=evaluator, context=context, descriptor=item[1], index_entry=item[2]
            )
            for item in supported
        )
        scan = evaluate_conflicts(
            evaluator=evaluator,
            context=context,
            decisions=decisions,
            descriptors=tuple(item[1] for item in supported),
            considered_index_entries=tuple(item[2] for item in supported),
            proposals=(),
        )
        before_loading = tuple(
            revalidate_before_loading(
                evaluator=evaluator,
                context=context,
                descriptor=item[1],
                original_decision=decisions[index],
            )
            for index, item in enumerate(supported)
        )
        self._commit_evidence(
            context=context, decisions=decisions, scan=scan, before_loading=before_loading
        )
        probe = GF.configured_durable_revalidation_probe(
            evaluator=evaluator,
            context=context,
            bindings=tuple(
                GF.bind_consumption_evidence(
                    descriptor=item[1],
                    original_decision=decisions[index],
                    before_loading=before_loading[index],
                    conflict_scan=scan,
                )
                for index, item in enumerate(supported)
            ),
            compatibility_history=environment.compatibility_history,
        )
        authority_binding = P.create_production_authority_binding(
            controller=environment.controller,
            lifecycle_store=environment.lifecycle_store,
            attestation_store=environment.attestation_store,
            taint_store=environment.taint_store,
            admission_journal=environment.admission_journal,
            admission_causal_history=environment.admission_causal_history,
            compatibility_history=environment.compatibility_history,
            compatibility_probe=probe,
            knowledge_store=environment.knowledge_store,
            snapshot_attempt_id=environment.snapshot_attempt_id,
            snapshot_evaluator_declaration=environment.snapshot_evaluator_declaration,
            snapshot_actor_set=environment.snapshot_actor_set,
            snapshot_independence_proof=environment.snapshot_independence_proof,
        )
        return _AttemptAuthorityBinding(
            evaluator=evaluator, context=context, authority_binding=authority_binding
        )

    def _commit_evidence(
        self,
        *,
        context: object,
        decisions: tuple,
        scan: object,
        before_loading: tuple,
    ) -> None:
        """Append this attempt's evidence to the append-only history.

        A later attempt can legitimately reproduce a byte-identical record; the
        history refuses that duplicate and the refusal is the correct outcome,
        because the record it already holds is the one required. Any other
        refusal is a real failure and is not swallowed.
        """

        history = self._environment.compatibility_history
        records: list[object] = [context]
        for decision in decisions:
            records.extend((decision.evidence, decision))
        records.append(scan)
        records.extend(before_loading)
        for record in records:
            try:
                history.append_record(record, expected_parent_anchor=history.current_anchor())
            except CompatibilityStoreViolation as exc:
                if exc.failure_code is not CompatibilityStoreFailureCode.RECORD_DUPLICATE:
                    raise

    def _excluded_refs(self, replay_result: object) -> tuple[ExcludedKnowledgeRef, ...]:
        """Every frozen candidate the replay did not deliver is a stated exclusion.

        Absence is recorded rather than implied: a candidate that was in the
        universe and did not reach the worker carries a reason, so a later
        reader cannot mistake "not selected" for "never existed".
        """

        delivered = {
            content_key_digest(observation.behavior_content_key)
            for observation in getattr(replay_result, "observations", ())
        }
        return tuple(
            ExcludedKnowledgeRef(ref=reference, reason=ExclusionReason.NOT_SELECTED_FOR_TASK)
            for reference in self._environment.subjects
            if reference.ref_id not in delivered
        )


@dataclass(frozen=True)
class _AttemptAuthorityBinding:
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
