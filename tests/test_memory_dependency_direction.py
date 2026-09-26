"""Dependency direction of the memory subsystem (an architecture tripwire).

The runtime core offers typed adapter points (``synapse.memory_points``) and
knows no court; only the composition root (the CLI) builds the memory
subsystem. Gold never imports it: project runs reach the court through the
port the composition hands them. The subsystem's own outbound edges are a
reviewed whitelist, each owned by the one module whose responsibility it is.

The scan reads source with ``ast`` and imports nothing it inspects.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "synapse"
MEMORY = "synapse.memory_consolidation"
COMPOSITION_ROOTS = frozenset({"synapse/cli.py"})

#: Outbound edge -> the subsystem modules that own it (relative to the package).
APPROVED_OUTBOUND = {
    "synapse.memory_points": {"factory.py", "session.py", "learning/behavior.py", "learning/triggers.py",
                              "learning/applicability.py"},
    "synapse.habit_triggers": {"learning/triggers.py", "learning/applicability.py", "learning/dependencies.py"},
    # The program's own literals: values a driver or author knew before any answer (result references).
    "synapse.lexer": {"learning/dependencies.py"},
    "synapse.hardening": {"court/consolidation.py", "court/window.py"},
    "synapse.version": {"factory.py"},
    # The verified re-execution and session reader of durable cognitive runs (court ports).
    "synapse.durable_cognitive": {"factory.py"},
    # The admitted MCP tool boundary, used only by the gateway's transport.
    "synapse.agents.codec": {"tools/mcp_transport.py"},
    "synapse.agents.mcp_tools": {"tools/mcp_transport.py"},
    # Gold's decision chain and owner journal.
    "synapse.experiments.gold.project_court": {"owner.py", "court/consolidation.py"},
    "synapse.experiments.gold.project_memory_store": {"owner.py"},
    # Gold's canonical record identity.
    "synapse.experiments.gold.canonicalization": {"records.py", "legitimacy.py"},
    "synapse.experiments.gold.contracts": {"records.py"},
    # Gold's publication gates and lifecycle, reached only through the legitimacy adapter.
    **{f"synapse.experiments.gold.{name}": {"legitimacy.py"} for name in (
        "admission_journal", "knowledge_environment", "learned_habit_lifecycle", "learned_habit_profile",
        "persistence", "source_verification", "stage12.reusable", "stage13.publication",
        "stage13.publication_store")},
}


def _module_parts(path: Path) -> list[str]:
    return list(path.relative_to(REPO_ROOT).with_suffix("").parts)


def _imports(path: Path) -> set[str]:
    found = set()
    parts = _module_parts(path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found.add(node.module)
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(parts[:len(parts) - node.level])
            found.update({f"{base}.{node.module}"} if node.module else {f"{base}.{alias.name}" for alias in node.names})
    return found


def _sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_only_the_composition_root_builds_the_memory_subsystem():
    offenders = []
    for path in _sources(PACKAGE):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative.startswith("synapse/memory_consolidation/") or relative in COMPOSITION_ROOTS:
            continue
        if any(name == MEMORY or name.startswith(MEMORY + ".") for name in _imports(path)):
            offenders.append(relative)
    assert offenders == []


def test_memory_outbound_edges_are_the_reviewed_whitelist():
    root = PACKAGE / "memory_consolidation"
    unexpected = []
    for path in _sources(root):
        owner = path.relative_to(root).as_posix()
        for name in _imports(path):
            if not name.startswith("synapse.") or name == MEMORY or name.startswith(MEMORY + "."):
                continue
            if owner not in APPROVED_OUTBOUND.get(name, ()):
                unexpected.append(f"{owner} -> {name}")
    assert unexpected == []
