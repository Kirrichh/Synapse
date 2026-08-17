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
from synapse.experiments.gold import authority_config as AC
from synapse.experiments.gold import point_of_use as P
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AttemptId,
    AuthorityIdentity,
    AuthorityRole,
    GateCheckedDimension,
    GateDecisionKind,
    GateKind,
    RunId,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)
from synapse.experiments.gold.retrieval import gate_selectable_candidates
from synapse.experiments.gold.admission_journal import FileAdmissionJournal
from tests.gold_store_fence import fence_for

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gold" / "admission_matrix_v1.json"
NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 30, 12, 5, 0, tzinfo=timezone.utc)


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
FROZEN_SET_REF = HashBoundRef(
    kind=RefKind.ARTIFACT,
    ref_id="frozen-set-1",
    schema_id=SchemaVersion.FROZEN_CANDIDATE_SET_V2.value,
    sha256=hashlib.sha256(b"frozen-set-1").hexdigest(),
    byte_length=len(b"frozen-set-1"),
    media_type="application/json",
)

GRANT = A.GrantEnvelope(
    scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version="policy-v1"
)
REQUEST = A.RequestedEnvelope(scopes=("repo:x",), capabilities=("read",), oracles=("swebench",))

CLEAN_TAINT = A.TaintFinding(
    consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
)
def compat_finding(
    subject_ref: HashBoundRef,
    consumer_context_ref: HashBoundRef,
    **overrides: bool,
) -> A.CompatibilityFinding:
    """A compatibility answer that says what it is about.

    Deliberately a function of the question rather than a module constant: a
    finding now names its subject and its frozen consumer context, so a fixed
    value would be an answer about one subject reused for every other — the very
    substitution the gate exists to refuse.
    """

    fields = {
        "compatible": True,
        "evidence_complete": True,
        "drifted": False,
        "conflicts_unresolved": False,
    } | overrides
    return A.CompatibilityFinding(
        subject_ref=subject_ref, consumer_context_ref=consumer_context_ref, **fields
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


def anchors(**overrides: str) -> dict[str, object]:
    """What a trusted reader returns: the committed boundary and every head."""

    heads = {
        name: {
            "anchor_sha256": hashlib.sha256(f"{name}-head-0".encode()).hexdigest(),
            "sequence": 1,
        }
        for name in A.AUTHORITY_HEAD_DOMAINS
    }
    for name, value in overrides.items():
        heads[name] = {
            "anchor_sha256": hashlib.sha256(f"{name}-{value}".encode()).hexdigest(),
            "sequence": 2,
        }
    return {"boundary_ref": BOUNDARY_REF, "heads": heads}


class Journal:
    """A minimal append-only decision journal implementing the production port.

    NR-06 keeps this in the acceptance layer: the semantics live in
    ``commit_gate_decision`` and ``require_committed_decision``, and this test
    double only stores bytes, exactly as a real journal does.

    **The coordination is not doubled.** ``mutation_fence`` is a real
    ``FileSnapshotFence`` and every append opens a real interval on it. Storing
    bytes in a list is a fair simplification of a byte store; deciding for
    yourself when a coordinator's epoch moves is not a simplification of
    anything, it is a second implementation of the property under test. So the
    bytes are fake and the coordination is real, and a reader handed this journal
    can check that it answers to the same coordinator.
    """

    def __init__(self, *, failing: bool = False, forgetful: bool = False, fence=None) -> None:
        from tests.gold_store_fence import quiet_fence

        self._records: list[bytes] = []
        self._failing = failing
        self._forgetful = forgetful
        self.mutation_fence = fence if fence is not None else quiet_fence()

    def append_record(self, payload: bytes, *, ticket=None) -> None:
        from synapse.experiments.gold.persistence import (
            require_ticket_of_coordinator,
            store_transaction,
        )

        if self._failing:
            raise A.GateDependencyUnavailable("journal unavailable")
        if ticket is None:
            with store_transaction(self.mutation_fence):
                self._records.append(payload)
        else:
            require_ticket_of_coordinator(
                ticket, coordinator_id=self.mutation_fence.coordinator_id()
            )
            self._records.append(payload)

    def contains_record(self, digest: str) -> bool:
        if self._forgetful:
            return False
        return any(hashlib.sha256(item).hexdigest() == digest for item in self._records)

    def _anchor_after(self, count: int) -> str:
        running = hashlib.sha256(b"journal-genesis").digest()
        for item in self._records[:count]:
            running = hashlib.sha256(running + hashlib.sha256(item).digest()).digest()
        return running.hex()

    def current_anchor(self) -> str:
        return self._anchor_after(len(self._records))

    def extends(self, anchor: str) -> bool:
        """True when the anchor is a prefix of the committed history."""

        return any(
            self._anchor_after(count) == anchor
            for count in range(len(self._records) + 1)
        )

    def rollback(self, keep: int) -> None:
        """Drop committed records, as a rolled-back store would."""

        self._records = self._records[:keep]


def gate_declaration(
    *,
    authority: str = "gate-authority",
    roles=None,
    policy: str = "policy-v1",
    component: str = "gate-evaluator",
    version: str = "synapse.stage4.gate-evaluator/v1",
):
    """The registration a controller is configured from.

    An identity plus a role map used to be passed straight to
    ``configure_gate_controller``, which let the caller state its own
    entitlement. Now the controller is configured from a declaration, and the
    roles come out of it.
    """

    return AC.create_gate_evaluator_declaration(
        authority_handle=authority_handle(),
        evaluator_identity=AuthorityIdentity(value=authority),
        evaluator_component_id=component,
        evaluator_component_version=version,
        gate_roles=roles or {
            GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        },
        policy_version=policy,
        trusted_clock=lambda: NOW,
    )



#: The actor set every controller in this suite establishes independence over.
#: A verifier must bring the same three, because independence is a property of a
#: decision *and* the actors in play, not of the evaluator alone.
SOURCE_ACTORS = (
    ActorIdentity(value="producer"),
    ActorIdentity(value="retriever"),
    ActorIdentity(value="consumer"),
)


def entitlement(*, authority: str = "gate-authority", roles=None, policy: str = "policy-v1",
                per_gate=None):
    """The verifier's own declaration and actor set, per gate, rebuilt not borrowed.

    Deliberately not read off the controller. `require_entitled_decision` exists
    to re-establish entitlement from copies the verifier holds; handing it the
    evaluator's own object would check that a record agrees with itself, which is
    the self-approval the whole mechanism is against.

    ``per_gate`` names a different authority for individual gates, which is how a
    chain decided by four separate reviewers is verified. The default names one
    authority four times, which is equally legal.
    """

    names = dict(per_gate or {})
    return {
        "entitlements": {
            gate: (
                gate_declaration(
                    authority=names.get(gate, authority), roles=roles, policy=policy
                ),
                SOURCE_ACTORS,
            )
            for gate in GateKind
        }
    }



def _retrieval_entitlement():
    """The verifier's own entitlement, in the shape the retrieval owner takes."""

    return entitlement()["entitlements"]


def controller(
    *, taint=None, provenance=None, lifecycle=None, compat=None, boundary=None, grant=None,
    heads=None, roles=None, authority: str = "gate-authority", policy: str = "policy-v1",
    clock=None,
) -> A.ConfiguredGateController:
    return A.configure_gate_controller(
        declaration=gate_declaration(authority=authority, roles=roles, policy=policy),
        policy_version=policy,
        run_id=RunId("admission-run"),
        attempt_id=AttemptId("admission-attempt"),
        repository_revision="a" * 40,
        environment_profile_id="admission-env",
        trusted_clock=clock or (lambda: NOW),
        taint_probe=taint or (lambda item: CLEAN_TAINT),
        provenance_probe=provenance or (lambda item: True),
        lifecycle_probe=lifecycle or (lambda item: True),
        compatibility_probe=compat or (lambda item, ctx: compat_finding(item, ctx)),
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
        boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
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


def test_consumption_declares_every_dimension_a_probe_can_answer_for() -> None:
    """Consumption is the widest gate, and every dimension it names is answered.

    The history here is worth keeping, because the test was wrong twice in
    opposite directions. It first asserted the declaration was the whole enum,
    which was an overclaim: nothing was recorded as answering for ``TOOLS``. The
    fix narrowed the declaration — and that was wrong too, because §22 puts tools
    in the mandatory vocabulary and forbids treating an absent dimension as
    passed, so a gate that quietly stops naming it is not compliant either.

    Both revisions shared one mistake: treating the declaration as the thing to
    adjust. The evidence existed the whole time. The compatibility owner
    evaluates ``ENVIRONMENT_AND_TOOLCHAIN`` over ``tool_inputs`` carried
    separately from ``environment_inputs``, and distinguishes the two in its
    reasons, so one finding settles both — ``DIMENSION_SOURCE`` now records that,
    and the declaration is whole again with something behind every name.

    What the test pins is the invariant that survives all three revisions:
    consumption declares exactly what some probe can supply, and no dimension of
    the closed vocabulary is left unanswerable.
    """

    consumption = A.required_dimensions(GateKind.CONSUMPTION)
    answerable = {item for group in A.DIMENSION_SOURCE.values() for item in group}
    assert consumption == answerable
    assert consumption == set(GateCheckedDimension), (
        "a dimension no probe answers for is a hole the closed vocabulary names"
    )
    assert GateCheckedDimension.TOOLS in consumption
    for gate in (GateKind.INGESTION, GateKind.PUBLICATION):
        assert A.required_dimensions(gate) < consumption

    # Retrieval covers the same dimensions, and that is not a defect to paper
    # over: consumption differs from retrieval by *when* it looks and by the
    # committed boundary it additionally requires, not by consulting a further
    # dimension. Asserting a strict superset here would only be satisfiable by
    # inventing one.
    assert A.required_dimensions(GateKind.RETRIEVAL) == consumption


def test_blocking_reason_always_outranks_an_admitting_one() -> None:
    assert A.resolve_decision_kind(GateKind.CONSUMPTION, ("REVALIDATION_PASSED",)) is GateDecisionKind.ADMIT
    mixed = tuple(sorted(("REVALIDATION_PASSED", "LIFECYCLE_CHANGED")))
    assert A.resolve_decision_kind(GateKind.CONSUMPTION, mixed) is GateDecisionKind.REJECT


def test_the_single_precedence_table_resolves_all_mixed_outcomes() -> None:
    assert A.resolve_decision_kind(
        GateKind.CONSUMPTION, tuple(sorted(("SUBJECT_QUARANTINED", "LIFECYCLE_CHANGED")))
    ) is GateDecisionKind.QUARANTINE
    assert A.resolve_decision_kind(
        GateKind.CONSUMPTION, tuple(sorted(("LIFECYCLE_CHANGED", "HUMAN_REVIEW_REQUIRED")))
    ) is GateDecisionKind.REJECT
    assert A.resolve_decision_kind(
        GateKind.CONSUMPTION, tuple(sorted(("HUMAN_REVIEW_REQUIRED", "REVALIDATION_PASSED")))
    ) is GateDecisionKind.REQUIRE_REVIEW
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
        ingestion=ingestion, publication=publication, retrieval=retrieval, consumption=consumption,
        **entitlement(),
    )
    assert chain.admitted
    assert chain.blocking_reasons() == ()
    admitted = A.require_consumption_admitted(
        chain.consumption, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
    )
    assert admitted is chain.consumption
    assert admitted.subject_refs == SUBJECTS


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
        raise A.GateDependencyUnavailable("dependency unavailable")

    taint = A.TaintFinding(
        consumable=probes.get("taint_consumable", True),
        chain_complete=probes.get("taint_chain_complete", True),
        quarantined=probes.get("taint_quarantined", False),
        blocks_publication=probes.get("taint_blocks_publication", False),
    )
    def compat(item, ctx):
        return compat_finding(
            item, ctx,
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
        compat=raiser if probes.get("compat_raises") else compat,
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
        raise A.GateDependencyUnavailable("probe exploded")

    control = controller(**{probe: raiser})
    consumption = full_chain(control)[3]
    assert not consumption.admitted
    assert "DEPENDENCY_UNAVAILABLE" in consumption.reason_codes


@pytest.mark.parametrize("bogus", ["yes", 1, None, object()])
def test_non_exact_probe_result_is_a_contract_violation_not_an_outage(bogus) -> None:
    """A probe that answers the wrong type is broken, not unreachable.

    Both readings refuse — the difference is what an incident analysis is told
    afterwards. A store that could not be reached is an outage and someone goes
    looking at the store; an adapter returning ``None`` where a ``TaintFinding``
    was required is a defect in the adapter and nothing is wrong with the store
    at all. Filing the second as ``DEPENDENCY_UNAVAILABLE`` sends the analysis
    to the wrong place, so the two are reported separately.
    """

    control = controller(taint=lambda item: bogus)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION


def test_a_compatibility_answer_about_another_consumer_context_is_refused() -> None:
    """The kill for the substitution the gate could not previously see.

    A compatibility port used to be called with the subject alone, so a
    controller configured with a closure bound to consumer context A produced a
    decision that recorded consumer context B — formally valid, and resting on
    evidence computed for somebody else's context. §22 requires compatibility to
    be established against the exact frozen consumer context, and the only way a
    gate can check that is for the answer to say which context it is about.
    """

    foreign = ref(RefKind.ARTIFACT, "another-consumer")
    control = controller(compat=lambda item, ctx: compat_finding(item, foreign))

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION
    assert "consumer context" in excinfo.value.detail


def test_a_compatibility_answer_about_another_subject_is_refused() -> None:
    """The same substitution one axis over: right context, wrong subject."""

    control = controller(
        compat=lambda item, ctx: compat_finding(ref(RefKind.ARTIFACT, "obj-z"), ctx)
    )

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION
    assert "subject" in excinfo.value.detail


def test_the_compatibility_port_is_handed_the_context_it_must_answer_about() -> None:
    """A port that is never told the context cannot be bound to it.

    Checking the answer is only half of it: if the gate did not pass the context
    in, an honest port would have nothing to bind its finding to, and the check
    above could only ever be satisfied by a port that already knew the answer.
    """

    seen: list[tuple[str, str]] = []

    def probe(item, ctx):
        seen.append((item.ref_id, ctx.ref_id))
        return compat_finding(item, ctx)

    full_chain(controller(compat=probe))
    assert seen, "the compatibility port was never consulted"
    assert {context for _, context in seen} == {CONTEXT_REF.ref_id}
    assert {subject for subject, _ in seen} == {item.ref_id for item in SUBJECTS}


def test_a_probe_answering_with_a_subclass_is_refused() -> None:
    """The exact-type demand is not decoration: a subclass can drop the invariant.

    ``TaintFinding.__post_init__`` is the only thing standing between the gate
    and a finding that claims to be consumable while quarantined. A subclass
    overrides it away and the object still satisfies ``isinstance``, so an
    ``isinstance`` check would let a port hand the gate a taint verdict the real
    type cannot represent. Nothing here is unreachable and nothing is down —
    the port broke its contract, and that is what the gate reports.
    """

    class ForgivingTaint(A.TaintFinding):
        def __post_init__(self) -> None:
            return None

    forged = ForgivingTaint(
        consumable=True, chain_complete="not-a-bool", quarantined=True, blocks_publication=False
    )
    assert isinstance(forged, A.TaintFinding)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(controller(taint=lambda item: forged))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION


def test_a_grant_probe_answering_with_a_subclass_is_refused() -> None:
    """The same hole on the envelope that decides scope, capability and oracle."""

    class UncheckedGrant(A.GrantEnvelope):
        def __post_init__(self) -> None:
            return None

    forged = UncheckedGrant(
        scopes=("repo:x", "repo:x", "repo:everything"),
        capabilities=("read",),
        oracles=("swebench",),
        policy_version="policy-v1",
    )
    assert isinstance(forged, A.GrantEnvelope)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(controller(grant=lambda: forged))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION


@pytest.mark.parametrize(
    ("probe", "wrong"),
    [
        ("taint", lambda item: compat_finding(item, CONTEXT_REF)),
        ("compat", lambda item, ctx: CLEAN_TAINT),
        ("grant", lambda: REQUEST),
    ],
    ids=["taint-answers-compatibility", "compatibility-answers-taint", "grant-answers-request"],
)
def test_a_probe_answering_a_neighbouring_domain_is_never_read_as_an_outage(probe, wrong) -> None:
    """The kill for the mutant that pools every malformed answer as unavailability.

    These are the dangerous returns: a well-formed value of a real authority
    type, just not the one this probe was asked for — and in the grant case a
    ``RequestedEnvelope``, which carries the same three fields as the
    ``GrantEnvelope`` it was substituted for and differs only by the missing
    policy version. A gate that duck-typed the answer would read what the
    consumer *asked* for as what it had been *granted*.
    """

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(controller(**{probe: wrong}))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.PROBE_CONTRACT_VIOLATION
    assert excinfo.value.failure_code is not A.AdmissionFailureCode.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt, SystemExit, GeneratorExit, TypeError, AssertionError],
    ids=lambda item: item.__name__,
)
def test_an_undeclared_probe_error_is_not_reported_as_unavailability(error) -> None:
    """A defect must be reported as a defect, not as a store being down.

    Interpreter control-flow exceptions and programming errors carry nothing
    about dependency availability. Folding them into DEPENDENCY_UNAVAILABLE
    would keep the outcome restrictive — the gate still refuses — but it would
    make an incident analysis read a broken adapter as an outage.
    """

    def raiser(*_args, **_kwargs):
        raise error()

    with pytest.raises(error):
        full_chain(controller(lifecycle=raiser))


