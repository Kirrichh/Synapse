from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from itertools import count

import pytest

from synapse.experiments.gold import retrieval as retrieval_module
from synapse.experiments.gold.behavior import BehaviorKind
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.compatibility import (
    CompatibilityDecisionKind,
    CompatibilityDimension,
    CompatibilityFailureCode,
    CompatibilityReason,
    CompatibilityValueState,
    CompatibilityViolation,
    ConflictDecisionKind,
    ConflictKind,
    ConflictProposalDisposition,
    RevalidationOutcome,
    RevalidationStage,
    compatibility_value,
    create_compatibility_subject_evidence,
    create_conflict_evidence_proposal,
    evaluate_compatibility,
    evaluate_conflicts,
    validate_compatibility_decision,
    validate_compatibility_subject_descriptor,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    ContractFailureCode,
    ContractViolation,
    IdentityDomain,
    RepositoryRevision,
    RunId,
    compute_record_id,
)
from synapse.experiments.gold.lifecycle import LifecycleState
from synapse.experiments.gold.retrieval import (
    RANKING_PROFILE_V1,
    RETRIEVAL_POLICY_V1,
    CandidateDisposition,
    LoadOutcome,
    RetrievalFailureCode,
    RetrievalBindingTarget,
    RetrievalOutcome,
    RetrievalQuery,
    RetrievalAdmission,
    RetrievalLoadDecision,
    RetrievalViolation,
    binding_to_retrieval_target,
    configure_ranking_feature_provider,
    configure_retriever,
    create_retrieval_query,
    RetrievalEnumeration,
    candidate_subject_ref,
    enumerate_retrieval_candidates,
    enumerate_retrieval_candidates_durably,
    gate_selectable_candidates,
    consumer_context_ref_of,
    configure_durable_retrieval_persistence,
    select_and_load_durably,
    retrieval_query_from_dict,
    revalidate_loaded_before_consumption,
    validate_ranking_feature_observation,
    validate_retrieval_binding_target,
    validate_retrieval_decision,
    validate_retrieval_load_decision,
)

from tests.test_stage4_gold_compatibility import (
    NOW,
    _forged_descriptor,
    _fresh_platform_observation,
    _make_harness,
    _other_python_binding,
    _python_binding_repo,
    _recomputed_revalidation_with_observation,
    _ref,
    _shared_harness,
)
from tests.gold_frozen_candidates import frozen_for_retriever, snapshot_over


_DURABILITY_CASES = count(1)


def _durability_for(retriever):
    from synapse.experiments.gold.admission_journal import FileAdmissionJournal
    from synapse.experiments.gold.admission_store import FileAdmissionCausalStore
    from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
    from tests.gold_store_fence import fence_for

    library = getattr(retriever, "_library", retriever)
    root = library._root.parent
    fence = fence_for(root)
    case_root = root / "retrieval-durability" / str(next(_DURABILITY_CASES))
    case_root.mkdir(parents=True, exist_ok=True)
    journal = FileAdmissionJournal(case_root / "decisions.journal", fence)
    causal = FileAdmissionCausalStore(
        case_root / "causal",
        admission_history=journal,
        mutation_fence=fence,
    )
    compatibility = FileCompatibilityStore(
        case_root / "compatibility",
        mutation_fence=fence,
    )
    persistence = configure_durable_retrieval_persistence(
        compatibility_history=compatibility,
        admission_causal_history=causal,
    )
    return journal, persistence


def _durable_enumeration(*, retriever, context, query, frozen):
    journal, persistence = _durability_for(retriever)
    enumeration = enumerate_retrieval_candidates_durably(
        retriever=retriever,
        context=context,
        query=query,
        frozen=frozen,
        persistence=persistence,
    )
    return enumeration, journal, persistence





def _retrieval_entitlement():
    """The verifier's own declaration and actor set for the retrieval gate.

    Rebuilt with the same inputs the controller used rather than read off it:
    entitlement is a claim about whose copies were consulted, so consulting the
    evaluator's own object would only show a record agreeing with itself.
    """

    from synapse.experiments.gold import authority_config as AC
    from synapse.experiments.gold.contracts import (
        ActorIdentity,
        AuthorityIdentity,
        AuthorityRole,
        GateKind,
        create_stage4_authority_configuration,
        create_stage4_authority_handle,
    )

    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="gate-attester"),
        builder_actor=ActorIdentity(value="gate-builder"),
        taint_classifier_authority=AuthorityIdentity(value="gate-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="gate-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="gate-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="gate-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="gate-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="gate-authority"),
        evaluator_component_id="gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version="policy-v1",
        trusted_clock=lambda: NOW,
    )
    actors = (
        ActorIdentity(value="gate-producer"),
        ActorIdentity(value="gate-retriever"),
        ActorIdentity(value="gate-consumer"),
    )
    return {gate: (declaration, actors) for gate in GateKind}


def _gate_controller(*, admit: bool = True):
    """A §22 controller for the Patch 6 suites.

    These tests are about retrieval semantics, not admission — but that is not a
    licence to skip the gate, which is exactly the mistake the previous helper
    made. The gate runs for real with permissive probes, so the pre-existing
    assertions measure what they always measured while the barrier stays in the
    path.
    """

    from synapse.experiments.gold import admission as A
    from synapse.experiments.gold import authority_config as AC
    from synapse.experiments.gold.contracts import (
        ActorIdentity,
        AuthorityIdentity,
        AuthorityRole,
        GateKind,
        create_stage4_authority_configuration,
        create_stage4_authority_handle,
    )

    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="gate-attester"),
        builder_actor=ActorIdentity(value="gate-builder"),
        taint_classifier_authority=AuthorityIdentity(value="gate-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="gate-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="gate-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="gate-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="gate-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="gate-authority"),
        evaluator_component_id="gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version="policy-v1",
        trusted_clock=lambda: NOW,
    )
    grant = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",),
        policy_version="policy-v1",
    )
    requested = A.RequestedEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",)
    )
    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version="policy-v1",
        run_id=RunId("retrieval-gate-run"),
        attempt_id=AttemptId("retrieval-gate-attempt"),
        repository_revision="a" * 40,
        environment_profile_id="retrieval-gate-env",
        trusted_clock=lambda: NOW,
        taint_probe=lambda item: A.TaintFinding(
            consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
        ),
        provenance_probe=lambda item: True,
        lifecycle_probe=lambda item: admit,
        compatibility_probe=lambda item, ctx: A.CompatibilityFinding(
            compatible=True, evidence_complete=True, drifted=False,
            conflicts_unresolved=False, subject_ref=item, consumer_context_ref=ctx,
        ),
        boundary_probe=lambda item: True,
        grant_probe=lambda: grant,
        head_reader=lambda: {"boundary_ref": None, "heads": {}},
        producer_actor=ActorIdentity(value="gate-producer"),
        retriever_actor=ActorIdentity(value="gate-retriever"),
        consumer_actor=ActorIdentity(value="gate-consumer"),
    )
    return controller, requested



def _admission_for(enumeration, context, *, admit=True, refs=None, journal=None):
    """Run the real retrieval gate over an enumeration and return its verdict.

    The seam tests need a genuine admission rather than a hand-made one — that
    is the point of the change they are testing. ``admit=False`` drives the gate
    to a blocking verdict through a real probe rather than by handing back an
    empty tuple.
    """

    from synapse.experiments.gold import admission as A
    controller, requested = _gate_controller(admit=admit)
    subjects = enumeration.subject_refs if refs is None else refs
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subjects)
    publication = A.evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    if journal is None:
        journal, _persistence = _durability_for(context._evaluator._library)
    return gate_selectable_candidates(
        controller=controller,
        candidates=subjects,
        consumer_context_ref=consumer_context_ref_of(context),
        boundary_ref=enumeration.governing_snapshot.boundary_ref,
        frozen=enumeration.governing_snapshot,
        requested=requested,
        publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=journal,
        trusted_clock=lambda: NOW,
    )


def _retrieve_all(*, retriever, context, query, frozen=None):
    """Enumerate, run the real retrieval gate over what was found, then load.

    The gate is not skipped and its verdict is not synthesised: ingestion and
    publication are evaluated over the enumerated subject set, the retrieval
    gate decides against that exact set, and its sealed admission is what
    reaches the loader.

    ``frozen`` defaults to a real committed §21 snapshot over everything the
    library currently holds, so these tests keep considering the candidates they
    always considered while the enumeration is genuinely constrained by a
    boundary. A test that wants the snapshot and the library to disagree passes
    its own.
    """

    from synapse.experiments.gold import admission as A

    if frozen is None:
        frozen = frozen_for_retriever(retriever)
    journal, persistence = _durability_for(retriever)
    enumeration = enumerate_retrieval_candidates_durably(
        retriever=retriever, context=context, query=query, frozen=frozen,
        persistence=persistence,
    )
    controller, requested = _gate_controller()
    subjects = enumeration.subject_refs
    if not subjects:
        return select_and_load_durably(
            retriever=retriever, context=context, query=query,
            enumeration=enumeration,
            admission=gate_selectable_candidates(
                controller=controller, candidates=(),
                consumer_context_ref=consumer_context_ref_of(context),
                boundary_ref=frozen.boundary_ref,
                frozen=frozen,
                requested=requested, publication_decision=None,
                entitlements=_retrieval_entitlement(),
                journal=journal, trusted_clock=lambda: NOW,
            ),
            persistence=persistence,
        )
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subjects)
    publication = A.evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    admission = gate_selectable_candidates(
        controller=controller,
        candidates=subjects,
        consumer_context_ref=consumer_context_ref_of(context),
        boundary_ref=frozen.boundary_ref,
        frozen=frozen,
        requested=requested,
        publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=journal, trusted_clock=lambda: NOW,
    )
    return select_and_load_durably(
        retriever=retriever, context=context, query=query,
        enumeration=enumeration, admission=admission,
        persistence=persistence,
    )

