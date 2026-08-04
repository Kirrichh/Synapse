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
from pathlib import Path

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
    AuthorityIdentity,
    AuthorityRole,
    GateDecisionKind,
    GateKind,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)

from tests.test_stage4_gold_compatibility import _fresh_platform_observation, _make_harness

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"


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
