"""Stage 4 consumption gate bound to real Stage 3 evidence (Patch 8 repair, round 14).

Item 12 of the second review. The projection from a stage-3
`CompatibilityRevalidationRecord` into a `CompatibilityFinding` already existed;
what did not exist was anything that made the §22 consumption gate read one.
`configure_gate_controller` took a `compatibility_probe` and every caller
supplied a callable of its own, so the gate consulted *something* about
compatibility and nothing required that something to be a revalidation of the
exact subject against the exact consumer context.

These tests run the real gate against the real binding: the records come from
the compatibility harness, the revalidation happens when the gate asks, and the
refusals are driven by making a real record say no rather than by a flag.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold import gate_findings as GF
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.compatibility import (
    CompatibilityFailureCode,
    CompatibilityViolation,
    ConflictDecisionKind,
    RevalidationOutcome,
    create_compatibility_context,
    evaluate_compatibility,
    evaluate_conflicts,
    revalidate_before_consumption,
    revalidate_before_loading,
)
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    GateDecisionKind,
    GateKind,
    RunId,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.compatibility_store import FileCompatibilityStore
from synapse.experiments.gold.persistence import store_transaction
from tests.gold_store_fence import fence_for

from tests.test_stage4_gold_compatibility import _fresh_platform_observation, _make_harness

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"
FROZEN_SET_REF = HashBoundRef(
    kind=RefKind.ARTIFACT,
    ref_id="consumption-frozen-set",
    schema_id=SchemaVersion.FROZEN_CANDIDATE_SET_V2.value,
    sha256=hashlib.sha256(b"consumption-frozen-set").hexdigest(),
    byte_length=len(b"consumption-frozen-set"),
    media_type="application/json",
)


def _stage3_inputs(harness):
    """The four Stage 3 records a consumption binding needs, all genuine."""

    before_loading = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    scan = evaluate_conflicts(
        evaluator=harness.evaluator,
        context=harness.context,
        decisions=(harness.decision,),
        descriptors=(harness.descriptor,),
        considered_index_entries=(harness.entry,),
        proposals=(),
    )
    return before_loading, scan


def _binding(harness):
    before_loading, scan = _stage3_inputs(harness)
    return GF.bind_consumption_evidence(
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
        conflict_scan=scan,
    )


def production_point_of_use_case(
    tmp_path: Path,
    *,
    observation_hook=None,
    gate_time: datetime = NOW,
    behavior_core: dict | None = None,
    extra_behavior_cores: tuple[dict, ...] = (),
    program_artifacts: tuple[tuple[HashBoundRef, bytes], ...] = (),
):
    """Build the real, single-coordinator point-of-use authority graph.

    The historical compatibility records and gate chain are intentionally
    committed *after* the boundary.  The manifest therefore freezes only the
    candidate universe and the pre-attempt history roots, while the current
    attempt's decisions remain external records that name that exact boundary.
    """

    from synapse.experiments.gold import knowledge as K
    from synapse.experiments.gold import point_of_use as P
    from synapse.experiments.gold import admission_store as S
    from synapse.experiments.gold.admission_journal import FileAdmissionJournal
    from synapse.experiments.gold.compatibility import (
        configure_compatibility_evaluator,
        create_compatibility_context,
    )
    from synapse.experiments.gold.lifecycle import open_lifecycle_store
    from synapse.experiments.gold.provenance import open_behavior_attestation_store
    from synapse.experiments.gold.taint import open_taint_history_store

    world = _make_harness(
        tmp_path / "world",
        behavior_core=behavior_core,
        extra_resolved_cores=extra_behavior_cores,
        program_artifacts=program_artifacts,
    )
    # Every published behavior this world admits, primary first. §23 admits an
    # *ordered* set of behaviors, so a world that can publish only one subject
    # cannot express the ordered case at all — which is why it is a parameter
    # rather than a fixed one.
    supported = (
        (world.unit, world.descriptor, world.entry),
        *(
            (candidate[0], candidate[7], candidate[3])
            for candidate in world.extra_candidates
        ),
    )
    # The library already uses this coordinator.  Re-open the three authority
    # histories over the same immutable bytes and the same exact fence object.
    fence = fence_for(world.root)
    lifecycle_store = open_lifecycle_store(
        root=world.root / "lifecycle",
        authority_handle=world.handle,
        mutation_fence=fence,
        trusted_anchor=world.lifecycle_store.current_anchor(),
    )
    attestation_store = open_behavior_attestation_store(
        root=world.root / "attestations",
        authority_handle=world.handle,
        platform_attester=world.attester,
        mutation_fence=fence,
        trusted_anchor=world.attestation_store.current_anchor(),
    )
    taint_store = open_taint_history_store(
        root=world.root / "taint",
        authority_handle=world.handle,
        mutation_fence=fence,
        trusted_anchor=world.taint_store.current_anchor(),
    )
    journal = FileAdmissionJournal(
        world.root / "admission" / "decisions.journal", fence
    )
    causal_history = S.FileAdmissionCausalStore(
        world.root / "admission-causal",
        mutation_fence=fence,
        admission_history=journal,
    )
    compatibility_history = FileCompatibilityStore(
        world.root / "compatibility", mutation_fence=fence
    )

    knowledge_context = K.create_knowledge_context(
        repository_revision="a" * 40,
        policy_version=POLICY,
        environment_profile_id="production-point-of-use",
    )
    run_id = RunId("point-of-use-run")
    attempt_id = AttemptId("point-of-use-attempt")
    admission_root_manifest = K.create_admission_root_manifest(
        context=knowledge_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=world.handle.configuration_id,
        admission_history=journal,
        retrieval_causal_history=causal_history,
        historical_admission_refs=(),
        historical_retrieval_decision_refs=(),
        created_at_utc=NOW,
        producer_component="point-of-use-snapshot-producer",
    )
    compatibility_evidence_manifest = K.create_compatibility_evidence_manifest(
        context=knowledge_context,
        run_id=run_id,
        attempt_id=attempt_id,
        authority_configuration_id=world.handle.configuration_id,
        compatibility_history=compatibility_history,
        compatibility_refs=(),
        created_at_utc=NOW,
        producer_component="point-of-use-snapshot-producer",
    )
    library_snapshot = world.library.current_snapshot().snapshot
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
    subject = GF.candidate_subject_ref(world.descriptor)
    #: Canonically ordered, because that is the one representation §22 decides
    #: about. The execution order a replay wants is a §23 concern and is chosen
    #: by the caller of the replay, not frozen here.
    subjects = A.canonical_subject_refs(
        tuple(GF.candidate_subject_ref(item[1]) for item in supported)
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
        created_at_utc=NOW,
        run_id=run_id,
        attempt_id=attempt_id,
        producer_component="point-of-use-snapshot-producer",
    )
    snapshot_actor_set = AC.create_snapshot_actor_set(
        authority_handle=world.handle,
        builder_actor=world.handle.configuration.builder_actor,
        producer_actor=ActorIdentity("point-of-use-snapshot-producer"),
        source_actor=ActorIdentity("point-of-use-source"),
        retriever_actor=ActorIdentity("point-of-use-retriever"),
        indexer_actor=ActorIdentity("point-of-use-indexer"),
        publisher_actor=ActorIdentity("point-of-use-publisher"),
        consumer_actor=ActorIdentity("point-of-use-consumer"),
        worker_actor=ActorIdentity("point-of-use-worker"),
        executor_actor=ActorIdentity("point-of-use-executor"),
    )
    snapshot_declaration = AC.create_snapshot_evaluator_declaration(
        authority_handle=world.handle,
        evaluator_identity=AuthorityIdentity("point-of-use-snapshot-evaluator"),
        authority_role=AuthorityRole.SNAPSHOT_COMPLETENESS_EVALUATOR,
        evaluator_component_id="snapshot-evaluator",
        evaluator_component_version="1.0.0",
        policy_version=POLICY,
        trusted_clock=lambda: NOW,
    )
    snapshot_proof = AC.create_snapshot_independence_proof(
        declaration=snapshot_declaration,
        actor_set=snapshot_actor_set,
    )
    snapshot_evaluator = K.configure_snapshot_evaluator(
        authority_handle=world.handle,
        declaration=snapshot_declaration,
        actor_set=snapshot_actor_set,
        independence_proof=snapshot_proof,
        trusted_clock=lambda: NOW,
        observed_roots_provider=lambda: roots,
        root_fence=fence,
        ref_resolver=lambda item: True,
        consumability_probe=lambda item: True,
    )
    evaluation = K.evaluate_snapshot_completeness(snapshot_evaluator, manifest=manifest)
    snapshot_root = world.root / "snapshot"
    from synapse.experiments.gold.knowledge_store import AuthoritativeKnowledgeStore
    knowledge_store = AuthoritativeKnowledgeStore(snapshot_root, mutation_fence=fence)
    transaction_id = "point-of-use-boundary"
    boundary = K.commit_atomic_snapshot_boundary(
        snapshot_root,
        transaction_id=transaction_id,
        manifest=manifest,
        evaluation=evaluation,
        admission_root_manifest=admission_root_manifest,
        admission_journal=journal,
        compatibility_evidence_manifest=compatibility_evidence_manifest,
        compatibility_history=compatibility_history,
        admission_causal_history=causal_history,
        knowledge_store=knowledge_store,
        run_id=run_id,
        attempt_id=attempt_id,
        expected_parent_boundary_id=None,
        start_sequence=0,
        commit_sequence=2,
        evaluator=snapshot_evaluator,
    )
    boundary_ref = K.atomic_boundary_ref(boundary)

    # Build the current attempt only after the boundary is committed.  The new
    # evaluator uses the exact production stores that the sealed binding exposes.
    def current_platform_observation():
        if observation_hook is not None:
            observation_hook(fence)
        return world.observation_provider()

    evaluator = configure_compatibility_evaluator(
        authority_handle=world.handle,
        declaration=world.declaration,
        evaluator_component_id=world.declaration.evaluator_component_id,
        evaluator_component_version=world.declaration.evaluator_component_version,
        trusted_clock=lambda: NOW,
        platform_observation_provider=current_platform_observation,
        library=world.library,
        lifecycle_store=lifecycle_store,
        attestation_store=attestation_store,
        taint_store=taint_store,
        evidence_resolver=lambda descriptor: world.catalog[descriptor.descriptor_id.value],
        binding_repo_root=world.root,
        conflict_assessor=world.evaluator._conflict_assessor,
        retriever_actor=world.evaluator.retriever_actor,
        consumer_actor=world.evaluator.consumer_actor,
        score_provider_actor=world.evaluator.score_provider_actor,
    )
    compatibility_context = create_compatibility_context(
        evaluator=evaluator,
        authority_handle=world.handle,
        observation=world.observation,
        library_snapshot=library_snapshot,
        lifecycle_snapshot=lifecycle_store.snapshot(),
        consumer_actor=evaluator.consumer_actor,
    )
    decisions = tuple(
        evaluate_compatibility(
            evaluator=evaluator,
            context=compatibility_context,
            descriptor=item[1],
            index_entry=item[2],
        )
        for item in supported
    )
    decision = decisions[0]
    # One scan over the whole selected set, not one per subject: a conflict is a
    # relation between candidates, so a scan that saw one candidate at a time
    # could not report one.
    conflict_scan = evaluate_conflicts(
        evaluator=evaluator,
        context=compatibility_context,
        decisions=decisions,
        descriptors=tuple(item[1] for item in supported),
        considered_index_entries=tuple(item[2] for item in supported),
        proposals=(),
    )
    stage2_records = tuple(
        revalidate_before_loading(
            evaluator=evaluator,
            context=compatibility_context,
            descriptor=item[1],
            original_decision=decisions[index],
        )
        for index, item in enumerate(supported)
    )
    stage2 = stage2_records[0]
    history_records = [compatibility_context]
    for item in decisions:
        history_records.extend((item.evidence, item))
    history_records.append(conflict_scan)
    history_records.extend(stage2_records)
    for record in history_records:
        compatibility_history.append_record(
            record, expected_parent_anchor=compatibility_history.current_anchor()
        )
    consumption_bindings = tuple(
        GF.bind_consumption_evidence(
            descriptor=item[1],
            original_decision=decisions[index],
            before_loading=stage2_records[index],
            conflict_scan=conflict_scan,
        )
        for index, item in enumerate(supported)
    )
    consumption_binding = consumption_bindings[0]
    raw_probe = GF.configured_revalidation_probe(
        evaluator=evaluator,
        context=compatibility_context,
        bindings=consumption_bindings,
    )
    durable_probe = GF.configured_durable_revalidation_probe(
        evaluator=evaluator,
        context=compatibility_context,
        bindings=consumption_bindings,
        compatibility_history=compatibility_history,
    )

    gate_now = [gate_time]
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=world.handle,
        evaluator_identity=AuthorityIdentity("point-of-use-gate-evaluator"),
        evaluator_component_id="point-of-use-gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version=POLICY,
        trusted_clock=lambda: gate_now[0],
    )
    actors = (
        ActorIdentity("point-of-use-producer"),
        ActorIdentity("point-of-use-retriever"),
        ActorIdentity("point-of-use-consumer"),
    )
    grant = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",),
        policy_version=POLICY,
    )
    requested = A.RequestedEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",)
    )

    def head_reader():
        lifecycle = lifecycle_store.current_anchor()
        provenance = attestation_store.current_anchor()
        taint = taint_store.current_anchor()
        return {
            "boundary_ref": boundary_ref,
            "heads": {
                "lifecycle": {"anchor_sha256": lifecycle.ordered_log_root_sha256, "sequence": lifecycle.entry_count},
                "provenance": {"anchor_sha256": provenance.ordered_log_root_sha256, "sequence": provenance.entry_count},
                "taint": {"anchor_sha256": taint.ordered_log_root_sha256, "sequence": taint.entry_count},
                "admission_decision": {"anchor_sha256": journal.current_anchor(), "sequence": len(journal._digests())},
                "retrieval_causal": {"anchor_sha256": causal_history.current_anchor(), "sequence": causal_history.current_sequence()},
                "compatibility": {"anchor_sha256": compatibility_history.current_anchor(), "sequence": compatibility_history.current_sequence()},
                "boundary": {"anchor_sha256": knowledge_store.current_anchor(), "sequence": knowledge_store.current_sequence()},
            },
        }

    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version=POLICY,
        run_id=run_id,
        attempt_id=attempt_id,
        repository_revision="a" * 40,
        environment_profile_id="production-point-of-use",
        trusted_clock=lambda: gate_now[0],
        taint_probe=lambda item: A.TaintFinding(
            consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
        ),
        provenance_probe=lambda item: True,
        lifecycle_probe=lambda item: True,
        compatibility_probe=raw_probe,
        boundary_probe=lambda item: item.to_dict() == boundary_ref.to_dict(),
        grant_probe=lambda: grant,
        head_reader=head_reader,
        producer_actor=actors[0],
        retriever_actor=actors[1],
        consumer_actor=actors[2],
    )
    context_ref = GF.consumer_context_ref_of(compatibility_context)
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=subjects)
    publication = A.evaluate_publication_gate(
        controller, subject_refs=subjects, requested=requested, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        controller,
        subject_refs=subjects,
        consumer_context_ref=context_ref,
        boundary_ref=boundary_ref,
        frozen_candidate_set_ref=FROZEN_SET_REF,
        requested=requested,
        predecessor=publication,
    )
    consumption = A.evaluate_consumption_gate(
        controller,
        subject_refs=subjects,
        consumer_context_ref=context_ref,
        boundary_ref=boundary_ref,
        requested=requested,
        predecessor=retrieval,
    )
    role_map = {
        GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
        GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
    }
    verifier_declaration = AC.create_gate_evaluator_declaration(
        authority_handle=world.handle,
        evaluator_identity=AuthorityIdentity("point-of-use-gate-evaluator"),
        evaluator_component_id="point-of-use-gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles=role_map,
        policy_version=POLICY,
        trusted_clock=lambda: gate_now[0],
    )
    entitlements = {gate: (verifier_declaration, actors) for gate in GateKind}
    chain = A.build_gate_decision_chain(
        ingestion=ingestion,
        publication=publication,
        retrieval=retrieval,
        consumption=consumption,
        entitlements=entitlements,
    )
    chain_evidence = S.commit_gate_chain(
        chain, store=journal, trusted_clock=lambda: gate_now[0]
    )
    from synapse.experiments.gold.coordination import read_current_authority_state
    fenced_state = read_current_authority_state(
        controller,
        fence=fence,
        participants=(
            lifecycle_store,
            attestation_store,
            taint_store,
            journal,
            causal_history,
            compatibility_history,
            knowledge_store,
        ),
    )
    handle = A.admit_for_consumption(
        chain,
        controller=controller,
        subject_refs=subjects,
        consumer_context_ref=context_ref,
        boundary_ref=boundary_ref,
        policy_version=POLICY,
        receipts=chain_evidence.receipts,
        fenced_state=fenced_state,
        journal=journal,
        entitlements=entitlements,
    )
    production = P.create_production_authority_binding(
        controller=controller,
        lifecycle_store=lifecycle_store,
        attestation_store=attestation_store,
        taint_store=taint_store,
        admission_journal=journal,
        admission_causal_history=causal_history,
        compatibility_history=compatibility_history,
        compatibility_probe=durable_probe,
        knowledge_store=knowledge_store,
        snapshot_attempt_id=attempt_id,
        snapshot_evaluator_declaration=snapshot_declaration,
        snapshot_actor_set=snapshot_actor_set,
        snapshot_independence_proof=snapshot_proof,
    )
    return SimpleNamespace(
        world=world,
        fence=fence,
        boundary=boundary,
        boundary_ref=boundary_ref,
        snapshot_root=snapshot_root,
        snapshot_transaction_id=transaction_id,
        knowledge_store=knowledge_store,
        snapshot_attempt_id=attempt_id,
        snapshot_evaluator_declaration=snapshot_declaration,
        snapshot_actor_set=snapshot_actor_set,
        snapshot_independence_proof=snapshot_proof,
        journal=journal,
        compatibility_history=compatibility_history,
        compatibility_probe=durable_probe,
        controller=controller,
        binding=production,
        chain=chain,
        evidence=chain_evidence,
        handle=handle,
        entitlements=entitlements,
        requested=requested,
        subject=subject,
        subjects=subjects,
        supported=supported,
        context_ref=context_ref,
        now=gate_now,
    )


def _controller(harness, *, probe):
    configuration = create_stage4_authority_configuration(
        platform_attester_actor=ActorIdentity(value="evidence-attester"),
        builder_actor=ActorIdentity(value="evidence-builder"),
        taint_classifier_authority=AuthorityIdentity(value="evidence-taint-classifier"),
        taint_reviewer_authority=AuthorityIdentity(value="evidence-taint-reviewer"),
        supersession_reviewer_authority=AuthorityIdentity(value="evidence-supersession"),
        revocation_reviewer_authority=AuthorityIdentity(value="evidence-revocation"),
        lifecycle_writer_actor=ActorIdentity(value="evidence-lifecycle-writer"),
        governing_human_authority=None,
    )
    declaration = AC.create_gate_evaluator_declaration(
        authority_handle=create_stage4_authority_handle(configuration),
        evaluator_identity=AuthorityIdentity(value="evidence-gate-authority"),
        evaluator_component_id="evidence-gate-evaluator",
        evaluator_component_version="synapse.stage4.gate-evaluator/v1",
        gate_roles={
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version=POLICY,
        trusted_clock=lambda: NOW,
    )
    grant = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version=POLICY
    )
    requested = A.RequestedEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",)
    )
    controller = A.configure_gate_controller(
        declaration=declaration,
        policy_version=POLICY,
        run_id=RunId("consumption-decision-run"),
        attempt_id=AttemptId("consumption-decision-attempt"),
        repository_revision="a" * 40,
        environment_profile_id="consumption-decision-env",
        trusted_clock=lambda: NOW,
        taint_probe=lambda item: A.TaintFinding(
            consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
        ),
        provenance_probe=lambda item: True,
        lifecycle_probe=lambda item: True,
        compatibility_probe=probe,
        boundary_probe=lambda item: True,
        grant_probe=lambda: grant,
        head_reader=lambda: {"boundary_ref": None, "heads": {}},
        producer_actor=ActorIdentity(value="evidence-producer"),
        retriever_actor=ActorIdentity(value="evidence-retriever"),
        consumer_actor=ActorIdentity(value="evidence-consumer"),
    )
    return controller, requested


def _consume(harness, *, probe, boundary_ref=None):
    """Run all four gates with the bound probe and return the last verdict."""

    controller, requested = _controller(harness, probe=probe)
    subject = GF.candidate_subject_ref(harness.descriptor)
    context_ref = GF.consumer_context_ref_of(harness.context)
    boundary = boundary_ref or HashBoundRef(
        kind=RefKind.ATOMIC_BOUNDARY,
        ref_id="evidence-boundary",
        schema_id="synapse.stage4.gold.atomic-snapshot-boundary/v1",
        sha256="e" * 64,
        byte_length=1,
        media_type="application/json",
    )
    ingestion = A.evaluate_ingestion_gate(controller, subject_refs=(subject,))
    publication = A.evaluate_publication_gate(
        controller, subject_refs=(subject,), requested=requested, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        controller, subject_refs=(subject,), consumer_context_ref=context_ref,
        boundary_ref=boundary, frozen_candidate_set_ref=FROZEN_SET_REF,
        requested=requested, predecessor=publication,
    )
    return A.evaluate_consumption_gate(
        controller, subject_refs=(subject,), consumer_context_ref=context_ref,
        boundary_ref=boundary, requested=requested, predecessor=retrieval,
    )


# ---------------------------------------------------------------------------
# The binding holds four records together or refuses them
# ---------------------------------------------------------------------------


def test_a_binding_derives_its_subject_from_the_descriptor(tmp_path: Path) -> None:
    """A binding cannot claim to be about a subject its descriptor does not name."""

    harness = _make_harness(tmp_path)
    binding = _binding(harness)
    assert binding.subject_ref == GF.candidate_subject_ref(harness.descriptor)


def test_a_binding_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        GF.ConsumptionEvidenceBinding()


def test_a_forged_binding_is_refused(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    genuine = _binding(harness)
    forged = object.__new__(GF.ConsumptionEvidenceBinding)
    for field in ("subject_ref", "descriptor", "original_decision", "before_loading", "conflict_scan"):
        object.__setattr__(forged, field, getattr(genuine, field))
    with pytest.raises(CompatibilityViolation) as caught:
        GF.validate_consumption_evidence_binding(forged)
    assert caught.value.failure_code is CompatibilityFailureCode.TRUSTED_OBJECT_FORGED


def test_a_stage_three_record_cannot_stand_in_for_the_stage_two_predecessor(tmp_path: Path) -> None:
    """The predecessor of a stage-3 check is stage 2, not another stage 3."""

    harness = _make_harness(tmp_path)
    before_loading, scan = _stage3_inputs(harness)
    stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
    )
    with pytest.raises(CompatibilityViolation) as caught:
        GF.bind_consumption_evidence(
            descriptor=harness.descriptor,
            original_decision=harness.decision,
            before_loading=stage3,
            conflict_scan=scan,
        )
    assert caught.value.failure_code is CompatibilityFailureCode.TOCTOU_REVALIDATION_FAILED


def test_a_stage_two_record_from_another_context_is_refused(tmp_path: Path) -> None:
    """Same object, same descriptor, same evaluator — a different context, refused.

    The records are wrong in exactly one way, which is what makes the test
    sharp. Two harnesses would also produce two decisions, but they would
    produce two evaluators as well, and the evaluator check would fire first;
    a record wrong in several ways is caught even when the check under test is
    gone, and then nothing has been demonstrated.

    What does the work is validating the decision against the stage-2 record's
    own context. That single line ties record, decision and descriptor together
    — a decision's identity is determined by its evaluator, context and
    descriptor — which is why the explicit id comparisons that used to sit
    beside it were unkillable and are gone.
    """

    harness = _make_harness(tmp_path)
    _, scan = _stage3_inputs(harness)

    # A second context from the *same* evaluator. Two harnesses would also give
    # two decisions, but they would also give two evaluators, and the evaluator
    # check would fire first — the record would be wrong in more ways than the
    # one under test.
    other_context = create_compatibility_context(
        evaluator=harness.evaluator,
        authority_handle=harness.handle,
        observation=_fresh_platform_observation(
            harness, environment_version="synapse.stage4.environment/v777"
        ),
        library_snapshot=harness.context.library_snapshot,
        lifecycle_snapshot=harness.context.lifecycle_snapshot,
        consumer_actor=harness.evaluator.consumer_actor,
    )
    other_decision = evaluate_compatibility(
        evaluator=harness.evaluator,
        context=other_context,
        descriptor=harness.descriptor,
        index_entry=harness.entry,
    )
    other_stage2 = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=other_context,
        descriptor=harness.descriptor,
        original_decision=other_decision,
    )
    assert other_stage2.descriptor_id == harness.descriptor.descriptor_id
    assert other_decision.decision_id != harness.decision.decision_id

    with pytest.raises(CompatibilityViolation) as caught:
        GF.bind_consumption_evidence(
            descriptor=harness.descriptor,
            original_decision=harness.decision,
            before_loading=other_stage2,
            conflict_scan=scan,
        )
    assert caught.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH


@pytest.mark.parametrize("substitute", [None, object(), "scan"])
def test_a_binding_requires_an_exact_conflict_scan(substitute, tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    before_loading, _ = _stage3_inputs(harness)
    with pytest.raises(CompatibilityViolation) as caught:
        GF.bind_consumption_evidence(
            descriptor=harness.descriptor,
            original_decision=harness.decision,
            before_loading=before_loading,
            conflict_scan=substitute,
        )
    assert caught.value.failure_code is CompatibilityFailureCode.CONFLICT_SCAN_INCOMPLETE


# ---------------------------------------------------------------------------
# The probe answers only about what it was configured for
# ---------------------------------------------------------------------------


def test_the_bound_probe_admits_a_subject_whose_revalidation_passes(tmp_path: Path) -> None:
    """The whole point: the gate's compatibility answer comes from a real record."""

    harness = _make_harness(tmp_path)
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=(_binding(harness),)
    )
    decision = _consume(harness, probe=probe)
    assert decision.decision_kind is GateDecisionKind.ADMIT
    assert decision.admitted


