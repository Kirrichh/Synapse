"""Executable conformance checks for docs/models/REPLAY_DETERMINISM_MODEL.md.

The model is derived from the runtime rather than chosen for it, so it is only
worth anything while it still describes the runtime. Every definition and every
theorem in the document has a check here, and the three proof obligations in §7
are expressed as tests that fail while the obstruction stands.

Two of those three concern the protected runtime rather than this stage —
``FIXED_HOST_ABI_OPCODES`` is not a total classification, and ``compute_call_id``
binds no arguments. They stood here as ``xfail(strict=True)`` and no longer do:
NR-03 forbids Stage 9 to repair either table, so as acceptance they could only
record a debt another owner holds. The defects themselves are still asserted as
*present* — see §7 below — and what Stage 9 owes instead is asserted where it
belongs, which is that its own path never depends on them.
"""

from __future__ import annotations

import inspect

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import GAS_BACK_EDGE, GAS_COSTS, CognitiveVM, VMState, compute_call_id
from synapse.runtime.vm_routing import (
    FIXED_HOST_ABI_OPCODES,
    NONDETERMINISTIC_HOST_SYMBOLS,
)

def _vm(locals_map: dict | None = None, stack: list | None = None) -> CognitiveVM:
    program = BytecodeProgram(instructions=[Instruction("HALT")], constants=[], version="2.2")
    vm = CognitiveVM(program, VMState())
    vm.state.locals = dict(locals_map or {})
    vm.state.stack = list(stack or [])
    return vm


def _transition_hash(locals_map: dict, stack: list) -> str:
    vm = _vm(locals_map, stack)
    vm._hash_transition(Instruction("CALL_HOST", "LLM_EVAL", 2))
    return vm.state.transition_hash


# ---------------------------------------------------------------------------
# §1 State space
# ---------------------------------------------------------------------------


def test_state_space_matches_definition_1_1() -> None:
    """Definition 1.1 enumerates the fields of VMState."""

    declared = {
        "ip", "stack", "locals", "gas_remaining", "call_stack", "guard_stack",
        "context_stack", "actor_stack", "policy_stack", "name_save_stack",
        "mailbox_inbound", "mailbox_outbound",
        "pending_message_receive", "pending_host_call", "transition_hash",
    }
    actual = set(vars(VMState()))
    missing = declared - actual
    assert not missing, f"Definition 1.1 names fields VMState does not have: {sorted(missing)}"


# ---------------------------------------------------------------------------
# §2 Observable projection
# ---------------------------------------------------------------------------


def test_projection_observes_the_fields_definition_2_1_lists() -> None:
    source = inspect.getsource(CognitiveVM._hash_transition)
    for observed in (
        "prev", "ip", "op", "locals_keys", "stack_len", "stack_top", "gas",
        "context_stack", "actor_stack", "policy_stack",
        "mailbox_inbound", "mailbox_outbound", "pending_message_receive",
    ):
        assert f'"{observed}"' in source, f"projection no longer observes {observed}"


def test_projection_is_strictly_coarser_than_state_equality() -> None:
    """Proposition 2.3, and the demonstration for proof obligation §7.1.

    Local *values* and non-top stack entries do not reach the transition hash.
    """

    differing_local_values = _transition_hash({"secret": "A", "n": 1}, [10, 20])
    other_local_values = _transition_hash({"secret": "B", "n": 999}, [10, 20])
    assert differing_local_values == other_local_values, (
        "local values now reach the transition hash; Proposition 2.3 and "
        "proof obligation §7.1 must be revised"
    )

    differing_stack_body = _transition_hash({}, [111, 222, 9])
    other_stack_body = _transition_hash({}, [777, 888, 9])
    assert differing_stack_body == other_stack_body, (
        "non-top stack entries now reach the transition hash; §7.1 must be revised"
    )


