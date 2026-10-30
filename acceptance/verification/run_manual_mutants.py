"""Run a manual mutation catalog against a fresh export of one published commit.

    python -B acceptance/verification/run_manual_mutants.py CATALOG --commit SHA --output REPORT

Each catalog entry replaces exactly one occurrence of ``original`` with
``mutant`` in ``source``. Its detecting test file must pass on the unchanged
export and fail on the mutated one with a failed case, not an interrupted or
uncollected run. The report has the shape that
``test_mutation_evidence_integrity.py`` checks against the same commit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from test_mutation_evidence_integrity import transform_digest


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.commit):
        raise SystemExit('commit must be a full immutable SHA-1')
    catalog = json.loads(args.catalog.read_text())
    if not args.catalog.resolve().is_relative_to(ROOT):
        raise SystemExit('the catalog must be a file of this repository so the commit can pin it')
    with tempfile.TemporaryDirectory(prefix='synapse-mutants-') as temporary:
        root = Path(temporary)
        archive = subprocess.run(['git', 'archive', args.commit], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', str(root)], input=archive, check=True)
        baselines, mutants = {}, []
        for mutant in catalog:
            test = mutant['detecting_test'].split('::')[0]
            if test not in baselines:
                code, lines = pytest(root, test)
                if code != 0:
                    raise SystemExit(f'baseline of {test} did not pass: {summary(lines)}')
                baselines[test] = summary(lines)
            mutants.append(run_mutant(root, mutant, test, baselines[test]))
        files = {item[key].split('::')[0] for item in catalog for key in ('source', 'detecting_test')}
        files.add(args.catalog.resolve().relative_to(ROOT).as_posix())
        files = sorted(files)
        snapshot = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files}
    report = {
        'provenance': 'git-commit-snapshot',
        'tested_commit': args.commit,
        'snapshot_files': snapshot,
        'transform_sha256': transform_digest(mutants),
        'method': ('Each detecting test file passed on a fresh export of tested_commit; each mutant replaced '
                   'one exact occurrence and its file was run again. Killed means failed cases, not an '
                   'interrupted or uncollected run.'),
        'manual_mutants': mutants,
    }
    args.output.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n')
    survived = [item['id'] for item in mutants if item['result'] != 'killed']
    print(f'{len(mutants) - len(survived)} of {len(mutants)} killed' + (f'; survived: {survived}' if survived else ''))


def run_mutant(root, mutant, test, baseline):
    path = root / mutant['source']
    source = path.read_text()
    if source.count(mutant['original']) != 1 or mutant['original'] == mutant['mutant']:
        raise SystemExit(f'{mutant["id"]}: original must occur exactly once and differ from the mutant')
    path.write_text(source.replace(mutant['original'], mutant['mutant'], 1))
    try:
        code, lines = pytest(root, test)
    finally:
        path.write_text(source)
    failed = [line.split(' - ', 1)[0] for line in lines if line.startswith('FAILED ')]
    evidence = list(dict.fromkeys(line.strip() for line in lines if line.startswith('E ')))[:3]
    return {**{key: mutant[key] for key in ('id', 'source', 'original', 'mutant', 'detecting_test')},
            'result': 'killed' if code == 1 and failed else 'survived',
            'baseline_summary': baseline, 'mutant_summary': summary(lines),
            'failed_cases': failed, 'failure_evidence': evidence}


def pytest(root, test):
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--tb=short', test],
                            cwd=root, env=environment, capture_output=True, text=True, timeout=900)
    return result.returncode, result.stdout.strip().splitlines()


def summary(lines):
    return re.sub(r' in [0-9.]+s.*$', '', lines[-1].strip('= ')) if lines else ''


if __name__ == '__main__':
    main()
