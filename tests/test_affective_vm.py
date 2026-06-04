from synapse import Lexer, Parser, Interpreter
from synapse.cvm import CognitiveVM, OutOfEnergy
from synapse.bytecode import CognitiveCompiler


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter(); interp.source_code = source; interp.interpret(compile_ast(source)); return interp


def test_affective_state_event_modulation_and_marker():
    source = '''
affective state "AgentMood" {
    dimensions {
        valence [-1.0, 1.0]
        arousal [0.0, 1.0]
        dominance [0.0, 1.0]
    }
    baseline {
        valence 0.2
        arousal 0.4
        dominance 0.6
    }
    decay 0.05 per minute
    bind mood
}

affective event "policy_violation" {
    valence -0.8
    arousal 0.6
    dominance -0.3
    duration 300
    bind emotional_tag
}

affective modulation {
    bind modulation_rules
}

somatic marker "deploy_decision" {
    threshold 0.4
    bind marker
}
print(modulation_rules.suppress)
print(marker.negative)
'''
    interp = run(source)
    out = interp.get_output()
    assert "migrate" in out
    assert "True" in out
    assert any(e.get("type") == "affective_event_tagged" for e in interp.execution_history)
    assert any(e.get("type") == "somatic_marker_evaluated" for e in interp.execution_history)


def test_affective_resonance_with_user():
    source = '''
affective state "AgentMood" {
    baseline { valence 0.0 arousal 0.4 dominance 0.6 }
    bind mood
}
resonate with @user { aspects ["emotional_tone"] window 5 bind profile }
affective resonance with @user {
    mirror emotional_tone
    regulate valence
    dampen arousal 0.2
    bind bridge
}
print(bridge.recommendation)
'''
    interp = run(source)
    assert "resonance" in interp.get_output() or "regulation" in interp.get_output()
    assert any(e.get("type") == "affective_resonance_applied" and e.get("atomic") is True for e in interp.execution_history)


def test_vm_compile_and_gas_snapshot():
    source = '''
compile vm { source "let x = 1" bind code }
run vm { source code gas 20 bind result }
print(result.halted)
print(result.transition_hash)
'''
    interp = run(source)
    out = interp.get_output()
    assert "True" in out
    assert any(e.get("type") == "vm_bytecode_compiled" for e in interp.execution_history)
    assert any(e.get("type") == "vm_executed" for e in interp.execution_history)
    assert interp.vm_snapshots


def test_vm_out_of_energy():
    source = '''
compile vm { source "let x = 1" bind code }
run vm { source code gas 0 bind result }
print(result.error)
'''
    interp = run(source)
    assert "OUT_OF_ENERGY" in interp.get_output()
