from synapse import Lexer, Parser, Interpreter


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_energy_pool_recharge_and_hysteresis_exit_rest():
    source = '''
agent Worker {
    model "mock"
    energy_pool {
        max 100
        initial 10
        recharge 5 per 2 events
        rest_threshold 15
        hysteresis_margin 5
    }
}
let self = Worker
context "first" { print("a") }
context "second" { print("b") }
context "third" { print("c") }
context "fourth" { print("d") }
'''
    interp = run(source)
    types = [e.get("type") for e in interp.execution_history]
    assert "agent_entered_rest" in types
    assert "energy_pool_recharged" in types
    assert "agent_exited_rest" in types
    assert interp.energy_pool.current >= 20


def test_context_scope_emits_enter_exit_and_restores_current_context():
    source = '''
context "deployment_task" {
    print("inside")
}
print("outside")
'''
    interp = run(source)
    ctx = [e for e in interp.execution_history if e.get("type") in {"context_entered", "context_exited"}]
    assert [e["type"] for e in ctx] == ["context_entered", "context_exited"]
    assert ctx[0]["label"] == "deployment_task"
    assert ctx[1]["label"] == "deployment_task"
    assert interp.current_context is None
    assert interp.get_output().splitlines() == ["inside", "outside"]


def test_energy_pool_without_declaration_is_backward_compatible():
    source = '''
context "plain" { print("ok") }
'''
    interp = run(source)
    assert interp.energy_pool is None
    assert "ok" in interp.get_output()


def test_energy_pool_decl_can_be_top_level():
    source = '''
energy_pool {
    max 50
    initial 10
    recharge 10 per 1 events
    rest_threshold 5
    hysteresis_margin 2
}
context "tick" { print("x") }
'''
    interp = run(source)
    assert interp.energy_pool.max == 50
    assert interp.energy_pool.current == 30  # enter + exit each recharge by 10, capped later if more events
