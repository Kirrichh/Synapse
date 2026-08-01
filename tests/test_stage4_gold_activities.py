"""Stage 4 Patch 9 — §23 governed external activities and recorded results.

The module under test exists to discharge one obligation the replay determinism
model records as open (``docs/models/REPLAY_DETERMINISM_MODEL.md`` §7.2): the
runtime's ``compute_call_id`` binds no inputs, so two calls whose results may
differ can share an identity, and a replay that resolves by identity may inject
the wrong recorded result. Theorem 5.2 states the requirement an identity must
satisfy; ``compute_activity_identity`` is the function that satisfies it, and
the first section below is the executable proof.

The remaining sections cover the module's three other rules: a recorded result is
bound to its activity by the idempotency key §23 calls activity identity; a
replay consumes recorded results and never reaches anything live; and nothing
becomes consumable without the §22 consumption gate. Every mandatory Stage 9
mutant that concerns activities has a named killing test at the end.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold import admission as A
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.contracts import (
    ActorIdentity,
    AuthorityIdentity,
    AuthorityRole,
    SchemaVersion,
    create_stage4_authority_configuration,
    create_stage4_authority_handle,
)

NOW = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)
POLICY = "policy-v1"

POSITION = ACT.ActivityPosition(
    program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=0
)
OTHER_POSITION = ACT.ActivityPosition(
    program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=1
)


def ref(kind: RefKind, name: str, payload: bytes = b"p") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx")
BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-1")
OTHER_CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx-2")
OTHER_BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")

GRANT = A.GrantEnvelope(
    scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version=POLICY
)
REQUEST = A.RequestedEnvelope(scopes=("repo:x",), capabilities=("read",), oracles=("swebench",))
CLEAN_TAINT = A.TaintFinding(
    consumable=True, chain_complete=True, quarantined=False, blocks_publication=False
)
CLEAN_COMPAT = A.CompatibilityFinding(
    compatible=True, evidence_complete=True, drifted=False, conflicts_unresolved=False
)


def controller(policy: str = POLICY) -> A.ConfiguredGateController:
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
    grant = A.GrantEnvelope(
        scopes=("repo:x",), capabilities=("read",), oracles=("swebench",), policy_version=policy
    )
    return A.configure_gate_controller(
        authority_handle=create_stage4_authority_handle(configuration),
        authority_identity=AuthorityIdentity(value="gate-authority"),
        authority_role=AuthorityRole.PUBLICATION_REVIEWER,
        policy_version=policy,
        trusted_clock=lambda: NOW,
        taint_probe=lambda item: CLEAN_TAINT,
        provenance_probe=lambda item: True,
        lifecycle_probe=lambda item: True,
        compatibility_probe=lambda item: CLEAN_COMPAT,
        boundary_probe=lambda item: True,
        grant_probe=lambda: grant,
        producer_actor=ActorIdentity(value="producer"),
        retriever_actor=ActorIdentity(value="retriever"),
        consumer_actor=ActorIdentity(value="consumer"),
    )


def consumption_decision(
    subject_refs: tuple[HashBoundRef, ...],
    *,
    consumer_context_ref: HashBoundRef = CONTEXT_REF,
    boundary_ref: HashBoundRef = BOUNDARY_REF,
    policy: str = POLICY,
) -> A.GateDecision:
    """Drive the full four-gate chain and return its consumption verdict."""

    control = controller(policy)
    ingestion = A.evaluate_ingestion_gate(control, subject_refs=subject_refs)
    publication = A.evaluate_publication_gate(
        control, subject_refs=subject_refs, requested=REQUEST, predecessor=ingestion
    )
    retrieval = A.evaluate_retrieval_gate(
        control, subject_refs=subject_refs, consumer_context_ref=consumer_context_ref,
        requested=REQUEST, predecessor=publication,
    )
    return A.evaluate_consumption_gate(
        control, subject_refs=subject_refs, consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref, requested=REQUEST, predecessor=retrieval,
    )


def recorded(
    *,
    kind: ACT.ActivityKind = ACT.ActivityKind.LLM_CALL,
    inputs: ACT.ActivityInputs | None = None,
    position: ACT.ActivityPosition = POSITION,
    policy: str = POLICY,
    disposition: ACT.ActivityDisposition = ACT.ActivityDisposition.RECORDED_CONSUMABLE,
    result: bytes = b"the recorded answer",
) -> ACT.RecordedActivity:
    return ACT.record_activity(
        kind=kind,
        inputs=inputs if inputs is not None else ACT.activity_inputs(prompt=b"explain the bug"),
        position=position,
        policy_version=policy,
        disposition=disposition,
        result=result,
        recorded_at_utc=NOW,
    )


def sealed_ledger(
    *activities: ACT.RecordedActivity,
    consumer_context_ref: HashBoundRef = CONTEXT_REF,
    boundary_ref: HashBoundRef = BOUNDARY_REF,
    policy: str = POLICY,
) -> ACT.ActivityLedger:
    items = tuple(activities)
    subject_refs = (
        A.canonical_subject_refs(tuple(ACT.activity_ref(item) for item in items))
        if items
        else (ref(RefKind.ARTIFACT, "no-activity"),)
    )
    decision = consumption_decision(
        subject_refs,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
        policy=policy,
    )
    return ACT.seal_activity_ledger(
        activities=items,
        policy_version=policy,
        consumption_decision=decision,
        consumer_context_ref=consumer_context_ref,
        boundary_ref=boundary_ref,
    )


# ---------------------------------------------------------------------------
# Theorem 5.2 — the separation requirement, which is the reason this module exists
# ---------------------------------------------------------------------------


def identity(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "kind": ACT.ActivityKind.LLM_CALL,
        "inputs": ACT.activity_inputs(prompt=b"explain the bug"),
        "policy_version": POLICY,
        "position": POSITION,
    }
    arguments.update(overrides)
    return ACT.compute_activity_identity(**arguments)  # type: ignore[arg-type]


def test_identity_separates_activities_whose_inputs_differ() -> None:
    """Theorem 5.2: result(a1) != result(a2) implies id(a1) != id(a2).

    The inputs are the only thing that changes, and they are the only thing the
    runtime's ``compute_call_id`` does not bind. This is the whole of proof
    obligation §7.2 in one assertion.
    """

    assert identity() != identity(inputs=ACT.activity_inputs(prompt=b"explain the OTHER bug"))


def test_identity_separates_a_changed_input_anywhere_in_the_vector() -> None:
    """A deep argument counts as much as the first one."""

    base = ACT.activity_inputs(alpha=b"1", beta=b"2", gamma=b"3")
    for changed in (
        ACT.activity_inputs(alpha=b"X", beta=b"2", gamma=b"3"),
        ACT.activity_inputs(alpha=b"1", beta=b"X", gamma=b"3"),
        ACT.activity_inputs(alpha=b"1", beta=b"2", gamma=b"X"),
    ):
        assert identity(inputs=base) != identity(inputs=changed)


def test_identity_separates_an_added_or_removed_input() -> None:
    assert identity(inputs=ACT.activity_inputs(a=b"1")) != identity(
        inputs=ACT.activity_inputs(a=b"1", b=b"2")
    )


def test_identity_separates_a_renamed_input_carrying_the_same_bytes() -> None:
    assert identity(inputs=ACT.activity_inputs(prompt=b"v")) != identity(
        inputs=ACT.activity_inputs(system=b"v")
    )


def test_identity_separates_kind_policy_and_position() -> None:
    assert identity() != identity(kind=ACT.ActivityKind.GIT_READ)
    assert identity() != identity(policy_version="policy-v2")
    assert identity() != identity(position=OTHER_POSITION)
    assert identity() != identity(
        position=ACT.ActivityPosition(
            program_hash="sha256:program-b", instruction_pointer=7, frame_depth=0, sequence=0
        )
    )


def test_identity_is_stable_for_the_same_activity() -> None:
    assert identity() == identity()


def test_identity_binds_the_input_vector_the_model_requires() -> None:
    """Corollary 5.4 named the missing parameter; it is present here."""

    import inspect

    parameters = set(inspect.signature(ACT.compute_activity_identity).parameters)
    assert parameters == {"kind", "inputs", "policy_version", "position"}


def test_identity_is_domain_separated_from_every_other_digest() -> None:
    """A bare canonical hash of the same payload must not equal the identity."""

    inputs = ACT.activity_inputs(prompt=b"v")
    naive = hashlib.sha256(
        ACT._canonical(
            {
                "kind": ACT.ActivityKind.LLM_CALL.value,
                "inputs": inputs.to_dict(),
                "policy_version": POLICY,
                "position": POSITION.to_dict(),
            }
        )
    ).hexdigest()
    assert identity(inputs=inputs) != naive


# ---------------------------------------------------------------------------
# The idempotency key — §23's "activity identity includes ... result hash"
# ---------------------------------------------------------------------------


def test_the_idempotency_key_binds_the_result_the_lookup_key_cannot() -> None:
    """Two keys, because they answer different questions at different times.

    The lookup key is what a replay searches by, before it knows the result. The
    idempotency key is computed once the result exists, and §23 requires it to
    include the result hash.
    """

    one = recorded(result=b"answer-A")
    two = recorded(result=b"answer-B")
    assert one.activity_identity == two.activity_identity, "same call, same lookup key"
    assert one.idempotency_key != two.idempotency_key, "different result, different binding"


def test_a_swapped_result_keeps_its_lookup_key_and_loses_its_binding() -> None:
    """This is what makes substitution detectable to a holder of the key.

    An attacker replacing the recorded result of an activity cannot change the
    key a manifest or lineage record already pinned, so the swap shows.
    """

    genuine = recorded(result=b"the real answer")
    forged = recorded(result=b"the substituted answer")
    assert forged.activity_identity == genuine.activity_identity
    assert forged.idempotency_key != genuine.idempotency_key


def test_the_two_keys_are_domain_separated_from_each_other() -> None:
    item = recorded()
    assert item.idempotency_key != item.activity_identity
    assert ACT.ACTIVITY_IDEMPOTENCY_PROFILE_V1 != ACT.ACTIVITY_IDENTITY_PROFILE_V1


def test_the_idempotency_key_is_stable_for_the_same_activity_and_result() -> None:
    arguments = dict(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version=POLICY,
        position=POSITION,
        result_sha256=hashlib.sha256(b"answer").hexdigest(),
    )
    assert ACT.compute_activity_idempotency_key(
        **arguments
    ) == ACT.compute_activity_idempotency_key(**arguments)


def test_a_rewritten_idempotency_key_does_not_survive_validation() -> None:
    item = recorded()
    object.__setattr__(item, "idempotency_key", "0" * 64)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.IDENTITY_MISMATCH


def test_the_ledger_publishes_the_refs_and_keys_a_request_pins() -> None:
    one = recorded(inputs=ACT.activity_inputs(prompt=b"one"))
    two = recorded(inputs=ACT.activity_inputs(prompt=b"two"))
    sealed = sealed_ledger(one, two)
    assert sealed.activity_refs() == tuple(
        ACT.activity_ref(item) for item in sealed.recorded()
    )
    assert sealed.idempotency_keys() == tuple(
        sorted((one.idempotency_key, two.idempotency_key))
    )
    assert len(sealed.recorded()) == 2


# ---------------------------------------------------------------------------
# ActivityInputs
# ---------------------------------------------------------------------------


def test_inputs_carry_digests_and_never_the_payload() -> None:
    secret = b"sk-live-do-not-log"
    inputs = ACT.activity_inputs(token=secret)
    rendered = repr(inputs.to_dict())
    assert secret.decode() not in rendered
    assert hashlib.sha256(secret).hexdigest() in rendered


def test_an_activity_with_no_inputs_is_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.activity_inputs()
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.INPUTS_MISSING


def test_inputs_must_be_exact_bytes() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.activity_inputs(prompt="a string")  # type: ignore[arg-type]
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


def test_unordered_inputs_are_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.ActivityInputs((("b", "0" * 64), ("a", "1" * 64)))
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.INPUTS_UNORDERED


def test_duplicate_input_names_are_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.ActivityInputs((("a", "0" * 64), ("a", "1" * 64)))
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.INPUTS_DUPLICATE


def test_a_non_hex_input_digest_is_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.ActivityInputs((("a", "Z" * 64),))
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.MALFORMED_SHA256


def test_inputs_survive_a_dict_round_trip() -> None:
    inputs = ACT.activity_inputs(a=b"1", b=b"2")
    assert ACT.ActivityInputs.from_dict(inputs.to_dict()) == inputs
    assert ACT.ActivityInputs.from_dict(inputs.to_dict()).digest() == inputs.digest()


def test_too_many_inputs_are_refused() -> None:
    entries = tuple((f"n{index:04d}", "0" * 64) for index in range(300))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.ActivityInputs(entries)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.RESOURCE_LIMIT_EXCEEDED


# ---------------------------------------------------------------------------
# RecordedActivity
# ---------------------------------------------------------------------------


def test_a_recorded_activity_cannot_be_built_by_its_constructor() -> None:
    with pytest.raises(TypeError):
        ACT.RecordedActivity()  # type: ignore[call-arg]


def test_a_recorded_activity_carries_its_own_identity() -> None:
    item = recorded()
    assert item.activity_identity == identity()
    assert item.schema_version is SchemaVersion.RECORDED_ACTIVITY_V1
    ACT.validate_recorded_activity(item)


def test_the_result_is_stored_as_a_digest() -> None:
    item = recorded(result=b"the recorded answer")
    assert item.result_sha256 == hashlib.sha256(b"the recorded answer").hexdigest()
    assert "the recorded answer" not in repr(item.to_dict())


def test_rewriting_a_recorded_field_invalidates_the_activity() -> None:
    item = recorded()
    object.__setattr__(item, "policy_version", "policy-v2")
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.IDENTITY_MISMATCH


def test_rewriting_the_result_digest_invalidates_the_record_id() -> None:
    item = recorded()
    object.__setattr__(item, "result_sha256", hashlib.sha256(b"forged").hexdigest())
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.IDENTITY_MISMATCH


def test_an_unsealed_lookalike_is_refused() -> None:
    forged = object.__new__(ACT.RecordedActivity)
    for name, value in vars(recorded()).items():
        object.__setattr__(forged, name, value)
    object.__setattr__(forged, "_trusted_seal", object())
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(forged)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TRUSTED_OBJECT_FORGED


def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.record_activity(
            kind=ACT.ActivityKind.GIT_READ,
            inputs=ACT.activity_inputs(rev=b"abc"),
            position=POSITION,
            policy_version=POLICY,
            disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
            result=b"tree",
            recorded_at_utc=datetime(2026, 7, 31, 9, 0, 0),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.MALFORMED_TIMESTAMP


def test_a_non_bytes_result_is_refused() -> None:
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        recorded(result="text")  # type: ignore[arg-type]
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


def test_the_activity_ref_is_bound_to_the_recorded_payload() -> None:
    item = recorded()
    reference = ACT.activity_ref(item)
    assert reference.kind is RefKind.ARTIFACT
    assert reference.schema_id == SchemaVersion.RECORDED_ACTIVITY_V1.value
    assert reference.sha256 == hashlib.sha256(item.canonical_bytes()).hexdigest()
    assert reference.byte_length == len(item.canonical_bytes())


# ---------------------------------------------------------------------------
# The consumption gate is the only door to a sealed ledger
# ---------------------------------------------------------------------------


def test_sealing_requires_a_consumption_decision_for_this_exact_set() -> None:
    item = recorded()
    ledger = sealed_ledger(item)
    assert len(ledger) == 1
    assert ledger.admitted_refs == (ACT.activity_ref(item),)


def test_a_decision_for_another_activity_set_does_not_seal_this_one() -> None:
    admitted = recorded(inputs=ACT.activity_inputs(prompt=b"admitted"))
    smuggled = recorded(inputs=ACT.activity_inputs(prompt=b"smuggled"))
    decision = consumption_decision((ACT.activity_ref(admitted),))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(smuggled,),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_an_extra_activity_cannot_ride_along_on_an_admitted_decision() -> None:
    admitted = recorded(inputs=ACT.activity_inputs(prompt=b"admitted"))
    extra = recorded(inputs=ACT.activity_inputs(prompt=b"extra"))
    decision = consumption_decision((ACT.activity_ref(admitted),))
    with pytest.raises(A.AdmissionViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(admitted, extra),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.SUBJECT_MISMATCH


def test_a_decision_from_another_boundary_does_not_seal() -> None:
    item = recorded()
    decision = consumption_decision(
        (ACT.activity_ref(item),), boundary_ref=OTHER_BOUNDARY_REF
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(item,),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.STALE_DECISION


def test_a_decision_from_another_consumer_context_does_not_seal() -> None:
    item = recorded()
    decision = consumption_decision(
        (ACT.activity_ref(item),), consumer_context_ref=OTHER_CONTEXT_REF
    )
    with pytest.raises(A.AdmissionViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(item,),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is A.AdmissionFailureCode.STALE_DECISION


def test_an_activity_recorded_under_another_policy_never_enters_a_ledger() -> None:
    item = recorded(policy="policy-v2")
    decision = consumption_decision((ACT.activity_ref(item),))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(item,),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.POLICY_VERSION_MISMATCH


def test_two_activities_sharing_one_identity_cannot_be_sealed_together() -> None:
    """Two identical records collapse to one identity; the ledger refuses them."""

    first = recorded()
    second = recorded()
    assert first.activity_identity == second.activity_identity
    decision = consumption_decision((ACT.activity_ref(first),))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(
            activities=(first, second),
            policy_version=POLICY,
            consumption_decision=decision,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.DUPLICATE_ACTIVITY


def test_an_empty_ledger_is_sealed_but_admits_nothing() -> None:
    ledger = sealed_ledger()
    assert len(ledger) == 0
    assert ledger.admitted_refs == ()
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_the_ledger_constructor_is_not_a_way_in() -> None:
    with pytest.raises(TypeError):
        ACT.ActivityLedger({}, POLICY, (), CONTEXT_REF, BOUNDARY_REF)


# ---------------------------------------------------------------------------
# Resolution: consume, never re-execute
# ---------------------------------------------------------------------------


def test_a_recorded_activity_resolves_to_its_recorded_result() -> None:
    item = recorded(result=b"answer")
    ledger = sealed_ledger(item)
    found = ledger.resolve(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        position=POSITION,
    )
    assert found.result_sha256 == hashlib.sha256(b"answer").hexdigest()


def test_an_unrecorded_activity_fails_and_never_falls_back_to_a_live_call() -> None:
    ledger = sealed_ledger(recorded())
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"a prompt nobody recorded"),
            position=POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_the_same_call_at_another_position_is_another_activity() -> None:
    ledger = sealed_ledger(recorded(position=POSITION))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=OTHER_POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_a_forbidden_activity_is_refused_even_when_it_was_recorded() -> None:
    item = recorded(disposition=ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY)
    ledger = sealed_ledger(item)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.FORBIDDEN_IN_REPLAY


def test_an_activity_requiring_fresh_authority_is_never_replayed_from_record() -> None:
    item = recorded(disposition=ACT.ActivityDisposition.REQUIRES_FRESH_AUTHORITY)
    ledger = sealed_ledger(item)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.FRESH_CALL_ATTEMPTED


def test_substituted_result_bytes_are_detected() -> None:
    ledger = sealed_ledger(recorded(result=b"answer"))
    query = dict(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        position=POSITION,
    )
    assert ledger.require_result(result=b"answer", **query)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.require_result(result=b"a different answer", **query)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.RESULT_HASH_MISMATCH


def test_resolution_under_another_policy_version_misses() -> None:
    """The ledger resolves under its own policy version, not the caller's wish."""

    ledger = sealed_ledger(recorded())
    assert ledger.policy_version == POLICY
    other = ACT.compute_activity_identity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version="policy-v2",
        position=POSITION,
    )
    assert other not in ledger.identities()


