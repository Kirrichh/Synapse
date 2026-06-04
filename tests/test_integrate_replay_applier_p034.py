import copy
import pytest

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, IntegrateIsolationViolation, ReplayIntegrityError, RuntimeMode


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run_live(source: str) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def run_replay(source: str, history) -> Interpreter:
    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = source
    replay.interpret(parse(source))
    return replay


def test_integrate_replay_committed_applies_write_set_without_body_execution():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    replay = run_replay('''
let x = 1
integrate x {
    x = 999
    print("body must not execute")
} on fail rollback
''', live.execution_history)

    assert replay.global_env.get("x") == 2
    assert replay.replay_cursor == len(replay.execution_history)
    assert replay.last_integrate_write_set.to_list()[0]["new_value"] == 2


def test_integrate_replay_pre_state_hash_mismatch_fails():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    with pytest.raises(ReplayIntegrityError, match="pre_state_hash mismatch"):
        run_replay('''
let x = 7
integrate x {
    x = 999
} on fail rollback
''', live.execution_history)


def test_integrate_replay_write_set_hash_mismatch_fails():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    history = copy.deepcopy(live.execution_history)
    event = next(e for e in history if e.get("type") == "integrate_committed")
    event["write_set"][0]["new_value"] = 3
    with pytest.raises(ReplayIntegrityError, match="write_set_hash mismatch"):
        run_replay('''
let x = 1
integrate x {
    x = 999
} on fail rollback
''', history)


def test_integrate_replay_post_state_hash_mismatch_fails():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    history = copy.deepcopy(live.execution_history)
    event = next(e for e in history if e.get("type") == "integrate_committed")
    event["post_state_hash"] = "sha256:bad"
    with pytest.raises(ReplayIntegrityError, match="post_state_hash mismatch"):
        run_replay('''
let x = 1
integrate x {
    x = 999
} on fail rollback
''', history)


def test_integrate_replay_aborted_consumes_event_without_body_execution():
    live = Interpreter()
    live.integrate_i2_skeleton_enabled = True
    source = '''
let x = 1
integrate x {
    x = 2
    random()
} on fail rollback
'''
    live.source_code = source
    with pytest.raises(IntegrateIsolationViolation):
        live.interpret(parse(source))
    assert live.global_env.get("x") == 1

    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay_source = '''
let x = 1
integrate x {
    x = 999
    print("body must not execute")
} on fail rollback
'''
    replay.source_code = replay_source
    with pytest.raises(IntegrateIsolationViolation):
        replay.interpret(parse(replay_source))
    assert replay.global_env.get("x") == 1
    assert replay.replay_cursor == len(replay.execution_history)


def test_integrate_replay_event_already_applied_guard():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')
    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = '''
let x = 1
integrate x {
    x = 999
} on fail rollback
'''
    replay.interpret(parse(replay.source_code))
    replay.replay_cursor = 0
    replay.runtime_mode = RuntimeMode.REPLAY
    with pytest.raises(ReplayIntegrityError, match="already applied"):
        replay.interpret(parse(replay.source_code))


def test_legacy_integrate_default_replay_path_unchanged():
    live = Interpreter()
    source = '''
let x = 1
integrate x {
    x = 2
} on fail rollback
'''
    live.source_code = source
    live.interpret(parse(source))
    replay = Interpreter()
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = source
    replay.interpret(parse(source))
    assert any(e.get("type") == "integrate_committed" for e in replay.execution_history)
