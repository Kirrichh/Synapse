import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from synapse import Lexer, Parser, Interpreter, PolicyViolationException, PolicyCompilationError
from synapse.ast import PolicyDef, RejectStmt
from synapse.interpreter import RuntimeMode


def parse(src):
    return Parser(Lexer(src).scan_tokens()).parse()


def test_guard_parsing():
    ast = parse('''
policy SafetyGov {
    target "Worker.process"
    guard (args) {
        let unsafe = args[0].contains("unsafe")
        if unsafe {
            reject "blocked"
        }
    }
}
''')
    policy = ast.statements[0]
    assert isinstance(policy, PolicyDef)
    assert policy.guard_params == ["args"]
    assert len(policy.guard_body) == 2


def test_guard_blocks_send_and_records_atomic_violation():
    src = '''
policy SafetyGov {
    target "Worker.process"
    guard (args) {
        if args[0].contains("unsafe") {
            reject "semantic block"
        }
    }
}

agent Worker {
    model "mock"
}

send Worker.process("unsafe request")
'''
    i = Interpreter()
    try:
        i.interpret(parse(src))
        assert False, "expected policy violation"
    except PolicyViolationException as exc:
        assert "semantic block" in str(exc)
    assert [e["type"] for e in i.execution_history] == ["policy_violation"]
    assert i.mailboxes.get("Worker") in (None, [])


def test_guard_pass_is_atomic_and_replay_safe_after_policy_evolution():
    src_v1 = '''
policy SafetyGov {
    target "Worker.process"
    guard (args) {
        let analysis = llm(prompt "old guard prompt")
        if args[0].contains("unsafe") {
            reject "blocked"
        }
    }
}
agent Worker { model "mock" }
send Worker.process("safe job")
let self = Worker
receive {
    sender => msg {
        print(msg.payload)
    }
}
'''
    i1 = Interpreter()
    i1.interpret(parse(src_v1))
    assert i1.get_output().strip() == "safe job"
    types = [e["type"] for e in i1.execution_history]
    assert types == ["policy_evaluated", "message_sent", "message_received"]
    # Internal guard llm() must not contaminate main workflow history.
    assert "llm_call" not in types

    src_v2 = src_v1.replace('old guard prompt', 'new improved guard prompt')
    i2 = Interpreter()
    i2.load_snapshot(i1.snapshot())
    i2.interpret(parse(src_v2))
    assert i2.get_output().strip() == "safe job"
    assert i2.replay_cursor >= 3


def test_guard_context_rejects_state_mutation():
    src = '''
policy MutatingPolicy {
    target "Worker.process"
    guard (args) {
        memory.write("hidden side effect")
    }
}
agent Worker { model "mock" }
send Worker.process("job")
'''
    i = Interpreter()
    try:
        i.interpret(parse(src))
        assert False, "expected policy compilation error"
    except PolicyCompilationError:
        pass


if __name__ == "__main__":
    test_guard_parsing()
    test_guard_blocks_send_and_records_atomic_violation()
    test_guard_pass_is_atomic_and_replay_safe_after_policy_evolution()
    test_guard_context_rejects_state_mutation()
    print("All semantic guardrail tests passed!")
