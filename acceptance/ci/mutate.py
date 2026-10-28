"""Falsify changed test-selection behavior in disposable, synthetic workspaces."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


CAMPAIGN_CASES = [
    'acceptance/ci/test_dependency_selection.py',
    'acceptance/ci/test_execution_selection.py',
    'acceptance/ci/test_ownership_selection.py',
    'acceptance/ci/test_plan_selection.py',
    'acceptance/ci/test_verification_gate.py',
]


def execute(root, *, include_details=False, cases=CAMPAIGN_CASES):
    try:
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                                 '--tb=short', *cases],
                                cwd=root, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return ('TIMEOUT', '') if include_details else 'TIMEOUT'
    if result.returncode == 0:
        return ('PASS', result.stdout) if include_details else 'PASS'
    output = result.stdout + result.stderr
    invalid = ('ERROR collecting', 'ERROR at setup', 'ModuleNotFoundError', 'ImportError',
               'FileNotFoundError', 'PermissionError', 'TimeoutExpired', ' error in ')
    semantic = ('E   assert', 'AssertionError', 'Failed: DID NOT RAISE')
    if (result.returncode == 1 and ' failed' in output and not any(item in output for item in invalid)
            and any(item in output for item in semantic)):
        return ('KILLED', output) if include_details else 'KILLED'
    return ('INVALID', output) if include_details else 'INVALID'


def main():
    if execute(ROOT) != 'PASS':
        raise SystemExit('Unmutated selection acceptance failed.')
    results = []
    with tempfile.TemporaryDirectory(prefix='synapse-ci-mutation-') as temporary:
        root = Path(temporary)
        for directory in ('scripts/ci', 'acceptance/ci'):
            shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns('__pycache__'))
        workflow = root / '.github/workflows/stage4-gold.yml'
        workflow.parent.mkdir(parents=True)
        shutil.copy2(ROOT / '.github/workflows/stage4-gold.yml', workflow)
        # The manifest case executes the existing architecture check unchanged.
        (root / 'tests').mkdir()
        shutil.copy2(ROOT / 'tests/test_stage4_gold_canonical_entrypoint.py', root / 'tests')
        if execute(root) != 'PASS':
            raise SystemExit('Mutation workspace baseline failed.')
        for seed in json.loads((ROOT / 'acceptance/ci/mutations.json').read_text()):
            path = root / seed['target']
            source = path.read_text()
            if source.count(seed['before']) != 1:
                raise SystemExit('Ambiguous mutation application: ' + seed['id'])
            try:
                path.write_text(source.replace(seed['before'], seed['after'], 1))
                # This mutant intentionally imports an unrelated test that raises
                # during collection. Use the direct selection assertion so a
                # collection error can never be mistaken for a mutation kill.
                cases = (['acceptance/ci/test_execution_selection.py::test_fixed_invocation_keeps_options_and_only_selected_cases']
                         if seed['id'] == 'CI-execution-filter' else CAMPAIGN_CASES)
                status, output = execute(root, include_details=True, cases=cases)
            finally:
                path.write_text(source)
            status = 'SURVIVED' if status == 'PASS' else status
            print(seed['id'], status, flush=True)
            if status == 'KILLED':
                for line in output.splitlines():
                    if line.startswith('FAILED ') or line.startswith('E   '):
                        print('  ' + line, flush=True)
            results.append(status)

    return 0 if all(result == 'KILLED' for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
