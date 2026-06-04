from synapse.lexer import Lexer, TokenType
from synapse.parser import Parser
from synapse.interpreter import Interpreter, PolicyViolationException
from synapse.ast import IntentDef, DeclareIntentStmt, ObserveBlock, GovernedMemoryForget, LLMCall


def parse(src):
    return Parser(Lexer(src).scan_tokens()).parse()


def test_tokens():
    toks = Lexer('intent declare trust level observe on').scan_tokens()
    types = [t.type for t in toks]
    assert TokenType.INTENT in types
    assert TokenType.DECLARE in types
    assert TokenType.TRUST in types
    assert TokenType.LEVEL in types
    assert TokenType.OBSERVE in types
    assert TokenType.ON in types


def test_parse_intent_observe_forget_llm_sugar():
    ast = parse('''
intent send_payment {
    action "transfer funds"
    amount 5000
    target "external_account"
    reversible false
}

declare intent send_payment

observe Worker.process {
    on policy_violation => msg {
        print(msg.reason)
    }
}

agent A {
    model "mock"
    trust level high
    trust scope ["finance", "legal"]
}

fn main() {
    let x = llm "Analyze this"
    memory.forget("user_pii") {
        reason "GDPR deletion request"
        audit true
        irreversible true
    }
}
''')
    assert isinstance(ast.statements[0], IntentDef)
    assert isinstance(ast.statements[1], DeclareIntentStmt)
    assert isinstance(ast.statements[2], ObserveBlock)
    assert isinstance(ast.statements[-1].body[0].value, LLMCall)
    assert isinstance(ast.statements[-1].body[1].expr, GovernedMemoryForget)


def test_intent_policy_blocks_before_action():
    src = '''
intent send_payment {
    action "transfer funds"
    amount 5000
    target "external_account"
    reversible false
}

policy IntentControl {
    target "intent.send_payment"
    guard (args) {
        if args[0].amount > 1000 {
            reject "large payment requires approval"
        }
    }
}

fn main() {
    declare intent send_payment
    print("should not execute")
}
'''
    interp = Interpreter()
    try:
        interp.interpret(parse(src))
        assert False, 'expected policy violation'
    except PolicyViolationException:
        pass
    assert any(e.get('type') == 'policy_violation' for e in interp.execution_history)
    assert 'should not execute' not in interp.get_output()


def test_trust_guard_and_observe():
    src = '''
agent Validator {
    model "mock"
    trust level untrusted
    trust scope ["finance"]
}

agent Worker {
    model "mock"
}

observe Worker.process {
    on policy_violation => msg {
        print("observed " + msg.reason)
    }
}

policy DataProcessing {
    target "Worker.process"
    guard (args) {
        if source.trust == untrusted {
            reject "source is untrusted"
        }
    }
}

fn main() {
    let self = Validator
    send Worker.process("payload")
}
'''
    interp = Interpreter()
    try:
        interp.interpret(parse(src))
        assert False, 'expected policy violation'
    except PolicyViolationException:
        pass
    assert 'observed source is untrusted' in interp.get_output()


def test_governed_memory_forget_audit():
    src = '''
agent Keeper {
    model "mock"
}

fn main() {
    memory.write("user_pii") {
        reason "test setup"
        retention user_controlled
    }
    memory.forget("user_pii") {
        reason "GDPR deletion request"
        audit true
        irreversible true
    }
    print("forgot")
}
'''
    interp = Interpreter()
    interp.interpret(parse(src))
    assert 'forgot' in interp.get_output()
    assert any(e.get('type') == 'memory_forgotten' for e in interp.execution_history)


if __name__ == '__main__':
    test_tokens(); print('OK: tokens')
    test_parse_intent_observe_forget_llm_sugar(); print('OK: parse new governance')
    test_intent_policy_blocks_before_action(); print('OK: intent policy')
    test_trust_guard_and_observe(); print('OK: trust + observe')
    test_governed_memory_forget_audit(); print('OK: memory forget')
    print('All intent/trust/observe tests passed!')
