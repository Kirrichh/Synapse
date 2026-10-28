"""A benchmark must not label altered source files as a published commit."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


ENTRYPOINTS = {
    'run_series.py': ['--native-addon', '{tmp}', '--replay-artifact', '{tmp}'],
    'remeasure_bytecode.py': ['--artifact', '{tmp}'],
}


@pytest.mark.parametrize('entrypoint', sorted(ENTRYPOINTS))
def test_changed_candidate_is_rejected_before_running_cli(tmp_path, entrypoint):
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', commit], cwd=ROOT)
    trees = []
    for name in ('base', 'candidate'):
        tree = tmp_path / name
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tree, filter='data')
        trees.append(tree)
    with (trees[1] / 'synapse/canonical_values.py').open('a') as file:
        file.write('\n# altered after archive\n')
    runner = ROOT / 'acceptance/benchmarks/pr112' / entrypoint
    extra = [item.replace('{tmp}', str(tmp_path)) for item in ENTRYPOINTS[entrypoint]]
    result = subprocess.run([sys.executable, '-B', str(runner), '--base', str(trees[0]), '--candidate', str(trees[1]),
        '--python', '/bin/false', *extra,
        '--output', str(tmp_path / 'result.json'), '--base-sha', commit, '--candidate-sha', commit],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'source identity differs from commit' in result.stderr
    assert not (tmp_path / 'result.json').exists()


def test_harness_identity_ignores_interpreter_caches_but_not_sources(tmp_path):
    sys.path.insert(0, str(ROOT / 'acceptance/benchmarks/pr112'))
    try:
        from run_series import harness_digest
    finally:
        sys.path.remove(str(ROOT / 'acceptance/benchmarks/pr112'))
    (tmp_path / 'case.py').write_text('VALUE = 1\n')
    clean = harness_digest(tmp_path)
    (tmp_path / '__pycache__').mkdir()
    (tmp_path / '__pycache__' / 'case.cpython-312.pyc').write_bytes(b'cache')
    assert harness_digest(tmp_path) == clean
    (tmp_path / 'case.py').write_text('VALUE = 2\n')
    assert harness_digest(tmp_path) != clean


def test_snapshot_rechecks_modified_source_after_initial_verification(tmp_path):
    import pytest
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
