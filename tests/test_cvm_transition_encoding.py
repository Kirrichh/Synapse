"""Acceptance of byte-exact legacy transition hashes after encoding reuse."""
import copy
import hashlib
import json

import pytest

from synapse import compile_to_ast
from synapse.bytecode import BytecodeProgram, CognitiveCompiler, Instruction
from synapse.cvm import CognitiveVM, VMState, encode_vm_value


def legacy_transition_hash(state, previous, instruction=None, call_id=None):
    """Existing v2.2 wire contract, independent of optimized encoding fragments."""
    payload = {
        "prev": previous, "ip": state.ip,
        "locals_keys": sorted(state.locals.keys()),
        "stack_len": len(state.stack),
        "stack_top": repr(state.stack[-1]) if state.stack else None,
        "gas": state.gas_remaining,
        "context_stack": tuple(state.context_stack),
        "actor_stack": tuple(state.actor_stack),
        "policy_stack": tuple(state.policy_stack),
        "mailbox_inbound": encode_vm_value(state.mailbox_inbound),
        "mailbox_outbound": encode_vm_value(state.mailbox_outbound),
        "pending_message_receive": encode_vm_value(state.pending_message_receive),
    }
    options = {"sort_keys": True, "default": str}
    if instruction is not None:
        payload["op"] = instruction.to_dict()
        options["separators"] = (",", ":")
    else:
        payload.update(resume_call_id=call_id, pending_host_call=None)
    encoded = json.dumps(payload, **options).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("source", [
    "let acc = 1 for i in range(20) { acc = (acc * 17) % 103 }",
    "let n = 0 while n < 20 { n = n + 1 }",
    "fn fib(n) { if n < 2 { return n } return fib(n-1) + fib(n-2) } let result = fib(6)",
    'let items = ["строка", true, none, 0.125] for x in items { let result = x }',
])
def test_every_transition_matches_legacy_wire_contract_and_snapshot_resume(source):
    program = CognitiveCompiler().compile(compile_to_ast(source))
    vm = CognitiveVM(program, VMState(gas_remaining=100_000))
    steps = 0
    while not vm.halted:
        previous = vm.state.transition_hash
        instruction = program.instructions[vm.state.ip]
        vm.step()
        assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, instruction)
        steps += 1
        if steps == 9:
            snapshot = copy.deepcopy(vm.snapshot())
            resumed = CognitiveVM.restore(snapshot)
            resumed.run()
    assert resumed.state.to_dict() == vm.state.to_dict()


def test_mutable_stack_mailbox_scopes_and_operands_never_reuse_stale_bytes():
    # All instructions have the same operational behavior. Their ignored
    # operands still participate in the wire contract, including bool vs int.
    operands = [None, True, 1, False, 0, '"кириллица\\\n', {"nested": [1]}, 1.25, None]
    program = BytecodeProgram([Instruction("DUP", value) for value in operands])
    shared = ["start"]
    state = VMState(stack=[shared], locals={"z": 1}, gas_remaining=1000)
    vm = CognitiveVM(program, state)
    for i, instruction in enumerate(program.instructions):
        shared.append(i)
        state.locals.pop("a", None)
        if i % 2:
            state.locals["a"] = i
        state.context_stack[:] = ["outer", str(i % 2)]
        state.actor_stack[:] = ["actor"] if i % 2 else []
        state.policy_stack[:] = ["policy"] if i % 3 else []
        if i == 2:
            state.mailbox_inbound.append({"payload": shared})
            state.mailbox_outbound.append({"value": 1})
        if i == 3:
            state.mailbox_outbound[0]["value"] = 2
            state.pending_message_receive = {"receiver_id": "receiver"}
        if i == 4:
            state.mailbox_inbound.clear()
            state.mailbox_outbound.clear()
            state.pending_message_receive = None
        previous = state.transition_hash
        vm.step()
        assert state.transition_hash == legacy_transition_hash(state, previous, instruction)


