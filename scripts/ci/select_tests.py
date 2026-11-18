"""Build affected test matrices while keeping existing CI commands and environments."""
from __future__ import annotations

import argparse
import fnmatch
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

import yaml

from changes import changed_paths, comparison
from dependencies import add_runtime_edges, affected, python_graph

ROOT = Path(__file__).resolve().parents[2]
MANUAL = {'acceptance/stage4/stage16/test_live_gemini_worker.py',
          'tests/test_as2_postgresql_external_provider_p0645.py'}
SPECIAL = {'tests/test_durable_execution.py', 'tests/test_version_sync.py'}


def matrices(workflow, selected, reached):
    result, owned = {}, set()
    for name, job in workflow['jobs'].items():
        if name in {'select', 'gold-slow', 'additional-tests', 'ui', 'durable', 'version', 'selection-acceptance'}:
            continue
        commands = '\n'.join(step.get('run', '') for step in job.get('steps', []))
        fixed = set(re.findall(r'(?:tests|acceptance)/[\w/.*-]+\.py', commands))
        owned.update(fixed)
        catalog = job.get('env', {}).get('TEST_MATRIX')
        matrix = yaml.safe_load(catalog) if catalog else job.get('strategy', {}).get('matrix')
        rows = []
        if matrix:
            if 'include' in matrix:
                rows = matrix['include']
            else:
                keys = list(matrix)
                rows = [dict(zip(keys, values)) for values in itertools.product(*(matrix[key] for key in keys))]
            owned.update(row['file'] for row in rows if row.get('file'))
        def touches(patterns):
            return any(fnmatch.fnmatchcase(path, pattern) for path in selected for pattern in patterns)
        if name == 'gold-stage16-mutations':
            enabled = 'acceptance/stage4/stage16/mutations.py' in reached
            chosen = rows if enabled else []
        else:
            chosen = [row for row in rows if touches({row['file']} if row.get('file') else fixed)]
            enabled = bool(chosen) if matrix else touches(fixed)
        result[name] = {'enabled': enabled, 'matrix': {'include': chosen or rows[:1]}}
    return result, owned


def ui_selection(changed, selected):
    paths = {path for path in changed if not path.endswith('.md') and '/evidence/' not in path}
    workflow = '.github/workflows/ui-tests.yml' in paths
    control = any(path.startswith('synapse/machine/') for path in paths)
    native = workflow or control or any(path.startswith(('synapse/ui/src-tauri/', 'acceptance/ui_bridge/native/', 'scripts/ui/')) for path in paths)
    native |= bool(paths & {'synapse/cli.py', 'synapse/__main__.py', 'synapse/__init__.py', 'pyproject.toml'})
    frontend = workflow or any(path.startswith(('synapse/ui/src/', 'synapse/ui/contracts/', 'synapse/ui/tooling/', 'acceptance/ui_bridge/typescript/', 'acceptance/ui_bridge/browser_')) for path in paths)
    frontend |= bool(paths & {'synapse/ui/index.html', 'synapse/ui/vite.config.ts', 'synapse/ui/tsconfig.json', 'acceptance/ui_bridge/browser_acceptance.py', 'acceptance/ui_bridge/renderer_peer.js'})
    shared = bool(paths & {'synapse/ui/package.json', 'synapse/ui/package-lock.json', 'acceptance/ui_bridge/corpus.json'})
    native |= shared or any(path.startswith('synapse/ui/contracts/') for path in paths)
    frontend |= shared or control
    python = sorted(path for path in selected if path.startswith('acceptance/ui_bridge/test_'))
    kinds = {'python': bool(python), 'native': native, 'renderer': frontend}
    for kind in kinds:
        if f'acceptance/ui_bridge/mutation/{kind}.json' in paths:
            kinds[kind] = True
    return {**kinds, 'files': python, 'changed': sorted(paths),
            'reporting': 'acceptance/ui_bridge/test_mutation_evidence_acceptance.py' in selected}


