"""The isolated selector campaign must not depend on the checkout or hide failures."""
from pathlib import Path
import shutil
import subprocess

from acceptance.ci import mutate


def test_actual_synthetic_campaign_workspace_has_a_passing_baseline(tmp_path):
    root = Path(mutate.ROOT)
    for directory in ('scripts/ci', 'acceptance/ci'):
        shutil.copytree(root / directory, tmp_path / directory, ignore=shutil.ignore_patterns('__pycache__'))
    workflow = tmp_path / '.github/workflows/stage4-gold.yml'
    workflow.parent.mkdir(parents=True)
    shutil.copy2(root / '.github/workflows/stage4-gold.yml', workflow)
    (tmp_path / 'tests').mkdir()
    shutil.copy2(root / 'tests/test_stage4_gold_canonical_entrypoint.py', tmp_path / 'tests')
    assert mutate.execute(tmp_path) == 'PASS'


def test_timeout_and_collection_or_import_failure_are_not_mutation_kills(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('pytest', 1)
    monkeypatch.setattr(mutate.subprocess, 'run', timeout)
    assert mutate.execute(tmp_path) == 'TIMEOUT'
    for stdout in ('1 failed, 1 error\nERROR collecting test_example.py',
                   '1 failed\nE   ModuleNotFoundError: No module named missing',
                   '1 failed\nERROR at setup of test_example',
                   '1 failed\nE   FileNotFoundError: required fixture missing'):
        monkeypatch.setattr(mutate.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 1, stdout, ''))
        assert mutate.execute(tmp_path) == 'INVALID'


def test_only_a_collected_assertion_failure_is_a_mutation_kill(monkeypatch, tmp_path):
    failed = subprocess.CompletedProcess([], 1, 'FAILED test_sample.py::test_value\nE   assert False\n1 failed', '')
    monkeypatch.setattr(mutate.subprocess, 'run', lambda *a, **k: failed)
    assert mutate.execute(tmp_path) == 'KILLED'