# ---------------------------------------------------------------------------
# The seal and the binding
# ---------------------------------------------------------------------------


def test_a_sealed_ledger_cannot_be_extended_or_rewritten() -> None:
    ledger = sealed_ledger(recorded())
    for mutate in (
        lambda: setattr(ledger, "_by_identity", {}),
        lambda: setattr(ledger, "_policy_version", "policy-v2"),
        lambda: setattr(ledger, "_boundary_ref", OTHER_BOUNDARY_REF),
    ):
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            mutate()
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_SEALED
    with pytest.raises(ACT.ActivityViolation):
        delattr(ledger, "_by_identity")
    assert len(ledger) == 1


def test_a_ledger_admitted_for_another_run_is_refused() -> None:
    ledger = sealed_ledger(recorded())
    ledger.require_bound_to(consumer_context_ref=CONTEXT_REF, boundary_ref=BOUNDARY_REF)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.require_bound_to(
            consumer_context_ref=CONTEXT_REF, boundary_ref=OTHER_BOUNDARY_REF
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_NOT_BOUND
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.require_bound_to(
            consumer_context_ref=OTHER_CONTEXT_REF, boundary_ref=BOUNDARY_REF
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_NOT_BOUND


def test_the_ledger_root_covers_the_activity_set() -> None:
    one = recorded(inputs=ACT.activity_inputs(prompt=b"one"))
    two = recorded(inputs=ACT.activity_inputs(prompt=b"two"))
    assert sealed_ledger(one).ledger_root() != sealed_ledger(one, two).ledger_root()
    assert sealed_ledger(one, two).ledger_root() == sealed_ledger(two, one).ledger_root()


def test_the_ledger_root_covers_the_binding() -> None:
    """Two ledgers over the same activities but different runs differ."""

    item = recorded()
    here = sealed_ledger(item)
    there = sealed_ledger(item, boundary_ref=OTHER_BOUNDARY_REF)
    assert here.ledger_root() != there.ledger_root()


# ---------------------------------------------------------------------------
# Mandatory mutation killers
# ---------------------------------------------------------------------------


def test_mutant_replay_reinvokes_an_external_activity_is_killed() -> None:
    """Mutant: resolution falls back to a live producer on a ledger miss.

    The ledger holds no producer to fall back to and its resolution path calls
    nothing but its own validators, so the mutant cannot be written without
    adding a dependency the assertions below would see. Three halves are
    checked: a miss raises, the resolution path invokes only known-pure
    helpers, and no callable is reachable from the sealed object.
    """

    ledger = sealed_ledger(recorded())
    with pytest.raises(ACT.ActivityViolation):
        ledger.resolve(
            kind=ACT.ActivityKind.NETWORK_FETCH,
            inputs=ACT.activity_inputs(url=b"https://example.invalid"),
            position=POSITION,
        )

    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ACT.ActivityLedger.resolve)))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    allowed = {
        "compute_activity_identity", "validate_recorded_activity", "_fail",
        "get", "digest",
    }
    assert called <= allowed, f"resolve() invokes something outside its own validators: {called - allowed}"
    assert not any(callable(value) for value in vars(ledger).values())


