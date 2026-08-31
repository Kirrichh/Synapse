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

import json

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"
CLI_MODULE = REPO_ROOT / "synapse" / "cli.py"
OWNERSHIP_MANIFEST = REPO_ROOT / "governance" / "stage4_ownership_v1.json"

#: The §12 composition roots. The canonical entrypoint may reach Stage 4, but
#: only through one of these: a root is the module allowed to touch every side,
#: so entering past one means the entrypoint inherited edges no single owner is
#: allowed to have.
COMPOSITION_ROOTS = frozenset(
    json.loads(OWNERSHIP_MANIFEST.read_text(encoding="utf-8"))["composition_roots"]
)

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


def _gold_modules_imported_by(path: Path) -> set[str]:
    """The gold modules one file names, as manifest keys.

    Relative imports count. ``synapse/cli.py`` reaches its own package with a
    leading dot, and a scan that only understood absolute names would report
    the canonical entrypoint as reaching nothing — passing while the thing it
    checks is false.
    """

    prefix = "synapse.experiments.gold."
    package = ".".join(("synapse", *path.relative_to(REPO_ROOT / "synapse").parts[:-1]))
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.level == 0:
                names.append(node.module)
            elif node.level == 1:
                names.append(f"{package}.{node.module}")
    for name in names:
        if name.startswith(prefix):
            modules.add(name[len(prefix):].replace(".", "/") + ".py")
    return modules


def test_stage4_is_reachable_from_the_canonical_entrypoint() -> None:
    """The other half of NR-01/NR-02, and the half that was missing.

    Every check above asserts the *absence* of a competing entrypoint. None of
    them can notice the absence of the only correct one — which is how Stage 4
    spent eleven patches unreachable from ``synapse.cli.main()`` with this file
    green. A subsystem no production caller can start is not governed by the
    single lifecycle; it is outside it.
    """

    reached = _gold_modules_imported_by(CLI_MODULE)
    assert reached, (
        "synapse/cli.py reaches no Stage 4 module; NR-01/NR-02 require the "
        "production call to come from the single lifecycle, and a subsystem "
        "nothing can start does not satisfy that by being quiet"
    )


def test_stage4_is_entered_only_through_a_declared_composition_root() -> None:
    """Entering past a root would hand the entrypoint an owner's private edges."""

    outside = sorted(_gold_modules_imported_by(CLI_MODULE) - COMPOSITION_ROOTS)
    assert not outside, (
        f"synapse/cli.py enters Stage 4 through {outside}, which are not §12 "
        "composition roots; the entrypoint may only reach a root"
    )