def test_projection_does_separate_what_it_claims_to_observe() -> None:
    """The projection is coarse, not broken: what it observes, it separates."""

    assert _transition_hash({"a": 1}, [1]) != _transition_hash({"b": 1}, [1])   # key set
    assert _transition_hash({}, [1]) != _transition_hash({}, [1, 1])           # length
    assert _transition_hash({}, [1]) != _transition_hash({}, [2])              # top


# ---------------------------------------------------------------------------
# §4 Theorem 1 and its executable conformance
# ---------------------------------------------------------------------------

# The opcode obligations execute through the real governed adapter in
# ``test_stage4_od10_execution_conformance.py``.  A second set-based partition
# here would only prove that two tables agree with each other.


# ---------------------------------------------------------------------------
# §5 Theorem 2 — activity identity
# ---------------------------------------------------------------------------


def test_call_identity_does_not_separate_arguments() -> None:
    """Corollary 5.4, and the demonstration for proof obligation §7.2."""

    parameters = set(inspect.signature(compute_call_id).parameters)
    assert "args" not in parameters and "inputs" not in parameters, (
        "compute_call_id now takes an argument vector; Corollary 5.4 and proof "
        "obligation §7.2 must be revised"
    )
    fixed = dict(
        program_hash="sha256:p", ip=7, transition_hash="sha256:t",
        event_id="evt--0000005", frame_depth=0,
    )
    assert compute_call_id(**fixed) == compute_call_id(**fixed)


def test_activity_lookup_key_discharges_obligation_7_2() -> None:
    """§7.2: the replacement key satisfies what ``compute_call_id`` cannot.

    The obligation is about the key a replay *resolves by*, because that is the
    key whose collisions inject the wrong recorded result. That key is
    ``compute_activity_lookup_key``: it is computed before the result exists,
    which is when a replay needs it. The model is only discharged if it exists
    in the tree, binds the content Corollary 5.3 fixes, and separates two calls
    that differ only in their inputs. All three are checked here so that the
    document cannot claim a discharge the code does not provide.

    §23's activity identity is a second, later key that additionally binds the
    result hash; it is covered by ``tests/test_stage4_gold_activities.py``.
    """

    from synapse.experiments.gold.activities import (
        ActivityKind,
        ActivityPosition,
        activity_inputs,
        compute_activity_lookup_key,
    )

    assert set(inspect.signature(compute_activity_lookup_key).parameters) == {
        "kind", "inputs", "policy_version", "position"
    }
    position = ActivityPosition(
        program_hash="sha256:p", instruction_pointer=7, frame_depth=0, sequence=0
    )
    fixed = dict(kind=ActivityKind.LLM_CALL, policy_version="policy-v1", position=position)
    left = compute_activity_lookup_key(inputs=activity_inputs(arg=b"deep-A"), **fixed)
    right = compute_activity_lookup_key(inputs=activity_inputs(arg=b"deep-B"), **fixed)
    assert left != right, "the replacement key does not separate distinct inputs"
    assert left == compute_activity_lookup_key(inputs=activity_inputs(arg=b"deep-A"), **fixed)


def test_identity_separation_requirement_is_expressible() -> None:
    """Theorem 5.2 states a property an identity function must have."""

    def separating(inputs: tuple) -> str:
        import hashlib
        return hashlib.sha256(repr(inputs).encode()).hexdigest()

    assert separating((1, 2)) != separating((1, 3))
    assert separating((1, 2)) == separating((1, 2))


# ---------------------------------------------------------------------------
# §6 Cost function
# ---------------------------------------------------------------------------


def test_gas_is_a_total_deterministic_cost_function() -> None:
    for opcode, cost in GAS_COSTS.items():
        assert type(cost) is int and cost >= 0, f"{opcode} has a non-natural cost"
    assert GAS_COSTS["HALT"] == 0
    assert GAS_BACK_EDGE > 0


def test_effect_bearing_opcodes_cost_more_than_pure_stack_operations() -> None:
    """Cost is not the classifier, but it should not contradict it."""

    pure_max = max(GAS_COSTS[op] for op in ("LOAD_CONST", "STORE", "POP", "DUP", "ADD"))
    for opcode in ("LLM_EVAL", "LLM_REQUEST", "DREAM", "FRACTURE_SELF", "HOST_EVAL"):
        assert GAS_COSTS[opcode] > pure_max


