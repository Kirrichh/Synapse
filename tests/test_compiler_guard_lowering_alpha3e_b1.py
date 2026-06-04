"""Alpha3e Track B.1 source-level guard lowering tests.

These tests deliberately stay lexical: guarded side effects must have a local
catch(GUARD_VIOLATION) ancestor in the same function.  No throws propagation,
no non-throwing guard syntax, and no interprocedural inference are introduced.
"""
import pytest

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.bytecode import CognitiveCompiler, CompileError


def parse_source(src: str):
    return Parser(Lexer(src).scan_tokens()).parse()


def compile_source(src: str):
    return CognitiveCompiler().compile(parse_source(src))


def ops(program):
    return [ins.op for ins in program.instructions]


def test_guarded_memory_write_without_catch_is_compile_error():
    src = '''
fn main() {
    memory.write("x") { guard true }
}
'''
    with pytest.raises(CompileError, match="guarded side-effect statement may raise GUARD_VIOLATION"):
        compile_source(src)


def test_helper_delegation_without_local_catch_is_compile_error_even_if_caller_catches():
    src = '''
fn safe_store() {
    memory.write("x") { guard true }
}

fn main() {
    try {
        safe_store()
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
'''
    with pytest.raises(CompileError, match="wrap it in try/catch"):
        compile_source(src)


def test_handled_governed_memory_write_lowers_to_guard_opcodes_and_ack():
    src = '''
fn main() {
    try {
        memory.write("x") { guard true }
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
'''
    program = compile_source(src)
    opcode_list = ops(program)
    assert "GUARD_ENTER" in opcode_list
    assert "GUARD_CHECK_RESULT" in opcode_list
    assert "GUARD_EXIT" in opcode_list
    assert "GUARD_VIOLATION_ACK" in opcode_list
    assert "SYS_MEMORY_WRITE" in [ins.a for ins in program.instructions if ins.op == "CALL_HOST"]
    assert program.guard_cleanup_table, "compiled guarded write must emit GuardCleanupRange"


def test_guard_fail_path_jumps_to_ack_handler_before_handler_side_effects():
    src = '''
fn main() {
    try {
        memory.write("x") { guard false }
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
'''
    program = compile_source(src)
    instructions = program.instructions
    ack_ip = next(i for i, ins in enumerate(instructions) if ins.op == "GUARD_VIOLATION_ACK")
    print_ip = next(i for i, ins in enumerate(instructions) if ins.op == "CALL_HOST" and ins.a == "print")
    fail_jumps = [ins for ins in instructions if ins.op == "JUMP" and ins.a == ack_ip]
    assert fail_jumps, "guard fail path must jump to handler ACK"
    assert ack_ip < print_ip, "compiler-inserted ACK must precede handler side effects"


def test_try_catch_only_accepts_guard_violation():
    src = '''
fn main() {
    try {
        print("x")
    } catch (OTHER_ERROR) {
        print("bad")
    }
}
'''
    with pytest.raises(Exception, match=r"Only catch\(GUARD_VIOLATION\)"):
        parse_source(src)


def test_governed_write_without_guard_field_defaults_to_passing_guard_but_still_requires_catch():
    src = '''
fn main() {
    try {
        memory.write("x") { reason "allowed with local recovery" }
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
'''
    program = compile_source(src)
    opcode_list = ops(program)
    assert "LOAD_TRUE" in opcode_list
    assert "GUARD_ENTER" in opcode_list
    assert program.guard_cleanup_table