def test_mutant_identity_ignores_inputs_is_killed() -> None:
    """Mutant: identity is computed from position alone, as ``compute_call_id`` is.

    Under that mutant the two records below share an identity, the second
    overwrites the first in the ledger, and a replay consumes the wrong result.
    """

    first = recorded(inputs=ACT.activity_inputs(prompt=b"A"), result=b"answer-A")
    second = recorded(inputs=ACT.activity_inputs(prompt=b"B"), result=b"answer-B")
    assert first.activity_identity != second.activity_identity
    ledger = sealed_ledger(first, second)
    assert len(ledger) == 2
    for prompt, expected in ((b"A", b"answer-A"), (b"B", b"answer-B")):
        found = ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=prompt),
            position=POSITION,
        )
        assert found.result_sha256 == hashlib.sha256(expected).hexdigest()


def test_mutant_ledger_seals_without_a_consumption_decision_is_killed() -> None:
    """Mutant: ``seal_activity_ledger`` drops the gate call.

    A decision that admits nothing relevant must not produce a ledger. The
    keyword is required, so removing the barrier is a signature change and not a
    silent one.
    """

    import inspect

    parameters = inspect.signature(ACT.seal_activity_ledger).parameters
    assert "consumption_decision" in parameters
    assert parameters["consumption_decision"].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        ACT.seal_activity_ledger(  # type: ignore[call-arg]
            activities=(recorded(),),
            policy_version=POLICY,
            consumer_context_ref=CONTEXT_REF,
            boundary_ref=BOUNDARY_REF,
        )


def test_mutant_result_bytes_are_accepted_unverified_is_killed() -> None:
    ledger = sealed_ledger(recorded(result=b"answer"))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.require_result(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
            result=b"answer ",
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.RESULT_HASH_MISMATCH


def test_mutant_a_forbidden_disposition_is_ignored_is_killed() -> None:
    for disposition, code in (
        (ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY, ACT.ActivityFailureCode.FORBIDDEN_IN_REPLAY),
        (
            ACT.ActivityDisposition.REQUIRES_FRESH_AUTHORITY,
            ACT.ActivityFailureCode.FRESH_CALL_ATTEMPTED,
        ),
    ):
        ledger = sealed_ledger(recorded(disposition=disposition))
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            ledger.resolve(
                kind=ACT.ActivityKind.LLM_CALL,
                inputs=ACT.activity_inputs(prompt=b"explain the bug"),
                position=POSITION,
            )
        assert excinfo.value.failure_code is code
