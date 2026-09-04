"""Production assembly of one already-authorised attempt's Stage 3/7/8/10 inputs.

This owner materializes an attempt only after the run controller has authorised
that attempt. It prepares a provisional knowledge basis for the delivery owner
to finalize at point of use; durable basis storage belongs to the run-record
materializer rather than to this input source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import HashBoundRef, content_key_digest
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
from .attempt_knowledge import create_attempt_knowledge_basis
from .attempt_inputs import (
    AttemptInputAvailability,
    KnowledgeDependencyUnavailable,
    PreparedAttemptInputs,
)
from .attempt_plan import GoldAttemptPlanProfile, accept_attempt_plan, check_attempt_plan_approval
from .models import GoldAttemptContext, GoldAttemptResult, GoldRunManifest
from .vocabulary import GoldRunFailureCode, GoldRunViolation


_MIN_ATTEMPT_INDEX = 1


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@runtime_checkable
class AttemptReplayPort(Protocol):
    def replay_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int
    ) -> object: ...


@runtime_checkable
class AttemptRetrievalPort(Protocol):
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
    def worktree_for_attempt(
        self, *, manifest: GoldRunManifest, attempt_index: int
    ) -> Path: ...


@runtime_checkable
class AttemptWorldPort(Protocol):
    def world_for_attempt(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        previous_context: object | None,
    ) -> object: ...


def mint_attempt_authority(
    *, environment: GoldAttemptEnvironment, moment: datetime
) -> "AttemptAuthority":
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
    return AttemptAuthority(minted.evaluator, minted.context, authority_binding, minted)


class GoldAttemptInputSource:
    """Production ``AttemptInputsPort`` for an already-authorised attempt."""

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

    def check_approval(self, *, manifest: GoldRunManifest) -> None:
        check_attempt_plan_approval(profile=self._plan_profile, manifest=manifest)

    def prepare(
        self,
        *,
        manifest: GoldRunManifest,
        attempt_index: int,
        previous_context: object | None,
    ) -> AttemptInputAvailability:
        if type(manifest) is not GoldRunManifest:
            raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "manifest must be exact")
        if type(attempt_index) is not int or attempt_index < _MIN_ATTEMPT_INDEX:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "attempt index must be one-based",
            )
        previous = getattr(previous_context, "context", previous_context)
        previous_result = getattr(previous_context, "result", None)
        if attempt_index > 1:
            if type(previous) is not GoldAttemptContext or type(previous_result) is not GoldAttemptResult:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "continued preparation requires the completed predecessor")
            previous.validate_identity()
            previous_result.validate_identity()
            if (
                previous_result.run_id != manifest.run_id
                or previous_result.attempt_index != attempt_index - 1
                or previous_result.context_sha256 != previous.context_sha256
            ):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "feedback belongs to another predecessor")
        world = self._worlds.world_for_attempt(
            manifest=manifest,
            attempt_index=attempt_index,
            previous_context=previous,
        )
        environment = require_gold_attempt_environment(getattr(world, "environment", None))
        approval = self._plan_profile.approval_policy
        if approval is not None:
            if approval.run_manifest_sha256 != manifest.manifest_sha256:
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "approval is bound to another manifest")
            if approval.store_root.resolve().is_relative_to(environment.repo_root.resolve()):
                raise _fail(GoldRunFailureCode.AUTHORITY_MISMATCH, "approval store must be outside worker repository scope")
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
        basis = create_attempt_knowledge_basis(
            run_id=manifest.run_id.value,
            attempt_id=str(attempt_index),
            attempt_index=attempt_index,
            admitted_subject_refs=_admitted_subject_refs(environment, gate_decision),
            retrieval_gate_decision_ref=A.gate_decision_ref(gate_decision),
            consumer_context_ref=environment.consumer_context_ref,
            boundary_ref=environment.admitted_handle.boundary_ref,
            policy_version=environment.admitted_handle.policy_version,
        )
        replay_result = replay.replay_for_attempt(
            manifest=manifest,
            attempt_index=attempt_index,
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
            compatibility=minted.compatibility,
            compatibility_history=environment.compatibility_history,
            previous_result=previous_result,
        )
        return PreparedAttemptInputs(
            admission_request=admission_request,
            retrieval_gate_decision=gate_decision,
            retrieval_causal_record=causal_record,
            replay_result=replay_result,
            intent=plan.intent,
            accepted_plan=plan.accepted,
            plan_authority=plan.authority,
            plan_semantic_sha256=plan.semantic_sha256,
            knowledge_items=(),
            excluded_refs=_excluded_refs(environment, replay_result),
            context_budget=self._context_budget,
            worker_worktree=self._worktrees.worktree_for_attempt(
                manifest=manifest,
                attempt_index=attempt_index,
            ),
            knowledge_basis=basis,
            knowledge_basis_sha256=basis.digest(),
            current_plan_state_reader=_CurrentPlanStateReader(
                minted.authority_binding.compatibility_probe,
                manifest.config.base_revision,
                environment.knowledge_snapshot_ref,
                plan.authority.policy.sha256,
            ),
        )


@dataclass(frozen=True)
class AttemptAuthority:
    evaluator: object
    context: object
    authority_binding: object
    compatibility: object


@dataclass(frozen=True)
class _CurrentPlanStateReader:
    compatibility_probe: object
    repository_revision: str
    knowledge_snapshot_ref: HashBoundRef
    policy_sha256: str

    def read_current_plan_state(self, *, admitted_knowledge: object) -> CurrentPlanState:
        records = self.compatibility_probe.records
        if not records:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "fresh admission produced no revalidation",
            )
        return CurrentPlanState(
            repository_revision_sha256=self.repository_revision,
            knowledge_snapshot_ref=self.knowledge_snapshot_ref,
            policy_sha256=self.policy_sha256,
            admitted_knowledge=admitted_knowledge,
            compatibility_revalidation=records[0],
        )


def _admitted_subject_refs(
    environment: GoldAttemptEnvironment, gate_decision: object
) -> tuple[HashBoundRef, ...]:
    admitted = getattr(environment.admitted_handle, "subject_refs", None)
    if type(admitted) is not tuple or not admitted:
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "the admitted handle names no subjects",
        )
    selectable = frozenset(getattr(gate_decision, "subject_refs", ()))
    if any(item not in selectable for item in admitted):
        raise _fail(
            GoldRunFailureCode.AUTHORITY_MISMATCH,
            "an admitted subject was never selectable",
        )
    return admitted


def _attempt_port(world: object, name: str, protocol: type) -> object:
    port = getattr(world, name, None)
    if not isinstance(port, protocol):
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            f"attempt world supplies no {name}",
        )
    return port


def _excluded_refs(
    environment: GoldAttemptEnvironment, replay_result: object
) -> tuple[ExcludedKnowledgeRef, ...]:
    delivered = {
        content_key_digest(observation.behavior_content_key)
        for observation in getattr(replay_result, "observations", ())
    }
    return tuple(
        ExcludedKnowledgeRef(
            ref=reference,
            reason=ExclusionReason.NOT_SELECTED_FOR_TASK,
        )
        for reference in environment.subjects
        if reference.ref_id not in delivered
    )


__all__ = [
    "AttemptReplayPort",
    "AttemptRetrievalPort",
    "AttemptWorktreePort",
    "GoldAttemptInputSource",
]
