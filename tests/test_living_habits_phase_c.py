from synapse import Lexer, Parser, Interpreter


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def run(source):
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(compile_ast(source))
    return interp


def events(interp, typ):
    return [e for e in interp.execution_history if e.get("type") == typ]


def test_habit_body_executes_and_consumes_energy():
    source = '''
energy_pool { max 10 initial 5 recharge 0 per 100 events rest_threshold 1 hysteresis_margin 1 }
habit "BodyHabit" from pattern {
    energy_cost 2
    activate when { context "task" }
    body { print("habit body") }
    bind habit_id
}
context "task" { print("task body") }
'''
    interp = run(source)
    assert "habit body" in interp.get_output()
    activated = events(interp, "habit_activated")
    assert activated
    assert activated[0]["habit_name"] == "BodyHabit"
    assert activated[0]["energy_spent"] == 2.0
    assert interp.energy_pool.current <= 3


def test_body_failure_does_not_increment_activation_and_refunds_energy():
    source = '''
energy_pool { max 10 initial 5 recharge 0 per 100 events rest_threshold 1 hysteresis_margin 1 }
habit "FailHabit" from pattern {
    energy_cost 2
    activate when { context "task" }
    body { assert false, "boom" }
    bind habit_id
}
context "task" { print("after") }
'''
    interp = run(source)
    failed = events(interp, "habit_execution_failed")
    assert failed
    assert failed[0]["activation_count_unchanged"] is True
    assert not events(interp, "habit_activated")
    assert interp.energy_pool.current >= 5


def test_fatigue_and_recovery_after_rest_events():
    source = '''
habit "FatigueHabit" from pattern {
    energy_cost 0
    activate when { context "task" }
    fatigue after 2 activations { energy_cost_multiplier 2 require_rest 2 events }
    body { print("hit") }
    bind habit_id
}
context "task" { print("a") }
context "task" { print("b") }
affective event "tick1" { arousal 0.0 bind t1 }
affective event "tick2" { arousal 0.0 bind t2 }
'''
    interp = run(source)
    assert len(events(interp, "habit_activated")) >= 2
    assert events(interp, "habit_fatigued")
    assert events(interp, "habit_recovered")


def test_recursive_same_habit_is_blocked_by_lock():
    source = '''
habit "RecursiveHabit" from pattern {
    energy_cost 0
    activate when { context "loop" }
    body { context "loop" { print("inner") } }
    bind habit_id
}
context "loop" { print("outer") }
'''
    interp = run(source)
    failed = events(interp, "habit_execution_failed")
    assert any("self-lock" in e.get("error", "") for e in failed)


def test_replay_skips_body_execution():
    interp = Interpreter()
    interp.runtime_mode = interp.runtime_mode.REPLAY if hasattr(interp.runtime_mode, 'REPLAY') else interp.runtime_mode
    source = '''
habit "ReplayBody" from pattern {
    activate when { context "task" }
    body { print("should not run") }
    bind habit_id
}
'''
    interp.source_code = source
    interp.interpret(compile_ast(source))
    before = len(interp.execution_history)
    interp.process_habits_on_event({"type": "context_entered", "label": "task"})
    assert len(interp.execution_history) == before
    assert "should not run" not in interp.get_output()


def test_without_energy_pool_body_executes_free():
    source = '''
habit "FreeHabit" from pattern {
    energy_cost 100
    activate when { context "task" }
    body { print("free") }
    bind habit_id
}
context "task" { print("done") }
'''
    interp = run(source)
    assert "free" in interp.get_output()
    activated = events(interp, "habit_activated")
    assert activated and activated[0]["energy_pool_after"] is None