def test_the_production_probe_persists_stage_three_before_answering(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path / "world")
    fence = fence_for(tmp_path / "authority")
    history = FileCompatibilityStore(tmp_path / "compatibility", mutation_fence=fence)
    probe = GF.configured_durable_revalidation_probe(
        evaluator=harness.evaluator,
        context=harness.context,
        bindings=(_binding(harness),),
        compatibility_history=history,
    )
    subject = GF.candidate_subject_ref(harness.descriptor)
    context_ref = GF.consumer_context_ref_of(harness.context)

    with pytest.raises(CompatibilityViolation):
        probe(subject, context_ref)
    assert history.current_sequence() == 0

    with fence.exclusive() as guard:
        with store_transaction(fence, guard=guard) as ticket:
            with probe.active(ticket):
                finding = probe(subject, context_ref)
                assert finding.compatible
                assert history.current_sequence() == 1
                assert probe.receipts[0].record_ref.ref_id == probe.records[0].revalidation_id.digest_sha256


def test_the_sealed_point_of_use_binding_commits_stage_three_and_consumption_atomically(
    tmp_path: Path,
) -> None:
    from synapse.experiments.gold import point_of_use as P

    case = production_point_of_use_case(tmp_path)
    before_epoch = case.fence.current_epoch()
    before_compatibility = case.compatibility_history.current_sequence()
    before_admission = len(case.journal._digests())

    knowledge = P.admit_for_use_now(
        case.handle,
        binding=case.binding,
        chain=case.chain,
        evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
    )

    P.validate_current_admitted_knowledge(knowledge)
    assert case.fence.current_epoch() == before_epoch + 2
    assert knowledge.observed_epoch == before_epoch + 2
    assert case.compatibility_history.current_sequence() == before_compatibility + 1
    assert len(case.journal._digests()) == before_admission + 1
    assert knowledge.boundary_ref.to_dict() == case.boundary_ref.to_dict()
    assert knowledge.subject_refs == (case.subject,)
    assert P.require_current_point_of_use_evidence(
        knowledge, binding=case.binding
    ) is None

    with store_transaction(case.fence):
        pass
    with pytest.raises(A.AdmissionViolation) as stale:
        P.require_current_point_of_use_evidence(knowledge, binding=case.binding)
    assert stale.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_raw_probe_arguments_cannot_create_current_authority(tmp_path: Path) -> None:
    from synapse.experiments.gold import point_of_use as P

    case = production_point_of_use_case(tmp_path)
    with pytest.raises(TypeError):
        P.admit_for_use_now(
            case.handle,
            controller=case.controller,
            journal=case.journal,
            fence=case.fence,
            chain=case.chain,
            evidence=case.evidence,
            entitlements=case.entitlements,
            requested=case.requested,
        )


