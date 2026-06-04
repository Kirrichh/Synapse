"""
Тесты парсера
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.lexer import Lexer
from synapse.parser import Parser
from synapse.ast import *

def test_let_statement():
    source = "let x = 42"
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    assert len(ast.statements) == 1
    assert isinstance(ast.statements[0], LetStmt)
    assert ast.statements[0].name == "x"
    print("OK: Let statement")

def test_agent_def():
    source = 'agent Bot { model "gpt-4" fn hello() { return 1 } }'
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    assert len(ast.statements) == 1
    assert isinstance(ast.statements[0], AgentDef)
    assert ast.statements[0].name == "Bot"
    print("OK: Agent definition")

def test_function_def():
    source = 'fn add(a, b) { return a + b }'
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    assert isinstance(ast.statements[0], FnDef)
    assert ast.statements[0].name == "add"
    assert ast.statements[0].params == ["a", "b"]
    print("OK: Function definition")

def test_if_statement():
    source = 'if x > 5 { let y = 10 } else { let y = 0 }'
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    assert isinstance(ast.statements[0], IfStmt)
    print("OK: If statement")


def test_governance_nodes():
    source = '''policy SafeAI { require "evidence" forbid "hidden state change" }
claim grounded { text "ok" evidence "test" confidence high }
consequence email { external_state_change true requires_confirmation true }
verify { check grounded.confidence == high }'''
    tokens = Lexer(source).scan_tokens()
    ast = Parser(tokens).parse()
    assert isinstance(ast.statements[0], PolicyDef)
    assert isinstance(ast.statements[1], ClaimDef)
    assert isinstance(ast.statements[2], ConsequenceDef)
    assert isinstance(ast.statements[3], VerifyBlock)
    print("OK: Governance nodes")

if __name__ == "__main__":
    test_let_statement()
    test_agent_def()
    test_function_def()
    test_if_statement()
    test_governance_nodes()
    print("All parser tests passed!")
