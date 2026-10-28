"""Mutation evidence must describe the published snapshot it was measured on.

The report is a historical record. Its files are read from the recorded commit,
never from the working tree, so later work on the guarded sources does not
invalidate the record, and a changed record cannot borrow today's sources.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def committed_reader(root, commit):
    assert re.fullmatch(r'[0-9a-f]{40}', commit), 'tested_commit must be a full immutable SHA-1'
    available = subprocess.run(['git', 'cat-file', '-e', commit + '^{commit}'], cwd=root, capture_output=True)
    assert available.returncode == 0, f'tested commit {commit} is unavailable; fetch the full history first'

    def read(name):
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts, name
        entry = subprocess.check_output(['git', 'ls-tree', commit, '--', name], cwd=root, text=True).split()
        assert entry and entry[1] == 'blob' and entry[0] in ('100644', '100755'), name
        return subprocess.check_output(['git', 'cat-file', 'blob', entry[2]], cwd=root)
    return read


def test_report_references_executable_mutants_and_existing_tests():
    report = json.loads((ROOT / 'reports/pr112_targeted_mutations.json').read_text())
    assert report['provenance'] == 'git-commit-snapshot'
    read = committed_reader(ROOT, report['tested_commit'])
    assert_snapshot_matches(read, report)
    assert_transforms_match(report)
    assert_selector_campaign(report)
    campaign = report['selector_campaign']
    assert_snapshot_matches(read, campaign)
    catalog = json.loads(read('acceptance/ci/mutations.json'))
    assert [seed['id'] for seed in catalog] == [item['id'] for item in campaign['mutations']]
    assert 'acceptance/ci/mutations.json' in campaign['snapshot_files']
    for seed in catalog:
        assert seed['target'] in campaign['snapshot_files']
        source = read(seed['target']).decode('utf-8')
        assert source.count(seed['before']) == 1 and seed['before'] != seed['after']
        ast.parse(source.replace(seed['before'], seed['after'], 1))
    for mutant in report['manual_mutants']:
        source = read(mutant['source']).decode('utf-8')
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
        if name:
            tree = ast.parse(read(path).decode('utf-8'))
            functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name.split('[', 1)[0] in functions, mutant['id']
    assert any(item['id'] == 'canonical-failure-fallback' and item['reason']
               for item in report['excluded_mutants'])
    assert report['snapshot_files']
    assert all(mutant['result'] == 'killed' for mutant in report['manual_mutants'])


def assert_snapshot_matches(read, report):
    for name, digest in report['snapshot_files'].items():
        assert hashlib.sha256(read(name)).hexdigest() == digest, name


def commit_file(repository, name, content):
    (repository / name).write_text(content)
    subprocess.run(['git', 'add', name], cwd=repository, check=True)
    subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'commit', '-qm', name],
                   cwd=repository, check=True)
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()


def test_evidence_is_read_from_its_commit_not_from_later_work(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    tested = commit_file(tmp_path, 'module.py', 'VALUE = 1\nOTHER = 2\n')
    report = {'snapshot_files': {'module.py': hashlib.sha256(b'VALUE = 1\nOTHER = 2\n').hexdigest()}}
    commit_file(tmp_path, 'module.py', 'VALUE = 1\nOTHER = 3\n')
    (tmp_path / 'module.py').write_text('uncommitted later edit\n')
    assert_snapshot_matches(committed_reader(tmp_path, tested), report)


def test_record_that_differs_from_its_commit_is_rejected(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    tested = commit_file(tmp_path, 'module.py', 'VALUE = 1\nOTHER = 2\n')
    later = commit_file(tmp_path, 'module.py', 'VALUE = 1\nOTHER = 3\n')
    report = {'snapshot_files': {'module.py': hashlib.sha256(b'VALUE = 1\nOTHER = 3\n').hexdigest()}}
    try:
        assert_snapshot_matches(committed_reader(tmp_path, tested), report)
    except AssertionError:
        assert_snapshot_matches(committed_reader(tmp_path, later), report)
        return
    raise AssertionError('a record of other sources was accepted as the tested snapshot')


def test_unavailable_or_abbreviated_commit_is_refused(tmp_path):
    subprocess.run(['git', 'init', '-q'], cwd=tmp_path, check=True)
    tested = commit_file(tmp_path, 'module.py', 'VALUE = 1\n')
    for commit in (tested[:12], '0' * 40):
        try:
            committed_reader(tmp_path, commit)
        except AssertionError:
            continue
        raise AssertionError('an unverifiable commit was accepted: ' + commit)



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
