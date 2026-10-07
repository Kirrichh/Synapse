"""Acceptance: an integrate transaction commits all it changed or leaves nothing behind.

Two transaction paths run DSL programs: the default one (snapshot and rollback
of the runtime's state) and the opt-in Alpha3g path (a state overlay whose
write-set is the transaction's record, replayed without rerunning the body).
Nondeterministic and external calls are refused inside either, whatever name
they are reached under.
"""
import copy

import pytest

from synapse import Interpreter, compile_to_ast
from synapse.interpreter import RuntimeMode


def run(source, overlay=False):
    """The interpreter after the program and the error it raised, if any."""
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = overlay
    interp.source_code = source
    try:
        interp.interpret(compile_to_ast(source))
    except Exception as error:  # the program's own failure is part of the observation
        return interp, error
    return interp, None


def replay(source, history):
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.execution_history = copy.deepcopy(history)
    interp.runtime_mode = RuntimeMode.REPLAY
    interp.source_code = source
    try:
        interp.interpret(compile_to_ast(source))
    except Exception:  # an aborted transaction is raised again on replay
        pass
    return interp


def transaction_event(interp):
    return [event for event in interp.execution_history if event["type"].startswith("integrate_")][-1]


def test_a_failing_body_rolls_back_whatever_its_on_fail_policy():
    interp, error = run('let x = 1\nlet dream_result = "d"\nintegrate dream_result {\n    x = 2\n'
                        '    print("must roll back")\n    let y = 1 / 0\n} on fail warn\n')
    assert str(error) == "Division by zero"
    assert interp.global_env.get("x") == 1
    assert interp.get_output() == ""
    event = transaction_event(interp)
    assert (event["type"], event["error"], event["cause"]) == ("integrate_rollback", "RuntimeError", "Division by zero")


def test_a_rollback_restores_mood_palace_records_and_somatic_markers():
    interp, error = run(
        'affective state "Mood" { baseline { valence 0.0 arousal 0.4 dominance 0.6 } bind mood }\n'
        'memory palace "P" { rooms { semantic } backend sqlite bind palace }\n'
        'let dream_result = "d"\n'
        'integrate dream_result {\n'
        '    affective event "joy" { valence 0.9 bind joy }\n'
        '    imprint into palace.semantic { content "must roll back" confidence 1.0 bind mid }\n'
        '    imprint into palace.hidden { content "new room" confidence 1.0 bind hid }\n'
        '    memory palace "Inner" { rooms { semantic } backend sqlite bind inner }\n'
        '    somatic marker "gut" { gut_feeling 0.9 bind marker }\n'
        '    assert false, "abort"\n'
        '} on fail rollback\n'
        'recall from palace.semantic { query "must roll back" limit 5 bind found }\n')
    assert error is None
    assert interp.affective_states["Mood"].current == {"valence": 0.0, "arousal": 0.4, "dominance": 0.6}
    assert interp.global_env.get("found") == []
    assert interp.memory_palaces["P"].rooms == ["semantic"]
    assert sorted(interp.memory_palaces) == ["P"]
    assert interp.somatic_markers == {}
    assert transaction_event(interp)["type"] == "integrate_rollback"


def test_a_committed_transaction_keeps_what_it_changed():
    interp, error = run(
        'affective state "Mood" { baseline { valence 0.0 arousal 0.4 dominance 0.6 } bind mood }\n'
        'let dream_result = "d"\n'
        'integrate dream_result {\n    affective event "joy" { valence 0.5 bind joy }\n} on fail rollback\n')
    assert error is None
    assert interp.affective_states["Mood"].current["valence"] == 0.5
    assert transaction_event(interp)["type"] == "integrate_committed"


CLOSURE = 'let x = 1\nfn setx() { x = 9 }\nintegrate x {\n    setx()\nBODY} on fail rollback\n'


