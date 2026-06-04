"""
Тесты лексера
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse.lexer import Lexer, TokenType

def test_basic_tokens():
    source = "let x = 42"
    lexer = Lexer(source)
    tokens = lexer.scan_tokens()
    types = [t.type for t in tokens]
    assert TokenType.LET in types
    assert TokenType.IDENTIFIER in types
    assert TokenType.NUMBER in types
    print("OK: Basic tokens")

def test_string():
    source = 'let msg = "hello world"'
    lexer = Lexer(source)
    tokens = lexer.scan_tokens()
    string_token = [t for t in tokens if t.type == TokenType.STRING][0]
    assert string_token.value == "hello world"
    print("OK: String token")

def test_keywords():
    source = "agent fn if else return model memory thought flow superpose policy verify claim consequence require forbid check evidence confidence"
    lexer = Lexer(source)
    tokens = lexer.scan_tokens()
    types = [t.type for t in tokens if t.type != TokenType.EOF]
    expected = [TokenType.AGENT, TokenType.FN, TokenType.IF, TokenType.ELSE,
                TokenType.RETURN, TokenType.MODEL, TokenType.MEMORY,
                TokenType.THOUGHT, TokenType.FLOW, TokenType.SUPERPOSE,
                TokenType.POLICY, TokenType.VERIFY, TokenType.CLAIM,
                TokenType.CONSEQUENCE, TokenType.REQUIRE, TokenType.FORBID,
                TokenType.CHECK, TokenType.EVIDENCE, TokenType.CONFIDENCE]
    assert types == expected
    print("OK: Keywords")

if __name__ == "__main__":
    test_basic_tokens()
    test_string()
    test_keywords()
    print("All lexer tests passed!")
