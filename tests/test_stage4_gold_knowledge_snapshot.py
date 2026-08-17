"""Stage 4 Patch 7 — RepositoryKnowledgeSnapshot and AtomicSnapshotBoundary.

Covers the §21 acceptance checks: atomic construction, missing store/ref,
rollback and mix-and-match, revoked-object exclusion, and restart/recovery of an
exact snapshot identity. The mandatory mutants for this stage each have a named
killing test at the end of the file.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from synapse.experiments.gold import knowledge as K
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold.admission_journal import (
    FileAdmissionJournal,
    FileSnapshotFence,
    JournalAdapterFailureCode,
    JournalAdapterViolation,
)
from synapse.experiments.gold.knowledge_store import (
    AuthoritativeKnowledgeStore,
    KnowledgeStoreFailureCode,
    KnowledgeStoreViolation,
)
from synapse.experiments.gold.persistence import store_transaction

from tests.gold_store_fence import fence_for, quiet_fence


def _mutate(fence) -> None:
    """One complete authority transaction on a real coordinator."""

    with store_transaction(fence):
        pass


def test_root_observation_fence_requires_exclusive_at_configuration() -> None:
    class IncompleteFence:
        def coordinator_id(self):
            return "a" * 32

        def current_epoch(self):
            return 0

        def mutating(self):
            raise AssertionError("not reached")

    with pytest.raises(K.KnowledgeViolation) as caught:
        K.require_root_observation_fence(IncompleteFence())
    assert caught.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH

from synapse.experiments.gold.canonicalization import (
    STABLE_CANONICAL_CODEC_ID,
    STAGE4_CANONICAL_PROFILE_V1,
    HashBoundRef,
    RefKind,
    decode_stage4_canonical_bytes,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    ContractViolation,
    AuthorityIdentity,
    AuthorityRole,
    IdentityDomain,
    RunId,
    SnapshotCompletenessStatus,
    require_snapshot_status_admits_execution,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.persistence import (
    PersistenceFailureCode,
    PersistenceViolation,
    ensure_directory,
    committed_transaction_exists,
    stage_snapshot_transaction,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "gold" / "snapshot_manifests_v1"

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
_ROOT_FIXTURES: dict[int, SimpleNamespace] = {}
_KNOWLEDGE_STORES: dict[str, AuthoritativeKnowledgeStore] = {}
_KNOWLEDGE_FENCE = quiet_fence()
ADMISSION_ROOT = hashlib.sha256(b"admission-root").hexdigest()
COMPAT_ROOT = hashlib.sha256(b"compatibility-root").hexdigest()


def ref(kind: RefKind, name: str, payload: bytes = b"payload") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


@pytest.fixture
def context() -> K.KnowledgeContext:
    return K.create_knowledge_context(
        repository_revision="a" * 40,
        policy_version="policy-v1",
        environment_profile_id="env-1",
    )


def make_roots(
    *, library: int = 7, index: int = 7, lifecycle: int = 42,
    library_root: bytes = b"lib", index_root: bytes = b"idx", lifecycle_root: bytes = b"lc",
    admission_refs=None,
    retrieval_refs=None,
    compatibility_refs=(),
    run_id: RunId | None = None,
    attempt_id: AttemptId | None = None,
    fence=None,
) -> K.SnapshotRootSet:
    fence = fence or _KNOWLEDGE_FENCE
    if admission_refs is None:
        admission_refs = (ref(RefKind.GATE_DECISION, "gate-1"),)
    if retrieval_refs is None:
        retrieval_refs = (ref(RefKind.ARTIFACT, "retrieval-1"),)
    admission_history = _FixedHistory(
        anchor=hashlib.sha256(b"admission").hexdigest(),
        refs=admission_refs,
        fence=fence,
    )
    causal_history = _FixedHistory(
        anchor=hashlib.sha256(b"causal").hexdigest(),
        refs=retrieval_refs,
        fence=fence,
    )
    compatibility_history = _FixedHistory(
        anchor=hashlib.sha256(b"compatibility").hexdigest(),
        refs=compatibility_refs,
        fence=fence,
    )
    handle = authority_handle()
    root_context = K.create_knowledge_context(
        repository_revision="a" * 40,
        policy_version="policy-v1",
        environment_profile_id="env-1",
    )
    identity_seed = hashlib.sha256(
        json.dumps(
            {
                "library": [library, library_root.hex()],
                "index": [index, index_root.hex()],
                "lifecycle": [lifecycle, lifecycle_root.hex()],
                "admission": [item.to_dict() for item in admission_refs],
                "retrieval": [item.to_dict() for item in retrieval_refs],
                "compatibility": [item.to_dict() for item in compatibility_refs],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    run_id = run_id or RunId(value=f"knowledge-run-{identity_seed}")
    attempt_id = attempt_id or AttemptId(value=f"knowledge-attempt-{identity_seed}")
    admission_manifest = K.create_admission_root_manifest(
        context=root_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=handle.configuration_id,
        admission_history=admission_history,
        retrieval_causal_history=causal_history,
        historical_admission_refs=admission_refs,
        historical_retrieval_decision_refs=retrieval_refs,
        created_at_utc=NOW,
        producer_component="knowledge-test",
    )
    compatibility_manifest = K.create_compatibility_evidence_manifest(
        context=root_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=handle.configuration_id,
        compatibility_history=compatibility_history,
        compatibility_refs=compatibility_refs,
        created_at_utc=NOW,
        producer_component="knowledge-test",
    )
    result = K.create_snapshot_root_set(
        library_root_sha256=hashlib.sha256(library_root).hexdigest(),
        library_generation=library,
        index_root_sha256=hashlib.sha256(index_root).hexdigest(),
        index_generation=index,
        lifecycle_root_sha256=hashlib.sha256(lifecycle_root).hexdigest(),
        lifecycle_record_count=lifecycle,
        admission_root_manifest=admission_manifest,
        compatibility_evidence_manifest=compatibility_manifest,
    )
    _ROOT_FIXTURES[id(result)] = SimpleNamespace(
        fence=fence,
        authority_handle=handle,
        run_id=run_id,
        attempt_id=attempt_id,
        admission_root_manifest=admission_manifest,
        compatibility_evidence_manifest=compatibility_manifest,
        admission_history=admission_history,
        causal_history=causal_history,
        compatibility_history=compatibility_history,
    )
    return result


def default_roots() -> K.SnapshotRootSet:
    return make_roots()


def _create_manifest(**kwargs) -> K.SnapshotManifest:
    roots = kwargs["roots"]
    fixture = _ROOT_FIXTURES[id(roots)]
    kwargs.setdefault("run_id", fixture.run_id)
    kwargs.setdefault("attempt_id", fixture.attempt_id)
    kwargs.setdefault("producer_component", "knowledge-test")
    return K.create_snapshot_manifest(**kwargs)


def make_manifest(
    context: K.KnowledgeContext,
    *, roots: K.SnapshotRootSet | None = None, parent=None,
) -> K.SnapshotManifest:
    selected_roots = roots or default_roots()
    fixture = _ROOT_FIXTURES[id(selected_roots)]
    return _create_manifest(
        context=context,
        roots=selected_roots,
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(ref(RefKind.ARTIFACT, "retrieval-1"),),
        conflict_refs=(),
        created_at_utc=NOW,
        parent_snapshot_id=parent,
    )


def authority_handle():
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="attester"),
        builder_actor=ActorIdentity(value="builder"),
        taint_classifier_authority=AuthorityIdentity(value="taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity(value="revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity(value="lifecycle-writer"),
        governing_human_authority=None,
    )
    return create_stage4_authority_handle(configuration)


def make_evaluator(
    *, observed=None, resolver=None, probe=None, producer: str = "snapshot-producer",
    authority: str = "platform-evaluator", root_fence=None,
) -> K.ConfiguredSnapshotEvaluator:
    handle = authority_handle()
    actor_set = AC.create_snapshot_actor_set(
        authority_handle=handle,
        builder_actor=handle.configuration.builder_actor,
        producer_actor=ActorIdentity(value=producer),
        source_actor=ActorIdentity(value="snapshot-source"),
        retriever_actor=ActorIdentity(value="snapshot-retriever"),
        indexer_actor=ActorIdentity(value="snapshot-indexer"),
        publisher_actor=ActorIdentity(value="snapshot-publisher"),
        consumer_actor=ActorIdentity(value="snapshot-consumer"),
        worker_actor=ActorIdentity(value="snapshot-worker"),
        executor_actor=ActorIdentity(value="snapshot-executor"),
    )
    declaration = AC.create_snapshot_evaluator_declaration(
        authority_handle=handle,
        evaluator_identity=AuthorityIdentity(value=authority),
        evaluator_component_id="snapshot-evaluator",
        evaluator_component_version="1.0.0",
        policy_version="policy-v1",
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        trusted_clock=lambda: NOW,
    )
    proof = AC.create_snapshot_independence_proof(declaration=declaration, actor_set=actor_set)
    return K.configure_snapshot_evaluator(
        authority_handle=handle,
        declaration=declaration,
        actor_set=actor_set,
        independence_proof=proof,
        trusted_clock=lambda: NOW,
        observed_roots_provider=observed or (lambda: default_roots()),
        root_fence=root_fence or quiet_fence(),
        ref_resolver=resolver or (lambda item: True),
        consumability_probe=probe or (lambda item: True),
    )


def complete_decision(manifest: K.SnapshotManifest, **kwargs) -> K.SnapshotCompletenessDecision:
    evaluator = make_evaluator(observed=lambda: manifest.roots, **kwargs)
    return K.evaluate_snapshot_completeness(evaluator, manifest=manifest).decision


def evaluation_for(manifest: K.SnapshotManifest, root: Path, **kwargs):
    """An evaluation taken under the boundary store's *own* coordinator.

    The evaluator used to be built with a fence of its own while the commit was
    handed ``fence_for(root)``, so the observation and the write answered to two
    unrelated counters. That worked only because nothing compared them. Now the
    commit re-checks the token's coordinator, and one root has one coordinator.
    """

    evaluator = make_evaluator(
        observed=lambda: manifest.roots,
        root_fence=_ROOT_FIXTURES[id(manifest.roots)].fence,
        **kwargs,
    )
    return evaluator, K.evaluate_snapshot_completeness(evaluator, manifest=manifest)


def admission_journal(root: Path) -> FileAdmissionJournal:
    """A real file-backed decision journal beside the boundary store.

    The boundary now records an admission root the journal has to confirm, so
    the suite has to have one. Round 12's ``FileAdmissionJournal`` satisfies the
    structural port by shape, which is the whole point of declaring the port
    structurally: neither §21 nor §22 names the other.
    """

    journal = FileAdmissionJournal(
        root.parent / "admission" / "decisions.journal", fence_for(root.parent)
    )
    if not journal.contains_record(hashlib.sha256(b"a committed gate decision").hexdigest()):
        journal.append_record(b"a committed gate decision")
    if not journal.contains_record(hashlib.sha256(b"payload").hexdigest()):
        journal.append_record(b"payload")
    return journal


class _FixedHistory:
    """A structural durable-history view seeded with the manifest's historical refs."""

    def __init__(self, *, anchor: str, refs=(), fence=None, sequence: int | None = None) -> None:
        self.anchor = anchor
        self.refs = {json.dumps(item.to_dict(), sort_keys=True) for item in refs}
        self.mutation_fence = fence or quiet_fence()
        self.sequence = len(self.refs) if sequence is None else sequence
        self._prefix_refs = {(self.anchor, self.sequence): set(self.refs)}
        self._known_anchors = {self.anchor}

    def current_anchor(self):
        return self.anchor

    def current_sequence(self):
        return self.sequence

    def extends(self, anchor):
        return anchor in self._known_anchors

    def contains_ref(self, item):
        return json.dumps(item.to_dict(), sort_keys=True) in self.refs

    def contains_record(self, digest):
        return any(json.loads(item).get("sha256") == digest for item in self.refs)

    def contains_ref_at(self, anchor, sequence, item):
        return json.dumps(item.to_dict(), sort_keys=True) in self._prefix_refs.get((anchor, sequence), set())

    def contains_record_at(self, anchor, sequence, digest):
        return any(
            json.loads(item).get("sha256") == digest
            for item in self._prefix_refs.get((anchor, sequence), set())
        )

    def prefix_extends(self, child_anchor, child_sequence, parent_anchor, parent_sequence):
        return child_anchor in self._known_anchors and parent_anchor in self._known_anchors and child_sequence >= parent_sequence

    def append_record(self, payload):
        prior = bytes.fromhex(self.anchor)
        self.anchor = hashlib.sha256(prior + hashlib.sha256(payload).digest()).hexdigest()
        self.sequence += 1
        self._known_anchors.add(self.anchor)
        self._prefix_refs[(self.anchor, self.sequence)] = set(self.refs)


#: ``None`` is a value a caller may legitimately pass as the journal, so the
#: helper cannot use it to mean "build me a real one".
_DEFAULT_JOURNAL = object()


