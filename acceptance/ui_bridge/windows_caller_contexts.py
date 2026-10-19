"""Prepare/run external native caller-context variants and detecting mutants.

The source checkout and installed application remain intact. Private topology
copies grant IPC and, for the origin case, relax earlier navigation/request guards
solely to reach the production native caller filter with a real foreign context.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
CALLER = 'synapse/ui/src-tauri/src/caller.rs'
DESKTOP = 'synapse/ui/src-tauri/src/desktop/mod.rs'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--cargo', type=Path)
    parser.add_argument('--node', type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    assert not workspace.is_relative_to(ROOT)
    prepared = workspace / 'prepared.json'
    if args.prepare:
        assert args.cargo and not workspace.exists(), 'fresh external workspace required'
        workspace.mkdir(parents=True)
        subject = workspace / 'subject'
        for relative in ('synapse/ui/src-tauri', 'synapse/ui/contracts', 'synapse/ui/dist',
                         'synapse/machine/schemas', 'synapse/commands'):
            shutil.copytree(ROOT / relative, subject / relative,
                            ignore=shutil.ignore_patterns('target', 'gen', '__pycache__', '*.pyc'))
        shutil.copy2(ROOT / 'synapse/machine/release_profile.json', subject / 'synapse/machine')
        shutil.copytree(ROOT / 'synapse/ui/src-tauri/resources/core', workspace / 'core')
        caller = (subject / CALLER).read_text(encoding='utf-8')
        desktop = (subject / DESKTOP).read_text(encoding='utf-8')
        configuration = subject / 'synapse/ui/src-tauri/tauri.conf.json'
        capability = subject / 'synapse/ui/src-tauri/capabilities/main.json'
        config = json.loads(configuration.read_text())
        cap = json.loads(capability.read_text())
        record = {'schema': 1, 'target': CALLER, 'source_sha256': hashlib.sha256(caller.encode()).hexdigest(),
                  'scope': 'external topology copies; actual Tauri command handlers and native context; not installed artifacts',
                  'variants': []}
        for context in ('label', 'origin'):
            changed_config = json.loads(json.dumps(config))
            changed_cap = json.loads(json.dumps(cap))
            changed_desktop = desktop
            if context == 'label':
                changed_config['app']['windows'][0]['label'] = 'foreign'
                changed_cap['windows'] = ['foreign']
                before, after = 'label != "main"', 'false'
            else:
                changed_cap['remote'] = {'urls': ['http://127.0.0.1:*']}
                start = 'let target = url::Url::parse(origin)?.join(&path.to_string_lossy())?;'
                end = 'crate::caller::authorize("main", &target)\n                    .map_err(|_| "desktop requires packaged content")?;'
                assert desktop.count(start) == desktop.count(end) == 1
                changed_desktop = changed_desktop.replace(start, 'let target = url::Url::parse(&std::env::var("SYNAPSE_ACCEPTANCE_FOREIGN_URL")?)?;')
                changed_desktop = changed_desktop.replace(end, '')
                changed_desktop = changed_desktop.replace('.on_navigation(|url| crate::caller::authorize("main", url).is_ok())', '.on_navigation(|_| true)')
                changed_desktop = changed_desktop.replace('let protected = crate::webview_network::install(&platform, config.use_https_scheme);', 'let protected: windows::core::Result<()> = Ok(());')
                before, after = '|| !local', '|| false'
            configuration.write_text(json.dumps(changed_config, indent=2), encoding='utf-8')
            capability.write_text(json.dumps(changed_cap, indent=2), encoding='utf-8')
            (subject / DESKTOP).write_text(changed_desktop, encoding='utf-8')
            assert caller.count(before) == 1
            for mutant in (False, True):
                (subject / CALLER).write_text(caller.replace(before, after, 1) if mutant else caller, encoding='utf-8')
                result = subprocess.run([str(args.cargo.absolute()), 'build', '--offline', '--locked', '--manifest-path',
                                         'synapse/ui/src-tauri/Cargo.toml'], cwd=subject, capture_output=True, timeout=600)
                assert result.returncode == 0, 'private caller variant build failed'
                name = context + ('-mutant' if mutant else '-baseline') + '.exe'
                shutil.copy2(Path(os.environ['CARGO_TARGET_DIR']) / 'debug/synapse-desktop.exe', workspace / name)
                record['variants'].append({'context': context, 'mutant': mutant, 'artifact': name,
                                           'sha256': digest(workspace / name), 'before': before, 'after': after})
                print(name, 'BUILT', flush=True)
        prepared.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        return 0
    assert args.node
    record = json.loads(prepared.read_text())
    for variant in record['variants']:
        executable = workspace / variant['artifact']
        assert digest(executable) == variant['sha256']
        observed = workspace / 'observed.json'
        observed.unlink(missing_ok=True)
        result = subprocess.run([str(args.node), str(ROOT / 'acceptance/ui_bridge/windows_caller_observer.mjs'),
            '--executable', str(executable), '--workspace', str(workspace), '--output', str(observed),
            '--context', variant['context']], capture_output=True, timeout=90)
        observation = json.loads(observed.read_text()) if observed.exists() else {'status': 'INFRASTRUCTURE_FAILURE'}
        variant['observation'] = observation
        variant['result'] = ('KILLED' if observation['status'] == 'BYPASS_DETECTED' else 'SURVIVED' if observation['status'] == 'PASS'
                             else 'INFRASTRUCTURE_FAILURE') if variant['mutant'] else observation['status']
        print(variant['artifact'], variant['result'], flush=True)
        args.output.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return 0 if all(row['result'] == ('KILLED' if row['mutant'] else 'PASS') for row in record['variants']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
