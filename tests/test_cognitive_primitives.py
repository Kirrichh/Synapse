from synapse import Lexer, Parser, Interpreter, run
from synapse.lexer import TokenType
from synapse.ast import DebateBlock, ReflectBlock, CallExpr, Variable


def compile_ast(source):
    return Parser(Lexer(source).scan_tokens()).parse()


def test_tokens():
    tokens = Lexer('debate reflect judge rounds last events filter data |> clean').scan_tokens()
    types = [t.type for t in tokens]
    assert TokenType.DEBATE in types
    assert TokenType.REFLECT in types
    assert TokenType.JUDGE in types
    assert TokenType.ROUNDS in types
    assert TokenType.PIPE in types


def test_pipeline_parser_desugars_to_calls():
    ast = compile_ast('let out = data |> clean |> analyze')
    expr = ast.statements[0].value
    assert isinstance(expr, CallExpr)
    assert isinstance(expr.callee, Variable)
    assert expr.callee.name == 'analyze'
    assert isinstance(expr.args[0], CallExpr)
    assert expr.args[0].callee.name == 'clean'


def test_debate_parser():
    ast = compile_ast('let x = debate { branch a { return "A" } branch b { return "B" } } judge "j" rounds 2')
    debate = ast.statements[0].value
    assert isinstance(debate, DebateBlock)
    assert len(debate.branches) == 2


def test_reflect_parser():
    ast = compile_ast('let h = reflect { last 5 events filter type == "llm_call" }')
    assert isinstance(ast.statements[0].value, ReflectBlock)


def test_pipeline_runtime():
    output = run('''
fn clean(x) { return x + " clean" }
fn analyze(x) { return x + " analyze" }
fn main() {
  let data = "raw"
  print(data |> clean |> analyze)
}
''')
    assert output.strip() == 'raw clean analyze'


def test_debate_runtime_round_and_history():
    source = '''
fn main() {
  let decision = debate {
    branch bull {
      if debate.round() == 1 {
        return llm "argue for expansion"
      } else {
        let opposing = debate.history("bear")
        return llm "reply to {opposing}"
      }
    }
    branch bear {
      return llm "argue against expansion"
    }
  } judge "neutral_arbiter" rounds 2
  print(decision.contains("neutral_arbiter"))
}
'''
    interp = Interpreter()
    run(source, interp)
    assert interp.get_output().strip() == 'True'
    assert any(e.get('type') == 'debate_completed' for e in interp.execution_history)
    assert len([e for e in interp.execution_history if e.get('type') == 'llm_call']) == 5


def test_reflect_filters_history():
    source = '''
fn main() {
  let a = llm "first call"
  let b = random()
  let h = reflect { last 10 events filter type == "llm_call" }
  print(h.length)
}
'''
    output = run(source)
    assert output.strip() == '1'


if __name__ == '__main__':
    test_tokens()
    test_pipeline_parser_desugars_to_calls()
    test_debate_parser()
    test_reflect_parser()
    test_pipeline_runtime()
    test_debate_runtime_round_and_history()
    test_reflect_filters_history()
    print('All cognitive primitive tests passed!')
