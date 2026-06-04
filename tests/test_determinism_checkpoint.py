"""
Determinism drift and state checkpoint tests for Synapse v0.5.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse import compile_to_ast
from synapse.builtins import BUILTINS
from synapse.interpreter import Interpreter, Suspension, RuntimeMode


def drain(flow, send_value=None):
    try:
        if send_value is None:
            return next(flow)
        return flow.send(send_value)
    except StopIteration:
        return None


def test_random_replay_prevents_branch_drift():
    source = '''let chance = random()
if chance > 0.5 {
    let response = llm(prompt "Execute strategy A")
    print(response)
} else {
    print("Skip execution")
}'''
    ast = compile_to_ast(source)
    original_random = BUILTINS["random"]
    try:
        BUILTINS["random"] = lambda: 0.7
        interpreter_a = Interpreter()
        flow_a = interpreter_a.interpret_async(ast)
        status = drain(flow_a)
        assert isinstance(status, Suspension)
        assert status.reason == "awaiting_llm"
        drain(flow_a, "strategy A completed")
        assert "strategy A completed" in interpreter_a.get_output()
        assert interpreter_a.execution_history[0]["type"] == "side_effect"
        assert interpreter_a.execution_history[0]["name"] == "random"
        assert interpreter_a.execution_history[0]["result"] == 0.7

        # A live random() call would now choose the other branch, but replay must not.
        BUILTINS["random"] = lambda: 0.2
        interpreter_b = Interpreter()
        interpreter_b.load_snapshot(interpreter_a.snapshot())
        assert interpreter_b.runtime_mode == RuntimeMode.REPLAY
        flow_b = interpreter_b.interpret_async(ast)
        drain(flow_b)
        assert "strategy A completed" in interpreter_b.get_output()
        assert "Skip execution" not in interpreter_b.get_output()
        assert interpreter_b.replay_cursor == len(interpreter_b.execution_history)
        print("OK: Random replay prevents branch drift")
    finally:
        BUILTINS["random"] = original_random


def test_time_and_uuid_are_recorded_as_side_effects():
    source = '''let t = time()
let id = uuid()
print(str(t))
print(id)'''
    ast = compile_to_ast(source)
    original_time = BUILTINS["time"]
    original_uuid = BUILTINS["uuid"]
    try:
        BUILTINS["time"] = lambda: 123.456
        BUILTINS["uuid"] = lambda: "uuid-live-a"
        interpreter_a = Interpreter()
        interpreter_a.interpret(ast)
        snapshot = interpreter_a.snapshot()
        side_effects = [e for e in snapshot["execution_history"] if e.get("type") == "side_effect"]
        assert [e["name"] for e in side_effects] == ["time", "uuid"]

        BUILTINS["time"] = lambda: 999.0
        BUILTINS["uuid"] = lambda: "uuid-live-b"
        interpreter_b = Interpreter()
        interpreter_b.load_snapshot(snapshot)
        interpreter_b.interpret(ast)
        output = interpreter_b.get_output()
        assert "123.456" in output
        assert "uuid-live-a" in output
        assert "999.0" not in output
        assert "uuid-live-b" not in output
        print("OK: time/uuid side effects are replayed")
    finally:
        BUILTINS["time"] = original_time
        BUILTINS["uuid"] = original_uuid


def test_state_checkpoint_artifact():
    source = '''let chance = random()
print(str(chance))'''
    original_random = BUILTINS["random"]
    try:
        BUILTINS["random"] = lambda: 0.42
        interpreter = Interpreter()
        interpreter.interpret(compile_to_ast(source))
        checkpoint = interpreter.create_state_checkpoint("after-random")
        snapshot = interpreter.snapshot()
        assert checkpoint["type"] == "checkpoint"
        assert checkpoint["label"] == "after-random"
        assert checkpoint["history_offset"] == 1
        assert snapshot["checkpoints"][-1]["label"] == "after-random"
        assert snapshot["execution_history"][-1]["type"] == "checkpoint"
        print("OK: State checkpoint artifact")
    finally:
        BUILTINS["random"] = original_random


if __name__ == "__main__":
    test_random_replay_prevents_branch_drift()
    test_time_and_uuid_are_recorded_as_side_effects()
    test_state_checkpoint_artifact()
    print("All determinism/checkpoint tests passed!")
