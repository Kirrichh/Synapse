from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, DreamIsolationViolation


def compile_ast(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source: str, interp=None):
    interp = interp or Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_resonate_profile_binding_and_event():
    interp = Interpreter()
    interp.execution_history.append({
        "type": "message_received",
        "message": {"sender": "@user", "payload": "срочно нужен runtime replay AST patch!!!"},
    })
    run('''
resonate with @user {
    depth deep
    aspects ["emotional_tone", "knowledge_level", "urgency"]
    window 20
    bind profile
}
print(profile.aspects.emotional_tone.value)
print(profile.aspects.knowledge_level.value)
''', interp)
    out = interp.get_output()
    assert "anxious" in out
    assert "expert" in out
    assert any(e.get("type") == "resonance_profile_computed" for e in interp.execution_history)


def test_unknown_aspect_is_safe():
    interp = run('''
resonate with @user {
    aspects ["unknown_x"]
    window 5
    bind profile
}
print(profile.aspects.unknown_x.confidence)
''')
    assert "0.0" in interp.get_output()


def test_reflect_on_fractures_returns_events():
    interp = run('''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst { return "ok" }
} consensus weighted
let fracture_log = reflect on fractures { last 3 events }
print(fracture_log.length)
''')
    assert "3" in interp.get_output() or "2" in interp.get_output()


def test_measure_identity_coherence():
    interp = run('''
measure identity_coherence {
    window 20
    metrics ["soulprint_stability", "fracture_consensus_rate", "resonance_drift"]
    bind coherence
}
print(coherence.score)
''')
    assert interp.get_output().strip()


def test_resonate_forbidden_inside_dream():
    try:
        run('''
dream {
    resonate with @user { aspects ["urgency"] window 5 }
}
''')
        assert False, "expected DreamIsolationViolation"
    except Exception as exc:
        assert "resonate is forbidden inside dream" in str(exc)


def test_resonate_inside_fracture_kills_subagent_not_base():
    interp = run('''
agent Guide { model "mock" }
let self = Guide
fracture self into {
    Analyst {
        resonate with @user { aspects ["urgency"] window 5 }
        return "bad"
    }
    Guardian { return "ok" }
} consensus weighted
print("done")
''')
    deaths = [e for e in interp.execution_history if e.get("type") == "subagent_terminated"]
    assert any(e.get("subagent") == "Analyst" and e.get("death_type") == "KILLED_ISOLATION" for e in deaths)
    assert "done" in interp.get_output()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All resonance/inter-subjectivity tests passed!")
