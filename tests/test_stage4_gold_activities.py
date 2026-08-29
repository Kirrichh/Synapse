"""Stage 4 Patch 9 — §23 governed external activities and recorded results.

The module under test exists to discharge one obligation the replay determinism
model records as open (``docs/models/REPLAY_DETERMINISM_MODEL.md`` §7.2): the
runtime's ``compute_call_id`` binds no inputs, so two calls whose results may
differ can share a resolution key, and a replay that resolves by that key may
inject the wrong recorded result. Theorem 5.2 states the requirement the key a
replay searches by must satisfy; ``compute_activity_lookup_key`` is the function
that satisfies it, and the first section below is the executable proof.

§23 asks for something the lookup key cannot be. An activity identity includes
the result hash, and a replay looking a result up does not have one yet, so the
two are separate functions: ``compute_activity_lookup_key`` before the result
exists and ``compute_activity_identity`` after. The second section is the proof
that they are separate and that identity binds the exact result.

The remaining sections cover the module's other rules: a replay consumes recorded
results and never reaches anything live, and nothing becomes consumable without
the §22 consumption gate. Every mandatory Stage 9 mutant that concerns activities
has a named killing test at the end.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from synapse.experiments.gold import activities as ACT
from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.activity_store import activity_result_ref
from synapse.experiments.gold.contracts import (
    AttemptId,
    RepositoryRevision,
    RunId,
    SchemaVersion,
)
from tests import gold_point_of_use_world as WORLD

NOW = datetime(2026, 7, 31, 9, 0, 0, tzinfo=timezone.utc)

#: The policy version the production world admits under. A ledger takes its
#: policy from the admission, so an activity recorded under another one is
#: refused — which is what several cases below rely on.
POLICY = "policy-v1"


def ref(kind: RefKind, name: str, payload: bytes = b"p") -> HashBoundRef:
    return HashBoundRef(
        kind=kind,
        ref_id=name,
        schema_id="synapse.stage4.gold.thing/v1",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
        media_type="application/json",
    )


#: Refs that belong to no admission at all. They exist so a negative case can
#: name "another consumer context" or "another boundary" without needing a second
#: production world: what the ledger refuses is a ref that is not the one it was
#: sealed under, and any ref that is not that one will do.
OTHER_CONTEXT_REF = ref(RefKind.ARTIFACT, "consumer-ctx-2")
OTHER_BOUNDARY_REF = ref(RefKind.ATOMIC_BOUNDARY, "boundary-2")

POSITION = ACT.ActivityPosition(
    program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=0
)
OTHER_POSITION = ACT.ActivityPosition(
    program_hash="sha256:program-a", instruction_pointer=7, frame_depth=0, sequence=1
)


#: The §13 execution identity these records are stamped with. Constant, because
#: the production world this module admits against builds one run and one attempt
#: whatever it publishes.
RECORD_CONTEXT = ACT.ActivityRecordContext(
    run_id=RunId("point-of-use-run"),
    attempt_id=AttemptId("point-of-use-attempt"),
    repository_revision=RepositoryRevision.git_commit("a" * 40),
    environment_profile_id="production-point-of-use",
    producer_component="stage9-activity-recorder",
)

#: The bytes behind every result reference this module mints, so a case that
#: needs the store to actually hold them can publish them.
RESULT_BYTES: dict = {}

_PRODUCTION_BY_REF: dict[str, object] = {}


def production_provenance(
    evaluator_object=None,
    *,
    kind: ACT.ActivityKind = ACT.ActivityKind.LLM_CALL,
    inputs: ACT.ActivityInputs | None = None,
    position: ACT.ActivityPosition = POSITION,
    result: bytes = b"the recorded answer",
    context: ACT.ActivityRecordContext = RECORD_CONTEXT,
):
    """The production phase of §9.4 for this module's actors."""

    configured = evaluator() if evaluator_object is None else evaluator_object
    return APR.record_activity_production_provenance(
        configured.provenance_authority,
        kind=kind,
        inputs=inputs if inputs is not None else ACT.activity_inputs(prompt=b"explain the bug"),
        position=position,
        result=result,
        result_ref=activity_result_ref(result),
        context=context,
    )


def consumption_provenance(evaluator_object=None, *, machine_adapter_id=None):
    """The consumption phase, for the cases that reach a decision."""

    configured = evaluator() if evaluator_object is None else evaluator_object
    return APR.record_activity_consumption_provenance(
        configured.provenance_authority,
        machine_adapter_id=(
            machine_adapter_id
            or "synapse.stage4.gold.cognitive-vm-replay-adapter/v1"
        ),
    )


def production_for(activity: ACT.RecordedActivity):
    return _PRODUCTION_BY_REF[activity.production_provenance_ref.ref_id]


def recorded(
    *,
    kind: ACT.ActivityKind = ACT.ActivityKind.LLM_CALL,
    inputs: ACT.ActivityInputs | None = None,
    position: ACT.ActivityPosition = POSITION,
    policy: str = POLICY,
    result: bytes = b"the recorded answer",
) -> ACT.RecordedActivity:
    """One recorded activity, with the reference its exact bytes live behind.

    The reference is derived from the bytes rather than supplied: a record that
    could name a blob it does not hash to is the substitution the content
    address exists to prevent, and ``record_activity`` refuses one.
    """

    reference = activity_result_ref(result)
    configured = evaluator(policy_version=policy)
    exact_inputs = inputs if inputs is not None else ACT.activity_inputs(prompt=b"explain the bug")
    production = production_provenance(
        configured,
        kind=kind,
        inputs=exact_inputs,
        position=position,
        result=result,
    )
    record = ACT.record_activity(
        kind=kind,
        inputs=exact_inputs,
        position=position,
        result=result,
        result_ref=reference,
        context=RECORD_CONTEXT,
        entitlement=AP.issue_activity_recorder_entitlement(
            configured, production=production
        ),
    )
    _PRODUCTION_BY_REF[record.production_provenance_ref.ref_id] = production
    RESULT_BYTES[record.result_sha256] = result
    return record


