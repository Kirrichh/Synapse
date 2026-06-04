"""
Тесты интерпретатора
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synapse import run


def test_arithmetic():
    source = """let x = 2 + 3 * 4
print(x)"""
    output = run(source)
    assert "14" in output
    print("OK: Arithmetic")


def test_variables():
    source = """let a = 10
let b = 20
print(a + b)"""
    output = run(source)
    assert "30" in output
    print("OK: Variables")


def test_function():
    source = """fn double(x) { return x * 2 }
let result = double(5)
print(result)"""
    output = run(source)
    assert "10" in output
    print("OK: Function")


def test_auto_main():
    source = """fn main() {
    print("auto-main works")
}"""
    output = run(source)
    assert "auto-main works" in output
    print("OK: Auto main")


def test_if():
    source = """let x = 10
if x > 5 { print("big") } else { print("small") }"""
    output = run(source)
    assert "big" in output
    print("OK: If statement")


def test_list():
    source = """let nums = [1, 2, 3]
print(len(nums))"""
    output = run(source)
    assert "3" in output
    print("OK: List")


def test_loop():
    source = """let total = 0
for i in [1, 2, 3, 4, 5] { total = total + i }
print(total)"""
    output = run(source)
    assert "15" in output
    print("OK: Loop")


def test_llm_call():
    source = """let p = prompt "hello"
let result = llm(p)
print(result)"""
    output = run(source)
    assert len(output) > 0
    print("OK: LLM call")


def test_agent():
    source = """agent TestBot { model "mock" fn answer() { return 42 } }
let bot = TestBot()
let result = bot.answer()
print(result)"""
    output = run(source)
    assert "42" in output
    print("OK: Agent")


def test_policy_claim_verify_consequence():
    source = """policy SafeAI {
    require "critical claims need evidence"
    forbid "external state changes without confirmation"
}
claim safe_result {
    text "The answer is based on provided context"
    evidence "conversation"
    confidence high
}
consequence send_email {
    external_state_change true
    reversible false
    requires_confirmation true
}
verify {
    check safe_result.confidence == high, "claim confidence must be high"
    check send_email.requires_confirmation == true, "external actions require confirmation"
}
print("governance ok")"""
    output = run(source)
    assert "governance ok" in output
    print("OK: Policy/Claim/Verify/Consequence")


if __name__ == "__main__":
    test_arithmetic()
    test_variables()
    test_function()
    test_auto_main()
    test_if()
    test_list()
    test_loop()
    test_llm_call()
    test_agent()
    test_policy_claim_verify_consequence()
    print("All interpreter tests passed!")
