"""The four admission gates, in order, and the authority they produce (§22).

This is the last of the three assemblies that had no production home. §22 makes
admission a chain rather than a verdict: ingestion decides whether a candidate
may be extracted from its source at all, publication whether it may become
visible, retrieval whether it may become selectable, and consumption whether it
may be used now — each naming its predecessor, so a decision cannot be lifted
out of the sequence that justified it.

Two properties are the reason this belongs to the platform. The order is fixed
here rather than chosen by a caller, because a chain assembled in another order
is a different claim about what was checked. And the fenced authority state is
read across every participating store at once through one coordinator, so a
reader can tell that lifecycle moved while taint was being read — a per-store
reading would report every observation as coherent.

What the caller supplies is what only the caller can know: the probes that
answer taint, provenance, lifecycle and entitlement questions about a
particular deployment, and — when retrieval is decided durably rather than by
the plain gate — the decision that produced. `configure_gate_controller` has
always taken its probes as parameters; that is the seam, not a shortcut.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import admission_store as S
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    RunId,
)
from synapse.experiments.gold.coordination import read_current_authority_state
from synapse.experiments.gold.gate_findings import require_durable_revalidation_probe
from synapse.experiments.gold.run_compatibility import MintedCompatibilityEvidence

from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


#: One role per gate, fixed. A declaration that mapped a gate to another role
#: would let one evaluator's authority stand in for another's, which is the
#: separation §22 exists to keep.
_GATE_ROLES = {
    A.GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
    A.GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
    A.GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
    A.GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
}


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class GateActorDeclaration:
    """Who evaluates the gates, and the three actors the chain distinguishes."""

    evaluator_identity: AuthorityIdentity
    evaluator_component_id: str
    evaluator_component_version: str
    producer_actor: ActorIdentity
    retriever_actor: ActorIdentity
    consumer_actor: ActorIdentity

    def __post_init__(self) -> None:
        if type(self.evaluator_identity) is not AuthorityIdentity:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "evaluator_identity must be an exact authority identity",
            )
        for name in ("producer_actor", "retriever_actor", "consumer_actor"):
            if type(getattr(self, name)) is not ActorIdentity:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
        for name in ("evaluator_component_id", "evaluator_component_version"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")


@dataclass(frozen=True)
class GateProbeBindings:
    """The four questions about a deployment that the gates cannot answer alone.

    They are callables because each is a reading of live state: whether this
    subject's taint permits consumption, whether it is attested, what its
    effective lifecycle allows, and what the verifier has actually been granted.
    An unavailable probe is expected to raise rather than answer, and the gates
    turn that into a typed unavailability instead of a permission.
    """

    taint_probe: Callable[[HashBoundRef], object]
    provenance_probe: Callable[[HashBoundRef], bool]
    lifecycle_probe: Callable[[HashBoundRef], bool]
    grant_probe: Callable[[], object]

    def __post_init__(self) -> None:
        for name in ("taint_probe", "provenance_probe", "lifecycle_probe", "grant_probe"):
            if not callable(getattr(self, name)):
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be callable")


@dataclass(frozen=True)
class AuthorityStores:
    """The seven participants one coordinator fences together."""

    lifecycle_store: object
    attestation_store: object
    taint_store: object
    admission_journal: object
    admission_causal_history: object
    compatibility_history: object
    knowledge_store: object
    mutation_fence: object

    def participants(self) -> tuple[object, ...]:
        return (
            self.lifecycle_store,
            self.attestation_store,
            self.taint_store,
            self.admission_journal,
            self.admission_causal_history,
            self.compatibility_history,
            self.knowledge_store,
        )


@dataclass(frozen=True)
class AdmittedRunKnowledge:
    """One completed admission: every decision, the chain, and the authority."""

    controller: object
    evaluator_declaration: object
    entitlements: dict
    ingestion: object
    publication: object
    retrieval: object
    consumption: object
    chain: object
    chain_evidence: object
    fenced_state: object
    handle: object
    authority_binding: object


def configure_run_gate_controller(
    *,
    authority_handle: object,
    stores: AuthorityStores,
    actors: GateActorDeclaration,
    probes: GateProbeBindings,
    evidence: MintedCompatibilityEvidence,
    boundary_ref: HashBoundRef,
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: str,
    policy_version: str,
    environment_profile_id: str,
    trusted_clock: Callable[[], datetime],
) -> tuple[object, object]:
    """Bind one gate controller to this run's stores, probes and boundary."""

    if type(actors) is not GateActorDeclaration or type(probes) is not GateProbeBindings:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "gate actors and probes must be exact")
    if type(stores) is not AuthorityStores:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "authority stores must be exact")
    #: The §22 consumption gate's compatibility answer has to come from a real
    #: Stage 3 revalidation of *this* subject against *this* context. Anything
    #: else handed to ``compatibility_probe`` is a callable of the caller's own
    #: devising, and the gate would then consult something about compatibility
    #: with nothing requiring that something to be a revalidation at all — so
    #: the probes arrive as minted evidence rather than as a parameter.
    if type(evidence) is not MintedCompatibilityEvidence:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "the gate controller takes its compatibility probe from minted Stage 3 evidence",
        )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=authority_handle,
        evaluator_identity=actors.evaluator_identity,
        evaluator_component_id=actors.evaluator_component_id,
        evaluator_component_version=actors.evaluator_component_version,
        gate_roles=dict(_GATE_ROLES),
        policy_version=policy_version,
        trusted_clock=trusted_clock,
    )
    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version=policy_version,
        run_id=run_id,
        attempt_id=attempt_id,
        repository_revision=repository_revision,
        environment_profile_id=environment_profile_id,
        trusted_clock=trusted_clock,
        taint_probe=probes.taint_probe,
        provenance_probe=probes.provenance_probe,
        lifecycle_probe=probes.lifecycle_probe,
        compatibility_probe=evidence.revalidation_probe,
        boundary_probe=_boundary_probe(boundary_ref),
        grant_probe=probes.grant_probe,
        head_reader=_head_reader(stores, boundary_ref),
        producer_actor=actors.producer_actor,
        retriever_actor=actors.retriever_actor,
        consumer_actor=actors.consumer_actor,
    )
    return controller, declaration


