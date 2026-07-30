"""Acceptance-boundary tripwire (Patch 1 artifact, added in Patch 6.5).

NR-06: tests, fixtures and mutation harnesses are an external acceptance layer.
Production code never imports them and never reads them as runtime
configuration. A requirement that exists only inside a test is not a production
contract.

The scan covers the whole ``synapse`` production package, not just Stage 4:
a bypass introduced anywhere else would weaken the same boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_PACKAGE = REPO_ROOT / "synapse"

FORBIDDEN_IMPORT_ROOTS = frozenset(
    {"pytest", "tests", "_pytest", "unittest.mock", "hypothesis"}
)
FORBIDDEN_PATH_FRAGMENTS = ("tests/fixtures", "tests\\fixtures", "tests/support")


def _python_sources(package: Path) -> list[Path]:
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in path.parts
    )


PRODUCTION_SOURCES = _python_sources(PRODUCTION_PACKAGE)


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module)
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_production_package_has_sources_to_inspect() -> None:
    assert len(PRODUCTION_SOURCES) > 50


@pytest.mark.parametrize(
    "path", PRODUCTION_SOURCES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_production_module_never_imports_the_acceptance_layer(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending = _imported_roots(tree) & FORBIDDEN_IMPORT_ROOTS
    assert not offending, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offending)}; NR-06 keeps tests "
        "and fixtures outside production semantics"
    )


@pytest.mark.parametrize(
    "path", PRODUCTION_SOURCES, ids=lambda p: str(p.relative_to(REPO_ROOT))
)
def test_production_module_never_reads_fixture_paths(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for fragment in FORBIDDEN_PATH_FRAGMENTS:
                assert fragment not in node.value, (
                    f"{path.relative_to(REPO_ROOT)} references {fragment!r}; production "
                    "must not read acceptance fixtures as runtime configuration"
                )