def _recomputed_load_with_revalidation(
    load: RetrievalLoadDecision,
    revalidation,
) -> RetrievalLoadDecision:
    result = object.__new__(RetrievalLoadDecision)
    for name, value in vars(load).items():
        object.__setattr__(result, name, value)
    object.__setattr__(result, "_revalidation", revalidation)
    object.__setattr__(
        result,
        "before_loading_revalidation_id",
        revalidation.revalidation_id,
    )
    payload = retrieval_module._canonical(retrieval_module._load_payload(result))
    object.__setattr__(
        result,
        "load_decision_id",
        compute_record_id(
            domain=IdentityDomain.RETRIEVAL_LOAD_DECISION,
            canonical_bytes=payload,
        ),
    )
    return result


def _configured_retriever(
    harness,
    *,
    scorer,
    descriptor_resolver=None,
    score_input_resolver=None,
    conflict_proposal_resolver=None,
    required_binding_targets=(),
    selected_set_limit=1,
):
    provider = configure_ranking_feature_provider(
        component_id="semantic-score-provider",
        component_version="synapse.stage4.semantic-score-provider/v1",
        scoring_profile=RANKING_PROFILE_V1,
        scorer=scorer,
        input_ref_resolver=(
            score_input_resolver
            if score_input_resolver is not None
            else lambda query_id, descriptor_id: _ref(
                f"score-{query_id.value[-8:]}-{descriptor_id.value[-8:]}",
                RefKind.ARTIFACT,
            )
        ),
        actor_identity=harness.evaluator.score_provider_actor,
    )
    descriptor_by_key = {harness.entry.content_key: harness.descriptor}
    descriptor_by_key.update({item[3].content_key: item[7] for item in harness.extra_candidates})
    resolver = descriptor_resolver if descriptor_resolver is not None else lambda entry: descriptor_by_key[entry.content_key]
    retriever = configure_retriever(
        authority_handle=harness.handle,
        evaluator=harness.evaluator,
        evaluator_declaration=harness.declaration,
        retrieval_policy=RETRIEVAL_POLICY_V1,
        trusted_clock=lambda: NOW,
        descriptor_resolver=resolver,
        conflict_proposal_resolver=conflict_proposal_resolver or (lambda context, decisions, descriptors: ()),
        ranking_provider=provider,
        library=harness.library,
    )
    query = create_retrieval_query(
        retriever=retriever,
        context=harness.context,
        requested_behavior_kinds=(harness.unit.core.behavior_kind,),
        required_binding_targets=required_binding_targets,
        selected_set_limit=selected_set_limit,
    )
    return retriever, provider, query


def _durable_retrieval_persistence(harness, *, compatibility_port=None, causal_port=None):
    from synapse.experiments.gold.admission_journal import FileAdmissionJournal
    from synapse.experiments.gold.admission_store import FileAdmissionCausalStore
    from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
    from tests.gold_store_fence import fence_for

    fence = fence_for(harness.root)
    journal = FileAdmissionJournal(
        harness.root / "retrieval-admission" / "decisions.journal", fence
    )
    compatibility = compatibility_port or FileCompatibilityStore(
        harness.root / "retrieval-compatibility", mutation_fence=fence
    )
    causal = causal_port or FileAdmissionCausalStore(
        harness.root / "retrieval-causal",
        mutation_fence=fence,
        admission_history=journal,
    )
    persistence = retrieval_module.configure_durable_retrieval_persistence(
        compatibility_history=compatibility,
        admission_causal_history=causal,
    )
    return persistence, compatibility, causal


def _durable_frozen_for(retriever, root: Path):
    from synapse.experiments.gold import gate_findings as GF
    from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

    case = production_point_of_use_case(root / "boundary")
    frozen = GF.frozen_candidates_from_snapshot(
        knowledge_store=case.knowledge_store,
        attempt_id=case.snapshot_attempt_id,
        expected_context=case.knowledge_store.open_current().manifest.context,
        frozen_at_utc=NOW,
        evaluator_declaration=case.snapshot_evaluator_declaration,
        evaluator_actor_set=case.snapshot_actor_set,
        evaluator_independence_proof=case.snapshot_independence_proof,
    )
    expected = {
        retrieval_module._ref_key(retrieval_module.index_entry_subject_ref(item))
        for item in retriever._library.search_index()
    }
    assert set(frozen.subject_ref_keys) == expected
    return frozen


def test_durable_retrieval_commits_every_predecessor_before_ranking_and_loading(
    tmp_path: Path, monkeypatch,
) -> None:
    harness = _make_harness(tmp_path)
    persistence, compatibility, causal = _durable_retrieval_persistence(harness)
    score_observations: list[int] = []

    def scorer(query_id, descriptor_id, score_input):
        score_observations.append(compatibility.current_sequence())
        return 1_000_000

    retriever, _, query = _configured_retriever(harness, scorer=scorer)
    enumeration = retrieval_module.enumerate_retrieval_candidates_durably(
        retriever=retriever,
        context=harness.context,
        query=query,
        frozen=_durable_frozen_for(retriever, tmp_path),
        persistence=persistence,
    )
    assert score_observations == [4], (
        "context, evidence, decision and conflict scan must precede ranking"
    )
    admission = _admission_for(
        enumeration, harness.context, journal=causal._admission_history
    )
    original_get = harness.library.get_verified_behavior
    reads: list[tuple[int, int]] = []

    def guarded_get(*args, **kwargs):
        reads.append((compatibility.current_sequence(), causal.current_sequence()))
        assert compatibility.current_sequence() == 5
        assert causal.current_sequence() == 1
        return original_get(*args, **kwargs)

    monkeypatch.setattr(harness.library, "get_verified_behavior", guarded_get)
    result = retrieval_module.select_and_load_durably(
        retriever=retriever,
        context=harness.context,
        query=query,
        enumeration=enumeration,
        admission=admission,
        persistence=persistence,
    )
    assert reads == [(5, 1)]
    assert result.load_decisions[0].outcome is LoadOutcome.VERIFIED_LOADED
    causal_ref = retrieval_module.retrieval_causal_record_ref(result.causal_record)
    assert causal.contains_ref(causal_ref)
    assert causal.resolve_ref(causal_ref) == result.causal_record.canonical_bytes()
    assert (
        result.causal_record.retrieval_decision_ref.to_dict()
        == retrieval_module.retrieval_decision_ref(result.decision).to_dict()
    )
    assert result.causal_record.retrieval_gate_decision_ref.ref_id == (
        admission.decision.gate_decision_id.digest_sha256
    )
    original_gate_ref = result.causal_record.retrieval_gate_decision_ref
    object.__setattr__(
        result.causal_record,
        "retrieval_gate_decision_ref",
        HashBoundRef(
            kind=original_gate_ref.kind,
            ref_id="forged-retrieval-gate-decision",
            schema_id=original_gate_ref.schema_id,
            sha256=original_gate_ref.sha256,
            byte_length=original_gate_ref.byte_length,
            media_type=original_gate_ref.media_type,
        ),
    )
    with pytest.raises(RetrievalViolation) as caught:
        retrieval_module.validate_retrieval_causal_record(result.causal_record)
    assert caught.value.failure_code is RetrievalFailureCode.TRUSTED_RECORD_FORGED


def test_stage_two_durability_failure_blocks_the_behavior_blob_read(
    tmp_path: Path, monkeypatch,
) -> None:
    from synapse.experiments.gold.compatibility import CompatibilityRevalidationRecord

    harness = _make_harness(tmp_path)
    persistence, compatibility, causal = _durable_retrieval_persistence(harness)

    class FailStageTwo:
        mutation_fence = compatibility.mutation_fence

        def current_anchor(self):
            return compatibility.current_anchor()

        def contains_ref(self, item):
            return compatibility.contains_ref(item)

        def append_record(self, record, *, expected_parent_anchor, ticket=None):
            if type(record) is CompatibilityRevalidationRecord:
                raise OSError("simulated Stage 2 durability outage")
            return compatibility.append_record(
                record, expected_parent_anchor=expected_parent_anchor, ticket=ticket
            )

    persistence = retrieval_module.configure_durable_retrieval_persistence(
        compatibility_history=FailStageTwo(), admission_causal_history=causal
    )
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 1_000_000
    )
    enumeration = retrieval_module.enumerate_retrieval_candidates_durably(
        retriever=retriever,
        context=harness.context,
        query=query,
        frozen=_durable_frozen_for(retriever, tmp_path),
        persistence=persistence,
    )
    admission = _admission_for(
        enumeration, harness.context, journal=causal._admission_history
    )
    monkeypatch.setattr(
        harness.library,
        "get_verified_behavior",
        lambda *args, **kwargs: pytest.fail("blob read happened before durable Stage 2"),
    )
    with pytest.raises(RetrievalViolation) as caught:
        retrieval_module.select_and_load_durably(
            retriever=retriever,
            context=harness.context,
            query=query,
            enumeration=enumeration,
            admission=admission,
            persistence=persistence,
        )
    assert caught.value.failure_code is RetrievalFailureCode.DURABILITY_UNAVAILABLE
    assert compatibility.current_sequence() == 4
    assert causal.current_sequence() == 1


