"""Benchmark instrumentation must not enter ordinary pytest collection."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_reporting_tree_does_not_register_benchmark_instrumentation():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, '-B', '-m', 'pytest', '--collect-only', '--trace-config',
         '-q', '-p', 'no:cacheprovider', 'reports/pr112_benchmark'],
        cwd=root, env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode in (0, 5), result.stderr
    assert 'reports/pr112_benchmark/conftest.py' not in result.stdout


def test_instrumentation_is_active_only_when_explicitly_requested(tmp_path):
    root = Path(__file__).resolve().parents[2]
    probe = tmp_path / 'test_probe.py'
    probe.write_text(
        'import os, subprocess\n'
        'def test_subprocess_owner():\n'
        '    assert subprocess.run.__name__ == os.environ["EXPECTED_RUN"]\n'
    )
    environment = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                   'PYTHONPATH': str(root / 'acceptance/benchmarks/pr112'),
                   'PR112_BENCH_TRACE': str(tmp_path / 'trace.jsonl')}
    command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(probe)]
    normal = subprocess.run(command, cwd=root, env={**environment, 'EXPECTED_RUN': 'run'},
                            capture_output=True, text=True, timeout=60)
    assert normal.returncode == 0, normal.stdout + normal.stderr
    explicit = subprocess.run(command[:7] + ['-p', 'benchmark_plugin'] + command[7:], cwd=root,
                              env={**environment, 'EXPECTED_RUN': 'measured'},
                              capture_output=True, text=True, timeout=60)
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr


def test_benchmark_rejects_unequal_source_bytecode_before_running(tmp_path):
    root = Path(__file__).resolve().parents[2]
    base = tmp_path / 'base'
    candidate = tmp_path / 'candidate'
    base.mkdir()
    (candidate / '__pycache__').mkdir(parents=True)
    (candidate / '__pycache__' / 'example.cpython-312.pyc').write_bytes(b'bytecode')
    harness = tmp_path / 'harness'
    shutil.copytree(root / 'acceptance/benchmarks/pr112', harness, ignore=shutil.ignore_patterns('__pycache__'))
    runner = harness / 'run_series.py'
    command = [sys.executable, '-B', str(runner), '--base', str(base), '--candidate', str(candidate),
               '--python', '/bin/false', '--native-addon', str(tmp_path), '--replay-artifact', str(tmp_path),
               '--output', str(tmp_path / 'result.json'), '--base-sha', 'base', '--candidate-sha', 'candidate']
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert 'source tree contains bytecode' in result.stderr
