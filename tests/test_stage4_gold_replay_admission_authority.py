"""Stage 4 authority, barrier, and sealed admission acceptance shard."""

from __future__ import annotations

from tests.stage4_gold_replay_support import *  # noqa: F403


def test_mutant_a_stale_admission_still_replays_is_killed() -> None:
    """Mutant B1: ``execute_replay`` stops re-checking the admission.

    This is the audit finding of round A stated as a test. The request is built
    under an admission that holds; the world then moves on, exactly as it does
    whenever another point-of-use attempt is admitted; and the same request is
    handed to the same machines. Before the fix this reached
    ``REPLAY_IDENTICAL`` — a run whose authority the system had already
    classified as stale. §22 puts the consumption decision immediately before
    replay, so a request that outlived its admission must not execute at all.
    """

    unit, _binding = pure_behavior()
    prepared = pure_prepared()
    request = prepared.request()

    # The world moves: a second attempt is admitted over the same subject.
    WORLD.admit(WORLD.admission_request(published_core(unit)))

    # The public path cannot express this any more — it admits and runs as one
    # act — so the case is stated where a restored request would arrive: at the
    # executor, which re-checks the admission it was handed against the world as
    # it is now.
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._require_current_admission(
            request, authority=prepared._last_binding.authority
        )
    assert excinfo.value.failure_code is R.ReplayFailureCode.ADMISSION_NOT_CURRENT

def test_a_forged_authority_is_not_reported_as_a_stale_admission() -> None:
    """NR-10: a forged binding and a stale admission are different facts.

    The first revision of the point-of-use re-check caught every exception and
    reported all of them as ``ADMISSION_NOT_CURRENT``. That reads as caution and
    is the opposite: an object that was never a production binding, a store that
    cannot be reached and an admission the world has moved past would all arrive
    at the caller wearing the mildest of the three labels, and §2 names that
    status relabelling outright.
    """

    from synapse.experiments.gold.admission import AdmissionFailureCode, AdmissionViolation

    prepared = pure_prepared()
    request = prepared.request()

    class NotABinding:
        def open_current_snapshot(self):  # pragma: no cover - must never be reached
            raise AssertionError("a forged binding was asked for the current snapshot")

    with pytest.raises(AdmissionViolation) as excinfo:
        R._require_current_admission(request, authority=NotABinding())
    assert excinfo.value.failure_code is AdmissionFailureCode.TRUSTED_OBJECT_FORGED

def test_mutant_a_binding_for_an_unadmitted_behavior_is_accepted_is_killed() -> None:
    """Mutant B2: the validator stops tying compiled programs to admitted refs.

    The factory ties each subject reference to the unit it names, but a factory
    check protects only objects that went through the factory. A restored or
    mutated request can carry the admitted references of one behavior beside the
    compiled binding of another, and until this comparison existed nothing
    downstream would notice: the machine would run a program no gate ever saw,
    under an admission that names something else.
    """

    prepared = pure_prepared()
    request = prepared.request()
    R.validate_replay_request(request)
    stranger = unit_with(contract_for(scripted_transitions(["ADD"])), literal=77)
    assert stranger.content_key.digest_sha256 not in {
        item.ref_id for item in request.knowledge_subject_refs
    }
    original = request.bindings
    object.__setattr__(
        request,
        "bindings",
        (R.replay_program_binding(unit=stranger, binding=compile_behavior_unit(stranger)),),
    )
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.SUBJECT_NOT_ADMITTED
    finally:
        object.__setattr__(request, "bindings", original)
        _reseal(request)

def test_mutant_a_forged_admission_identity_is_accepted_is_killed() -> None:
    """Mutant B3: the admission identities go back to being unresolved fields.

    ``admitted_knowledge_id`` and ``consumption_decision_id`` were checked only
    for being record identities, so a consistently forged request could name any
    admission and any decision it liked. They are now resolved against the
    admission object the request carries, which nothing outside
    ``admit_for_use_now`` can mint.
    """

    from synapse.experiments.gold.contracts import IdentityDomain, compute_record_id

    prepared = pure_prepared()
    request = prepared.request()
    R.validate_replay_request(request)
    impostor = compute_record_id(
        domain=IdentityDomain.BEHAVIOR_REPLAY_REQUEST, canonical_bytes=b"another-admission"
    )
    for field_name in ("admitted_knowledge_id", "consumption_decision_id"):
        original = getattr(request, field_name)
        object.__setattr__(request, field_name, impostor)
        _reseal(request)
        try:
            with pytest.raises(R.ReplayViolation) as excinfo:
                R.validate_replay_request(request)
            assert excinfo.value.failure_code is R.ReplayFailureCode.IDENTITY_MISMATCH
        finally:
            object.__setattr__(request, field_name, original)
            _reseal(request)

def test_mutant_a_ledger_from_another_admission_is_accepted_is_killed() -> None:
    """Mutant B5: the ledger stops being tied to *this* request's admission.

    Two ledgers sealed in the same world agree on everything ``require_bound_to``
    can see — consumer context, boundary, admitted subject set, policy version —
    because those describe the world, not the moment. What separates them is
    which revalidation admitted them, and a request that accepted either one
    would let an activity set sealed under an earlier admission travel into a
    run admitted by a later one.
    """

    prepared = pure_prepared()
    request = prepared.request()
    R.validate_replay_request(request)
    unit, _binding = pure_behavior()
    elsewhere = ACT.seal_activity_ledger(
        activities=(), admitted=WORLD.admitted_knowledge(published_core(unit))
    )
    assert elsewhere.activity_refs() == request.recorded_activity_refs
    assert (
        elsewhere.admitted_knowledge_id.digest_sha256
        != request.ledger.admitted_knowledge_id.digest_sha256
    ), "the two ledgers must rest on different admissions for this case to exist"

    original = request.ledger
    object.__setattr__(request, "ledger", elsewhere)
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.LEDGER_NOT_BOUND
    finally:
        object.__setattr__(request, "ledger", original)
        _reseal(request)