def test_a_function_declared_outside_writes_through_the_transaction_and_its_write_replays():
    source = CLOSURE.replace("BODY", "")
    live, error = run(source, overlay=True)
    assert error is None
    assert live.global_env.get("x") == 9
    event = transaction_event(live)
    assert event["type"] == "integrate_committed"
    assert [write["path"] for write in event["write_set"]] == ["/env/x"]
    assert replay(source, live.execution_history).global_env.get("x") == 9


def test_an_aborted_transaction_discards_a_function_write():
    live, error = run(CLOSURE.replace("BODY", '    assert false, "abort"\n'), overlay=True)
    assert str(error) == "abort"
    assert live.global_env.get("x") == 1
    assert transaction_event(live)["type"] == "integrate_aborted"


def test_a_function_declared_in_a_block_of_the_body_keeps_that_block_scope():
    live, error = run('let x = 1\nintegrate x {\n    if true {\n        let local = 4\n'
                      '        fn read() { return local }\n        x = read()\n    }\n} on fail rollback\n', overlay=True)
    assert error is None
    assert live.global_env.get("x") == 4


def test_a_function_reads_the_value_the_transaction_wrote():
    live, error = run('let x = 1\nfn getx() { return x }\nlet seen = 0\n'
                      'integrate x {\n    x = 5\n    seen = getx()\n} on fail rollback\n', overlay=True)
    assert error is None
    assert (live.global_env.get("x"), live.global_env.get("seen")) == (5, 5)


@pytest.mark.parametrize("source, reason", [
    ('fn make() {\n    let n = 0\n    fn inc() { n = n + 1\n return n }\n    return inc\n}\n'
     'let inc = make()\nlet x = 1\nintegrate x {\n    x = inc()\n} on fail rollback\n',
     "a function closing over state outside the integrate scope cannot run inside the transaction"),
    ('let x = 1\nfn getx() { return x }\nfn outer() {\n    let x = 2\n    integrate x {\n        x = getx()\n'
     '    } on fail rollback\n}\nouter()\n',
     "a function declared outside the scope shadowing ['x'] cannot run inside the transaction"),
])
def test_a_function_whose_state_the_transaction_cannot_hold_is_refused(source, reason):
    live, error = run(source, overlay=True)
    assert str(error) == reason
    assert live.global_env.get("x") == 1
    assert transaction_event(live)["abort_reason"] == "barrier_violation"


@pytest.mark.parametrize("prelude, call, operation", [
    ('agent Oracle { model "mock" }\n', 'x = Oracle.think("value")', "think"),
    ("let r = random\n", "x = r()", "random"),
    ("let clock = time\n", "x = clock()", "time"),
    ("let p = print\n", 'p("x")', "print"),
])
def test_a_nondeterministic_call_is_refused_under_any_name(prelude, call, operation):
    live, error = run(prelude + f"let x = 1\nintegrate x {{\n    {call}\n}} on fail rollback\n", overlay=True)
    assert error is not None and str(error).startswith(f"{operation} is forbidden")
    assert live.global_env.get("x") == 1
    event = transaction_event(live)
    assert (event["abort_reason"], event["barrier_op"]) == ("barrier_violation", operation)


def test_a_host_function_is_refused_inside_the_overlay_transaction():
    calls = []
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.global_env.define("provider", lambda: calls.append("called") or 0.73)
    with pytest.raises(Exception, match="Python callable is forbidden"):
        interp.interpret(compile_to_ast("let x = 1\nintegrate x {\n    x = provider()\n} on fail rollback\n"))
    assert calls == []
    assert interp.global_env.get("x") == 1


def test_a_model_call_is_refused_inside_the_default_transaction_too():
    interp, error = run('agent Oracle { model "mock" }\nlet x = 1\nlet dream_result = "d"\n'
                        'integrate dream_result {\n    x = Oracle.think("value")\n} on fail rollback\n')
    assert str(error) == "think is forbidden inside integrate transaction"
    assert interp.global_env.get("x") == 1