def commit(
    root: Path, manifest, decision, *, transaction_id="tx-1", start=1, commit_sequence=2,
    parent=None, journal=_DEFAULT_JOURNAL, admission_root=None, evaluation=None,
    compatibility_root=COMPAT_ROOT, compatibility_history=None, causal_history=None,
):
    """Commit a boundary, taking a fresh observation unless one is supplied.

    ``decision`` stays a parameter because most cases here vary it — a swapped
    verdict, a foreign one, an incomplete one. ``evaluation`` is what carries the
    *observation*, and by default it is taken here, under the boundary store's
    own coordinator, immediately before the commit. A case that needs a stale,
    foreign or replayed token passes its own.
    """

    fixture = _ROOT_FIXTURES[id(manifest.roots)]
    store = fixture.admission_history if journal is _DEFAULT_JOURNAL else journal
    evaluator, evaluated = evaluation if evaluation is not None else evaluation_for(manifest, root)
    if decision is not evaluated.decision:
        # The verdict and the observation are one sealed input now, so a case that
        # varies the decision has to re-seal the pair. `_repair_pair` is the
        # harness saying "these two really do belong together"; a case proving
        # they must *not* be mixed passes its own evaluation instead.
        evaluated = _reseal(evaluator, decision, evaluated)
    knowledge_store = AuthoritativeKnowledgeStore(root, mutation_fence=fixture.fence)
    _KNOWLEDGE_STORES[str(root)] = knowledge_store
    expected_parent = None if parent is None else parent.atomic_boundary_id.digest_sha256
    assert manifest.envelope is not None
    return K.commit_atomic_snapshot_boundary(
        root,
        transaction_id=transaction_id,
        manifest=manifest,
        evaluation=evaluated,
        admission_root_manifest=fixture.admission_root_manifest,
        admission_journal=store,
        compatibility_evidence_manifest=fixture.compatibility_evidence_manifest,
        compatibility_history=compatibility_history or fixture.compatibility_history,
        admission_causal_history=causal_history or fixture.causal_history,
        knowledge_store=knowledge_store,
        run_id=manifest.envelope.run_id,
        attempt_id=manifest.envelope.attempt_id,
        expected_parent_boundary_id=expected_parent,
        start_sequence=start,
        commit_sequence=commit_sequence,
        # The coordinator comes from the evaluator, so the fence that bracketed
        # the observation and the fence that guards the write cannot be two
        # different objects — which is what they used to be here.
        evaluator=evaluator,
    )


def _reseal(evaluator, decision, evaluated):
    """Re-seal an evaluation around a decision the case supplied.

    Only for cases whose subject is something *other* than the pairing — a
    lineage rule, a sequence rule, an admission root. Those pass a hand-built
    decision and still need a well-formed evaluation to carry it, and the sealed
    pair is the only shape the commit accepts. Cases about the pairing itself
    build two real evaluations and mix them, which is what must be refused.
    """

    token = object.__new__(K.RootObservationToken)
    for field in ("coordinator_id", "epoch", "roots_digest", "evaluator_identity"):
        object.__setattr__(token, field, getattr(evaluated.token, field))
    # Taken from the decision, not from the old token: the re-sealed pair has to
    # be self-consistent, or the seal refuses it here and the case never reaches
    # the rule it is actually about.
    object.__setattr__(token, "context", decision.context)
    object.__setattr__(token, "snapshot_id", decision.snapshot_id)
    object.__setattr__(token, "decision_digest", K._decision_digest(decision))
    object.__setattr__(token, "_trusted_seal", K._ROOT_TOKEN_SEAL)
    return K._evaluation(decision, token)


# ---------------------------------------------------------------------------
# Manifest identity and immutability
# ---------------------------------------------------------------------------


def test_manifest_identity_is_deterministic_over_selection(context) -> None:
    first = make_manifest(context)
    second = make_manifest(context)
    assert first.snapshot_id.value == second.snapshot_id.value
    assert first.snapshot_id.domain is IdentityDomain.KNOWLEDGE_SNAPSHOT_V2


def test_manifest_identity_changes_with_any_selected_object(context) -> None:
    base = make_manifest(context)
    other = _create_manifest(
        context=context,
        roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-2"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(ref(RefKind.ARTIFACT, "retrieval-1"),),
        conflict_refs=(),
        created_at_utc=NOW,
    )
    assert base.snapshot_id.value != other.snapshot_id.value


def test_manifest_payload_carries_no_self_asserted_status(context) -> None:
    payload = decode_stage4_canonical_bytes(
        make_manifest(context).canonical_bytes(),
        profile_id=STAGE4_CANONICAL_PROFILE_V1,
        codec_id=STABLE_CANONICAL_CODEC_ID,
    )
    assert "completeness_status" not in payload
    assert "snapshot_id" not in payload


def test_manifest_is_frozen_after_construction(context) -> None:
    manifest = make_manifest(context)
    with pytest.raises((AttributeError, TypeError)):
        manifest.behavior_refs = ()  # type: ignore[misc]


def test_manifest_rejects_direct_construction(context) -> None:
    with pytest.raises(TypeError):
        K.SnapshotManifest()  # type: ignore[call-arg]


def test_manifest_rejects_unordered_and_duplicate_refs(context) -> None:
    duplicate = (ref(RefKind.BINDING, "binding-1"), ref(RefKind.BINDING, "binding-1"))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _create_manifest(
            context=context, roots=make_roots(),
            behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
            binding_refs=duplicate, attestation_refs=(), admission_refs=(),
            retrieval_decision_refs=(), conflict_refs=(), created_at_utc=NOW,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DUPLICATE_REFERENCE


def test_manifest_requires_at_least_one_selected_object(context) -> None:
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _create_manifest(
            context=context, roots=make_roots(), behavior_refs=(), binding_refs=(),
            attestation_refs=(), admission_refs=(), retrieval_decision_refs=(),
            conflict_refs=(), created_at_utc=NOW,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


# ---------------------------------------------------------------------------
# Completeness evaluation
# ---------------------------------------------------------------------------


def test_complete_status_requires_every_check_to_pass(context) -> None:
    decision = complete_decision(make_manifest(context))
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_evaluator_cannot_be_the_snapshot_producer() -> None:
    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        make_evaluator(producer="same-actor", authority="same-actor")
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT


@pytest.mark.parametrize(
    "colliding_field",
    (
        "builder_actor",
        "producer_actor",
        "source_actor",
        "retriever_actor",
        "indexer_actor",
        "publisher_actor",
        "consumer_actor",
        "worker_actor",
        "executor_actor",
    ),
)
def test_snapshot_evaluator_cannot_collide_with_any_complete_actor_slot(
    colliding_field: str,
) -> None:
    handle = authority_handle()
    evaluator_value = (
        handle.configuration.builder_actor.value
        if colliding_field == "builder_actor"
        else "colliding-snapshot-evaluator"
    )
    if colliding_field == "builder_actor":
        with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
            AC.create_snapshot_evaluator_declaration(
                authority_handle=handle,
                evaluator_identity=AuthorityIdentity(value=evaluator_value),
                evaluator_component_id="snapshot-evaluator",
                evaluator_component_version="1.0.0",
                policy_version="policy-v1",
                authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
                trusted_clock=lambda: NOW,
            )
        assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT
        return

    actors = {
        "builder_actor": handle.configuration.builder_actor,
        "producer_actor": ActorIdentity(value="snapshot-producer"),
        "source_actor": ActorIdentity(value="snapshot-source"),
        "retriever_actor": ActorIdentity(value="snapshot-retriever"),
        "indexer_actor": ActorIdentity(value="snapshot-indexer"),
        "publisher_actor": ActorIdentity(value="snapshot-publisher"),
        "consumer_actor": ActorIdentity(value="snapshot-consumer"),
        "worker_actor": ActorIdentity(value="snapshot-worker"),
        "executor_actor": ActorIdentity(value="snapshot-executor"),
    }
    actors[colliding_field] = ActorIdentity(value=evaluator_value)
    actor_set = AC.create_snapshot_actor_set(authority_handle=handle, **actors)
    declaration = AC.create_snapshot_evaluator_declaration(
        authority_handle=handle,
        evaluator_identity=AuthorityIdentity(value=evaluator_value),
        evaluator_component_id="snapshot-evaluator",
        evaluator_component_version="1.0.0",
        policy_version="policy-v1",
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        trusted_clock=lambda: NOW,
    )
    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        AC.create_snapshot_independence_proof(
            declaration=declaration,
            actor_set=actor_set,
        )
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_snapshot_actor_set_cannot_omit_an_execution_actor() -> None:
    handle = authority_handle()
    with pytest.raises(TypeError):
        AC.create_snapshot_actor_set(
            authority_handle=handle,
            builder_actor=handle.configuration.builder_actor,
            producer_actor=ActorIdentity(value="snapshot-producer"),
            source_actor=ActorIdentity(value="snapshot-source"),
            retriever_actor=ActorIdentity(value="snapshot-retriever"),
            indexer_actor=ActorIdentity(value="snapshot-indexer"),
            publisher_actor=ActorIdentity(value="snapshot-publisher"),
            consumer_actor=ActorIdentity(value="snapshot-consumer"),
            worker_actor=ActorIdentity(value="snapshot-worker"),
        )


def test_snapshot_entitlement_rejects_a_declaration_from_another_configuration() -> None:
    declaration_handle = authority_handle()
    other_configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="attester"),
        builder_actor=ActorIdentity(value="other-builder"),
        taint_classifier_authority=AuthorityIdentity(value="taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="supersession-reviewer"),
        revocation_reviewer_authority=AuthorityIdentity(value="revocation-reviewer"),
        lifecycle_writer_actor=ActorIdentity(value="lifecycle-writer"),
        governing_human_authority=None,
    )
    actor_handle = create_stage4_authority_handle(other_configuration)
    declaration = AC.create_snapshot_evaluator_declaration(
        authority_handle=declaration_handle,
        evaluator_identity=AuthorityIdentity(value="platform-evaluator"),
        evaluator_component_id="snapshot-evaluator",
        evaluator_component_version="1.0.0",
        policy_version="policy-v1",
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        trusted_clock=lambda: NOW,
    )
    actor_set = AC.create_snapshot_actor_set(
        authority_handle=actor_handle,
        builder_actor=actor_handle.configuration.builder_actor,
        producer_actor=ActorIdentity(value="snapshot-producer"),
        source_actor=ActorIdentity(value="snapshot-source"),
        retriever_actor=ActorIdentity(value="snapshot-retriever"),
        indexer_actor=ActorIdentity(value="snapshot-indexer"),
        publisher_actor=ActorIdentity(value="snapshot-publisher"),
        consumer_actor=ActorIdentity(value="snapshot-consumer"),
        worker_actor=ActorIdentity(value="snapshot-worker"),
        executor_actor=ActorIdentity(value="snapshot-executor"),
    )
    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        AC.create_snapshot_independence_proof(
            declaration=declaration,
            actor_set=actor_set,
        )
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.CONFIGURATION_MISMATCH


def test_a_forged_snapshot_independence_proof_is_refused() -> None:
    evaluator = make_evaluator()
    object.__setattr__(
        evaluator.independence_proof,
        "independence_reason",
        "SELF_ASSERTED",
    )
    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        AC.require_snapshot_evaluator_entitlement(
            evaluator.independence_proof,
            declaration=evaluator.declaration,
            actor_set=evaluator.actor_set,
        )
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_unavailable_store_is_not_an_optimistic_pass(context) -> None:
    def unavailable():
        raise OSError("store is down")

    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=unavailable), manifest=make_manifest(context)
    ).decision
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE


@pytest.mark.parametrize(
    "blocked_kind,expected",
    [
        (RefKind.BINDING, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_BINDING),
        (RefKind.SOURCE_EVIDENCE, SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_ATTESTATION),
        (RefKind.GATE_DECISION, SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA),
    ],
)
def test_unresolved_reference_maps_to_its_exact_status(context, blocked_kind, expected) -> None:
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(resolver=lambda item: item.kind is not blocked_kind),
        manifest=make_manifest(context),
    ).decision
    assert decision.status is expected


def test_unconsumable_object_blocks_completeness(context) -> None:
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(probe=lambda item: False), manifest=make_manifest(context)
    ).decision
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_LIFECYCLE_STATE


def test_manifest_roots_must_match_observed_store_roots(context) -> None:
    manifest = make_manifest(context, roots=make_roots(library=7, index=7, lifecycle=42))
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: make_roots(library=7, index=7, lifecycle=43)),
        manifest=manifest,
    ).decision
    assert decision.status is SnapshotCompletenessStatus.ROLLBACK_DETECTED


def test_decision_identity_binds_its_exact_payload(context) -> None:
    decision = complete_decision(make_manifest(context))
    K.validate_completeness_decision(decision)
    assert decision.decision_id.domain is IdentityDomain.SNAPSHOT_COMPLETENESS_DECISION_V2


def test_decision_rejects_direct_construction() -> None:
    with pytest.raises(TypeError):
        K.SnapshotCompletenessDecision()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Rollback and mix-and-match detection
# ---------------------------------------------------------------------------


def test_root_regression_is_detected_per_root() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    assert K.detect_root_regression(make_roots(library=6, index=7, lifecycle=42), prior=prior) == "library"
    assert K.detect_root_regression(make_roots(library=7, index=6, lifecycle=42), prior=prior) == "index"
    assert K.detect_root_regression(make_roots(library=7, index=7, lifecycle=41), prior=prior) == "lifecycle"
    assert K.detect_root_regression(make_roots(library=8, index=8, lifecycle=43), prior=prior) is None


def test_same_generation_with_different_root_is_a_fork() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    forked = make_roots(library=7, index=7, lifecycle=42, library_root=b"other-lib")
    assert K.detect_root_regression(forked, prior=prior) == "library"


