"""Negative controls for the installed canonical byte corpus, outside the repo.

The copied installed Python inventory is verified before mutation. These cases
exercise its canonical CLI directly; Windows Job/pipe tests are recorded separately.
Only a failed behavioral assertion is a kill; collection/setup/timeouts are not.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SEEDS = [
    ('PIPE-duplicate-key', 'synapse/machine/codec.py',
     'if key in result:', 'if False:', 'duplicate top level'),
    ('PIPE-negative-zero', 'synapse/machine/codec.py',
     'if token == "-0" or abs(value) > SAFE_INTEGER:', 'if abs(value) > SAFE_INTEGER:', 'negative zero'),
    ('PIPE-boundary', 'synapse/machine/codec.py',
     'if length > limit:', 'if length >= limit:', 'exact_limit'),
    ('PIPE-refusal-correlation', 'synapse/machine/session.py',
     'return refusal("PARAMS_INVALID", fatal=False, request_id=request_id)',
     'return refusal("PARAMS_INVALID", fatal=False, request_id="unrelated")', 'multibyte'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scratch = args.workspace.resolve()
    assert not scratch.is_relative_to(ROOT) and not scratch.exists(), 'fresh external scratch required'
    manifest = json.loads((args.core / 'bundle-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((args.core / name).read_bytes()).hexdigest() == expected
    shutil.copytree(args.core, scratch / 'core')
    subject = scratch / 'core'
    environment = {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                   'SYNAPSE_ACCEPTANCE_MACHINE_COMMAND': json.dumps([str(subject / manifest['python']), '-I', '-B', '-m', 'synapse', 'machine'])}
    def observe(selector=None):
        report = scratch / 'pytest.xml'
        report.unlink(missing_ok=True)
        command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                   '--basetemp', str(scratch / 'pytest-temp'), '--junitxml', str(report),
                   'acceptance/ui_bridge/test_pipe_corpus_acceptance.py']
        if selector:
            # Pytest expressions quote multiword corpus IDs through separate terms.
            command += ['-k', ' and '.join(selector.split())]
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return 'INCONCLUSIVE_TIMEOUT', 0
        if not report.exists():
            return 'INFRASTRUCTURE_FAILURE', 0
        cases = ET.parse(report).findall('.//testcase')
        failures = [case.find('failure') for case in cases if case.find('failure') is not None]
        if result.returncode == 0 and cases:
            return 'PASS', len(cases)
        if result.returncode == 1 and failures and not ET.parse(report).findall('.//error') and all('AssertionError' in (failure.text or '') for failure in failures):
            return 'DETECTED', len(cases)
        return 'INFRASTRUCTURE_FAILURE', len(cases)
    baseline, count = observe()
    record = {'schema': 1, 'scope': 'copied installed canonical CLI; no GUI mutation claim',
              'baseline': baseline, 'baseline_cases': count, 'records': []}
    assert baseline == 'PASS', 'unmodified installed corpus baseline failed'
    for name, relative, before, after, selector in SEEDS:
        candidates = list(subject.rglob(relative.split('/')[-1]))
        target = next(path for path in candidates if path.as_posix().endswith(relative))
        original = target.read_text(encoding='utf-8')
        assert original.count(before) == 1
        try:
            target.write_text(original.replace(before, after, 1), encoding='utf-8')
            observed, cases = observe(selector)
        finally:
            target.write_text(original, encoding='utf-8')
        verdict = {'DETECTED': 'KILLED', 'PASS': 'SURVIVED'}.get(observed, observed)
        record['records'].append({'id': name, 'target': relative, 'before': before, 'after': after,
                                  'source_sha256': hashlib.sha256(original.encode()).hexdigest(),
                                  'detecting_cases': selector, 'executed': cases, 'result': verdict})
        print(name, verdict, flush=True)
        args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    record['final'], record['final_cases'] = observe()
    args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return 0 if record['final'] == 'PASS' and all(row['result'] == 'KILLED' for row in record['records']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