def test_causal_decision_durability_failure_blocks_stage_two_and_loading(
    tmp_path: Path, monkeypatch,
) -> None:
    harness = _make_harness(tmp_path)
    persistence, compatibility, causal = _durable_retrieval_persistence(harness)

    class FailCausal:
        mutation_fence = causal.mutation_fence

        def current_anchor(self):
            return causal.current_anchor()

        def current_sequence(self):
            return causal.current_sequence()

        def contains_ref(self, item):
            return False

        def append_retrieval_decision(self, **kwargs):
            raise OSError("simulated causal durability outage")

    persistence = retrieval_module.configure_durable_retrieval_persistence(
        compatibility_history=compatibility, admission_causal_history=FailCausal()
    )
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 1_000_000
    )
    enumeration = retrieval_module.enumerate_retrieval_candidates_durably(
        retriever=retriever,
        context=harness.context,
        query=query,
        frozen=_durable_frozen_for(retriever, tmp_path),
        persistence=persistence,
    )
    admission = _admission_for(
        enumeration, harness.context, journal=causal._admission_history
    )
    monkeypatch.setattr(
        harness.library,
        "get_verified_behavior",
        lambda *args, **kwargs: pytest.fail("blob read happened without a causal decision"),
    )
    with pytest.raises(RetrievalViolation) as caught:
        retrieval_module.select_and_load_durably(
            retriever=retriever,
            context=harness.context,
            query=query,
            enumeration=enumeration,
            admission=admission,
            persistence=persistence,
        )
    assert caught.value.failure_code is RetrievalFailureCode.DURABILITY_UNAVAILABLE
    assert compatibility.current_sequence() == 4
    assert causal.current_sequence() == 0


def test_durable_loading_refuses_a_raw_unpersisted_enumeration(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    persistence, _, causal = _durable_retrieval_persistence(harness)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 1_000_000
    )
    enumeration = enumerate_retrieval_candidates(
        retriever=retriever,
        context=harness.context,
        query=query,
        frozen=_durable_frozen_for(retriever, tmp_path),
    )
    admission = _admission_for(
        enumeration, harness.context, journal=causal._admission_history
    )
    with pytest.raises(RetrievalViolation) as caught:
        retrieval_module.select_and_load_durably(
            retriever=retriever,
            context=harness.context,
            query=query,
            enumeration=enumeration,
            admission=admission,
            persistence=persistence,
        )
    assert caught.value.failure_code is RetrievalFailureCode.DURABILITY_REQUIRED
    assert causal.current_sequence() == 0


@pytest.fixture
def _revoked_harness(tmp_path_factory):
    return _make_harness(tmp_path_factory.mktemp("stage4-patch6-shared-revoked"), revoked=True)


@pytest.fixture
def _unresolved_harness(tmp_path_factory):
    return _make_harness(
        tmp_path_factory.mktemp("stage4-patch6-shared-unresolved"),
        extra_unresolved=2,
    )


def test_s4_p6_acc_retrieval_01_compatibility_precedes_score_provider_and_ranking(_revoked_harness) -> None:
    harness = _revoked_harness
    score_calls: list[str] = []

    def scorer(query_id, descriptor_id, score_input):
        score_calls.append(descriptor_id.value)
        return 1_000_000

    retriever, _, query = _configured_retriever(harness, scorer=scorer)
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert harness.decision.decision_kind is CompatibilityDecisionKind.REVOKED
    assert score_calls == []
    assert len(result.decision.considered_candidates) == 1
    assert result.decision.considered_candidates[0].compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert result.decision.considered_candidates[0].disposition is CandidateDisposition.REJECTED
    assert result.decision.selected_candidate_ids == ()


def test_s4_p6_acc_retrieval_02_all_considered_candidates_and_rejections_remain_in_audit(_unresolved_harness) -> None:
    harness = _unresolved_harness
    entries = harness.library.search_index()
    known_key = harness.entry.content_key
    score_calls: list[str] = []

    def descriptor_resolver(entry):
        if entry.content_key == known_key:
            return harness.descriptor
        raise RetrievalViolation(RetrievalFailureCode.DESCRIPTOR_MISSING, "descriptor absent from catalog")

    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: score_calls.append(descriptor_id.value) or 500_000,
        descriptor_resolver=descriptor_resolver,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert len(entries) == 3
    assert query.selected_set_limit == 1
    assert len(result.decision.considered_candidates) == 3
    assert result.decision.selected_candidate_ids == ()
    assert result.decision.outcome is RetrievalOutcome.CONFLICT_BLOCKED
    assert score_calls == []
    unavailable = tuple(
        item
        for item in result.decision.considered_candidates
        if item.disposition is CandidateDisposition.DESCRIPTOR_UNAVAILABLE
    )
    assert len(unavailable) == 2
    assert unavailable[0].failure_code is RetrievalFailureCode.DESCRIPTOR_MISSING
    assert unavailable[1].failure_code is RetrievalFailureCode.DESCRIPTOR_MISSING


def test_s4_p6_acc_retrieval_03_semantic_score_never_grants_eligibility(_revoked_harness) -> None:
    harness = _revoked_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1_000_000,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    candidate = result.decision.considered_candidates[0]
    assert candidate.compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert candidate.ranking_feature_id is None
    assert candidate.ranking_key is None
    assert candidate.disposition is CandidateDisposition.REJECTED
    assert result.decision.selected_candidate_ids == ()


def test_s4_p6_acc_retrieval_04_revoked_candidate_is_never_selected(_revoked_harness) -> None:
    harness = _revoked_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1_000_000,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert result.decision.outcome is RetrievalOutcome.NO_CANDIDATES
    assert result.decision.considered_candidates[0].compatibility_kind is CompatibilityDecisionKind.REVOKED
    assert result.decision.selected_candidate_ids == ()
    assert result.load_decisions == ()


def test_s4_p6_acc_retrieval_05_identity_bound_scores_reproduce_order_and_conflicting_scores_fail_closed(_shared_harness) -> None:
    harness = _shared_harness
    observed_scores = [700_000, 700_000, 700_001]

    def scorer(query_id, descriptor_id, score_input):
        return observed_scores.pop(0)

    retriever, provider, query = _configured_retriever(harness, scorer=scorer)
    first = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    second = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    first_feature = first.decision.ranking_feature_observations[0]
    second_feature = second.decision.ranking_feature_observations[0]
    assert first_feature.semantic_score_micros == 700_000
    assert first_feature.observation_id == second_feature.observation_id
    assert first.decision.considered_candidates[0].ranking_key == second.decision.considered_candidates[0].ranking_key
    validate_ranking_feature_observation(first_feature, provider=provider)
    with pytest.raises(RetrievalViolation) as exc:
        _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert exc.value.failure_code is RetrievalFailureCode.RANKING_INPUT_INCONSISTENT


@pytest.mark.parametrize("score", [True, -1, 1_000_001, 0.5, "100"])
def test_s4_p6_acc_retrieval_score_01_exact_bounded_integer_only(_shared_harness, score: object) -> None:
    harness = _shared_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: score,
    )
    with pytest.raises(RetrievalViolation) as exc:
        _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert exc.value.failure_code is RetrievalFailureCode.RANKING_INPUT_MALFORMED


def test_s4_p6_acc_retrieval_loading_01_stage2_precedes_verified_load_and_stage3_executes_nothing(_shared_harness) -> None:
    harness = _shared_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 400_000,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert result.decision.outcome is RetrievalOutcome.SELECTED
    assert len(result.load_decisions) == 1
    load = result.load_decisions[0]
    assert load.outcome is LoadOutcome.VERIFIED_LOADED
    assert load._revalidation.outcome is RevalidationOutcome.PASSED
    assert harness.observation_provider.calls == 1
    assert load.loaded_content_key == harness.descriptor.content_key.value
    assert load.loaded_manifest_id == harness.descriptor.manifest_id.value
    stage3 = revalidate_loaded_before_consumption(
        retriever=retriever,
        context=harness.context,
        retrieval_decision=result.decision,
        load_decision=load,
    )
    assert stage3.prior_revalidation_id == load.before_loading_revalidation_id
    assert stage3.outcome is RevalidationOutcome.PASSED
    assert stage3.failure_code is None
    assert stage3.cause_code is None
    assert harness.observation_provider.calls == 2


def test_s4_p6_acc_retrieval_loading_02_publication_during_score_blocks_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _make_harness(tmp_path)
    verified_load_calls: list[tuple[object, object]] = []
    original_load = harness.library.get_verified_behavior

    def observed_load(content_key, manifest_id):
        verified_load_calls.append((content_key, manifest_id))
        return original_load(content_key, manifest_id)

    monkeypatch.setattr(harness.library, "get_verified_behavior", observed_load)

    def scorer(query_id, descriptor_id, score_input):
        harness.publish_extra("parallel-publication")
        return 900_000

    retriever, _, query = _configured_retriever(harness, scorer=scorer)
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert len(result.decision.selected_candidate_ids) == 1
    assert len(result.load_decisions) == 1
    blocked = result.load_decisions[0]
    assert blocked.outcome is LoadOutcome.REVALIDATION_BLOCKED
    assert blocked._revalidation.outcome is RevalidationOutcome.FAILED
    assert blocked.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert blocked.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
    assert blocked.loaded_content_key is None
    assert blocked.loaded_manifest_id is None
    assert blocked.pre_load_snapshot_sha256 is None
    assert blocked.post_load_snapshot_sha256 is None
    assert verified_load_calls == []


