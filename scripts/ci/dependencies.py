"""Select Python acceptance through imports and explicit non-import dependencies."""
from __future__ import annotations

import ast
from collections import defaultdict, deque
from pathlib import PurePosixPath
import re

# Data readers must be registered here when their paths are assembled at runtime.
# Keep the tool as a consumer too: its importers inherit the same dependency.
OWNERSHIP_READERS = {
    'tests/test_stage4_gold_canonical_entrypoint.py',
    'tests/test_stage4_gold_architecture.py',
    'tests/test_stage4_gold_dependency_direction.py',
    'scripts/stage9_ownership_dag.py',
}
BENCHMARK_GUARDS = {
    'acceptance/verification/test_benchmark_isolation.py',
    'acceptance/verification/test_benchmark_plugin_behavior.py',
    'acceptance/verification/test_benchmark_provenance.py',
}
# The mutation record is checked against its own pinned commit, so only the
# record itself, not later work on the sources it describes, selects its check.
# Pytest configuration can load plugins without an import edge.
DATA_DEPENDENCIES = {
    'governance/stage4_ownership_v2.json': OWNERSHIP_READERS,
    'reports/pr112_targeted_mutations.json': {'acceptance/verification/test_mutation_evidence_integrity.py'},
    'pytest.ini': BENCHMARK_GUARDS,
    'pyproject.toml': BENCHMARK_GUARDS,
    'setup.cfg': BENCHMARK_GUARDS,
    'tox.ini': BENCHMARK_GUARDS,
}


def python_graph(sources: dict[str, str], files: set[str]) -> dict[str, set[str]]:
    modules = {path.removesuffix('.py').replace('/', '.').removesuffix('.__init__'): path
               for path in sources}
    graph = {path: set() for path in sources}

    def resolve(name, owner):
        local = str(PurePosixPath(owner).parent / name.replace('.', '/')) + '.py'
        return modules.get(name) or (local if local in sources else None)

    for path, source in sources.items():
        tree = ast.parse(source, filename=path)
        package = path.split('/')[:-1]
        # The explicit machine command is a lazy dispatch boundary. Its peers
        # depend on the machine owner directly; other CLI commands do not.
        lazy_dispatch = {id(child) for branch in ast.walk(tree)
                         if path == 'synapse/cli.py' and isinstance(branch, ast.If)
                         and ast.unparse(branch.test) == "args.cmd == 'machine'"
                         for statement in branch.body for child in ast.walk(statement)
                         if isinstance(child, (ast.Import, ast.ImportFrom))}
        for node in ast.walk(tree):
            if id(node) in lazy_dispatch:
                continue
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                prefix = package[:len(package) - node.level + 1] if node.level else []
                module = '.'.join([*prefix, node.module or '']).strip('.')
                names = [module, *(module + '.' + alias.name for alias in node.names)]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Subprocess snippets, importlib names, and repo-relative assets.
                names = re.findall(r'\b(?:synapse|acceptance|scripts|tests)(?:\.\w+)+', node.value)
                for value in (node.value, str(PurePosixPath(path).parent / node.value)):
                    if value in files:
                        graph[path].add(value)
                if node.value in {'synapse', 'synapse.cli'} and 'subprocess' in source:
                    graph[path].add('synapse/cli.py')
            for name in names:
                dependency = resolve(name, path)
                if dependency:
                    graph[path].add(dependency)
                    # Importing a module executes each owning package initializer.
                    for parent in PurePosixPath(dependency).parents:
                        initializer = str(parent / '__init__.py')
                        if initializer in sources:
                            graph[path].add(initializer)
        # Pytest discovers parent fixtures without an ordinary import statement.
        if path.startswith(('tests/', 'acceptance/')):
            for parent in PurePosixPath(path).parents:
                fixture = str(parent / 'conftest.py')
                if fixture in sources:
                    graph[path].add(fixture)
    return graph


def add_runtime_edges(graph: dict[str, set[str]], files: set[str]) -> None:
    """Ownership edges for data, discovery, packaging, and child processes."""
    for asset, consumers in DATA_DEPENDENCIES.items():
        for consumer in consumers & graph.keys():
            graph[consumer].add(asset)
    # An unregistered governance data file may replace or supplement the manifest.
    # Until its readers are declared, conservatively retain this ownership family.
    unknown_governance = {path for path in files if path.startswith('governance/')
                          and path.endswith('.json') and path not in DATA_DEPENDENCIES}
    for consumer in OWNERSHIP_READERS & graph.keys():
        graph[consumer].update(unknown_governance)
    for path in graph:
        if path in {
            'tests/test_stage4_native_direct.py',
            'tests/test_stage4_canonical_differential.py',
            'tests/test_stage4_gold_native.py',
            'acceptance/stage4/stage11/test_native_runtime_resume_acceptance.py',
        }:
            graph[path].update(name for name in files if name.startswith('native/stage4_canonical/'))
        if path == 'tests/test_stage4_canonical_differential.py':
            graph[path].add('tests/data/stage4_baseline_1e226e52.json')
        if path.startswith('acceptance/verification/') and PurePosixPath(path).name == 'test_repository_selection.py':
            graph[path].update(name for name in files if name.startswith(('scripts/ci/', 'acceptance/benchmarks/pr112/',
                                                                          'native/stage4_canonical/')))
            graph[path].update({'.github/workflows/stage4-gold.yml', 'reports/pr112_targeted_mutations.json'})
        if path in BENCHMARK_GUARDS:
            graph[path].update(name for name in files if name.startswith('acceptance/benchmarks/pr112/'))
        if path.startswith('synapse/machine/'):
            graph[path].update(name for name in files if name.startswith('synapse/machine/') and not name.endswith('.py'))
        if path == 'acceptance/ui_bridge/machine_peer.py':
            graph[path].update({'synapse/cli.py', 'synapse/machine/entry.py'})
        if path == 'scripts/ui/build_core_bundle.py':
            graph[path].update(name for name in files if name.startswith('synapse/') and not name.startswith('synapse/ui/'))
            graph[path].add('pyproject.toml')
        if path == 'acceptance/ui_bridge/test_python_distribution_acceptance.py':
            graph[path].update({'pyproject.toml', 'synapse/ui/package.json', 'synapse/machine/release_profile.json'})
        if path == 'tests/test_version_sync.py':
            graph[path].update({'README.md', 'docs/SPEC.md'})
        # Architecture checks inspect source trees rather than importing owners.
        if path.startswith(('tests/', 'acceptance/')) and ('architecture' in path or 'dependency_direction' in path):
            graph[path].update(name for name in files if name.startswith('synapse/') and name.endswith('.py'))


def affected(graph: dict[str, set[str]], changed: set[str]) -> set[str]:
    reverse = defaultdict(set)
    for consumer, dependencies in graph.items():
        for dependency in dependencies:
            reverse[dependency].add(consumer)
    reached = set(changed)
    queue = deque(changed)
    while queue:
        for consumer in reverse[queue.popleft()] - reached:
            reached.add(consumer)
            queue.append(consumer)
    return reached