def test_a_declared_unavailability_still_blocks_without_admitting() -> None:
    def raiser(*_args, **_kwargs):
        raise A.GateDependencyUnavailable("lifecycle store is down")

    consumption = full_chain(controller(lifecycle=raiser))[3]
    assert not consumption.admitted
    assert "DEPENDENCY_UNAVAILABLE" in consumption.reason_codes


def test_an_admission_violation_from_a_probe_keeps_its_own_code() -> None:
    def raiser(*_args, **_kwargs):
        raise A.AdmissionViolation(
            A.AdmissionFailureCode.SUBJECT_MISMATCH, "probe saw another subject"
        )

    with pytest.raises(A.AdmissionViolation) as excinfo:
        full_chain(controller(lifecycle=raiser))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


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
        boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
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
        **entitlement(),
    )
    assert not chain.admitted
    assert chain.blocking_reasons()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_consumption_admitted(
            chain.consumption, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


def test_chain_refuses_decisions_from_different_authorities() -> None:
    first = full_chain(controller())
    other = full_chain(controller(authority="another-authority"))
    # The verifier legitimately holds the other authority's declaration too, so
    # entitlement passes for every decision and the chain rule is what refuses.
    # Without this the test would stop at AUTHORITY_NOT_INDEPENDENT and no longer
    # exercise the rule it is named for — a separate authority per gate is legal,
    # as the four-reviewer test shows; splicing one chain's verdict into another
    # is not.
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.build_gate_decision_chain(
            ingestion=first[0], publication=first[1], retrieval=first[2], consumption=other[3],
            **entitlement(per_gate={GateKind.CONSUMPTION: "another-authority"}),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.GATE_DECISION_REUSED


# ---------------------------------------------------------------------------
# Retrieval wiring: the gate runs before a candidate becomes selectable
# ---------------------------------------------------------------------------


def _retrieval_journal(tmp_path: Path) -> FileAdmissionJournal:
    return FileAdmissionJournal(
        tmp_path / "retrieval-gate" / "decisions.journal",
        fence_for(tmp_path),
    )


def _retrieval_frozen(tmp_path: Path):
    from tests.gold_frozen_candidates import frozen_for
    from tests.test_stage4_gold_compatibility import _make_harness

    harness = _make_harness(tmp_path / "retrieval-frozen-library")
    return frozen_for(
        harness.library,
        store_root=tmp_path / "retrieval-frozen-boundary",
    )


def test_retrieval_gate_runs_before_the_selectable_set(tmp_path: Path) -> None:
    frozen = _retrieval_frozen(tmp_path)
    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    journal = _retrieval_journal(tmp_path)
    selectable = gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=frozen.boundary_ref, frozen=frozen,
        requested=REQUEST, publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=journal, trusted_clock=lambda: NOW,
    )
    assert selectable.admitted_refs == SUBJECTS
    assert selectable.decision.gate_kind is GateKind.RETRIEVAL, (
        "the verdict travels with the refs; a bare tuple would be a caller's word for it"
    )
    A.require_committed_decision(
        selectable.decision_receipt,
        decision=selectable.decision,
        journal=journal,
    )


def test_rejected_candidate_never_becomes_selectable(tmp_path: Path) -> None:
    frozen = _retrieval_frozen(tmp_path)
    control = controller(lifecycle=lambda item: False)
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    assert gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=frozen.boundary_ref, frozen=frozen,
        requested=REQUEST, publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=_retrieval_journal(tmp_path), trusted_clock=lambda: NOW,
    ).admitted_refs == ()