def test_s4_p6_corrective_context_chain_02_recomputed_load_identity_cannot_hide_observation_substitution(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    result = _retrieve_all(
        retriever=retriever,
        context=harness.context,
        query=query,
    )
    load = result.load_decisions[0]
    assert load.outcome is LoadOutcome.VERIFIED_LOADED
    assert load._revalidation.outcome is RevalidationOutcome.PASSED
    assert load._revalidation._context is harness.context
    validate_retrieval_load_decision(load)

    different_observation = _fresh_platform_observation(
        harness,
        environment_version="synapse.stage4.environment/v999",
    )
    forged_revalidation = _recomputed_revalidation_with_observation(
        load._revalidation,
        different_observation,
    )
    assert forged_revalidation._context is harness.context
    assert forged_revalidation.observation_sha256 != harness.context.observation_sha256
    assert forged_revalidation.revalidation_id != load.before_loading_revalidation_id
    forged_load = _recomputed_load_with_revalidation(load, forged_revalidation)
    assert forged_load.before_loading_revalidation_id == forged_revalidation.revalidation_id
    assert forged_load.load_decision_id != load.load_decision_id

    with pytest.raises(CompatibilityViolation) as direct:
        validate_retrieval_load_decision(forged_load)
    assert direct.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH

    calls_before = harness.observation_provider.calls
    with pytest.raises(CompatibilityViolation) as consumption:
        revalidate_loaded_before_consumption(
            retriever=retriever,
            context=harness.context,
            retrieval_decision=result.decision,
            load_decision=forged_load,
        )
    assert consumption.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH
    assert harness.observation_provider.calls == calls_before


def test_s4_p6_corrective_context_chain_03_blocked_load_preserves_different_fresh_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _make_harness(tmp_path)
    verified_load_calls: list[tuple[object, object]] = []
    original_load = harness.library.get_verified_behavior

    def observed_load(content_key, manifest_id):
        verified_load_calls.append((content_key, manifest_id))
        return original_load(content_key, manifest_id)

    monkeypatch.setattr(harness.library, "get_verified_behavior", observed_load)
    fresh_observation = _fresh_platform_observation(
        harness,
        tool_version="synapse.stage4.compiler/v999",
    )
    harness.observation_provider.observation = fresh_observation
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )

    result = _retrieve_all(
        retriever=retriever,
        context=harness.context,
        query=query,
    )
    assert len(result.load_decisions) == 1
    blocked = result.load_decisions[0]
    assert blocked.outcome is LoadOutcome.REVALIDATION_BLOCKED
    assert blocked._revalidation.outcome is RevalidationOutcome.FAILED
    assert blocked._revalidation._context is harness.context
    assert blocked._revalidation._observation is fresh_observation
    assert blocked._revalidation.observation_sha256 is not None
    assert blocked._revalidation.observation_sha256 != harness.context.observation_sha256
    assert blocked.failure_code is blocked._revalidation.failure_code
    assert blocked.cause_code is blocked._revalidation.cause_code
    assert blocked.loaded_content_key is None
    assert blocked.loaded_manifest_id is None
    assert blocked.pre_load_snapshot_sha256 is None
    assert blocked.post_load_snapshot_sha256 is None
    assert verified_load_calls == []
    validate_retrieval_load_decision(blocked)


def test_s4_p6_corrective_consumption_01_failed_stage3_is_returned_as_typed_chain_evidence(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    load = result.load_decisions[0]
    assert load.outcome is LoadOutcome.VERIFIED_LOADED
    harness.publish_extra("consumption-drift")

    stage3 = revalidate_loaded_before_consumption(
        retriever=retriever,
        context=harness.context,
        retrieval_decision=result.decision,
        load_decision=load,
    )
    assert stage3.stage is RevalidationStage.BEFORE_CONSUMPTION
    assert stage3.prior_revalidation_id == load.before_loading_revalidation_id
    assert stage3.outcome is RevalidationOutcome.FAILED
    assert stage3.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
    assert stage3.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
    assert stage3.observation_sha256 == harness.context.observation_sha256


def test_s4_p6_acc_retrieval_query_01_strict_context_bound_transport_and_sealed_records(_shared_harness) -> None:
    harness = _shared_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 1,
    )
    assert retrieval_query_from_dict(query.to_dict(), retriever=retriever, context=harness.context).to_dict() == query.to_dict()
    altered = query.to_dict()
    altered["selected_set_limit"] = 2
    with pytest.raises(RetrievalViolation):
        retrieval_query_from_dict(altered, retriever=retriever, context=harness.context)
    with pytest.raises(TypeError):
        RetrievalQuery()
    with pytest.raises((TypeError, ValueError)):
        replace(query, selected_set_limit=0)


def test_s4_p6_acc_retrieval_audit_01_decision_binds_conflict_and_complete_candidate_records(_shared_harness) -> None:
    harness = _shared_harness
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 123_456,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    validate_retrieval_decision(
        result.decision,
        retriever=retriever,
        query=query,
        context=harness.context,
    )
    assert len(result.decision.considered_candidates) == len(harness.library.search_index())
    assert len(result.decision.conflict_records) == 1
    assert result.decision.conflict_records[0].compatibility_scan_id == result.decision.conflict_scan_id
    assert result.decision.ranking_feature_observations[0].observation_id == result.decision.considered_candidates[0].ranking_feature_id


_RETRIEVAL_CASE_FIXTURE = Path(__file__).parent / "fixtures" / "gold" / "retrieval_cases_v1.json"
_RETRIEVAL_CASE_DATA = json.loads(_RETRIEVAL_CASE_FIXTURE.read_text(encoding="utf-8"))
_RETRIEVAL_CASES = tuple(_RETRIEVAL_CASE_DATA["cases"])


def _subject_evidence(harness, *, attestation, bindings):
    return create_compatibility_subject_evidence(
        descriptor=harness.descriptor,
        unit=harness.unit,
        blob=harness.blob,
        manifest=harness.manifest,
        index_entry=harness.entry,
        attestation=attestation,
        bindings=bindings,
        taint_root_basis=harness.taint_profile,
        taint_source_profiles=(),
        taint_derivations=(),
        taint_decisions=(),
        lifecycle_record=harness.lifecycle_record,
        lifecycle_snapshot=harness.lifecycle_snapshot,
        lifecycle_context=harness.lifecycle_context,
        taint_history_anchor=harness.taint_store.current_anchor(),
    )


def _decision_case_result(decision, expected: dict[str, object]) -> dict[str, object]:
    dimension_name = expected["dimension"]
    reason = None
    if dimension_name is not None:
        dimension = next(
            item
            for item in decision.evidence.dimensions
            if item.dimension is CompatibilityDimension(dimension_name)
        )
        reason = dimension.reason.value
    return {
        "compatibility_decision": decision.decision_kind.value,
        "dimension": dimension_name,
        "reason": reason,
        "candidate_disposition": None,
        "conflict_result": None,
        "selected": False,
        "failure": None,
    }


@pytest.fixture
def _literal_harness_factory(tmp_path: Path):
    cache = {}

    def get_harness(key: str):
        if key in cache:
            return cache[key]

        scenario_root = tmp_path / key
        if key == "default":
            value = _make_harness(tmp_path / "default")
        elif key == "repository-mismatch":
            value = _make_harness(
                tmp_path / "repository-mismatch",
                context_repository_revision=RepositoryRevision.git_commit("3" * 40),
            )
        elif key == "policy-mismatch":
            value = _make_harness(
                tmp_path / "policy-mismatch",
                context_policy_version="synapse.stage4.gold.alternate-policy/v1",
            )
        elif key == "environment-mismatch":
            value = _make_harness(
                tmp_path / "environment-mismatch",
                context_environment_version="synapse.stage4.environment/v999",
            )
        elif key == "host-mismatch":
            value = _make_harness(
                tmp_path / "host-mismatch",
                context_host_version="synapse.stage4.host-abi/v999",
            )
        elif key == "tool-mismatch":
            value = _make_harness(
                tmp_path / "tool-mismatch",
                context_tool_version="synapse.other-compiler/v9",
            )
        elif key == "oracle-mismatch":
            value = _make_harness(
                tmp_path / "oracle-mismatch",
                context_oracle_ref_name="alternate-oracle-result",
            )
        elif key == "binding":
            scenario_root.mkdir()
            binding_repo, binding = _python_binding_repo(scenario_root)
            value = (
                _make_harness(
                    scenario_root / "binding-harness",
                    bindings=(binding,),
                    binding_repo_root=binding_repo,
                ),
                binding_repo,
                binding,
            )
        elif key == "typed-absence":
            value = _make_harness(
                tmp_path / "typed-absence",
                with_compiler_binding=False,
            )
        elif key == "STALE":
            value = _make_harness(
                tmp_path / "STALE",
                lifecycle_state=LifecycleState.STALE,
            )
        elif key == "REVOKED":
            value = _make_harness(
                tmp_path / "REVOKED",
                lifecycle_state=LifecycleState.REVOKED,
            )
        elif key == "SUPERSEDED":
            value = _make_harness(
                tmp_path / "SUPERSEDED",
                lifecycle_state=LifecycleState.SUPERSEDED,
            )
        elif key == "QUARANTINED":
            value = _make_harness(
                tmp_path / "QUARANTINED",
                lifecycle_state=LifecycleState.QUARANTINED,
            )
        elif key == "unresolved":
            value = _make_harness(
                tmp_path / "unresolved",
                extra_unresolved=2,
            )
        elif key == "resolved":
            value = _make_harness(
                tmp_path / "resolved",
                extra_resolved=1,
            )
        else:
            raise AssertionError(f"unknown literal harness key: {key}")

        cache[key] = value
        return value

    return get_harness


def _retrieval_case_result(result, expected: dict[str, object]) -> dict[str, object]:
    expected_disposition = expected["candidate_disposition"]
    audit = None
    if expected_disposition is not None:
        audit = next(
            item
            for item in result.decision.considered_candidates
            if item.disposition.value == expected_disposition
        )
    return {
        "compatibility_decision": (
            expected["compatibility_decision"]
            if audit is None
            else None if audit.compatibility_kind is None else audit.compatibility_kind.value
        ),
        "dimension": expected["dimension"],
        "reason": expected["reason"],
        "candidate_disposition": None if audit is None else audit.disposition.value,
        "conflict_result": result.decision.conflict_records[0].decision_kind.value,
        "selected": bool(result.decision.selected_candidate_ids),
        "failure": None if audit is None or audit.failure_code is None else audit.failure_code.value,
    }


