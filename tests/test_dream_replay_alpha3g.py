import copy

import pytest

from synapse import Lexer, Parser, Interpreter, RuntimeMode, LLMBackend, ReplayIntegrityError, run


def compile_ast(source: str):
    return Parser(Lexer(source).scan_tokens()).parse()


class FailingLLMBackend(LLMBackend):
    def complete(self, *args, **kwargs):  # pragma: no cover - failure path only
        raise AssertionError("LLM provider must not be called during dream replay")


def dream_event(history):
    events = [event for event in history if event.get("type") == "dream_completed"]
    assert len(events) == 1
    return events[0]


def test_live_dream_completed_records_key_result_hash_and_policy():
    source = '''
let captured = "alpha"
let result = dream {
  scenario "alpha simulation"
  return captured + "-result"
}
print(result)
'''
    interp = Interpreter()
    run(source, interp)

    event = dream_event(interp.execution_history)
    assert event["nested_event_policy"] == "execute_and_verify"
    assert event["result"] == "alpha-result"
    assert len(event["result_hash"]) == 64
    assert set(event["dream_key"]) == {
        "scenario_hash",
        "config_hash",
        "body_hash",
        "bound_variables_hash",
        "parent_history_hash",
        "runtime_version",
    }


def test_dream_replay_consumes_nested_llm_and_returns_recorded_result():
    source = '''
let prompt_text = "hello from dream"
let result = dream {
  scenario "llm simulation"
  return llm prompt_text
}
print(result)
'''
    live = Interpreter()
    run(source, live)
    assert [event.get("type") for event in live.execution_history] == ["llm_call", "dream_completed"]

    replay = Interpreter()
    replay.execution_history = copy.deepcopy(live.execution_history)
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0
    replay.llm_backend = FailingLLMBackend()
    run(source, replay)

    assert replay.get_output() == live.get_output()
    assert replay.replay_cursor == len(replay.execution_history)


def test_dream_replay_rejects_result_hash_mismatch():
    source = '''
let result = dream {
  scenario "integrity"
  return "stable"
}
print(result)
'''
    live = Interpreter()
    run(source, live)
    history = copy.deepcopy(live.execution_history)
    dream_event(history)["result_hash"] = "0" * 64

    replay = Interpreter()
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0

    with pytest.raises(ReplayIntegrityError, match="result_hash"):
        run(source, replay)


def test_bound_variables_hash_changes_when_captured_value_changes():
    source_a = '''
let captured = "alpha"
let result = dream {
  scenario "same scenario"
  return captured
}
'''
    source_b = source_a.replace('"alpha"', '"beta"')

    interp_a = Interpreter()
    interp_b = Interpreter()
    run(source_a, interp_a)
    run(source_b, interp_b)

    key_a = dream_event(interp_a.execution_history)["dream_key"]
    key_b = dream_event(interp_b.execution_history)["dream_key"]
    assert key_a["bound_variables_hash"] != key_b["bound_variables_hash"]
    assert key_a["scenario_hash"] == key_b["scenario_hash"]
    assert key_a["body_hash"] == key_b["body_hash"]


def test_dream_replay_rejects_dream_key_mismatch():
    source = '''
let captured = "alpha"
let result = dream {
  scenario "key mismatch"
  return captured
}
'''
    live = Interpreter()
    run(source, live)
    history = copy.deepcopy(live.execution_history)
    dream_event(history)["dream_key"] = dict(dream_event(history)["dream_key"], bound_variables_hash="bad")

    replay = Interpreter()
    replay.execution_history = history
    replay.runtime_mode = RuntimeMode.REPLAY
    replay.replay_cursor = 0

    with pytest.raises(ReplayIntegrityError, match="dream_key"):
        run(source, replay)