def test_new_index_with_old_lifecycle_is_mix_and_match() -> None:
    prior = make_roots(library=7, index=7, lifecycle=42)
    mixed = make_roots(library=7, index=9, lifecycle=42)
    assert K.detect_mixed_generation(mixed, prior=prior) is not None
    assert K.detect_mixed_generation(make_roots(library=8, index=8, lifecycle=43), prior=prior) is None


def test_mixed_generation_is_reported_by_the_evaluator(context) -> None:
    mixed = make_roots(library=7, index=9, lifecycle=42)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: mixed),
        manifest=make_manifest(context, roots=mixed),
        prior_roots=make_roots(library=7, index=8, lifecycle=42),
    ).decision
    assert decision.status is SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED


def test_component_from_another_context_is_mix_and_match(context) -> None:
    other = K.create_knowledge_context(
        repository_revision="b" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_same_context(other, context, subject="component")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


# ---------------------------------------------------------------------------
# Atomic commit and visibility
# ---------------------------------------------------------------------------


def test_commit_requires_a_complete_decision(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(probe=lambda item: False), manifest=manifest
    ).decision
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMPLETENESS_NOT_ADMITTED


def test_commit_rejects_a_decision_about_another_manifest(context, tmp_path) -> None:
    manifest = make_manifest(context)
    other = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    decision = complete_decision(other)
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_committed_snapshot_restores_with_an_identical_identity(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    boundary = commit(root, manifest, decision)

    restored = K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert restored.snapshot_id.value == manifest.snapshot_id.value
    assert restored.atomic_boundary_id.value == boundary.atomic_boundary_id.value
    assert restored.completeness_status is SnapshotCompletenessStatus.COMPLETE


def test_snapshot_is_invisible_before_the_terminal_commit_marker(context, tmp_path) -> None:
    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    with store_transaction(_KNOWLEDGE_FENCE) as ticket:
        stage_snapshot_transaction(
            root,
            transaction_id="tx-staged",
            members={K.MANIFEST_MEMBER_NAME: manifest.canonical_bytes()},
            ticket=ticket,
        )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-staged")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMIT_MARKER_ABSENT
    authority = AuthoritativeKnowledgeStore(root, mutation_fence=_KNOWLEDGE_FENCE)
    assert authority.current_boundary_id() is None
    assert authority.attempt_boundary_id(_ROOT_FIXTURES[id(manifest.roots)].attempt_id) is None


def test_transaction_id_is_consumed_exactly_once(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    commit(root, manifest, decision)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TRANSACTION_ID_REUSED


def test_a_second_genesis_is_refused_by_the_authoritative_store(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    store = _KNOWLEDGE_STORES[str(root)]
    frame = store._frames()[0]
    assert manifest.envelope is not None

    with store_transaction(_KNOWLEDGE_FENCE) as ticket:
        with pytest.raises(KnowledgeStoreViolation) as excinfo:
            store.commit_boundary(
                boundary=boundary,
                transaction_id=boundary.transaction_id,
                run_id=manifest.envelope.run_id,
                attempt_id=manifest.envelope.attempt_id,
                expected_parent_boundary_id=None,
                committed_members=frame.committed_members,
                ticket=ticket,
            )
    assert excinfo.value.failure_code is KnowledgeStoreFailureCode.SECOND_GENESIS
    assert store.current_boundary_id() == boundary.atomic_boundary_id.digest_sha256


def test_two_children_cannot_both_cas_the_same_authoritative_parent(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))
    first_id = first_boundary.atomic_boundary_id.digest_sha256

    winner = make_manifest(
        context,
        roots=make_roots(library=8, index=8, lifecycle=43),
        parent=first.snapshot_id,
    )
    winner_boundary = commit(
        root,
        winner,
        complete_decision(winner),
        transaction_id="tx-winner",
        start=first_boundary.commit_sequence,
        commit_sequence=7,
        parent=first_boundary,
    )
    sibling = make_manifest(
        context,
        roots=make_roots(library=9, index=9, lifecycle=44),
        parent=first.snapshot_id,
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root,
            sibling,
            complete_decision(sibling),
            transaction_id="tx-sibling",
            start=first_boundary.commit_sequence,
            commit_sequence=8,
            parent=first_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.LINEAGE_MISMATCH
    store = _KNOWLEDGE_STORES[str(root)]
    assert first_id != winner_boundary.atomic_boundary_id.digest_sha256
    assert store.current_boundary_id() == winner_boundary.atomic_boundary_id.digest_sha256
    assert not (root / "tx-sibling").exists()


def test_an_attempt_cannot_be_rebound_to_a_child_boundary(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))
    assert first.envelope is not None
    rebound_roots = make_roots(
        library=8,
        index=8,
        lifecycle=43,
        run_id=first.envelope.run_id,
        attempt_id=first.envelope.attempt_id,
    )
    rebound = make_manifest(context, roots=rebound_roots, parent=first.snapshot_id)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root,
            rebound,
            complete_decision(rebound),
            transaction_id="tx-rebound",
            start=first_boundary.commit_sequence,
            commit_sequence=7,
            parent=first_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS
    store = _KNOWLEDGE_STORES[str(root)]
    assert store.current_boundary_id() == first_boundary.atomic_boundary_id.digest_sha256
    assert store.attempt_boundary_id(first.envelope.attempt_id) == first_boundary.atomic_boundary_id.digest_sha256


def test_a_crash_before_staging_creates_no_authority(
    context, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "boundaries"
    roots = make_roots(fence=quiet_fence())
    manifest = make_manifest(context, roots=roots)

    def crash_before_staging(*args, **kwargs):
        raise RuntimeError("crash before staging")

    monkeypatch.setattr(K, "stage_snapshot_transaction", crash_before_staging)
    with pytest.raises(JournalAdapterViolation) as excinfo:
        commit(root, manifest, complete_decision(manifest))
    assert excinfo.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    store = _KNOWLEDGE_STORES[str(root)]
    assert store.current_boundary_id() is None
    assert not (root / "tx-1").exists()


def test_a_crash_after_staging_creates_no_authority(
    context, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "boundaries"
    roots = make_roots(fence=quiet_fence())
    manifest = make_manifest(context, roots=roots)

    def crash_after_staging(*args, **kwargs):
        raise RuntimeError("crash after staging")

    monkeypatch.setattr(K, "commit_snapshot_transaction", crash_after_staging)
    with pytest.raises(JournalAdapterViolation) as excinfo:
        commit(root, manifest, complete_decision(manifest))
    assert excinfo.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    store = _KNOWLEDGE_STORES[str(root)]
    assert store.current_boundary_id() is None
    assert (root / "tx-1").is_dir()
    assert not (root / "tx-1" / "commit-marker.json").exists()


def test_an_audit_marker_without_the_authoritative_frame_is_not_current(
    context, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "boundaries"
    roots = make_roots(fence=quiet_fence())
    manifest = make_manifest(context, roots=roots)

    def crash_before_authority(self, **kwargs):
        raise RuntimeError("crash before terminal authority frame")

    monkeypatch.setattr(AuthoritativeKnowledgeStore, "commit_boundary", crash_before_authority)
    with pytest.raises(JournalAdapterViolation) as excinfo:
        commit(root, manifest, complete_decision(manifest))
    assert excinfo.value.failure_code is JournalAdapterFailureCode.MUTATION_ABORTED
    audited = K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert audited.manifest.snapshot_id == manifest.snapshot_id
    assert type(audited) is K.CommittedKnowledgeSnapshotAudit
    assert not hasattr(audited, "executable_refs")
    store = _KNOWLEDGE_STORES[str(root)]
    assert store.current_boundary_id() is None
    with pytest.raises(KnowledgeStoreViolation) as excinfo:
        store.open_current()
    assert excinfo.value.failure_code is KnowledgeStoreFailureCode.BOUNDARY_MISSING


def test_commit_sequence_must_advance(context, tmp_path) -> None:
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    ensure_directory(tmp_path / "boundaries")
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(tmp_path / "boundaries", manifest, decision, start=5, commit_sequence=5)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC


def test_derived_snapshot_must_declare_its_parent(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    parent_boundary = commit(root, first, complete_decision(first))
    orphan = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, orphan, complete_decision(orphan),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


def test_commit_refuses_a_root_regression_against_the_parent(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    parent_boundary = commit(root, first, complete_decision(first))
    older = make_manifest(context, roots=make_roots(library=7, index=7, lifecycle=42), parent=first.snapshot_id)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, older, complete_decision(older),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ROLLBACK_DETECTED


# ---------------------------------------------------------------------------
# Restart, corruption and consumer revalidation
# ---------------------------------------------------------------------------


def test_restart_rejects_mutated_committed_bytes(context, tmp_path) -> None:
    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    commit(root, manifest, complete_decision(manifest))
    member = root / "tx-1" / K.MANIFEST_MEMBER_NAME
    member.chmod(0o600)
    member.write_bytes(b'{"tampered": true}')
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED


def test_restart_recovers_the_exact_authoritative_head_and_attempt_binding(
    context, tmp_path
) -> None:
    root = tmp_path / "boundaries"
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    assert manifest.envelope is not None
    original = _KNOWLEDGE_STORES[str(root)]
    original_frame = original._frames()[0]

    reopened = AuthoritativeKnowledgeStore(root, mutation_fence=_KNOWLEDGE_FENCE)
    assert reopened.current_sequence() == 1
    assert reopened.current_boundary_id() == boundary.atomic_boundary_id.digest_sha256
    assert reopened.attempt_boundary_id(manifest.envelope.attempt_id) == boundary.atomic_boundary_id.digest_sha256
    assert reopened.open_current().boundary.atomic_boundary_id == boundary.atomic_boundary_id
    assert reopened.open_for_attempt(manifest.envelope.attempt_id).manifest.snapshot_id == manifest.snapshot_id
    assert reopened._frames()[0].canonical_bytes() == original_frame.canonical_bytes()


def test_a_torn_authoritative_terminal_frame_never_recovers_as_current(
    context, tmp_path
) -> None:
    root = tmp_path / "boundaries"
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    store = _KNOWLEDGE_STORES[str(root)]
    store.path.write_bytes(store.path.read_bytes() + b"\x00")

    with pytest.raises(KnowledgeStoreViolation) as excinfo:
        AuthoritativeKnowledgeStore(root, mutation_fence=_KNOWLEDGE_FENCE)
    assert excinfo.value.failure_code is KnowledgeStoreFailureCode.STORE_TORN


def test_a_corrupted_authoritative_terminal_frame_never_recovers_as_current(
    context, tmp_path
) -> None:
    root = tmp_path / "boundaries"
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    store = _KNOWLEDGE_STORES[str(root)]
    raw = bytearray(store.path.read_bytes())
    raw[-1] ^= 0x01
    store.path.write_bytes(bytes(raw))

    with pytest.raises(KnowledgeStoreViolation) as excinfo:
        AuthoritativeKnowledgeStore(root, mutation_fence=_KNOWLEDGE_FENCE)
    assert excinfo.value.failure_code is KnowledgeStoreFailureCode.STORE_CORRUPT


def test_an_authoritative_head_with_a_missing_member_fails_closed(
    context, tmp_path
) -> None:
    root = tmp_path / "boundaries"
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    store = _KNOWLEDGE_STORES[str(root)]
    (root / "tx-1" / K.MANIFEST_MEMBER_NAME).unlink()

    with pytest.raises(KnowledgeStoreViolation) as excinfo:
        store.open_current()
    assert excinfo.value.failure_code is KnowledgeStoreFailureCode.MEMBER_MISMATCH


def test_one_attempt_consumes_one_boundary(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43), parent=first.snapshot_id)
    second_boundary = commit(
        root, second, complete_decision(second),
        transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
    )
    store = _KNOWLEDGE_STORES[str(root)]
    assert first.envelope is not None and second.envelope is not None
    assert store.open_for_attempt(first.envelope.attempt_id).boundary.atomic_boundary_id == first_boundary.atomic_boundary_id
    assert store.open_for_attempt(second.envelope.attempt_id).boundary.atomic_boundary_id == second_boundary.atomic_boundary_id
    assert store.current_boundary_id() == second_boundary.atomic_boundary_id.digest_sha256
    restored = store.open_for_attempt(first.envelope.attempt_id)
    K.require_snapshot_bound_to_attempt(
        restored, attempt_boundary_id=first_boundary.atomic_boundary_id, expected_context=context
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_snapshot_bound_to_attempt(
            restored, attempt_boundary_id=second_boundary.atomic_boundary_id, expected_context=context
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_usable_snapshot_refuses_a_boundary_other_than_the_attempt_boundary(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    # The parent boundary is passed now, not just named in the manifest. This
    # test used to commit a child that declared ``first`` as its parent while
    # handing the commit no parent at all — a chain nothing verified, which is
    # the defect the lineage comparison closes.
    first_boundary = commit(root, first, complete_decision(first))
    second = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43), parent=first.snapshot_id)
    second_boundary = commit(
        root, second, complete_decision(second), transaction_id="tx-2", start=2, commit_sequence=3,
        parent=first_boundary,
    )
    assert first.envelope is not None
    restored = _KNOWLEDGE_STORES[str(root)].open_for_attempt(first.envelope.attempt_id)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_snapshot_bound_to_attempt(
            restored,
            attempt_boundary_id=second_boundary.atomic_boundary_id,
            expected_context=context,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_consumer_revalidation_rejects_a_foreign_context(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    assert manifest.envelope is not None
    restored = _KNOWLEDGE_STORES[str(root)].open_for_attempt(manifest.envelope.attempt_id)
    foreign = K.create_knowledge_context(
        repository_revision="c" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.require_snapshot_bound_to_attempt(
            restored, attempt_boundary_id=boundary.atomic_boundary_id, expected_context=foreign
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


def test_revoked_object_leaves_the_executable_view_but_stays_in_audit(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    restored = _KNOWLEDGE_STORES[str(root)].open_current()

    assert len(restored.executable_refs(consumability_probe=lambda item: True)) == 2
    executable = restored.executable_refs(consumability_probe=lambda item: item.kind is not RefKind.BINDING)
    assert len(executable) == 1
    assert all(item.kind is not RefKind.BINDING for item in executable)
    # The manifest is audit history and still records every selected object.
    assert len(restored.manifest.selected_refs()) == 2


def test_usable_snapshot_rejects_direct_construction() -> None:
    with pytest.raises(TypeError):
        K.UsableKnowledgeSnapshot()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Committed fixtures
# ---------------------------------------------------------------------------


def _fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_valid_fixture_manifest_restores(context) -> None:
    restored = K.snapshot_manifest_from_dict(_fixture("valid_manifest.json"))
    assert restored.schema_version is K.SchemaVersion.KNOWLEDGE_SNAPSHOT_V1
    assert restored.snapshot_id.domain is IdentityDomain.KNOWLEDGE_SNAPSHOT


@pytest.mark.parametrize("name", ["incomplete_manifest.json", "unknown_field_manifest.json"])
def test_partial_or_unknown_field_fixture_is_rejected(name: str) -> None:
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.snapshot_manifest_from_dict(_fixture(name))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.PARTIAL_MANIFEST


def test_rollback_fixture_is_detected_against_the_baseline() -> None:
    baseline = K.SnapshotRootSet.from_dict(_fixture("baseline_roots.json")["roots"])
    rollback = K.snapshot_manifest_from_dict(_fixture("rollback_manifest.json"))
    assert K.detect_root_regression(rollback.roots, prior=baseline) is not None


def test_mixed_generation_fixture_is_detected_against_the_baseline() -> None:
    baseline = K.SnapshotRootSet.from_dict(_fixture("baseline_roots.json")["roots"])
    mixed = K.snapshot_manifest_from_dict(_fixture("mixed_generation_manifest.json"))
    assert K.detect_mixed_generation(mixed.roots, prior=baseline) is not None


# ---------------------------------------------------------------------------
# Mandatory mutation killers for Patch 7
# ---------------------------------------------------------------------------


def test_mutant_partial_manifest_treated_as_a_snapshot(context, tmp_path) -> None:
    """S4-MUT-ATOMIC-SNAPSHOT-01: a partial manifest must never be a snapshot."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with store_transaction(_KNOWLEDGE_FENCE) as ticket:
        stage_snapshot_transaction(
            root,
            transaction_id="tx-partial",
            members={
                K.MANIFEST_MEMBER_NAME: manifest.canonical_bytes(),
                K.DECISION_MEMBER_NAME: complete_decision(manifest).canonical_bytes(),
            },
            ticket=ticket,
        )
    with pytest.raises(K.KnowledgeViolation):
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-partial")
    with pytest.raises(K.KnowledgeViolation):
        K.snapshot_manifest_from_dict(_fixture("incomplete_manifest.json"))


def test_mutant_recovery_substitutes_an_older_root(context, tmp_path) -> None:
    """S4-MUT-ATOMIC-SNAPSHOT-02: an older root must never be selected."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    newer = make_manifest(context, roots=make_roots(library=9, index=9, lifecycle=50))
    parent_boundary = commit(root, newer, complete_decision(newer))
    older = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=49), parent=newer.snapshot_id)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, older, complete_decision(older),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ROLLBACK_DETECTED


def test_mutant_new_index_mixed_with_old_lifecycle(context) -> None:
    """A new index root beside an old lifecycle root must be refused."""

    mixed = make_roots(library=7, index=12, lifecycle=42)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: mixed),
        manifest=make_manifest(context, roots=mixed),
        prior_roots=make_roots(library=7, index=8, lifecycle=42),
    ).decision
    assert decision.status is SnapshotCompletenessStatus.MIX_AND_MATCH_DETECTED


def test_mutant_object_added_after_freeze(context, tmp_path) -> None:
    """An object added after the freeze must not reach a committed snapshot."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    commit(root, manifest, decision)

    with pytest.raises((AttributeError, TypeError)):
        manifest.behavior_refs = manifest.behavior_refs + (ref(RefKind.ARTIFACT, "late"),)  # type: ignore[misc]

    restored = K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert restored.manifest.snapshot_id.value == manifest.snapshot_id.value
    assert len(restored.manifest.selected_refs()) == 2

    # A manifest that really does gain an object is a different snapshot, and a
    # decision made over the frozen bytes does not describe it.
    extended = _create_manifest(
        context=context,
        roots=manifest.roots,
        behavior_refs=tuple(sorted(
            manifest.behavior_refs + (ref(RefKind.ARTIFACT, "late"),),
            key=lambda item: f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}",
        )),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    assert extended.snapshot_id.value != manifest.snapshot_id.value
    counterfactual_root = root / "extended-manifest"
    ensure_directory(counterfactual_root)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(counterfactual_root, extended, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


# ---------------------------------------------------------------------------
# Round 15 — every ref collection is resolved, and the boundary's two roots
# ---------------------------------------------------------------------------


def evidence_manifest(
    context: K.KnowledgeContext,
    *,
    roots: K.SnapshotRootSet | None = None,
) -> K.SnapshotManifest:
    """A manifest that actually carries both compatibility evidence collections.

    ``make_manifest`` leaves ``conflict_refs`` empty, so a test written against
    it cannot tell a collection that is resolved from one that is skipped — an
    empty tuple passes either way.
    """

    admission_refs = (ref(RefKind.GATE_DECISION, "gate-1"),)
    retrieval_refs = (ref(RefKind.GATE_DECISION, "retrieval-1"),)
    conflict_refs = (ref(RefKind.CONTRACT_CONDITION, "conflict-1"),)
    selected_roots = roots or make_roots(
        admission_refs=admission_refs,
        retrieval_refs=retrieval_refs,
        compatibility_refs=conflict_refs,
    )
    return _create_manifest(
        context=context,
        roots=selected_roots,
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=admission_refs,
        retrieval_decision_refs=retrieval_refs,
        conflict_refs=conflict_refs,
        created_at_utc=NOW,
    )


@pytest.mark.parametrize("dangling", ["retrieval-1", "conflict-1"])
def test_a_dangling_compatibility_evidence_ref_blocks_completeness(context, dangling) -> None:
    """COMPLETE is the status that admits execution, so what it skips is executable.

    Both collections used to go unresolved. A snapshot whose retrieval decisions
    or conflict records did not exist was declared COMPLETE and passed
    ``require_snapshot_status_admits_execution`` — the manifest named evidence
    and nothing checked that the evidence was there.

    Blocking by ``ref_id`` rather than by kind is deliberate: several
    collections share a ``RefKind``, so a kind-shaped test cannot say which
    collection the evaluator actually consulted.
    """

    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots,
            resolver=lambda item: item.ref_id != dangling,
        ),
        manifest=manifest,
    ).decision
    assert decision.status is SnapshotCompletenessStatus.INCOMPLETE_COMPATIBILITY_DATA
    assert decision.status is not SnapshotCompletenessStatus.COMPLETE


def test_a_manifest_with_every_ref_resolvable_is_complete(context) -> None:
    """Guards the two tests above from passing because nothing can ever pass."""

    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots), manifest=manifest
    ).decision
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_completeness_resolves_every_ref_collection_the_manifest_declares(context) -> None:
    """A seventh collection must not be able to land unresolved.

    Naming the collections in the evaluator only protects the ones someone
    remembered to name. This asks the manifest what it carries and asserts the
    evaluator consulted each one, so adding a field without wiring it fails here
    rather than silently widening what COMPLETE overlooks.
    """

    manifest = evidence_manifest(context)
    payload = manifest.to_dict()["payload"]
    declared = tuple(
        name
        for name in payload
        if name.endswith("_refs") and payload[name]
    )
    assert len(declared) == 6, f"the manifest carries {declared}; keep this test in step"

    consulted: list[str] = []
    K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots,
            resolver=lambda item: consulted.append(item.ref_id) or True,
        ),
        manifest=manifest,
    )
    for name in declared:
        for entry in payload[name]:
            assert entry["ref_id"] in consulted, (
                f"{name} was declared by the manifest and never resolved; a snapshot "
                "can reach COMPLETE with it dangling"
            )


def test_the_compatibility_root_is_a_confirmed_durable_prefix(context, tmp_path) -> None:
    """The boundary records the supplied prefix only after history confirms it."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = evidence_manifest(context)
    decision = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots), manifest=manifest
    ).decision
    boundary = commit(root, manifest, decision)
    assert (
        boundary.compatibility_evidence_root_sha256
        == manifest.roots.compatibility_evidence_manifest_ref.sha256
    )

    other = tmp_path / "unconfirmed"
    ensure_directory(other)
    unconfirmed = evidence_manifest(context)
    fixture = _ROOT_FIXTURES[id(unconfirmed.roots)]
    fixture.compatibility_history.anchor = hashlib.sha256(b"other-compatibility").hexdigest()
    fixture.compatibility_history._known_anchors = {fixture.compatibility_history.anchor}
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(other, unconfirmed, complete_decision(unconfirmed))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMPATIBILITY_ROOT_UNCONFIRMED
    assert _no_marker(other)


def test_the_compatibility_root_moves_with_the_evidence_and_with_its_order(context) -> None:
    """Different evidence, and the same evidence in a different order, differ.

    Order matters because two snapshots holding the same records in a different
    order are different states; a root that collapsed them would let one be
    presented as the other.
    """

    base = evidence_manifest(context)
    without_conflict = _create_manifest(
        context=context,
        roots=base.roots,
        behavior_refs=base.behavior_refs,
        binding_refs=base.binding_refs,
        attestation_refs=base.attestation_refs,
        admission_refs=base.admission_refs,
        retrieval_decision_refs=base.retrieval_decision_refs,
        conflict_refs=(),
        created_at_utc=NOW,
    )
    assert K.compatibility_evidence_root(base) != K.compatibility_evidence_root(without_conflict)


def test_the_same_records_filed_the_other_way_round_are_a_different_state(context) -> None:
    """Order-sensitivity tested through the function, not beside it.

    An earlier version of this test built the chain by hand and compared two
    hand-built orders. That checks arithmetic, not the code: a mutant that sorts
    the evidence *inside* ``compatibility_evidence_root`` survived it, because
    the hand-built comparison never called the function.

    The separating case is the same two records filed the other way round. A
    record entered as a retrieval decision and a record entered as a conflict
    are different claims about the snapshot, so swapping them must move the
    root. Any implementation that sorts the evidence collapses the two into one
    value and lets either be presented as the other.
    """

    # Distinct payloads, and deliberately in the order sorting would reverse: a
    # first attempt gave both refs the default payload, so their digests were
    # equal, ``sorted`` was stable, and the mutant that sorts was indisponible
    # from the real code. The assertion that was supposed to establish
    # distinctness compared a pair with its own permutation and passed either
    # way — a check that checked nothing.
    left = ref(RefKind.GATE_DECISION, "evidence-a", b"evidence-a")
    right = ref(RefKind.GATE_DECISION, "evidence-b", b"evidence-b")
    assert left.sha256 > right.sha256, "the fixture must be in the order sorting would change"
    one = _create_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(left,), conflict_refs=(right,), created_at_utc=NOW,
    )
    other = _create_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(ref(RefKind.BINDING, "binding-1"),),
        attestation_refs=(ref(RefKind.SOURCE_EVIDENCE, "attestation-1"),),
        admission_refs=(ref(RefKind.GATE_DECISION, "gate-1"),),
        retrieval_decision_refs=(right,), conflict_refs=(left,), created_at_utc=NOW,
    )
    assert K.compatibility_evidence_root(one) != K.compatibility_evidence_root(other)


def test_an_admission_root_the_journal_does_not_confirm_is_refused(context, tmp_path) -> None:
    """The boundary must not attest an admission history that never existed."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    history = _ROOT_FIXTURES[id(manifest.roots)].admission_history
    history.anchor = hashlib.sha256(b"unrelated-admission-history").hexdigest()
    history._known_anchors = {history.anchor}
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, decision)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ADMISSION_ROOT_UNCONFIRMED


def test_an_admission_root_still_confirms_after_the_journal_grows(context, tmp_path) -> None:
    """A snapshot commit refuses history movement after its exact prefix capture."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    journal = _ROOT_FIXTURES[id(manifest.roots)].admission_history
    witnessed = journal.current_anchor()
    journal.append_record(b"a later unrelated decision")
    assert journal.current_anchor() != witnessed

    with pytest.raises(K.KnowledgeViolation) as caught:
        commit(root, manifest, complete_decision(manifest), journal=journal)
    assert caught.value.failure_code is K.KnowledgeFailureCode.ROOT_SET_MISMATCH


@pytest.mark.parametrize(
    "history_name,expected_detail",
    (
        ("admission_history", "historical admission ref"),
        ("causal_history", "historical retrieval decision ref"),
        ("compatibility_history", "historical conflict ref"),
    ),
)
def test_current_history_membership_cannot_replace_exact_prefix_membership(
    context, tmp_path, history_name: str, expected_detail: str
) -> None:
    admission_refs = (ref(RefKind.GATE_DECISION, "admission-prefix-ref"),)
    retrieval_refs = (ref(RefKind.GATE_DECISION, "retrieval-prefix-ref"),)
    compatibility_refs = (ref(RefKind.CONTRACT_CONDITION, "compatibility-prefix-ref"),)
    roots = make_roots(
        admission_refs=admission_refs,
        retrieval_refs=retrieval_refs,
        compatibility_refs=compatibility_refs,
    )
    manifest = _create_manifest(
        context=context,
        roots=roots,
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-1"),),
        binding_refs=(),
        attestation_refs=(),
        admission_refs=admission_refs,
        retrieval_decision_refs=retrieval_refs,
        conflict_refs=compatibility_refs,
        created_at_utc=NOW,
    )
    fixture = _ROOT_FIXTURES[id(roots)]
    history = getattr(fixture, history_name)
    prefix = (history.current_anchor(), history.current_sequence())
    assert all(history.contains_ref(item) for item in (
        admission_refs if history_name == "admission_history"
        else retrieval_refs if history_name == "causal_history"
        else compatibility_refs
    ))
    history._prefix_refs[prefix] = set()

    root = tmp_path / history_name
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, complete_decision(manifest))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.UNRESOLVED_REFERENCE
    assert expected_detail in excinfo.value.detail
    assert _no_marker(root)


@pytest.mark.parametrize(
    "history_name",
    ("admission_history", "causal_history", "compatibility_history"),
)
def test_a_child_history_prefix_that_does_not_extend_its_parent_is_rollback(
    context, tmp_path, history_name: str
) -> None:
    root = tmp_path / history_name
    parent = make_manifest(context)
    parent_boundary = commit(root, parent, complete_decision(parent))
    child_roots = make_roots(library=8, index=8, lifecycle=43)
    child = make_manifest(context, roots=child_roots, parent=parent.snapshot_id)
    history = getattr(_ROOT_FIXTURES[id(child_roots)], history_name)
    history.prefix_extends = lambda *args: False

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root,
            child,
            complete_decision(child),
            transaction_id="tx-child",
            start=parent_boundary.commit_sequence,
            commit_sequence=7,
            parent=parent_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ROLLBACK_DETECTED
    assert _no_marker(root, "tx-child")


def test_a_port_that_reports_an_outage_keeps_its_classification(context, tmp_path) -> None:
    """A port speaking this owner's vocabulary is believed, not re-guessed."""

    class Unreachable(_FixedHistory):
        def current_anchor(self) -> str:
            raise K.KnowledgeViolation(
                K.KnowledgeFailureCode.STORE_UNAVAILABLE, "journal is down"
            )

        def extends(self, anchor: str) -> bool:
            raise K.KnowledgeViolation(
                K.KnowledgeFailureCode.STORE_UNAVAILABLE, "journal is down"
            )

        def contains_record(self, digest: str) -> bool:
            raise K.KnowledgeViolation(
                K.KnowledgeFailureCode.STORE_UNAVAILABLE, "journal is down"
            )

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    fixture = _ROOT_FIXTURES[id(manifest.roots)]
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Unreachable(anchor=ADMISSION_ROOT, fence=fixture.fence),
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.STORE_UNAVAILABLE


def test_a_failure_this_owner_cannot_recognise_is_not_called_an_outage(
    context, tmp_path
) -> None:
    """The regression this round reverses.

    §21 may not import the admission package, so it cannot recognise that
    package's exception types. Reporting an unrecognised failure as
    ``STORE_UNAVAILABLE`` told an operator the store was unreachable and the
    commit could be retried, when the truth might be a corrupt journal that no
    retry will fix — the substitution NR-10 exists to forbid.
    """

    class Foreign(_FixedHistory):
        def current_anchor(self) -> str:
            raise RuntimeError("something this owner has never heard of")

        def extends(self, anchor: str) -> bool:
            raise RuntimeError("something this owner has never heard of")

        def contains_record(self, digest: str) -> bool:
            raise RuntimeError("something this owner has never heard of")

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    fixture = _ROOT_FIXTURES[id(manifest.roots)]
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Foreign(anchor=ADMISSION_ROOT, fence=fixture.fence),
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.ADMISSION_HISTORY_UNCLASSIFIED
    assert excinfo.value.failure_code is not K.KnowledgeFailureCode.STORE_UNAVAILABLE


@pytest.mark.parametrize(
    "arrange,expected",
    [
        ("corrupt", "ADMISSION_HISTORY_CORRUPT"),
        ("unreachable", "STORE_UNAVAILABLE"),
    ],
)
def test_the_wrapped_journal_reports_corruption_and_outage_apart(
    tmp_path, arrange, expected
) -> None:
    """`FileAdmissionJournal` keeps the two apart; the wrapper carries that across.

    Raw, its `JournalAdapterViolation` is a type §21 cannot name, so a corrupt
    store arrives unclassified. Wrapped, the distinction the journal took care to
    make survives the boundary between the two owners.
    """

    from synapse.experiments.gold.gate_findings import SnapshotAdmissionHistory

    if arrange == "corrupt":
        directory = tmp_path / "adm"
        directory.mkdir()
        (directory / "decisions.journal").write_bytes(b"somebody else's file entirely")
        path = directory / "decisions.journal"
    else:
        blocked = tmp_path / "blocked"
        blocked.write_bytes(b"not a directory")
        path = blocked / "adm" / "decisions.journal"

    # The fence is rooted outside the arranged damage on purpose. This test asks
    # how the *journal* reports corruption and an outage, and a fence that was
    # itself unreachable would make the refusal arrive for a reason the assertion
    # does not name.
    wrapped = SnapshotAdmissionHistory(FileAdmissionJournal(path, fence_for(tmp_path)))
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        wrapped.current_anchor()
    assert excinfo.value.failure_code.value == expected


@pytest.mark.parametrize("substitute", [None, object(), "journal"])
def test_a_commit_requires_something_shaped_like_an_admission_journal(
    context, tmp_path, substitute
) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=substitute,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def test_a_journal_answering_extends_with_the_wrong_type_is_a_broken_adapter(
    context, tmp_path
) -> None:
    """A truthy non-bool must not be read as confirmation."""

    class Sloppy(_FixedHistory):
        def extends(self, anchor: str) -> object:
            return "yes"

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    fixture = _ROOT_FIXTURES[id(manifest.roots)]
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, manifest, complete_decision(manifest),
            journal=Sloppy(anchor=ADMISSION_ROOT, fence=fixture.fence),
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def test_a_sequence_gap_between_boundaries_is_refused(context, tmp_path) -> None:
    """A child begins exactly where its parent ended.

    This test previously asserted the opposite, on my reasoning that lineage
    travels in ``parent_boundary_digest`` so the numbers need only order. That is
    wrong in the part that matters: the digest proves *which* parent, not that
    nothing went unrecorded in between. §21 specifies a monotonic transaction
    range with "gaps/forks/rollback detected", and a gap that is accepted is a
    gap that is not detected.

    Both directions are refused here — a gap forward and a step backward — so the
    rule is pinned as equality rather than as an inequality that happens to hold.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    parent = commit(root, manifest, complete_decision(manifest))

    child_manifest = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )

    for label, start, commit_sequence in (
        ("a gap forward", 1000, 1001),
        ("a step backward", 0, 1),
        ("one past the parent", parent.commit_sequence + 1, parent.commit_sequence + 2),
    ):
        with pytest.raises(K.KnowledgeViolation) as excinfo:
            commit(
                root, child_manifest, complete_decision(child_manifest),
                transaction_id=f"tx-{start}", start=start,
                commit_sequence=commit_sequence, parent=parent,
            )
        assert excinfo.value.failure_code is K.KnowledgeFailureCode.SEQUENCE_NOT_MONOTONIC, label

    # The contiguous child commits, so the three refusals above are not a
    # blanket refusal of every derived boundary.
    child = commit(
        root, child_manifest, complete_decision(child_manifest),
        transaction_id="tx-next", start=parent.commit_sequence,
        commit_sequence=parent.commit_sequence + 1, parent=parent,
    )
    assert child.start_sequence == parent.commit_sequence
    assert child.parent_boundary_digest == parent.atomic_boundary_id.digest_sha256


# ---------------------------------------------------------------------------
# Round 16 — the on-disk adversary, and the §22 boundary probe
# ---------------------------------------------------------------------------


MARKER_NAME = "commit-marker.json"


def rewrite_marker(root: Path, transaction_id: str, member: str, payload: bytes) -> None:
    """Replace a committed member *and* the digest the marker records for it.

    The adversary who only edits a member is already refused: the marker pins a
    digest per member and the mismatch is corruption. This is the next adversary
    along — one who can rewrite the marker too — and everything §21 checks after
    persistence is satisfied has to stand on its own against them.
    """

    directory = root / transaction_id
    path = directory / member
    path.chmod(0o600)
    path.write_bytes(payload)
    marker_path = directory / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    for entry in marker["members"]:
        if entry["member_name"] == member:
            entry["sha256"] = hashlib.sha256(payload).hexdigest()
            entry["byte_length"] = len(payload)
    marker_path.chmod(0o600)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")


def test_a_rewritten_manifest_with_a_matching_marker_is_still_refused(context, tmp_path) -> None:
    """Persistence satisfied, and the snapshot still does not open.

    The existing mutated-bytes test stops at the layer below: it edits a member
    and the recorded digest catches it. That proves persistence works, not that
    §21 does. Here the marker is rewritten to agree with the tampered bytes, so
    every integrity check passes and §21's own bindings are the only thing left
    standing between the adversary and a usable snapshot.

    The binding that catches it is the decision's: a completeness decision names
    the manifest it was reached over, and the planted manifest is not that one.
    That happens at decode, before the marker is consulted at all — which is why
    this test cannot also stand in for the marker check, and why the marker gets
    a test of its own below.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))

    other = _create_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-9"),),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    rewrite_marker(root, "tx-1", K.MANIFEST_MEMBER_NAME, other.canonical_bytes())

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_a_boundary_moved_into_another_transaction_is_refused(context, tmp_path) -> None:
    """Two committed snapshots, and one's boundary planted in the other.

    Every byte is genuine — the boundary really was committed, just not here.
    Without the transaction-id binding the planted boundary would verify against
    its own manifest hash and marker, and the two transactions would become
    interchangeable.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))
    second = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=first.snapshot_id
    )
    commit(
        root, second, complete_decision(second), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=first_boundary,
    )

    planted = (root / "tx-2" / K.BOUNDARY_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.BOUNDARY_MEMBER_NAME, planted)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH


def test_a_decision_swapped_after_the_marker_is_refused(context, tmp_path) -> None:
    """A decision that describes another snapshot is refused as it is decoded."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    first_boundary = commit(root, manifest, complete_decision(manifest))
    other = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )
    commit(
        root, other, complete_decision(other), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=first_boundary,
    )

    planted = (root / "tx-2" / K.DECISION_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.DECISION_MEMBER_NAME, planted)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.DECISION_SUBJECT_MISMATCH


def test_an_untouched_committed_snapshot_still_opens(context, tmp_path) -> None:
    """Guards the three adversaries above from passing because nothing opens."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    restored = K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert restored.boundary.atomic_boundary_id.value == boundary.atomic_boundary_id.value


def test_a_boundary_is_named_by_its_own_identity(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    reference = K.atomic_boundary_ref(boundary)
    assert reference.kind is RefKind.ATOMIC_BOUNDARY
    assert reference.ref_id == boundary.atomic_boundary_id.digest_sha256
    assert reference.sha256 == hashlib.sha256(boundary.canonical_bytes()).hexdigest()


def boundary_probe_for(root: Path, boundary, context):
    from synapse.experiments.gold.gate_findings import configured_boundary_probe

    store = AuthoritativeKnowledgeStore(root, mutation_fence=_KNOWLEDGE_FENCE)
    snapshot = store.open_current()
    assert snapshot.manifest.envelope is not None
    evaluator = make_evaluator(
        observed=lambda: snapshot.manifest.roots,
        root_fence=_KNOWLEDGE_FENCE,
    )
    return configured_boundary_probe(
        knowledge_store=store,
        attempt_id=snapshot.manifest.envelope.attempt_id,
        expected_context=context,
        evaluator_declaration=evaluator.declaration,
        evaluator_actor_set=evaluator.actor_set,
        evaluator_independence_proof=evaluator.independence_proof,
    )


def test_the_boundary_probe_confirms_the_committed_boundary(context, tmp_path) -> None:
    """The §22 gate's boundary answer now comes from a committed §21 snapshot.

    ``configure_gate_controller`` took a ``boundary_probe`` and every caller
    supplied a callable of its own. The gate asked *something* whether a boundary
    was committed and nothing required that something to have resolved the
    authoritative store frame.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    assert probe(K.atomic_boundary_ref(boundary)) is True


def test_the_boundary_probe_denies_a_reference_to_another_boundary(context, tmp_path) -> None:
    """Verified, and simply not the boundary this attempt is bound to."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    boundary = commit(root, first, complete_decision(first))
    second = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=first.snapshot_id
    )
    other = commit(
        root, second, complete_decision(second), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=boundary,
    )

    probe = boundary_probe_for(root, other, context)
    assert probe(K.atomic_boundary_ref(boundary)) is False
    assert probe(K.atomic_boundary_ref(other)) is True


def test_the_boundary_probe_reads_the_store_at_the_moment_it_is_asked(context, tmp_path) -> None:
    """Wiring-time verification would describe a moment that has passed.

    The probe is built while the snapshot is intact and asked after the committed
    bytes have been rewritten under it, marker and all. A probe that verified once
    at construction would still answer yes.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    assert probe(K.atomic_boundary_ref(boundary)) is True

    other = _create_manifest(
        context=context, roots=make_roots(),
        behavior_refs=(ref(RefKind.ARTIFACT, "behavior-9"),),
        binding_refs=manifest.binding_refs,
        attestation_refs=manifest.attestation_refs,
        admission_refs=manifest.admission_refs,
        retrieval_decision_refs=manifest.retrieval_decision_refs,
        conflict_refs=manifest.conflict_refs,
        created_at_utc=NOW,
    )
    rewrite_marker(root, "tx-1", K.MANIFEST_MEMBER_NAME, other.canonical_bytes())

    with pytest.raises(K.KnowledgeViolation):
        probe(K.atomic_boundary_ref(boundary))


def test_a_damaged_snapshot_is_not_reported_as_a_different_boundary(context, tmp_path) -> None:
    """Damage and mismatch are different facts and must not share an answer."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)

    member = root / "tx-1" / K.MANIFEST_MEMBER_NAME
    member.chmod(0o600)
    member.write_bytes(b'{"tampered": true}')
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(K.atomic_boundary_ref(boundary))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED


def test_an_unreadable_boundary_store_is_declared_as_a_gate_outage(context, tmp_path) -> None:
    """The gate classifies by exception type, so an outage must arrive as one."""

    from synapse.experiments.gold.admission import GateDependencyUnavailable

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)

    # The whole committed transaction goes away, which is the store being
    # unreadable rather than the snapshot being wrong. Answering False here
    # would tell the gate "that is a different boundary", which is a claim
    # nobody is in a position to make.
    shutil.rmtree(root / "tx-1")
    with pytest.raises(GateDependencyUnavailable):
        probe(K.atomic_boundary_ref(boundary))


def test_the_boundary_probe_refuses_a_foreign_consumer_context(context, tmp_path) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    foreign = K.create_knowledge_context(
        repository_revision="b" * 40, policy_version="policy-v1", environment_profile_id="env-1"
    )
    probe = boundary_probe_for(root, boundary, foreign)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(K.atomic_boundary_ref(boundary))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MIX_AND_MATCH_DETECTED


@pytest.mark.parametrize("substitute", [None, object(), "boundary"])
def test_the_boundary_probe_requires_an_exact_reference(context, tmp_path, substitute) -> None:
    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    boundary = commit(root, manifest, complete_decision(manifest))
    probe = boundary_probe_for(root, boundary, context)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        probe(substitute)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def edit_marker(root: Path, transaction_id: str, **fields) -> None:
    """Change marker fields while leaving the member digests correct.

    The member digests are what persistence checks; these fields are what §21
    checks. Editing them separately is how each §21 binding gets a case that
    reaches it, instead of a case that trips whichever guard happens to run
    first.
    """

    marker_path = root / transaction_id / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker.update(fields)
    marker_path.chmod(0o600)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")


def test_a_marker_naming_another_boundary_is_refused(context, tmp_path) -> None:
    """Isolated: only the marker's boundary id is wrong."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    first_boundary = commit(root, manifest, complete_decision(manifest))
    other = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )
    elsewhere = commit(
        root, other, complete_decision(other), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=first_boundary,
    )
    edit_marker(root, "tx-1", boundary_id=elsewhere.atomic_boundary_id.value)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "names a different boundary" in excinfo.value.detail


