"""
Deterministic replay and consequence governance tests for Synapse v0.4.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse import compile_to_ast
from synapse.interpreter import Interpreter, Suspension, PolicyViolationException, RuntimeMode
from synapse.lexer import Lexer, TokenType


def drain(flow, send_value=None):
    try:
        if send_value is None:
            return next(flow)
        return flow.send(send_value)
    except StopIteration:
        return None


def test_policy_target_tokens():
    tokens = Lexer('policy Guard { target "Worker.process" forbid "nuclear-launch" }').scan_tokens()
    types = [t.type for t in tokens]
    assert TokenType.POLICY in types
    assert TokenType.TARGET in types
    assert TokenType.FORBID in types
    print("OK: Policy target tokens")


def test_policy_guard_blocks_send():
    source = '''policy FinancialControl {
    target "Worker.process"
    forbid "nuclear-launch"
}
agent Worker { model "mock" }
send Worker.process("nuclear-launch")'''
    ast = compile_to_ast(source)
    interpreter = Interpreter()
    try:
        interpreter.interpret(ast)
    except PolicyViolationException as e:
        assert "Worker.process" in str(e)
        assert interpreter.execution_history[-1]["type"] == "policy_violation"
        print("OK: Policy guard blocks send")
        return
    raise AssertionError("PolicyViolationException was not raised")


def test_deterministic_llm_replay_cross_process():
    source = '''let p = prompt "durable question"
let answer = llm(p)
print(answer)'''
    ast = compile_to_ast(source)

    interpreter_a = Interpreter()
    flow_a = interpreter_a.interpret_async(ast)
    status = drain(flow_a)
    assert isinstance(status, Suspension)
    assert status.reason == "awaiting_llm"
    drain(flow_a, "stored answer")
    assert "stored answer" in interpreter_a.get_output()
    snapshot = interpreter_a.snapshot()
    assert snapshot["execution_history"][0]["type"] == "llm_call"

    interpreter_b = Interpreter()
    interpreter_b.load_snapshot(snapshot)
    assert interpreter_b.runtime_mode == RuntimeMode.REPLAY
    flow_b = interpreter_b.interpret_async(ast)
    result = drain(flow_b)
    assert result is None
    assert "stored answer" in interpreter_b.get_output()
    assert interpreter_b.runtime_mode == RuntimeMode.REPLAY or interpreter_b.replay_cursor == len(interpreter_b.execution_history)
    print("OK: Deterministic LLM replay cross-process")


def test_deterministic_receive_replay_cross_process():
    source = '''agent Inbox { model "mock" }
let self = Inbox
receive { sender => msg { print(msg.payload) } }'''
    ast = compile_to_ast(source)

    interpreter_a = Interpreter()
    interpreter_a.mailboxes["Inbox"] = [{"sender": "tester", "receiver": "Inbox", "method": "approve", "args": ["approved"], "payload": "approved"}]
    flow_a = interpreter_a.interpret_async(ast)
    drain(flow_a)
    assert "approved" in interpreter_a.get_output()
    snapshot = interpreter_a.snapshot()
    assert any(e.get("type") == "message_received" for e in snapshot["execution_history"])

    interpreter_b = Interpreter()
    interpreter_b.load_snapshot(snapshot)
    # Clear mailboxes to prove receive comes from history, not from live mailbox.
    interpreter_b.mailboxes = {"global": []}
    flow_b = interpreter_b.interpret_async(ast)
    drain(flow_b)
    assert "approved" in interpreter_b.get_output()
    print("OK: Deterministic receive replay cross-process")


if __name__ == "__main__":
    test_policy_target_tokens()
    test_policy_guard_blocks_send()
    test_deterministic_llm_replay_cross_process()
    test_deterministic_receive_replay_cross_process()
    print("All replay/governance tests passed!")