def test_production_binding_is_factory_sealed(tmp_path: Path) -> None:
    from synapse.experiments.gold import point_of_use as P

    with pytest.raises(TypeError):
        P.ProductionAuthorityBinding()
    case = production_point_of_use_case(tmp_path)
    object.__setattr__(case.binding, "compatibility_history", object())
    with pytest.raises(A.AdmissionViolation) as caught:
        P.validate_production_authority_binding(case.binding)
    assert caught.value.failure_code is A.AdmissionFailureCode.TYPE_MISMATCH


def test_the_probe_refuses_a_question_about_another_consumer_context(tmp_path: Path) -> None:
    """An answer computed for one consumer must not be handed to another."""

    harness = _make_harness(tmp_path / "own")
    other = _make_harness(
        tmp_path / "other", context_environment_version="synapse.stage4.environment/v999"
    )
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=(_binding(harness),)
    )
    assert GF.consumer_context_ref_of(other.context) != GF.consumer_context_ref_of(harness.context)
    with pytest.raises(CompatibilityViolation) as caught:
        probe(
            GF.candidate_subject_ref(harness.descriptor),
            GF.consumer_context_ref_of(other.context),
        )
    assert caught.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH


def test_a_subject_with_no_binding_blocks_as_an_unanswered_dependency(tmp_path: Path) -> None:
    """Absent evidence is neither compatibility nor incompatibility.

    The gate has a vocabulary for a dependency that could not answer, and it
    blocks on it. Fabricating a finding would put a verdict in the durable
    record that no evaluator ever reached.
    """

    harness = _make_harness(tmp_path)
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=()
    )
    with pytest.raises(A.GateDependencyUnavailable):
        probe(
            GF.candidate_subject_ref(harness.descriptor),
            GF.consumer_context_ref_of(harness.context),
        )

    decision = _consume(harness, probe=probe)
    assert not decision.admitted
    assert A.ConsumptionReason.DEPENDENCY_UNAVAILABLE.value in decision.reason_codes


