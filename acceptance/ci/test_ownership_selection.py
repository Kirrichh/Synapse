"""Manifest behavior and CI coverage must change together, without a full-suite fallback."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

from select_tests import build_plan

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'governance/stage4_ownership_v2.json'
CANONICAL = 'tests/test_stage4_gold_canonical_entrypoint.py'
READERS = {CANONICAL, 'tests/test_stage4_gold_architecture.py',
           'tests/test_stage4_gold_dependency_direction.py', 'tests/test_stage4_gold_ownership_dag.py'}
WORKFLOW = {'jobs': {'gold-fast': {'steps': [{'run': 'python -m pytest tests/test_stage4_gold_*.py'}]}}}


def subject(root):
    sources = {MANIFEST: json.dumps({'composition_roots': ['runner_composition.py']}),
               'synapse/cli.py': 'from synapse.experiments.gold.runner_composition import run\n',
               'synapse/experiments/gold/__init__.py': '',
               'scripts/stage9_ownership_dag.py': '',
               'tests/test_stage4_gold_ownership_dag.py': 'from scripts import stage9_ownership_dag\n',
               'tests/test_unrelated.py': '', 'docs/ui/notes.md': 'Synthetic notes.'}
    for name in READERS - sources.keys():
        sources[name] = ''
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    shutil.copy2(ROOT / CANONICAL, root / CANONICAL)
    return set(sources)


def run_canonical(root):
    return subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                           CANONICAL + '::test_stage4_is_entered_only_through_a_declared_composition_root'],
                          cwd=root, capture_output=True, text=True, timeout=15)


def test_manifest_rejection_is_executed_by_the_selected_architecture_group(tmp_path):
    files = subject(tmp_path)
    assert run_canonical(tmp_path).returncode == 0
    (tmp_path / MANIFEST).write_text(json.dumps({'composition_roots': []}))
    rejected = run_canonical(tmp_path)
    assert rejected.returncode == 1 and 'not §12' in rejected.stdout
    plan = build_plan(tmp_path, files, {MANIFEST}, WORKFLOW)
    assert set(plan['tests']) == READERS
    assert plan['groups']['gold-fast']['enabled']


def test_unknown_or_removed_governance_data_retains_related_ownership_checks(tmp_path):
    files = subject(tmp_path)
    plan = build_plan(tmp_path, files, {'governance/supplement.json'}, WORKFLOW)
    assert set(plan['tests']) == READERS


def test_unrelated_notes_do_not_schedule_product_checks(tmp_path):
    files = subject(tmp_path)
    plan = build_plan(tmp_path, files, {'docs/ui/notes.md'}, WORKFLOW)
    assert plan['tests'] == []
    assert not plan['groups']['gold-fast']['enabled']
