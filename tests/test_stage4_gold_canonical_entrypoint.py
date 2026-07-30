"""Stage 4 canonical-entrypoint tripwire (Patch 1 artifact, added in Patch 6.5).

NR-02 keeps exactly one canonical program entrypoint:
``python -m synapse`` -> ``synapse.__main__`` -> ``synapse.cli.main()``.

Stage 4 may be reached from that lifecycle once the run controller lands, but it
must never grow its own ``__main__``, CLI, daemon or sidecar. This test asserts
the absence of a competing entrypoint, not the absence of wiring.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"

ENTRYPOINT_MODULE_NAMES = frozenset({"argparse", "optparse", "click", "typer"})
SERVER_MODULE_NAMES = frozenset(
    {"asyncio", "socket", "socketserver", "http.server", "uvicorn", "flask"}
)


def _python_sources(package: Path) -> list[Path]:
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in path.parts
    )


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module)
    return roots


def test_canonical_entrypoint_chain_is_intact() -> None:
    main_module = REPO_ROOT / "synapse" / "__main__.py"
    assert main_module.exists()
    source = main_module.read_text(encoding="utf-8")
    assert "cli" in source and "main" in source


def test_gold_package_has_no_module_entrypoint() -> None:
    assert not (GOLD_PACKAGE / "__main__.py").exists(), (
        "NR-02 forbids a separate Stage 4 entrypoint; production wiring starts "
        "only from python -m synapse -> synapse.cli.main()"
    )


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_gold_module_declares_no_script_block(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_source = ast.dump(node.test)
        assert "__main__" not in test_source, (
            f"{path.relative_to(REPO_ROOT)} declares a script block; Stage 4 has no "
            "standalone run path"
        )


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_gold_module_builds_no_cli_or_daemon(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = _imported_roots(tree)
    forbidden = roots & (ENTRYPOINT_MODULE_NAMES | SERVER_MODULE_NAMES)
    assert not forbidden, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(forbidden)}; NR-02 forbids a "
        "Stage 4 CLI, daemon or sidecar"
    )


def test_gold_package_exports_nothing_prematurely() -> None:
    """Patch 1 requires a public boundary without premature re-export."""

    init_source = (GOLD_PACKAGE / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(init_source)
    reexports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not reexports, (
        "gold/__init__.py must stay a package boundary; approved public "
        "entrypoints are exported only after Stage 4 stabilises (Patch 16)"
    )
