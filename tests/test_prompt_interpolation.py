"""Regression tests for prompt template interpolation in the tree-walker.

Bug: PromptExpr returned its raw template, so `prompt "X: {var}"` never
substituted environment variables (the CVM bridge path did interpolate via
_render_prompt, creating a divergence between execution paths).
"""
from synapse import Lexer, Parser, Interpreter


def _eval_prompt(body: str):
    source = f"""
fn main() {{
    {body}
    return p
}}
"""
    interp = Interpreter()
    interp.source_code = source
    interp.interpret(Parser(Lexer(source).scan_tokens()).parse())
    main_fn = interp.global_env.get_function("main")
    return interp.call_function(main_fn, [], interp.global_env)


def test_prompt_substitutes_env_variable():
    assert _eval_prompt('let topic = "quantum"\n    let p = prompt "About: {topic}"') == "About: quantum"

def test_prompt_keeps_unknown_placeholder():
    assert _eval_prompt('let p = prompt "Raw {not_defined} stays"') == "Raw {not_defined} stays"

def test_prompt_double_braces_escape():
    assert _eval_prompt('let x = "v"\n    let p = prompt "{{literal}} and {x}"') == "{literal} and v"

def test_prompt_non_string_value_coerced():
    assert _eval_prompt('let n = 42\n    let p = prompt "n={n}"') == "n=42"