def test_a_binding_prepared_for_another_context_is_refused_at_build_time(tmp_path: Path) -> None:
    """The mismatch is caught when the probe is wired, not when the gate asks.

    ``revalidate_before_consumption`` would also refuse this, because a stage-3
    record must extend a stage-2 record made in the same context. That refusal
    arrives during gate evaluation, when the operator who mis-wired the probe is
    no longer looking. Catching it at construction is a different check at a
    different time, and until now nothing exercised it — the mutant that deleted
    it survived, which is what a rule with no test looks like.
    """

    harness = _make_harness(tmp_path)
    binding = _binding(harness)
    other_context = create_compatibility_context(
        evaluator=harness.evaluator,
        authority_handle=harness.handle,
        observation=_fresh_platform_observation(
            harness, environment_version="synapse.stage4.environment/v555"
        ),
        library_snapshot=harness.context.library_snapshot,
        lifecycle_snapshot=harness.context.lifecycle_snapshot,
        consumer_actor=harness.evaluator.consumer_actor,
    )
    with pytest.raises(CompatibilityViolation) as caught:
        GF.configured_revalidation_probe(
            evaluator=harness.evaluator, context=other_context, bindings=(binding,)
        )
    assert caught.value.failure_code is CompatibilityFailureCode.CONTEXT_MISMATCH


