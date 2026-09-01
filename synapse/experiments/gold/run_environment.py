"""The one production composition of a run's sealed attempt environment (§21-§22).

The three assemblies a run needs before it may consume anything — the committed
snapshot, this consumption's Stage 3 evidence, and the four-gate admission
chain — are each a platform module now. Nothing composed them. This is that
composition, and there is exactly one of it: a caller free to run the three in
its own order could commit a boundary, then mint evidence against a world that
had already moved past it, and the sealed environment would still look valid.

The order is therefore fixed here:

    committed snapshot -> Stage 3 evidence -> gate chain -> sealed environment

Each step consumes the previous one's records rather than rebuilding them. The
gate controller's compatibility answer comes from the evidence minted in step
two, not from a callable a caller supplies; the environment is sealed over the
handle the gates actually issued, not over one assembled beside them.

What the caller still declares is what only a deployment knows: which
candidates exist, who the actors are, and the probes that answer taint,
provenance, lifecycle and entitlement questions about live state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import AttemptId, RunId
from synapse.experiments.gold.run_compatibility import (
    CompatibilityEvaluatorBindings,
    MintedCompatibilityEvidence,
    mint_compatibility_evidence,
)
from synapse.experiments.gold.run_gate_chain import (
    AdmittedRunKnowledge,
    AuthorityStores,
    GateActorDeclaration,
    GateProbeBindings,
    admit_run_knowledge,
    configure_run_gate_controller,
)
from synapse.experiments.gold.run_snapshot import (
    CommittedRunSnapshot,
    SnapshotActorDeclaration,
    commit_run_snapshot,
)
from synapse.experiments.gold.runner.attempt_environment import (
    GoldAttemptEnvironment,
    create_gold_attempt_environment,
)
from synapse.experiments.gold.runner.vocabulary import (
    GoldRunFailureCode,
    GoldRunViolation,
)


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class AssembledRunEnvironment:
    """The sealed environment and the records the three steps produced.

    The intermediate records are returned rather than discarded because they
    are the evidence for the environment: a caller auditing why a subject was
    admitted needs the decisions, not only the handle they produced.
    """

    environment: GoldAttemptEnvironment
    snapshot: CommittedRunSnapshot
    evidence: MintedCompatibilityEvidence
    admission: AdmittedRunKnowledge
    consumer_context_ref: HashBoundRef


def assemble_run_environment(
    *,
    authority_handle: object,
    stores: AuthorityStores,
    library: object,
    repo_root: Path,
    snapshot_root: Path,
    candidates: tuple[tuple[object, object, object], ...],
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: str,
    policy_version: str,
    environment_profile_id: str,
    snapshot_actors: SnapshotActorDeclaration,
    compatibility: CompatibilityEvaluatorBindings,
    gate_actors: GateActorDeclaration,
    gate_probes: GateProbeBindings,
    requested: object,
    created_at_utc: datetime,
    trusted_clock: Callable[[], datetime],
    gate_clock: Callable[[], datetime],
    ref_resolver: Callable[[object], bool],
    consumability_probe: Callable[[object], bool],
    transaction_id: str,
    frozen_candidate_set_ref: HashBoundRef | None = None,
    retrieval_decision: Callable[..., object] | None = None,
) -> AssembledRunEnvironment:
    """Assemble and seal one run's attempt environment, in the one valid order."""

    if type(stores) is not AuthorityStores:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "authority stores must be exact")
    if type(candidates) is not tuple or not candidates:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "a run environment needs a non-empty candidate universe",
        )

    #: Read before the boundary is committed, because that is the library state
    #: the snapshot's roots describe. Re-reading it afterwards would let the
    #: evidence be about a generation the manifest never named.
    library_snapshot = library.current_snapshot().snapshot

    snapshot = commit_run_snapshot(
        snapshot_root=snapshot_root,
        knowledge_store=stores.knowledge_store,
        library=library,
        lifecycle_store=stores.lifecycle_store,
        admission_journal=stores.admission_journal,
        admission_causal_history=stores.admission_causal_history,
        compatibility_history=stores.compatibility_history,
        authority_handle=authority_handle,
        mutation_fence=stores.mutation_fence,
        descriptors=tuple(item[1] for item in candidates),
        run_id=run_id,
        attempt_id=attempt_id,
        repository_revision=repository_revision,
        policy_version=policy_version,
        environment_profile_id=environment_profile_id,
        actors=snapshot_actors,
        created_at_utc=created_at_utc,
        trusted_clock=trusted_clock,
        ref_resolver=ref_resolver,
        consumability_probe=consumability_probe,
        transaction_id=transaction_id,
    )
    evidence = mint_compatibility_evidence(
        authority_handle=authority_handle,
        bindings=compatibility,
        library=library,
        library_snapshot=library_snapshot,
        lifecycle_store=stores.lifecycle_store,
        attestation_store=stores.attestation_store,
        taint_store=stores.taint_store,
        compatibility_history=stores.compatibility_history,
        candidates=candidates,
        trusted_clock=trusted_clock,
    )
    controller, evaluator_declaration = configure_run_gate_controller(
        authority_handle=authority_handle,
        stores=stores,
        actors=gate_actors,
        probes=gate_probes,
        evidence=evidence,
        boundary_ref=snapshot.boundary_ref,
        run_id=run_id,
        attempt_id=attempt_id,
        repository_revision=repository_revision,
        policy_version=policy_version,
        environment_profile_id=environment_profile_id,
        trusted_clock=gate_clock,
    )
    consumer_context_ref = GF.consumer_context_ref_of(evidence.context)
    admission = admit_run_knowledge(
        authority_handle=authority_handle,
        controller=controller,
        evaluator_declaration=evaluator_declaration,
        stores=stores,
        actors=gate_actors,
        subjects=snapshot.subjects,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=snapshot.boundary_ref,
        requested=requested,
        policy_version=policy_version,
        trusted_clock=gate_clock,
        evidence=evidence,
        snapshot_attempt_id=attempt_id,
        snapshot_evaluator_declaration=snapshot.evaluator_declaration,
        snapshot_actor_set=snapshot.actor_set,
        snapshot_independence_proof=snapshot.independence_proof,
        frozen_candidate_set_ref=frozen_candidate_set_ref,
        retrieval_decision=retrieval_decision,
    )
    environment = create_gold_attempt_environment(
        authority_handle=authority_handle,
        admitted_handle=admission.handle,
        declaration=compatibility.declaration,
        library=library,
        repo_root=repo_root,
        lifecycle_store=stores.lifecycle_store,
        attestation_store=stores.attestation_store,
        taint_store=stores.taint_store,
        admission_journal=stores.admission_journal,
        admission_causal_history=stores.admission_causal_history,
        compatibility_history=stores.compatibility_history,
        knowledge_store=stores.knowledge_store,
        controller=controller,
        chain=admission.chain,
        chain_evidence=admission.chain_evidence,
        entitlements=admission.entitlements,
        requested=requested,
        #: The *manifest* ref, not the boundary ref. The plan and the current
        #: state reader name the snapshot a run planned against; the boundary
        #: ref is what the gate probe checks a subject was admitted under.
        #: Swapping them produces a run that looks whole and checked the wrong
        #: thing.
        knowledge_snapshot_ref=snapshot.boundary.manifest_ref,
        consumer_context_ref=consumer_context_ref,
        subjects=snapshot.subjects,
        supported=candidates,
        snapshot_attempt_id=attempt_id,
        snapshot_evaluator_declaration=snapshot.evaluator_declaration,
        snapshot_actor_set=snapshot.actor_set,
        snapshot_independence_proof=snapshot.independence_proof,
        observation=compatibility.observation,
        observation_provider=compatibility.observation_provider,
        evidence_resolver=compatibility.evidence_resolver,
        conflict_assessor=compatibility.conflict_assessor,
        retriever_actor=compatibility.retriever_actor,
        consumer_actor=compatibility.consumer_actor,
        score_provider_actor=compatibility.score_provider_actor,
        trusted_clock=gate_clock,
    )
    return AssembledRunEnvironment(
        environment=environment,
        snapshot=snapshot,
        evidence=evidence,
        admission=admission,
        consumer_context_ref=consumer_context_ref,
    )


__all__ = ["AssembledRunEnvironment", "assemble_run_environment"]
