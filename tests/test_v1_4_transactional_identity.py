from synapse.lexer import Lexer, TokenType
from synapse.parser import Parser
from synapse.ast import AssertStmt, IntegrateBlock, EvolveStmt, PolicyDef
from synapse.interpreter import Interpreter, IntegrateIsolationViolation, RuntimeError


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source: str):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def test_v14_tokens():
    tokens = Lexer('assert x under Policy on fail rollback warn halt ticket evolution').scan_tokens()
    types = [t.type for t in tokens]
    assert TokenType.ASSERT in types
    assert TokenType.UNDER in types
    assert TokenType.FAIL in types
    assert TokenType.ROLLBACK in types
    assert TokenType.WARN in types
    assert TokenType.HALT in types


def test_parse_assert_integrate_evolve_policy_fields():
    source = '''
policy AlignmentPolicy {
    target "evolve.Guide"
    trigger: true
    cooldown: 10 events
    max_delta: 0.05
    guard: soulprint.values.integrity >= 0.9
    require_approval: false
}
let result = "insight"
integrate result { assert true } on fail rollback
evolve self when true after 10 events under AlignmentPolicy { let note = "ok" }
'''
    ast = parse(source)
    assert isinstance(ast.statements[0], PolicyDef)
    assert ast.statements[0].cooldown is not None
    assert ast.statements[0].guard_expr is not None
    assert isinstance(ast.statements[2], IntegrateBlock)
    assert isinstance(ast.statements[2].body[0], AssertStmt)
    assert isinstance(ast.statements[3], EvolveStmt)
    assert ast.statements[3].policy_ref == 'AlignmentPolicy'
    assert ast.statements[3].delay_unit == 'events'


def test_integrate_rollback_on_assert_failure():
    interp = run('''
let x = 1
let dream_result = "insight"
integrate dream_result {
    x = 2
    assert x == 3, "bad integration"
} on fail rollback
print(x)
''')
    assert interp.get_output() == '1'
    assert interp.global_env.get('x') == 1
    assert any(e.get('type') == 'integrate_rollback' for e in interp.execution_history)


def test_integrate_warn_keeps_mutation():
    interp = run('''
let x = 1
let dream_result = "insight"
integrate dream_result {
    x = 2
    assert false, "warn only"
} on fail warn
print(x)
''')
    assert 'Integrate warning: warn only' in interp.get_output()
    assert interp.global_env.get('x') == 2


def test_integrate_blocks_llm():
    try:
        run('''
let dream_result = "insight"
integrate dream_result {
    let r = llm "forbidden"
} on fail halt
''')
    except IntegrateIsolationViolation:
        return
    assert False, 'Expected IntegrateIsolationViolation'


def test_evolve_under_policy_and_ticket():
    interp = run('''
policy AlignmentPolicy {
    target "evolve.Guide"
    guard: soulprint.values.integrity >= 0.9
    require_approval: false
}
agent Guide {
    model "mock"
    soulprint { values: [ integrity: 1.0 ] memory: long_term style: "ok" }
}
let self = Guide
evolve self when false after 3 events under AlignmentPolicy {
    let note = "not yet"
}
evolve self when true after 3 events under AlignmentPolicy {
    let note = "now"
}
''')
    assert interp.evolution_tickets
    assert any(e.get('type') == 'soulprint_evolved' and e.get('policy') == 'AlignmentPolicy' for e in interp.execution_history)


if __name__ == '__main__':
    test_v14_tokens()
    test_parse_assert_integrate_evolve_policy_fields()
    test_integrate_rollback_on_assert_failure()
    test_integrate_warn_keeps_mutation()
    test_integrate_blocks_llm()
    test_evolve_under_policy_and_ticket()
    print('All v1.4 transactional identity tests passed!')