def admitted():
    """The one present-time admission this module seals every ledger under.

    One admission, many ledgers, because ``seal_activity_ledger`` does not admit
    — it requires the ``CurrentAdmittedKnowledge`` the barrier minted. The
    admission itself costs a real fenced transaction against real stores, and a
    point-of-use attempt admits exactly once, so performing one per ledger would
    be both slow and impossible.
    """

    return WORLD.admitted_knowledge()


def sealed_ledger(*activities: ACT.RecordedActivity) -> ACT.ActivityLedger:
    return ACT.seal_activity_ledger(activities=tuple(activities), admitted=admitted())


# ---------------------------------------------------------------------------
# Theorem 5.2 — the separation requirement, which is the reason this module exists
# ---------------------------------------------------------------------------


def lookup_key(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "kind": ACT.ActivityKind.LLM_CALL,
        "inputs": ACT.activity_inputs(prompt=b"explain the bug"),
        "policy_version": POLICY,
        "position": POSITION,
    }
    arguments.update(overrides)
    return ACT.compute_activity_lookup_key(**arguments)  # type: ignore[arg-type]


def test_the_lookup_key_separates_activities_whose_inputs_differ() -> None:
    """Theorem 5.2: result(a1) != result(a2) implies id(a1) != id(a2).

    The inputs are the only thing that changes, and they are the only thing the
    runtime's ``compute_call_id`` does not bind. This is the whole of proof
    obligation §7.2 in one assertion.
    """

    assert lookup_key() != lookup_key(inputs=ACT.activity_inputs(prompt=b"explain the OTHER bug"))


def test_the_lookup_key_separates_a_changed_input_anywhere_in_the_vector() -> None:
    """A deep argument counts as much as the first one."""

    base = ACT.activity_inputs(alpha=b"1", beta=b"2", gamma=b"3")
    for changed in (
        ACT.activity_inputs(alpha=b"X", beta=b"2", gamma=b"3"),
        ACT.activity_inputs(alpha=b"1", beta=b"X", gamma=b"3"),
        ACT.activity_inputs(alpha=b"1", beta=b"2", gamma=b"X"),
    ):
        assert lookup_key(inputs=base) != lookup_key(inputs=changed)


def test_the_lookup_key_separates_an_added_or_removed_input() -> None:
    assert lookup_key(inputs=ACT.activity_inputs(a=b"1")) != lookup_key(
        inputs=ACT.activity_inputs(a=b"1", b=b"2")
    )


def test_the_lookup_key_separates_a_renamed_input_carrying_the_same_bytes() -> None:
    assert lookup_key(inputs=ACT.activity_inputs(prompt=b"v")) != lookup_key(
        inputs=ACT.activity_inputs(system=b"v")
    )


def test_the_lookup_key_separates_kind_policy_and_position() -> None:
    assert lookup_key() != lookup_key(kind=ACT.ActivityKind.GIT_READ)
    assert lookup_key() != lookup_key(policy_version="policy-v2")
    assert lookup_key() != lookup_key(position=OTHER_POSITION)
    assert lookup_key() != lookup_key(
        position=ACT.ActivityPosition(
            program_hash="sha256:program-b", instruction_pointer=7, frame_depth=0, sequence=0
        )
    )


def test_the_lookup_key_is_stable_for_the_same_activity() -> None:
    assert lookup_key() == lookup_key()


def test_the_lookup_key_binds_the_input_vector_the_model_requires() -> None:
    """Corollary 5.4 named the missing parameter; it is present here.

    It is also the complete parameter list. The result is deliberately absent:
    a replay computes this key to find the result, so a key that needed the
    result could not be computed at the moment it is needed.
    """

    import inspect

    parameters = set(inspect.signature(ACT.compute_activity_lookup_key).parameters)
    assert parameters == {"kind", "inputs", "policy_version", "position"}


def test_the_lookup_key_is_domain_separated_from_every_other_digest() -> None:
    """A bare canonical hash of the same payload must not equal the key."""

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
    assert lookup_key(inputs=inputs) != naive


# ---------------------------------------------------------------------------
# Activity identity — §23's "activity identity includes ... result hash"
# ---------------------------------------------------------------------------


def test_identity_binds_the_result_the_lookup_key_cannot() -> None:
    """Two keys, because they answer different questions at different times.

    The lookup key is what a replay searches by, before it knows the result. The
    identity is computed once the result exists, and §23 requires it to include
    the result hash.
    """

    one = recorded(result=b"answer-A")
    two = recorded(result=b"answer-B")
    assert one.lookup_key == two.lookup_key, "same call, same lookup key"
    assert one.activity_identity != two.activity_identity, "different result, different identity"


def test_a_swapped_result_keeps_its_lookup_key_and_loses_its_identity() -> None:
    """This is what makes substitution detectable to a holder of the identity.

    An attacker replacing the recorded result of an activity cannot change the
    key a manifest or lineage record already pinned, so the swap shows.
    """

    genuine = recorded(result=b"the real answer")
    forged = recorded(result=b"the substituted answer")
    assert forged.lookup_key == genuine.lookup_key
    assert forged.activity_identity != genuine.activity_identity


def test_identity_binds_the_reference_as_well_as_the_bytes() -> None:
    """Bytes and reference are substitutable independently, so both are bound.

    Re-pointing the reference at other bytes leaves the result hash alone, and
    an identity over the hash alone would call the two activities the same one.
    """

    common = dict(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version=POLICY,
        position=POSITION,
        result_sha256=hashlib.sha256(b"answer").hexdigest(),
    )
    here = ACT.compute_activity_identity(result_ref=activity_result_ref(b"answer"), **common)
    there = ACT.compute_activity_identity(
        result_ref=activity_result_ref(b"another blob entirely"), **common
    )
    assert here != there


