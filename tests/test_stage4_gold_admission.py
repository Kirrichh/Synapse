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


def anchors(**overrides: str) -> dict[str, str]:
    """One coherent observation of every authority head, as the reader returns it."""

    observed = {
        name: hashlib.sha256(f"{name}-head-0".encode()).hexdigest()
        for name in A.AUTHORITY_HEAD_DOMAINS
    }
    observed.update(
        {name: hashlib.sha256(f"{name}-{value}".encode()).hexdigest() for name, value in overrides.items()}
    )
    return observed


class Journal:
    """A minimal append-only decision journal implementing the production port.

    NR-06 keeps this in the acceptance layer: the semantics live in
    ``commit_gate_decision`` and ``require_committed_decision``, and this test
    double only stores bytes, exactly as a real journal does.
    """

    def __init__(self, *, failing: bool = False, forgetful: bool = False) -> None:
        self._records: list[bytes] = []
        self._failing = failing
        self._forgetful = forgetful

    def append_record(self, payload: bytes) -> None:
        if self._failing:
            raise OSError("journal unavailable")
        self._records.append(payload)

    def contains_record(self, digest: str) -> bool:
        if self._forgetful:
            return False
        return any(hashlib.sha256(item).hexdigest() == digest for item in self._records)

    def current_anchor(self) -> str:
        running = hashlib.sha256(b"journal-genesis").digest()
        for item in self._records:
            running = hashlib.sha256(running + hashlib.sha256(item).digest()).digest()
        return running.hex()


