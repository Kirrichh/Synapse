"""Stage 4 dependency-direction tripwire (Patch 1 artifact, added in Patch 6.5).

NR-05 fixes a single production dependency direction: ``gold/* -> swebench/*``.
No swebench production module may import ``synapse.experiments.gold``. Outbound
imports from the gold package are limited to an approved whitelist so that a new
cross-boundary dependency is a deliberate, reviewed change rather than a silent
one.

This is an architecture test: it reads source with ``ast`` and never imports the
modules under inspection, so a cycle or a heavy import cannot hide from it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"
SWEBENCH_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "swebench"
GOLD_MODULE_PREFIX = "synapse.experiments.gold"

# Approved outbound dependencies of the gold package. Every entry is a narrow
# typed contract, not a protected-core module:
#   synapse.version          - version authority constants
#   synapse.ast              - AST node types for behavior compiler binding
#   synapse.bytecode         - compiler/program contracts for behavior binding
#   synapse.canonical_values - canonical value helpers
#   synapse.change.*         - committed-input controlled-change contracts
# Adding an entry requires an explicit NR-03/NR-05 review of the new boundary.
APPROVED_GOLD_OUTBOUND = frozenset(
    {
        "synapse.version",
        "synapse.ast",
        "synapse.bytecode",
        "synapse.canonical_values",
        "synapse.change.contract",
        "synapse.change.workspace",
    }
)

# NR-03 protected core: the gold package may never import these directly.
PROTECTED_CORE_MODULES = frozenset(
    {
        "synapse.interpreter",
        "synapse.cvm",
        "synapse.application",
        "synapse.cli",
        "synapse.golden_replay",
    }
)


def _python_sources(package: Path) -> list[Path]:
    return sorted(
        path
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imported_modules(path: Path) -> set[str]:
    """Return absolute ``synapse.*`` modules imported by ``path``."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Relative imports stay inside the owning package by construction.
            if node.level or not node.module:
                continue
            if node.module.startswith("synapse"):
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("synapse"):
                    modules.add(alias.name)
    return modules


def test_gold_package_has_sources_to_inspect() -> None:
    # Guards against the tripwire silently passing on an empty glob.
    assert _python_sources(GOLD_PACKAGE)
    assert _python_sources(SWEBENCH_PACKAGE)


@pytest.mark.parametrize(
    "path", _python_sources(SWEBENCH_PACKAGE), ids=lambda p: p.name
)
def test_swebench_production_module_never_imports_gold(path: Path) -> None:
    offending = {
        module
        for module in _imported_modules(path)
        if module == GOLD_MODULE_PREFIX or module.startswith(GOLD_MODULE_PREFIX + ".")
    }
    assert not offending, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offending)}; NR-05 fixes the "
        "production dependency direction as gold/* -> swebench/* only"
    )


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_gold_outbound_imports_stay_inside_the_whitelist(path: Path) -> None:
    outbound = {
        module
        for module in _imported_modules(path)
        if not (
            module == GOLD_MODULE_PREFIX or module.startswith(GOLD_MODULE_PREFIX + ".")
        )
    }
    unapproved = outbound - APPROVED_GOLD_OUTBOUND
    assert not unapproved, (
        f"{path.relative_to(REPO_ROOT)} imports unapproved modules {sorted(unapproved)}; "
        "extend APPROVED_GOLD_OUTBOUND only with a reviewed NR-03/NR-05 boundary"
    )


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_gold_never_imports_protected_core_directly(path: Path) -> None:
    offending = _imported_modules(path) & PROTECTED_CORE_MODULES
    assert not offending, (
        f"{path.relative_to(REPO_ROOT)} imports protected-core module(s) "
        f"{sorted(offending)}; NR-03 allows only a narrow typed adapter point"
    )


def test_whitelist_contains_no_protected_core_module() -> None:
    assert not APPROVED_GOLD_OUTBOUND & PROTECTED_CORE_MODULES


def test_knowledge_and_admission_owners_do_not_import_each_other() -> None:
    """Patch 6.5 exists so §21 and §22 owners never form a module cycle.

    The knowledge-snapshot owner references gate decisions and the admission
    owner references a committed snapshot boundary through hash-bound refs and
    injected resolver protocols declared in ``contracts.py``. A direct import
    between the two would reintroduce the cycle that Patch 6.5 removed.
    """

    knowledge = GOLD_PACKAGE / "knowledge.py"
    admission = GOLD_PACKAGE / "admission.py"
    if knowledge.exists():
        assert f"{GOLD_MODULE_PREFIX}.admission" not in _imported_modules(knowledge)
        assert "from .admission" not in knowledge.read_text(encoding="utf-8")
    if admission.exists():
        assert f"{GOLD_MODULE_PREFIX}.knowledge" not in _imported_modules(admission)
        assert "from .knowledge" not in admission.read_text(encoding="utf-8")