def admit_run_knowledge(
    *,
    authority_handle: object,
    controller: object,
    evaluator_declaration: object,
    stores: AuthorityStores,
    actors: GateActorDeclaration,
    subjects: tuple[HashBoundRef, ...],
    consumer_context_ref: HashBoundRef,
    boundary_ref: HashBoundRef,
    requested: object,
    policy_version: str,
    trusted_clock: Callable[[], datetime],
    evidence: MintedCompatibilityEvidence,
    snapshot_attempt_id: AttemptId,
    snapshot_evaluator_declaration: object,
    snapshot_actor_set: object,
    snapshot_independence_proof: object,
    frozen_candidate_set_ref: HashBoundRef | None = None,
    retrieval_decision: Callable[..., object] | None = None,
) -> AdmittedRunKnowledge:
    """Run the four gates in order and bind the authority they produce.

    Retrieval is the one gate a run may decide durably instead of by the plain
    gate — §20's ranking and gating happen together over a frozen candidate set
    — so the caller may supply that decision. Everything else about the order
    stays here: a chain whose retrieval came from elsewhere is still a chain
    whose consumption names it as predecessor.
    """

    #: The entitlement a chain carries is the *verifier's* declaration, not the
    #: controller's, even when one component fills both roles. Reusing the
    #: controller's object would make the chain assert that whoever decided also
    #: vouched for the decision.
    verifier_declaration = AC.create_gate_evaluator_declaration(
        authority_handle=authority_handle,
        evaluator_identity=actors.evaluator_identity,
        evaluator_component_id=actors.evaluator_component_id,
        evaluator_component_version=actors.evaluator_component_version,
        gate_roles=dict(_GATE_ROLES),
        policy_version=policy_version,
        trusted_clock=trusted_clock,
    )
    verifier_actors = (
        actors.producer_actor,
        actors.retriever_actor,
        actors.consumer_actor,
    )
    entitlements = {gate: (verifier_declaration, verifier_actors) for gate in A.GateKind}
    #: Refused here rather than deep inside the binding: a durable probe that is
    #: not factory sealed is a forged Stage 3 answer, and the authority binding
    #: is the last place it could still be caught before it is trusted.
    durable_probe = require_durable_revalidation_probe(evidence.durable_revalidation_probe)
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subjects)
    publication = A.evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    if retrieval_decision is None:
        if type(frozen_candidate_set_ref) is not HashBoundRef:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "the plain retrieval gate needs the frozen candidate set it decides over",
            )
        retrieval = A.evaluate_retrieval_gate(
            controller,
            subject_refs=subjects,
            consumer_context_ref=consumer_context_ref,
            boundary_ref=boundary_ref,
            frozen_candidate_set_ref=frozen_candidate_set_ref,
            requested=requested,
            predecessor=publication,
        )
    else:
        #: A durable retrieval decides over the frozen candidate set and needs
        #: the same evidence and snapshot authority this admission is using --
        #: handed over rather than looked up, so it cannot decide against a
        #: different one.
        retrieval = retrieval_decision(
            controller=controller,
            subjects=subjects,
            consumer_context_ref=consumer_context_ref,
            boundary_ref=boundary_ref,
            requested=requested,
            publication_decision=publication,
            entitlements=entitlements,
            evidence=evidence,
            snapshot_evaluator_declaration=snapshot_evaluator_declaration,
            snapshot_actor_set=snapshot_actor_set,
            snapshot_independence_proof=snapshot_independence_proof,
        )
        if retrieval is None:
            raise _fail(
                GoldRunFailureCode.AUTHORITY_MISMATCH,
                "durable retrieval admitted no subject for this consumption",
            )
    consumption = A.evaluate_consumption_gate(
        controller,
        subject_refs=subjects,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        requested=requested,
        predecessor=retrieval,
    )
    chain = A.build_gate_decision_chain(
        ingestion=ingestion,
        publication=publication,
        retrieval=retrieval,
        consumption=consumption,
        entitlements=entitlements,
    )
    chain_evidence = S.commit_gate_chain(
        chain, store=stores.admission_journal, trusted_clock=trusted_clock
    )
    #: One reading across every participant under one coordinator: a per-store
    #: reading cannot report that lifecycle moved while taint was being read.
    fenced_state = read_current_authority_state(
        controller, fence=stores.mutation_fence, participants=stores.participants()
    )
    handle = A.admit_for_consumption(
        chain,
        controller=controller,
        subject_refs=subjects,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy_version=policy_version,
        receipts=chain_evidence.receipts,
        fenced_state=fenced_state,
        journal=stores.admission_journal,
        entitlements=entitlements,
    )
    authority_binding = P.create_production_authority_binding(
        controller=controller,
        lifecycle_store=stores.lifecycle_store,
        attestation_store=stores.attestation_store,
        taint_store=stores.taint_store,
        admission_journal=stores.admission_journal,
        admission_causal_history=stores.admission_causal_history,
        compatibility_history=stores.compatibility_history,
        compatibility_probe=durable_probe,
        knowledge_store=stores.knowledge_store,
        snapshot_attempt_id=snapshot_attempt_id,
        snapshot_evaluator_declaration=snapshot_evaluator_declaration,
        snapshot_actor_set=snapshot_actor_set,
        snapshot_independence_proof=snapshot_independence_proof,
    )
    return AdmittedRunKnowledge(
        controller=controller,
        evaluator_declaration=evaluator_declaration,
        entitlements=entitlements,
        ingestion=ingestion,
        publication=publication,
        retrieval=retrieval,
        consumption=consumption,
        chain=chain,
        chain_evidence=chain_evidence,
        fenced_state=fenced_state,
        handle=handle,
        authority_binding=authority_binding,
    )