def controller(
    *, taint=None, provenance=None, lifecycle=None, compat=None, boundary=None, grant=None,
    heads=None, authority: str = "gate-authority", policy: str = "policy-v1",
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
        head_reader=heads or (lambda: anchors()),
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


# ---------------------------------------------------------------------------
# §22 durability — "Decisions immutable, persisted and linked in lineage"
# ---------------------------------------------------------------------------


def committed_chain(control=None, journal=None):
    """A full admitted chain plus the durable receipt for its consumption."""

    control = control or controller()
    journal = journal or Journal()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    receipt = A.commit_gate_decision(
        decisions[3], journal=journal, trusted_clock=lambda: NOW
    )
    return control, journal, chain, receipt


def test_a_decision_reaches_the_journal_before_a_receipt_exists() -> None:
    journal = Journal()
    consumption = full_chain(controller())[3]
    assert not journal.contains_record(
        hashlib.sha256(consumption.canonical_bytes()).hexdigest()
    )
    receipt = A.commit_gate_decision(consumption, journal=journal, trusted_clock=lambda: NOW)
    assert journal.contains_record(receipt.decision_digest)
    assert receipt.gate_decision_id.digest_sha256 == consumption.gate_decision_id.digest_sha256
    A.validate_commit_receipt(receipt)


def test_an_unavailable_journal_produces_no_receipt() -> None:
    """A write that did not happen is not recorded as if it had."""

    consumption = full_chain(controller())[3]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.commit_gate_decision(
            consumption, journal=Journal(failing=True), trusted_clock=lambda: NOW
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.JOURNAL_UNAVAILABLE


def test_a_journal_that_does_not_report_the_record_produces_no_receipt() -> None:
    consumption = full_chain(controller())[3]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.commit_gate_decision(
            consumption, journal=Journal(forgetful=True), trusted_clock=lambda: NOW
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_receipt_cannot_be_built_by_its_constructor() -> None:
    for factory in (A.DecisionCommitReceipt, A.AuthorityHeadSet, A.AdmittedKnowledgeHandle):
        with pytest.raises(TypeError):
            factory()


def test_an_incomplete_journal_port_is_refused() -> None:
    class Partial:
        def append_record(self, payload: bytes) -> None: ...

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_decision_journal(Partial())
    assert excinfo.value.failure_code is A.AdmissionFailureCode.JOURNAL_UNAVAILABLE


def test_a_receipt_from_another_decision_admits_nothing() -> None:
    control, journal, _, _ = committed_chain()
    other = full_chain(controller(authority="other-authority"))[3]
    receipt = A.commit_gate_decision(other, journal=journal, trusted_clock=lambda: NOW)
    consumption = full_chain(control)[3]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_committed_decision(receipt, decision=consumption, journal=journal)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_receipt_stops_admitting_when_the_journal_loses_the_record() -> None:
    """Durability is asserted now, not remembered from when the receipt was issued."""

    _, journal, _, receipt = committed_chain()
    consumption = full_chain(controller())[3]
    A.require_committed_decision(receipt, decision=consumption, journal=journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_committed_decision(
            receipt, decision=consumption, journal=Journal(forgetful=True)
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


# ---------------------------------------------------------------------------
# §22 current authority heads
# ---------------------------------------------------------------------------


def test_heads_are_captured_in_one_observation() -> None:
    calls: list[int] = []

    def reader():
        calls.append(1)
        return anchors()

    head_set = A.capture_authority_heads(controller(heads=reader), boundary_ref=BOUNDARY_REF)
    assert len(calls) == 1, "a second call would be a second observation"
    assert set(head_set.anchors) == set(A.AUTHORITY_HEAD_DOMAINS)
    A.validate_authority_head_set(head_set)


def test_a_partial_head_observation_is_refused() -> None:
    partial = {name: anchors()[name] for name in A.AUTHORITY_HEAD_DOMAINS[:3]}
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=lambda: partial), boundary_ref=BOUNDARY_REF)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE


def test_an_unreadable_head_is_not_a_fresh_head() -> None:
    def explode():
        raise OSError("store unavailable")

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=explode), boundary_ref=BOUNDARY_REF)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize("domain", A.AUTHORITY_HEAD_DOMAINS)
def test_a_head_that_moved_since_the_observation_is_stale(domain: str) -> None:
    """TOCTOU: a revoke, a taint escalation or a new admission after capture."""

    current = anchors()
    control = controller(heads=lambda: dict(current))
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    current[domain] = hashlib.sha256(f"{domain}-moved".encode()).hexdigest()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_current_heads(head_set, controller=control, boundary_ref=BOUNDARY_REF)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_an_observation_from_another_boundary_is_refused() -> None:
    control = controller()
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    other = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_current_heads(head_set, controller=control, boundary_ref=other)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_a_rewritten_anchor_does_not_survive_validation() -> None:
    head_set = A.capture_authority_heads(controller(), boundary_ref=BOUNDARY_REF)
    forged = dict(head_set.anchors)
    forged["lifecycle"] = hashlib.sha256(b"forged").hexdigest()
    object.__setattr__(head_set, "anchors", forged)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_authority_head_set(head_set)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


# ---------------------------------------------------------------------------
# AdmittedKnowledgeHandle — the only carrier of consumable knowledge
# ---------------------------------------------------------------------------


def admitted_handle(control=None, journal=None):
    control, journal, chain, receipt = committed_chain(control, journal)
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    return control, journal, A.admit_for_consumption(
        chain,
        controller=control,
        subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1",
        receipt=receipt,
        head_set=head_set,
        journal=journal,
    )


def test_a_clean_admitted_chain_mints_a_handle() -> None:
    _, _, handle = admitted_handle()
    assert handle.subject_refs == SUBJECTS
    assert handle.policy_version == "policy-v1"
    A.validate_admitted_handle(handle)
    assert A.admitted_handle_ref(handle).schema_id == (
        A.SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V1.value
    )


def test_a_handle_requires_a_durable_decision() -> None:
    """An evaluated but uncommitted ADMIT mints nothing."""

    control = controller()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    journal = Journal()
    receipt = A.commit_gate_decision(decisions[3], journal=journal, trusted_clock=lambda: NOW)
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipt=receipt, head_set=head_set,
            journal=Journal(),  # a different journal never saw the append
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_handle_requires_heads_that_are_still_current() -> None:
    current = anchors()
    control = controller(heads=lambda: dict(current))
    _, journal, chain, receipt = committed_chain(control)
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    current["lifecycle"] = hashlib.sha256(b"revoked-after-capture").hexdigest()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipt=receipt, head_set=head_set,
            journal=journal,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_a_blocked_chain_mints_no_handle_at_all() -> None:
    control = controller(lifecycle=lambda item: False)
    journal = Journal()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
    )
    assert not chain.admitted
    receipt = A.commit_gate_decision(decisions[3], journal=journal, trusted_clock=lambda: NOW)
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipt=receipt, head_set=head_set,
            journal=journal,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


