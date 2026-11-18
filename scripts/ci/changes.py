"""Read cumulative branch changes; never execute event text as shell code."""
from __future__ import annotations

import subprocess
from pathlib import Path


def comparison(event, kind, head):
    if kind == 'pull_request':
        return event['pull_request']['base']['sha'], event['pull_request']['head']['sha'], True
    default = event.get('repository', {}).get('default_branch', 'main')
    if kind == 'push' and event.get('ref') == 'refs/heads/' + default:
        return event['before'], head, False
    return 'refs/remotes/origin/' + default, head, True


def changed_paths(root: Path, base: str, head: str, merge_base: bool) -> tuple[str, set[str]]:
    for revision in (base, head):
        subprocess.run(['git', 'rev-parse', '--verify', revision + '^{commit}'], cwd=root,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if merge_base:
        base = subprocess.check_output(['git', 'merge-base', base, head], cwd=root,
                                       stderr=subprocess.DEVNULL).decode().strip()
    paths = subprocess.check_output(['git', 'diff', '--name-only', '--no-renames', '-z', base, head, '--'], cwd=root)
    return base, {path.decode('utf-8') for path in paths.split(b'\0') if path}
