from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, PolicyViolationException


def compile_ast(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source: str) -> Interpreter:
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_integrate_rollback_removes_memory_write_history_and_audit():
    interp = run('''
agent Guide { model "mock" }
let self = Guide
let dream_result = "insight"
integrate dream_result {
    memory.write("bad") { reason "should not persist" }
    assert false, "forced rollback"
} on fail rollback
''')
    assert interp.memory_audit == []
    assert interp.execution_history[-1]["type"] == "integrate_rollback"
    assert not any(e.get("type") == "memory_audit" for e in interp.execution_history)
    assert Guide_memory_is_clean(interp)


def Guide_memory_is_clean(interp: Interpreter) -> bool:
    guide = interp.global_env.get("Guide")
    return guide.memory.to_dict()["short_term"] == []


def test_integrate_rollback_restores_output_buffer():
    interp = run('''
let dream_result = "insight"
integrate dream_result {
    print("should not appear")
    assert false, "rollback"
} on fail rollback
print("after")
''')
    output = interp.get_output()
    assert "should not appear" not in output
    assert "after" in output


def test_integrate_reason_is_logged():
    interp = run('''
let dream_result = "insight"
integrate dream_result {
    reason "stress-test derived"
    assert true
} on fail rollback
''')
    assert interp.execution_history[-1]["type"] == "integrate_committed"
    assert interp.execution_history[-1]["reason"] == "stress-test derived"


def test_evolve_max_delta_blocks_large_mutation():
    source = '''
agent Guide {
    model "mock"
    soulprint { values: [ curiosity: 0.5 ] memory: long_term style: "" }
}
policy AlignmentPolicy {
    target "evolve.Guide"
    max_delta: 0.05
}
let self = Guide
evolve self under AlignmentPolicy {
    soulprint = {"values": {"curiosity": 0.99}, "memory_type": "long_term", "style": "", "version": "1.0", "protected": true}
}
'''
    try:
        run(source)
    except PolicyViolationException as exc:
        assert "max_delta" in str(exc)
        return
    assert False, "expected PolicyViolationException"


if __name__ == "__main__":
    test_integrate_rollback_removes_memory_write_history_and_audit()
    test_integrate_rollback_restores_output_buffer()
    test_integrate_reason_is_logged()
    test_evolve_max_delta_blocks_large_mutation()
    print("All v1.4.1 replay-safe integrate tests passed!")
