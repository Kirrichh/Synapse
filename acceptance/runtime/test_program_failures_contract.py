"""Acceptance: a program's failure is reported as that failure, in the interpreter and in the compiled VM.

Each case runs a DSL program through the public application entry point or the
interpreter and reads only what the product reports: the run status, its
diagnostics, its output and the values the program bound.
"""
from synapse import Interpreter, compile_to_ast
from synapse.application import SourceExecutionRequest, execute_source


def outcome(source):
    result = execute_source(SourceExecutionRequest(source))
    return result.status, result.output, list(result.diagnostics)


def in_vm(program_source, prelude=""):
    """Compile ``program_source`` with ``compile vm`` and run it with ``run vm``; the interpreter that ran it."""
    interp = Interpreter()
    interp.global_env.define("src", program_source)
    interp.interpret(compile_to_ast(prelude + "compile vm { source src bind code }\nrun vm { source code }\n"))
    return interp


def test_an_assertion_failing_in_main_fails_the_run():
    status, output, diagnostics = outcome('fn main() { print("before")\nassert false\nprint("after") }')
    assert status == "ERROR"
    assert diagnostics == ["Assert failed: assertion failed"]
    assert "after" not in output


def test_an_undefined_name_in_main_is_reported_by_its_own_name():
    status, _, diagnostics = outcome('fn main() { let value = unknown_identifier\nprint("after") }')
    assert status == "ERROR"
    assert diagnostics == ["Undefined variable or function: 'unknown_identifier'"]


def test_an_error_inside_a_called_function_is_that_function_error():
    status, _, diagnostics = outcome('fn inner() { let v = missing_name\nreturn v }\nlet r = inner()')
    assert status == "ERROR"
    assert diagnostics == ["Undefined variable or function: 'missing_name'"]


def test_a_main_that_succeeds_runs_once():
    assert outcome('fn main() { print("inside") }') == ("OK", "inside", [])


def test_run_vm_runs_only_compiled_bytecode():
    status, _, diagnostics = outcome('let s = "let x = 1"\nrun vm { source s }')
    assert status == "ERROR"
    assert diagnostics == ["run vm runs compiled bytecode (compile vm), not str"]


def test_an_assertion_failing_in_the_vm_fails_the_run():
    status, _, diagnostics = outcome('let src = "assert false"\ncompile vm { source src bind code }\nrun vm { source code }\n')
    assert (status, diagnostics) == ("ERROR", ["assertion failed"])


def test_a_computed_callee_is_called_with_its_arguments_in_the_vm_as_in_the_interpreter():
    source = 'fn identity(x) { return x }\nfn twice(x) { return x * 2 }\nlet functions = [identity, twice]\nlet answer = functions[1](7)'
    tree = Interpreter()
    tree.interpret(compile_to_ast(source))
    assert tree.global_env.get("answer") == 14
    assert in_vm(source).global_env.get("vm_result")["locals"]["answer"] == 14


def test_a_vm_inline_guard_that_passes_continues_and_one_that_fails_runs_its_handler():
    template = ('fn main() {\n    try {\n        memory.write("x") { guard VERDICT }\n        print("written")\n'
                '    } catch (GUARD_VIOLATION) {\n        print("denied")\n    }\n}\nmain()\n')
    passed = in_vm(template.replace("VERDICT", "true")).global_env.get("vm_result")
    failed = in_vm(template.replace("VERDICT", "false")).global_env.get("vm_result")
    assert passed["output"] == ["written"]
    assert failed["output"] == ["denied"]
