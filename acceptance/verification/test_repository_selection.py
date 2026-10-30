"""Exercise real selection, workflow ownership and the final result gate."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/ci'))
from check_results import required_jobs
from select_tests import build_plan


@pytest.fixture(scope='module')
def repository():
    files = {item.decode() for item in subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0') if item}
    workflow = yaml.safe_load((ROOT / '.github/workflows/stage4-gold.yml').read_text())
    return files, workflow


@pytest.mark.parametrize('changed,required', [
    ('reports/pr112_targeted_mutations.json', {'acceptance/verification/test_mutation_evidence_integrity.py'}),
    ('acceptance/benchmarks/pr112/benchmark_plugin.py', {'acceptance/verification/test_benchmark_isolation.py',
                                                        'acceptance/verification/test_benchmark_plugin_behavior.py',
                                                        'acceptance/verification/test_benchmark_provenance.py'}),
    ('pytest.ini', {'acceptance/verification/test_benchmark_isolation.py',
                    'acceptance/verification/test_benchmark_plugin_behavior.py'}),
])
def test_real_plan_selects_required_protectors_and_gate_rejects_missing_results(repository, changed, required):
    files, workflow = repository
    plan = build_plan(ROOT, files, {changed}, workflow)
    assert required.issubset(plan['additional'])
    assert 'additional-tests' in required_jobs(plan)
    assert 'additional-tests' in workflow['jobs']['gold-slow']['needs']
    results = {name: {'result': 'success'} for name in required_jobs(plan)}
    gate = ROOT / 'scripts/ci/check_results.py'
    env = {**os.environ, 'TEST_PLAN': json.dumps(plan)}
    assert subprocess.run([sys.executable, str(gate)], env={**env, 'JOB_RESULTS': json.dumps(results)}).returncode == 0
    for state in ('failure', 'cancelled', 'skipped', None):
        altered = dict(results)
        if state is None:
            altered.pop('additional-tests')
        else:
            altered['additional-tests'] = {'result': state}
        assert subprocess.run([sys.executable, str(gate)], env={**env, 'JOB_RESULTS': json.dumps(altered)},
                              capture_output=True).returncode != 0


def test_real_native_plan_retains_required_native_cases_without_unrelated_fallbacks(repository):
    files, workflow = repository
    plan = build_plan(ROOT, files, {'native/stage4_canonical/src/lib.rs'}, workflow)
    required = {'tests/test_stage4_native_direct.py', 'tests/test_stage4_canonical_differential.py',
                'tests/test_stage4_gold_native.py', 'acceptance/stage4/stage11/test_native_runtime_resume_acceptance.py'}
    assert required.issubset(plan['tests'])
    assert plan['groups']['stage4-native']['enabled']
    assert 'stage4-native' in required_jobs(plan)
    assert 'tests/test_stage4_gold_architecture.py' not in plan['tests']
    job = workflow['jobs']['stage4-native']
    commands = '\n'.join(step.get('run', '') for step in job['steps'])
    for case in required:
        assert case in commands
    assert 'maturin build' in commands and 'pip install /tmp/stage4-native-wheel/' in commands
    assert any(step.get('env', {}).get('SYNAPSE_REQUIRE_STAGE4_NATIVE') == '1' for step in job['steps'])


@pytest.mark.parametrize('changed', [
    'synapse/worker/local_edits.py',
    'synapse/experiments/gold/canonicalization.py',
    'acceptance/stage4/stage10/test_adaptive_line_boundaries.py',
    '.github/workflows/stage4-gold.yml',
])
def test_later_work_on_recorded_sources_is_not_gated_by_the_mutation_record(repository, changed):
    files, workflow = repository
    plan = build_plan(ROOT, files, {changed}, workflow)
    assert 'acceptance/verification/test_mutation_evidence_integrity.py' not in plan['tests']


def test_real_unrelated_documentation_does_not_select_protectors(repository):
    files, workflow = repository
    plan = build_plan(ROOT, files, {'docs/DETERMINISM_CONTRACT.md'}, workflow)
    assert not {'acceptance/verification/test_mutation_evidence_integrity.py',
                'acceptance/verification/test_benchmark_isolation.py',
                'acceptance/verification/test_benchmark_plugin_behavior.py',
                'acceptance/verification/test_benchmark_provenance.py'}.intersection(plan['tests'])
    assert not plan['groups']['stage4-native']['enabled']
