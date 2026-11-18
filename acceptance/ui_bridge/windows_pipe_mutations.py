"""Serial negative controls for actual Windows byte-pipe fidelity.

Two private native-launcher variants merge stderr into stdout or misdirect stdout
to stderr. Only the byte witness's explicit assertion detects a mutant.
No installed files or product checkout sources are modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
TARGET = 'synapse/ui/src-tauri/src/lifecycle/windows/launch.rs'
SEEDS = [
    ('PIPE-stderr-merge', 'startup.StartupInfo.hStdError = handles[2];', 'startup.StartupInfo.hStdError = handles[1];'),
    ('PIPE-stdout-misdirection', 'startup.StartupInfo.hStdOutput = handles[1];', 'startup.StartupInfo.hStdOutput = handles[2];'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--cargo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    assert not workspace.is_relative_to(ROOT) and not workspace.exists()
    workspace.mkdir(parents=True)
    subject = workspace / 'subject'
    for relative in ('synapse/ui/src-tauri', 'synapse/ui/contracts', 'synapse/machine/schemas', 'acceptance/ui_bridge/native'):
        shutil.copytree(ROOT / relative, subject / relative,
                        ignore=shutil.ignore_patterns('resources', 'target', 'gen', '__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'synapse/machine/release_profile.json', subject / 'synapse/machine')
    shutil.copy2(ROOT / 'acceptance/ui_bridge/corpus.json', subject / 'acceptance/ui_bridge')
    source_path = subject / TARGET
    source = source_path.read_text(encoding='utf-8')
    executable = Path(os.environ['CARGO_TARGET_DIR']) / 'debug/windows_pipe_bytes.exe'
    def observe():
        build = subprocess.run([str(args.cargo.absolute()), 'build', '--offline', '--locked', '--manifest-path',
            'acceptance/ui_bridge/native/Cargo.toml', '--bin', 'windows_pipe_bytes'], cwd=subject, capture_output=True, timeout=180)
        if build.returncode:
            return 'INVALID'
        try:
            result = subprocess.run([str(executable)], cwd=workspace, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return 'INCONCLUSIVE_TIMEOUT'
        if result.returncode == 0 and json.loads(result.stdout)['status'] == 'PASS':
            return 'PASS'
        return 'DETECTED' if b'panicked at' in result.stderr and (b'assertion' in result.stderr or b'CORE_EXITED' in result.stderr or b'PROTOCOL_ERROR' in result.stderr) else 'INFRASTRUCTURE_FAILURE'
    record = {'schema': 1, 'target': TARGET, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'baseline': observe(), 'records': []}
    assert record['baseline'] == 'PASS'
    for name, before, after in SEEDS:
        assert source.count(before) == 1
        try:
            source_path.write_text(source.replace(before, after, 1), encoding='utf-8')
            observed = observe()
        finally:
            source_path.write_text(source, encoding='utf-8')
        verdict = {'DETECTED': 'KILLED', 'PASS': 'SURVIVED'}.get(observed, observed)
        record['records'].append({'id': name, 'before': before, 'after': after, 'result': verdict})
        print(name, verdict, flush=True)
        args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    record['final'] = observe()
    args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return 0 if record['final'] == 'PASS' and all(row['result'] == 'KILLED' for row in record['records']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