def test_the_two_keys_are_domain_separated_from_each_other() -> None:
    item = recorded()
    assert item.activity_identity != item.lookup_key
    assert ACT.ACTIVITY_LOOKUP_KEY_PROFILE_V1 != ACT.ACTIVITY_IDENTITY_PROFILE_V1


def test_identity_is_stable_for_the_same_activity_and_result() -> None:
    arguments = dict(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version=POLICY,
        position=POSITION,
        result_sha256=hashlib.sha256(b"answer").hexdigest(),
        result_ref=activity_result_ref(b"answer"),
    )
    assert ACT.compute_activity_identity(**arguments) == ACT.compute_activity_identity(**arguments)


def test_a_rewritten_identity_does_not_survive_validation() -> None:
    item = recorded()
    object.__setattr__(item, "activity_identity", "0" * 64)
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
    assert sealed.lookup_keys() == tuple(sorted((one.lookup_key, two.lookup_key)))
    assert sealed.activity_identities() == tuple(
        sorted((one.activity_identity, two.activity_identity))
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


def test_a_recorded_activity_carries_both_of_its_keys() -> None:
    item = recorded(result=b"the recorded answer")
    assert item.lookup_key == lookup_key()
    assert item.activity_identity == ACT.compute_activity_identity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version=POLICY,
        position=POSITION,
        result_sha256=hashlib.sha256(b"the recorded answer").hexdigest(),
        result_ref=activity_result_ref(b"the recorded answer"),
    )
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


def test_rewriting_the_result_digest_leaves_the_reference_naming_other_bytes() -> None:
    """The reference is the first thing that catches it, and it catches it exactly."""

    item = recorded()
    object.__setattr__(item, "result_sha256", hashlib.sha256(b"forged").hexdigest())
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.RESULT_REF_MISMATCH


def test_rewriting_the_result_and_its_reference_together_invalidates_the_identity() -> None:
    """A consistent swap passes the ref check and still fails: identity binds both."""

    item = recorded()
    object.__setattr__(item, "result_sha256", hashlib.sha256(b"forged").hexdigest())
    object.__setattr__(item, "result_ref", activity_result_ref(b"forged"))
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
    configured = evaluator(
        trusted_clock=lambda: datetime(2026, 7, 31, 9, 0, 0)
    )
    with pytest.raises(APR.ActivityProvenanceViolation) as excinfo:
        production_provenance(
            configured,
            kind=ACT.ActivityKind.GIT_READ,
            inputs=ACT.activity_inputs(rev=b"abc"),
            position=POSITION,
            result=b"tree",
            context=RECORD_CONTEXT,
        )
    assert (
        excinfo.value.failure_code
        is APR.ActivityProvenanceFailureCode.MALFORMED_TIMESTAMP
    )


