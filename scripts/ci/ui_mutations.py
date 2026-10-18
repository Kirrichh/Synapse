"""Run existing UI mutation campaigns only for selected behavior owners."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def selected_seeds(kind, seeds, plan):
    if kind != 'python':
        paths = set(plan['changed'])
        shared = any(path.startswith(('synapse/machine/', 'synapse/ui/contracts/')) for path in paths)
        shared |= bool(paths & {'.github/workflows/ui-tests.yml', f'acceptance/ui_bridge/mutation/{kind}.json',
                                'synapse/ui/package.json', 'synapse/ui/package-lock.json', 'acceptance/ui_bridge/corpus.json'})
        def selected(seed):
            if shared or seed['target'] in paths:
                return True
            if kind == 'native':
                return f"acceptance/ui_bridge/native/{seed['case']}.rs" in paths
            if seed['kind'] == 'browser':
                return any(path.startswith('acceptance/ui_bridge/browser_') for path in paths) or 'acceptance/ui_bridge/renderer_peer.js' in paths
            return any(path.startswith('acceptance/ui_bridge/typescript/') for path in paths)
        return [seed['id'] for seed in seeds if selected(seed)] if plan[kind] else []
    selected = set(plan.get('mutation_files', plan['files']))
    return [seed['id'] for seed in seeds if selected.intersection(case.split('::')[0] for case in seed['cases'])]


def main(kind):
    plan = json.loads(os.environ['UI_TEST_PLAN'])
    sources = [Path(f'acceptance/ui_bridge/mutation/{kind}.json')]
    if kind == 'python':
        sources.append(Path('acceptance/ui_bridge/mutation/reporting.json'))
    seeds = [seed for source in sources for seed in json.loads(source.read_text())]
    selected = selected_seeds(kind, seeds, plan)
    if not selected:
        return 0
    # Only structured, non-sensitive evidence; diagnostics are not retained.
    with tempfile.TemporaryDirectory(prefix='synapse-ui-mutation-') as workspace:
        return subprocess.call([sys.executable, 'acceptance/ui_bridge/mutation/run.py',
                                *map(str, sources), '--python', sys.executable,
                                '--workspace', workspace, '--output', str(Path(workspace) / 'results'),
                                '--timeout', '180', *[part for item in selected for part in ('--only', item)]])


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
