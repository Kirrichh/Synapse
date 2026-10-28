"""A benchmark must not label altered source files as a published commit."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]


def test_changed_candidate_is_rejected_before_running_cli(tmp_path):
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
    runner = ROOT / 'acceptance/benchmarks/pr112/run_series.py'
    result = subprocess.run([sys.executable, '-B', str(runner), '--base', str(trees[0]), '--candidate', str(trees[1]),
        '--python', '/bin/false', '--native-addon', str(tmp_path), '--replay-artifact', str(tmp_path),
        '--output', str(tmp_path / 'result.json'), '--base-sha', commit, '--candidate-sha', commit],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'source identity differs from commit' in result.stderr
    assert not (tmp_path / 'result.json').exists()


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
