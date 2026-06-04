"""
Durable actor runtime tests for Synapse v0.3.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse import compile_to_ast, run
from synapse.lexer import Lexer, TokenType
from synapse.parser import Parser
from synapse.ast import SendStmt, ReceiveBlock
from synapse.interpreter import Interpreter, Suspension, Environment


def test_actor_tokens():
    tokens = Lexer('send Bot.work("x")\nreceive { sender => msg { print(msg.payload) } }').scan_tokens()
    token_types = [t.type for t in tokens]
    assert TokenType.SEND in token_types
    assert TokenType.RECEIVE in token_types
    assert TokenType.FATARROW in token_types
    print("OK: Actor tokens")


def test_actor_parser():
    ast = Parser(Lexer('send Bot.work("x")\nreceive { sender => msg { print(msg.payload) } }').scan_tokens()).parse()
    assert isinstance(ast.statements[0], SendStmt)
    assert isinstance(ast.statements[1], ReceiveBlock)
    print("OK: Actor parser")


def test_send_receive_runtime():
    source = '''agent Worker { model "mock" }
send Worker.process("job-42")
let self = Worker
receive {
    sender => msg {
        print(sender)
        print(msg.method)
        print(msg.payload)
    }
}'''
    output = run(source)
    assert "global" in output
    assert "process" in output
    assert "job-42" in output
    print("OK: Send/receive runtime")


def test_llm_suspension_and_snapshot():
    source = '''let p = prompt "hello durable runtime"
let answer = llm(p)
print(answer)'''
    ast = compile_to_ast(source)
    interpreter = Interpreter()
    flow = interpreter.interpret_async(ast)
    status = next(flow)
    assert isinstance(status, Suspension)
    assert status.reason == "awaiting_llm"
    snapshot = interpreter.snapshot(status)
    assert snapshot["suspension"]["reason"] == "awaiting_llm"
    restored = Interpreter.restore_snapshot(snapshot)
    assert isinstance(restored.global_env, Environment)
    try:
        flow.send("LLM answer after wake")
    except StopIteration:
        pass
    assert "LLM answer after wake" in interpreter.get_output()
    print("OK: LLM suspension and snapshot")


def test_receive_suspension_resume():
    source = '''agent Inbox { model "mock" }
let self = Inbox
receive { sender => msg { print(msg.payload) } }'''
    ast = compile_to_ast(source)
    interpreter = Interpreter()
    flow = interpreter.interpret_async(ast)
    status = next(flow)
    assert isinstance(status, Suspension)
    assert status.reason == "awaiting_message"
    try:
        flow.send({"sender": "tester", "receiver": "Inbox", "method": "approve", "args": ["approved"], "payload": "approved"})
    except StopIteration:
        pass
    assert "approved" in interpreter.get_output()
    print("OK: Receive suspension resume")


if __name__ == "__main__":
    test_actor_tokens()
    test_actor_parser()
    test_send_receive_runtime()
    test_llm_suspension_and_snapshot()
    test_receive_suspension_resume()
    print("All durable actor tests passed!")