def test_the_context_reference_is_the_context_identity_not_one_of_its_inputs(
    tmp_path: Path,
) -> None:
    """The gate's name for a consumer must separate every context that differs.

    A context binds an observation, a policy version, an environment, a tool
    version and a scope. Naming it after any single one of those would collapse
    two consumers that agree on that input and differ elsewhere — and the probe
    uses this name to decide whether an answer belongs to the consumer asking.
    So the reference carries the context's *own* identity, which is computed
    from all of them.
    """

    harness = _make_harness(tmp_path)
    ref = GF.consumer_context_ref_of(harness.context)
    assert ref.ref_id == harness.context.context_id.digest_sha256
    assert ref.schema_id == harness.context.schema_version
    assert ref.ref_id != harness.context.observation_sha256


def test_two_bindings_cannot_claim_the_same_subject(tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    binding = _binding(harness)
    with pytest.raises(CompatibilityViolation) as caught:
        GF.configured_revalidation_probe(
            evaluator=harness.evaluator, context=harness.context, bindings=(binding, binding)
        )
    assert caught.value.failure_code is CompatibilityFailureCode.SUBJECT_DESCRIPTOR_MISMATCH


@pytest.mark.parametrize("substitute", [None, "bindings", [1]])
def test_the_probe_requires_an_exact_tuple_of_bindings(substitute, tmp_path: Path) -> None:
    harness = _make_harness(tmp_path)
    with pytest.raises(CompatibilityViolation):
        GF.configured_revalidation_probe(
            evaluator=harness.evaluator, context=harness.context, bindings=substitute
        )


# ---------------------------------------------------------------------------
# The revalidation happens when the gate asks
# ---------------------------------------------------------------------------


def test_the_revalidation_runs_at_the_moment_of_the_question(tmp_path: Path, monkeypatch) -> None:
    """A record produced in advance describes a moment that has passed.

    Counting the calls is the only way to tell a probe that revalidates from one
    that hands back something it computed earlier, and the difference is the
    entire reason stage 3 exists.
    """

    harness = _make_harness(tmp_path)
    calls: list[int] = []
    original = GF.revalidate_before_consumption

    def counted(**kwargs):
        calls.append(1)
        return original(**kwargs)

    monkeypatch.setattr(GF, "revalidate_before_consumption", counted)
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=(_binding(harness),)
    )
    assert calls == [], "building the probe must not revalidate anything"

    subject = GF.candidate_subject_ref(harness.descriptor)
    context_ref = GF.consumer_context_ref_of(harness.context)
    probe(subject, context_ref)
    probe(subject, context_ref)
    assert len(calls) == 2, "each question gets its own revalidation"