# ---------------------------------------------------------------------------
# §7 Proof obligations, as Stage 9 can state them
# ---------------------------------------------------------------------------
#
# Two checks stood here as strict xfails, and both asserted a property of the
# protected runtime rather than of this stage: that ``FIXED_HOST_ABI_OPCODES``
# classifies every effect-bearing opcode, and that ``compute_call_id`` separates
# calls differing only in their arguments. NR-03 forbids Stage 9 to repair
# either, so as acceptance they could only ever record a debt someone else owes.
#
# What Stage 9 owes is that *its own* path never depends on those tables, and
# that is asserted where it belongs: every opcode occurrence is driven through
# the governed adapter in ``test_stage4_od10_execution_conformance.py``, and the
# replacement key separates what ``compute_call_id`` cannot
# (``test_activity_lookup_key_discharges_obligation_7_2``, below).


def test_the_two_classification_tables_are_disjoint_namespaces() -> None:
    """§7.3, second half: neither table is a complete classification.

    Opcodes and SYS_* symbols never meet, so a reader consulting one of them
    learns nothing about the other.
    """

    assert not (FIXED_HOST_ABI_OPCODES & NONDETERMINISTIC_HOST_SYMBOLS)


# ---------------------------------------------------------------------------
# §6.4 Theorem 3 — the only justified relation
# ---------------------------------------------------------------------------


def test_replay_identical_is_the_only_relation_the_runtime_implements() -> None:
    """No reconciliation rule for two differing chain hashes exists anywhere."""

    import synapse.cvm as cvm_module
    import synapse.golden_replay as golden

    for module in (cvm_module, golden):
        source = inspect.getsource(module)
        for absent in ("semantic_equivalence", "SEMANTIC_EQUIVALENT", "reconcile_hash"):
            assert absent not in source, (
                f"{module.__name__} references {absent}; Theorem 3 must be revised"
            )


# ---------------------------------------------------------------------------
# OD-10/V1-E1 — the frozen decision, checked against executable contracts
# ---------------------------------------------------------------------------
#
# OD-10 was ratified and frozen as OD-10/V1 on 2026-08-22. Before that the model
# above carried the status DERIVED PROPOSAL, and §41 forbids dependent code
# against an unfrozen decision — so what the document said and what the tree did
# could drift without either being wrong. They cannot now: the decision names
# exact contents, and the checks below read them out of the implementation.
#
# These are conformance checks, not a second definition. Each one names the thing
# OD-10/V1 froze and asserts the code still is that thing; none of them invents a
# requirement the decision does not state.


def test_od10_v1_fails_closed_on_an_unknown_opcode() -> None:
    """An opcode outside the three groups has no class and is refused."""

    from synapse.experiments.gold import replay as R

    assert R.classify_replay_opcode("ADD") == "admissible"
    assert R.classify_replay_opcode("LLM_EVAL") == "recorded_only"
    assert R.classify_replay_opcode("CALL") == "dispatch_guarded"
    with pytest.raises(R.ReplayViolation) as excinfo:
        R.classify_replay_opcode("NO_SUCH_OPCODE")
    assert excinfo.value.failure_code is R.ReplayFailureCode.OPCODE_NOT_CLASSIFIED


def test_od10_v1_freezes_the_activity_schema_contents() -> None:
    """The eight things an activity record binds, named by the decision."""

    from synapse.experiments.gold import activities as A

    fields = set(A.RecordedActivity.__dataclass_fields__)
    for required in (
        "kind", "inputs", "position", "policy_version",
        "result_sha256", "result_ref", "lookup_key", "activity_identity",
        "envelope",
    ):
        assert required in fields, f"the activity schema no longer binds {required}"

    position = set(A.ActivityPosition.__dataclass_fields__)
    assert position == {"program_hash", "instruction_pointer", "frame_depth", "sequence"}

    # Provenance travels in the §13 envelope, and the record context is what the
    # recorder must supply for it to be filled in.
    context = set(A.ActivityRecordContext.__dataclass_fields__)
    assert {"run_id", "attempt_id", "repository_revision", "environment_profile_id"} <= context


def test_od10_v1_e1_binds_the_codec_into_the_activity_identity() -> None:
    """Identity binds lookup key, result digest, result reference *and* codec.

    The codec is the one of the four that neither of the other three can see: the
    same bytes read under another codec are a different value while digest and
    reference hold still.
    """

    from synapse.experiments.gold import activities as A

    source = inspect.getsource(A.compute_activity_identity)
    for bound in (
        "lookup_key", "result_sha256", "result_ref", "ACTIVITY_RESULT_CODEC_V1_E1"
    ):
        assert bound in source, f"activity identity no longer binds {bound}"
    assert A.ACTIVITY_RESULT_CODEC_V1_E1 == (
        "synapse.stage4.gold.activity-result-codec-e1/v1"
    )


def test_od10_v1_requires_an_exact_canonical_round_trip() -> None:
    """"Результат принимается только при exact canonical round-trip."""

    from synapse.experiments.gold import replay as R

    from synapse.experiments.gold import replay_vm_codec as RVC
    for raw in (b" 1 ", b'{"b":1,"a":2}', b"[1,  2]"):
        with pytest.raises(R.ReplayViolation) as excinfo:
            RVC.decode_recorded_result(raw)
        assert excinfo.value.failure_code is R.ReplayFailureCode.RESULT_NOT_DECODABLE
    for value in (None, True, 3, "s", [1, 2], {"a": 1}):
        raw = RVC.encode_recorded_result(value)
        assert RVC.encode_recorded_result(RVC.decode_recorded_result(raw)) == raw


def test_od10_v1_freezes_the_side_effect_policy_vocabulary() -> None:
    """Three dispositions, and only one of them permits injection."""

    from synapse.experiments.gold.activities import ActivityDisposition

    assert [item.value for item in ActivityDisposition] == [
        "RECORDED_CONSUMABLE", "FORBIDDEN_IN_REPLAY", "REQUIRES_FRESH_AUTHORITY"
    ]


def test_od10_v1_admits_no_relation_but_replay_identical() -> None:
    """"Допустим только REPLAY_IDENTICAL; semantic equivalence не разрешена."""

    from synapse.experiments.gold import replay as R

    # The vocabulary, not its declaration order — the order is pinned by the
    # replay suite, and OD-10/V1 freezes which relations exist.
    assert {item.value for item in R.ReplayStatus} == {
        "REPLAY_IDENTICAL", "REPLAY_FAILED", "REPLAY_INCOMPATIBLE", "INFRA_ERROR"
    }
    identical = {
        reason for reason in R.ReplayFailureReason
        if R.status_for_reason(reason) is R.ReplayStatus.REPLAY_IDENTICAL
    }
    assert not identical, "a failure reason now produces REPLAY_IDENTICAL"


def test_od10_v1_ownership_places_each_store_under_its_owner() -> None:
    """Owners and adapters, in one direction only.

    The cycle this rules out was real: ``replay_store`` imports the replay
    contracts, and the binding factory imported ``FileReplayStore`` back, so the
    two were one module under two names while being declared two owners.
    """

    from pathlib import Path

    package = Path("synapse/experiments/gold")
    owners_and_adapters = {
        "replay_store.py": "replay.py",
        "activity_store.py": "activities.py",
        "activity_policy_store.py": "activity_policy.py",
    }
    for adapter, owner in owners_and_adapters.items():
        adapter_source = (package / adapter).read_text(encoding="utf-8")
        owner_source = (package / owner).read_text(encoding="utf-8")
        assert f"from .{Path(owner).stem} import" in adapter_source, (
            f"{adapter} does not depend on the owner it adapts"
        )
        assert f"from .{Path(adapter).stem} import" not in owner_source, (
            f"{owner} imports its own adapter {adapter}; that is a cycle, not an adapter"
        )
