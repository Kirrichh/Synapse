"""Stage 4 Patch 8 — the four §22 authority gates and ConsumptionDecision.

Covers the §22 acceptance checks: a positive and negative matrix for each gate,
constructor/replace/low-level mutation, stale decision context mismatch,
source-taint and poisoned memory, scope/oracle/policy expansion, and unavailable
dependency behaviour. Every mandatory mutant has a named killing test at the end.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    GateCheckedDimension,
    GateDecisionKind,
    GateKind,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.retrieval import gate_selectable_candidates

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gold" / "admission_matrix_v1.json"
NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def ref(kind: RefKind, name: str, payload: bytes = b"p") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


def _key(item: HashBoundRef) -> str:
    return f"{item.kind.value}\x00{item.ref_id}\x00{item.sha256}"


SUBJECTS = tuple(sorted((ref(RefKind.ARTIFACT, "obj-a"), ref(RefKind.BINDING, "obj-b")), key=_key))
CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx")
BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-1")

GRANT = A.GrantEnvelope(
    scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version="policy-v1"
)
REQUEST = A.RequestedEnvelope(scopes=("repo:x",), capabilities=("read",), oracles=("swebench",))

CLEAN_TAINT = A.TaintFinding(
    consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
)
CLEAN_COMPAT = A.CompatibilityFinding(
    compatible=True, evidence_complete=True, drifted=False, conflicts_unresolved=False
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


def controller(
    *, taint=None, provenance=None, lifecycle=None, compat=None, boundary=None, grant=None,
    authority: str = "gate-authority", policy: str = "policy-v1",
) -> A.ConfiguredGateController:
    return A.configure_gate_controller(
        authority_handle=authority_handle(),
        authority_identity=AuthorityIdentity(value=authority),
        authority_role=AuthorityRole.PUBLICATION_REVIEWER,
        policy_version=policy,
        trusted_clock=lambda: NOW,
        taint_probe=taint or (lambda item: CLEAN_TAINT),
        provenance_probe=provenance or (lambda item: True),
        lifecycle_probe=lifecycle or (lambda item: True),
        compatibility_probe=compat or (lambda item: CLEAN_COMPAT),
        boundary_probe=boundary or (lambda item: True),
        grant_probe=grant or (lambda: GRANT),
        producer_actor=ActorIdentity(value="producer"),
        retriever_actor=ActorIdentity(value="retriever"),
        consumer_actor=ActorIdentity(value="consumer"),
    )


def full_chain(control, requested: A.RequestedEnvelope = REQUEST):
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=requested, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        control, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        requested=requested, predecessor=publication,
    )
    consumption = A.evaluate_consumption_gate(
        control, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, requested=requested, predecessor=retrieval,
    )
    return ingestion, publication, retrieval, consumption


# ---------------------------------------------------------------------------
# Vocabulary, precedence and required dimensions
# ---------------------------------------------------------------------------


# Three reasons name the same condition at more than one gate: a request for
# human review, an unavailable dependency, and a quarantined subject. They are
# still separate enum members per gate, so a reason from one vocabulary is never
# accepted by another gate.
_DELIBERATELY_SHARED_REASON_NAMES = frozenset(
    {"HUMAN_REVIEW_REQUIRED", "DEPENDENCY_UNAVAILABLE", "SUBJECT_QUARANTINED"}
)


def test_every_gate_owns_a_distinct_closed_vocabulary() -> None:
    vocabularies = {gate: gate_reason_values(gate) for gate in GateKind}
    assert all(vocabularies.values())
    for gate, values in vocabularies.items():
        for other, other_values in vocabularies.items():
            if gate is other:
                continue
            assert (values & other_values) <= _DELIBERATELY_SHARED_REASON_NAMES


def test_a_reason_is_never_valid_outside_its_own_gate() -> None:
    for gate in GateKind:
        for other in GateKind:
            if other is gate:
                continue
            foreign = gate_reason_values(other) - gate_reason_values(gate)
            for name in sorted(foreign)[:3]:
                with pytest.raises(A.AdmissionViolation) as excinfo:
                    A.resolve_decision_kind(gate, (name,))
                assert excinfo.value.failure_code is A.AdmissionFailureCode.UNKNOWN_REASON_CODE


def gate_reason_values(gate: GateKind) -> set[str]:
    return {item.value for item in A.gate_reason_vocabulary(gate)}


def test_consumption_checks_every_declared_dimension() -> None:
    assert A.required_dimensions(GateKind.CONSUMPTION) == frozenset(GateCheckedDimension)
    for gate in (GateKind.INGESTION, GateKind.PUBLICATION, GateKind.RETRIEVAL):
        assert A.required_dimensions(gate) < frozenset(GateCheckedDimension)


def test_blocking_reason_always_outranks_an_admitting_one() -> None:
    assert A.resolve_decision_kind(GateKind.CONSUMPTION, ("REVALIDATION_PASSED",)) is GateDecisionKind.ADMIT
    mixed = tuple(sorted(("REVALIDATION_PASSED", "LIFECYCLE_CHANGED")))
    assert A.resolve_decision_kind(GateKind.CONSUMPTION, mixed) is GateDecisionKind.REJECT


def test_quarantine_outranks_review_and_reject_outranks_nothing_else() -> None:
    assert A.resolve_decision_kind(
        GateKind.CONSUMPTION, tuple(sorted(("SUBJECT_QUARANTINED", "LIFECYCLE_CHANGED")))
    ) is GateDecisionKind.QUARANTINE
    assert A.resolve_decision_kind(
        GateKind.CONSUMPTION, ("HUMAN_REVIEW_REQUIRED",)
    ) is GateDecisionKind.REQUIRE_REVIEW


@pytest.mark.parametrize("reasons", [(), ("NOT_A_REASON",), ("SOURCE_CLASSIFIED",)])
def test_unusable_reason_sets_are_refused(reasons) -> None:
    with pytest.raises(A.AdmissionViolation):
        A.resolve_decision_kind(GateKind.CONSUMPTION, reasons)


# ---------------------------------------------------------------------------
# Positive path
# ---------------------------------------------------------------------------


def test_clean_path_admits_at_every_gate() -> None:
    decisions = full_chain(controller())
    assert all(item.admitted for item in decisions)
    assert [item.gate_kind for item in decisions] == list(GateKind)
    assert len({item.gate_decision_id.value for item in decisions}) == 4


def test_chain_binds_four_decisions_in_order() -> None:
    ingestion, publication, retrieval, consumption = full_chain(controller())
    chain = A.build_gate_decision_chain(
        ingestion=ingestion, publication=publication, retrieval=retrieval, consumption=consumption
    )
    assert chain.admitted
    assert chain.blocking_reasons() == ()
    admitted = A.admitted_subject_refs(
        chain, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    )
    assert admitted == SUBJECTS


def test_each_decision_records_its_required_dimensions() -> None:
    for decision in full_chain(controller()):
        recorded = {GateCheckedDimension(item) for item in decision.checked_dimensions}
        assert A.required_dimensions(decision.gate_kind) <= recorded


# ---------------------------------------------------------------------------
# Negative matrix from the committed fixture
# ---------------------------------------------------------------------------


def _matrix_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def _controller_for(probes: dict) -> tuple[A.ConfiguredGateController, A.RequestedEnvelope]:
    def raiser(*_args, **_kwargs):
        raise OSError("dependency unavailable")

    taint = A.TaintFinding(
        consumable=probes.get("taint_consumable", True),
        chain_complete=probes.get("taint_chain_complete", True),
        quarantined=probes.get("taint_quarantined", False),
        blocks_publication=probes.get("taint_blocks_publication", False),
    )
    compat = A.CompatibilityFinding(
        compatible=probes.get("compat_compatible", True),
        evidence_complete=probes.get("compat_evidence_complete", True),
        drifted=probes.get("compat_drifted", False),
        conflicts_unresolved=probes.get("compat_conflicts", False),
    )
    grant = A.GrantEnvelope(
        scopes=GRANT.scopes, capabilities=GRANT.capabilities, oracles=GRANT.oracles,
        policy_version=probes.get("grant_policy", "policy-v1"),
    )
    requested = REQUEST
    if "expand" in probes:
        fields = {"scopes": REQUEST.scopes, "capabilities": REQUEST.capabilities, "oracles": REQUEST.oracles}
        fields[probes["expand"]] = tuple(sorted(fields[probes["expand"]] + ("extra",)))
        requested = A.RequestedEnvelope(**fields)
    control = controller(
        taint=raiser if probes.get("taint_raises") else (lambda item: taint),
        provenance=lambda item: probes.get("provenance", True),
        lifecycle=lambda item: probes.get("lifecycle", True),
        compat=raiser if probes.get("compat_raises") else (lambda item: compat),
        boundary=lambda item: probes.get("boundary", True),
        grant=raiser if probes.get("grant_raises") else (lambda: grant),
    )
    return control, requested


@pytest.mark.parametrize("case", _matrix_cases(), ids=lambda item: item["id"])
def test_admission_matrix_case(case: dict) -> None:
    control, requested = _controller_for(case["probes"])
    gate = GateKind(case["gate"])
    decisions = dict(zip(GateKind, full_chain(control, requested=requested)))
    decision = decisions[gate]
    assert decision.decision_kind is GateDecisionKind(case["expect_kind"]), decision.reason_codes
    for expected in case["expect_reasons"]:
        assert expected in decision.reason_codes


def test_matrix_covers_all_four_gates() -> None:
    covered = {case["gate"] for case in _matrix_cases()}
    assert covered == {gate.value for gate in GateKind}


# ---------------------------------------------------------------------------
# Unavailable dependency is never an admission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe", ["taint", "provenance", "lifecycle", "compat", "boundary", "grant"]
)
def test_probe_failure_never_admits(probe: str) -> None:
    def raiser(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    control = controller(**{probe: raiser})
    consumption = full_chain(control)[3]
    assert not consumption.admitted
    assert "DEPENDENCY_UNAVAILABLE" in consumption.reason_codes


@pytest.mark.parametrize("bogus", ["yes", 1, None, object()])
def test_non_exact_probe_result_never_admits(bogus) -> None:
    control = controller(taint=lambda item: bogus)
    consumption = full_chain(control)[3]
    assert not consumption.admitted
    assert "DEPENDENCY_UNAVAILABLE" in consumption.reason_codes


def test_probe_raising_base_exception_never_admits() -> None:
    def raiser(*_args, **_kwargs):
        raise KeyboardInterrupt()

    control = controller(lifecycle=raiser)
    consumption = full_chain(control)[3]
    assert not consumption.admitted


# ---------------------------------------------------------------------------
# Bypass paths
# ---------------------------------------------------------------------------


def test_decision_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError):
        A.GateDecision()  # type: ignore[call-arg]


def test_decision_cannot_be_copied_into_existence() -> None:
    consumption = full_chain(controller())[3]
    with pytest.raises(TypeError):
        copy.copy(consumption)


def test_dataclasses_replace_cannot_forge_an_admission() -> None:
    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    with pytest.raises((TypeError, A.AdmissionViolation)):
        forged = replace(blocked, decision_kind=GateDecisionKind.ADMIT)
        A.validate_gate_decision(forged)


def test_low_level_decision_kind_mutation_is_detected() -> None:
    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    object.__setattr__(blocked, "decision_kind", GateDecisionKind.ADMIT)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_gate_decision(blocked)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.CONTRADICTORY_DIAGNOSTICS


def test_low_level_reason_rewrite_is_detected() -> None:
    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    object.__setattr__(blocked, "decision_kind", GateDecisionKind.ADMIT)
    object.__setattr__(blocked, "reason_codes", ("REVALIDATION_PASSED",))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_gate_decision(blocked)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_dropping_a_checked_dimension_is_detected() -> None:
    consumption = full_chain(controller())[3]
    object.__setattr__(consumption, "checked_dimensions", ("SOURCE_TAINT",))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_gate_decision(consumption)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DIMENSION_NOT_CHECKED


def test_unknown_dimension_value_is_refused() -> None:
    consumption = full_chain(controller())[3]
    object.__setattr__(consumption, "checked_dimensions", ("NOT_A_DIMENSION",))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_gate_decision(consumption)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DIMENSION_NOT_CHECKED


def test_gate_authority_cannot_be_a_participant() -> None:
    for participant in ("producer", "retriever", "consumer"):
        with pytest.raises(A.AdmissionViolation) as excinfo:
            controller(authority=participant)
        assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT


# ---------------------------------------------------------------------------
# Stale decisions and the consumption barrier
# ---------------------------------------------------------------------------


def test_only_a_consumption_decision_opens_the_barrier() -> None:
    _, _, retrieval, _ = full_chain(controller())
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_consumption_admitted(
            retrieval, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.GATE_KIND_MISMATCH


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"consumer_context_ref": ref(RefKind.ARTIFACT, "other-ctx")}, A.AdmissionFailureCode.STALE_DECISION),
        ({"boundary_ref": ref(RefKind.ATOMIC_BOUNDARY, "other-boundary")}, A.AdmissionFailureCode.STALE_DECISION),
        ({"policy_version": "policy-v9"}, A.AdmissionFailureCode.POLICY_VERSION_MISMATCH),
        ({"subject_refs": (ref(RefKind.ARTIFACT, "other-obj"),)}, A.AdmissionFailureCode.SUBJECT_MISMATCH),
    ],
)
def test_barrier_refuses_a_decision_from_another_world(override, expected) -> None:
    consumption = full_chain(controller())[3]
    args = dict(
        subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    )
    args.update(override)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_consumption_admitted(consumption, **args)
    assert excinfo.value.failure_code is expected


def test_consumption_refuses_a_retrieval_from_another_context() -> None:
    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        control, subject_refs=SUBJECTS, consumer_context_ref=ref(RefKind.ARTIFACT, "ctx-a"),
        requested=REQUEST, predecessor=publication,
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.evaluate_consumption_gate(
            control, subject_refs=SUBJECTS, consumer_context_ref=ref(RefKind.ARTIFACT, "ctx-b"),
            boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.STALE_DECISION


def test_blocked_chain_admits_nothing_at_all() -> None:
    decisions = full_chain(controller(lifecycle=lambda item: False))
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    assert not chain.admitted
    assert chain.blocking_reasons()
    assert A.admitted_subject_refs(
        chain, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    ) == ()


def test_chain_refuses_decisions_from_different_authorities() -> None:
    first = full_chain(controller())
    other = full_chain(controller(authority="another-authority"))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.build_gate_decision_chain(
            ingestion=first[0], publication=first[1], retrieval=first[2], consumption=other[3]
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.GATE_DECISION_REUSED


# ---------------------------------------------------------------------------
# Retrieval wiring: the gate runs before a candidate becomes selectable
# ---------------------------------------------------------------------------


def test_retrieval_gate_runs_before_the_selectable_set() -> None:
    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    selectable = gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        requested=REQUEST, publication_decision=publication,
    )
    assert selectable == SUBJECTS


def test_rejected_candidate_never_becomes_selectable() -> None:
    control = controller(lifecycle=lambda item: False)
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    assert gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        requested=REQUEST, publication_decision=publication,
    ) == ()


def test_selectable_set_requires_a_configured_controller() -> None:
    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        gate_selectable_candidates(
            controller=object(), candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            requested=REQUEST, publication_decision=publication,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.TRUSTED_OBJECT_FORGED


# ---------------------------------------------------------------------------
# Mandatory mutation killers for Patch 8
# ---------------------------------------------------------------------------


def test_mutant_one_gate_decision_reused_for_all_phases() -> None:
    """A verdict from one gate must never satisfy another."""

    ingestion, publication, retrieval, _ = full_chain(controller())
    for decision, wrong_gate in (
        (ingestion, GateKind.RETRIEVAL),
        (publication, GateKind.INGESTION),
        (retrieval, GateKind.PUBLICATION),
    ):
        with pytest.raises(A.AdmissionViolation) as excinfo:
            A.require_gate_predecessor(decision, expected_gate=wrong_gate, subject_refs=SUBJECTS)
        assert excinfo.value.failure_code is A.AdmissionFailureCode.GATE_DECISION_REUSED


def test_mutant_exception_defaults_to_admit() -> None:
    """An error inside any probe must never resolve to ADMIT."""

    def raiser(*_args, **_kwargs):
        raise RuntimeError("probe exploded")

    for probe in ("taint", "provenance", "lifecycle", "compat", "boundary", "grant"):
        consumption = full_chain(controller(**{probe: raiser}))[3]
        assert consumption.decision_kind is not GateDecisionKind.ADMIT


def test_mutant_old_admission_accepted_after_revoke() -> None:
    """An object revoked after retrieval must not ride an earlier admission."""

    calls = {"count": 0}

    def revoked_at_consumption(_item):
        calls["count"] += 1
        return calls["count"] <= 4

    decisions = full_chain(controller(lifecycle=revoked_at_consumption))
    assert decisions[2].admitted
    assert not decisions[3].admitted
    assert "LIFECYCLE_CHANGED" in decisions[3].reason_codes
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    assert A.admitted_subject_refs(
        chain, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    ) == ()


def test_mutant_successful_but_poisoned_source_counted_as_safe() -> None:
    """A source that executed successfully is still poisoned if its taint says so."""

    poisoned = A.TaintFinding(
        consumable=False, chain_complete=True, quarantined=False, blocks_publication=False
    )
    decisions = full_chain(controller(taint=lambda item: poisoned))
    assert not decisions[2].admitted
    assert "TAINT_BLOCKS_RETRIEVAL" in decisions[2].reason_codes
    assert not decisions[3].admitted
    assert "TAINT_BLOCKS_CONSUMPTION" in decisions[3].reason_codes


def test_mutant_taint_relaxed_by_success_without_authority() -> None:
    """S4-MUT-TAINT-SUCCESS-01: an incomplete chain is never read as permissive."""

    reduced_without_chain = A.TaintFinding(
        consumable=True, chain_complete=False, quarantined=False, blocks_publication=False
    )
    consumption = full_chain(controller(taint=lambda item: reduced_without_chain))[3]
    assert not consumption.admitted
    assert "TAINT_CHAIN_INCOMPLETE" in consumption.reason_codes


def test_mutant_rejected_item_enters_prompt_or_replay() -> None:
    """Nothing a gate rejected may reach a worker context or a replay input."""

    control = controller(compat=lambda item: A.CompatibilityFinding(
        compatible=False, evidence_complete=True, drifted=False, conflicts_unresolved=False
    ))
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    assert A.admitted_subject_refs(
        chain, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    ) == ()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    assert gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        requested=REQUEST, publication_decision=publication,
    ) == ()


# ---------------------------------------------------------------------------
# Low-level deserialization is a bypass path, so restoration is anchored
# ---------------------------------------------------------------------------


def _payload(decision: A.GateDecision) -> dict:
    return {key: value for key, value in decision.to_dict().items() if key != "gate_decision_id"}


def test_decision_round_trips_under_its_own_reference() -> None:
    consumption = full_chain(controller())[3]
    anchor = A.gate_decision_ref(consumption)
    restored = A.gate_decision_from_dict(_payload(consumption), expected_ref=anchor)
    assert restored.gate_decision_id.value == consumption.gate_decision_id.value
    assert restored.decision_kind is consumption.decision_kind


def test_self_consistent_forgery_is_refused_by_the_anchor() -> None:
    """Recomputing the hash is not a defence; the anchor is held elsewhere.

    An attacker editing a stored verdict can recompute its identity, so a
    payload that is internally consistent would restore cleanly. Restoration is
    therefore bound to the reference a committed snapshot or lineage record
    already holds.
    """

    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    anchor = A.gate_decision_ref(blocked)
    forged = _payload(blocked)
    forged["decision_kind"] = "ADMIT"
    forged["reason_codes"] = ["REVALIDATION_PASSED"]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.gate_decision_from_dict(forged, expected_ref=anchor)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_restoration_refuses_another_decisions_reference() -> None:
    admitted = full_chain(controller())[3]
    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.gate_decision_from_dict(_payload(admitted), expected_ref=A.gate_decision_ref(blocked))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda d: d.update({"checked_dimensions": ["SOURCE_TAINT"]}), A.AdmissionFailureCode.DIMENSION_NOT_CHECKED),
        (lambda d: d.pop("boundary_ref"), A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH),
        (lambda d: d.update({"extra_field": 1}), A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH),
        (lambda d: d.update({"schema_version": "other/v9"}), A.AdmissionFailureCode.UNKNOWN_SCHEMA_VERSION),
        (lambda d: d.update({"gate_kind": "NOT_A_GATE"}), A.AdmissionFailureCode.TYPE_MISMATCH),
    ],
)
def test_malformed_payload_fails_closed(mutate, expected) -> None:
    consumption = full_chain(controller())[3]
    anchor = A.gate_decision_ref(consumption)
    payload = _payload(consumption)
    mutate(payload)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.gate_decision_from_dict(payload, expected_ref=anchor)
    assert excinfo.value.failure_code is expected


def test_restoration_requires_a_gate_decision_reference() -> None:
    consumption = full_chain(controller())[3]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.gate_decision_from_dict(_payload(consumption), expected_ref=ref(RefKind.ARTIFACT, "not-a-gate-ref"))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.TYPE_MISMATCH


# ---------------------------------------------------------------------------
# Consumption reconstructs effective taint (S4-ACC-TAINT-AUTH-03)
# ---------------------------------------------------------------------------


def test_effective_taint_projects_into_the_gate_finding() -> None:
    from synapse.experiments.gold.taint import (
        EffectiveTaint,
        TaintClass,
        consumption_finding_from_effective_taint,
    )

    clean = EffectiveTaint(
        taint_classes=(TaintClass.REPOSITORY_CONTENT,), quarantined=False,
        last_decision_id=None, decision_sequence=1,
    )
    finding = consumption_finding_from_effective_taint(clean, chain_complete=True)
    assert finding.consumable and finding.chain_complete and not finding.quarantined

    secret = EffectiveTaint(
        taint_classes=(TaintClass.CONTAINS_SECRET_LIKE_DATA,), quarantined=False,
        last_decision_id=None, decision_sequence=1,
    )
    blocked = consumption_finding_from_effective_taint(secret, chain_complete=True)
    assert not blocked.consumable and blocked.blocks_publication

    quarantined = EffectiveTaint(
        taint_classes=(), quarantined=True, last_decision_id=None, decision_sequence=1
    )
    assert not consumption_finding_from_effective_taint(quarantined, chain_complete=True).consumable

    # An incomplete chain is reported as incomplete, never as permissive.
    assert not consumption_finding_from_effective_taint(clean, chain_complete=False).chain_complete


def test_reconstructed_taint_drives_the_consumption_gate() -> None:
    from synapse.experiments.gold.taint import (
        EffectiveTaint,
        TaintClass,
        consumption_finding_from_effective_taint,
    )

    executable = EffectiveTaint(
        taint_classes=(TaintClass.CONTAINS_EXECUTABLE_CONTENT,), quarantined=False,
        last_decision_id=None, decision_sequence=1,
    )
    finding = consumption_finding_from_effective_taint(executable, chain_complete=True)
    consumption = full_chain(controller(taint=lambda item: finding))[3]
    assert not consumption.admitted
    assert "TAINT_BLOCKS_CONSUMPTION" in consumption.reason_codes
