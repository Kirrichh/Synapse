"""External Windows request-guard mutation campaign; raw diagnostics stay transient.

Prepare private variants first, then run their real WebView2 windows serially on
an ordinary desktop. Only an observed outbound navigation request kills a native
guard mutant; build failures, missing readiness and debugger timeouts do not.
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

ROOT = Path(__file__).resolve().parents[2]
TARGET = 'synapse/ui/src-tauri/src/webview_network.rs'
POLICY = [
    ('NET-host', 'matches!(url.host_str(), Some("tauri.localhost" | "ipc.localhost"))', 'true'),
    ('NET-port', 'url.port().is_none()', 'true'),
    ('NET-user', 'url.username().is_empty()', 'true'),
    ('NET-scheme', 'url.scheme() == if https { "https" } else { "http" }', 'true'),
]
NATIVE = [
    ('NET-filter', 'w!("*"), COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,',
     'w!("http://tauri.localhost/*"), COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,'),
    ('NET-response', 'if !local && args.SetResponse(&denied).is_err() {',
     'if !local && false {'),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--resume-prepare', action='store_true')
    parser.add_argument('--cargo', type=Path)
    parser.add_argument('--node', type=Path)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    assert not workspace.is_relative_to(ROOT), 'private variants must stay outside source'
    workspace.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace / 'prepared.json'
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}

    def execute(command, cwd=ROOT, timeout=600):
        return subprocess.run(command, cwd=cwd, env=environment,
                              capture_output=True, timeout=timeout)

    if args.prepare:
        assert args.cargo and args.baseline
        subject = workspace / 'subject'
        ignored = shutil.ignore_patterns('target', 'gen', '__pycache__', '*.pyc')
        if args.resume_prepare:
            assert subject.is_dir() and not manifest_path.exists()
            for folder in ('synapse/ui/src-tauri/src', 'acceptance/ui_bridge/native'):
                for original in (ROOT / folder).glob('**/*.rs'):
                    assert digest(original) == digest(subject / original.relative_to(ROOT))
            assert digest(args.baseline) == digest(workspace / 'baseline.exe')
        else:
            assert not subject.exists(), 'use a fresh private workspace'
            for relative in ('synapse/ui/src-tauri', 'synapse/ui/contracts', 'synapse/ui/dist',
                             'synapse/machine/schemas', 'acceptance/ui_bridge/native'):
                shutil.copytree(ROOT / relative, subject / relative, ignore=ignored)
            shutil.copy2(ROOT / 'synapse/machine/release_profile.json', subject / 'synapse/machine')
            # All executables share the same verified local bundle, never a fake core.
            shutil.copytree(ROOT / 'synapse/ui/src-tauri/resources/core', workspace / 'core')
            shutil.copy2(args.baseline, workspace / 'baseline.exe')
        source_path = subject / TARGET
        source = source_path.read_text(encoding='utf-8')
        record = {'schema': 1, 'source_encoding': 'UTF-8 with LF', 'target': TARGET,
                  'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                  'startup_sha256': hashlib.sha256((ROOT / 'synapse/ui/src-tauri/src/desktop/mod.rs').read_text(encoding='utf-8').encode()).hexdigest(),
                  'policy': [], 'variants': []}
        environment['CARGO_TARGET_DIR'] = str(workspace / 'policy-target')
        # Rustup dispatches by argv[0]; preserve the cargo proxy's basename.
        command = [str(args.cargo.absolute()), 'test', '--offline', '--locked', '--manifest-path',
                   'acceptance/ui_bridge/native/Cargo.toml', '--test', 'webview_network']
        result = execute(command, subject)
        assert result.returncode == 0, 'policy baseline must pass'
        record['policy_baseline'] = 'PASS'
        for name, before, after in POLICY:
            assert source.count(before) == 1
            try:
                source_path.write_text(source.replace(before, after, 1), encoding='utf-8')
                result = execute(command, subject)
                verdict = ('SURVIVED' if result.returncode == 0 else
                           'KILLED' if b'test result: FAILED' in result.stdout else 'INVALID')
                record['policy'].append({'id': name, 'before': before, 'after': after, 'result': verdict})
                print(name, verdict, flush=True)
            finally:
                source_path.write_text(source, encoding='utf-8')
        assert execute(command, subject).returncode == 0, 'final policy baseline must pass'
        record['policy_final'] = 'PASS'
        # Reuse already compiled dependency artifacts; each variant rebuilds the
        # changed crate from the disposable subject, never from mutated product files.
        environment['CARGO_TARGET_DIR'] = os.environ['CARGO_TARGET_DIR']
        command = [str(args.cargo.absolute()), 'build', '--offline', '--locked', '--manifest-path',
                   'synapse/ui/src-tauri/Cargo.toml']
        for name, before, after in NATIVE:
            assert source.count(before) == 1
            try:
                source_path.write_text(source.replace(before, after, 1), encoding='utf-8')
                result = execute(command, subject)
                assert result.returncode == 0, 'native variant compilation failed'
                artifact = workspace / (name + '.exe')
                shutil.copy2(Path(environment['CARGO_TARGET_DIR']) / 'debug/synapse-desktop.exe', artifact)
                record['variants'].append({'id': name, 'before': before, 'after': after,
                                           'artifact': artifact.name, 'sha256': digest(artifact)})
                print(name, 'BUILT', flush=True)
            finally:
                source_path.write_text(source, encoding='utf-8')
        record['baseline_sha256'] = digest(workspace / 'baseline.exe')
        manifest_path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        return 0 if all(row['result'] == 'KILLED' for row in record['policy']) else 1

    assert args.node
    record = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert digest(workspace / 'baseline.exe') == record['baseline_sha256']
    for variant in record['variants']:
        assert digest(workspace / variant['artifact']) == variant['sha256']
    def observe(executable):
        output = workspace / 'observed.json'
        output.unlink(missing_ok=True)
        result = execute([str(args.node.resolve()), str(ROOT / 'acceptance/ui_bridge/windows_installed_security.mjs'),
                          '--executable', str(executable), '--workspace', str(workspace),
                          '--output', str(output)], timeout=120)
        if not output.exists():
            return 'INVALID', None
        observation = json.loads(output.read_text(encoding='utf-8'))
        checks = observation['checks']
        if result.returncode == 0 and checks and all(row['status'] == 'PASS' for row in checks):
            return 'PASS', observation
        failure = next((row for row in checks if row['status'] == 'FAIL'), {})
        if (failure.get('name') == 'foreign top-level navigation denied'
                and failure.get('observed', {}).get('networkRequests', 0) > 0):
            return 'NETWORK_REQUEST', observation
        return 'INVALID', observation
    baseline, observation = observe(workspace / 'baseline.exe')
    record['native_baseline'] = baseline
    record['native_baseline_observation'] = observation
    args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    assert baseline == 'PASS', 'real window baseline must pass'
    for variant in record['variants']:
        observed, observation = observe(workspace / variant['artifact'])
        variant['result'] = {'NETWORK_REQUEST': 'KILLED', 'PASS': 'SURVIVED'}.get(observed, observed)
        variant['observation'] = observation
        print(variant['id'], variant['result'], flush=True)
    final, observation = observe(workspace / 'baseline.exe')
    record['native_final'] = final
    record['native_final_observation'] = observation
    args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return 0 if final == 'PASS' and all(row['result'] == 'KILLED' for row in record['variants'] + record['policy']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
