import copy

import pytest

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.interpreter import Interpreter, ReplayIntegrityError, RuntimeMode, IntegrateIsolationViolation
from synapse.state_overlay import ALPHA3G_LOCAL_JSON_PROFILE, STABLE_CANONICAL_PROFILE


def parse(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


def run_live(source: str, *, profile: str = ALPHA3G_LOCAL_JSON_PROFILE) -> Interpreter:
    interp = Interpreter()
    interp.integrate_i2_skeleton_enabled = True
    interp.integrate_hash_profile = profile
    interp.source_code = source
    interp.interpret(parse(source))
    return interp


def run_replay(source: str, history, *, profile: str = ALPHA3G_LOCAL_JSON_PROFILE) -> Interpreter:
    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    # The replay runner may keep the legacy default.  Event metadata must drive
    # verification for stable-canonical histories.
    replay.integrate_hash_profile = profile
    replay.execution_history = copy.deepcopy(history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.source_code = source
    replay.interpret(parse(source))
    return replay


def latest_event(interp: Interpreter, event_type: str):
    return [e for e in interp.execution_history if e.get("type") == event_type][-1]


def test_integrate_stable_profile_emits_explicit_hash_profile_and_value_profile():
    interp = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''', profile=STABLE_CANONICAL_PROFILE)

    event = latest_event(interp, "integrate_committed")
    assert event["hash_profile"] == STABLE_CANONICAL_PROFILE
    assert event["write_set"][0]["value_profile"] == STABLE_CANONICAL_PROFILE
    assert event["pre_state_hash"].startswith("sha256:")
    assert event["post_state_hash"].startswith("sha256:")
    assert event["write_set_hash"].startswith("sha256:")
    assert interp.global_env.get("x") == 2


def test_integrate_legacy_default_keeps_existing_event_shape_without_profile_fields():
    interp = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''')

    event = latest_event(interp, "integrate_committed")
    assert "hash_profile" not in event
    assert "value_profile" not in event["write_set"][0]


def test_integrate_stable_profile_replay_uses_recorded_event_profile_with_legacy_runner_default():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''', profile=STABLE_CANONICAL_PROFILE)

    replay = run_replay('''
let x = 1
integrate x {
    x = 999
    print("body must not execute")
} on fail rollback
''', live.execution_history)

    assert replay.global_env.get("x") == 2
    assert replay.replay_cursor == len(replay.execution_history)
    assert replay.last_integrate_write_set.to_list()[0]["value_profile"] == STABLE_CANONICAL_PROFILE


def test_integrate_stable_profile_aborted_event_has_profile_and_replays_abort():
    live = Interpreter()
    live.integrate_i2_skeleton_enabled = True
    live.integrate_hash_profile = STABLE_CANONICAL_PROFILE
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

    event = latest_event(live, "integrate_aborted")
    assert event["hash_profile"] == STABLE_CANONICAL_PROFILE
    assert event["overlay_summary"]["dirty_paths"][0]["path"] == "/env/x"

    replay = Interpreter()
    replay.integrate_i2_skeleton_enabled = True
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.source_code = source
    with pytest.raises(IntegrateIsolationViolation):
        replay.interpret(parse(source))
    assert replay.global_env.get("x") == 1


def test_integrate_stable_profile_replay_rejects_tampered_hash_profile():
    live = run_live('''
let x = 1
integrate x {
    x = 2
} on fail rollback
''', profile=STABLE_CANONICAL_PROFILE)
    history = copy.deepcopy(live.execution_history)
    event = latest_event(type("Holder", (), {"execution_history": history})(), "integrate_committed")
    event["hash_profile"] = "unknown.profile.v1"

    with pytest.raises(IntegrateIsolationViolation, match="unsupported integrate hash profile"):
        run_replay('''
let x = 1
integrate x {
    x = 999
} on fail rollback
''', history)


def test_integrate_stable_profile_hashing_fails_closed_for_host_objects():
    interp = Interpreter()
    interp.integrate_hash_profile = STABLE_CANONICAL_PROFILE

    with pytest.raises(Exception, match="unsupported"):
        interp.hash_integrate_payload({"x": object()})