def test_a_marker_hash_that_differs_from_the_boundary_is_refused(context, tmp_path) -> None:
    """Isolated: only the marker's own hash is wrong."""

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    commit(root, manifest, complete_decision(manifest))
    edit_marker(root, "tx-1", marker_sha256=hashlib.sha256(b"not the marker").hexdigest())

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "commit marker hash differs" in excinfo.value.detail


def test_a_boundary_from_another_transaction_is_refused_even_with_a_matching_marker(
    context, tmp_path
) -> None:
    """The adversary controls the marker completely, and the id still refuses.

    Everything is made consistent: the planted boundary's own id and marker hash
    are written into the marker, so the two checks before this one pass by
    construction. What the planted boundary cannot change is which transaction it
    was committed under.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    first_boundary = commit(root, manifest, complete_decision(manifest))
    other = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )
    elsewhere = commit(
        root, other, complete_decision(other), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=first_boundary,
    )

    planted = (root / "tx-2" / K.BOUNDARY_MEMBER_NAME).read_bytes()
    rewrite_marker(root, "tx-1", K.BOUNDARY_MEMBER_NAME, planted)
    edit_marker(
        root, "tx-1",
        boundary_id=elsewhere.atomic_boundary_id.value,
        marker_sha256=elsewhere.commit_marker,
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "transaction id differs" in excinfo.value.detail


def test_a_consistent_pair_from_another_snapshot_breaks_the_commit_binding(
    context, tmp_path
) -> None:
    """The case only the commit marker catches.

    Both members are replaced together by another transaction's genuine pair, so
    the decision does describe its manifest and the decode-time binding is
    satisfied. The boundary is untouched, so its id and marker hash still agree
    with the marker. The one thing left that can tell the truth is the marker the
    boundary recorded over the bytes it actually committed.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    manifest = make_manifest(context)
    first_boundary = commit(root, manifest, complete_decision(manifest))
    other = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=99), parent=manifest.snapshot_id
    )
    commit(
        root, other, complete_decision(other), transaction_id="tx-2",
        start=2, commit_sequence=4, parent=first_boundary,
    )

    rewrite_marker(
        root, "tx-1", K.MANIFEST_MEMBER_NAME,
        (root / "tx-2" / K.MANIFEST_MEMBER_NAME).read_bytes(),
    )
    rewrite_marker(
        root, "tx-1", K.DECISION_MEMBER_NAME,
        (root / "tx-2" / K.DECISION_MEMBER_NAME).read_bytes(),
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert "not the ones this boundary marked" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 18 — minting the frozen candidate set from a committed snapshot
#
# `gate_findings.frozen_candidates_from_snapshot` is what turns a committed §21
# boundary into the set retrieval is allowed to consider. Three mutants in the
# round-18 campaign survived because nothing here exercised the refusal side:
# the mint skipping authoritative attempt-bound snapshot opening entirely, a
# manifest whose behavior refs are not library subject names being accepted, and
# a snapshot that selected no behavior being reported as constraining something.
# ---------------------------------------------------------------------------


def subject_ref(name: str) -> HashBoundRef:
    """A §22 library subject name, built without a library.

    ``library_subject_ref`` needs only the four identity values, which is the
    property that lets the write side compute the name the read side will compute
    later. It also lets this suite build a snapshot that genuinely constrains
    retrieval without paying for a real library and a real git repository.
    """

    from synapse.experiments.gold.canonicalization import library_subject_ref

    blob = hashlib.sha256(f"frozen-blob-{name}".encode()).hexdigest()
    manifest = hashlib.sha256(f"frozen-manifest-{name}".encode()).hexdigest()
    return library_subject_ref(
        content_key=f"synapse.stage4.gold.content-key/v1:{blob}",
        manifest_id=f"BEHAVIOR_MANIFEST:{manifest}",
        blob_digest_sha256=blob,
        manifest_digest_sha256=manifest,
    )


def _canonically_ordered(refs) -> tuple[HashBoundRef, ...]:
    return tuple(
        sorted(refs, key=lambda item: f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}")
    )


def subject_manifest(
    context: K.KnowledgeContext,
    *,
    subjects: tuple[str, ...] = ("alpha", "beta"),
    behavior_refs=None,
    binding_refs=(),
    roots: K.SnapshotRootSet | None = None,
    parent=None,
) -> K.SnapshotManifest:
    """A manifest whose behavior refs are library subject names."""

    return _create_manifest(
        context=context,
        roots=roots or make_roots(admission_refs=(), retrieval_refs=()),
        behavior_refs=(
            _canonically_ordered(subject_ref(name) for name in subjects)
            if behavior_refs is None
            else behavior_refs
        ),
        binding_refs=binding_refs,
        attestation_refs=(),
        admission_refs=(),
        retrieval_decision_refs=(),
        conflict_refs=(),
        created_at_utc=NOW,
        parent_snapshot_id=parent,
    )


def _committed(
    root: Path,
    manifest,
    *,
    transaction_id="tx-frozen",
    start=1,
    commit_sequence=2,
    parent=None,
):
    ensure_directory(root)
    commit(
        root, manifest, complete_decision(manifest),
        transaction_id=transaction_id, start=start, commit_sequence=commit_sequence,
        parent=parent,
    )
    assert manifest.envelope is not None
    return _KNOWLEDGE_STORES[str(root)].open_for_attempt(manifest.envelope.attempt_id)


def _mint(root: Path, *, context, transaction_id="tx-frozen", boundary_id=None, snapshot=None):
    """Mint through the real adapter, which opens the transaction for itself.

    The snapshot handed in here is used only to learn the boundary id the attempt
    binds to. It is deliberately not passed on: the mint has no snapshot parameter
    any more, because a caller-supplied one is the stale source in the pair.
    """

    from synapse.experiments.gold import gate_findings as GF

    store = _KNOWLEDGE_STORES[str(root)]
    selected = snapshot or store.open_current()
    assert selected.manifest.envelope is not None
    evaluator = make_evaluator(
        observed=lambda: selected.manifest.roots,
        root_fence=_KNOWLEDGE_FENCE,
    )
    return GF.frozen_candidates_from_snapshot(
        knowledge_store=store,
        attempt_id=selected.manifest.envelope.attempt_id,
        expected_context=context,
        frozen_at_utc=NOW,
        evaluator_declaration=evaluator.declaration,
        evaluator_actor_set=evaluator.actor_set,
        evaluator_independence_proof=evaluator.independence_proof,
    )


def test_a_frozen_set_names_exactly_the_subjects_the_snapshot_froze(context, tmp_path) -> None:
    """The set is derived from the manifest, not supplied alongside it."""

    manifest = subject_manifest(context)
    root = tmp_path / "boundaries"
    snapshot = _committed(root, manifest)
    frozen = _mint(root, context=context, snapshot=snapshot)

    expected = tuple(
        sorted(
            f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}"
            for item in manifest.behavior_refs
        )
    )
    assert frozen.subject_ref_keys == expected
    assert frozen.boundary_id_sha256 == snapshot.boundary.atomic_boundary_id.digest_sha256
    assert frozen.snapshot_id_sha256 == manifest.snapshot_id.digest_sha256
    assert expected[0] in frozen


