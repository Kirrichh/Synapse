from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, IntegrateIsolationViolation
from synapse.state_overlay import WriteSet


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run_i3(source: str) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def test_integrate_committed_event_emitted_and_applies_base_state():
    interp = run_i3('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    events = [e for e in interp.execution_history if e.get("type") == "integrate_committed"]
    assert len(events) == 1
    event = events[0]
    assert event["schema_version"] == "alpha3g.integrate.v1"
    assert event["pre_state_hash"].startswith("sha256:")
    assert event["post_state_hash"].startswith("sha256:")
    assert event["write_set_hash"].startswith("sha256:")
    assert event["write_set"][0]["path"] == "/env/x"
    assert event["write_set"][0]["new_value"] == 2
    assert interp.global_env.get("x") == 2
    assert isinstance(interp.last_integrate_write_set, WriteSet)


def test_integrate_committed_empty_writeset_for_noop():
    interp = run_i3('''
let x = 1
integrate x {
    x = 1
} on fail rollback
''')
    event = [e for e in interp.execution_history if e.get("type") == "integrate_committed"][-1]
    assert event["write_set"] == []
    assert interp.last_integrate_write_set.entries == ()
    assert interp.global_env.get("x") == 1


def test_integrate_committed_writeset_is_sorted():
    interp = run_i3('''
let b = 1
let a = 1
integrate b {
    b = 2
    a = 3
} on fail rollback
''')
    event = [e for e in interp.execution_history if e.get("type") == "integrate_committed"][-1]
    assert [entry["path"] for entry in event["write_set"]] == ["/env/a", "/env/b"]
    assert interp.global_env.get("a") == 3
    assert interp.global_env.get("b") == 2


def test_integrate_aborted_event_on_barrier_violation_and_no_base_apply():
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    source = '''
let x = 1
integrate x {
    x = 2
    random()
} on fail rollback
'''
    interp.source_code = source
    try:
        interp.interpret(parse(source))
    except IntegrateIsolationViolation:
        pass
    else:
        assert False, "expected IntegrateIsolationViolation"
    assert interp.global_env.get("x") == 1
    assert interp.last_integrate_write_set is None
    event = [e for e in interp.execution_history if e.get("type") == "integrate_aborted"][-1]
    assert event["schema_version"] == "alpha3g.integrate.v1"
    assert event["abort_reason"] == "barrier_violation"
    assert event["barrier_op"] == "random"
    assert event["exception_type"] == "IntegrateIsolationViolation"
    assert "Traceback" not in str(event.get("message"))
    assert event["overlay_summary"]["dirty_count"] == 1
    assert event["overlay_summary"]["dirty_paths"][0]["path"] == "/env/x"


def test_legacy_integrate_default_path_unchanged():
    interp = Interpreter()
    source = '''
let x = 1
integrate x {
    x = 2
} on fail rollback
'''
    interp.source_code = source
    interp.interpret(parse(source))
    assert any(e.get("type") == "integrate_committed" for e in interp.execution_history)
    # Legacy path still uses state_diff rather than Alpha3g write_set schema.
    assert any("state_diff" in e for e in interp.execution_history if e.get("type") == "integrate_committed")