def _execute_literal_retrieval_case(
    case: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    literal_harness_factory,
) -> dict[str, object]:
    delta = case["delta"]
    expected = case["expected"]
    assert type(delta) is dict
    assert type(expected) is dict
    scenario = delta["scenario"]

    if scenario == "fully-compatible":
        harness = literal_harness_factory("default")
        retriever, _, query = _configured_retriever(harness, scorer=lambda *args: 500_000)
        return _retrieval_case_result(
            _retrieve_all(retriever=retriever, context=harness.context, query=query),
            expected,
        )
    if scenario in {"repository-mismatch", "policy-mismatch", "host-mismatch", "environment-mismatch", "tool-mismatch", "oracle-mismatch"}:
        harness = literal_harness_factory(scenario)
        return _decision_case_result(harness.decision, expected)
    if scenario in {"binding-absent", "binding-invalid"}:
        harness, repo, binding = literal_harness_factory("binding")
        evidence_bindings = () if scenario == "binding-absent" else (
            _other_python_binding(repo, binding.repository_revision),
        )
        catalog_key = harness.descriptor.descriptor_id.value
        original_evidence = harness.catalog[catalog_key]
        try:
            harness.catalog[catalog_key] = _subject_evidence(
                harness,
                attestation=harness.attestation,
                bindings=evidence_bindings,
            )
            decision = evaluate_compatibility(
                evaluator=harness.evaluator,
                context=harness.context,
                descriptor=harness.descriptor,
                index_entry=harness.entry,
            )
        finally:
            harness.catalog[catalog_key] = original_evidence
        return _decision_case_result(decision, expected)
    if scenario in {"program-substitution", "descriptor-revision-substitution"}:
        harness = literal_harness_factory("default")
        forged = _forged_descriptor(harness.descriptor)
        if scenario == "program-substitution":
            object.__setattr__(forged, "program_sha256", delta["program_sha256"])
        else:
            object.__setattr__(
                forged,
                "repository_revision",
                RepositoryRevision.git_commit(delta["repository_revision"]),
            )
        with pytest.raises(CompatibilityViolation) as exc:
            validate_compatibility_subject_descriptor(forged)
        return {
            "compatibility_decision": None,
            "dimension": expected["dimension"],
            "reason": None,
            "candidate_disposition": None,
            "conflict_result": None,
            "selected": False,
            "failure": exc.value.failure_code.value,
        }
    if scenario == "missing-evidence":
        harness = literal_harness_factory("default")
        catalog_key = harness.descriptor.descriptor_id.value
        original_evidence = harness.catalog[catalog_key]
        try:
            harness.catalog[catalog_key] = _subject_evidence(
                harness,
                attestation=None,
                bindings=(),
            )
            decision = evaluate_compatibility(
                evaluator=harness.evaluator,
                context=harness.context,
                descriptor=harness.descriptor,
                index_entry=harness.entry,
            )
        finally:
            harness.catalog[catalog_key] = original_evidence
        return _decision_case_result(decision, expected)
    if scenario == "typed-absence":
        observed = compatibility_value(label="fixture-literal", exact_value=delta["literal_value"])
        assert observed.state.value == delta["literal_state"]
        harness = literal_harness_factory("typed-absence")
        compiler_dimension = next(
            item
            for item in harness.decision.evidence.dimensions
            if item.dimension is CompatibilityDimension.CANONICALIZATION_AND_COMPILER
        )
        assert compiler_dimension.producer_value.state is CompatibilityValueState.MISSING
        return _decision_case_result(harness.decision, expected)
    if scenario == "lifecycle":
        harness = literal_harness_factory(delta["state"])
        return _decision_case_result(harness.decision, expected)
    if scenario in {"poisoned-index", "descriptor-unavailable", "incomplete-conflict"}:
        harness = literal_harness_factory("unresolved")
        if scenario == "poisoned-index":
            resolver = lambda entry: harness.descriptor
        else:
            resolver = lambda entry: harness.descriptor if entry.content_key == harness.entry.content_key else (_ for _ in ()).throw(
                RetrievalViolation(RetrievalFailureCode.DESCRIPTOR_MISSING, "literal descriptor absence")
            )
        retriever, _, query = _configured_retriever(
            harness,
            scorer=lambda *args: 500_000,
            descriptor_resolver=resolver,
        )
        return _retrieval_case_result(
            _retrieve_all(retriever=retriever, context=harness.context, query=query),
            expected,
        )
    if scenario in {"proposal-create", "proposal-suppress"}:
        harness = literal_harness_factory("resolved")
        harness.conflict_matrix.clear()
        other = harness.extra_candidates[0][7]
        key = tuple(sorted((harness.descriptor.descriptor_id.value, other.descriptor_id.value)))
        proposal = create_conflict_evidence_proposal(
            conflict_kind=ConflictKind.CONTRADICTORY_EVIDENCE,
            proposer_actor=ActorIdentity("fixture-conflict-proposer"),
            left_descriptor_id=harness.descriptor.descriptor_id,
            right_descriptor_id=other.descriptor_id,
            scope=("src/a.py",),
            binding_targets=("pkg.symbol",),
            evidence_refs=(_ref("fixture-proposal", RefKind.SOURCE_EVIDENCE),),
        )
        try:
            if scenario == "proposal-suppress":
                harness.conflict_matrix[key] = (
                    ConflictKind(delta["authoritative_kind"]),
                    (_ref("fixture-authoritative-conflict", RefKind.SOURCE_EVIDENCE),),
                )
            proposals = (proposal,) if delta.get("proposal_present", True) else ()
            retriever, _, query = _configured_retriever(
                harness,
                scorer=lambda *args: 500_000,
                conflict_proposal_resolver=lambda context, decisions, descriptors: proposals,
                selected_set_limit=2,
            )
            return _retrieval_case_result(
                _retrieve_all(retriever=retriever, context=harness.context, query=query),
                expected,
            )
        finally:
            harness.conflict_matrix.clear()
    if scenario == "equal-score":
        harness = literal_harness_factory("resolved")
        harness.conflict_matrix.clear()
        scores = iter(delta["scores"])
        retriever, _, query = _configured_retriever(
            harness,
            scorer=lambda *args: next(scores),
            selected_set_limit=1,
        )
        result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
        by_id = {item.candidate_id.value: item for item in result.decision.considered_candidates}
        ordered_keys = tuple(by_id[item.value].ranking_key for item in result.decision.selected_candidate_ids)
        assert ordered_keys == tuple(sorted(ordered_keys))
        return _retrieval_case_result(result, expected)
    if scenario == "no-candidates":
        # An empty *live index* no longer produces this case, and the change is
        # the point rather than an inconvenience. What the run may consider is
        # fixed by a committed snapshot; a library offering nothing while the
        # snapshot names objects is a store that lost them, which the
        # ``snapshot-names-a-missing-object`` case below asserts as a refusal.
        # "Nothing matched" is still perfectly reachable — the query asks for a
        # behavior kind none of the frozen objects has — and that is the honest
        # way to reach it now.
        unmatched = BehaviorKind(delta["requested_behavior_kind"])
        # Built directly rather than through the literal factory: the query must
        # ask for a kind the consumer would accept, so the declaration has to
        # allow one the frozen world does not contain.
        harness = _make_harness(tmp_path, extra_allowed_behavior_kinds=(unmatched,))
        assert harness.unit.core.behavior_kind is not unmatched
        retriever, _, _ = _configured_retriever(harness, scorer=lambda *args: 500_000)
        query = create_retrieval_query(
            retriever=retriever,
            context=harness.context,
            requested_behavior_kinds=(unmatched,),
            required_binding_targets=(),
            selected_set_limit=1,
        )
        return _retrieval_case_result(
            _retrieve_all(retriever=retriever, context=harness.context, query=query),
            expected,
        )
    if scenario == "snapshot-names-a-missing-object":
        harness = literal_harness_factory("default")
        retriever, _, query = _configured_retriever(harness, scorer=lambda *args: 500_000)
        frozen = frozen_for_retriever(retriever)
        # The snapshot is committed over what the library held; the library then
        # loses it. Narrowing the candidate set to what survived would let the
        # run proceed over a quietly smaller world and call the result complete.
        monkeypatch.setattr(harness.library, "search_index", lambda **kwargs: ())
        with pytest.raises(RetrievalViolation) as exc:
            _retrieve_all(
                retriever=retriever, context=harness.context, query=query, frozen=frozen
            )
        return {
            "compatibility_decision": None,
            "dimension": None,
            "reason": None,
            "candidate_disposition": None,
            "conflict_result": None,
            "selected": False,
            "failure": exc.value.failure_code.value,
        }
    if scenario == "toctou-before-loading":
        harness = _make_harness(tmp_path)

        def drifting_score(*args):
            harness.publish_extra("fixture-loading-drift")
            return 500_000

        retriever, _, query = _configured_retriever(harness, scorer=drifting_score)
        result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
        blocked = result.load_decisions[0]
        assert blocked.outcome is LoadOutcome.REVALIDATION_BLOCKED
        assert blocked._revalidation.outcome is RevalidationOutcome.FAILED
        assert blocked.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        assert blocked.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
        assert blocked.loaded_content_key is None
        assert blocked.loaded_manifest_id is None
        assert blocked.pre_load_snapshot_sha256 is None
        assert blocked.post_load_snapshot_sha256 is None
        return {
            **_retrieval_case_result(result, expected),
            "failure": blocked.failure_code.value,
        }
    if scenario == "toctou-before-consumption":
        harness = _make_harness(tmp_path)
        retriever, _, query = _configured_retriever(harness, scorer=lambda *args: 500_000)
        result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
        harness.publish_extra("fixture-consumption-drift")
        stage3 = revalidate_loaded_before_consumption(
            retriever=retriever,
            context=harness.context,
            retrieval_decision=result.decision,
            load_decision=result.load_decisions[0],
        )
        assert stage3.outcome is RevalidationOutcome.FAILED
        assert stage3.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED
        assert stage3.cause_code is CompatibilityFailureCode.SNAPSHOT_DRIFT
        return {
            **_retrieval_case_result(result, expected),
            "failure": stage3.failure_code.value,
        }
    if scenario == "typed-binding-target":
        harness, repo, binding = literal_harness_factory("binding")
        assert binding.path == delta["path"]
        assert binding.module == delta["module"]
        assert binding.qualname == delta["qualname"]
        assert binding.symbol_kind.value == delta["symbol_kind"]
        retriever, _, query = _configured_retriever(
            harness,
            scorer=lambda *args: 500_000,
            required_binding_targets=(binding,),
        )
        assert query.required_binding_targets == (binding_to_retrieval_target(binding),)
        return _retrieval_case_result(
            _retrieve_all(retriever=retriever, context=harness.context, query=query),
            expected,
        )
    raise AssertionError(f"unhandled literal fixture scenario: {scenario}")