def test_a_frozen_set_cannot_be_minted_for_another_attempt_boundary(context, tmp_path) -> None:
    """One attempt consumes one boundary, and the mint is where that is checked.

    Minting without re-verifying would let an attempt bound to one boundary
    receive the frozen world of another — a silent substitution of what the run
    is allowed to know, with every individual record still valid.
    """

    root = tmp_path / "boundaries"
    first = _committed(root, subject_manifest(context))
    second_manifest = subject_manifest(
        context,
        subjects=("gamma",),
        roots=make_roots(
            library=9,
            index=9,
            admission_refs=(),
            retrieval_refs=(),
        ),
        parent=first.manifest.snapshot_id,
    )
    second = _committed(
        root,
        second_manifest,
        transaction_id="tx-frozen-2", start=2, commit_sequence=4,
        parent=first.boundary,
    )
    assert first.boundary.atomic_boundary_id.value != second.boundary.atomic_boundary_id.value

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context, snapshot=first)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.MULTIPLE_ACTIVE_SNAPSHOTS


def test_a_frozen_set_cannot_be_minted_against_a_foreign_consumer_context(context, tmp_path) -> None:
    """A snapshot taken of one repository state does not constrain another."""

    root = tmp_path / "boundaries"
    _committed(root, subject_manifest(context))
    foreign = K.create_knowledge_context(
        repository_revision="b" * 40,
        policy_version="policy-v1",
        environment_profile_id="env-1",
    )
    assert foreign.repository_revision != context.repository_revision

    with pytest.raises(K.KnowledgeViolation):
        _mint(root, context=foreign)


