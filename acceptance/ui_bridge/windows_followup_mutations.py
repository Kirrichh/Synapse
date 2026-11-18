"""Serial Windows mutation campaign in disposable source copies outside the repo.

Raw compiler/test output is transient. A compiler error or infrastructure failure
is never a behavioral kill. Run without any installed Synapse window active.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = Path('acceptance/ui_bridge')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--cargo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    assert not workspace.is_relative_to(ROOT), 'mutation scratch must remain outside source'
    workspace.mkdir(parents=True, exist_ok=True)
    seeds = json.loads((ROOT / ACCEPTANCE / 'windows_followup_mutants.json').read_text())
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                   'CARGO_TARGET_DIR': str(workspace / 'target')}
    records = []
    with tempfile.TemporaryDirectory(prefix='subject-', dir=workspace) as temporary:
        subject = Path(temporary)
        ignored = shutil.ignore_patterns('ui', '__pycache__', '*.pyc')
        shutil.copytree(ROOT / 'synapse', subject / 'synapse', ignore=ignored)
        ignored = shutil.ignore_patterns('target', 'gen', 'resources', '__pycache__', '*.pyc')
        for relative in ('synapse/ui/src-tauri', 'synapse/ui/contracts', 'acceptance/ui_bridge'):
            shutil.copytree(ROOT / relative, subject / relative, ignore=ignored)
        def run(command, timeout=600):
            return subprocess.run(command, cwd=subject, env=environment, capture_output=True, timeout=timeout)
        def compile_bins():
            return run([str(args.cargo), 'build', '--offline', '--locked', '--manifest-path',
                        str(ACCEPTANCE / 'native/Cargo.toml'), '--bins']).returncode == 0
        def observe(kind, scenario=None):
            if kind == 'stderr':
                result = run([sys.executable, '-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                              '--basetemp', str(workspace / 'pytest'),
                              str(ACCEPTANCE / 'test_output_backpressure_acceptance.py')], timeout=30)
                if result.returncode == 0:
                    return 'PASS'
                return 'FAIL' if (b'AssertionError' in result.stdout and b'WRITE_TIMEOUT' in result.stdout
                                  and b'1 failed' in result.stdout) else 'INFRASTRUCTURE_FAILURE'
            observed = workspace / 'observed.json'
            observed.unlink(missing_ok=True)
            if kind == 'startup':
                command = [sys.executable, '-B', str(ACCEPTANCE / 'windows_startup_debugger.py'),
                           '--executable', str(workspace / 'target/debug/windows_startup_probe.exe'), '--output', str(observed)]
                if scenario:
                    command += ['--scenario', scenario]
            else:
                command = [sys.executable, '-B', str(ACCEPTANCE / 'windows_runtime_descendants.py'),
                           '--executable', str(workspace / 'target/debug/windows_runtime_probe.exe'),
                           '--scratch', str(workspace), '--output', str(observed)]
            run(command, timeout=180)
            if not observed.exists():
                return 'INFRASTRUCTURE_FAILURE'
            statuses = [row['result'] for row in json.loads(observed.read_text())['records']]
            if not statuses or 'INFRASTRUCTURE_FAILURE' in statuses:
                return 'INFRASTRUCTURE_FAILURE'
            return 'FAIL' if 'FAIL' in statuses else 'PASS'
        compiled = compile_bins()
        baselines = {kind: observe(kind) if compiled else 'INVALID' for kind in ('startup', 'runtime', 'stderr')}
        print('BASELINES', json.dumps(baselines), flush=True)
        assert all(value == 'PASS' for value in baselines.values()), 'mutation baseline must pass'
        for seed in seeds:
            target = subject / seed['target']
            source = target.read_text(encoding='utf-8')
            assert source.count(seed['before']) == 1, 'mutation seed is no longer unique'
            row = {key: seed[key] for key in ('id', 'target', 'behavior', 'kind')}
            row.update(source_sha256=hashlib.sha256(source.encode()).hexdigest(), baseline='PASS')
            try:
                target.write_text(source.replace(seed['before'], seed['after'], 1), encoding='utf-8')
                built = seed['kind'] == 'stderr' or compile_bins()
                observed = observe(seed['kind'], seed.get('scenario')) if built else 'INVALID'
                row['result'] = {'FAIL': 'KILLED', 'PASS': 'SURVIVED'}.get(observed, observed)
            except subprocess.TimeoutExpired:
                row['result'] = 'INCONCLUSIVE_TIMEOUT'
            finally:
                target.write_text(source, encoding='utf-8')
            records.append(row)
            args.output.write_text(json.dumps({'schema': 1, 'source_encoding': 'UTF-8 with LF',
                'baselines': baselines, 'records': records}, indent=2) + '\n', encoding='utf-8')
            print(seed['id'], row['result'], flush=True)
        # Confirm the unmodified isolated source still satisfies every observer.
        final_compiled = compile_bins()
        final = {kind: observe(kind) if final_compiled else 'INVALID' for kind in ('startup', 'runtime', 'stderr')}
        args.output.write_text(json.dumps({'schema': 1, 'source_encoding': 'UTF-8 with LF',
            'baselines': baselines, 'final_rerun': final, 'records': records}, indent=2) + '\n', encoding='utf-8')
        print('FINAL', json.dumps(final), flush=True)
    return 0 if all(row['result'] == 'KILLED' for row in records) and all(value == 'PASS' for value in final.values()) else 1

if __name__ == '__main__':
    raise SystemExit(main())