@pytest.mark.parametrize("case", _RETRIEVAL_CASES, ids=lambda case: case["name"])
def test_s4_p6_followup_fixture_01_every_literal_case_executes_the_production_path(
    case: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _literal_harness_factory,
) -> None:
    assert _execute_literal_retrieval_case(
        case,
        tmp_path,
        monkeypatch,
        _literal_harness_factory,
    ) == case["expected"]


def test_s4_p6_followup_candidates_01_missing_descriptor_or_decision_makes_scan_incomplete(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_unresolved=1)
    score_calls: list[str] = []

    def resolver(entry):
        if entry.content_key == harness.entry.content_key:
            return harness.descriptor
        raise RetrievalViolation(RetrievalFailureCode.DESCRIPTOR_MISSING, "descriptor unavailable")

    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: score_calls.append(descriptor_id.value) or 1,
        descriptor_resolver=resolver,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    scan = result.decision.conflict_records[0]._scan
    assert len(result.decision.considered_candidates) == 2
    assert scan.decision_kind is ConflictDecisionKind.SCAN_INCOMPLETE
    assert len(scan.request.considered_candidate_keys) == 2
    assert len(scan.request.validated_candidate_ids) == 1
    assert len(scan.request.incomplete_candidate_keys) == 1
    assert result.decision.selected_candidate_ids == ()
    assert result.load_decisions == ()
    assert score_calls == []


def test_s4_p6_followup_conflicts_01_proposals_neither_create_nor_suppress_authoritative_conflict(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_resolved=1)
    other_descriptor = harness.extra_candidates[0][7]
    pair_key = tuple(sorted((harness.descriptor.descriptor_id.value, other_descriptor.descriptor_id.value)))
    proposal = create_conflict_evidence_proposal(
        conflict_kind=ConflictKind.CONTRADICTORY_EVIDENCE,
        proposer_actor=ActorIdentity("untrusted-conflict-proposer"),
        left_descriptor_id=harness.descriptor.descriptor_id,
        right_descriptor_id=other_descriptor.descriptor_id,
        scope=("src/a.py",),
        binding_targets=("pkg.symbol",),
        evidence_refs=(_ref("proposal-evidence", RefKind.SOURCE_EVIDENCE),),
    )
    with_proposal, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        conflict_proposal_resolver=lambda context, decisions, descriptors: (proposal,),
        selected_set_limit=2,
    )
    proposal_result = _retrieve_all(retriever=with_proposal, context=harness.context, query=query)
    proposal_scan = proposal_result.decision.conflict_records[0]._scan
    assert proposal_scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    assert proposal_scan.request.proposals == (proposal,)
    assert proposal_scan.request.pair_assessments[0].conflict_kind is None
    assert proposal_scan.request.pair_assessments[0].proposal_dispositions == (
        (proposal.proposal_id, ConflictProposalDisposition.RECORDED_UNTRUSTED_EVIDENCE),
    )
    assert len(proposal_result.decision.selected_candidate_ids) == 2
    assert len(proposal_result.load_decisions) == 2

    harness.conflict_matrix[pair_key] = (
        ConflictKind.CONTRADICTORY_EVIDENCE,
        (_ref("authoritative-conflict-evidence", RefKind.SOURCE_EVIDENCE),),
    )
    without_proposal, _, conflict_query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        conflict_proposal_resolver=lambda context, decisions, descriptors: (),
        selected_set_limit=2,
    )
    conflict_result = _retrieve_all(
        retriever=without_proposal,
        context=harness.context,
        query=conflict_query,
    )
    conflict_scan = conflict_result.decision.conflict_records[0]._scan
    assert conflict_scan.request.proposals == ()
    assert conflict_scan.request.pair_assessments[0].conflict_kind is ConflictKind.CONTRADICTORY_EVIDENCE
    assert conflict_scan.decision_kind is ConflictDecisionKind.UNRESOLVED_CONFLICT
    assert conflict_result.decision.selected_candidate_ids == ()
    assert conflict_result.load_decisions == ()


def test_s4_p6_followup_conflicts_02_pairwise_assessments_cover_every_required_pair(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 250_000,
        selected_set_limit=3,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    scan = result.decision.conflict_records[0]._scan
    descriptor_ids = tuple(item.value for item in scan.request.validated_candidate_ids)
    expected_pairs = {
        (left, right)
        for index, left in enumerate(descriptor_ids)
        for right in descriptor_ids[index + 1 :]
    }
    observed_pairs = {
        (item.left_descriptor_id.value, item.right_descriptor_id.value)
        for item in scan.request.pair_assessments
    }
    assert len(scan.request.considered_candidate_keys) == 3
    assert len(scan.request.validated_candidate_ids) == 3
    assert scan.request.incomplete_candidate_keys == ()
    assert len(scan.request.pair_assessments) == 3
    assert observed_pairs == expected_pairs
    assert all(item.conflict_kind is None for item in scan.request.pair_assessments)
    assert scan.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    assert len(result.decision.selected_candidate_ids) == 3
    assert len(result.load_decisions) == 3


def test_s4_p6_followup_query_01_binding_targets_use_typed_binding_semantics(tmp_path: Path) -> None:
    repo, binding = _python_binding_repo(tmp_path)
    harness = _make_harness(
        tmp_path / "harness",
        bindings=(binding,),
        binding_repo_root=repo,
    )
    target = binding_to_retrieval_target(binding)
    assert type(target) is RetrievalBindingTarget
    assert target.path == binding.path
    assert target.module == binding.module
    assert target.qualname == binding.qualname
    assert target.symbol_kind == binding.symbol_kind.value
    assert target.binding_ref.ref_id != target.qualname
    validate_retrieval_binding_target(target)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 100_000,
        required_binding_targets=(binding,),
    )
    assert query.required_binding_targets == (target,)
    rebuilt = retrieval_query_from_dict(
        query.to_dict(),
        retriever=retriever,
        context=harness.context,
        required_bindings=(binding,),
    )
    assert rebuilt.to_dict() == query.to_dict()
    selected = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    assert selected.decision.outcome is RetrievalOutcome.SELECTED
    assert len(selected.decision.selected_candidate_ids) == 1
    with pytest.raises(RetrievalViolation) as exc:
        create_retrieval_query(
            retriever=retriever,
            context=harness.context,
            requested_behavior_kinds=(harness.unit.core.behavior_kind,),
            required_binding_targets=(binding.binding_id.value,),
            selected_set_limit=1,
        )
    assert exc.value.failure_code is RetrievalFailureCode.MALFORMED_QUERY


def test_s4_p6_followup_ranking_01_fixed_observations_have_insertion_independent_total_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ranking order must not depend on the order the library hands entries back.

    The reversal below used to be the whole mechanism: enumeration read
    ``search_index()`` directly, so reversing it genuinely changed the order
    candidates arrived in and the assertions proved the ranking recovered a total
    order regardless.

    Enumeration now takes its order from the frozen set, whose keys are sorted,
    so the reversal can no longer reach the ranking at all. That is a stronger
    guarantee rather than a weaker test, and it is worth keeping the reversal to
    say so out loud: the property being asserted is that library order is not an
    input to the result, and it is now enforced in two independent places — the
    frozen ordering upstream and the total order on ranking keys downstream.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    first_retriever, _, first_query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        selected_set_limit=2,
    )
    first = _retrieve_all(
        retriever=first_retriever,
        context=harness.context,
        query=first_query,
    )
    first_order = tuple(item.value for item in first.decision.selected_candidate_ids)
    first_keys = tuple(sorted(
        item.ranking_key
        for item in first.decision.considered_candidates
        if item.ranking_key is not None
    ))
    original_entries = harness.library.search_index()
    monkeypatch.setattr(harness.library, "search_index", lambda **kwargs: tuple(reversed(original_entries)))
    second_retriever, _, second_query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        selected_set_limit=2,
    )
    second = _retrieve_all(
        retriever=second_retriever,
        context=harness.context,
        query=second_query,
    )
    second_order = tuple(item.value for item in second.decision.selected_candidate_ids)
    second_keys = tuple(sorted(
        item.ranking_key
        for item in second.decision.considered_candidates
        if item.ranking_key is not None
    ))
    assert len(first_order) == 2
    assert len(first_keys) == 2
    assert first_order == second_order
    assert first_keys == second_keys
    assert first_keys == tuple(sorted(first_keys))