def test_a_frozen_set_cannot_be_minted_after_the_commit_marker_is_removed(
    context, tmp_path
) -> None:
    """The reviewer's P0 sequence, and the reason the mint stopped taking a snapshot.

    Open a snapshot, delete the terminal commit marker, mint. Round 19 produced a
    `FrozenCandidateSet` here for a snapshot that no longer existed, because the
    mint called `require_snapshot_bound_to_attempt` — which reads no disk — under a
    docstring claiming the snapshot had been re-verified at mint time.

    Nothing about the in-memory records is wrong: they were opened from a genuinely
    committed transaction and every identity still recomputes. That is exactly why
    re-checking the object cannot detect this and re-opening the transaction can.
    """

    root = tmp_path / "boundaries"
    # The attempt is bound to this boundary before the damage, which is the
    # situation being modelled: a holder that legitimately opened a snapshot and
    # kept its identity.
    snapshot = _committed(root, subject_manifest(context))
    marker = root / "tx-frozen" / "commit-marker.json"
    assert marker.exists()
    marker.unlink()

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context, snapshot=snapshot)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMIT_MARKER_ABSENT


def test_a_frozen_set_cannot_be_minted_when_committed_bytes_were_edited(
    context, tmp_path
) -> None:
    """The other durability failure, kept apart from an absent marker.

    A member rewritten after the commit is corruption, not absence, and §21
    reports the two separately. The mint inherits that distinction by re-opening
    rather than by classifying anything itself.
    """

    root = tmp_path / "boundaries"
    snapshot = _committed(root, subject_manifest(context))
    member = root / "tx-frozen" / K.MANIFEST_MEMBER_NAME
    raw = bytearray(member.read_bytes())
    raw[-2] ^= 0x01
    member.write_bytes(bytes(raw))

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context, snapshot=snapshot)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED


