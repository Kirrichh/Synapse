import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from synapse import Lexer, Parser, Interpreter, compile_to_ast, run_until_suspension
from synapse.lexer import TokenType
from synapse.ast import ReceiveBlock
from synapse.interpreter import RuntimeMode, Suspension


def parse(src):
    return Parser(Lexer(src).scan_tokens()).parse()


def test_receive_timeout_token_and_parser():
    src = '''receive timeout 30 {
    sender => msg { print(msg.payload) }
} else {
    print("timeout")
}'''
    tokens = Lexer(src).scan_tokens()
    assert TokenType.TIMEOUT in [t.type for t in tokens]
    ast = parse(src)
    rb = ast.statements[0]
    assert isinstance(rb, ReceiveBlock)
    assert rb.timeout.value == 30
    assert len(rb.else_body) == 1


def test_receive_timeout_executes_else_and_records_event():
    src = '''agent Inbox { model "mock" }
let self = Inbox
receive timeout 5 {
    sender => msg { print(msg.payload) }
} else {
    print("approval timeout")
}'''
    i = Interpreter()
    i.interpret(parse(src))
    assert i.get_output().strip() == "approval timeout"
    assert i.execution_history[-1]["type"] == "receive_timeout"
    assert i.execution_history[-1]["timeout"] == 5
    assert i.actor_log[-1]["type"] == "receive_timeout"


def test_receive_timeout_replay_is_deterministic():
    src = '''agent Inbox { model "mock" }
let self = Inbox
receive timeout 5 {
    sender => msg { print(msg.payload) }
} else {
    print("approval timeout")
}'''
    i1 = Interpreter()
    i1.interpret(parse(src))
    snapshot = i1.snapshot()

    i2 = Interpreter()
    i2.load_snapshot(snapshot)
    i2.interpret(parse(src))
    assert i2.get_output().strip() == "approval timeout"
    assert i2.replay_cursor >= 1


def test_mailbox_fifo_regression():
    src = '''agent Worker { model "mock" }
send Worker.process("first")
send Worker.process("second")
let self = Worker
receive { sender => msg { print(msg.payload) } }
receive { sender => msg { print(msg.payload) } }'''
    i = Interpreter()
    i.interpret(parse(src))
    assert i.get_output().splitlines() == ["first", "second"]
    received = [e for e in i.execution_history if e.get("type") == "message_received"]
    assert [e["message"]["payload"] for e in received] == ["first", "second"]


def test_actor_log_records_delivery_order():
    src = '''agent Worker { model "mock" }
send Worker.process("alpha")
send Worker.process("beta")'''
    i = Interpreter()
    i.interpret(parse(src))
    assert [m["payload"] for m in i.actor_log] == ["alpha", "beta"]


def test_async_receive_timeout_suspension_and_resume_timeout():
    src = '''agent Inbox { model "mock" }
let self = Inbox
receive timeout 10 {
    sender => msg { print(msg.payload) }
} else {
    print("timed out")
}'''
    ast = compile_to_ast(src)
    i = Interpreter()
    flow = i.interpret_async(ast)
    status = next(flow)
    assert isinstance(status, Suspension)
    assert status.reason == "awaiting_message_or_timeout"
    assert status.payload["timeout"] == 10
    try:
        flow.send({"timeout": True})
    except StopIteration:
        pass
    assert i.get_output().strip() == "timed out"
    assert i.execution_history[-1]["type"] == "receive_timeout"


if __name__ == "__main__":
    test_receive_timeout_token_and_parser()
    test_receive_timeout_executes_else_and_records_event()
    test_receive_timeout_replay_is_deterministic()
    test_mailbox_fifo_regression()
    test_actor_log_records_delivery_order()
    test_async_receive_timeout_suspension_and_resume_timeout()
    print("All receive timeout/audit tests passed!")