def test_s4_p6_corrective_audit_02_nested_compatibility_tamper_is_rejected_by_consumer(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    validate_retrieval_decision(
        result.decision,
        retriever=retriever,
        query=query,
        context=harness.context,
    )
    candidate = result.decision.considered_candidates[0]
    original_decision = candidate._compatibility_decision
    assert original_decision is not None
    validate_compatibility_decision(
        original_decision,
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
    )

    forged_dimension = object.__new__(type(original_decision.evidence.dimensions[0]))
    for name, value in vars(original_decision.evidence.dimensions[0]).items():
        object.__setattr__(forged_dimension, name, value)
    object.__setattr__(forged_dimension, "reason", CompatibilityReason.VALUE_MISMATCH)
    forged_evidence = object.__new__(type(original_decision.evidence))
    for name, value in vars(original_decision.evidence).items():
        object.__setattr__(forged_evidence, name, value)
    object.__setattr__(
        forged_evidence,
        "dimensions",
        (forged_dimension, *original_decision.evidence.dimensions[1:]),
    )
    forged_decision = object.__new__(type(original_decision))
    for name, value in vars(original_decision).items():
        object.__setattr__(forged_decision, name, value)
    object.__setattr__(forged_decision, "evidence", forged_evidence)
    object.__setattr__(candidate, "_compatibility_decision", forged_decision)

    with pytest.raises(CompatibilityViolation):
        validate_retrieval_decision(
            result.decision,
            retriever=retriever,
            query=query,
            context=harness.context,
        )


def test_s4_p6_corrective_authority_02_configured_authority_logic_cannot_be_reassigned(tmp_path: Path) -> None:
    evaluator_harness = _make_harness(tmp_path / "evaluator", extra_resolved=1)
    evaluator_control = evaluate_conflicts(
        evaluator=evaluator_harness.evaluator,
        context=evaluator_harness.context,
        decisions=(evaluator_harness.decision, evaluator_harness.extra_candidates[0][8]),
        descriptors=(evaluator_harness.descriptor, evaluator_harness.extra_candidates[0][7]),
        considered_index_entries=tuple(evaluator_harness.library.search_index()),
        proposals=(),
    )
    assert evaluator_control.decision_kind is ConflictDecisionKind.NO_CONFLICT_FOUND
    evaluator_calls: list[str] = []

    def replacement_assessor(context, left_decision, right_decision, left_descriptor, right_descriptor):
        evaluator_calls.append(left_descriptor.descriptor_id.value)
        return ConflictKind.CONTRADICTORY_EVIDENCE, ()

    with pytest.raises(AttributeError):
        evaluator_harness.evaluator._conflict_assessor = replacement_assessor
    object.__setattr__(evaluator_harness.evaluator, "_conflict_assessor", replacement_assessor)
    with pytest.raises(CompatibilityViolation) as evaluator_exc:
        evaluate_conflicts(
            evaluator=evaluator_harness.evaluator,
            context=evaluator_harness.context,
            decisions=(evaluator_harness.decision, evaluator_harness.extra_candidates[0][8]),
            descriptors=(evaluator_harness.descriptor, evaluator_harness.extra_candidates[0][7]),
            considered_index_entries=tuple(evaluator_harness.library.search_index()),
            proposals=(),
        )
    assert evaluator_exc.value.failure_code is CompatibilityFailureCode.EVALUATOR_CAPABILITY_MISMATCH
    assert evaluator_calls == []

    retriever_harness = _make_harness(tmp_path / "retriever")
    retriever, _, query = _configured_retriever(
        retriever_harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    retriever_control = _retrieve_all(
        retriever=retriever,
        context=retriever_harness.context,
        query=query,
    )
    assert retriever_control.decision.outcome is RetrievalOutcome.SELECTED
    retriever_calls: list[str] = []

    def replacement_resolver(entry):
        retriever_calls.append(entry.content_key)
        return retriever_harness.descriptor

    with pytest.raises(AttributeError):
        retriever._descriptor_resolver = replacement_resolver
    object.__setattr__(retriever, "_descriptor_resolver", replacement_resolver)
    with pytest.raises(RetrievalViolation) as retriever_exc:
        _retrieve_all(retriever=retriever, context=retriever_harness.context, query=query)
    assert retriever_exc.value.failure_code is RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER
    assert retriever_calls == []

    provider_harness = _make_harness(tmp_path / "provider")
    provider_retriever, provider, provider_query = _configured_retriever(
        provider_harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
    )
    provider_control = _retrieve_all(
        retriever=provider_retriever,
        context=provider_harness.context,
        query=provider_query,
    )
    assert provider_control.decision.outcome is RetrievalOutcome.SELECTED
    provider_calls: list[str] = []

    def replacement_scorer(query_id, descriptor_id, score_input):
        provider_calls.append(descriptor_id.value)
        return 1

    with pytest.raises(AttributeError):
        provider._scorer = replacement_scorer
    object.__setattr__(provider, "_scorer", replacement_scorer)
    with pytest.raises(RetrievalViolation) as provider_exc:
        _retrieve_all(
            retriever=provider_retriever,
            context=provider_harness.context,
            query=provider_query,
        )
    assert provider_exc.value.failure_code is RetrievalFailureCode.RANKING_INPUT_MALFORMED
    assert provider_calls == []


def test_s4_p6_corrective_ranking_02_exact_key_uses_validated_manifest_and_content_identity(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path, extra_resolved=3)
    retriever, _, query = _configured_retriever(
        harness,
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        selected_set_limit=4,
    )
    result = _retrieve_all(retriever=retriever, context=harness.context, query=query)
    ranked = tuple(sorted(
        result.decision.considered_candidates,
        key=lambda item: item.ranking_key,
    ))
    descriptor_order = tuple(sorted(
        result.decision.considered_candidates,
        key=lambda item: (item._descriptor.descriptor_id.value, item._descriptor.content_key.value),
    ))
    assert len(ranked) == 4
    assert all(item._descriptor is not None for item in ranked)
    assert all(item.ranking_key is not None for item in ranked)
    assert tuple(item._descriptor.descriptor_id for item in ranked) != tuple(item._descriptor.descriptor_id for item in descriptor_order)
    assert tuple(item.ranking_key for item in ranked) == tuple(
        (
            -item._ranking_feature.semantic_score_micros,
            item._descriptor.manifest_id.value,
            item._descriptor.content_key.value,
        )
        for item in ranked
    )
    assert result.decision.selected_candidate_ids == tuple(item.candidate_id for item in ranked)


# ---------------------------------------------------------------------------
# The §22 seam: enumeration is fixed before the gate, selection happens after
# ---------------------------------------------------------------------------


def test_only_gate_admitted_candidates_are_ranked_selected_and_loaded(tmp_path: Path) -> None:
    """The kill for a selection that ignores what the gate admitted.

    This is the whole point of the split. ``enumerate_retrieval_candidates``
    fixes the subject set, the §22 chain runs over exactly that set, and
    ``select_and_load`` may only see what came back admitted. Admitting nothing
    must therefore select nothing and load nothing — not "select as before
    because the list was there anyway".
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    assert enumeration.subject_refs, "the enumeration must offer the gate something to decide"

    admitted_all = select_and_load_durably(
        retriever=retriever, context=harness.context, query=query,
        enumeration=enumeration,
        admission=_admission_for(enumeration, harness.context, journal=journal),
        persistence=persistence,
    )
    assert admitted_all.decision.selected_candidate_ids
    assert admitted_all.load_decisions

    admitted_none = select_and_load_durably(
        retriever=retriever, context=harness.context, query=query,
        enumeration=enumeration,
        admission=_admission_for(enumeration, harness.context, admit=False, journal=journal),
        persistence=persistence,
    )
    assert admitted_none.decision.selected_candidate_ids == ()
    assert admitted_none.load_decisions == ()
    # The rejected candidates keep their place in the audit trace.
    assert len(admitted_none.decision.considered_candidates) == len(
        admitted_all.decision.considered_candidates
    )


def test_a_candidate_the_enumeration_never_found_cannot_be_admitted(tmp_path: Path) -> None:
    """An admitted ref must come from this enumeration, not from anywhere.

    Otherwise the gate's answer and the loader's input are two different sets,
    and a caller could hand back a ref for an object this query never
    considered.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    foreign = _ref("never-enumerated", RefKind.ARTIFACT)

    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration,
            admission=_admission_for(enumeration, harness.context, refs=(foreign,), journal=journal),
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE


def test_an_enumeration_from_another_query_is_refused(tmp_path: Path) -> None:
    """Enumeration and selection must describe the same question."""

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    _, _, other_query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000,
        selected_set_limit=2,
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )

    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=other_query,
            enumeration=enumeration,
            admission=_admission_for(enumeration, harness.context, journal=journal),
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER


def test_an_enumeration_cannot_be_built_outside_the_retriever(tmp_path: Path) -> None:
    """A hand-built enumeration would let a caller name its own subject set."""

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )

    with pytest.raises(TypeError):
        RetrievalEnumeration()

    object.__setattr__(enumeration, "_trusted_seal", object())
    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration,
            admission=_admission_for(enumeration, harness.context, journal=journal),
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.TRUSTED_RECORD_FORGED


def test_the_gate_reference_is_exact_stable_and_distinct_per_candidate(tmp_path: Path) -> None:
    """What the gate decides about must be one exact object, and only that one.

    A tampered descriptor cannot be constructed to test this — the type is
    factory-created and ``candidate_subject_ref`` validates it first — so the
    property is stated the way it can be observed: the reference is a pure
    function of the descriptor, it separates genuinely distinct candidates, and
    a candidate that is not exactly a descriptor gets no reference at all.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    descriptors = [harness.descriptor] + [item[7] for item in harness.extra_candidates]
    refs = [candidate_subject_ref(item) for item in descriptors]

    assert len(descriptors) >= 2, "distinctness needs more than one candidate"
    assert len({item.sha256 for item in refs}) == len(refs), "candidates must not collide"
    assert all(item.kind is RefKind.ARTIFACT for item in refs)
    assert candidate_subject_ref(harness.descriptor).sha256 == refs[0].sha256

    for bogus in (None, "descriptor", harness.entry):
        with pytest.raises((CompatibilityViolation, RetrievalViolation, TypeError)):
            candidate_subject_ref(bogus)


def test_a_fabricated_admission_cannot_be_handed_to_the_loader(tmp_path: Path) -> None:
    """The kill for the barrier itself, which had none.

    ``RetrievalAdmission`` exists so that the admitted set arrives as a verdict
    rather than as a list a caller assembled. That is worth nothing unless a
    fabricated one is refused — and until this test there was no case anywhere
    that tried, so the seal was an assumption rather than a checked property.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )

    with pytest.raises(TypeError):
        RetrievalAdmission()

    genuine = _admission_for(enumeration, harness.context, journal=journal)
    object.__setattr__(genuine, "_trusted_seal", object())
    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration, admission=genuine,
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.TRUSTED_RECORD_FORGED