def _boundary_probe(boundary_ref: HashBoundRef) -> Callable[[HashBoundRef], bool]:
    """A subject may only be consumed under the boundary this run committed."""

    return lambda item: item.to_dict() == boundary_ref.to_dict()


def _head_reader(stores: AuthorityStores, boundary_ref: HashBoundRef) -> Callable[[], dict]:
    """Report every history's head together with the boundary they sit under."""

    def read() -> dict:
        lifecycle = stores.lifecycle_store.current_anchor()
        provenance = stores.attestation_store.current_anchor()
        taint = stores.taint_store.current_anchor()
        return {
            "boundary_ref": boundary_ref,
            "heads": {
                "lifecycle": {
                    "anchor_sha256": lifecycle.ordered_log_root_sha256,
                    "sequence": lifecycle.entry_count,
                },
                "provenance": {
                    "anchor_sha256": provenance.ordered_log_root_sha256,
                    "sequence": provenance.entry_count,
                },
                "taint": {
                    "anchor_sha256": taint.ordered_log_root_sha256,
                    "sequence": taint.entry_count,
                },
                "admission_decision": {
                    "anchor_sha256": stores.admission_journal.current_anchor(),
                    "sequence": stores.admission_journal.current_sequence(),
                },
                "retrieval_causal": {
                    "anchor_sha256": stores.admission_causal_history.current_anchor(),
                    "sequence": stores.admission_causal_history.current_sequence(),
                },
                "compatibility": {
                    "anchor_sha256": stores.compatibility_history.current_anchor(),
                    "sequence": stores.compatibility_history.current_sequence(),
                },
                "boundary": {
                    "anchor_sha256": stores.knowledge_store.current_anchor(),
                    "sequence": stores.knowledge_store.current_sequence(),
                },
            },
        }

    return read


__all__ = [
    "AdmittedRunKnowledge",
    "AuthorityStores",
    "GateActorDeclaration",
    "GateProbeBindings",
    "admit_run_knowledge",
    "configure_run_gate_controller",
]