def build_plan(root, files, changed, workflow, *, environment_changed=False):
    sources = {path: (root / path).read_text(encoding='utf-8-sig') for path in files if path.endswith('.py') and (root / path).is_file()}
    for path in changed - sources.keys():
        if path.endswith('.py'):
            sources[path] = ''  # Keep edges to deleted owners resolvable.
    graph = python_graph(sources, files | changed)
    add_runtime_edges(graph, files | changed)
    mutation_catalog = root / 'acceptance/stage4/stage16/mutations.json'
    stage_mutants = json.loads(mutation_catalog.read_text())['cases'] if mutation_catalog.exists() else []
    reached = affected(graph, changed)
    cases = {path for path in files if path.startswith(('tests/', 'acceptance/')) and Path(path).name.startswith('test_') and path.endswith('.py')}
    selected = (cases if environment_changed else cases & reached) - MANUAL
    # Selector fixtures name synthetic product paths; those are not consumers.
    selected = {path for path in selected if not path.startswith('acceptance/ci/')}
    for name in ('python', 'reporting'):
        catalog = f'acceptance/ui_bridge/mutation/{name}.json'
        if catalog in changed and (root / catalog).exists():
            for seed in json.loads((root / catalog).read_text()):
                selected.update(case.split('::')[0] for case in seed['cases'])
    tooling = any(path.startswith(('scripts/ci/', 'acceptance/ci/', '.github/workflows/')) for path in changed)
    if tooling:
        selected.update(path for path in cases if path.startswith('acceptance/ci/'))
    if '.github/workflows/ui-tests.yml' in changed:
        selected.update(path for path in cases if path.startswith('acceptance/ui_bridge/'))
    groups, owned = matrices(workflow, selected, reached)
    if 'gold-stage16-mutations' in groups:
        all_mutants = bool(changed & {'acceptance/stage4/stage16/mutations.py', 'acceptance/stage4/stage16/mutations.json'})
        rows = [{'case': seed['id']} for seed in stage_mutants if all_mutants or seed['path'] in reached or seed['killer'].split('::')[0] in selected]
        groups['gold-stage16-mutations'] = {'enabled': bool(rows), 'matrix': {'include': rows or [{'case': 'unselected'}]}}
    additional = sorted(path for path in selected - SPECIAL if not path.startswith(('acceptance/ui_bridge/', 'acceptance/ci/'))
                        and not any(fnmatch.fnmatchcase(path, pattern) for pattern in owned))
    ui_changed = changed | ({'synapse/machine/release_profile.json'} if environment_changed else set())
    ui = ui_selection(ui_changed, selected)
    ui['mutation_files'] = sorted(selected)
    catalogs = [root / f'acceptance/ui_bridge/mutation/{name}.json' for name in ('python', 'reporting')]
    ui['python_mutations'] = any(selected.intersection(case.split('::')[0] for case in seed['cases'])
                                 for catalog in catalogs if catalog.exists() for seed in json.loads(catalog.read_text()))
    return {'tests': sorted(selected), 'groups': groups, 'additional': additional,
            'ui': ui, 'tooling': tooling,
            'durable': 'tests/test_durable_execution.py' in selected,
            'version': 'tests/test_version_sync.py' in selected}


def python_environment_changed(root, base, changed):
    if 'pyproject.toml' not in changed:
        return False
    old = tomllib.loads(subprocess.check_output(['git', 'show', base + ':pyproject.toml'], cwd=root).decode())
    new = tomllib.loads((root / 'pyproject.toml').read_text())
    def dependencies(value):
        project = value.get('project', {})
        return value.get('build-system'), [project.get(key) for key in ('dependencies', 'optional-dependencies', 'requires-python')]
    return dependencies(old) != dependencies(new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--head', default='HEAD')
    args = parser.parse_args()
    if args.base:
        base, head, merge_base = args.base, args.head, True
    else:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        base, head, merge_base = comparison(event, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_SHA'])
    base, changed = changed_paths(ROOT, base, head, merge_base)
    files = {path.decode() for path in subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0') if path}
    workflow = yaml.safe_load((ROOT / '.github/workflows/stage4-gold.yml').read_text())
    plan = build_plan(ROOT, files, changed, workflow, environment_changed=python_environment_changed(ROOT, base, changed))
    if 'GITHUB_OUTPUT' in os.environ:
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
            output.write('plan=' + json.dumps(plan, separators=(',', ':')) + '\n')
    print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    main()