def test_mutant_the_snapshot_is_the_boundary_again_is_killed() -> None:
    """Mutant B4: ``knowledge_snapshot_id`` goes back to being the boundary id.

    §21 gives the selected knowledge state and the transaction that publishes it
    separate identities. A request that names the boundary twice has not said
    which snapshot it read, and the field that was supposed to say so becomes
    decoration.
    """

    prepared = pure_prepared()
    request = prepared.request()
    R.validate_replay_request(request)
    assert request.knowledge_snapshot_id != request.boundary_ref.ref_id
    original_id = request.knowledge_snapshot_id
    original_ref = request.snapshot_manifest_ref
    object.__setattr__(request, "knowledge_snapshot_id", request.boundary_ref.ref_id)
    object.__setattr__(request, "snapshot_manifest_ref", request.boundary_ref)
    _reseal(request)
    try:
        with pytest.raises(R.ReplayViolation) as excinfo:
            R.validate_replay_request(request)
        assert excinfo.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH
    finally:
        object.__setattr__(request, "knowledge_snapshot_id", original_id)
        object.__setattr__(request, "snapshot_manifest_ref", original_ref)
        _reseal(request)

def test_a_request_that_declares_no_predecessor_cannot_be_resumed() -> None:
    """Мутант A12: снята проверка «продолжение обязано назвать предшественника».

    Каждый случай выше подавал продолжающий запрос, поэтому ветка отсутствующей
    линии не исполнялась ни разу. Обычный запрос — не продолжение, и попытка
    возобновить его отвергается типизированно, а не падает по дороге.
    """

    first = pure_prepared().run()
    plain = pure_prepared()
    request = plain.request()
    assert request.resumed_from_result_ref is None
    with pytest.raises(R.ReplayViolation) as excinfo:
        R._require_resume_lineage(request, resumed_from=first)
    assert excinfo.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH

def test_the_request_reads_its_authority_off_the_admission_not_the_caller() -> None:
    """There is nothing left for a caller to assert about its own entitlement."""

    import inspect

    parameters = set(inspect.signature(R._create_replay_request).parameters)
    for name in (
        "knowledge_snapshot_id",
        "consumption_decision",
        "knowledge_subject_refs",
        "consumer_context_ref",
        "boundary_ref",
        "policy_version",
    ):
        assert name not in parameters, (
            f"{name} is a caller assertion about authority; it belongs to the admission"
        )
    prepared = pure_prepared()
    request = prepared.request()
    assert request.knowledge_snapshot_id == request.snapshot_manifest_ref.ref_id
    assert request.policy_version == POLICY

def test_the_barrier_is_crossed_before_anything_is_compiled() -> None:
    """The order is a sequence this function performs, not a flag a caller sets."""

    unit, _binding = pure_behavior()
    order: list[tuple[str, int]] = []
    core = published_core(unit)
    provider = WORLD.platform_observation_provider(core)
    calls_before = provider.calls

    def watching_compiler(value):
        order.append(("compile", provider.calls))
        return compile_behavior_unit(value)

    prepare_for(unit, compiler=watching_compiler).request()
    assert order and order[0][0] == "compile"
    assert order[0][1] > calls_before, "compilation started before the first fresh barrier"

def test_a_ledger_is_sealed_by_the_request_against_its_own_admission() -> None:
    """A ledger cannot be sealed elsewhere and carried in.

    One point-of-use attempt admits exactly once, so a request and a
    separately-sealed ledger could never share an admission. The request seals
    its own, and there is no parameter through which another one could arrive.
    """

    import inspect

    assert "ledger" not in inspect.signature(R._create_replay_request).parameters
    prepared = pure_prepared()
    request = prepared.request()
    knowledge_id = request.ledger.admitted_knowledge_id
    assert knowledge_id == request.admitted_knowledge_id
    assert request.ledger.knowledge_subject_refs == request.knowledge_subject_refs

def test_production_ingress_derives_policy_version_from_the_sealed_evaluator() -> None:
    unit, fixture = llm_artifact_behavior(prompt="sealed policy version")
    prepared = prepare_for(unit, activities=(fixture,))
    binding = prepared._governed()["binding"]
    restored = prepared.bundle.activity_store.require_record(
        ACT.activity_ref(prepared._durable_activities[0])
    )
    production = prepared.bundle.activity_policy_store.require_production_provenance_for_activity(
        restored.production_provenance_ref,
        evaluator=binding.activity_policy_evaluator,
        activity=restored,
    )
    assert restored.policy_version == binding.activity_policy_evaluator.declaration.policy_version
    assert production.policy_version == restored.policy_version

def test_a_binding_from_another_unit_is_refused() -> None:
    from synapse.experiments.gold.canonicalization import (
        CanonicalizationFailureCode,
        CanonicalizationViolation,
    )

    _unit, binding = pure_behavior()
    other_unit = unit_with(contract_for(("some-other-transition",)))
    with pytest.raises(CanonicalizationViolation) as excinfo:
        # A compiler that hands back the binding of a different unit. Injecting
        # the compiler buys nothing, because its output is revalidated against
        # the unit it was asked about.
        prepare_for(other_unit, compiler=lambda _value: binding).request()
    assert excinfo.value.failure_code is CanonicalizationFailureCode.COMPILER_BINDING_MISMATCH

def test_a_request_does_not_accept_its_own_replay_contract() -> None:
    import inspect

    assert "replay_contract" not in inspect.signature(RC.run_governed_replay).parameters
    assert "replay_contract" not in inspect.signature(R._create_replay_request).parameters