def test_a_revoked_subject_blocks_through_a_real_failed_revalidation(tmp_path: Path) -> None:
    """The refusal is produced by the domain, not by a flag in the test.

    A revoked object's stage-3 record fails, the projection reports it as drift,
    and the consumption gate blocks. Nothing in this path was told to say no.
    """

    harness = _make_harness(tmp_path, revoked=True)
    before_loading, scan = _stage3_inputs(harness)
    binding = GF.bind_consumption_evidence(
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
        conflict_scan=scan,
    )
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=(binding,)
    )
    finding = probe(binding.subject_ref, GF.consumer_context_ref_of(harness.context))
    assert finding.drifted or not finding.compatible

    decision = _consume(harness, probe=probe)
    assert not decision.admitted


def test_an_unresolved_conflict_reaches_the_gate_as_unresolved(tmp_path: Path) -> None:
    """Conflict is a dimension the gate declares it checked, so it must arrive."""

    harness = _make_harness(tmp_path, extra_unresolved=1)
    before_loading = revalidate_before_loading(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
    )
    incomplete = evaluate_conflicts(
        evaluator=harness.evaluator,
        context=harness.context,
        decisions=(harness.decision,),
        descriptors=(harness.descriptor,),
        considered_index_entries=harness.library.search_index(),
        proposals=(),
    )
    assert incomplete.decision_kind is not ConflictDecisionKind.NO_CONFLICT_FOUND

    binding = GF.bind_consumption_evidence(
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
        conflict_scan=incomplete,
    )
    probe = GF.configured_revalidation_probe(
        evaluator=harness.evaluator, context=harness.context, bindings=(binding,)
    )
    finding = probe(binding.subject_ref, GF.consumer_context_ref_of(harness.context))
    assert finding.conflicts_unresolved

    decision = _consume(harness, probe=probe)
    assert not decision.admitted


def test_a_passing_revalidation_is_what_the_admitting_path_rests_on(tmp_path: Path) -> None:
    """Guards the positive test above from passing for the wrong reason."""

    harness = _make_harness(tmp_path)
    before_loading, _ = _stage3_inputs(harness)
    stage3 = revalidate_before_consumption(
        evaluator=harness.evaluator,
        context=harness.context,
        descriptor=harness.descriptor,
        original_decision=harness.decision,
        before_loading=before_loading,
    )
    assert stage3.outcome is RevalidationOutcome.PASSED
