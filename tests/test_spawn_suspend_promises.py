import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.lexer import Lexer, TokenType
from synapse.parser import Parser
from synapse.ast import SpawnExpr, AwaitExpr, SendStmt, SuspendExpr
from synapse.interpreter import Interpreter, Suspension, RuntimeMode


def compile_to_ast(src):
    return Parser(Lexer(src).scan_tokens()).parse()


def run_async_until_status(source, interpreter=None):
    ast = compile_to_ast(source)
    interpreter = interpreter or Interpreter()
    interpreter.source_code = source
    flow = interpreter.interpret_async(ast)
    try:
        status = next(flow)
    except StopIteration as done:
        return interpreter, None, done.value
    return interpreter, flow, status


def test_tokens():
    tokens = Lexer('let p = spawn Analyst()\nlet x = suspend approval("ok")\nlet y = await p.get_response()\nasync').scan_tokens()
    types = [t.type for t in tokens]
    assert TokenType.SPAWN in types
    assert TokenType.SUSPEND in types
    assert TokenType.AWAIT in types
    assert TokenType.ASYNC in types


def test_parse_spawn_await_suspend_async_send():
    ast = compile_to_ast('''
agent Analyst { model "mock" }
let p = spawn Analyst()
p => queue_task("job-1")
let approved = suspend await_human_approval("plan")
let result = await p.get_response()
''')
    assert isinstance(ast.statements[1].value, SpawnExpr)
    assert isinstance(ast.statements[2], SendStmt)
    assert ast.statements[2].async_send is True
    assert isinstance(ast.statements[3].value, SuspendExpr)
    assert isinstance(ast.statements[4].value, AwaitExpr)


def test_spawn_actor_ref_and_async_send_fifo_mailbox():
    source = '''
agent Analyst { model "mock" }
let p = spawn Analyst()
p => queue_task("job-1")
'''
    interp = Interpreter()
    interp.interpret(compile_to_ast(source))
    refs = list(interp.spawned_actors.keys())
    assert len(refs) == 1
    proc_id = refs[0]
    assert interp.mailboxes[proc_id][0]["method"] == "queue_task"
    assert interp.mailboxes[proc_id][0]["payload"] == "job-1"
    assert any(e.get("type") == "actor_spawned" for e in interp.execution_history)


def test_suspend_external_signal_resume_and_history():
    source = '''
let plan = "deploy-plan"
let approved = suspend await_human_approval(plan)
print(approved)
'''
    interp, flow, status = run_async_until_status(source)
    assert isinstance(status, Suspension)
    assert status.reason == "awaiting_external_signal"
    promise_id = status.payload["promise_id"]
    try:
        flow.send(True)
    except StopIteration:
        pass
    assert interp.promises[promise_id]["status"] == "resolved"
    assert interp.get_output() == "True"
    assert any(e.get("type") == "promise_created" for e in interp.execution_history)
    assert any(e.get("type") == "promise_resolved" for e in interp.execution_history)


def test_mobility_envelope_contains_durable_process_state():
    source = '''
agent Analyst { model "mock" }
let p = spawn Analyst()
p => queue_task("job-1")
'''
    interp = Interpreter()
    interp.interpret(compile_to_ast(source))
    envelope = interp.dump_state(source_code=source, actor_name="Analyst")
    runtime = envelope["runtime"]
    assert runtime["spawned_actors"]
    assert "promises" in runtime
    assert "llm_context_cache" in runtime


def test_replay_spawn_uses_same_process_id():
    source = '''
agent Analyst { model "mock" }
let p = spawn Analyst()
p => queue_task("job-1")
'''
    ast = compile_to_ast(source)
    a = Interpreter()
    a.interpret(ast)
    original_proc = next(iter(a.spawned_actors.keys()))
    snap = a.snapshot()
    b = Interpreter()
    b.load_snapshot(snap)
    b.interpret(ast)
    replayed_proc = next(iter(b.spawned_actors.keys()))
    assert replayed_proc == original_proc


if __name__ == "__main__":
    test_tokens()
    test_parse_spawn_await_suspend_async_send()
    test_spawn_actor_ref_and_async_send_fifo_mailbox()
    test_suspend_external_signal_resume_and_history()
    test_mobility_envelope_contains_durable_process_state()
    test_replay_spawn_uses_same_process_id()
    print("All spawn/suspend/promise tests passed!")
