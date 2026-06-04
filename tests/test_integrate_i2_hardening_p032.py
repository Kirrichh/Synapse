import pytest

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import (
    Interpreter,
    IntegrateIsolationViolation,
    IntegrateOverlayEnvironment,
    NondeterminismBarrierViolation,
)
from synapse.state_overlay import StateOverlay, WriteSet, WriteSetEntry


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run_i2(source: str) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def expect_i2_violation(source: str) -> IntegrateIsolationViolation:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    with pytest.raises(IntegrateIsolationViolation) as exc_info:
        interp.interpret(parse(source))
    assert isinstance(exc_info.value, NondeterminismBarrierViolation)
    assert interp.last_integrate_write_set is None
    assert any(e.get("type") == "integrate_aborted" for e in interp.execution_history)
    return exc_info.value


def test_integrate_i2_parent_mutable_read_is_clone_on_first_read():
    interp = run_i2('''
let x = [1, 2]
integrate x {
    x.append(3)
} on fail rollback
''')
    assert interp.global_env.get("x") == [1, 2, 3]
    assert interp.last_integrate_write_set.to_list()[0]["path"] == "/env/x"
    assert interp.last_integrate_write_set.to_list()[0]["new_value"] == [1, 2, 3]


def test_integrate_overlay_environment_preserves_aliasing_inside_transaction():
    interp = Interpreter()
    interp.global_env.define("user", {"name": "Bob"})
    overlay = StateOverlay({"env": interp.flatten_env_variables(interp.global_env), "memory": {}})
    tx_env = IntegrateOverlayEnvironment(interp.global_env, overlay)

    first = tx_env.get("user")
    second = tx_env.get("user")
    first["name"] = "Ada"

    assert second["name"] == "Ada"
    assert interp.global_env.get("user") == {"name": "Bob"}
    write_set = overlay.commit()
    assert write_set.to_list()[0]["new_value"] == {"name": "Ada"}


def test_integrate_i2_print_is_blocked_without_host_stdout(capsys):
    violation = expect_i2_violation('''
let x = 1
integrate x {
    print("should-not-print")
} on fail rollback
''')
    captured = capsys.readouterr()
    assert "print" in str(violation)
    assert "should-not-print" not in captured.out


def test_integrate_i2_nested_integrate_is_blocked_and_no_writeset_leaks():
    violation = expect_i2_violation('''
let x = 1
integrate x {
    integrate x {
        x = 2
    } on fail rollback
} on fail rollback
''')
    assert "nested integrate" in str(violation)


def test_integrate_i2_spawn_is_blocked_before_actor_or_promise_creation():
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    source = '''
agent Analyst { model "mock" }
let x = 1
integrate x {
    let p = spawn Analyst()
} on fail rollback
'''
    interp.source_code = source
    with pytest.raises(IntegrateIsolationViolation) as exc_info:
        interp.interpret(parse(source))
    assert "spawn" in str(exc_info.value)
    assert interp.last_integrate_write_set is None
    assert interp.spawned_actors == {}
    assert not any(event.get("type") == "actor_spawned" for event in interp.execution_history)


def test_integrate_i2_ordinary_exception_discards_overlay_and_writeset():
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    source = '''
let x = 1
integrate x {
    x = 2
    undefined_call()
} on fail rollback
'''
    interp.source_code = source
    with pytest.raises(Exception):
        interp.interpret(parse(source))
    assert interp.global_env.get("x") == 1
    assert interp.last_integrate_write_set is None
    assert any(e.get("type") == "integrate_aborted" for e in interp.execution_history)


def test_integrate_i2_noop_transaction_produces_empty_writeset():
    interp = run_i2('''
let x = 1
integrate x {
    x = 1
} on fail rollback
''')
    assert isinstance(interp.last_integrate_write_set, WriteSet)
    assert interp.last_integrate_write_set.entries == ()


def test_writeset_rejects_unsorted_entries():
    first = WriteSetEntry(path="/env/b", granularity="top_level", op="replace", old_value_hash=None, new_value=1, new_value_hash="sha256:b")
    second = WriteSetEntry(path="/env/a", granularity="top_level", op="replace", old_value_hash=None, new_value=1, new_value_hash="sha256:a")
    with pytest.raises(ValueError):
        WriteSet((first, second))