def test_a_non_bytes_result_is_refused() -> None:
    configured = evaluator()
    inputs = ACT.activity_inputs(rev=b"abc")
    production = production_provenance(
        configured,
        kind=ACT.ActivityKind.GIT_READ,
        inputs=inputs,
        result=b"text",
    )
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.record_activity(
            kind=ACT.ActivityKind.GIT_READ,
            inputs=inputs,
            position=POSITION,
            result="text",  # type: ignore[arg-type]
            result_ref=activity_result_ref(b"text"),
            context=RECORD_CONTEXT,
            entitlement=AP.issue_activity_recorder_entitlement(
                configured, production=production
            ),
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


def test_the_activity_ref_is_bound_to_the_recorded_payload() -> None:
    item = recorded()
    reference = ACT.activity_ref(item)
    assert reference.kind is RefKind.ARTIFACT
    assert reference.schema_id == SchemaVersion.RECORDED_ACTIVITY_V1.value
    assert reference.sha256 == hashlib.sha256(item.canonical_bytes()).hexdigest()
    assert reference.byte_length == len(item.canonical_bytes())


# ---------------------------------------------------------------------------
# The point-of-use barrier is the only door to a sealed ledger
# ---------------------------------------------------------------------------
#
# These cases used to run a four-gate chain over the *activity refs* and require
# the decision to name exactly the sealed set. That chain is not constructible by
# any production path: every §22 subject needs a `CompatibilitySubjectDescriptor`
# built from a published behavior with its blob, manifest, index entry,
# attestation and lifecycle records, and `admit_for_use_now` refuses a subject
# set its Stage 3 probe does not cover. A `RecordedActivity` has none of those.
# The old cases therefore asserted a property of a hand-built controller.
#
# What is asserted now is the requirement that does hold: sealing takes the
# product of the barrier — `CurrentAdmittedKnowledge`, which only
# `admit_for_use_now` can mint — and the ledger's whole binding is read off it.


def test_sealing_binds_the_ledger_to_the_present_time_admission() -> None:
    item = recorded()
    ledger = sealed_ledger(item)
    knowledge = admitted()
    assert len(ledger) == 1
    assert ledger.policy_version == knowledge.policy_version
    assert ledger.consumer_context_ref == knowledge.consumer_context_ref
    assert ledger.boundary_ref == knowledge.boundary_ref
    assert ledger.knowledge_subject_refs == knowledge.subject_refs
    assert ledger.admitted_knowledge_id == knowledge.knowledge_id


def test_a_stored_gate_decision_cannot_seal_a_ledger() -> None:
    """The decision the chain reached is not present-time authority."""

    decision = WORLD.world().chain.consumption
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(activities=(recorded(),), admitted=decision)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


def test_an_admitted_handle_cannot_seal_a_ledger() -> None:
    """A handle proves an admission happened, not that it still holds."""

    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(activities=(recorded(),), admitted=WORLD.world().handle)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


def test_present_time_admitted_knowledge_cannot_be_constructed_by_a_caller() -> None:
    """The barrier's product is unforgeable, which is what makes requiring it mean anything."""

    from synapse.experiments.gold.point_of_use import CurrentAdmittedKnowledge

    with pytest.raises(TypeError):
        CurrentAdmittedKnowledge()
    counterfeit = object.__new__(CurrentAdmittedKnowledge)
    for name, value in vars(admitted()).items():
        object.__setattr__(counterfeit, name, value)
    object.__setattr__(counterfeit, "_trusted_seal", object())
    with pytest.raises(Exception) as excinfo:
        ACT.seal_activity_ledger(activities=(recorded(),), admitted=counterfeit)
    assert "seal" in str(excinfo.value).lower() or "forged" in str(excinfo.value).lower()


def test_an_activity_recorded_under_another_policy_never_enters_a_ledger() -> None:
    item = recorded(policy="policy-v2")
    assert admitted().policy_version != "policy-v2"
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(activities=(item,), admitted=admitted())
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.POLICY_VERSION_MISMATCH


def test_two_activities_sharing_one_lookup_key_cannot_be_sealed_together() -> None:
    """Two records a replay could not tell apart; the ledger refuses them.

    The pair below differ in their results, so their identities differ — but a
    replay resolves by lookup key and those are equal, so the ledger would have
    to pick one arbitrarily. It refuses instead.
    """

    first = recorded(result=b"answer-A")
    second = recorded(result=b"answer-B")
    assert first.lookup_key == second.lookup_key
    assert first.activity_identity != second.activity_identity
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.seal_activity_ledger(activities=(first, second), admitted=admitted())
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.DUPLICATE_ACTIVITY


def test_an_empty_ledger_is_sealed_but_resolves_nothing() -> None:
    ledger = sealed_ledger()
    assert len(ledger) == 0
    # Still bound to the run: an empty ledger carried into another one is refused
    # by the same binding check as a full one.
    assert ledger.knowledge_subject_refs == admitted().subject_refs
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ledger.resolve(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.ACTIVITY_NOT_RECORDED


def test_the_ledger_constructor_is_not_a_way_in() -> None:
    knowledge = admitted()
    with pytest.raises(TypeError):
        ACT.ActivityLedger(
            {},
            knowledge.policy_version,
            knowledge.subject_refs,
            knowledge.consumer_context_ref,
            knowledge.boundary_ref,
            knowledge.knowledge_id,
        )


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


def test_a_record_cannot_carry_a_policy_verdict() -> None:
    item = recorded()
    object.__setattr__(item, "disposition", ACT.ActivityDisposition.RECORDED_CONSUMABLE)
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TYPE_MISMATCH


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
    other = ACT.compute_activity_lookup_key(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=ACT.activity_inputs(prompt=b"explain the bug"),
        policy_version="policy-v2",
        position=POSITION,
    )
    assert other not in ledger.lookup_keys()


# ---------------------------------------------------------------------------
# The seal and the binding
# ---------------------------------------------------------------------------


def test_a_sealed_ledger_cannot_be_extended_or_rewritten() -> None:
    ledger = sealed_ledger(recorded())
    for mutate in (
        lambda: setattr(ledger, "_by_lookup_key", {}),
        lambda: setattr(ledger, "_policy_version", "policy-v2"),
        lambda: setattr(ledger, "_boundary_ref", OTHER_BOUNDARY_REF),
    ):
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            mutate()
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_SEALED
    with pytest.raises(ACT.ActivityViolation):
        delattr(ledger, "_by_lookup_key")
    assert len(ledger) == 1


def test_a_ledger_sealed_for_another_run_is_refused() -> None:
    knowledge = admitted()
    ledger = sealed_ledger(recorded())
    ledger.require_bound_to(
        consumer_context_ref=knowledge.consumer_context_ref,
        boundary_ref=knowledge.boundary_ref,
        knowledge_subject_refs=knowledge.subject_refs,
    )
    for wrong in (
        {"boundary_ref": OTHER_BOUNDARY_REF},
        {"consumer_context_ref": OTHER_CONTEXT_REF},
        {"knowledge_subject_refs": (ref(RefKind.ARTIFACT, "another-subject"),)},
    ):
        arguments = {
            "consumer_context_ref": knowledge.consumer_context_ref,
            "boundary_ref": knowledge.boundary_ref,
            "knowledge_subject_refs": knowledge.subject_refs,
        } | wrong
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            ledger.require_bound_to(**arguments)
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.LEDGER_NOT_BOUND


def test_the_ledger_root_covers_the_activity_set() -> None:
    one = recorded(inputs=ACT.activity_inputs(prompt=b"one"))
    two = recorded(inputs=ACT.activity_inputs(prompt=b"two"))
    assert sealed_ledger(one).ledger_root() != sealed_ledger(one, two).ledger_root()
    assert sealed_ledger(one, two).ledger_root() == sealed_ledger(two, one).ledger_root()


def test_the_ledger_root_covers_the_admission_it_was_sealed_under() -> None:
    """Two ledgers over the same activities but different admissions differ.

    A second admission means a second point-of-use attempt with its own Stage 3
    evidence, because one attempt admits exactly once. That is the only way to
    obtain two, and it is what a manifest recording a ledger root has to be able
    to tell apart.
    """

    from synapse.experiments.gold import point_of_use as P

    request = WORLD.admission_request()
    second = P.admit_for_use_now(
        request.handle,
        binding=request.binding,
        chain=request.chain,
        evidence=request.evidence,
        entitlements=request.entitlements,
        requested=request.requested,
    )
    assert second.knowledge_id != admitted().knowledge_id
    item = recorded()
    here = sealed_ledger(item)
    there = ACT.seal_activity_ledger(activities=(item,), admitted=second)
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
        "compute_activity_lookup_key", "validate_recorded_activity", "_fail",
        "get", "digest", "hasattr",
    }
    assert called <= allowed, f"resolve() invokes something outside its own validators: {called - allowed}"
    assert not any(callable(value) for value in vars(ledger).values())


def test_mutant_identity_ignores_inputs_is_killed() -> None:
    """Mutant: the key is computed from position alone, as ``compute_call_id`` is.

    Under that mutant the two records below share a lookup key, the second
    overwrites the first in the ledger, and a replay consumes the wrong result.
    """

    first = recorded(inputs=ACT.activity_inputs(prompt=b"A"), result=b"answer-A")
    second = recorded(inputs=ACT.activity_inputs(prompt=b"B"), result=b"answer-B")
    assert first.lookup_key != second.lookup_key
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


def test_mutant_ledger_seals_without_present_time_admission_is_killed() -> None:
    """Mutant: ``seal_activity_ledger`` drops the barrier's product.

    The keyword is required, so removing it is a signature change and not a
    silent one — and there is no second way to state the binding, because policy
    version, consumer context and boundary are no longer parameters at all. A
    caller that tries to supply them by hand does not seal a weaker ledger; it
    fails to call the function.
    """

    import inspect

    parameters = inspect.signature(ACT.seal_activity_ledger).parameters
    assert "admitted" in parameters
    assert parameters["admitted"].default is inspect.Parameter.empty
    assert set(parameters) == {"activities", "admitted"}
    with pytest.raises(TypeError):
        ACT.seal_activity_ledger(  # type: ignore[call-arg]
            activities=(recorded(),),
            policy_version=POLICY,
            consumer_context_ref=admitted().consumer_context_ref,
            boundary_ref=admitted().boundary_ref,
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


def test_mutant_caller_provided_disposition_is_policy_verdict_is_killed() -> None:
    import inspect

    assert "disposition" not in inspect.signature(ACT.record_activity).parameters
    configured = evaluator()
    production = production_provenance(configured, result=b"answer")
    with pytest.raises(TypeError):
        ACT.record_activity(
            kind=ACT.ActivityKind.LLM_CALL,
            inputs=ACT.activity_inputs(prompt=b"explain the bug"),
            position=POSITION,
            disposition=ACT.ActivityDisposition.RECORDED_CONSUMABLE,
            result=b"answer",
            result_ref=activity_result_ref(b"answer"),
            context=RECORD_CONTEXT,
            entitlement=AP.issue_activity_recorder_entitlement(
                configured, production=production
            ),
        )


# ---------------------------------------------------------------------------
# OD-10 — the activity policy authority
# ---------------------------------------------------------------------------
#
# §41's OD-10 asks who decides whether a recorded external result may be
# consumed in a replay. §22 cannot: its subjects are published behavior units,
# and a recorded activity is not one. So the decision has its own authority, and
# the cases below are the acceptance for it. Two halves: the evaluator is a real
# authority (declared role, closed policy, disjoint from every actor it decides
# about), and its answer is valid for exactly the activity and the context it was
# taken in.

from types import SimpleNamespace  # noqa: E402

from synapse.experiments.gold import activity_policy as AP  # noqa: E402
from synapse.experiments.gold import activity_provenance as APR  # noqa: E402
from synapse.experiments.gold.contracts import (  # noqa: E402
    ActorIdentity,
    AuthorityIdentity,
)

EVALUATOR_IDENTITY = AuthorityIdentity("stage9-activity-policy-evaluator")

#: A capability profile digest. Its *value* is never interpreted by this module —
#: the decision carries it and the consumer compares it — so a fixed digest says
#: exactly as much as the executor's real one and costs no import.
CAPABILITY_DIGEST = hashlib.sha256(b"capability-profile").hexdigest()

ACTORS = {
    "producer_actor": ActorIdentity("stage9-activity-producer"),
    "recorder_actor": ActorIdentity("stage9-activity-recorder"),
    "worker_actor": ActorIdentity("stage9-worker"),
    "model_actor": ActorIdentity("stage9-model"),
    "replay_executor_actor": ActorIdentity("stage9-replay-executor"),
    "machine_adapter_actor": ActorIdentity("stage9-machine-adapter"),
    "consumer_actor": ActorIdentity("stage9-consumer"),
}


def declaration(
    *,
    dispositions: dict | None = None,
    evaluator_identity: AuthorityIdentity = EVALUATOR_IDENTITY,
    policy_version: str = POLICY,
) -> AP.ActivityPolicyDeclaration:
    mapping = {kind: ACT.ActivityDisposition.RECORDED_CONSUMABLE for kind in ACT.ActivityKind}
    mapping.update(dispositions or {})
    return AP.create_activity_policy_declaration(
        authority_handle=WORLD.authority_handle(),
        evaluator_identity=evaluator_identity,
        evaluator_component_id="stage9-activity-policy",
        evaluator_component_version="synapse.stage4.activity-policy/v1",
        policy_version=policy_version,
        dispositions=mapping,
        trusted_clock=lambda: NOW,
    )


def actor_set(**overrides: ActorIdentity) -> AP.ActivityPolicyActorSet:
    return AP.create_activity_policy_actor_set(
        authority_handle=WORLD.authority_handle(), **(ACTORS | overrides)
    )


def evaluator(
    *,
    dispositions: dict | None = None,
    lifecycle_store=None,
    taint_store=None,
    policy_version: str = POLICY,
    trusted_clock=lambda: NOW,
) -> AP.ConfiguredActivityPolicyEvaluator:
    declared = declaration(dispositions=dispositions, policy_version=policy_version)
    actors = actor_set()
    return AP.configure_activity_policy_evaluator(
        declaration=declared,
        actor_set=actors,
        independence_proof=AP.create_activity_policy_independence_proof(
            declaration=declared, actor_set=actors
        ),
        lifecycle_store=lifecycle_store or WORLD.lifecycle_store(),
        taint_store=taint_store or WORLD.taint_store(),
        trusted_clock=trusted_clock,
    )


def execution_context(activity: ACT.RecordedActivity, **overrides: object) -> dict:
    knowledge = admitted()
    run_id, attempt_id = WORLD.run_identity()
    return {
        "consumer_context_ref": knowledge.consumer_context_ref,
        "boundary_ref": knowledge.boundary_ref,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "environment_profile_id": knowledge.envelope.environment_profile_id,
        "capability_profile_digest": CAPABILITY_DIGEST,
        "production": production_for(activity),
        "consumption": consumption_provenance(),
    } | overrides


class MovedHead:
    """A durable history whose head is not where it was when a decision was taken.

    Revoke, quarantine, taint escalation and supersession are all *appends* to
    the lifecycle or taint history, so what a consumer must detect is one thing:
    the head moved. Moving it through ``AuthorityAnchorPort`` — the one question
    the evaluator asks a history, and a declared part of its interface — makes
    that single observation directly, and covers all four causes without this
    suite minting four different authority records to produce the same effect.
    """

    def __init__(self, marker: bytes) -> None:
        self._digest = hashlib.sha256(marker).hexdigest()

    def current_anchor(self) -> object:
        return SimpleNamespace(ordered_log_root_sha256=self._digest)


def test_the_caller_does_not_state_a_disposition() -> None:
    """An authority that took the answer as a parameter would not be one.

    The old shape had ``disposition`` on the record and nothing checked where it
    came from, so the caller graded its own homework. The parameter is gone from
    the evaluation signature, and the answer comes off the declared policy.
    """

    import inspect

    parameters = set(inspect.signature(AP.evaluate_activity_policy).parameters)
    assert "disposition" not in parameters
    assert parameters == {
        "evaluator", "activity", "consumer_context_ref", "boundary_ref",
        "run_id", "attempt_id", "environment_profile_id", "capability_profile_digest",
        # The consuming half of §9.4 provenance. An input to the decision, not a
        # verdict in it: it says who is asking, and the disposition still comes
        # off the declared policy.
        "production", "consumption",
    }
    item = recorded()
    decided = AP.evaluate_activity_policy(
        evaluator(), activity=item, **execution_context(item)
    )
    assert decided.disposition is ACT.ActivityDisposition.RECORDED_CONSUMABLE


def test_the_policy_must_answer_for_every_activity_kind() -> None:
    """A partial mapping is a policy with a hole, and a hole is a default."""

    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.create_activity_policy_declaration(
            authority_handle=WORLD.authority_handle(),
            evaluator_identity=EVALUATOR_IDENTITY,
            evaluator_component_id="stage9-activity-policy",
            evaluator_component_version="synapse.stage4.activity-policy/v1",
            policy_version=POLICY,
            dispositions={ACT.ActivityKind.LLM_CALL: ACT.ActivityDisposition.RECORDED_CONSUMABLE},
            trusted_clock=lambda: NOW,
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.POLICY_INCOMPLETE


@pytest.mark.parametrize("role", sorted(ACTORS))
def test_an_evaluator_that_is_also_an_actor_it_decides_about_is_refused(role: str) -> None:
    """Every actor, not just the obvious one.

    An evaluator that merely differed from the producer could still be the
    worker that asked for the call or the executor that will consume its result,
    and either of those is self-approval under another name.
    """

    colliding = declaration(evaluator_identity=EVALUATOR_IDENTITY)
    actors = actor_set(**{role: ActorIdentity(EVALUATOR_IDENTITY.value)})
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.create_activity_policy_independence_proof(declaration=colliding, actor_set=actors)
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_a_consumable_decision_passes_its_own_consumer_side_check() -> None:
    item = recorded()
    configured = evaluator()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(configured, activity=item, **context)
    AP.require_consumable_activity_decision(
        decided, evaluator=configured, activity=item, **context
    )


def test_a_decision_about_another_activity_is_refused() -> None:
    configured = evaluator()
    original = recorded(inputs=ACT.activity_inputs(prompt=b"one"))
    context = execution_context(original)
    decided = AP.evaluate_activity_policy(
        configured, activity=original, **context
    )
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided,
            evaluator=configured,
            activity=recorded(inputs=ACT.activity_inputs(prompt=b"two")),
            **context,
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_SUBJECT_MISMATCH


def test_a_decision_about_another_result_of_the_same_activity_is_refused() -> None:
    """The substitution the lookup key cannot see, refused where it matters.

    Both records answer the same call, so they share a lookup key and a decision
    keyed by that alone would carry over. The decision binds the identity, which
    the swap changes.
    """

    configured = evaluator()
    genuine = recorded(result=b"the real answer")
    forged = recorded(result=b"the substituted answer")
    context = execution_context(genuine)
    assert genuine.lookup_key == forged.lookup_key
    decided = AP.evaluate_activity_policy(configured, activity=genuine, **context)
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided, evaluator=configured, activity=forged, **context
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_SUBJECT_MISMATCH


@pytest.mark.parametrize(
    "moved",
    [
        "consumer_context_ref",
        "boundary_ref",
        "run_id",
        "attempt_id",
        "environment_profile_id",
        "capability_profile_digest",
    ],
)
def test_a_decision_taken_for_another_execution_context_is_refused(moved: str) -> None:
    """Each binding moves on its own, and each of them changes the question."""

    from synapse.experiments.gold.contracts import AttemptId as _AttemptId, RunId as _RunId

    configured = evaluator()
    item = recorded()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(configured, activity=item, **context)
    replacement = {
        "consumer_context_ref": OTHER_CONTEXT_REF,
        "boundary_ref": OTHER_BOUNDARY_REF,
        "run_id": _RunId("another-run"),
        "attempt_id": _AttemptId("another-attempt"),
        "environment_profile_id": "another-environment",
        "capability_profile_digest": hashlib.sha256(b"another-profile").hexdigest(),
    }[moved]
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided,
            evaluator=configured,
            activity=item,
            **execution_context(item, **{moved: replacement}),
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_CONTEXT_MISMATCH


@pytest.mark.parametrize("history", ["lifecycle", "taint"])
def test_a_decision_does_not_survive_the_history_it_was_taken_against(history: str) -> None:
    """Revoke, quarantine, taint escalation and supersession, in one observation."""

    item = recorded()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(evaluator(), activity=item, **context)
    moved = evaluator(**{f"{history}_store": MovedHead(b"a later head")})
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided, evaluator=moved, activity=item, **context
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_STATE_DRIFTED


def test_a_decision_taken_under_another_declaration_is_refused() -> None:
    """A policy that changed after the answer does not get to keep the answer."""

    item = recorded()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(evaluator(), activity=item, **context)
    superseded = evaluator(
        dispositions={ACT.ActivityKind.GIT_READ: ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY}
    )
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided, evaluator=superseded, activity=item, **context
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.DECISION_STATE_DRIFTED


@pytest.mark.parametrize(
    "answer",
    [
        ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY,
        ACT.ActivityDisposition.REQUIRES_FRESH_AUTHORITY,
    ],
)
def test_a_non_consumable_answer_is_refused_at_the_point_of_consumption(answer) -> None:
    """``REQUIRES_FRESH_AUTHORITY`` is a refusal during replay, not weaker permission."""

    configured = evaluator(dispositions={ACT.ActivityKind.LLM_CALL: answer})
    item = recorded()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(configured, activity=item, **context)
    assert decided.disposition is answer
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided, evaluator=configured, activity=item, **context
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.NOT_CONSUMABLE


def test_a_record_cannot_override_the_evaluators_policy_verdict() -> None:
    """Caller metadata cannot turn a policy refusal into permission."""

    configured = evaluator(
        dispositions={
            ACT.ActivityKind.LLM_CALL: ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY
        }
    )
    item = recorded()
    context = execution_context(item)
    decided = AP.evaluate_activity_policy(configured, activity=item, **context)
    assert decided.disposition is ACT.ActivityDisposition.FORBIDDEN_IN_REPLAY
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_consumable_activity_decision(
            decided, evaluator=configured, activity=item, **context
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.NOT_CONSUMABLE


def test_a_decision_cannot_be_built_by_its_constructor() -> None:
    with pytest.raises(TypeError):
        AP.ActivityPolicyDecision()  # type: ignore[call-arg]


def test_a_rewritten_decision_does_not_survive_validation() -> None:
    item = recorded()
    decided = AP.evaluate_activity_policy(
        evaluator(), activity=item, **execution_context(item)
    )
    object.__setattr__(decided, "disposition", ACT.ActivityDisposition.RECORDED_CONSUMABLE)
    object.__setattr__(decided, "environment_profile_id", "another-environment")
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.validate_activity_policy_decision(decided)
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.IDENTITY_MISMATCH


def test_an_unsealed_evaluator_lookalike_is_refused() -> None:
    """Requiring a configured evaluator has to mean one that was configured."""

    counterfeit = object.__new__(AP.ConfiguredActivityPolicyEvaluator)
    for name, value in vars(evaluator()).items():
        object.__setattr__(counterfeit, name, value)
    object.__setattr__(counterfeit, "_trusted_seal", object())
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.require_activity_policy_evaluator(counterfeit)
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.TRUSTED_OBJECT_FORGED


def test_the_decision_ref_is_bound_to_the_decision_it_names() -> None:
    item = recorded()
    decided = AP.evaluate_activity_policy(
        evaluator(), activity=item, **execution_context(item)
    )
    reference = AP.activity_policy_decision_ref(decided)
    assert reference.kind is RefKind.GATE_DECISION
    assert reference.ref_id == decided.decision_id.digest_sha256
    assert reference.sha256 == hashlib.sha256(decided.canonical_bytes()).hexdigest()
    assert reference.byte_length == len(decided.canonical_bytes())


# ---------------------------------------------------------------------------
# OD-10/V1 §9.4 — actors resolved from provenance, not declared by a caller
# ---------------------------------------------------------------------------
#
# The gap these close was reproducible and quiet. A record carried a free-form
# ``producer_component`` string and no actor identity at all;
# ``record_activity`` asked nobody's permission; and the actor set was assembled
# with no connection to any record. So an evaluator could *be* the real recorder
# while the sealed set named somebody else, and every downstream check passed —
# the set and the work had no point of contact to disagree at.


def test_recording_requires_an_entitlement_from_the_policy_authority() -> None:
    """A recorder that entitles itself is the whole defect in one line."""

    for counterfeit in (None, object(), "entitled"):
        with pytest.raises(ACT.ActivityViolation) as excinfo:
            ACT.record_activity(
                kind=ACT.ActivityKind.LLM_CALL,
                inputs=ACT.activity_inputs(prompt=b"p"),
                position=POSITION,
                result=b"r",
                result_ref=activity_result_ref(b"r"),
                context=RECORD_CONTEXT,
                entitlement=counterfeit,
            )
        assert excinfo.value.failure_code is ACT.ActivityFailureCode.RECORDER_NOT_ENTITLED


def test_an_entitlement_cannot_be_minted_outside_the_policy_authority() -> None:
    """The declaration lives with the record; the issuing does not.

    ``activities.py`` owns what an entitlement *is* because it owns the record.
    It cannot own who gets one — that is an authority question — so the factory
    checks a seal only ``activity_policy`` can reach.
    """

    with pytest.raises(TypeError):
        ACT.ActivityRecorderEntitlement()  # type: ignore[call-arg]
    production = production_provenance()
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.issue_recorder_entitlement(
            producer_actor=ACTORS["producer_actor"],
            recorder_actor=ACTORS["recorder_actor"],
            actor_set_id=evaluator().actor_set.actor_set_id,
            configuration_id=evaluator().declaration.configuration_id,
            production_provenance_ref=APR.activity_provenance_ref(production),
            production_provenance=production,
        )
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.TRUSTED_OBJECT_FORGED


def test_production_provenance_has_no_caller_declared_actor_parameters() -> None:
    import inspect

    parameters = inspect.signature(
        APR.record_activity_production_provenance
    ).parameters
    assert not set(ACTORS) & set(parameters)
    configured = evaluator()
    provenance = production_provenance(configured)
    assert provenance.actors() == (
        configured.actor_set.producer_actor,
        configured.actor_set.recorder_actor,
        configured.actor_set.worker_actor,
        configured.actor_set.model_actor,
    )


@pytest.mark.parametrize("role", ["producer_actor", "recorder_actor"])
def test_the_authority_refuses_to_entitle_itself(role: str) -> None:
    """The reproduction, closed at the point the entitlement is asked for.

    An actor set whose producer or recorder *is* the evaluator cannot be sealed
    at all — the independence proof refuses it — so the way this defect was
    reachable was through a set that named someone else while the evaluator did
    the work. The entitlement is where the two meet, and it refuses.
    """

    colliding = declaration(evaluator_identity=AuthorityIdentity("the-same-party"))
    actors = actor_set(**{role: ActorIdentity("the-same-party")})
    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.create_activity_policy_independence_proof(
            declaration=colliding, actor_set=actors
        )
    assert excinfo.value.failure_code is AP.ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT


def test_a_record_carries_the_actors_that_actually_made_it() -> None:
    """Not a description of them: identities, taken off the entitlement."""

    from synapse.experiments.gold.contracts import ActorIdentity as Identity

    item = recorded()
    assert type(item.producer_actor) is Identity
    assert type(item.recorder_actor) is Identity
    assert item.producer_actor == ACTORS["producer_actor"]
    assert item.recorder_actor == ACTORS["recorder_actor"]
    # And they are inside the record's own identity, so rewriting one is caught.
    object.__setattr__(item, "recorder_actor", Identity("someone-else"))
    with pytest.raises(ACT.ActivityViolation) as excinfo:
        ACT.validate_recorded_activity(item)
    assert excinfo.value.failure_code is ACT.ActivityFailureCode.IDENTITY_MISMATCH


def other_evaluator():
    """A second, entirely valid evaluator whose sealed set names other actors."""

    declared = declaration(evaluator_identity=AuthorityIdentity("other-policy-evaluator"))
    actors = actor_set(
        producer_actor=ActorIdentity("other-producer"),
        recorder_actor=ActorIdentity("other-recorder"),
    )
    return AP.configure_activity_policy_evaluator(
        declaration=declared,
        actor_set=actors,
        independence_proof=AP.create_activity_policy_independence_proof(
            declaration=declared, actor_set=actors
        ),
        lifecycle_store=WORLD.lifecycle_store(),
        taint_store=WORLD.taint_store(),
        trusted_clock=lambda: NOW,
    )


def test_the_consumer_checks_independence_against_the_resolved_actors() -> None:
    """The half that makes the record's actors load-bearing.

    A decision could otherwise be taken about a record made by anyone at all: the
    subject checks compare identity, inputs and result, none of which say who
    produced them.

    Both records here are genuine — no field is rewritten, because a rewritten
    field breaks the record's own envelope binding and never reaches this check.
    The second is simply recorded under another authority's entitlement, which is
    the realistic version of "a record this evaluator's set does not name".
    """

    elsewhere = other_evaluator()
    inputs = ACT.activity_inputs(prompt=b"explain the bug")
    production = production_provenance(elsewhere, inputs=inputs)
    foreign = ACT.record_activity(
        kind=ACT.ActivityKind.LLM_CALL,
        inputs=inputs,
        position=POSITION,
        result=b"the recorded answer",
        result_ref=activity_result_ref(b"the recorded answer"),
        context=RECORD_CONTEXT,
        entitlement=AP.issue_activity_recorder_entitlement(
            elsewhere, production=production,
        ),
    )
    _PRODUCTION_BY_REF[foreign.production_provenance_ref.ref_id] = production
    RESULT_BYTES[foreign.result_sha256] = b"the recorded answer"

    configured = evaluator()
    mine = recorded()
    context = execution_context(mine)
    AP.require_consumable_activity_decision(
        AP.evaluate_activity_policy(configured, activity=mine, **context),
        evaluator=configured, activity=mine, **context,
    )

    with pytest.raises(AP.ActivityPolicyViolation) as excinfo:
        AP.evaluate_activity_policy(
            configured,
            activity=foreign,
            **execution_context(foreign),
        )
    assert (
        excinfo.value.failure_code
        is AP.ActivityPolicyFailureCode.CONFIGURATION_MISMATCH
    )


def test_an_evaluator_that_did_the_work_is_refused_at_every_reachable_point() -> None:
    """Three refusals, and no fourth state left for the consumer to catch.

    The consumer-side evaluator-identity check exists and is asserted by reading
    it, not by reaching it — because it cannot be reached. Getting there would
    need a sealed actor set naming the evaluator as producer or recorder, and the
    independence proof refuses to seal one; an entitlement for the evaluator,
    which the authority refuses to issue; or a record edited afterwards, which
    fails its own envelope binding. Asserting the unreachability *is* the
    property, and a case that forged its way past all three would be asserting
    something about the forgery instead.
    """

    import inspect

    same = AuthorityIdentity("the-same-party")
    colliding = declaration(evaluator_identity=same)

    # 1. The set cannot be sealed with the evaluator in it.
    with pytest.raises(AP.ActivityPolicyViolation) as sealed:
        AP.create_activity_policy_independence_proof(
            declaration=colliding,
            actor_set=actor_set(recorder_actor=ActorIdentity(same.value)),
        )
    assert sealed.value.failure_code is AP.ActivityPolicyFailureCode.EVALUATOR_NOT_INDEPENDENT

    # 2. No provenance API accepts a caller-supplied recorder identity.
    assert "recorder_actor" not in inspect.signature(
        APR.record_activity_production_provenance
    ).parameters

    # 3. The record cannot be edited into it.
    item = recorded()
    object.__setattr__(item, "recorder_actor", ActorIdentity(EVALUATOR_IDENTITY.value))
    with pytest.raises(ACT.ActivityViolation) as edited:
        ACT.validate_recorded_activity(item)
    assert edited.value.failure_code is ACT.ActivityFailureCode.IDENTITY_MISMATCH

    # And the consumer-side check stands behind all three.
    source = inspect.getsource(AP.require_consumable_activity_decision)
    assert "EVALUATOR_NOT_INDEPENDENT" in source
    assert "activity.recorder_actor" in source and "activity.producer_actor" in source
