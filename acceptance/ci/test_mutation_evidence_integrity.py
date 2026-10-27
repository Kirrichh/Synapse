"""Mutation evidence must reference current code and existing acceptance cases."""
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_report_references_executable_mutants_and_existing_tests():
    report = json.loads((ROOT / 'reports/pr112_targeted_mutations.json').read_text())
    for mutant in report['manual_mutants']:
        source = (ROOT / mutant['source']).read_text()
        assert source.count(mutant['original']) == 1, mutant['id']
        path, _, name = mutant['detecting_test'].partition('::')
        test = ROOT / path
        assert test.is_file(), mutant['id']
        if name:
            tree = ast.parse(test.read_text())
            functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name.split('[', 1)[0] in functions, mutant['id']
    assert any(item['id'] == 'canonical-failure-fallback' and item['reason']
               for item in report['excluded_mutants'])
    assert report['tested_commit']
