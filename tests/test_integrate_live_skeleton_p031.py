from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, IntegrateIsolationViolation
from synapse.state_overlay import WriteSet


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run_i2(source: str) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def test_integrate_i2_isolates_assignment_and_collects_draft_writeset():
    interp = run_i2('''
let x = 1
integrate x {
    x = 2
} on fail rollback
print(x)
''')
    assert interp.get_output() == "2"
    assert interp.global_env.get("x") == 2
    assert isinstance(interp.last_integrate_write_set, WriteSet)
    assert interp.last_integrate_write_set.to_list() == [
        {
            "path": "/env/x",
            "granularity": "top_level",
            "op": "replace",
            "old_value_hash": interp.last_integrate_write_set.entries[0].old_value_hash,
            "new_value": 2,
            "new_value_hash": interp.last_integrate_write_set.entries[0].new_value_hash,
        }
    ]
    assert any(e.get("type") == "integrate_committed" for e in interp.execution_history)


def test_integrate_i2_let_binding_enters_overlay_not_parent_env():
    interp = run_i2('''
let x = 1
integrate x {
    let y = 3
} on fail rollback
print(x)
''')
    assert interp.get_output() == "1"
    assert interp.global_env.get("y") == 3
    assert interp.last_integrate_write_set.to_list()[0]["path"] == "/env/y"
    assert any(e.get("type") == "integrate_committed" for e in interp.execution_history)


def test_integrate_i2_barrier_blocks_random():
    try:
        run_i2('''
let x = 1
integrate x {
    let r = random()
} on fail rollback
''')
    except IntegrateIsolationViolation as exc:
        assert "random" in str(exc)
        return
    assert False, "Expected IntegrateIsolationViolation for random() inside I2 integrate"


def test_integrate_i2_barrier_blocks_time():
    try:
        run_i2('''
let x = 1
integrate x {
    let t = time()
} on fail rollback
''')
    except IntegrateIsolationViolation as exc:
        assert "time" in str(exc)
        return
    assert False, "Expected IntegrateIsolationViolation for time() inside I2 integrate"


def test_integrate_i2_barrier_blocks_uuid():
    try:
        run_i2('''
let x = 1
integrate x {
    let u = uuid()
} on fail rollback
''')
    except IntegrateIsolationViolation as exc:
        assert "uuid" in str(exc)
        return
    assert False, "Expected IntegrateIsolationViolation for uuid() inside I2 integrate"


def test_integrate_i2_barrier_blocks_print_and_writes_aborted_event():
    try:
        run_i2('''
let x = 1
integrate x {
    print("blocked")
} on fail rollback
''')
    except IntegrateIsolationViolation as exc:
        assert "print" in str(exc)
        return
    assert False, "Expected IntegrateIsolationViolation for print() inside I2 integrate"


def test_legacy_integrate_path_still_emits_history_by_default():
    interp = Interpreter()
    source = '''
let x = 1
integrate x {
    assert true
} on fail rollback
'''
    interp.source_code = source
    interp.interpret(parse(source))
    assert any(e.get("type") == "integrate_committed" for e in interp.execution_history)
