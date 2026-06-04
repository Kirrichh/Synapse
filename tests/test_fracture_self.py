from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, FracturePanicException


def compile_ast(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source: str):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_fracture_natural_and_aborted_continue_to_integrate():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst { return "rational-position" }
    Guardian { assert false, "safety concern" }
} consensus weighted integrate {
    print(consensus.deaths.Guardian)
    print(consensus.positions.Analyst)
}
'''
    interp = run(source)
    assert "ABORTED" in interp.get_output()
    assert "rational-position" in interp.get_output()
    events = [e.get("type") for e in interp.execution_history]
    assert "identity_fractured" in events
    assert "identity_integrated" in events
    guardian = [e for e in interp.execution_history if e.get("type") == "subagent_terminated" and e.get("subagent") == "Guardian"][0]
    assert guardian["death_type"] == "ABORTED"


def test_forbidden_side_effect_kills_subagent_not_base():
    source = '''
agent Guide { model "mock" }
agent Worker { model "mock" }
let self = Guide
fracture self into {
    Analyst { return "ok" }
    Sender { send Worker.process("forbidden") }
} consensus weighted integrate {
    print(consensus.deaths.Sender)
    print(consensus.positions.Analyst)
}
print("base-alive")
'''
    interp = run(source)
    out = interp.get_output()
    assert "KILLED" in out
    assert "ok" in out
    assert "base-alive" in out
    killed = [e for e in interp.execution_history if e.get("type") == "subagent_terminated" and e.get("subagent") == "Sender"][0]
    assert killed["death_type"].startswith("KILLED")
    assert "forbidden inside sub-agent" in killed["reason"]


def test_unhandled_runtime_error_panics_fracture():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Broken { unknown_function() }
} consensus weighted integrate {
    print("should-not-integrate")
}
'''
    interp = Interpreter()
    interp.source_code = source
    try:
        interp.interpret(compile_ast(source))
        assert False, "expected FracturePanicException"
    except FracturePanicException:
        pass
    assert any(e.get("type") == "fracture_panic" for e in interp.execution_history)
    assert "should-not-integrate" not in interp.get_output()


def test_nested_fracture_allowed_at_depth_2():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Meta {
        let micro = fracture self into { Micro { return "x" } } consensus weighted
        return micro.positions.Micro
    }
    Analyst { return "ok" }
} consensus weighted integrate {
    print(consensus.deaths.Meta)
    print(consensus.positions.Meta)
    print(consensus.positions.Analyst)
}
'''
    interp = run(source)
    assert "NATURAL" in interp.get_output()
    assert "x" in interp.get_output()
    assert "ok" in interp.get_output()


if __name__ == "__main__":
    test_fracture_natural_and_aborted_continue_to_integrate()
    test_forbidden_side_effect_kills_subagent_not_base()
    test_unhandled_runtime_error_panics_fracture()
    test_nested_fracture_allowed_at_depth_2()
    print("All fracture self tests passed!")
