"""Alpha.3-A1: program_hash and JSON-safe VM value serialization."""
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from synapse.cvm import CognitiveVM, VMState, FunctionObject, CallFrame


def parse_source(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def compile_source(source: str) -> BytecodeProgram:
    return CognitiveCompiler().compile(parse_source(source))


def run_source(source: str) -> CognitiveVM:
    bytecode = compile_source(source)
    vm = CognitiveVM(bytecode)
    vm.state.gas_remaining = 5000
    vm.run()
    return vm


def test_program_hash_stable():
    src = "let x = 1"
    bytecode1 = compile_source(src)
    bytecode2 = compile_source(src)
    assert bytecode1.program_hash == bytecode2.program_hash


def test_program_hash_changes_with_constants():
    bytecode1 = compile_source("let x = 1")
    bytecode2 = compile_source("let x = 2")
    assert bytecode1.program_hash != bytecode2.program_hash


def test_program_hash_includes_host_abi_version():
    instructions = [Instruction("LOAD_CONST", 0), Instruction("HALT")]
    p1 = BytecodeProgram(instructions=instructions, constants=[1], host_abi_version="2.2")
    p2 = BytecodeProgram(instructions=instructions, constants=[1], host_abi_version="2.3")
    assert p1.program_hash != p2.program_hash


def test_function_object_in_locals_roundtrip():
    vm = run_source("fn add(a, b) { return a + b }")

    assert "add" in vm.state.locals
    assert isinstance(vm.state.locals["add"], FunctionObject)

    serialized = vm.state.to_dict()
    restored = VMState.from_dict(serialized)

    assert "add" in restored.locals
    assert isinstance(restored.locals["add"], FunctionObject)
    assert restored.locals["add"].name == "add"
    assert restored.locals["add"].params == ["a", "b"]
    assert restored.locals["add"].program_hash == vm.program.program_hash


def test_callframe_roundtrip_with_closure():
    src = """
let base = 100
fn add_base(x) { return base + x }
let res = add_base(42)
"""
    bytecode = compile_source(src)
    vm = CognitiveVM(bytecode)
    vm.state.gas_remaining = 5000

    while not vm.state.call_stack and not vm.halted:
        vm.step()

    assert vm.state.call_stack
    frame = vm.state.call_stack[-1]
    assert frame.fn_name == "add_base"
    assert frame.program_hash == bytecode.program_hash
    assert frame.body_ip is not None
    assert "base" in frame.locals_snapshot

    serialized = frame.to_dict()
    restored = CallFrame.from_dict(serialized)

    assert restored.fn_name == frame.fn_name
    assert restored.program_hash == frame.program_hash
    assert restored.stack_base == frame.stack_base
    assert restored.body_ip == frame.body_ip
    assert "base" in restored.locals_snapshot


def test_backward_compat_old_snapshot():
    old_snapshot = {
        "ip": 5,
        "stack": [42],
        "locals": {"x": 10},
        "gas_remaining": 1000,
        # No call_stack, no name_save_stack, no program_hash.
    }
    state = VMState.from_dict(old_snapshot)
    assert state.call_stack == []
    assert state.name_save_stack == []
    assert state.locals["x"] == 10
    assert state.ip == 5


def test_call_argument_order_preserved():
    vm = run_source("fn sub(a, b) { return a - b }\nlet res = sub(10, 3)")
    assert vm.state.locals.get("res") == 7


def test_function_locals_do_not_leak():
    src = """
fn f(x) {
    let secret = 42
    return x + 1
}
let y = f(1)
"""
    vm = run_source(src)

    public_locals = {
        k: v for k, v in vm.state.locals.items()
        if not k.startswith("_") and not isinstance(v, FunctionObject)
    }

    assert public_locals.get("y") == 2
    assert "x" not in public_locals
    assert "secret" not in public_locals
