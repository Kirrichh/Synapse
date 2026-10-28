"""Mutation evidence must reference current code and existing acceptance cases."""
import ast
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_report_references_executable_mutants_and_existing_tests():
    report = json.loads((ROOT / 'reports/pr112_targeted_mutations.json').read_text())
    assert_snapshot_matches(ROOT, report)
    assert_transforms_match(report)
    assert_selector_campaign(report)
    campaign = report['selector_campaign']
    assert_snapshot_matches(ROOT, campaign)
    catalog = json.loads((ROOT / 'acceptance/ci/mutations.json').read_text())
    assert [seed['id'] for seed in catalog] == [item['id'] for item in campaign['mutations']]
    assert 'acceptance/ci/mutations.json' in campaign['snapshot_files']
    for seed in catalog:
        assert seed['target'] in campaign['snapshot_files']
        source = (ROOT / seed['target']).read_text()
        assert source.count(seed['before']) == 1 and seed['before'] != seed['after']
        ast.parse(source.replace(seed['before'], seed['after'], 1))
    for mutant in report['manual_mutants']:
        source = (ROOT / mutant['source']).read_text()
        assert mutant['source'] in report['snapshot_files']
        assert mutant['detecting_test'].split('::')[0] in report['snapshot_files']
        assert mutant['original'] != mutant['mutant']
        changed = source.replace(mutant['original'], mutant['mutant'], 1)
        if mutant['source'].endswith('.py'):
            ast.parse(changed)
        elif mutant['source'].endswith(('.yaml', '.yml')):
            import yaml
            assert isinstance(yaml.safe_load(changed), dict)
        else:
            raise AssertionError('unsupported mutation source')
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
    assert report['provenance'] == 'sha256-file-snapshot'
    assert report['snapshot_files']
    assert all(mutant['result'] == 'killed' for mutant in report['manual_mutants'])


def assert_snapshot_matches(root, report):
    for name, digest in report['snapshot_files'].items():
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        target = root / path
        assert target.is_file() and not target.is_symlink()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, name


def test_source_changes_outside_the_mutation_span_invalidate_evidence(tmp_path):
    source = tmp_path / 'module.py'
    source.write_text('VALUE = 1\nOTHER = 2\n')
    report = {'manual_mutants': [{'source': 'module.py', 'original': 'VALUE = 1'}],
              'snapshot_files': {'module.py': hashlib.sha256(source.read_bytes()).hexdigest()}}
    assert_snapshot_matches(tmp_path, report)
    source.write_text('VALUE = 1\nOTHER = 3\n')
    try:
        assert_snapshot_matches(tmp_path, report)
    except AssertionError:
        return
    raise AssertionError('changed source still accepted as the tested snapshot')


def transform_digest(mutants):
    keys = ('id', 'source', 'original', 'mutant', 'detecting_test')
    descriptions = [{key: mutant[key] for key in keys} for mutant in mutants]
    raw = json.dumps(descriptions, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


def assert_transforms_match(report):
    assert report['transform_sha256'] == transform_digest(report['manual_mutants'])


def test_changed_mutation_description_is_not_the_recorded_experiment():
    report = {'manual_mutants': [{'id': 'm', 'source': 'module.py', 'original': 'a',
                                  'mutant': 'b', 'detecting_test': 'test_module.py::test_a'}],
              'transform_sha256': ''}
    report['transform_sha256'] = transform_digest(report['manual_mutants'])
    assert_transforms_match(report)
    report['manual_mutants'][0]['mutant'] = 'c'
    try:
        assert_transforms_match(report)
    except AssertionError:
        return
    raise AssertionError('changed mutation description was accepted')


def assert_selector_campaign(report):
    mutations = report['selector_campaign']['mutations']
    assert mutations
    for mutation in mutations:
        assert mutation['result'] == 'KILLED'
        assert mutation['failed_cases'] and mutation['failure_evidence']


def test_selector_campaign_cannot_claim_a_kill_without_a_failed_case():
    campaign = {'mutations': [{'id': 'one', 'result': 'KILLED', 'failed_cases': [], 'failure_evidence': []}]}
    try:
        assert_selector_campaign({'selector_campaign': campaign})
    except AssertionError:
        return
    raise AssertionError('selector kill had no demonstrated failure')
