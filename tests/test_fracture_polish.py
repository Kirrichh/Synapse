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


def test_nested_fracture_depth_2_succeeds():
    source = '''
agent Guide { model "mock" }
let self = Guide
let result = fracture self into {
    Analyst {
        let micro = fracture self into {
            MicroAnalyst { return "deep" }
        } consensus weighted
        return micro.positions.MicroAnalyst
    }
} consensus weighted
print(result.positions.Analyst)
'''
    interp = run(source)
    assert "deep" in interp.get_output()


def test_nested_fracture_depth_3_is_killed_not_global_panic():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst {
        let micro = fracture self into {
            Micro {
                let nano = fracture self into { Nano { return "too deep" } }
                return nano
            }
        }
        return micro
    }
    Other { return "ok" }
} consensus weighted integrate {
    print(consensus.positions.Other)
}
'''
    interp = run(source)
    out = interp.get_output()
    assert "ok" in out
    nested = [e for e in interp.execution_history if e.get("type") == "subagent_terminated" and e.get("subagent") == "Analyst"][0]
    assert nested["position"]["deaths"]["Micro"] == "KILLED_NESTED"


def test_nested_fracture_integrate_blocked():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst {
        let micro = fracture self into {
            Micro { return "x" }
        } consensus weighted integrate {
            print("not allowed")
        }
        return micro
    }
    Other { return "ok" }
} consensus weighted integrate {
    print(consensus.positions.Other)
}
'''
    interp = run(source)
    out = interp.get_output()
    assert "ok" in out
    assert "not allowed" not in out
    nested = [e for e in interp.execution_history if e.get("type") == "subagent_terminated" and e.get("subagent") == "Analyst"][0]
    assert nested["death_type"] == "KILLED_NESTED"


def test_ephemeral_compaction_removes_subagent_llm_from_main_history():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst {
        let a = llm "test call 1"
        let b = llm "test call 2"
        assert true
        return "done"
    }
} consensus weighted
'''
    interp = run(source)
    subagent_events = [e for e in interp.execution_history if e.get("type") == "subagent_terminated"]
    assert len(subagent_events) == 1
    assert subagent_events[0]["ephemeral_summary"]["llm_calls"] == 2
    assert not [e for e in interp.execution_history if e.get("type") == "llm_call"]


def test_granular_death_types_memory():
    source = '''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst { memory.write("bad") { reason "test" } }
} consensus weighted
'''
    try:
        interp = run(source)
    except FracturePanicException:
        assert False, "memory isolation should kill subagent, not panic fracture"
    deaths = [e for e in interp.execution_history if e.get("type") == "subagent_terminated"]
    assert deaths[0]["death_type"] == "KILLED_MEMORY"


def test_evolve_cooldown_deferred():
    source = '''
agent Guide { model "mock" }
policy CooldownPolicy {
    target "evolve.Guide"
    cooldown: 5
}
let self = Guide
evolve self under CooldownPolicy { let note = "first" }
evolve self under CooldownPolicy { let note = "too soon" }
'''
    interp = run(source)
    deferred = [e for e in interp.execution_history if e.get("type") == "evolution_deferred"]
    assert len(deferred) == 1
    assert deferred[0]["reason"] == "cooldown"
    assert deferred[0]["events_remaining"] == 5


if __name__ == "__main__":
    test_nested_fracture_depth_2_succeeds()
    test_nested_fracture_depth_3_is_killed_not_global_panic()
    test_nested_fracture_integrate_blocked()
    test_ephemeral_compaction_removes_subagent_llm_from_main_history()
    test_granular_death_types_memory()
    test_evolve_cooldown_deferred()
    print("All fracture polish tests passed!")
