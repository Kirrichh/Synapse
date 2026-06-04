from synapse.lexer import Lexer, TokenType
from synapse.parser import Parser
from synapse.ast import AgentDef, DreamBlock, EvolveStmt, ReflectBlock
from synapse.interpreter import Interpreter, DreamIsolationViolation, IdentityCrisisError


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def test_inner_life_tokens():
    source = 'soulprint dream scenario depth constraints integrate evolve when after with self values temperature'
    types = [t.type for t in Lexer(source).scan_tokens()]
    for tok in [TokenType.SOULPRINT, TokenType.DREAM, TokenType.SCENARIO, TokenType.DEPTH,
                TokenType.CONSTRAINTS, TokenType.INTEGRATE, TokenType.EVOLVE, TokenType.WHEN,
                TokenType.AFTER, TokenType.WITH, TokenType.SELF, TokenType.VALUES, TokenType.TEMPERATURE]:
        assert tok in types


def test_soulprint_parses_inside_agent():
    ast = compile_ast('''
agent Guide {
  model "mock"
  soulprint {
    values: [ curiosity: 0.94, integrity: 1.0 ]
    memory: long_term
    style: "precise"
  }
}
''')
    agent = ast.statements[0]
    assert isinstance(agent, AgentDef)
    assert agent.soulprint.values["curiosity"] == 0.94
    assert agent.soulprint.style == "precise"


def test_dream_integrate_executes_after_sandbox():
    source = '''
agent Guide { model "mock" }
let self = Guide
let result = dream {
  scenario "market stress test"
  temperature 0.8
  depth deep
  return "insight-1"
} integrate {
  print(dream_result)
}
'''
    interp = Interpreter()
    interp.interpret(compile_ast(source))
    assert "insight-1" in interp.get_output()
    assert any(e.get("type") == "dream_completed" for e in interp.execution_history)


def test_dream_blocks_external_side_effects():
    source = '''
agent Guide { model "mock" }
let self = Guide
let result = dream {
  memory.write("bad")
}
'''
    interp = Interpreter()
    try:
        interp.interpret(compile_ast(source))
        assert False, "dream should reject memory side effects"
    except DreamIsolationViolation:
        pass


def test_dream_blocks_governed_memory_write():
    source = '''
agent Guide { model "mock" }
let self = Guide
let result = dream {
  memory.write("bad") { reason "bypass" }
}
'''
    interp = Interpreter()
    try:
        interp.interpret(compile_ast(source))
        assert False, "dream should reject governed memory writes"
    except DreamIsolationViolation:
        pass


def test_evolve_records_guarded_identity_event():
    source = '''
agent Guide {
  model "mock"
  soulprint { values: [ curiosity: 0.5 ] memory: long_term style: "stable" }
}
let self = Guide
let user_satisfaction = 0.7
evolve self when user_satisfaction < 0.8 after 10 with "AlignmentPolicy" {
  let note = "increase curiosity carefully"
}
'''
    interp = Interpreter()
    interp.interpret(compile_ast(source))
    events = [e for e in interp.execution_history if e.get("type") == "soulprint_evolved"]
    assert len(events) == 1
    assert events[0]["safety_guard"] == "AlignmentPolicy"


def test_direct_soulprint_assignment_is_blocked():
    source = 'soulprint = "overwrite"'
    interp = Interpreter()
    try:
        interp.interpret(compile_ast(source))
        assert False, "direct soulprint assignment must be blocked"
    except IdentityCrisisError:
        pass


def test_reflect_on_values():
    source = '''
agent Guide { model "mock" soulprint { values: [ curiosity: 0.9 ] memory: long_term style: "" } }
let self = Guide
let vals = reflect on values { last 1 events }
print(vals["curiosity"])
'''
    interp = Interpreter()
    interp.interpret(compile_ast(source))
    assert "0.9" in interp.get_output()

if __name__ == "__main__":
    test_inner_life_tokens()
    test_soulprint_parses_inside_agent()
    test_dream_integrate_executes_after_sandbox()
    test_dream_blocks_external_side_effects()
    test_dream_blocks_governed_memory_write()
    test_evolve_records_guarded_identity_event()
    test_direct_soulprint_assignment_is_blocked()
    test_reflect_on_values()
    print("All inner life tests passed!")