def test_a_snapshot_whose_behavior_refs_are_not_library_subjects_constrains_nothing(
    context, tmp_path
) -> None:
    """Refused at the point of use, not accepted with a set that matches nothing.

    `behavior_refs` is kind-constrained but not schema-constrained, so a manifest
    may carry refs that are not library subject names. Such a snapshot cannot
    constrain retrieval at all — every live entry would fail to match — and
    accepting it would silently permit everything instead of refusing once.
    """

    manifest = make_manifest(context)
    assert all(
        item.schema_id != "synapse.stage4.gold.library-subject/v1"
        for item in manifest.behavior_refs
    )
    root = tmp_path / "boundaries"
    _committed(root, manifest)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context)
    assert "library subject names" in excinfo.value.detail


def test_a_snapshot_that_selected_no_behavior_constrains_nothing(context, tmp_path) -> None:
    """An empty behavior selection is a refusal, not an empty frozen set.

    A manifest is valid with no behavior refs as long as it selects some binding,
    and a frozen set built from *bindings* would name objects the library index
    cannot offer while claiming to constrain the run.
    """

    manifest = subject_manifest(
        context, behavior_refs=(), binding_refs=(ref(RefKind.BINDING, "binding-1"),)
    )
    assert manifest.behavior_refs == ()
    root = tmp_path / "boundaries"
    _committed(root, manifest)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context)
    assert "no selected behavior" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 19 — a boundary names the parent it actually extends
# ---------------------------------------------------------------------------


def test_a_child_declaring_another_lineage_is_refused(context, tmp_path) -> None:
    """Declaring *a* parent is not descending from *this* one.

    Everything else about this commit is in order — the contexts match, no root
    regresses, and round 17's contiguity rule lines the sequence numbers up
    exactly. Only the declared parent belongs to a different lineage, and until
    now nothing compared it to the boundary being extended. That is the fork §21
    claims to detect, and the contiguity rule made it harder to see rather than
    closing it: the numbers now agree while the identities need not.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))

    stranger = make_manifest(context, roots=make_roots(library=8, index=8, lifecycle=43))
    assert stranger.snapshot_id.value != first.snapshot_id.value

    child = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=44), parent=stranger.snapshot_id
    )
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root, child, complete_decision(child),
            transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.LINEAGE_MISMATCH
    assert "other than the boundary it extends" in excinfo.value.detail


def test_the_same_child_commits_when_it_names_the_boundary_it_extends(context, tmp_path) -> None:
    """The control. Without it, the refusal above proves only that something said no.

    Identical in every respect to the previous commit except the declared
    parent, so the refusal there is shown to follow from the lineage comparison
    and from nothing else in the fixture.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    first = make_manifest(context)
    first_boundary = commit(root, first, complete_decision(first))

    child = make_manifest(
        context, roots=make_roots(library=9, index=9, lifecycle=44), parent=first.snapshot_id
    )
    boundary = commit(
        root, child, complete_decision(child),
        transaction_id="tx-2", start=2, commit_sequence=3, parent=first_boundary,
    )
    assert boundary.parent_boundary_digest == first_boundary.atomic_boundary_id.digest_sha256
    assert child.parent_snapshot_digest == first_boundary.manifest_ref.ref_id


def test_a_genesis_commit_refuses_a_manifest_that_claims_descent(context, tmp_path) -> None:
    """The other half of the same rule.

    A manifest claiming a parent while being committed as genesis would put an
    unverifiable ancestor into the permanent record: nothing at this point can
    confirm that snapshot was ever committed, and a lineage that cannot be
    walked is not a lineage. `PARTIAL_MANIFEST` would be the wrong answer — the
    manifest omitted nothing, it named something.
    """

    root = tmp_path / "boundaries"
    ensure_directory(root)
    orphan = make_manifest(context)
    claimant = make_manifest(
        context, roots=make_roots(library=8, index=8, lifecycle=43), parent=orphan.snapshot_id
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, claimant, complete_decision(claimant))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.LINEAGE_MISMATCH
    assert "without one" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Round 19 — the roots are observed as one moment or not at all
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P0-3 — the gap between the verdict and the marker
# ---------------------------------------------------------------------------


def _no_marker(root: Path, transaction_id: str = "tx-1") -> bool:
    """No terminal marker, which is the only thing that makes a snapshot exist."""

    return not committed_transaction_exists(root, transaction_id=transaction_id)


@pytest.mark.parametrize(
    "moved",
    [
        pytest.param({"library_root": b"lib-after"}, id="library"),
        pytest.param({"index_root": b"idx-after"}, id="index"),
        pytest.param({"lifecycle_root": b"lc-after"}, id="lifecycle"),
        pytest.param({"library": 8}, id="library-generation"),
        pytest.param({"lifecycle": 43}, id="lifecycle-record-count"),
    ],
)
def test_a_root_that_moved_between_the_verdict_and_the_commit_writes_no_marker(
    context, tmp_path, moved
) -> None:
    """The whole point of the token, one root at a time.

    A completeness verdict is computed against roots read at some instant. If a
    root changes before the marker is written, the boundary would attest a world
    that no longer exists — and every value in it would validate, which is why
    validating the boundary can never catch this. Only re-reading the roots under
    the lock and comparing them against what the verdict saw does.

    The epoch is deliberately *not* what catches these. The provider simply
    starts answering differently, exactly as a store restored from a backup or
    repaired in place would, so the coordinator's count is untouched and the
    content comparison is the only thing standing there.
    """

    roots = make_roots()
    manifest = make_manifest(context, roots=roots)
    root = tmp_path / "boundaries"
    ensure_directory(root)

    answered = {"roots": roots}
    evaluator = make_evaluator(
        observed=lambda: answered["roots"], root_fence=_KNOWLEDGE_FENCE
    )
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    assert evaluated.decision.status is K.SnapshotCompletenessStatus.COMPLETE

    answered["roots"] = make_roots(**moved)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluated))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.OBSERVATION_TORN
    assert _no_marker(root), "a refused commit must leave no terminal marker"


def test_a_commit_under_another_coordinator_writes_no_marker(context, tmp_path) -> None:
    """A matching epoch is not the same world.

    Two real coordinators, both settled, both even. Everything the commit could
    compare numerically agrees; only the recorded identity separates them, and
    without it a verdict reached in one world would be committed into another.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    elsewhere = tmp_path / "elsewhere"
    ensure_directory(elsewhere)

    mine = make_evaluator(observed=lambda: manifest.roots, root_fence=_KNOWLEDGE_FENCE)
    evaluated = K.evaluate_snapshot_completeness(mine, manifest=manifest)
    stranger = make_evaluator(
        observed=lambda: manifest.roots, root_fence=fence_for(elsewhere)
    )
    assert (
        fence_for(elsewhere).coordinator_id() != _KNOWLEDGE_FENCE.coordinator_id()
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, evaluated.decision, evaluation=(stranger, evaluated))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert _no_marker(root)


def test_a_verdict_cannot_be_sealed_with_another_evaluations_observation(
    context, tmp_path
) -> None:
    """The reproduced defect, refused where it is now impossible rather than caught.

    Two evaluations of one snapshot, one coordinator, one epoch, identical roots
    and one evaluator identity. The first is COMPLETE; the second refuses because
    a reference will not resolve. Before the binding these two produced
    interchangeable tokens, and the COMPLETE verdict committed under the refusing
    evaluation's token — with a terminal marker written.

    Now the pair cannot be assembled at all: the token names the verdict it was
    taken for, and the seal checks it. That is the difference between a rule the
    commit re-checks and a rule the object cannot violate.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE

    complete = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots, root_fence=fence), manifest=manifest
    )
    refusing = K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots, root_fence=fence, resolver=lambda item: False
        ),
        manifest=manifest,
    )

    assert complete.decision.status is K.SnapshotCompletenessStatus.COMPLETE
    assert refusing.decision.status is not K.SnapshotCompletenessStatus.COMPLETE
    assert refusing.token is not None
    assert refusing.token.roots_digest == complete.token.roots_digest
    assert refusing.token.snapshot_id.value == complete.token.snapshot_id.value
    assert refusing.token.evaluator_identity.value == complete.token.evaluator_identity.value
    assert refusing.token.decision_digest != complete.token.decision_digest, (
        "the verdict is the only thing that separates them, so it is what the token names"
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K._evaluation(complete.decision, refusing.token)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert _no_marker(root)


def test_two_verdicts_over_identical_roots_and_epoch_are_not_interchangeable(
    context, tmp_path
) -> None:
    """Different `decision_id`, everything else equal — and still not each other's.

    The observation is the same observation: same epoch, same roots, same
    coordinator. Only the conclusions differ, which is precisely the case a token
    describing the *world* could never separate.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE

    first = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots, root_fence=fence), manifest=manifest
    )
    second = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots, root_fence=fence, probe=lambda item: False),
        manifest=manifest,
    )
    assert first.decision.decision_id.value != second.decision.decision_id.value
    assert first.token.epoch == second.token.epoch
    assert first.token.roots_digest == second.token.roots_digest

    for decision, token in ((first.decision, second.token), (second.decision, first.token)):
        with pytest.raises(K.KnowledgeViolation) as excinfo:
            K._evaluation(decision, token)
        assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert _no_marker(root)


def test_a_token_from_another_evaluator_configuration_is_refused(context, tmp_path) -> None:
    """Same `AuthorityIdentity`, different configuration — still a different observation.

    Two evaluators can carry one authority identity and answer differently,
    because a configuration is more than its name: different resolvers, different
    probes, different producers. The identity field alone would call these the
    same party; the verdict binding does not.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE

    mine = K.evaluate_snapshot_completeness(
        make_evaluator(observed=lambda: manifest.roots, root_fence=fence), manifest=manifest
    )
    theirs = K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots,
            root_fence=fence,
            probe=lambda item: False,
            producer="another-producer",
        ),
        manifest=manifest,
    )
    assert theirs.token.evaluator_identity.value == mine.token.evaluator_identity.value

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K._evaluation(mine.decision, theirs.token)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert _no_marker(root)


def test_a_tampered_evaluation_writes_no_marker(context, tmp_path) -> None:
    """Re-checked at the commit as well, because a sealed object still travels.

    The seal stops a mismatched pair being *built*. It cannot stop somebody
    reaching into one afterwards, so the commit validates the evaluation it is
    handed rather than trusting that it was well-formed when it was made.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE

    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=fence)
    good = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    refusing = K.evaluate_snapshot_completeness(
        make_evaluator(
            observed=lambda: manifest.roots, root_fence=fence, resolver=lambda item: False
        ),
        manifest=manifest,
    )
    object.__setattr__(good, "token", refusing.token)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, good.decision, evaluation=(evaluator, good))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.BOUNDARY_MISMATCH
    assert _no_marker(root)


def test_commit_rechecks_snapshot_entitlement_from_its_own_configuration_copies(
    context, tmp_path
) -> None:
    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    evaluator = make_evaluator(
        observed=lambda: manifest.roots,
        root_fence=_KNOWLEDGE_FENCE,
    )
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    object.__setattr__(
        evaluator.independence_proof,
        "independence_reason",
        "SELF_ASSERTED",
    )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(
            root,
            manifest,
            evaluated.decision,
            evaluation=(evaluator, evaluated),
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.EVALUATOR_NOT_INDEPENDENT
    assert _no_marker(root)


def test_a_replayed_token_writes_no_marker(context, tmp_path) -> None:
    """The same token, used again after the world has moved on.

    Replay is not substitution: the token is genuinely this snapshot's and was
    genuinely valid. What it no longer describes is the present, and the epoch is
    what says so — a later even value means a whole transaction landed after the
    verdict was reached.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE

    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=fence)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    _mutate(fence)  # somebody else's complete transaction

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluated))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.OBSERVATION_TORN
    assert _no_marker(root)


def test_a_complete_verdict_cannot_exist_without_the_observation_it_rests_on(
    context, tmp_path
) -> None:
    """The one combination the evaluation record refuses outright.

    An unavailable store still yields a verdict — §21 names that status — and it
    yields no token, because there was no coherent moment to record. Those two
    facts are consistent. ``COMPLETE`` with no token is not, and it is refused at
    the seal rather than left for the commit to notice.
    """

    manifest = make_manifest(context)

    def unavailable():
        raise RuntimeError("the store is unreachable")

    evaluated = K.evaluate_snapshot_completeness(
        make_evaluator(observed=unavailable), manifest=manifest
    )
    assert evaluated.token is None
    assert evaluated.decision.status is K.SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE
    K.validate_completeness_evaluation(evaluated)

    complete = make_evaluator(observed=lambda: manifest.roots)
    good = K.evaluate_snapshot_completeness(complete, manifest=manifest)
    assert good.decision.status is K.SnapshotCompletenessStatus.COMPLETE
    object.__setattr__(good, "token", None)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.validate_completeness_evaluation(good)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TRUSTED_OBJECT_FORGED