@pytest.mark.parametrize("initial", [
    {"locals": {str(i): i for i in range(160)}},
    {"locals": {1: "legacy numeric key"}},
    {"context_stack": ["x" * 20_000]},
    {"gas_remaining": 5.5},
    {"mailbox_inbound": None},
])
def test_unusual_and_large_legacy_states_keep_their_wire_form(initial):
    vm = CognitiveVM(BytecodeProgram([Instruction("HALT")]), VMState(**initial))
    previous = vm.state.transition_hash
    vm.step()
    assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, vm.program.instructions[0])


def test_fragment_eviction_does_not_change_transition_hashes():
    program = BytecodeProgram([Instruction("DUP", str(i)) for i in range(180)]
                              + [Instruction("DUP", "0")])
    vm = CognitiveVM(program, VMState(stack=[42], gas_remaining=1000))
    for instruction in program.instructions:
        previous = vm.state.transition_hash
        vm.step()
        assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, instruction)


def test_equal_python_operands_keep_distinct_json_types_with_unchanged_bindings():
    program = BytecodeProgram([Instruction("DUP", value) for value in (True, 1, False, 0, True)])
    vm = CognitiveVM(program, VMState(stack=[42], gas_remaining=100))
    for instruction in program.instructions:
        previous = vm.state.transition_hash
        vm.step()
        assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, instruction)


def test_resume_keeps_legacy_whitespace_and_fresh_mutable_result():
    def host(opcode, request, extra):
        if opcode == "HOST_STATUS":
            return {"event_id": "evt--0000001"}
        return {"status": "STATUS_PAUSED_HOST_CALL"}

    vm = CognitiveVM(BytecodeProgram([Instruction("CALL_HOST", "SYS_LLM_EVAL", 0)]), host=host)
    vm.run()
    call_id = vm.state.pending_host_call["call_id"]
    previous = vm.state.transition_hash
    vm.resume_host_call(call_id, {"текст": [1, True, "quote\"\\\n"]})
    assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, call_id=call_id)


def test_identical_loop_compilations_are_stable_and_compiler_reuse_is_isolated():
    source = "let total = 0 for x in range(3) { for y in range(2) { total = total + x + y } }"
    compiler = CognitiveCompiler()
    first = compiler.compile(compile_to_ast(source))
    saved = copy.deepcopy(first.to_dict())
    second = compiler.compile(compile_to_ast(source))
    third = CognitiveCompiler().compile(compile_to_ast(source))
    assert first.to_dict() == saved == second.to_dict() == third.to_dict()
    results = []
    for program in (first, second, third):
        vm = CognitiveVM(program, VMState(gas_remaining=10_000))
        results.append(vm.run())
    assert results[0] == results[1] == results[2]
    assert results[0]["locals"]["total"] == 9


def test_generated_loop_locals_do_not_shadow_source_variables():
    source = ("let __iter_0 = 80 let __idx_0 = 90 let i = 7 let total = 0 "
              "for i in range(3) { total = total + i } "
              "let result = __iter_0 + __idx_0 + i + total")
    vm = CognitiveVM(CognitiveCompiler().compile(compile_to_ast(source)))
    assert vm.run()["locals"]["result"] == 180


def test_existing_serialized_temporary_names_are_not_rewritten():
    program = BytecodeProgram([Instruction("LOAD_CONST", 0),
                               Instruction("STORE", "__iter_140642384869280"),
                               Instruction("HALT")], constants=[[0, 1]])
    old_bytes = json.dumps(program.to_dict(), sort_keys=True)
    restored = BytecodeProgram.from_dict(json.loads(old_bytes))
    assert json.dumps(restored.to_dict(), sort_keys=True) == old_bytes
    vm = CognitiveVM(restored)
    for instruction in restored.instructions:
        previous = vm.state.transition_hash
        vm.step()
        assert vm.state.transition_hash == legacy_transition_hash(vm.state, previous, instruction)
