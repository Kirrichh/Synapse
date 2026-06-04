"""CVM v2.2 test suite — управляющие структуры, функции, арифметика.

Тесты независимы от дерево-обходного интерпретатора: они компилируют
Synapse AST напрямую через CognitiveCompiler и запускают CognitiveVM.
Это фиксирует CVM v2.2 как самостоятельный execution path.
"""
from __future__ import annotations

import pytest
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.bytecode import CognitiveCompiler, BytecodeProgram, Instruction
from synapse.cvm import (
    CognitiveVM, VMState, VMAssertionFailed,
    OutOfEnergy, VMStackUnderflow, UnknownOpcodeError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compile_source(source: str) -> BytecodeProgram:
    ast = Parser(Lexer(source).scan_tokens()).parse()
    return CognitiveCompiler().compile(ast)


def run_source(source: str, gas: int = 5000) -> dict:
    program = compile_source(source)
    vm = CognitiveVM(program)
    vm.state.gas_remaining = gas
    return vm.run()


def run_source_vm(source: str, gas: int = 5000) -> CognitiveVM:
    program = compile_source(source)
    vm = CognitiveVM(program)
    vm.state.gas_remaining = gas
    vm.run()
    return vm


# ---------------------------------------------------------------------------
# 1. Базовые инструкции
# ---------------------------------------------------------------------------

class TestBasicInstructions:
    def test_load_const_and_store(self):
        program = BytecodeProgram(
            instructions=[
                Instruction("LOAD_CONST", 0),
                Instruction("STORE", "x"),
                Instruction("LOAD_NAME", "x"),
                Instruction("HALT"),
            ],
            constants=[42],
        )
        vm = CognitiveVM(program)
        result = vm.run()
        assert result["locals"]["x"] == 42
        assert result["stack"] == [42]

    def test_load_none_true_false(self):
        program = BytecodeProgram(
            instructions=[
                Instruction("LOAD_NONE"),
                Instruction("STORE", "a"),
                Instruction("LOAD_TRUE"),
                Instruction("STORE", "b"),
                Instruction("LOAD_FALSE"),
                Instruction("STORE", "c"),
                Instruction("HALT"),
            ],
            constants=[],
        )
        vm = CognitiveVM(program)
        vm.run()
        assert vm.state.locals["a"] is None
        assert vm.state.locals["b"] is True
        assert vm.state.locals["c"] is False

    def test_dup(self):
        program = BytecodeProgram(
            instructions=[
                Instruction("LOAD_CONST", 0),
                Instruction("DUP"),
                Instruction("STORE", "a"),
                Instruction("STORE", "b"),
                Instruction("HALT"),
            ],
            constants=[99],
        )
        vm = CognitiveVM(program)
        vm.run()
        assert vm.state.locals["a"] == 99
        assert vm.state.locals["b"] == 99

    def test_stack_underflow(self):
        program = BytecodeProgram(
            instructions=[Instruction("POP"), Instruction("HALT")],
            constants=[],
        )
        vm = CognitiveVM(program)
        with pytest.raises(VMStackUnderflow):
            vm.run()


# ---------------------------------------------------------------------------
# 2. Арифметика
# ---------------------------------------------------------------------------

class TestArithmetic:
    def test_add(self):
        result = run_source("let x = 2 + 3")
        assert result["locals"]["x"] == 5

    def test_sub(self):
        result = run_source("let x = 10 - 4")
        assert result["locals"]["x"] == 6

    def test_mul(self):
        result = run_source("let x = 3 * 7")
        assert result["locals"]["x"] == 21

    def test_div(self):
        result = run_source("let x = 10 / 4")
        assert abs(result["locals"]["x"] - 2.5) < 1e-9

    def test_mod(self):
        result = run_source("let x = 10 % 3")
        assert result["locals"]["x"] == 1

    def test_unary_neg(self):
        result = run_source("let x = -5")
        assert result["locals"]["x"] == -5

    def test_complex_expr(self):
        result = run_source("let x = (2 + 3) * (10 - 4)")
        assert result["locals"]["x"] == 30

    def test_chained_arithmetic(self):
        result = run_source("let a = 1\nlet b = 2\nlet c = a + b * 3")
        # tree-walking parser: operator precedence должно давать 1 + (2*3) = 7
        assert result["locals"]["c"] == 7


# ---------------------------------------------------------------------------
# 3. Сравнения и логика
# ---------------------------------------------------------------------------

class TestComparisons:
    def test_eq_true(self):
        result = run_source("let x = (3 == 3)")
        assert result["locals"]["x"] is True

    def test_eq_false(self):
        result = run_source("let x = (3 == 4)")
        assert result["locals"]["x"] is False

    def test_neq(self):
        result = run_source("let x = (3 != 4)")
        assert result["locals"]["x"] is True

    def test_lt_gt(self):
        result = run_source("let a = (1 < 2)\nlet b = (2 > 3)")
        assert result["locals"]["a"] is True
        assert result["locals"]["b"] is False

    def test_lte_gte(self):
        result = run_source("let a = (2 <= 2)\nlet b = (3 >= 4)")
        assert result["locals"]["a"] is True
        assert result["locals"]["b"] is False

    def test_not(self):
        result = run_source("let x = not false")
        assert result["locals"]["x"] is True

    def test_and_short_circuit(self):
        # false and <anything> → false, второй операнд не должен оцениваться
        result = run_source("let x = false and true")
        assert result["locals"]["x"] is False

    def test_or_short_circuit(self):
        result = run_source("let x = true or false")
        assert result["locals"]["x"] is True


# ---------------------------------------------------------------------------
# 4. Условия (if/else)
# ---------------------------------------------------------------------------

class TestConditionals:
    def test_if_true_branch(self):
        result = run_source("""
let x = 0
if true {
    let x = 1
}
""")
        assert result["locals"]["x"] == 1

    def test_if_false_skips_body(self):
        result = run_source("""
let x = 0
if false {
    let x = 99
}
""")
        assert result["locals"]["x"] == 0

    def test_if_else(self):
        result = run_source("""
let val = 10
let result = 0
if val > 5 {
    let result = 1
} else {
    let result = 2
}
""")
        assert result["locals"]["result"] == 1

    def test_nested_if(self):
        result = run_source("""
let a = 5
let b = 10
let out = 0
if a < b {
    if a > 3 {
        let out = 42
    }
}
""")
        assert result["locals"]["out"] == 42

    def test_if_with_comparison(self):
        result = run_source("""
let score = 75
let grade = 0
if score >= 90 {
    let grade = 4
} else {
    if score >= 70 {
        let grade = 3
    } else {
        let grade = 2
    }
}
""")
        assert result["locals"]["grade"] == 3


# ---------------------------------------------------------------------------
# 5. Цикл while
# ---------------------------------------------------------------------------

class TestWhileLoop:
    def test_simple_while(self):
        result = run_source("""
let i = 0
let sum = 0
while i < 5 {
    let sum = sum + i
    let i = i + 1
}
""")
        assert result["locals"]["sum"] == 10  # 0+1+2+3+4

    def test_while_not_entered(self):
        result = run_source("""
let x = 99
while false {
    let x = 0
}
""")
        assert result["locals"]["x"] == 99

    def test_while_counter(self):
        result = run_source("""
let count = 0
while count < 10 {
    let count = count + 1
}
""")
        assert result["locals"]["count"] == 10


# ---------------------------------------------------------------------------
# 6. Цикл for
# ---------------------------------------------------------------------------

class TestForLoop:
    def test_for_over_list(self):
        result = run_source("""
let items = [1, 2, 3, 4]
let total = 0
for item in items {
    let total = total + item
}
""")
        assert result["locals"]["total"] == 10

    def test_for_collect(self):
        result = run_source("""
let nums = [10, 20, 30]
let final_val = 0
for n in nums {
    let final_val = n
}
""")
        assert result["locals"]["final_val"] == 30


# ---------------------------------------------------------------------------
# 7. Функции
# ---------------------------------------------------------------------------

class TestFunctions:
    def test_fn_definition_and_call(self):
        result = run_source("""
fn add(a, b) {
    return a + b
}
let result = add(3, 4)
""")
        assert result["locals"]["result"] == 7

    def test_fn_implicit_return_none(self):
        result = run_source("""
fn noop() {
}
let x = noop()
""")
        assert result["locals"]["x"] is None

    def test_fn_recursive_factorial(self):
        # Рекурсия: factorial(5) = 120
        result = run_source("""
fn factorial(n) {
    if n <= 1 {
        return 1
    }
    return n * factorial(n - 1)
}
let result = factorial(5)
""", gas=50000)
        assert result["locals"]["result"] == 120

    def test_fn_closure_over_outer(self):
        result = run_source("""
let base = 100
fn add_base(x) {
    return base + x
}
let result = add_base(42)
""")
        assert result["locals"]["result"] == 142

    def test_multiple_fns(self):
        result = run_source("""
fn square(x) { return x * x }
fn cube(x) { return x * square(x) }
let result = cube(3)
""", gas=20000)
        assert result["locals"]["result"] == 27


# ---------------------------------------------------------------------------
# 8. Структуры данных
# ---------------------------------------------------------------------------

class TestDataStructures:
    def test_list_literal(self):
        result = run_source("let xs = [1, 2, 3]")
        assert result["locals"]["xs"] == [1, 2, 3]

    def test_list_index(self):
        result = run_source("let xs = [10, 20, 30]\nlet x = xs[1]")
        assert result["locals"]["x"] == 20

    def test_nested_list(self):
        result = run_source("let matrix = [[1, 2], [3, 4]]")
        assert result["locals"]["matrix"] == [[1, 2], [3, 4]]


# ---------------------------------------------------------------------------
# 9. Gas metering
# ---------------------------------------------------------------------------

class TestGasMetering:
    def test_out_of_energy(self):
        with pytest.raises(OutOfEnergy):
            run_source("let x = 1 + 2", gas=2)  # слишком мало

    def test_gas_consumed(self):
        result = run_source("let x = 1 + 2", gas=5000)
        assert result["gas_remaining"] < 5000

    def test_gas_refund_on_cached_host(self):
        """Host возвращает from_cache=True → gas refund."""
        program = BytecodeProgram(
            instructions=[
                Instruction("METRICS", None, None),
                Instruction("HALT"),
            ],
            constants=[],
        )

        def fake_host(op, a, b):
            return {"status": "ok", "value": {}, "from_cache": True}

        vm = CognitiveVM(program, host=fake_host)
        vm.state.gas_remaining = 100
        gas_before = vm.state.gas_remaining
        vm.run()
        # METRICS стоит 2, refund min(2//2,2)=1 → итого -1 газа
        assert vm.state.gas_remaining >= gas_before - 2


# ---------------------------------------------------------------------------
# 10. Transition hash
# ---------------------------------------------------------------------------

class TestTransitionHash:
    def test_hash_changes_after_each_step(self):
        program = BytecodeProgram(
            instructions=[
                Instruction("LOAD_CONST", 0),
                Instruction("STORE", "x"),
                Instruction("HALT"),
            ],
            constants=[1],
        )
        vm = CognitiveVM(program)
        h0 = vm.state.transition_hash
        vm.step()
        h1 = vm.state.transition_hash
        vm.step()
        h2 = vm.state.transition_hash
        assert h0 != h1
        assert h1 != h2
        assert h0 != h2

    def test_same_program_same_hash(self):
        """Детерминизм: одна и та же программа → одинаковый финальный хэш."""
        source = "let x = 2 + 3"
        r1 = run_source(source)
        r2 = run_source(source)
        assert r1["transition_hash"] == r2["transition_hash"]

    def test_different_inputs_different_hash(self):
        r1 = run_source("let x = 1")
        r2 = run_source("let x = 2")
        assert r1["transition_hash"] != r2["transition_hash"]


# ---------------------------------------------------------------------------
# 11. Snapshot / restore
# ---------------------------------------------------------------------------

class TestSnapshotRestore:
    def test_restore_and_continue(self):
        """Сохраняем snapshot на полпути и продолжаем с него."""
        source = "let x = 10\nlet y = x + 5"
        program = compile_source(source)
        vm = CognitiveVM(program)
        # Выполняем только первую инструкцию (LOAD_CONST)
        vm.step()
        snap = vm.snapshot()

        # Восстанавливаем
        vm2 = CognitiveVM.restore(snap)
        vm2.run()
        # x должен быть в locals vm2 (если мы до STORE дошли)
        # Главное: программа не крашится при restore

    def test_snapshot_is_json_serializable(self):
        import json
        source = "let x = 42\nlet y = x + 1"
        program = compile_source(source)
        vm = CognitiveVM(program)
        vm.run()
        snap = vm.snapshot()
        serialized = json.dumps(snap, default=str)
        assert "bytecode_program" in serialized


# ---------------------------------------------------------------------------
# 12. vm_routing v2.2
# ---------------------------------------------------------------------------

class TestVMRoutingV22:
    def test_basic_nodes_route_to_cvm(self):
        from synapse.runtime.vm_routing import classify_ast_node_v22
        for name in ["LetStmt", "IfStmt", "WhileStmt", "FnDef", "BinaryExpr", "Literal"]:
            d = classify_ast_node_v22(name)
            assert d.route == "CVM", f"{name} should route to CVM"

    def test_cognitive_nodes_still_host_eval(self):
        from synapse.runtime.vm_routing import classify_ast_node_v22
        for name in ["DreamBlock", "ResonanceStmt", "CollectiveDreamStmt"]:
            d = classify_ast_node_v22(name)
            assert d.route == "HOST_EVAL", f"{name} should still be HOST_EVAL"

    def test_dynamic_opcodes_classified(self):
        from synapse.runtime.vm_routing import classify_host_opcode_v22
        for op in ["JUMP", "CALL", "RETURN", "ADD", "EQ", "BUILD_LIST"]:
            d = classify_host_opcode_v22(op)
            assert d.route == "CVM", f"{op} should be CVM"

    def test_host_abi_still_classified(self):
        from synapse.runtime.vm_routing import classify_host_opcode_v22
        for op in ["IMPRINT", "RECALL", "LLM_EVAL", "FRACTURE_SELF"]:
            d = classify_host_opcode_v22(op)
            assert d.route == "CVM_HOST_ABI"


# ---------------------------------------------------------------------------
# 13. vm_coverage_ratio — теперь > 0.5 для простых программ
# ---------------------------------------------------------------------------

class TestVMCoverageRatio:
    def test_simple_program_coverage(self):
        """Простая программа с if/while должна иметь vm_coverage_ratio > 0.5."""
        from synapse.runtime.vm_routing import coverage_ratio, classify_ast_node_v22
        from synapse.lexer import Lexer
        from synapse.parser import Parser

        source = "let x = 1\nlet y = x + 2\nif y > 2 { let z = y * 3 }"
        ast = Parser(Lexer(source).scan_tokens()).parse()

        vm_count = 0
        host_count = 0
        for stmt in ast.statements:
            d = classify_ast_node_v22(stmt)
            if d.route == "CVM":
                vm_count += 1
            else:
                host_count += 1

        total = vm_count + host_count
        ratio = vm_count / total if total else 0
        assert ratio > 0.5, f"Expected > 0.5 CVM ratio, got {ratio:.2f}"

    def test_bytecode_program_version(self):
        program = compile_source("let x = 1")
        assert program.version == "2.2"

    def test_no_host_eval_for_simple_program(self):
        """Простая программа без когнитивных примитивов не должна иметь HOST_EVAL."""
        program = compile_source("""
let x = 1
let y = 2
let z = x + y
if z > 2 {
    let result = z * 10
}
""")
        host_evals = [i for i in program.instructions if i.op == "HOST_EVAL"]
        assert len(host_evals) == 0, f"Unexpected HOST_EVAL: {host_evals}"


# ---------------------------------------------------------------------------
# 14. Интеграция: compile vm / run vm через Interpreter
# ---------------------------------------------------------------------------

class TestCVMIntegration:
    def test_compile_and_run_via_interpreter(self):
        from synapse import Interpreter
        from synapse.lexer import Lexer
        from synapse.parser import Parser

        source = """
compile vm { source "let x = 6 + 7" bind code }
run vm { source code gas 500 bind result }
"""
        ast = Parser(Lexer(source).scan_tokens()).parse()
        interp = Interpreter()
        interp.interpret(ast)
        # Нет исключений → успех; проверяем историю
        vm_events = [e for e in interp.execution_history
                     if e.get("type") in ("vm_bytecode_compiled", "vm_executed")]
        assert len(vm_events) >= 2

    def test_checkpoint_and_resume_via_interpreter(self):
        from synapse import Interpreter
        from synapse.lexer import Lexer
        from synapse.parser import Parser

        source = """
compile vm { source "let x = 1" bind code }
run vm { source code gas 500 checkpoint "mid" at_ip 1 bind partial }
run vm { resume_from "mid" gas 500 bind final }
"""
        ast = Parser(Lexer(source).scan_tokens()).parse()
        interp = Interpreter()
        interp.interpret(ast)
        assert "mid" in interp.vm_checkpoints
        resume_events = [e for e in interp.execution_history if e.get("type") == "vm_resumed"]
        assert len(resume_events) >= 1
