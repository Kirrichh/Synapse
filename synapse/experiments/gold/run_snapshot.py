"""One run's knowledge snapshot manifest and its committed atomic boundary (§21).

This assembly had no production home. It existed only inside an acceptance
fixture, which is why a Gold run could be started from a test and not from the
platform: the records a run needs before it may consume anything — the
knowledge context, the admission and compatibility evidence manifests, the
snapshot root set, the manifest itself and the atomic boundary that commits
them together — were built by test code.

The ordering here is the contract. §21 makes the boundary the thing that turns
a manifest into a usable authority object: the roots are read from the stores
that will be consumed, the manifest names exactly those roots, completeness is
evaluated by an authority that is independent of the producer, and only then is
the boundary committed. A snapshot assembled in another order can be internally
consistent and still describe a world nobody is running.

What stays with the caller: which candidates exist, and the probes that decide
whether a referenced object resolves and is consumable. Those are readings of
a particular world, not the shape of a snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold import knowledge as K
from synapse.experiments.gold.canonicalization import HashBoundRef
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    RunId,
)

from .runner.vocabulary import GoldRunFailureCode, GoldRunViolation


#: The boundary commits the manifest and the two evidence manifests together,
#: so one boundary spans exactly this many sequence positions. The span is a
#: property of that transaction rather than of the caller: a caller free to
#: choose it could commit a boundary whose recorded span does not describe the
#: records it actually wrote.
_BOUNDARY_SPAN = 2

#: Where a run's first boundary starts. Every later boundary starts where its
#: parent committed, which is what makes the run's snapshots one chain instead
#: of a set of unrelated genesis commits.
_GENESIS_SEQUENCE = 0


def _fail(code: GoldRunFailureCode, detail: str) -> GoldRunViolation:
    return GoldRunViolation(code, detail)


@dataclass(frozen=True)
class SnapshotActorDeclaration:
    """The actors §21 requires to be distinct for one snapshot.

    Independence is the point: the evaluator that decides completeness must not
    be the producer that built the manifest, and the proof of that is checked
    rather than asserted. Declaring the names is how an operator makes the
    separation real for their deployment instead of inheriting one.
    """

    producer_actor: ActorIdentity
    source_actor: ActorIdentity
    retriever_actor: ActorIdentity
    indexer_actor: ActorIdentity
    publisher_actor: ActorIdentity
    consumer_actor: ActorIdentity
    worker_actor: ActorIdentity
    executor_actor: ActorIdentity
    evaluator_identity: AuthorityIdentity
    evaluator_component_id: str
    evaluator_component_version: str
    producer_component: str

    def __post_init__(self) -> None:
        for name in (
            "producer_actor",
            "source_actor",
            "retriever_actor",
            "indexer_actor",
            "publisher_actor",
            "consumer_actor",
            "worker_actor",
            "executor_actor",
        ):
            if type(getattr(self, name)) is not ActorIdentity:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be an exact actor identity")
        if type(self.evaluator_identity) is not AuthorityIdentity:
            raise _fail(
                GoldRunFailureCode.TYPE_MISMATCH,
                "evaluator_identity must be an exact authority identity",
            )
        for name in ("evaluator_component_id", "evaluator_component_version", "producer_component"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise _fail(GoldRunFailureCode.TYPE_MISMATCH, f"{name} must be a non-empty string")


@dataclass(frozen=True)
class SnapshotLineage:
    """Where this attempt's boundary attaches to the run's chain of snapshots.

    A run takes one snapshot per attempt, and they are a *chain*: attempt N's
    boundary names attempt N-1's as parent, and starts where that one committed.
    ``parent_snapshot`` is ``None`` for the first attempt only.

    The whole predecessor snapshot is carried, not only its boundary, because
    §21 records two different facts about a parent: the transaction this one
    extends, and the state it descends from. The first lives on the boundary and
    the second on the manifest, and a child that had only one of them would have
    to invent the other.

    Passing ``None`` for a later attempt is not a smaller claim, it is a
    different one -- it says this snapshot begins a run's history. Two genesis
    boundaries inside one run would each describe a world with no predecessor,
    and nothing downstream could tell which of them an attempt consumed from.
    """

    parent_snapshot: object | None = None


@dataclass(frozen=True)
class CommittedRunSnapshot:
    """One run's snapshot: the records, the authority that judged it, the boundary."""

    knowledge_context: object
    admission_root_manifest: object
    compatibility_evidence_manifest: object
    roots: object
    subjects: tuple[HashBoundRef, ...]
    manifest: object
    actor_set: object
    evaluator_declaration: object
    independence_proof: object
    evaluator: object
    evaluation: object
    boundary: object
    boundary_ref: HashBoundRef
    transaction_id: str
    snapshot_root: Path


