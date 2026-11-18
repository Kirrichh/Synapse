"""A benchmark must not label altered source files as a published commit."""
import io
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'acceptance/benchmarks/pr112'
ENTRYPOINTS = {
    'run_series.py': ['--native-addon', '{tmp}', '--replay-artifact', '{tmp}'],
    'remeasure_bytecode.py': ['--artifact', '{tmp}'],
}


def fresh_harness(tmp_path):
    # Imports elsewhere in a test session may leave caches beside the repository harness.
    copy = tmp_path / 'harness'
    shutil.copytree(HARNESS, copy, ignore=shutil.ignore_patterns('__pycache__'))
    return copy


def published_trees(tmp_path, *, alter_candidate):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', commit], cwd=ROOT)
    trees = []
    for name in ('base', 'candidate'):
        tree = tmp_path / name
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tree, filter='data')
        trees.append(tree)
    if alter_candidate:
        with (trees[1] / 'synapse/canonical_values.py').open('a') as file:
            file.write('\n# altered after archive\n')
    return commit, trees


def run_entrypoint(harness, entrypoint, trees, commit, tmp_path, environment=None):
    extra = [item.replace('{tmp}', str(tmp_path)) for item in ENTRYPOINTS[entrypoint]]
    return subprocess.run([sys.executable, '-B', str(harness / entrypoint), '--base', str(trees[0]),
        '--candidate', str(trees[1]), '--python', '/bin/false', *extra,
        '--output', str(tmp_path / 'result.json'), '--base-sha', commit, '--candidate-sha', commit],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize('entrypoint', sorted(ENTRYPOINTS))
def test_changed_candidate_is_rejected_before_running_cli(tmp_path, entrypoint):
    commit, trees = published_trees(tmp_path, alter_candidate=True)
    result = run_entrypoint(fresh_harness(tmp_path), entrypoint, trees, commit, tmp_path)
    assert result.returncode != 0
    assert 'source identity differs from commit' in result.stderr
    assert not (tmp_path / 'result.json').exists()


@pytest.mark.parametrize('entrypoint', sorted(ENTRYPOINTS))
def test_executable_cache_cannot_replace_the_provenance_checker(tmp_path, entrypoint):
    # A valid unchecked-hash cache is executed instead of its unchanged source,
    # even under -B. This one disables the source comparison.
    commit, trees = published_trees(tmp_path, alter_candidate=True)
    harness = fresh_harness(tmp_path)
    source = (harness / 'source_snapshot.py').read_text()
    forged = source.replace("raise ValueError('source identity differs from commit: ' + relative)", 'pass')
    assert forged != source
    staging = tmp_path / 'forged.py'
    staging.write_text(forged)
    py_compile.compile(str(staging), cfile=str(harness / '__pycache__' / f'source_snapshot.{sys.implementation.cache_tag}.pyc'),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH, doraise=True)
    result = run_entrypoint(harness, entrypoint, trees, commit, tmp_path)
    assert result.returncode != 0
    assert 'must run from sources without bytecode' in result.stderr
    assert '/bin/false' not in result.stderr
    assert not (tmp_path / 'result.json').exists()


@pytest.mark.parametrize('entrypoint', sorted(ENTRYPOINTS))
def test_external_bytecode_cache_location_is_refused(tmp_path, entrypoint):
    commit, trees = published_trees(tmp_path, alter_candidate=False)
    environment = {**os.environ, 'PYTHONPYCACHEPREFIX': str(tmp_path / 'pycache')}
    result = run_entrypoint(fresh_harness(tmp_path), entrypoint, trees, commit, tmp_path, environment)
    assert result.returncode != 0
    assert 'must run from sources without bytecode or a pycache prefix' in result.stderr
    assert not (tmp_path / 'result.json').exists()


def test_harness_identity_refuses_bytecode_and_detects_source_changes(tmp_path):
    sys.path.insert(0, str(HARNESS))
    try:
        from run_series import harness_digest
    finally:
        sys.path.remove(str(HARNESS))
    (tmp_path / 'case.py').write_text('VALUE = 1\n')
    clean = harness_digest(tmp_path)
    (tmp_path / 'case.py').write_text('VALUE = 2\n')
    assert harness_digest(tmp_path) != clean
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'case.cpython-312.pyc').write_bytes(b'cache')
    with pytest.raises(ValueError, match='harness contains bytecode'):
        harness_digest(tmp_path)


def test_snapshot_rechecks_modified_source_after_initial_verification(tmp_path):
    from acceptance.benchmarks.pr112.source_snapshot import verify_source_tree

    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', commit], cwd=ROOT)
    source = tmp_path / 'source'
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter='data')
    identity = verify_source_tree(source, ROOT, commit)
    assert identity['commit'] == commit
    with (source / 'synapse/canonical_values.py').open('a') as file:
        file.write('\n# modified during measurement\n')
    with pytest.raises(ValueError, match='source identity differs from commit'):
        verify_source_tree(source, ROOT, commit)
