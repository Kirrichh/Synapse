"""Acceptance: an integrate transaction commits all it changed or leaves nothing behind.

Two transaction paths run DSL programs: the default one (snapshot and rollback
of the runtime's state) and the opt-in Alpha3g path (a state overlay whose
write-set is the transaction's record, replayed without rerunning the body).
Nondeterministic and external calls are refused inside either, whatever name
or form they are reached under — a callback included. A rollback puts the
runtime's objects back in place, so a reference taken before sees the state
restored.
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


def test_a_rollback_puts_back_enclosing_scopes_closure_state_and_shared_references():
    interp, error = run(
        'let x = 1\nlet box = {"n": 1}\nlet alias = box\nlet nest = {"inner": {"n": 1}}\nlet rows = [[1]]\n'
        'fn counter() {\n    let cell = {"n": 0}\n    fn bump() { cell.n = cell.n + 1\n return cell.n }\n    return bump\n}\n'
        'let bump = counter()\nfn change() { x = 9 }\n'
        'fn outer() {\n    let d = "d"\n    integrate d {\n        change()\n        box.n = 2\n        bump()\n        nest.inner.n = 2\n'
        '        let row = rows[0]\n        row.append(2)\n'
        '        assert false, "abort"\n    } on fail rollback\n}\nouter()\nalias.n = 3\nlet next = bump()\n')
    assert error is None
    assert interp.global_env.get("x") == 1
    assert interp.global_env.get("box") == {"n": 3} and interp.global_env.get("alias") is interp.global_env.get("box")
    assert interp.global_env.get("next") == 1
    assert interp.global_env.get("nest") == {"inner": {"n": 1}} and interp.global_env.get("rows") == [[1]]
    assert transaction_event(interp)["type"] == "integrate_rollback"


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


def test_random_reached_through_its_call_method_is_recorded_and_refused_in_the_overlay():
    live, error = run("let r = random\nlet value = r.__call__()\n")
    assert error is None
    assert [event["name"] for event in live.execution_history if event["type"] == "side_effect"] == ["random"]
    overlay, error = run("let r = random\nlet value = 1\nintegrate value {\n    value = r.__call__()\n} on fail rollback\n",
                         overlay=True)
    assert str(error) == "random is forbidden inside Alpha3g I2 integrate skeleton"
    assert overlay.global_env.get("value") == 1


def test_a_host_object_method_is_refused_inside_the_overlay_transaction_before_it_runs():
    calls = []

    class Provider:
        def complete(self):
            calls.append("performed")
            return 0.75
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.global_env.define("provider", Provider())
    with pytest.raises(Exception, match="Python callable is forbidden"):
        interp.interpret(compile_to_ast("let x = 1\nintegrate x {\n    x = provider.complete()\n} on fail rollback\n"))
    assert calls == [] and interp.global_env.get("x") == 1


def test_a_model_call_through_an_alias_is_refused_inside_the_default_transaction():
    interp, error = run('agent Oracle { model "mock" }\nlet ask = Oracle.think\nlet x = 1\nlet d = "d"\n'
                        'integrate d {\n    x = ask("value")\n} on fail rollback\n')
    assert str(error) == "think is forbidden inside integrate transaction"
    assert interp.global_env.get("x") == 1


def test_a_deterministic_builtin_under_another_name_runs_inside_the_overlay_transaction():
    live, error = run("let size = len\nlet x = 1\nintegrate x {\n    x = size([1, 2, 3])\n} on fail rollback\n", overlay=True)
    assert error is None
    assert live.global_env.get("x") == 3


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


@pytest.mark.parametrize("change, abort, seen", [
    ('memory.write("new") { reason "exercise" }', True, ["old"]),
    ('memory.write("new") { reason "exercise" }', False, ["old", "new"]),
    ("memory.clear()", True, ["old"]),
    ("memory.clear()", False, []),
    ("memory.forget()", True, ["old"]),
])
def test_an_agents_memory_after_the_transaction_is_one_state_for_every_holder(change, abort, seen):
    interp, error = run('agent Guide { model "mock" }\nlet self = Guide\nlet ref = Guide.memory\n'
                        'memory.write("old") { reason "setup" }\nlet x = 1\n'
                        f'integrate x {{\n    {change}\n'
                        + ('    assert false, "abort"\n' if abort else '') +
                        '} on fail rollback\nlet seen_ref = ref.read()\nlet seen_owner = Guide.memory.read()\n')
    assert error is None
    written = [entry["value"] for entry in interp.global_env.get("seen_owner")]
    assert written == seen
    assert interp.global_env.get("seen_ref") == interp.global_env.get("seen_owner")
    assert interp.global_env.get("ref") is interp.global_env.get("Guide").memory


@pytest.mark.parametrize("prelude, call, operation", [
    ("", "let drawn = uuid()", "uuid"),
    ("", "let drawn = time()", "time"),
    ("let r = random\n", "let drawn = r()", "random"),
])
def test_a_nondeterministic_call_is_refused_inside_the_default_transaction(prelude, call, operation):
    # A drawn value would leave the record with a rollback, and a replay would serve it a later draw (K1).
    interp, error = run(prelude + f'let x = 1\nintegrate x {{\n    x = 2\n    {call}\n}} on fail rollback\n')
    assert str(error) == f"{operation} is forbidden inside integrate transaction"
    assert interp.global_env.get("x") == 1
    assert not [event for event in interp.execution_history if event["type"] == "side_effect"]


@pytest.mark.parametrize("abort", [True, False])
def test_control_a_default_transaction_and_a_later_draw_replay_alike(abort):
    source = ('let x = 1\nintegrate x {\n    x = 2\n' + ('    assert false, "abort"\n' if abort else '')
              + '} on fail rollback\nlet final_value = uuid()\n')
    live, error = run(source)
    assert error is None
    again = Interpreter()
    again.load_snapshot(copy.deepcopy(live.snapshot()))
    again.interpret(compile_to_ast(source))
    assert again.global_env.get("final_value") == live.global_env.get("final_value")
    assert [event["type"] for event in again.execution_history] == [event["type"] for event in live.execution_history]
    assert again.global_env.get("x") == live.global_env.get("x") == (1 if abort else 2)


@pytest.mark.parametrize("overlay", [False, True])
@pytest.mark.parametrize("kind, shadow", [("map", ""), ("filter", ""), ("map", "let map = 0\n")],
                         ids=["map", "filter", "map-name-bound-to-data"])
def test_a_callback_reaching_a_model_is_refused_like_a_direct_call(kind, shadow, overlay):
    # A builtin name bound to data still calls the builtin: the barrier holds on that route too.
    interp, error = run('agent Oracle { model "mock" }\nlet ask = Oracle.think\n' + shadow
                        + f'let x = 1\nintegrate x {{\n    x = {kind}(ask, ["value"])\n}} on fail rollback\n', overlay=overlay)
    assert str(error) == "think is forbidden inside integrate transaction"
    assert interp.global_env.get("x") == 1
    assert interp.global_env.get("Oracle").memory.short_term == []


@pytest.mark.parametrize("overlay", [False, True])
def test_control_pure_callbacks_and_program_functions_work_inside_a_transaction(overlay):
    live, error = run('fn double(v) { return v * 2 }\nlet x = 1\nlet y = 1\nlet z = 1\nintegrate x {\n'
                      '    x = map(abs, [-2, 0, 3])\n    y = filter(abs, [-2, 0, 3])\n    z = map(double, [1, 2])\n'
                      '} on fail rollback\n', overlay=overlay)
    assert error is None
    assert [live.global_env.get(name) for name in ("x", "y", "z")] == [[2, 0, 3], [-2, 3], [2, 4]]