def test_a_verdict_for_another_consumer_is_refused(tmp_path: Path) -> None:
    """A genuine admission is still the wrong one if it names another consumer.

    Compatibility and admission are both properties of a subject *in a context*.
    A verdict given for somebody else's context is as useless here as a forged
    one, and until now nothing distinguished them because every test used a
    single context.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    # A different *path* is not a different context: identical inputs give an
    # identical identity, which is the canonicalisation working. The environment
    # version is part of the context identity, so changing it is what actually
    # produces another consumer.
    other = _make_harness(
        tmp_path / "other",
        extra_resolved=2,
        context_environment_version="synapse.stage4.environment/v999",
    )
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )

    foreign = _admission_for(enumeration, other.context, journal=journal)
    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration, admission=foreign,
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.WRONG_CONFIGURED_RETRIEVER


def test_refs_cannot_be_admitted_by_a_blocking_verdict(tmp_path: Path) -> None:
    """A blocked decision admits nothing, whatever the record says it admitted.

    The factory never produces this pair, so the check is defence in depth for a
    record edited afterwards — and defence in depth still has to be shown to
    work, or it is decoration.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )

    blocked = _admission_for(enumeration, harness.context, admit=False, journal=journal)
    assert blocked.admitted_refs == (), "a blocking verdict admits nothing to begin with"
    object.__setattr__(blocked, "admitted_refs", enumeration.subject_refs)

    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration, admission=blocked,
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.COMPATIBILITY_REJECTED


def test_a_verdict_over_only_part_of_the_enumeration_is_refused(tmp_path: Path) -> None:
    """The case only the coverage check sees, and the reason it is not redundant.

    Two checks guard the verdict: it must cover exactly what was enumerated, and
    the refs it admits must come from that enumeration. A verdict about a
    *foreign* object trips both, which is why the earlier test could not tell
    them apart — it asserted the right failure code for the wrong reason.

    A verdict over a strict subset separates them. Every admitted ref is
    genuinely enumerated, so the second check is satisfied; what is wrong is that
    the gate was never asked about the rest. Without the coverage check those
    candidates would be quietly treated as "not admitted" when the truth is "not
    presented" — a caller could narrow what the gate sees and the record would
    look like a decision.
    """

    harness = _make_harness(tmp_path, extra_resolved=2)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 900_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    assert len(enumeration.subject_refs) >= 2, "a subset needs something to be a subset of"

    partial = _admission_for(
        enumeration, harness.context, refs=enumeration.subject_refs[:1], journal=journal
    )
    admitted = {item.sha256 for item in partial.admitted_refs}
    enumerated = {item.sha256 for item in enumeration.subject_refs}
    assert admitted < enumerated, "every admitted ref is enumerated, so only coverage fails"

    with pytest.raises(RetrievalViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration, admission=partial,
            persistence=persistence,
        )
    assert excinfo.value.failure_code is RetrievalFailureCode.CANDIDATE_SET_INCOMPLETE
    assert "cover exactly" in str(excinfo.value)


# ---------------------------------------------------------------------------
# §21: the committed snapshot fixes what a run may consider
# ---------------------------------------------------------------------------


def test_an_object_outside_the_frozen_snapshot_is_never_a_candidate(tmp_path: Path) -> None:
    """The normative mutant, executed rather than described.

    §21 says the snapshot is *the* input of knowledge for a run. Enumeration took
    its candidates from ``library.search_index()``, so an object the snapshot
    never saw was enumerated, evaluated for compatibility and could pass the
    Retrieval Gate — and nothing detected it, because the committed snapshot was
    undamaged the whole time and a boundary probe verifies that a boundary is
    committed, not that retrieval obeyed one.

    The library here holds two fully resolvable objects and the snapshot freezes
    one. There is nothing wrong with the second: it is published, indexed, has a
    descriptor and would rank. It is simply not in the frozen world, and that
    alone must keep it out of the candidate set.

    Freezing *both* is the control. Without it, an assertion that one object is
    absent proves only that something excluded it; with it, the exclusion is
    shown to follow from the snapshot and from nothing else in the harness.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 500_000
    )
    inside = harness.entry
    outside = harness.extra_candidates[0][3]
    assert inside.content_key != outside.content_key

    store = tmp_path / "frozen-scenario"
    partial, _ = snapshot_over((inside,), store_root=store)
    whole, _ = snapshot_over((inside, outside), store_root=store)

    constrained = enumerate_retrieval_candidates(
        retriever=retriever, context=harness.context, query=query, frozen=partial
    )
    considered = {item._index_entry.content_key for item in constrained.candidates}
    assert considered == {inside.content_key}, "the live index is not the candidate set"
    assert outside.content_key not in considered
    assert constrained.subject_refs == (
        candidate_subject_ref(harness.descriptor),
    )

    unconstrained = enumerate_retrieval_candidates(
        retriever=retriever, context=harness.context, query=query, frozen=whole
    )
    assert {item._index_entry.content_key for item in unconstrained.candidates} == {
        inside.content_key,
        outside.content_key,
    }, "the second object is enumerable, so its earlier absence came from the snapshot"


def test_an_enumeration_carries_the_boundary_that_governed_it(tmp_path: Path) -> None:
    """Provenance is recorded, not assumed.

    An auditor reading a retrieval record has to be able to say *which* snapshot
    fixed the candidate set. Being told that one did is not evidence of anything,
    and a subsequent reader cannot check a claim that was never written down.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 500_000
    )
    frozen, snapshot = snapshot_over(
        harness.library.search_index(), store_root=tmp_path / "frozen-provenance"
    )
    enumeration = enumerate_retrieval_candidates(
        retriever=retriever, context=harness.context, query=query, frozen=frozen
    )

    assert enumeration.governing_snapshot is frozen
    assert (
        enumeration.governing_snapshot.boundary_id_sha256
        == snapshot.boundary.atomic_boundary_id.digest_sha256
    )
    assert (
        enumeration.governing_snapshot.snapshot_id_sha256
        == snapshot.manifest.snapshot_id.digest_sha256
    )
    # Every enumerated subject is named by the snapshot the record points at, so
    # the provenance and the candidate set describe one world rather than two.
    assert len(frozen.subject_ref_keys) == len(harness.library.search_index())


def test_an_enumeration_whose_provenance_was_stripped_cannot_be_used(tmp_path: Path) -> None:
    """A recorded boundary is worth nothing if the loader accepts one without it.

    The field is set by the only function that can build an enumeration, so the
    way it goes missing in practice is an object edited afterwards — which is
    exactly the case the seal check exists for and the case that had no test.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 500_000
    )
    enumeration, journal, persistence = _durable_enumeration(
        retriever=retriever, context=harness.context, query=query,
        frozen=frozen_for_retriever(retriever),
    )
    admission = _admission_for(enumeration, harness.context, journal=journal)

    object.__setattr__(enumeration, "governing_snapshot", None)
    with pytest.raises(ContractViolation) as excinfo:
        select_and_load_durably(
            retriever=retriever, context=harness.context, query=query,
            enumeration=enumeration, admission=admission,
            persistence=persistence,
        )
    assert excinfo.value.failure_code is ContractFailureCode.TRUSTED_OBJECT_FORGED


def test_an_unsealed_frozen_set_cannot_constrain_an_enumeration(tmp_path: Path) -> None:
    """The demand for a frozen set is worth what its verification is worth.

    A caller that can hand ``enumerate_retrieval_candidates`` an object of its own
    shape has not been constrained by a snapshot — it has been asked to describe
    one. The mutation campaign found this gap by removing the validation and
    surviving every tier: the seal existed, and nothing checked that the
    enumeration consulted it.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 500_000
    )

    class Impostor:
        boundary_id_sha256 = "0" * 64
        snapshot_id_sha256 = "1" * 64
        subject_ref_keys = ()

    with pytest.raises(ContractViolation) as excinfo:
        enumerate_retrieval_candidates(
            retriever=retriever, context=harness.context, query=query,
            frozen=Impostor(),
        )
    assert excinfo.value.failure_code is ContractFailureCode.TRUSTED_OBJECT_FORGED


def test_a_frozen_set_whose_seal_was_stripped_cannot_constrain_an_enumeration(
    tmp_path: Path,
) -> None:
    """The harder half: a real set, correct in every field, minus its seal.

    Type and shape cannot separate this from a genuine frozen set — the keys are
    the ones the snapshot froze and they all resolve, so an enumeration that
    checked only that its input *looked* right would proceed and produce a result
    indistinguishable from a governed one. Only the seal says where it came from.
    """

    harness = _make_harness(tmp_path, extra_resolved=1)
    retriever, _, query = _configured_retriever(
        harness, scorer=lambda query_id, descriptor_id, score_input: 500_000
    )
    frozen, _ = snapshot_over(
        harness.library.search_index(), store_root=tmp_path / "frozen-seal"
    )
    assert enumerate_retrieval_candidates(
        retriever=retriever, context=harness.context, query=query, frozen=frozen
    ).candidates, "the set must be usable before it is broken"

    object.__setattr__(frozen, "_trusted_seal", object())
    with pytest.raises(ContractViolation) as excinfo:
        enumerate_retrieval_candidates(
            retriever=retriever, context=harness.context, query=query, frozen=frozen
        )
    assert excinfo.value.failure_code is ContractFailureCode.TRUSTED_OBJECT_FORGED