def test_selectable_set_requires_a_configured_controller(tmp_path: Path) -> None:
    frozen = _retrieval_frozen(tmp_path)
    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        gate_selectable_candidates(
            controller=object(), candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=frozen.boundary_ref, frozen=frozen,
            requested=REQUEST, publication_decision=publication,
            entitlements=_retrieval_entitlement(),
            journal=_retrieval_journal(tmp_path), trusted_clock=lambda: NOW,
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
        raise A.GateDependencyUnavailable("probe exploded")

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
        **entitlement(),
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_consumption_admitted(
            chain.consumption, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


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


def test_mutant_rejected_item_enters_prompt_or_replay(tmp_path: Path) -> None:
    """Nothing a gate rejected may reach a worker context or a replay input."""

    control = controller(
        compat=lambda item, ctx: compat_finding(item, ctx, compatible=False)
    )
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_consumption_admitted(
            chain.consumption, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF, policy_version="policy-v1",
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    frozen = _retrieval_frozen(tmp_path)
    assert gate_selectable_candidates(
        controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=frozen.boundary_ref, frozen=frozen,
        requested=REQUEST, publication_decision=publication,
        entitlements=_retrieval_entitlement(),
        journal=_retrieval_journal(tmp_path), trusted_clock=lambda: NOW,
    ).admitted_refs == ()


# ---------------------------------------------------------------------------
# Low-level deserialization is a bypass path, so restoration is anchored
# ---------------------------------------------------------------------------


def _payload(decision: A.GateDecision) -> dict:
    return {key: value for key, value in decision.to_dict().items() if key != "gate_decision_id"}


def _rebind_mutated_v2_decision(decision: A.GateDecision) -> None:
    """Keep a mutation internally envelope-consistent so the deeper barrier is tested."""

    prior = decision.envelope
    envelope = A.create_common_envelope(
        schema_version=A.SchemaVersion.COMMON_ENVELOPE_V2,
        identity_domain=A.IdentityDomain.GATE_DECISION_V2,
        canonical_payload_bytes=A._canonical(A._decision_payload(decision)),
        run_id=prior.run_id,
        attempt_id=prior.attempt_id,
        created_at_utc=prior.created_at_utc,
        producer_component=prior.producer_component,
        repository_revision=prior.repository_revision,
        policy_version=prior.policy_version,
        environment_profile_id=prior.environment_profile_id,
        lineage_parent_ids=prior.lineage_parent_ids,
    )
    object.__setattr__(decision, "envelope", envelope)
    object.__setattr__(
        decision,
        "envelope_binding_sha256",
        A.compute_envelope_binding_sha256(envelope),
    )
    object.__setattr__(decision, "gate_decision_id", envelope.record_id)


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
    forged["payload"]["decision_kind"] = "ADMIT"
    forged["payload"]["reason_codes"] = ["REVALIDATION_PASSED"]
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
    mutate(payload["payload"])
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
    from synapse.experiments.gold.taint import EffectiveTaint, TaintClass
    from synapse.experiments.gold.gate_findings import consumption_finding_from_effective_taint

    clean = EffectiveTaint(
        taint_classes=(TaintClass.REPOSITORY_CONTENT,), quarantined=False,
        last_decision_id=None, decision_sequence=1, chain_complete=True,
    )
    finding = consumption_finding_from_effective_taint(clean)
    assert finding.consumable and finding.chain_complete and not finding.quarantined

    secret = EffectiveTaint(
        taint_classes=(TaintClass.CONTAINS_SECRET_LIKE_DATA,), quarantined=False,
        last_decision_id=None, decision_sequence=1, chain_complete=True,
    )
    blocked = consumption_finding_from_effective_taint(secret)
    assert not blocked.consumable and blocked.blocks_publication

    quarantined = EffectiveTaint(
        taint_classes=(), quarantined=True, last_decision_id=None,
        decision_sequence=1, chain_complete=True,
    )
    assert not consumption_finding_from_effective_taint(quarantined).consumable

    # An incomplete chain is reported as incomplete, never as permissive — and
    # the record says so, so no caller had the chance to claim otherwise.
    unproven = replace(clean, chain_complete=False)
    assert not consumption_finding_from_effective_taint(unproven).chain_complete


def test_reconstructed_taint_drives_the_consumption_gate() -> None:
    from synapse.experiments.gold.taint import EffectiveTaint, TaintClass
    from synapse.experiments.gold.gate_findings import consumption_finding_from_effective_taint

    executable = EffectiveTaint(
        taint_classes=(TaintClass.CONTAINS_EXECUTABLE_CONTENT,), quarantined=False,
        last_decision_id=None, decision_sequence=1, chain_complete=True,
    )
    finding = consumption_finding_from_effective_taint(executable)
    consumption = full_chain(controller(taint=lambda item: finding))[3]
    assert not consumption.admitted
    assert "TAINT_BLOCKS_CONSUMPTION" in consumption.reason_codes


# ---------------------------------------------------------------------------
# §22 durability — "Decisions immutable, persisted and linked in lineage"
# ---------------------------------------------------------------------------


def committed_chain(control=None, journal=None):
    """A full admitted chain plus a durable receipt for every one of its decisions.

    All four are committed, because a handle now requires all four. Committing
    only the consumption verdict used to be enough, which made the chain a fact
    about memory rather than about storage.
    """

    control = control or controller()
    journal = journal or Journal()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    receipts = tuple(
        A.commit_gate_decision(item, journal=journal, trusted_clock=lambda: NOW)
        for item in decisions
    )
    return control, journal, chain, receipts


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


@pytest.mark.parametrize("error", [OSError, TypeError, KeyboardInterrupt])
def test_an_undeclared_journal_error_is_not_reported_as_unavailability(error) -> None:
    """The commit boundary is as narrow as the probe boundary, for the same reason.

    ``GateDependencyUnavailable`` is the declared way for a journal adapter to
    say the store could not be written; translating an ``OSError`` into it is
    the adapter's job, not the gate's. If the gate caught everything it would
    report a bug in the serialiser — or an operator interrupt — as a storage
    outage, and in every case the receipt is withheld either way, so the only
    thing the wide catch would buy is a wrong diagnosis.
    """

    class Undeclared(Journal):
        def append_record(self, payload: bytes) -> None:
            raise error()

    consumption = full_chain(controller())[3]
    with pytest.raises(error):
        A.commit_gate_decision(consumption, journal=Undeclared(), trusted_clock=lambda: NOW)


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

    _, journal, _, receipts = committed_chain()
    receipt = receipts[-1]
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

    head_set = A.capture_authority_heads(controller(heads=reader))
    assert len(calls) == 1, "a second call would be a second observation"
    assert tuple(item.domain for item in head_set.observations) == A.AUTHORITY_HEAD_DOMAINS
    assert head_set.boundary_ref == BOUNDARY_REF
    A.validate_authority_head_set(head_set)


def test_the_reader_supplies_the_boundary_the_caller_does_not() -> None:
    """The caller cannot assert which boundary is current; only the store can.

    An earlier revision took the boundary as an argument and stored it
    unchanged, so the later comparison checked a caller-supplied value against
    itself. Now the boundary arrives with the heads.
    """

    import inspect

    parameters = set(inspect.signature(A.capture_authority_heads).parameters)
    assert "boundary_ref" not in parameters
    assert "boundary_ref" not in set(inspect.signature(A.require_current_heads).parameters)


@pytest.mark.parametrize(
    "broken",
    [
        {"heads": {}},
        {"boundary_ref": BOUNDARY_REF},
        {"boundary_ref": BOUNDARY_REF, "heads": {}, "extra": 1},
    ],
    ids=["no-boundary", "no-heads", "extra-key"],
)
def test_a_malformed_head_observation_is_refused(broken) -> None:
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=lambda: broken))
    assert excinfo.value.failure_code in {
        A.AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE,
        A.AdmissionFailureCode.BOUNDARY_REQUIRED,
    }


def test_a_partial_head_observation_is_refused() -> None:
    full = anchors()
    partial = {
        "boundary_ref": full["boundary_ref"],
        "heads": {name: full["heads"][name] for name in A.AUTHORITY_HEAD_DOMAINS[:3]},
    }
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=lambda: partial))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE


def test_a_head_without_its_store_sequence_is_refused() -> None:
    """A bare digest is not an observation; the sequence is what a fence needs."""

    full = anchors()
    stripped = {
        "boundary_ref": full["boundary_ref"],
        "heads": {
            name: {"anchor_sha256": full["heads"][name]["anchor_sha256"]}
            for name in A.AUTHORITY_HEAD_DOMAINS
        },
    }
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=lambda: stripped))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE


def test_an_unreadable_head_is_not_a_fresh_head() -> None:
    def explode():
        raise A.GateDependencyUnavailable("store unavailable")

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.capture_authority_heads(controller(heads=explode))
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DEPENDENCY_UNAVAILABLE


def test_an_undeclared_reader_error_is_not_reported_as_unavailability() -> None:
    def explode():
        raise TypeError("the reader is broken, not the store")

    with pytest.raises(TypeError):
        A.capture_authority_heads(controller(heads=explode))


@pytest.mark.parametrize("domain", A.AUTHORITY_HEAD_DOMAINS)
def test_a_head_that_moved_since_the_observation_is_stale(domain: str) -> None:
    """TOCTOU: a revoke, a taint escalation or a new admission after capture."""

    current = anchors()
    control = controller(heads=lambda: current)
    head_set = A.capture_authority_heads(control)
    current["heads"][domain] = {
        "anchor_sha256": hashlib.sha256(f"{domain}-moved".encode()).hexdigest(),
        "sequence": 9,
    }
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_current_heads(head_set, controller=control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


@pytest.mark.parametrize("domain", A.AUTHORITY_HEAD_DOMAINS)
def test_a_head_whose_sequence_moved_under_an_unchanged_anchor_is_stale(domain: str) -> None:
    """The kill for the mutant that compares anchors and ignores the sequence.

    A store can land back on a content anchor it has held before — a revoke
    followed by a re-grant, or a rollback-and-replay that reconstructs the same
    head bytes. The anchor alone cannot tell those apart from "nothing
    happened", and the store's own sequence is what distinguishes them. A
    comparison that reads only ``anchor_sha256`` would call this window quiet
    and let a decision captured before the churn stay usable across it.
    """

    current = anchors()
    control = controller(heads=lambda: current)
    head_set = A.capture_authority_heads(control)
    unchanged = current["heads"][domain]["anchor_sha256"]
    current["heads"][domain] = {"anchor_sha256": unchanged, "sequence": 4}

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_current_heads(head_set, controller=control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE
    assert domain in excinfo.value.detail


def test_a_new_committed_boundary_makes_the_observation_stale() -> None:
    """The boundary is re-read like every other head, not carried over."""

    current = anchors()
    control = controller(heads=lambda: current)
    head_set = A.capture_authority_heads(control)
    current["boundary_ref"] = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_current_heads(head_set, controller=control)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_an_unchanged_world_returns_the_fresh_observation() -> None:
    control = controller()
    head_set = A.capture_authority_heads(control)
    fresh = A.require_current_heads(head_set, controller=control)
    assert fresh.boundary_ref == head_set.boundary_ref
    assert [item.to_dict() for item in fresh.observations] == [
        item.to_dict() for item in head_set.observations
    ]


def test_a_rewritten_anchor_does_not_survive_validation() -> None:
    head_set = A.capture_authority_heads(controller())
    forged = (
        A.AuthorityHeadObservation(
            domain="lifecycle",
            anchor_sha256=hashlib.sha256(b"forged").hexdigest(),
            sequence=1,
        ),
    ) + head_set.observations[1:]
    object.__setattr__(head_set, "observations", forged)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_authority_head_set(head_set)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_an_observation_cannot_name_an_undeclared_domain() -> None:
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.AuthorityHeadObservation(
            domain="not-a-domain",
            anchor_sha256=hashlib.sha256(b"x").hexdigest(),
            sequence=1,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_INCOMPLETE


# ---------------------------------------------------------------------------
# AdmittedKnowledgeHandle — the only carrier of consumable knowledge
# ---------------------------------------------------------------------------


def admitted_handle(control=None, journal=None):
    control, journal, chain, receipts = committed_chain(control, journal)
    fenced_state = _fenced_state(control, journal)
    return control, journal, A.admit_for_consumption(
        chain,
        controller=control,
        subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1",
        receipts=receipts,
        fenced_state=fenced_state,
        journal=journal,
        **entitlement()
    )


def _fenced_state(control, journal):
    from synapse.experiments.gold.coordination import read_current_authority_state

    return read_current_authority_state(
        control,
        fence=journal.mutation_fence,
        participants=(journal,),
    )


def test_a_clean_admitted_chain_mints_a_handle() -> None:
    _, _, handle = admitted_handle()
    assert handle.subject_refs == SUBJECTS
    assert handle.policy_version == "policy-v1"
    A.validate_admitted_handle(handle)
    assert A.admitted_handle_ref(handle).schema_id == (
        A.SchemaVersion.ADMITTED_KNOWLEDGE_HANDLE_V2.value
    )


def test_a_raw_unfenced_authority_head_set_cannot_mint_a_handle() -> None:
    control, journal, chain, receipts = committed_chain()
    raw_head_set = A.capture_authority_heads(control)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain,
            controller=control,
            subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1",
            receipts=receipts,
            fenced_state=raw_head_set,
            journal=journal,
            **entitlement(),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.TRUSTED_OBJECT_FORGED


def test_a_handle_requires_a_durable_decision() -> None:
    """An evaluated but uncommitted ADMIT mints nothing."""

    control = controller()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    journal = Journal()
    receipts = tuple(
        A.commit_gate_decision(item, journal=journal, trusted_clock=lambda: NOW)
        for item in decisions
    )
    fenced_state = _fenced_state(control, journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=Journal(),  # a different journal never saw the append,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_handle_requires_heads_that_are_still_current() -> None:
    current = anchors()
    control = controller(heads=lambda: current)
    _, journal, chain, receipts = committed_chain(control)
    fenced_state = _fenced_state(control, journal)
    current["heads"]["lifecycle"] = {
        "anchor_sha256": hashlib.sha256(b"revoked-after-capture").hexdigest(),
        "sequence": 7,
    }
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_a_blocked_chain_mints_no_handle_at_all() -> None:
    control = controller(lifecycle=lambda item: False)
    journal = Journal()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    assert not chain.admitted
    receipts = tuple(
        A.commit_gate_decision(item, journal=journal, trusted_clock=lambda: NOW)
        for item in decisions
    )
    fenced_state = _fenced_state(control, journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


def test_a_handle_for_another_subject_set_is_refused() -> None:
    control, journal, chain, receipts = committed_chain()
    fenced_state = _fenced_state(control, journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=(ref(RefKind.ARTIFACT, "obj-z"),),
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal, **entitlement(),
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
    """A-01: the retrieval record confers no consumption authority.

    The ungated path this used to describe is gone: ``retrieve_and_load`` was
    split into enumeration and the durable point-of-load orchestration, and the
    §22 gate runs between them.  The old public loader does not exist at all,
    so no caller can reach a load with a reusable in-memory verdict.
    """

    import inspect

    from synapse.experiments.gold import retrieval

    assert "audit" in retrieval.RetrievalResult.__doc__.lower()
    assert not hasattr(retrieval, "retrieve_and_load"), (
        "the ungated enumerate-rank-select-load path must not come back"
    )
    assert "retrieve_and_load" not in retrieval.__all__
    assert not hasattr(retrieval, "select_and_load")
    assert "select_and_load" not in retrieval.__all__
    parameters = inspect.signature(retrieval.select_and_load_durably).parameters
    assert "admission" in parameters and "persistence" in parameters
    assert "admitted_refs" not in parameters
    assert not hasattr(A, "admitted_subject_refs")
    assert "admitted_subject_refs" not in A.__all__
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

    _, journal, _, receipts = committed_chain()
    receipt = receipts[-1]
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
        boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
        requested=REQUEST, predecessor=publication,
    )
    consumption = A.evaluate_consumption_gate(
        clean, subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
    )
    return clean, A.build_gate_decision_chain(
        ingestion=ingestion, publication=publication,
        retrieval=retrieval, consumption=consumption,
        **entitlement(),
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
    receipts = tuple(
        A.commit_gate_decision(item, journal=journal, trusted_clock=lambda: NOW)
        for item in chain.decisions()
    )
    fenced_state = _fenced_state(control, journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.NOT_ADMITTED


# ---------------------------------------------------------------------------
# Authority semantics: role scope per gate, identity left to the configuration
# ---------------------------------------------------------------------------


def test_each_gate_admits_only_its_own_evaluator_role() -> None:
    """§22 wants four independent decisions, not one role standing in for four."""

    expected = {
        GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR,
        GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
        GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
        GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
    }
    for gate, role in expected.items():
        assert A.allowed_authority_roles(gate) == frozenset({role})
        assert A.require_role_for_gate(role, gate=gate) is role
        with pytest.raises(A.AdmissionViolation) as human:
            A.require_role_for_gate(AuthorityRole.GOVERNING_HUMAN, gate=gate)
        assert human.value.failure_code is A.AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED


@pytest.mark.parametrize("gate", list(GateKind))
def test_a_foreign_evaluator_role_is_refused_at_a_gate(gate: GateKind) -> None:
    foreign = [
        role
        for role in AuthorityRole
        if role not in A.allowed_authority_roles(gate)
    ]
    assert foreign, "every gate must have roles it refuses"
    for role in foreign:
        with pytest.raises(A.AdmissionViolation) as excinfo:
            A.require_role_for_gate(role, gate=gate)
        assert excinfo.value.failure_code is (
            A.AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED
        )


def test_a_controller_must_declare_a_role_for_every_gate() -> None:
    """A partial registration configures nothing.

    The refusal now comes from the declaration rather than from the controller,
    which is the point of the change: the missing role is a gap in the
    evaluator's entitlement, not a malformed argument to a factory.
    """

    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        controller(roles={GateKind.INGESTION: AuthorityRole.INGESTION_GATE_EVALUATOR})
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.ROLE_NOT_DECLARED


def test_a_controller_cannot_sign_a_gate_with_another_gates_role() -> None:
    with pytest.raises(A.AdmissionViolation) as excinfo:
        controller(roles={
            GateKind.INGESTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
            GateKind.PUBLICATION: AuthorityRole.PUBLICATION_GATE_EVALUATOR,
            GateKind.RETRIEVAL: AuthorityRole.RETRIEVAL_GATE_EVALUATOR,
            GateKind.CONSUMPTION: AuthorityRole.CONSUMPTION_GATE_EVALUATOR,
        })
    assert excinfo.value.failure_code is (
        A.AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED
    )


def test_a_decision_carries_the_role_of_its_own_gate() -> None:
    ingestion, publication, retrieval, consumption = full_chain(controller())
    assert ingestion.authority_role is AuthorityRole.INGESTION_GATE_EVALUATOR
    assert publication.authority_role is AuthorityRole.PUBLICATION_GATE_EVALUATOR
    assert retrieval.authority_role is AuthorityRole.RETRIEVAL_GATE_EVALUATOR
    assert consumption.authority_role is AuthorityRole.CONSUMPTION_GATE_EVALUATOR


def test_a_restored_decision_with_a_foreign_role_is_refused() -> None:
    """The matrix binds on restoration too, not only at evaluation."""

    consumption = full_chain(controller())[3]
    object.__setattr__(consumption, "authority_role", AuthorityRole.INGESTION_GATE_EVALUATOR)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.validate_gate_decision(consumption)
    assert excinfo.value.failure_code is (
        A.AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED
    )


def test_four_separate_authorities_may_decide_one_chain() -> None:
    """The norm permits separate reviewers; an earlier revision forbade it.

    §22 asks for four independent decisions with their own inputs and reason
    vocabularies. It never says one organisation must sign all four, and
    requiring identical identities across a chain would rule out exactly the
    separation of duties the section is about.
    """

    ingestion = A.evaluate_ingestion_gate(
        controller(authority="ingestion-authority"), subject_refs=SUBJECTS
    )
    publication = A.evaluate_publication_gate(
        controller(authority="publication-authority"),
        subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion,
    )
    retrieval = A.evaluate_retrieval_gate(
        controller(authority="retrieval-authority"),
        subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
        requested=REQUEST, predecessor=publication,
    )
    consumption = A.evaluate_consumption_gate(
        controller(authority="consumption-authority"),
        subject_refs=SUBJECTS, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
    )
    # The verifier holds four declarations, one per gate, exactly as the four
    # reviewers hold them. Entitlement is per gate for this reason: a single
    # declaration for the whole chain would make separation of duties unverifiable.
    chain = A.build_gate_decision_chain(
        ingestion=ingestion, publication=publication,
        retrieval=retrieval, consumption=consumption,
        **entitlement(per_gate={
            GateKind.INGESTION: "ingestion-authority",
            GateKind.PUBLICATION: "publication-authority",
            GateKind.RETRIEVAL: "retrieval-authority",
            GateKind.CONSUMPTION: "consumption-authority",
        }),
    )
    assert chain.admitted
    assert len({item.authority_identity.value for item in chain.decisions()}) == 4


def test_the_gate_authority_still_cannot_be_a_participant() -> None:
    """Relaxing the identity rule must not relax NR-08 independence."""

    with pytest.raises(A.AdmissionViolation) as excinfo:
        controller(authority="producer")
    assert excinfo.value.failure_code is (
        A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT
    )


# ---------------------------------------------------------------------------
# Point of use: the handle proves nothing on its own
# ---------------------------------------------------------------------------


def test_a_handle_is_re_checked_against_the_world_at_the_point_of_use() -> None:
    control, journal, handle = admitted_handle()
    consumption = full_chain(control)[3]
    audited = P.require_current_admitted_handle(
        handle, controller=control, journal=journal, consumption_decision=consumption,
        fence=journal.mutation_fence,
    )
    assert audited is None, "the cached decision is audit evidence, not current authority"


@pytest.mark.parametrize("domain", A.AUTHORITY_HEAD_DOMAINS)
def test_a_handle_stops_admitting_when_a_head_moves_after_minting(domain: str) -> None:
    """The gap this closes: minted valid, used after a revoke."""

    current = anchors()
    control = controller(heads=lambda: current)
    journal = Journal()
    _, _, handle = admitted_handle(control, journal)
    consumption = full_chain(control)[3]
    P.require_current_admitted_handle(
        handle, controller=control, journal=journal, consumption_decision=consumption,
        fence=journal.mutation_fence,
    )

    current["heads"][domain] = {
        "anchor_sha256": hashlib.sha256(f"{domain}-after-minting".encode()).hexdigest(),
        "sequence": 11,
    }
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_current_admitted_handle(
            handle, controller=control, journal=journal, consumption_decision=consumption,
            fence=journal.mutation_fence,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE

    # Structural validation still passes: that is exactly why the barrier exists.
    A.validate_admitted_handle(handle)


def test_a_handle_stops_admitting_when_the_boundary_is_replaced() -> None:
    current = anchors()
    control = controller(heads=lambda: current)
    journal = Journal()
    _, _, handle = admitted_handle(control, journal)
    consumption = full_chain(control)[3]
    current["boundary_ref"] = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_current_admitted_handle(
            handle, controller=control, journal=journal, consumption_decision=consumption,
            fence=journal.mutation_fence,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.HEAD_OBSERVATION_STALE


def test_a_handle_stops_admitting_when_its_decision_leaves_the_journal() -> None:
    control, journal, handle = admitted_handle()
    consumption = full_chain(control)[3]
    forgetful = Journal(forgetful=True)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_current_admitted_handle(
            handle, controller=control, journal=forgetful,
            consumption_decision=consumption, fence=forgetful.mutation_fence,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_the_barrier_refuses_a_decision_the_handle_was_not_minted_from() -> None:
    """The identity check runs first, and that ordering is observable.

    A foreign decision over *other* subjects would otherwise be reported as a
    subject mismatch — true, but describing the wrong problem. What actually
    happened is that a caller presented a decision this handle was never minted
    from, and that is what the failure has to say.
    """

    control, journal, handle = admitted_handle()
    other_subjects = tuple(
        sorted((ref(RefKind.ARTIFACT, "obj-y"), ref(RefKind.BINDING, "obj-z")), key=_key)
    )
    foreign_control = controller()
    ingestion = A.evaluate_ingestion_gate(foreign_control, subject_refs=other_subjects)
    publication = A.evaluate_publication_gate(
        foreign_control, subject_refs=other_subjects, requested=REQUEST, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        foreign_control, subject_refs=other_subjects, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, frozen_candidate_set_ref=FROZEN_SET_REF,
        requested=REQUEST, predecessor=publication,
    )
    foreign = A.evaluate_consumption_gate(
        foreign_control, subject_refs=other_subjects, consumer_context_ref=CONTEXT_REF,
        boundary_ref=BOUNDARY_REF, requested=REQUEST, predecessor=retrieval,
    )
    assert foreign.subject_keys() != handle.subject_refs[0].kind.value + ""

    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_current_admitted_handle(
            handle, controller=control, journal=journal, consumption_decision=foreign,
            fence=journal.mutation_fence,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


# ---------------------------------------------------------------------------
# CurrentAdmittedKnowledge: only the fresh production path can mint it
# ---------------------------------------------------------------------------


def _fresh_current(tmp_path):
    from tests.test_stage4_gold_consumption_evidence import production_point_of_use_case

    case = production_point_of_use_case(tmp_path)
    knowledge = P.admit_for_use_now(
        case.handle,
        binding=case.binding,
        chain=case.chain,
        evidence=case.evidence,
        entitlements=case.entitlements,
        requested=case.requested,
    )
    return case, knowledge


def test_the_revalidation_result_carries_the_admitted_binding_not_just_freshness(
    tmp_path,
) -> None:
    """A head set alone cannot say *which* knowledge was admitted.

    This is the whole reason the result is a sealed record rather than the
    fresh ``AuthorityHeadSet``: a consumer that received only the head set
    would know the world had not moved and nothing about the subjects, context,
    boundary, policy or decision it was cleared to act on.
    """

    case, knowledge = _fresh_current(tmp_path)

    assert type(knowledge) is P.CurrentAdmittedKnowledge
    P.validate_current_admitted_knowledge(knowledge)
    assert knowledge.handle_id == case.handle.handle_id
    assert knowledge.subject_refs == (case.subject,)
    assert knowledge.consumer_context_ref == case.context_ref
    assert knowledge.boundary_ref == case.boundary_ref
    assert knowledge.policy_version == "policy-v1"
    assert knowledge.consumption_decision_id == case.handle.consumption_decision_id
    assert knowledge.commit_receipt.journal_anchor != case.handle.commit_receipt.journal_anchor
    assert knowledge.journal_anchor_sha256 == case.journal.current_anchor()
    assert knowledge.observed_head_set.boundary_ref == case.boundary_ref


def test_cached_audit_performs_a_fresh_read_without_minting_authority() -> None:
    """The observation must be the read taken now, not the one taken at minting.

    A result that echoed the handle's own captured head set would be a cached
    boolean in a new wrapper — still valid-looking after a revoke, because it
    never consulted the store. With an unchanged store the two agree on every
    anchor, so the only visible difference is *when* the read happened: an
    advancing clock separates the fresh observation from the stored one, and
    that separation is what this asserts.
    """

    now = [NOW]
    current = anchors()
    reads: list[datetime] = []

    def read_heads():
        reads.append(now[0])
        return current

    control = controller(heads=read_heads, clock=lambda: now[0])
    journal = Journal()
    control, journal, chain, receipts = committed_chain(control, journal)
    fenced_state = _fenced_state(control, journal)
    handle = A.admit_for_consumption(
        chain, controller=control, subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state, journal=journal,
        **entitlement()
    )

    # Time passes between minting and use. Nothing else changes.
    now[0] = LATER

    audited = P.require_current_admitted_handle(
        handle, controller=control, journal=journal, consumption_decision=chain.consumption,
        fence=journal.mutation_fence,
    )
    assert audited is None
    assert reads[-1] == LATER
    assert len(reads) >= 3, "the cached audit must consult the live head reader"


def test_cached_audit_accepts_a_later_journal_prefix_without_minting_authority() -> None:
    """The anchor must describe the journal now, not when the receipt was issued.

    The receipt already carries the anchor witnessed at commit time, so copying
    it here would record nothing new — and the point of storing an anchor on the
    revalidation result is to pin *where the history stood when this consumer
    was cleared*. The journal legitimately grows between the two moments, and
    only a fresh read distinguishes a history that grew from one that was
    rewound and rebuilt underneath.
    """

    control, journal, chain, receipts = committed_chain()
    fenced_state = _fenced_state(control, journal)
    handle = A.admit_for_consumption(
        chain, controller=control, subject_refs=SUBJECTS,
        consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
        policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state, journal=journal,
        **entitlement()
    )

    # An unrelated decision is committed between minting and use.
    later = full_chain(controller(authority="second-authority"))[3]
    A.commit_gate_decision(later, journal=journal, trusted_clock=lambda: NOW)
    assert journal.current_anchor() != receipts[-1].journal_anchor

    audited = P.require_current_admitted_handle(
        handle, controller=control, journal=journal, consumption_decision=chain.consumption,
        fence=journal.mutation_fence,
    )
    assert audited is None
    assert journal.current_anchor() != receipts[-1].journal_anchor


def test_a_revalidation_result_cannot_be_built_by_its_constructor() -> None:
    with pytest.raises(TypeError):
        P.CurrentAdmittedKnowledge()


def test_a_revalidation_result_cannot_be_copied_into_existence(tmp_path) -> None:
    """``copy`` goes through ``__new__``, and the factory is the only way in."""

    _, knowledge = _fresh_current(tmp_path)
    with pytest.raises(TypeError):
        copy.copy(knowledge)


def test_a_reseal_of_a_revalidation_result_is_refused(tmp_path) -> None:
    """A seal is identity, not a flag: any object other than the factory's fails."""

    _, knowledge = _fresh_current(tmp_path)

    object.__setattr__(knowledge, "_trusted_seal", object())
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.validate_current_admitted_knowledge(knowledge)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.TRUSTED_OBJECT_FORGED


def test_a_revalidation_result_whose_subjects_were_swapped_is_refused(tmp_path) -> None:
    """Identity binds the payload: an edited field no longer matches knowledge_id."""

    _, knowledge = _fresh_current(tmp_path)

    object.__setattr__(
        knowledge,
        "subject_refs",
        tuple(sorted((ref(RefKind.ARTIFACT, "obj-y"), ref(RefKind.BINDING, "obj-z")), key=_key)),
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.validate_current_admitted_knowledge(knowledge)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_a_low_level_subject_swap_cannot_slip_past_the_use_site_either(tmp_path) -> None:
    """``require_admitted_subjects`` validates before it compares.

    Otherwise the barrier would be trivially defeatable: mutate the record's
    stored subject set to the smuggled one, and the comparison would agree
    with itself.
    """

    case, knowledge = _fresh_current(tmp_path)

    smuggled = (ref(RefKind.ARTIFACT, "obj-y"),)
    object.__setattr__(knowledge, "subject_refs", smuggled)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_admitted_subjects(
            knowledge, subject_refs=smuggled, consumer_context_ref=case.context_ref
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_IDENTITY_MISMATCH


def test_a_use_site_cannot_substitute_subjects_after_revalidating(tmp_path) -> None:
    """The kill for "verify one thing, use another".

    Holding a valid revalidation result is not the same as acting on it. A
    consumer cleared for {obj-a, obj-b} that compiles over {obj-a, obj-y} has
    satisfied every type in its signature while using an object no gate
    admitted, and this is the call that refuses it.
    """

    case, knowledge = _fresh_current(tmp_path)

    assert P.require_admitted_subjects(
        knowledge, subject_refs=(case.subject,), consumer_context_ref=case.context_ref
    ) is None

    smuggled = (ref(RefKind.ARTIFACT, "obj-y"),)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_admitted_subjects(
            knowledge, subject_refs=smuggled, consumer_context_ref=case.context_ref
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_a_use_site_cannot_borrow_another_consumers_clearance(tmp_path) -> None:
    """Admission is per consumer context, and a use site may not change it."""

    case, knowledge = _fresh_current(tmp_path)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_admitted_subjects(
            knowledge,
            subject_refs=(case.subject,),
            consumer_context_ref=ref(RefKind.ARTIFACT, "another-consumer"),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.STALE_DECISION


def test_a_use_site_refuses_a_dropped_subject_as_readily_as_an_added_one(tmp_path) -> None:
    """Narrowing is a substitution too: the admitted set is exact, not a bound."""

    case, knowledge = _fresh_current(tmp_path)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        P.require_admitted_subjects(
            knowledge, subject_refs=(), consumer_context_ref=case.context_ref
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_a_revalidation_result_is_canonically_serialisable(tmp_path) -> None:
    _, knowledge = _fresh_current(tmp_path)

    payload = knowledge.to_dict()
    assert payload["payload"]["schema_version"] == A.SchemaVersion.CURRENT_ADMITTED_KNOWLEDGE_V2.value
    assert json.loads(knowledge.canonical_bytes().decode()) == payload


# ---------------------------------------------------------------------------
# The journal anchor is an extension proof, not an equality
# ---------------------------------------------------------------------------


def test_a_later_append_does_not_invalidate_an_earlier_receipt() -> None:
    """Equality would be wrong: the journal legitimately grows."""

    control, journal, _, receipts = committed_chain()
    receipt = receipts[-1]
    consumption = full_chain(control)[3]
    A.require_committed_decision(receipt, decision=consumption, journal=journal)

    later = full_chain(controller(authority="second-authority"))[3]
    A.commit_gate_decision(later, journal=journal, trusted_clock=lambda: NOW)
    assert journal.current_anchor() != receipt.journal_anchor

    A.require_committed_decision(receipt, decision=consumption, journal=journal)


def test_a_forked_journal_stops_admitting_though_the_record_survives() -> None:
    """Isolates the extension proof from the membership check.

    A rollback that simply drops the record is caught by membership alone. The
    case only an extension proof sees is a *fork*: the history is rewound and
    rebuilt in another order, so the record is still present while the anchor
    the receipt witnessed is no longer a prefix of the committed history.
    """

    control = controller()
    first = full_chain(control)[3]
    second = full_chain(controller(authority="second-authority"))[3]

    journal = Journal()
    A.commit_gate_decision(first, journal=journal, trusted_clock=lambda: NOW)
    receipt = A.commit_gate_decision(second, journal=journal, trusted_clock=lambda: NOW)

    # Rewind and rebuild in the other order: both records are committed again.
    journal.rollback(0)
    A.commit_gate_decision(second, journal=journal, trusted_clock=lambda: NOW)
    A.commit_gate_decision(first, journal=journal, trusted_clock=lambda: NOW)

    assert journal.contains_record(receipt.decision_digest), "the record survived the fork"
    assert not journal.extends(receipt.journal_anchor), "its history did not"

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_committed_decision(receipt, decision=second, journal=journal)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.JOURNAL_ROLLED_BACK


def test_a_dropped_record_is_caught_by_membership() -> None:
    control, journal, _, receipts = committed_chain()
    receipt = receipts[-1]
    consumption = full_chain(control)[3]
    journal.rollback(0)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_committed_decision(receipt, decision=consumption, journal=journal)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_a_journal_without_an_extension_proof_is_refused() -> None:
    class NoExtends:
        def append_record(self, payload: bytes) -> None: ...
        def contains_record(self, digest: str) -> bool: return True
        def current_anchor(self) -> str: return "0" * 64

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_decision_journal(NoExtends())
    assert excinfo.value.failure_code is A.AdmissionFailureCode.JOURNAL_UNAVAILABLE


# ---------------------------------------------------------------------------
# §22 independence proof inside the decision identity
# ---------------------------------------------------------------------------


ACTORS = (
    ActorIdentity(value="producer"),
    ActorIdentity(value="retriever"),
    ActorIdentity(value="consumer"),
)


def test_a_decision_names_the_entitlement_it_rests_on() -> None:
    """Three digests, and each one is a separate claim about the evaluator."""

    control = controller()
    consumption = full_chain(control)[3]
    declaration = control.declaration

    assert consumption.configuration_digest == declaration.configuration_id.digest_sha256
    assert consumption.evaluator_declaration_digest == AC.declaration_digest(declaration)
    assert consumption.independence_proof_digest == control.independence_proof.proof_id.digest_sha256
    assert A.require_entitled_decision(
        consumption,
        declaration=declaration,
        proof=control.independence_proof,
        source_actors=ACTORS,
    ) is AuthorityRole.CONSUMPTION_GATE_EVALUATOR


def test_the_entitlement_survives_serialisation_and_restoration() -> None:
    """A restored decision must still say which entitlement it claims."""

    control = controller()
    consumption = full_chain(control)[3]
    restored = A.gate_decision_from_dict(
        _payload(consumption), expected_ref=A.gate_decision_ref(consumption)
    )
    assert A.require_entitled_decision(
        restored,
        declaration=control.declaration,
        proof=control.independence_proof,
        source_actors=ACTORS,
    ) is AuthorityRole.CONSUMPTION_GATE_EVALUATOR


def test_a_self_consistent_decision_from_an_undeclared_evaluator_is_refused() -> None:
    """The kill for the gap the digests exist to close.

    Before this, a decision carried only an authority identity and a role, both
    strings it stated about itself. An arbitrary identity that looked
    independent produced a decision that restored cleanly under its own
    reference, because a digest proves bytes and not entitlement. The verifier
    now brings its own declaration, and a decision made under a different one no
    longer verifies — even though it is internally perfect and its reference
    matches.
    """

    foreign = controller(authority="unregistered-authority")
    decision = full_chain(foreign)[3]
    A.gate_decision_from_dict(_payload(decision), expected_ref=A.gate_decision_ref(decision))

    trusted = controller()
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_entitled_decision(
            decision,
            declaration=trusted.declaration,
            proof=trusted.independence_proof,
            source_actors=ACTORS,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT


def test_a_proof_written_for_other_actors_does_not_verify_this_decision() -> None:
    """Independence is re-derived against the actors in play, not the stored ones."""

    control = controller()
    consumption = full_chain(control)[3]
    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        A.require_entitled_decision(
            consumption,
            declaration=control.declaration,
            proof=control.independence_proof,
            source_actors=(ActorIdentity(value="producer"), ActorIdentity(value="someone-else")),
        )
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.PROOF_SUBJECT_MISMATCH


def test_an_evaluator_cannot_be_declared_over_an_authority_it_already_holds() -> None:
    """Self-approval is refused at registration, not at decision time."""

    with pytest.raises(AC.AuthorityConfigViolation) as excinfo:
        gate_declaration(authority="taint-reviewer")
    assert excinfo.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_an_evaluator_that_is_a_participating_actor_gets_no_controller() -> None:
    """NR-08 at the point it can be enforced: the decider is not a party.

    Two barriers refuse this and the controller's own one fires first, so that
    is what is asserted. The second — ``create_gate_independence_proof``
    refusing to mint a proof whose evaluator is in its own actor set — is
    exercised directly in the adapter's tests; keeping both means neither
    depends on the other's ordering.
    """

    with pytest.raises(A.AdmissionViolation) as excinfo:
        controller(authority="producer")
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT

    with pytest.raises(AC.AuthorityConfigViolation) as direct:
        AC.create_gate_independence_proof(
            declaration=gate_declaration(authority="producer"), source_actors=ACTORS
        )
    assert direct.value.failure_code is AC.AuthorityConfigFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_a_role_swapped_into_a_decision_after_the_fact_is_refused() -> None:
    """Editing the role breaks the decision's identity before entitlement is reached.

    Worth stating because it shows the two barriers are layered rather than
    redundant: the identity check catches a role that was changed after the
    decision was sealed, and the entitlement check catches a role that was never
    granted in the first place. This test pins the first; the second cannot be
    reached through the factory, since the role is derived from the declaration
    rather than supplied, and the branch guarding it is deliberate
    defence-in-depth against a future path that does supply one.
    """

    control = controller()
    consumption = full_chain(control)[3]
    object.__setattr__(consumption, "authority_role", AuthorityRole.GOVERNING_HUMAN)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_entitled_decision(
            consumption,
            declaration=control.declaration,
            proof=control.independence_proof,
            source_actors=ACTORS,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_ROLE_NOT_PERMITTED


def test_the_controller_policy_must_be_the_declared_one() -> None:
    """An evaluator declared under one policy cannot decide under another."""

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.configure_gate_controller(
            declaration=gate_declaration(policy="policy-v1"),
            policy_version="policy-v2",
            run_id=RunId("admission-run"),
            attempt_id=AttemptId("admission-attempt"),
            repository_revision="a" * 40,
            environment_profile_id="admission-env",
            trusted_clock=lambda: NOW,
            taint_probe=lambda item: CLEAN_TAINT,
            provenance_probe=lambda item: True,
            lifecycle_probe=lambda item: True,
            compatibility_probe=lambda item, ctx: compat_finding(item, ctx),
            boundary_probe=lambda item: True,
            grant_probe=lambda: GRANT,
            head_reader=lambda: anchors(),
            producer_actor=ActorIdentity(value="producer"),
            retriever_actor=ActorIdentity(value="retriever"),
            consumer_actor=ActorIdentity(value="consumer"),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.POLICY_VERSION_MISMATCH


def test_a_decision_naming_a_foreign_declaration_is_refused() -> None:
    """The declaration digest is checked on its own, not via the proof.

    A forger who can recompute a decision's identity can also make its three
    entitlement digests disagree: keep the proof digest the verifier expects and
    swap the declaration digest for another. Every other barrier still passes —
    the decision is internally consistent, its reference matches, and the proof
    it names is the right one — so only the declaration check refuses it.
    """

    control = controller()
    foreign = controller(authority="second-authority")
    consumption = full_chain(control)[3]

    object.__setattr__(
        consumption, "evaluator_declaration_digest", AC.declaration_digest(foreign.declaration)
    )
    _rebind_mutated_v2_decision(consumption)
    A.validate_gate_decision(consumption)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_entitled_decision(
            consumption,
            declaration=control.declaration,
            proof=control.independence_proof,
            source_actors=ACTORS,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT
    assert "declaration" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Per-dimension evidence: a declared dimension must have something behind it
# ---------------------------------------------------------------------------


def test_every_declared_dimension_is_backed_by_named_evidence() -> None:
    """The check that turns a declaration into a claim with something behind it."""

    for decision in full_chain(controller()):
        evidence = A.require_dimension_evidence(decision)
        assert evidence, f"{decision.gate_kind.value} recorded no evidence at all"
        assert {item.dimension for item in evidence} == set(decision.checked_dimensions)
        for item in evidence:
            assert item.probe in A.DIMENSION_SOURCE
            assert item.dimension in {
                dim.value for dim in A.DIMENSION_SOURCE[item.probe]
            }, "evidence claims a dimension its own probe does not answer for"


def test_evidence_names_the_subject_and_context_it_was_gathered_about() -> None:
    """Evidence that cannot say what it is about proves nothing about anything."""

    consumption = full_chain(controller())[3]
    evidence = A.require_dimension_evidence(consumption)
    subject_keys = {f"{r.kind.value}\x00{r.ref_id}\x00{r.sha256}" for r in SUBJECTS}

    per_subject = [item for item in evidence if item.probe != "grant"]
    assert per_subject, "no per-subject evidence was recorded"
    for item in per_subject:
        assert item.subject_ref_key in subject_keys
    compat = [item for item in evidence if item.probe == "compatibility"]
    assert compat, "compatibility answered no dimension"
    for item in compat:
        assert item.consumer_context_key == (
            f"{CONTEXT_REF.kind.value}\x00{CONTEXT_REF.ref_id}\x00{CONTEXT_REF.sha256}"
        )


def test_a_blocked_dimension_is_recorded_as_blocked_not_omitted() -> None:
    """Absence and refusal are different states, and both must be visible."""

    blocked = full_chain(controller(lifecycle=lambda item: False))[3]
    evidence = A.require_dimension_evidence(blocked)
    lifecycle = [item for item in evidence if item.probe == "lifecycle"]
    assert lifecycle, "the lifecycle dimension vanished instead of being recorded"
    assert all(item.outcome is A.EvidenceOutcome.BLOCK for item in lifecycle)


def test_an_unavailable_probe_is_recorded_as_unavailable_not_as_a_pass() -> None:
    """NR-10 again: unknown, unavailable and false are three different states."""

    def raiser(*_args, **_kwargs):
        raise A.GateDependencyUnavailable("probe exploded")

    decision = full_chain(controller(taint=raiser))[3]
    evidence = A.require_dimension_evidence(decision)
    taint = [item for item in evidence if item.probe == "taint"]
    assert taint, "an unreachable probe left no trace"
    assert all(item.outcome is A.EvidenceOutcome.UNAVAILABLE for item in taint)
    assert not any(item.outcome is A.EvidenceOutcome.PASS for item in taint)


def test_evidence_survives_serialisation_and_restoration() -> None:
    consumption = full_chain(controller())[3]
    restored = A.gate_decision_from_dict(
        _payload(consumption), expected_ref=A.gate_decision_ref(consumption)
    )
    assert restored.dimension_evidence == consumption.dimension_evidence
    A.require_dimension_evidence(restored)


def test_a_dimension_declared_without_evidence_is_refused() -> None:
    """The kill for the defect: declaring coverage that nothing backs."""

    consumption = full_chain(controller())[3]
    object.__setattr__(
        consumption,
        "dimension_evidence",
        tuple(item for item in consumption.dimension_evidence if item.dimension != "SOURCE_TAINT"),
    )
    _rebind_mutated_v2_decision(consumption)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_dimension_evidence(consumption)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DIMENSION_NOT_CHECKED
    assert "SOURCE_TAINT" in excinfo.value.detail


def test_evidence_for_an_undeclared_dimension_is_refused() -> None:
    """Coverage that exceeds the declaration is as wrong as coverage that falls short."""

    ingestion = full_chain(controller())[0]
    smuggled = A.DimensionEvidence(
        dimension="ORACLE",
        probe="grant",
        outcome=A.EvidenceOutcome.PASS,
        subject_ref_key=None,
        consumer_context_key=None,
        evidence_sha256=hashlib.sha256(b"anything").hexdigest(),
    )
    object.__setattr__(ingestion, "dimension_evidence", ingestion.dimension_evidence + (smuggled,))
    _rebind_mutated_v2_decision(ingestion)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.require_dimension_evidence(ingestion)
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DIMENSION_NOT_CHECKED
    assert "ORACLE" in excinfo.value.detail


def test_two_different_findings_produce_two_different_evidence_digests() -> None:
    """The digest must cover the whole answer, not the part that serialises."""

    clean = full_chain(controller())[3]
    drifted = full_chain(
        controller(compat=lambda item, ctx: compat_finding(item, ctx, drifted=True))
    )[3]

    def digest_for(decision, probe):
        return {item.evidence_sha256 for item in decision.dimension_evidence if item.probe == probe}

    assert digest_for(clean, "compatibility") != digest_for(drifted, "compatibility")


# ---------------------------------------------------------------------------
# The second review: barriers that existed without standing in the path
# ---------------------------------------------------------------------------


def test_a_handle_needs_all_four_decisions_durable_not_only_the_last() -> None:
    """The kill for a handle resting on a lineage that was never written.

    A consumption ADMIT is meaningful because three earlier verdicts led to it.
    Committing only the last one made the chain a fact about memory: after a
    restart the working could not be reconstructed, and nothing noticed, because
    nothing asked.
    """

    control = controller()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    journal = Journal()
    only_last = (
        A.commit_gate_decision(decisions[3], journal=journal, trusted_clock=lambda: NOW),
    )
    fenced_state = _fenced_state(control, journal)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=only_last, fenced_state=fenced_state,
            journal=journal,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.CHAIN_NOT_DURABLE


def test_a_handle_is_refused_when_an_earlier_decision_was_never_written() -> None:
    """Four receipts are not four commits: each is checked against the journal."""

    control = controller()
    decisions = full_chain(control)
    chain = A.build_gate_decision_chain(
        ingestion=decisions[0], publication=decisions[1],
        retrieval=decisions[2], consumption=decisions[3],
        **entitlement(),
    )
    journal = Journal()
    elsewhere = Journal()
    receipts = (
        A.commit_gate_decision(decisions[0], journal=elsewhere, trusted_clock=lambda: NOW),
        *[
            A.commit_gate_decision(item, journal=journal, trusted_clock=lambda: NOW)
            for item in decisions[1:]
        ],
    )
    fenced_state = _fenced_state(control, journal)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal,
            **entitlement()
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DECISION_NOT_DURABLE


def test_building_a_chain_demands_evidence_for_every_declared_dimension() -> None:
    """The kill for a check that existed with no call site anywhere.

    ``require_dimension_evidence`` was public, correct and never invoked in
    production. A barrier nothing crosses is not a barrier — it is a function
    that happens to be right. It now runs on the mandatory path, so a decision
    whose evidence was stripped cannot reach a chain.
    """

    control = controller()
    decisions = full_chain(control)
    stripped = decisions[3]
    object.__setattr__(
        stripped,
        "dimension_evidence",
        tuple(item for item in stripped.dimension_evidence if item.dimension != "SOURCE_TAINT"),
    )
    _rebind_mutated_v2_decision(stripped)
    A.validate_gate_decision(stripped)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.build_gate_decision_chain(
            ingestion=decisions[0], publication=decisions[1],
            retrieval=decisions[2], consumption=stripped,
            **entitlement(),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.DIMENSION_NOT_CHECKED


def test_a_chain_cannot_be_built_against_another_authoritys_entitlement() -> None:
    """The negative case the required parameter does not supply on its own.

    Making `entitlements` mandatory forces a caller to pass *something*; a
    campaign mutant emptied the loop that reads it and every test still passed,
    because none of them ever passed something wrong. A barrier with no negative
    test is a parameter, not a check.
    """

    decisions = full_chain(controller())
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.build_gate_decision_chain(
            ingestion=decisions[0], publication=decisions[1],
            retrieval=decisions[2], consumption=decisions[3],
            **entitlement(authority="an-authority-that-decided-nothing"),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT


def test_a_chain_cannot_be_built_with_a_gate_left_unentitled() -> None:
    """An entitlement map missing one gate must refuse, not skip that gate."""

    decisions = full_chain(controller())
    held = entitlement()["entitlements"]
    del held[GateKind.RETRIEVAL]
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.build_gate_decision_chain(
            ingestion=decisions[0], publication=decisions[1],
            retrieval=decisions[2], consumption=decisions[3],
            entitlements=held,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT
    assert "retrieval" in excinfo.value.detail.lower()


def test_a_handle_cannot_be_minted_against_another_authoritys_entitlement() -> None:
    """The minting verifier holds its own copies, and wrong ones must refuse.

    Minting is a separate authority-use path from building, precisely because the
    two need not be the same party — so it re-establishes entitlement, and this is
    the test that the re-establishment can fail.
    """

    control, journal, chain, receipts = committed_chain()
    fenced_state = _fenced_state(control, journal)
    with pytest.raises(A.AdmissionViolation) as excinfo:
        A.admit_for_consumption(
            chain, controller=control, subject_refs=SUBJECTS,
            consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF,
            policy_version="policy-v1", receipts=receipts, fenced_state=fenced_state,
            journal=journal,
            **entitlement(authority="an-authority-that-decided-nothing"),
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT


def test_the_retrieval_gate_refuses_another_authoritys_entitlement(tmp_path) -> None:
    """The negative test the required parameter does not write for you.

    Third time in one round that a barrier was added and left without a case that
    makes it fire. A mutant replaced the whole check with `pass` and the retrieval
    suite stayed green for seventeen minutes — because every caller supplied
    correct entitlement and none supplied wrong.
    """

    from synapse.experiments.gold.retrieval import RetrievalViolation

    control = controller()
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=SUBJECTS)
    publication = A.evaluate_publication_gate(
        control, subject_refs=SUBJECTS, requested=REQUEST, predecessor=ingestion
    )
    foreign = entitlement(authority="an-authority-that-decided-nothing")["entitlements"]
    frozen = _retrieval_frozen(tmp_path)

    with pytest.raises(A.AdmissionViolation) as excinfo:
        gate_selectable_candidates(
            controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=frozen.boundary_ref, frozen=frozen,
            requested=REQUEST, publication_decision=publication,
            entitlements=foreign,
            journal=_retrieval_journal(tmp_path), trusted_clock=lambda: NOW,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.AUTHORITY_NOT_INDEPENDENT

    with pytest.raises(RetrievalViolation) as missing:
        gate_selectable_candidates(
            controller=control, candidates=SUBJECTS, consumer_context_ref=CONTEXT_REF,
            boundary_ref=frozen.boundary_ref, frozen=frozen,
            requested=REQUEST, publication_decision=publication,
            entitlements={},
            journal=_retrieval_journal(tmp_path), trusted_clock=lambda: NOW,
        )
    assert "no verifier entitlement" in str(missing.value)
