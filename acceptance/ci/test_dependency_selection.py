"""Changed owners reach their consumers, including subprocess and data edges."""
from dependencies import add_runtime_edges, affected, python_graph


def test_transitive_relative_imports_and_cycles_select_existing_consumers():
    sources = {'synapse/owner.py': 'from . import adapter',
               'synapse/adapter.py': 'from . import owner',
               'tests/test_related.py': 'from synapse import adapter',
               'tests/test_unrelated.py': 'import json'}
    graph = python_graph(sources, set(sources))
    reached = affected(graph, {'synapse/owner.py'})
    assert 'tests/test_related.py' in reached
    assert 'tests/test_unrelated.py' not in reached


def test_package_initialization_and_parent_fixtures_are_dependencies():
    sources = {'synapse/__init__.py': 'from . import owner', 'synapse/owner.py': '',
               'synapse/leaf.py': '', 'acceptance/feature/conftest.py': '',
               'acceptance/feature/test_behavior.py': 'from synapse.leaf import value',
               'acceptance/other/test_behavior.py': ''}
    graph = python_graph(sources, set(sources))
    for changed in ('synapse/owner.py', 'acceptance/feature/conftest.py'):
        reached = affected(graph, {changed})
        assert 'acceptance/feature/test_behavior.py' in reached
        assert 'acceptance/other/test_behavior.py' not in reached


def test_subprocess_snippets_and_local_helpers_reach_cli_behavior():
    sources = {'synapse/cli.py': '', 'acceptance/peer.py': 'import subprocess\ncode = "from synapse.cli import main; main()"',
               'acceptance/test_process.py': 'from peer import run'}
    reached = affected(python_graph(sources, set(sources)), {'synapse/cli.py'})
    assert 'acceptance/test_process.py' in reached


def test_deleted_owner_remains_a_dependency_of_a_surviving_import():
    sources = {'synapse/removed.py': '', 'tests/test_public.py': 'from synapse.removed import invoke'}
    assert 'tests/test_public.py' in affected(python_graph(sources, set(sources)), {'synapse/removed.py'})


def test_machine_data_and_canonical_peer_are_not_invisible_to_import_analysis():
    sources = {'synapse/machine/entry.py': '', 'synapse/cli.py': 'from .machine.entry import run',
               'acceptance/ui_bridge/machine_peer.py': '',
               'acceptance/ui_bridge/test_session.py': 'from machine_peer import connect'}
    files = set(sources) | {'synapse/machine/release_profile.json'}
    graph = python_graph(sources, files)
    add_runtime_edges(graph, files)
    assert 'acceptance/ui_bridge/test_session.py' in affected(graph, {'synapse/machine/release_profile.json'})


def test_machine_only_change_does_not_schedule_other_cli_commands():
    sources = {'synapse/machine/entry.py': '', 'synapse/cli.py': "if args.cmd == 'machine':\n    from .machine.entry import run",
               'tests/test_debugger.py': 'from synapse.cli import main',
               'acceptance/ui_bridge/machine_peer.py': '',
               'acceptance/ui_bridge/test_machine.py': 'from machine_peer import run'}
    graph = python_graph(sources, set(sources))
    add_runtime_edges(graph, set(sources))
    reached = affected(graph, {'synapse/machine/entry.py'})
    assert 'acceptance/ui_bridge/test_machine.py' in reached
    assert 'tests/test_debugger.py' not in reached