def test_a_handle_for_another_subject_set_is_refused() -> None:
    control, journal, chain, receipt = committed_chain()
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=(ref(RefKind.ARTIFACT, "obj-z"),),
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipt=receipt, head_set=head_set,
            journal=journal,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_a_rewritten_handle_does_not_survive_validation() -> None:
    _, _, handle = admitted_handle()
    object.__setattr__(handle, "policy_version", "policy-v2")
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_admitted_handle(handle)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_a_handle_cannot_borrow_another_decisions_receipt() -> None:
    control, journal, handle = admitted_handle()
    other = full_chain(controller(authority="other-authority"))[3]
    foreign = A.commit_gate_decision(other, journal=journal, trusted_clock=lambda: NOW)
    object.__setattr__(handle, "commit_receipt", foreign)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_admitted_handle(handle)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_the_legacy_retrieval_record_cannot_become_a_handle() -> None:
    """A-01: the ungated Patch 6 path confers no consumption authority."""

    import inspect

    from synapse.experiments.gold import retrieval

    assert "audit" in retrieval.RetrievalResult.__doc__.lower()
    assert "audit" in inspect.getdoc(retrieval.retrieve_and_load).lower()
    minting: list[str] = []
    for name, value in vars(A).items():
        if not inspect.isfunction(value) or value.__module__ != A.__name__:
            continue
        try:
            annotation = str(inspect.signature(value).return_annotation)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            continue
        if "AdmittedKnowledgeHandle" in annotation:
            minting.append(name)
    assert minting == ["admit_for_consumption"], (
        f"more than one function mints the capability: {minting}"
    )


def test_a_forged_receipt_digest_is_caught_by_the_payload_check() -> None:
    """The digest check is not redundant with the record-id check.

    For a genuine decision the two move together, since the record id is derived
    from the payload. They come apart exactly when a receipt is edited: keeping
    the gate_decision_id while changing the digest is what a forger would do, and
    only the payload comparison sees it.
    """

    _, journal, _, receipt = committed_chain()
    consumption = full_chain(controller())[3]

    # A second decision committed to the same journal. Pointing this receipt at
    # its digest defeats the other two barriers on purpose: the record id still
    # names this consumption, and the journal really does hold the digest. Only
    # comparing the digest against this decision's own payload catches it.
    other = full_chain(controller(authority="other-authority"))[3]
    borrowed = A.commit_gate_decision(other, journal=journal, trusted_clock=lambda: NOW)
    assert journal.contains_record(borrowed.decision_digest)
    object.__setattr__(receipt, "decision_digest", borrowed.decision_digest)
    assert receipt.gate_decision_id.digest_sha256 == consumption.gate_decision_id.digest_sha256

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_committed_decision(receipt, decision=consumption, journal=journal)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def blocked_early_chain():
    """A chain rejected at ingestion, evaluated cleanly from then on."""

    blocking = controller(provenance=lambda item: False)
    clean = controller()
    ingestion = A.evaluate_ingestion_gate(blocking, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        clean, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        clean, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        requested=REQUEST, predecessor=publication,
    )
    consumption = A.evaluate_consumption_gate(
        clean, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
    )
    return clean, A.build_gate_decision_chain(
        ingestion=ingestion, publication=publication,
        retrieval=retrieval, consumption=consumption,
    ), consumption


def test_an_early_rejection_propagates_through_every_later_gate() -> None:
    """A gate refuses a blocked predecessor, so a rejection cannot be outrun.

    This is what makes the chain-level admission check in
    ``admit_for_consumption`` an equivalent mutant rather than a live barrier:
    an admitted consumption decision whose ancestor was blocked is not
    constructible through the evaluators, because each of them refuses a
    predecessor that did not admit. The check stays as a cheap invariant at a
    security boundary — its redundancy depends on the evaluators continuing to
    behave this way, which is exactly what this test pins.
    """

    control, chain, consumption = blocked_early_chain()
    assert not chain.ingestion.admitted
    assert not consumption.admitted, "a later gate must not admit over a blocked ancestor"
    assert not chain.admitted
    for decision in chain.decisions():
        assert not decision.admitted

    journal = Journal()
    receipt = A.commit_gate_decision(consumption, journal=journal, trusted_clock=lambda: NOW)
    head_set = A.capture_authority_heads(control, boundary_ref=BOUNDARY_REF)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipt=receipt, head_set=head_set,
            journal=journal,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED
