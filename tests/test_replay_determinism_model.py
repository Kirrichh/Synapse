"""Executable conformance checks for docs/models/REPLAY_DETERMINISM_MODEL.md.

The model is derived from the runtime rather than chosen for it, so it is only
worth anything while it still describes the runtime. Every definition and every
theorem in the document has a check here, and the three proof obligations in §7
are expressed as tests that fail while the obstruction stands.

Two of those three are marked ``xfail(strict=True)``: they describe defects the
model surfaced, they are expected to fail today, and they will fail *loudly* the
day someone fixes the defect without updating the model.
"""

from __future__ import annotations

import inspect

import pytest

from synapse.bytecode import BytecodeProgram, Instruction
from synapse.cvm import GAS_BACK_EDGE, GAS_COSTS, CognitiveVM, VMState, compute_call_id
from synapse.runtime.vm_routing import (
    FIXED_HOST_ABI_OPCODES,
    NONDETERMINISTIC_HOST_SYMBOLS,
    classify_host_opcode,
)

# §4 Corollary 4.2 — the partition induced by Definition 3.1.
CATEGORY_A_OPCODES = frozenset(
    {
        "LOAD_CONST", "LOAD_NAME", "LOAD_NONE", "LOAD_TRUE", "LOAD_FALSE",
        "STORE", "POP", "DUP", "SAVE_NAME", "RESTORE_NAME",
        "JUMP", "JUMP_IF_FALSE", "JUMP_IF_TRUE", "CALL", "RETURN",
        "MAKE_FUNCTION", "HALT",
        "ADD", "SUB", "MUL", "DIV", "MOD",
        "EQ", "NEQ", "LT", "GT", "LTE", "GTE",
        "AND", "OR", "NOT", "UNARY_NEG",
        "BUILD_LIST", "BUILD_DICT", "INDEX", "MEMBER",
        "CONTEXT_ENTER", "CONTEXT_EXIT",
        "ACTOR_ENTER", "ACTOR_EXIT",
        "POLICY_ENTER", "POLICY_EXIT", "POLICY_RULE_ENTER", "POLICY_RULE_EXIT",
        "GUARD_ENTER", "GUARD_EXIT", "GUARD_CHECK_RESULT", "GUARD_VIOLATION_ACK",
        "CALL_METHOD",
        "RECEIVE_ENTER", "RECEIVE_EXIT",
    }
)

CATEGORY_B_OPCODES = frozenset(
    {
        "LLM_EVAL", "LLM_REQUEST", "LLM_RESUME", "PROMPT_BUILD",
        "DREAM", "IMPRINT", "RECALL",
        "AFFECT_EVENT", "AFFECT_STATE", "METRICS",
        "HOST_EVAL", "CALL_HOST", "FRACTURE_SELF",
        "HABIT_SUGGEST", "THRESHOLD_CHECK",
        "SEND", "RECEIVE", "MSG_SEND", "MSG_RECEIVE",
    }
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
# §4 Theorem 1 and the induced partition
# ---------------------------------------------------------------------------


def test_partition_covers_every_implemented_opcode() -> None:
    """Corollary 4.2 must classify every opcode the machine can charge for."""

    classified = CATEGORY_A_OPCODES | CATEGORY_B_OPCODES
    unclassified = set(GAS_COSTS) - classified
    assert not unclassified, (
        f"opcodes implemented but absent from the model partition: {sorted(unclassified)}"
    )


def test_partition_is_disjoint() -> None:
    assert not (CATEGORY_A_OPCODES & CATEGORY_B_OPCODES)


def test_category_a_opcodes_are_free_of_host_dispatch() -> None:
    """Category A must not overlap the fixed Host ABI surface."""

    assert not (CATEGORY_A_OPCODES & FIXED_HOST_ABI_OPCODES)


def test_every_fixed_host_abi_opcode_is_category_b() -> None:
    assert FIXED_HOST_ABI_OPCODES <= CATEGORY_B_OPCODES


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
# §7 Proof obligations — expected to fail while the obstruction stands
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="§7.3: FIXED_HOST_ABI_OPCODES covers 8 of 19 Category B opcodes; "
    "CALL_HOST, HABIT_SUGGEST, LLM_REQUEST, MSG_RECEIVE, MSG_SEND, PROMPT_BUILD, "
    "RECEIVE, SEND and THRESHOLD_CHECK classify as unknown_or_dynamic_opcode",
)
def test_effect_bearing_opcodes_are_classified() -> None:
    unclassified = sorted(
        opcode
        for opcode in CATEGORY_B_OPCODES
        if classify_host_opcode(opcode).reason == "unknown_or_dynamic_opcode"
    )
    assert not unclassified, f"Category B opcodes without a Host ABI classification: {unclassified}"


def test_the_two_classification_tables_are_disjoint_namespaces() -> None:
    """§7.3, second half: neither table is a complete classification.

    Opcodes and SYS_* symbols never meet, so a reader consulting one of them
    learns nothing about the other.
    """

    assert not (FIXED_HOST_ABI_OPCODES & NONDETERMINISTIC_HOST_SYMBOLS)


@pytest.mark.xfail(
    strict=True,
    reason="§7.1 + §7.2: the transition hash binds neither local values nor "
    "non-top stack entries, and compute_call_id takes no inputs, so no current "
    "identity satisfies the separation requirement of Theorem 5.2",
)
def test_current_identity_satisfies_theorem_5_2() -> None:
    """Two calls whose results may differ must not share an identity.

    Same position, same top-of-stack, different deeper argument: the transition
    hash collides, so any identity derived from it collides too.
    """

    left = _transition_hash({}, ["deep-A", "top"])
    right = _transition_hash({}, ["deep-B", "top"])
    fixed = dict(program_hash="sha256:p", ip=7, event_id="evt--0000005", frame_depth=0)
    assert compute_call_id(transition_hash=left, **fixed) != compute_call_id(
        transition_hash=right, **fixed
    ), "distinct call inputs share a call identity"


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
