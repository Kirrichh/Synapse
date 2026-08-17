"""A real committed §21 snapshot for suites whose subject is not §21.

``enumerate_retrieval_candidates`` now takes the candidate set from a committed
snapshot instead of from the live library index, and the only honest way for the
retrieval and compatibility suites to satisfy that is to commit a real boundary
and mint the frozen set through the adapter that re-verifies it. A hand-made
``FrozenCandidateSet`` would defeat exactly the barrier those suites now sit
behind — and there is no hand-made one to build, because the record is sealed and
its factory is private to ``gate_findings``.

So the snapshot here is genuine: a manifest whose ``behavior_refs`` are the §22
subject names of what the library actually held, a completeness decision from a
configured evaluator, an atomic boundary committed against a real admission
journal, and the whole thing re-opened from committed bytes before the frozen set
is minted. What the suites gain is that every enumeration in them is constrained
the way a production enumeration is.

The knowledge context here is *not* the compatibility context the retrieval
suites use. They are different §12 objects that happen to describe the same run:
``KnowledgeContext`` identifies the repository state a snapshot was taken of, and
``CompatibilityContext`` identifies the consumer a candidate is judged for.

This module is deliberately not named ``test_*``: it is a fixture, not a suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold import knowledge as K
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from synapse.experiments.gold.admission_store import FileAdmissionCausalStore
from synapse.experiments.gold.authority_config import (
    create_snapshot_actor_set,
    create_snapshot_evaluator_declaration,
    create_snapshot_independence_proof,
)
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    RunId,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.frozen_candidates import FrozenCandidateSet
from synapse.experiments.gold.knowledge_store import AuthoritativeKnowledgeStore
from synapse.experiments.gold.retrieval import index_entry_subject_ref
from tests.gold_store_fence import fence_for, quiet_fence

FREEZE_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
FREEZE_POLICY_VERSION = "policy-v1"
FREEZE_REVISION = "f" * 40


def knowledge_context() -> K.KnowledgeContext:
    """The §21 context every snapshot in these suites is taken under."""

    return K.create_knowledge_context(
        repository_revision=FREEZE_REVISION,
        policy_version=FREEZE_POLICY_VERSION,
        environment_profile_id="frozen-env",
    )


def _roots(
    admission_root_manifest: K.AdmissionRootManifestV2,
    compatibility_evidence_manifest: K.CompatibilityEvidenceManifestV2,
) -> K.SnapshotRootSet:
    return K.create_snapshot_root_set(
        library_root_sha256=hashlib.sha256(b"frozen-library").hexdigest(),
        library_generation=1,
        index_root_sha256=hashlib.sha256(b"frozen-index").hexdigest(),
        index_generation=1,
        lifecycle_root_sha256=hashlib.sha256(b"frozen-lifecycle").hexdigest(),
        lifecycle_record_count=1,
        admission_root_manifest=admission_root_manifest,
        compatibility_evidence_manifest=compatibility_evidence_manifest,
    )


def _authority_handle():
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="freeze-attester"),
        builder_actor=ActorIdentity(value="freeze-builder"),
        taint_classifier_authority=AuthorityIdentity(value="freeze-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="freeze-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="freeze-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="freeze-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="freeze-lifecycle-writer"),
        governing_human_authority=None,
    )
    return create_stage4_authority_handle(configuration)


def _evaluator(roots: K.SnapshotRootSet, *, fence, authority_handle) -> K.ConfiguredSnapshotEvaluator:
    actor_set = create_snapshot_actor_set(
        authority_handle=authority_handle,
        builder_actor=authority_handle.configuration.builder_actor,
        producer_actor=ActorIdentity(value="freeze-producer"),
        source_actor=ActorIdentity(value="freeze-source"),
        retriever_actor=ActorIdentity(value="freeze-retriever"),
        indexer_actor=ActorIdentity(value="freeze-indexer"),
        publisher_actor=ActorIdentity(value="freeze-publisher"),
        consumer_actor=ActorIdentity(value="freeze-consumer"),
        worker_actor=ActorIdentity(value="freeze-worker"),
        executor_actor=ActorIdentity(value="freeze-executor"),
    )
    declaration = create_snapshot_evaluator_declaration(
        authority_handle=authority_handle,
        evaluator_identity=AuthorityIdentity(value="freeze-evaluator"),
        evaluator_component_id="snapshot-evaluator",
        evaluator_component_version="1.0.0",
        policy_version=FREEZE_POLICY_VERSION,
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        trusted_clock=lambda: FREEZE_NOW,
    )
    proof = create_snapshot_independence_proof(declaration=declaration, actor_set=actor_set)
    return K.configure_snapshot_evaluator(
        authority_handle=authority_handle,
        declaration=declaration,
        actor_set=actor_set,
        independence_proof=proof,
        trusted_clock=lambda: FREEZE_NOW,
        observed_roots_provider=lambda: roots,
        root_fence=fence,
        ref_resolver=lambda item: True,
        consumability_probe=lambda item: True,
    )


def _already_committed(
    knowledge_store: AuthoritativeKnowledgeStore,
    *,
    store_root: Path,
    transaction_id: str,
    attempt_id: AttemptId,
) -> bool:
    """Whether this exact frozen set has been committed here before.

    An absent transaction directory is the only "not yet". Once the directory
    exists the snapshot has to open cleanly: corrupted bytes, a partial member
    set or a boundary that does not verify are real conditions of the store, and
    answering any of them by committing over the top would turn a detected fault
    into a silent repair.
    """

    if not (store_root / transaction_id).exists():
        return False
    knowledge_store.open_for_attempt(attempt_id)
    return True


def snapshot_over(
    entries,
    *,
    store_root: Path,
    transaction_id: str | None = None,
) -> tuple[FrozenCandidateSet, K.UsableKnowledgeSnapshot]:
    """Commit a snapshot naming exactly ``entries`` and mint its frozen set.

    ``entries`` are ``IndexEntry`` records, and their §22 subject names are
    computed the same way the enumeration computes them — by
    ``index_entry_subject_ref`` — so that a snapshot built here names objects in
    the vocabulary the filter matches on. Passing a subset is the whole point of
    the parameter: that is how a suite expresses "the library holds more than the
    snapshot froze".

    The default transaction id is derived from the frozen names, which makes two
    things true at once. A test that enumerates twice over the same library
    re-opens the snapshot it already committed instead of failing on a re-used
    transaction, and two different frozen sets can never land in the same
    transaction and quietly overwrite one another.
    """

    # §21's own canonical ref order, not an order of this fixture's choosing:
    # ``validate_snapshot_manifest`` sorts on kind, ref id and digest together,
    # and a manifest ordered any other way is refused.
    behavior_refs = tuple(
        sorted(
            (index_entry_subject_ref(item) for item in entries),
            key=lambda ref: f"{ref.kind.value}\x00{ref.ref_id}\x00{ref.sha256}",
        )
    )
    if transaction_id is None:
        digest = hashlib.sha256("".join(ref.sha256 for ref in behavior_refs).encode()).hexdigest()
        transaction_id = f"freeze-{digest[:16]}"
    # ``ensure_directory`` provisions one level only, so the store root has to
    # exist before either the journal or the boundary reaches for it.
    store_root.mkdir(parents=True, exist_ok=True)
    # Every preparatory write happens *before* the observation, and that ordering
    # is now load-bearing rather than incidental. The journal append below is a
    # real authority mutation on the shared coordinator; taking it after the
    # verdict would move the world between the verdict and the commit, and the
    # commit would refuse — correctly. A fixture that arranges its world first
    # and then observes it is what a producer actually does.
    fence = fence_for(store_root)
    journal = FileAdmissionJournal(
        store_root / "freeze-admission" / "decisions.journal", fence
    )
    anchor_bytes = b"a committed gate decision"
    if not journal.contains_record(hashlib.sha256(anchor_bytes).hexdigest()):
        journal.append_record(anchor_bytes)
    history = GF.SnapshotAdmissionHistory(journal)
    compatibility_history = FileCompatibilityStore(
        store_root / "freeze-compatibility",
        mutation_fence=fence,
    )
    admission_causal_history = FileAdmissionCausalStore(
        store_root / "freeze-admission-causal",
        mutation_fence=fence,
        admission_history=journal,
    )
    authority_handle = _authority_handle()
    run_id = RunId(value=f"freeze-run-{transaction_id}")
    attempt_id = AttemptId(value=f"freeze-attempt-{transaction_id}")
    context = knowledge_context()
    admission_root_manifest = K.create_admission_root_manifest(
        context=context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=authority_handle.configuration_id,
        admission_history=history,
        retrieval_causal_history=admission_causal_history,
        historical_admission_refs=(),
        historical_retrieval_decision_refs=(),
        created_at_utc=FREEZE_NOW,
        producer_component="frozen-candidate-fixture",
    )
    compatibility_evidence_manifest = K.create_compatibility_evidence_manifest(
        context=context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=authority_handle.configuration_id,
        compatibility_history=compatibility_history,
        compatibility_refs=(),
        created_at_utc=FREEZE_NOW,
        producer_component="frozen-candidate-fixture",
    )
    roots = _roots(admission_root_manifest, compatibility_evidence_manifest)
    knowledge_store = AuthoritativeKnowledgeStore(store_root, mutation_fence=fence)
    parent_snapshot = None if knowledge_store.current_boundary_id() is None else knowledge_store.open_current()
    manifest = K.create_snapshot_manifest(
        context=context,
        roots=roots,
        behavior_refs=behavior_refs,
        binding_refs=(),
        attestation_refs=(),
        admission_refs=(),
        retrieval_decision_refs=(),
        conflict_refs=(),
        created_at_utc=FREEZE_NOW,
        parent_snapshot_id=None if parent_snapshot is None else parent_snapshot.manifest.snapshot_id,
        run_id=run_id,
        attempt_id=attempt_id,
        producer_component="frozen-candidate-fixture",
    )

    # One root, one coordinator: the observation the verdict rests on and the
    # write that commits it must answer to the same counter, or the commit's
    # re-check is comparing against a world it never saw.
    evaluator = _evaluator(roots, fence=fence, authority_handle=authority_handle)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    decision = evaluated.decision

    if not _already_committed(
        knowledge_store,
        store_root=store_root,
        transaction_id=transaction_id,
        attempt_id=attempt_id,
    ):
        start_sequence = 0 if parent_snapshot is None else parent_snapshot.boundary.commit_sequence
        K.commit_atomic_snapshot_boundary(
            store_root,
            transaction_id=transaction_id,
            manifest=manifest,
            evaluation=evaluated,
            admission_root_manifest=admission_root_manifest,
            admission_journal=history,
            compatibility_evidence_manifest=compatibility_evidence_manifest,
            compatibility_history=compatibility_history,
            admission_causal_history=admission_causal_history,
            knowledge_store=knowledge_store,
            run_id=run_id,
            attempt_id=attempt_id,
            expected_parent_boundary_id=knowledge_store.current_boundary_id(),
            start_sequence=start_sequence,
            commit_sequence=start_sequence + 2,
            evaluator=evaluator,
        )
    # Re-opened from committed bytes rather than reused from the commit call:
    # a frozen set minted from an object that was never written and read back
    # would prove nothing about the store it claims to come from.
    snapshot = knowledge_store.open_for_attempt(attempt_id)
    if (
        snapshot.admission_root_manifest is None
        or snapshot.compatibility_evidence_manifest is None
        or snapshot.admission_root_manifest.admission_history_anchor != history.current_anchor()
        or snapshot.compatibility_evidence_manifest.compatibility_history_anchor
        != compatibility_history.current_anchor()
    ):
        raise AssertionError("the committed boundary no longer names durable history prefixes")
    # The mint opens the transaction again for itself; this one is opened only to
    # learn the boundary id the caller is binding the attempt to, and to hand the
    # snapshot back to tests that assert against its identities.
    frozen = GF.frozen_candidates_from_snapshot(
        knowledge_store=knowledge_store,
        attempt_id=attempt_id,
        expected_context=context,
        frozen_at_utc=FREEZE_NOW,
        evaluator_declaration=evaluator.declaration,
        evaluator_actor_set=evaluator.actor_set,
        evaluator_independence_proof=evaluator.independence_proof,
    )
    return frozen, snapshot


def frozen_for(
    library, *, store_root: Path, transaction_id: str | None = None
) -> FrozenCandidateSet:
    """Freeze everything the library currently offers.

    The common case for suites that are not about the snapshot at all: whatever
    the harness published is what the run is allowed to consider, so the
    pre-existing assertions go on measuring what they always measured while every
    enumeration crosses the §21 constraint.
    """

    frozen, _ = snapshot_over(
        library.search_index(), store_root=store_root, transaction_id=transaction_id
    )
    return frozen


def frozen_for_retriever(retriever) -> FrozenCandidateSet:
    """Freeze what a configured retriever's own library currently offers.

    The retriever holds the library it will enumerate, so taking the store root
    from it is what keeps the snapshot and the index in the same place even when
    a test builds two harnesses. Both attributes are private to their owners and
    read here for the same reason the suites already read ``retriever._library``:
    this is a fixture standing in for a caller that, in production, would be
    handed the snapshot rather than deriving one.
    """

    library = retriever._library
    return frozen_for(library, store_root=library._root.parent / "frozen-boundaries")
