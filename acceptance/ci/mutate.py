"""Falsify changed test-selection behavior in disposable, synthetic workspaces."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def execute(root):
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'acceptance/ci'],
                            cwd=root, capture_output=True, text=True, timeout=45)
    if result.returncode == 0:
        return 'PASS'
    return 'KILLED' if result.returncode == 1 and ' failed' in result.stdout and 'ERROR collecting' not in result.stdout else 'INVALID'


def main():
    if execute(ROOT) != 'PASS':
        raise SystemExit('Unmutated selection acceptance failed.')
    results = []
    for seed in json.loads((ROOT / 'acceptance/ci/mutations.json').read_text()):
        with tempfile.TemporaryDirectory(prefix='synapse-ci-mutation-') as temporary:
            root = Path(temporary)
            for directory in ('scripts/ci', 'acceptance/ci'):
                shutil.copytree(ROOT / directory, root / directory, ignore=shutil.ignore_patterns('__pycache__'))
            path = root / seed['target']
            source = path.read_text()
            if source.count(seed['before']) != 1:
                raise SystemExit('Ambiguous mutation application: ' + seed['id'])
            path.write_text(source.replace(seed['before'], seed['after'], 1))
            try:
                status = execute(root)
            except subprocess.TimeoutExpired:
                status = 'TIMEOUT'
            status = 'SURVIVED' if status == 'PASS' else status
            print(seed['id'], status, flush=True)
            results.append(status)
    return 0 if all(result == 'KILLED' for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
