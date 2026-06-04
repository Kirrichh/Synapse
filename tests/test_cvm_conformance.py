import pytest
from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter
from synapse.bytecode import CognitiveCompiler
from synapse.cvm import CognitiveVM


def parse_source(source: str):
    tokens = Lexer(source).scan_tokens()
    return Parser(tokens).parse()


# Baseline bootstrap subtraction: фиксируем символы, которые интерпретатор
# загружает по умолчанию (print, trust_at_least, уровни доверия и т.д.)
_BOOTSTRAP_KEYS = set(Interpreter().global_env.variables.keys())


def public_tree_locals(interp: Interpreter) -> dict:
    hidden = ("_", "__iter_", "__idx_")
    return {
        k: v for k, v in interp.global_env.variables.items()
        if k not in _BOOTSTRAP_KEYS and not k.startswith(hidden)
    }


def public_vm_locals(vm: CognitiveVM) -> dict:
    hidden = ("_", "__iter_", "__idx_")
    return {
        k: v for k, v in vm.state.locals.items()
        if not k.startswith(hidden)
        and not callable(v)
        and type(v).__name__ != "FunctionObject"
    }


def normalize_error(exc: Exception | None) -> str | None:
    """Унифицирует имена ошибок assertion между tree-walker и CVM."""
    if exc is None:
        return None
    name = type(exc).__name__
    msg = str(exc).lower()
    if name in {"VMAssertionFailed", "RuntimeError", "AssertionError"} and "assert" in msg:
        return "AssertionError"
    return name


def run_tree_walker(source: str) -> dict:
    program = parse_source(source)
    interp = Interpreter()
    try:
        interp.interpret(program)
        return {
            "locals": public_tree_locals(interp),
            "output": list(interp.output_buffer),
            "error": None,
        }
    except Exception as exc:
        return {
            "locals": public_tree_locals(interp),
            "output": list(interp.output_buffer),
            "error": normalize_error(exc),
        }


def run_cvm(source: str, gas: int = 5000) -> dict:
    program = parse_source(source)
    compiler = CognitiveCompiler()
    bytecode = compiler.compile(program)
    vm = CognitiveVM(bytecode)
    vm.state.gas_remaining = gas
    try:
        vm.run()
        return {
            "locals": public_vm_locals(vm),
            "output": list(getattr(vm, "_output", [])),
            "error": None,
        }
    except Exception as exc:
        return {
            "locals": public_vm_locals(vm),
            "output": list(getattr(vm, "_output", [])),
            "error": normalize_error(exc),
        }


def assert_conformance(source: str, expected_locals: dict = None):
    tree = run_tree_walker(source)
    cvm = run_cvm(source)

    assert tree["error"] == cvm["error"], f"Error drift: {tree['error']} != {cvm['error']}"
    assert tree["output"] == cvm["output"], f"Output drift:\nTree: {tree['output']}\nCVM: {cvm['output']}"
    assert tree["locals"] == cvm["locals"], f"Locals drift:\nTree: {tree['locals']}\nCVM: {cvm['locals']}"
    if expected_locals:
        assert cvm["locals"] == expected_locals, f"Expected locals mismatch"


# ─────────────────────────────────────────────────────────────
# TEST CASES
# ─────────────────────────────────────────────────────────────
def test_arithmetic():
    assert_conformance("let x = (2 + 3) * (10 - 4)", {"x": 30})


def test_comparison_logic():
    src = "let a = 5\nlet b = 10\nlet res = (a < b) and not (a == b)"
    assert_conformance(src, {"a": 5, "b": 10, "res": True})


def test_if_else_branching():
    src = "let val = 10\nlet out = 0\nif val > 5 { out = 1 } else { out = 2 }"
    assert_conformance(src, {"val": 10, "out": 1})


def test_while_loop():
    src = "let i = 0\nlet sum = 0\nwhile i < 5 {\nsum = sum + i\ni = i + 1\n}"
    assert_conformance(src, {"i": 5, "sum": 10})


def test_for_loop():
    src = "let items = [1, 2, 3, 4]\nlet total = 0\nfor item in items { total = total + item }"
    assert_conformance(src, {"items": [1, 2, 3, 4], "total": 10})




def test_for_loop_preserves_outer_binding():
    src = """
    let item = 100
    let items = [1, 2]
    let total = 0
    for item in items { total = total + item }
    """
    assert_conformance(src, {"item": 100, "items": [1, 2], "total": 3})


def test_nested_for_loop_preserves_loop_bindings():
    src = """
    let xs = [1, 2]
    let ys = [10, 20]
    let total = 0
    for x in xs {
        for y in ys {
            total = total + x + y
        }
    }
    """
    assert_conformance(src, {"xs": [1, 2], "ys": [10, 20], "total": 66})

def test_function_call():
    src = "fn add(a, b) { return a + b }\nlet res = add(3, 4)"
    assert_conformance(src, {"res": 7})


def test_recursion():
    src = "fn fact(n) { if n <= 1 { return 1 } return n * fact(n - 1) }\nlet res = fact(5)"
    assert_conformance(src, {"res": 120})


def test_closure():
    src = "let base = 100\nfn add_base(x) { return base + x }\nlet res = add_base(42)"
    assert_conformance(src, {"base": 100, "res": 142})


def test_list_index():
    src = "let xs = [10, 20, 30]\nlet x = xs[1]"
    assert_conformance(src, {"xs": [10, 20, 30], "x": 20})


def test_assert_pass():
    assert_conformance("assert 1 == 1", {})


def test_assert_fail():
    tree = run_tree_walker("assert 1 == 2")
    cvm = run_cvm("assert 1 == 2")
    assert tree["error"] == cvm["error"] == "AssertionError"
