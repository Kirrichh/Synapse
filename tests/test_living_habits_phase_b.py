from synapse import Lexer, Parser, Interpreter


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def test_suppress_priority_blocks_candidate_even_when_activate_matches():
    source = '''
affective state "Mood" { baseline { valence -0.6 arousal 0.8 dominance 0.3 } bind mood }
affective threshold "HighStress" { when arousal > 0.7 for 1 events action { suspend emergency_pause("x") } }
habit "DeepAnalysis" from pattern {
    energy_cost 0.4
    priority high
    activate when HighStress
    suppress when { mood.valence < -0.5 }
    bind habit_id
}
affective event "stress" { arousal 0.0 bind tag }
'''
    interp = run(source)
    suppressed = [e for e in interp.execution_history if e.get("type") == "habit_suppressed" and e.get("habit_name") == "DeepAnalysis"]
    candidates = [e for e in interp.execution_history if e.get("type") == "habit_candidate_suggested" and e.get("habit_name") == "DeepAnalysis"]
    assert suppressed
    assert not candidates
    assert suppressed[0]["reason"] == "suppress_when_matched"


def test_or_activation_second_block_matches():
    source = '''
affective state "Mood" { baseline { valence 0.1 arousal 0.2 dominance 0.6 } bind mood }
habit "ContextHabit" from pattern {
    activate when OtherThreshold
    activate when { context "task_b" }
    energy_cost 0.0
    bind habit_id
}
context "task_b" { print("inside") }
'''
    interp = run(source)
    candidates = [e for e in interp.execution_history if e.get("type") == "habit_candidate_suggested" and e.get("habit_name") == "ContextHabit"]
    assert candidates
    assert candidates[0]["trigger"] == "context_entered"


def test_inline_pad_conditions_are_and_semantics():
    source = '''
affective state "Mood" { baseline { valence 0.0 arousal 0.6 dominance 0.5 } bind mood }
habit "PadHabit" from pattern {
    activate when { mood.arousal > 0.5 mood.valence < -0.2 }
    bind habit_id
}
affective event "tick" { arousal 0.0 bind tag }
'''
    interp = run(source)
    assert not any(e.get("type") == "habit_candidate_suggested" and e.get("habit_name") == "PadHabit" for e in interp.execution_history)


def test_threshold_ref_requires_exact_name():
    source = '''
affective state "Mood" { baseline { valence 0.0 arousal 0.8 dominance 0.5 } bind mood }
affective threshold "Other" { when arousal > 0.7 for 1 events action { suspend emergency_pause("x") } }
habit "ExactHabit" from pattern {
    activate when HighStress
    bind habit_id
}
affective event "tick" { arousal 0.0 bind tag }
'''
    interp = run(source)
    assert any(e.get("type") == "affective_threshold_triggered" and e.get("threshold") == "Other" for e in interp.execution_history)
    assert not any(e.get("type") == "habit_candidate_suggested" and e.get("habit_name") == "ExactHabit" for e in interp.execution_history)


def test_context_mismatch_does_not_activate():
    source = '''
habit "ContextHabit" from pattern {
    activate when { context "task_a" }
    bind habit_id
}
context "task_b" { print("b") }
'''
    interp = run(source)
    assert not any(e.get("type") == "habit_candidate_suggested" and e.get("habit_name") == "ContextHabit" for e in interp.execution_history)


def test_replay_mode_skips_habit_processing():
    interp = Interpreter()
    interp.runtime_mode = interp.runtime_mode.REPLAY if hasattr(interp.runtime_mode, 'REPLAY') else interp.runtime_mode
    source = '''
habit "ReplayHabit" from pattern {
    activate when { context "task" }
    bind habit_id
}
'''
    interp.source_code = source
    interp.interpret(compile_ast(source))
    before = len(interp.execution_history)
    interp.process_habits_on_event({"type": "context_entered", "label": "task"})
    after = len(interp.execution_history)
    assert before == after


def test_registry_o1_subscriptions_reduce_candidate_scan():
    source = '''
'''
    interp = run(source)
    from synapse.habit import HabitRuntimeRecord
    from synapse.ast import ThresholdRef
    for i in range(50):
        interp.habit_registry.register(HabitRuntimeRecord(name=f"H{i}", activate_when=[ThresholdRef(name=f"T{i}")], suppress_when=[], energy_cost=0.0))
    candidates = interp.habit_registry.get_candidates("context_entered")
    assert candidates == []
    threshold_candidates = interp.habit_registry.get_candidates("affective_threshold_triggered")
    assert len(threshold_candidates) == 50