def test_a_forged_evaluation_writes_no_marker(context, tmp_path) -> None:
    """Filling in the fields is not the same as having made the observation."""

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=_KNOWLEDGE_FENCE)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    forged_token = object.__new__(K.RootObservationToken)
    for field in ("coordinator_id", "epoch", "roots_digest", "context", "snapshot_id",
                  "evaluator_identity", "decision_digest"):
        object.__setattr__(forged_token, field, getattr(evaluated.token, field))
    object.__setattr__(forged_token, "_trusted_seal", object())

    forged_pair = object.__new__(K.CompletenessEvaluation)
    object.__setattr__(forged_pair, "decision", evaluated.decision)
    object.__setattr__(forged_pair, "token", evaluated.token)
    object.__setattr__(forged_pair, "_trusted_seal", object())

    for evaluation, expected in (
        (forged_pair, K.KnowledgeFailureCode.TRUSTED_OBJECT_FORGED),
        (K._evaluation(evaluated.decision, evaluated.token), None),
    ):
        if expected is None:
            continue
        with pytest.raises(K.KnowledgeViolation) as excinfo:
            commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluation))
        assert excinfo.value.failure_code is expected

    # And a forged token cannot be sealed into an evaluation in the first place.
    with pytest.raises(K.KnowledgeViolation) as sealing:
        K._evaluation(evaluated.decision, forged_token)
    assert sealing.value.failure_code is K.KnowledgeFailureCode.TRUSTED_OBJECT_FORGED
    assert _no_marker(root)


def test_a_commit_that_fails_before_the_marker_leaves_no_snapshot(context, tmp_path) -> None:
    """Staged members are a crash state and never become a snapshot.

    The transaction id is consumed first, so the second attempt fails inside the
    staging step — after the directory exists and before any marker. What must
    survive that is the rule that the marker is the only thing that makes a
    snapshot exist: the staged bytes are visible on disk and open to nobody.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=_KNOWLEDGE_FENCE)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    with store_transaction(_KNOWLEDGE_FENCE) as ticket:
        stage_snapshot_transaction(
            root,
            transaction_id="tx-1",
            members={K.MANIFEST_MEMBER_NAME: manifest.canonical_bytes()},
            ticket=ticket,
        )

    fresh = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    with pytest.raises(K.KnowledgeViolation):
        commit(root, manifest, fresh.decision, evaluation=(evaluator, fresh))
    assert _no_marker(root), "staged members without a marker are not a snapshot"
    with pytest.raises(K.KnowledgeViolation) as opened:
        K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert opened.value.failure_code is K.KnowledgeFailureCode.COMMIT_MARKER_ABSENT


def test_the_comparison_and_the_write_happen_under_the_writer_lock(context, tmp_path) -> None:
    """Comparing without holding the lock only moves the window it leaves open.

    The campaign found this: deleting ``fence.exclusive()`` from the commit
    killed nothing, because a single-threaded suite cannot see a lock that is
    never contended. The attempt is made from inside — the root provider is
    called under the lock, during the re-read — by a second coordinator object
    over the same directory, which is a genuine second writer as far as the
    filesystem is concerned.
    """

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE
    seen: list[str] = []

    def observe_and_try_to_write():
        try:
            with FileSnapshotFence(fence.directory).exclusive():
                seen.append("acquired")
        except PersistenceViolation as exc:
            seen.append(exc.failure_code.value)
        return manifest.roots

    evaluator = make_evaluator(observed=observe_and_try_to_write, root_fence=fence)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)
    seen.clear()

    commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluated))

    assert seen, "the provider must actually have tried to take the writer lock"
    assert set(seen) == {PersistenceFailureCode.LOCK_BUSY.value}, (
        f"a second writer got in while the boundary was being committed: {seen}"
    )


def test_an_epoch_frame_written_outside_the_protocol_writes_no_marker(
    context, tmp_path
) -> None:
    """The residual this module has always declared, and the backstop for it.

    The coordinator's writer lock now covers every interval, so a writer using
    the protocol cannot land between the re-read and the marker — the earlier
    version of this case became impossible, which is the repair working. What the
    lock cannot cover is what the module has said from the start: it is not a
    distributed lock. A party that writes epoch frames directly, through the
    coordinator's own exempt primitive, is outside the protocol entirely.

    That is the one path left to the closing check, and it is why the check stays.
    """

    from synapse.experiments.gold.admission_journal import FENCE_EPOCH_JOURNAL_NAME
    from synapse.experiments.gold.persistence import append_coordinator_epoch_frame

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE
    reads = {"count": 0}

    def observe_then_let_an_outsider_write():
        reads["count"] += 1
        # Not on the first call: that one is the evaluation's own observation,
        # and interference there would tear it before a token ever existed.
        if reads["count"] > 1:
            epoch_path = fence.directory / FENCE_EPOCH_JOURNAL_NAME
            append_coordinator_epoch_frame(epoch_path, b"open" + b"\x00" * 16)
            append_coordinator_epoch_frame(epoch_path, b"close" + b"\x00" * 16)
        return manifest.roots

    evaluator = make_evaluator(
        observed=observe_then_let_an_outsider_write, root_fence=fence
    )
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluated))
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.OBSERVATION_TORN
    assert _no_marker(root)


def test_an_unchanged_world_commits_and_writes_its_marker(context, tmp_path) -> None:
    """The control. A check that refused everything would pass every case above."""

    manifest = make_manifest(context)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=_KNOWLEDGE_FENCE)
    evaluated = K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    boundary = commit(root, manifest, evaluated.decision, evaluation=(evaluator, evaluated))

    assert committed_transaction_exists(root, transaction_id="tx-1")
    restored = K.open_committed_snapshot_for_audit(root, transaction_id="tx-1")
    assert restored.boundary.atomic_boundary_id.value == boundary.atomic_boundary_id.value


def test_a_boundary_commit_is_one_interval_and_not_two(context, tmp_path) -> None:
    """Staging and the terminal marker belong to the same open interval.

    Written because the campaign proved nothing demanded it: splitting the
    commit into two intervals killed no test. The split is not cosmetic — it
    returns the epoch to even in the gap where the members exist and the marker
    does not, which is precisely the crash state this function is built to make
    unusable. A reader arriving in that gap would be told the store was settled.

    Two frames, so exactly two: one interval, opened and closed once.
    """

    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    root = tmp_path / "boundaries"
    ensure_directory(root)
    fence = _KNOWLEDGE_FENCE
    before = fence.current_epoch()
    assert before % 2 == 0

    boundary = commit(root, manifest, decision)

    assert fence.current_epoch() == before + 2, (
        "a boundary commit that moved the epoch by four staged its members in one "
        "interval and wrote its marker in another, leaving the store advertised as "
        "settled in between"
    )
    assert boundary.atomic_boundary_id.value


def test_a_root_observation_torn_by_a_concurrent_mutation_is_refused(context, tmp_path) -> None:
    """One call is not one instant, and this is the case that proves it.

    The three roots come from three stores. Before this round they were read
    through one callable and the result was treated as an instant, so a library
    root from before a publish could sit beside a lifecycle root from after it —
    a set describing no world that ever existed, in which every individual value
    validates. That is why validating the result cannot detect it and why the
    detection has to happen while the read is in progress.

    Here a real store mutation lands mid-read: the provider bumps the shared fence
    while it is producing the roots, exactly as a concurrent publisher would. The
    roots it returns are the ones the manifest declares, so nothing downstream has
    anything to object to — only the epoch says the observation is not of one
    moment.
    """

    fence = FileSnapshotFence(tmp_path / "torn-fence")
    manifest = make_manifest(context)

    def mutating_provider():
        # A store advancing the shared epoch is precisely what a concurrent write
        # does now that every owner is fenced.
        _mutate(fence)
        return manifest.roots

    evaluator = make_evaluator(observed=mutating_provider, root_fence=fence)
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.evaluate_snapshot_completeness(evaluator, manifest=manifest)

    assert excinfo.value.failure_code is K.KnowledgeFailureCode.OBSERVATION_TORN
    assert "while the roots were being observed" in excinfo.value.detail


def test_the_same_roots_read_without_interference_are_complete(context, tmp_path) -> None:
    """The control, and it is not optional.

    Without it the refusal above shows only that something said no. The roots,
    the manifest and the evaluator are identical; the single difference is that
    nothing mutates during the read.
    """

    fence = FileSnapshotFence(tmp_path / "quiet-fence")
    manifest = make_manifest(context)
    evaluator = make_evaluator(observed=lambda: manifest.roots, root_fence=fence)

    decision = K.evaluate_snapshot_completeness(evaluator, manifest=manifest).decision
    assert decision.status is SnapshotCompletenessStatus.COMPLETE


def test_a_torn_observation_is_not_reported_as_an_unreachable_store(context, tmp_path) -> None:
    """Two conditions, two answers, and the operator actions differ.

    An unreachable store is an outage: retry it. A torn observation means every
    store answered and the answers disagree about when — retrying is right, but
    the fault is contention rather than availability, and an operator told
    "unreachable" would go looking at the wrong thing. NR-10 forbids the
    substitution in either direction, so both statuses are asserted against the
    same fixture rather than one being assumed to imply the other.
    """

    manifest = make_manifest(context)

    def unavailable_provider():
        raise RuntimeError("the store is not reachable")

    unreachable = K.evaluate_snapshot_completeness(
        make_evaluator(observed=unavailable_provider), manifest=manifest
    ).decision
    assert unreachable.status is SnapshotCompletenessStatus.INCOMPLETE_REQUIRED_STORE

    fence = FileSnapshotFence(tmp_path / "torn")

    def mutating_provider():
        _mutate(fence)
        return manifest.roots

    # Two different shapes, not two values of one field. An unreachable store still
    # yields a signed verdict about the snapshot; a torn read yields none, because
    # the evaluation did not happen and there is nothing to sign.
    with pytest.raises(K.KnowledgeViolation) as excinfo:
        K.evaluate_snapshot_completeness(
            make_evaluator(observed=mutating_provider, root_fence=fence), manifest=manifest
        )
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.OBSERVATION_TORN


def test_an_evaluator_cannot_be_configured_without_a_root_fence() -> None:
    """An optional fence is the bypass, so the barrier lives in the signature."""

    configured = make_evaluator()
    with pytest.raises(TypeError):
        K.configure_snapshot_evaluator(
            authority_handle=configured._authority_handle,
            declaration=configured.declaration,
            actor_set=configured.actor_set,
            independence_proof=configured.independence_proof,
            trusted_clock=lambda: NOW,
            observed_roots_provider=lambda: make_roots(),
            ref_resolver=lambda item: True,
            consumability_probe=lambda item: True,
        )

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        make_evaluator(root_fence=object())
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.TYPE_MISMATCH


def test_a_committed_member_that_grew_is_corruption_and_not_an_outage(
    context, tmp_path
) -> None:
    """The finding round 20 recorded rather than fixed, now fixed.

    A member has a recorded length, so growing it trips the byte limit before any
    digest is compared and `persistence` reports `RESOURCE_LIMIT_EXCEEDED`. §21
    mapped everything it did not recognise to `STORE_UNAVAILABLE`, so an edited
    file was announced as a store that could not be reached — and the instruction
    that answer carries is "retry", which is work that cannot succeed. A wrong
    instruction is worse than a vague one.
    """

    root = tmp_path / "boundaries"
    snapshot = _committed(root, subject_manifest(context))
    member = root / "tx-frozen" / K.MANIFEST_MEMBER_NAME
    member.write_bytes(member.read_bytes() + b" ")

    with pytest.raises(K.KnowledgeViolation) as excinfo:
        _mint(root, context=context, snapshot=snapshot)
    assert excinfo.value.failure_code is K.KnowledgeFailureCode.COMMITTED_BYTES_CORRUPTED
    assert excinfo.value.failure_code is not K.KnowledgeFailureCode.STORE_UNAVAILABLE


def test_completeness_cannot_be_signed_under_a_borrowed_role(context) -> None:
    """A role that fits every decision distinguishes none of them.

    `configure_snapshot_evaluator` accepted any `AuthorityRole` and checked only
    that the evaluator was not the producer, so this suite itself ran §21
    completeness under `COMPATIBILITY_EVALUATOR` for rounds. That role's standing
    is about whether one behavior fits one consumer — decided from different
    evidence, by a party with no basis to judge whether a snapshot is whole.
    """

    for borrowed in (
        AuthorityRole.COMPATIBILITY_EVALUATOR,
        AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        AuthorityRole.GOVERNING_HUMAN,
    ):
        handle = authority_handle()
        with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
            AC.create_snapshot_evaluator_declaration(
                authority_handle=handle,
                evaluator_identity=AuthorityIdentity(value="platform-evaluator"),
                authority_role=borrowed,
                evaluator_component_id="snapshot-evaluator",
                evaluator_component_version="1.0.0",
                policy_version="policy-v1",
                trusted_clock=lambda: NOW,
            )
        assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.ROLE_NOT_DECLARED


def test_the_completeness_role_is_recorded_on_the_decision(context) -> None:
    """The control, and the part a consumer can check later.

    Refusing the wrong role at configuration time is worth little if the verdict
    does not say which role signed it — a reader restoring the decision would have
    to trust that the check ran.
    """

    manifest = make_manifest(context)
    decision = complete_decision(manifest)
    assert decision.authority_role is AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR
    assert decision.to_dict()["payload"]["authority_role"] == "SNAPSHOT_COMPLETENESS_EVALUATOR"