def _lineage_position(lineage: SnapshotLineage) -> tuple[str | None, object | None, int]:
    """The parent this snapshot descends from, and the sequence it starts at.

    All three are read off the parent boundary rather than counted from the
    attempt index. An index says which attempt this is; only the parent record
    says what the run actually committed last, and a restart that recounted from
    the index would claim a position the history may not be at.

    The parent is named twice on purpose, and they are different facts: the
    *boundary* digest is the transaction this one extends, and the *snapshot*
    digest is the state it descends from. §21 checks both, because a child that
    declared one lineage while chaining onto another would otherwise pass every
    remaining check.
    """

    parent = lineage.parent_snapshot
    if parent is None:
        return None, None, _GENESIS_SEQUENCE
    boundary = parent.boundary
    K.validate_atomic_boundary(boundary)
    #: The identity travels as a record id rather than a bare digest: a
    #: ``RecordId`` is constructible only from the bytes that produced it, so a
    #: parent named this way cannot be a digest someone typed.
    return (
        boundary.atomic_boundary_id.digest_sha256,
        parent.manifest.snapshot_id,
        boundary.commit_sequence,
    )


def commit_run_snapshot(
    *,
    snapshot_root: Path,
    knowledge_store: object,
    library: object,
    lifecycle_store: object,
    admission_journal: object,
    admission_causal_history: object,
    compatibility_history: object,
    authority_handle: object,
    mutation_fence: object,
    descriptors: tuple[object, ...],
    run_id: RunId,
    attempt_id: AttemptId,
    repository_revision: str,
    policy_version: str,
    environment_profile_id: str,
    actors: SnapshotActorDeclaration,
    created_at_utc: datetime,
    trusted_clock: Callable[[], datetime],
    ref_resolver: Callable[[object], bool],
    consumability_probe: Callable[[object], bool],
    transaction_id: str,
    lineage: SnapshotLineage = SnapshotLineage(),
) -> CommittedRunSnapshot:
    """Build and atomically commit the snapshot one run will consume from.

    The candidate universe is required to be non-empty. §22's gates are defined
    over a subject set, so a snapshot over nothing is not a smaller snapshot —
    it is a run with no knowledge, which the caller decides about before
    committing a boundary that would describe an empty world.
    """

    if type(actors) is not SnapshotActorDeclaration:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "snapshot actors must be exact")
    if type(lineage) is not SnapshotLineage:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "snapshot lineage must be exact")
    expected_parent_boundary_id, parent_snapshot_id, start_sequence = _lineage_position(
        lineage
    )
    if type(descriptors) is not tuple or not descriptors:
        raise _fail(
            GoldRunFailureCode.TYPE_MISMATCH,
            "a committed snapshot needs a non-empty candidate universe",
        )
    if type(run_id) is not RunId or type(attempt_id) is not AttemptId:
        raise _fail(GoldRunFailureCode.TYPE_MISMATCH, "run and attempt identity must be exact")

    knowledge_context = K.create_knowledge_context(
        repository_revision=repository_revision,
        policy_version=policy_version,
        environment_profile_id=environment_profile_id,
    )
    admission_root_manifest = K.create_admission_root_manifest(
        context=knowledge_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=authority_handle.configuration_id,
        admission_history=admission_journal,
        retrieval_causal_history=admission_causal_history,
        historical_admission_refs=(),
        historical_retrieval_decision_refs=(),
        created_at_utc=created_at_utc,
        producer_component=actors.producer_component,
    )
    compatibility_evidence_manifest = K.create_compatibility_evidence_manifest(
        context=knowledge_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=authority_handle.configuration_id,
        compatibility_history=compatibility_history,
        compatibility_refs=(),
        created_at_utc=created_at_utc,
        producer_component=actors.producer_component,
    )
    library_snapshot = library.current_snapshot().snapshot
    lifecycle_anchor = lifecycle_store.current_anchor()
    roots = K.create_snapshot_root_set(
        library_root_sha256=library_snapshot.integrity_manifest_sha256,
        library_generation=library_snapshot.generation,
        index_root_sha256=library_snapshot.index_sha256,
        index_generation=library_snapshot.generation,
        lifecycle_root_sha256=lifecycle_anchor.ordered_log_root_sha256,
        lifecycle_record_count=lifecycle_anchor.entry_count,
        admission_root_manifest=admission_root_manifest,
        compatibility_evidence_manifest=compatibility_evidence_manifest,
    )
    #: Canonically ordered, because that is the one representation §22 decides
    #: about. A permuted set is a different set to every gate entry point.
    subjects = A.canonical_subject_refs(
        tuple(GF.candidate_subject_ref(descriptor) for descriptor in descriptors)
    )
    manifest = K.create_snapshot_manifest(
        context=knowledge_context,
        roots=roots,
        behavior_refs=subjects,
        binding_refs=(),
        attestation_refs=(),
        admission_refs=(),
        retrieval_decision_refs=(),
        conflict_refs=(),
        created_at_utc=created_at_utc,
        run_id=run_id,
        attempt_id=attempt_id,
        producer_component=actors.producer_component,
        parent_snapshot_id=parent_snapshot_id,
    )
    actor_set = AC.create_snapshot_actor_set(
        authority_handle=authority_handle,
        builder_actor=authority_handle.configuration.builder_actor,
        producer_actor=actors.producer_actor,
        source_actor=actors.source_actor,
        retriever_actor=actors.retriever_actor,
        indexer_actor=actors.indexer_actor,
        publisher_actor=actors.publisher_actor,
        consumer_actor=actors.consumer_actor,
        worker_actor=actors.worker_actor,
        executor_actor=actors.executor_actor,
    )
    evaluator_declaration = AC.create_snapshot_evaluator_declaration(
        authority_handle=authority_handle,
        evaluator_identity=actors.evaluator_identity,
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        evaluator_component_id=actors.evaluator_component_id,
        evaluator_component_version=actors.evaluator_component_version,
        policy_version=policy_version,
        trusted_clock=trusted_clock,
    )
    independence_proof = AC.create_snapshot_independence_proof(
        declaration=evaluator_declaration,
        actor_set=actor_set,
    )
    evaluator = K.configure_snapshot_evaluator(
        authority_handle=authority_handle,
        declaration=evaluator_declaration,
        actor_set=actor_set,
        independence_proof=independence_proof,
        trusted_clock=trusted_clock,
        observed_roots_provider=lambda: roots,
        root_fence=mutation_fence,
        ref_resolver=ref_resolver,
        consumability_probe=consumability_probe,
    )
    evaluation = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    boundary = K.commit_atomic_snapshot_boundary(
        snapshot_root,
        transaction_id=transaction_id,
        manifest=manifest,
        evaluation=evaluation,
        admission_root_manifest=admission_root_manifest,
        admission_journal=admission_journal,
        compatibility_evidence_manifest=compatibility_evidence_manifest,
        compatibility_history=compatibility_history,
        admission_causal_history=admission_causal_history,
        knowledge_store=knowledge_store,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_parent_boundary_id=expected_parent_boundary_id,
        start_sequence=start_sequence,
        commit_sequence=start_sequence + _BOUNDARY_SPAN,
        evaluator=evaluator,
    )
    return CommittedRunSnapshot(
        knowledge_context=knowledge_context,
        admission_root_manifest=admission_root_manifest,
        compatibility_evidence_manifest=compatibility_evidence_manifest,
        roots=roots,
        subjects=subjects,
        manifest=manifest,
        actor_set=actor_set,
        evaluator_declaration=evaluator_declaration,
        independence_proof=independence_proof,
        evaluator=evaluator,
        evaluation=evaluation,
        boundary=boundary,
        boundary_ref=K.atomic_boundary_ref(boundary),
        transaction_id=transaction_id,
        snapshot_root=snapshot_root,
    )


__all__ = [
    "SnapshotLineage",
    "CommittedRunSnapshot",
    "SnapshotActorDeclaration",
    "commit_run_snapshot",
]
