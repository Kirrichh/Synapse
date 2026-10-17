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


SPEC_V15_EXAMPLE = '''
agent Base { model "mock" }
let self = Base
fracture self into {
    Analyst {
        focus "rational analysis"
        return "position"
    }

    Guardian {
        assert false, "safety concern"
    }
} consensus weighted integrate {
    reason "multi-perspective synthesis"
    print(consensus.positions.Analyst)
}
'''


def _integrated(interp):
    return [e for e in interp.execution_history if e.get("type") == "identity_integrated"]


def test_integrate_reason_of_the_spec_example_is_audit_metadata():
    interp = run(SPEC_V15_EXAMPLE)
    assert interp.get_output() == "position"
    event, = _integrated(interp)
    assert event["reason"] == "multi-perspective synthesis"


def test_integrate_reason_may_read_the_consensus():
    source = SPEC_V15_EXAMPLE.replace('reason "multi-perspective synthesis"',
                                      'reason "kept: " + consensus.positions.Analyst')
    event, = _integrated(run(source))
    assert event["reason"] == "kept: position"


def test_an_integration_of_only_a_reason_records_it():
    source = SPEC_V15_EXAMPLE.replace("    print(consensus.positions.Analyst)\n", "")
    interp = run(source)
    event, = _integrated(interp)
    assert event["reason"] == "multi-perspective synthesis"
    assert interp.get_output() == ""


def test_without_a_reason_the_integration_record_and_identity_are_unchanged():
    source = SPEC_V15_EXAMPLE.replace('    reason "multi-perspective synthesis"\n', "")
    interp = run(source)
    event, = _integrated(interp)
    assert "reason" not in event
    fracture = next(node for node in compile_ast(source).statements if type(node).__name__ == "FractureStmt")
    assert "integration_reason" not in Interpreter()._ast_to_canonical_data(fracture)


def test_a_nested_fracture_cannot_integrate_even_with_only_a_reason():
    source = '''
agent Base { model "mock" }
let self = Base
fracture self into {
    Outer {
        let inner = fracture self into {
            Deep { return "deep" }
        } consensus weighted integrate {
            reason "nested audit"
        }
        return inner
    }
} consensus weighted
'''
    interp = run(source)
    outer = [e for e in interp.execution_history if e.get("type") == "subagent_terminated" and e.get("subagent") == "Outer"][0]
    assert outer["death_type"] == "KILLED_NESTED"
