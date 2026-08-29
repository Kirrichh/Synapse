"""Stage 4 package-ownership tripwire (Patch 1 artifact, added in Patch 6.5).

Stage 4 responsibilities are distributed across the §12 ownership map and never
collapse into ``gold_runner.py`` or another god-file.  The repository file rule
makes responsibility blocking and size a review trigger; NR-04 remains the
separate specification-conformance rule.

This test locks four things:
  1. every module in the gold package is a declared §12 owner, an internal
     division of one, an adapter, or a composition root;
  2. an adapter holding part of a declared
     owner's responsibility, split out under the repository's file-size rule —
     really is one: it attaches to a real owner, depends on it, and is never
     depended on by it;
  3. an internal owner component remains part of its declared logical owner and
     is not laundered into a new §12 owner or concrete adapter;
  4. ``contracts.py`` stays a pure vocabulary/identity boundary — it performs no
     I/O and holds no domain state, as its own module docstring asserts.
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"
OWNERSHIP_MANIFEST = REPO_ROOT / "governance" / "stage4_ownership_v1.json"

# The map is read from the same versioned, non-production manifest as the DAG
# script.  Keeping it out of the package import path prevents acceptance-only
# metadata from becoming executable product configuration.
_OWNERSHIP = json.loads(OWNERSHIP_MANIFEST.read_text(encoding="utf-8"))
STAGE4_OWNERSHIP_MAP = _OWNERSHIP["owners"]
STAGE4_OWNER_COMPONENTS = _OWNERSHIP["owner_components"]
STAGE4_ADAPTER_COMPONENTS = _OWNERSHIP["adapter_components"]
STAGE4_OWNER_ADAPTERS = _OWNERSHIP["owner_adapters"]
STAGE4_COMPOSITION_ROOTS = _OWNERSHIP["composition_roots"]

# contracts.py declares "It performs no I/O". These roots would contradict that.
IO_MODULE_ROOTS = frozenset(
    {"os", "pathlib", "sqlite3", "shutil", "tempfile", "io", "subprocess", "socket"}
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
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _relative_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module:
            imports.add(node.module.split(".", 1)[0] + ".py")
        else:
            imports.update(alias.name.split(".", 1)[0] + ".py" for alias in node.names)
    return imports


def _private_owner_uses(adapter_path: Path, owner_stem: str) -> tuple[set[str], set[str]]:
    """Return statically visible private owner use and dynamic bypasses."""

    tree = ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))
    owner_types: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module == owner_stem
        ):
            owner_types.update(alias.asname or alias.name for alias in node.names)
            direct.update(alias.name for alias in node.names if alias.name.startswith("_"))

    owner_fields: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and isinstance(node.target.value, ast.Name)
            and node.target.value.id == "self"
            and isinstance(node.annotation, ast.Name)
            and node.annotation.id in owner_types
        ):
            owner_fields.add(node.target.attr)

    actual = set(direct)
    dynamic: set[str] = set()
    for scope in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        scope_nodes = tuple(ast.walk(scope))
        owner_variables = {
            argument.arg
            for argument in (*scope.args.posonlyargs, *scope.args.args, *scope.args.kwonlyargs)
            if isinstance(argument.annotation, ast.Name)
            and argument.annotation.id in owner_types
        }
        owner_variables.update(
            node.left.args[0].id
            for node in scope_nodes
            if isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Call)
            and isinstance(node.left.func, ast.Name)
            and node.left.func.id == "type"
            and len(node.left.args) == 1
            and isinstance(node.left.args[0], ast.Name)
            and any(
                isinstance(item, ast.Name) and item.id in owner_types
                for item in node.comparators
            )
        )

        def is_owner_expression(value: ast.AST) -> bool:
            return (
                isinstance(value, ast.Name)
                and value.id in owner_variables
            ) or (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"
                and value.attr in owner_fields
            )

        for node in scope_nodes:
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith("_")
                and is_owner_expression(node.value)
            ):
                actual.add(node.attr)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and is_owner_expression(node.args[0])
                and isinstance(node.args[1], ast.Constant)
                and type(node.args[1].value) is str
                and node.args[1].value.startswith("_")
            ):
                dynamic.add(node.args[1].value)
    return actual, dynamic


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_every_gold_module_has_one_declared_ownership_role(path: Path) -> None:
    assert (
        path.name in STAGE4_OWNERSHIP_MAP
        or path.name in STAGE4_OWNER_COMPONENTS
        or path.name in STAGE4_ADAPTER_COMPONENTS
        or path.name in STAGE4_OWNER_ADAPTERS
        or path.name in STAGE4_COMPOSITION_ROOTS
    ), (
        f"{path.name} is not an owner, internal component, adapter, or root; NR-04 "
        "forbids adding responsibilities outside the declared ownership topology"
    )


def test_ownership_map_declares_distinct_responsibilities() -> None:
    responsibilities = list(STAGE4_OWNERSHIP_MAP.values())
    assert len(responsibilities) == len(set(responsibilities))


def test_ownership_categories_do_not_overlap() -> None:
    """The two lists mean different things, so a file may appear in only one.

    A module in both would claim a §12 responsibility *and* the exemption from
    needing one — which is precisely the loophole an adapter list could become.
    """

    categories = (
        set(STAGE4_OWNERSHIP_MAP), set(STAGE4_OWNER_COMPONENTS),
        set(STAGE4_ADAPTER_COMPONENTS), set(STAGE4_OWNER_ADAPTERS),
        set(STAGE4_COMPOSITION_ROOTS),
    )
    assert all(not left & right for index, left in enumerate(categories)
               for right in categories[index + 1:])


@pytest.mark.parametrize("component", sorted(STAGE4_OWNER_COMPONENTS), ids=lambda name: name)
def test_an_internal_component_belongs_to_one_real_owner(component: str) -> None:
    owner = STAGE4_OWNER_COMPONENTS[component]
    assert owner in STAGE4_OWNERSHIP_MAP
    component_path, owner_path = GOLD_PACKAGE / component, GOLD_PACKAGE / owner
    assert component_path.exists() and owner_path.exists()
    component_stem = component_path.stem
    assert f"from .{component_stem} import" in owner_path.read_text(encoding="utf-8")
    forbidden = {owner, *STAGE4_OWNER_ADAPTERS, *STAGE4_COMPOSITION_ROOTS}
    assert not _relative_imports(component_path) & forbidden
    allowed_importers = {
        owner,
        *(name for name, attached in STAGE4_OWNER_COMPONENTS.items() if attached == owner),
        *(name for name, attached in STAGE4_OWNER_ADAPTERS.items() if attached == owner),
        *(name for name, attached in STAGE4_COMPOSITION_ROOTS.items() if attached == owner),
    }
    actual_importers = {
        path.name for path in _python_sources(GOLD_PACKAGE)
        if component in _relative_imports(path)
    }
    assert actual_importers <= allowed_importers
    tree = ast.parse(component_path.read_text(encoding="utf-8"), filename=str(component_path))
    assert not _imported_roots(tree) & IO_MODULE_ROOTS


@pytest.mark.parametrize(
    "component",
    sorted(STAGE4_ADAPTER_COMPONENTS),
    ids=lambda name: name,
)
def test_an_adapter_component_belongs_to_one_concrete_adapter(component: str) -> None:
    adapter = STAGE4_ADAPTER_COMPONENTS[component]
    assert adapter in STAGE4_OWNER_ADAPTERS
    component_path, adapter_path = GOLD_PACKAGE / component, GOLD_PACKAGE / adapter
    assert component_path.exists() and adapter_path.exists()
    component_stem = component_path.stem
    adapter_source = adapter_path.read_text(encoding="utf-8")
    assert (
        f"from . import {component_stem}" in adapter_source
        or f"from .{component_stem} import" in adapter_source
    )
    forbidden = {adapter, *STAGE4_OWNER_ADAPTERS, *STAGE4_COMPOSITION_ROOTS}
    assert not _relative_imports(component_path) & forbidden
    actual_importers = {
        path.name
        for path in _python_sources(GOLD_PACKAGE)
        if component in _relative_imports(path)
    }
    assert actual_importers == {adapter}
    tree = ast.parse(
        component_path.read_text(encoding="utf-8"),
        filename=str(component_path),
    )
    assert not _imported_roots(tree) & IO_MODULE_ROOTS


@pytest.mark.parametrize("adapter", sorted(STAGE4_OWNER_ADAPTERS), ids=lambda name: name)
def test_an_adapter_attaches_to_a_real_owner_in_one_direction(adapter: str) -> None:
    """What makes a file an adapter rather than an undeclared owner.

    Three conditions, and all of them are load-bearing. The owner must itself be
    a §12 owner, so an adapter cannot attach to another adapter and launder a new
    responsibility through the chain. The adapter must import its owner, because
    a file that shares nothing with the owner is not part of the owner's
    responsibility — it is a separate one wearing the label. And the owner must
    not import the adapter, because a cycle would mean the two are a single
    module spread over two files: the size rule satisfied on paper and the
    normative boundary blurred in fact.
    """

    owner_name = STAGE4_OWNER_ADAPTERS[adapter]
    assert owner_name in STAGE4_OWNERSHIP_MAP, (
        f"{adapter} attaches to {owner_name}, which is not a §12 owner"
    )

    adapter_path = GOLD_PACKAGE / adapter
    owner_path = GOLD_PACKAGE / owner_name
    assert adapter_path.exists() and owner_path.exists()

    adapter_source = adapter_path.read_text(encoding="utf-8")
    owner_source = owner_path.read_text(encoding="utf-8")
    owner_stem = owner_path.stem
    adapter_stem = adapter_path.stem

    assert (
        f"from .{owner_stem} import" in adapter_source
        or f"from . import {owner_stem}" in adapter_source
    ), (
        f"{adapter} does not depend on {owner_name}; an adapter holds part of its "
        "owner's responsibility, so it cannot stand apart from it"
    )
    assert f"from .{adapter_stem} import" not in owner_source, (
        f"{owner_name} imports {adapter}; the dependency must run one way or the "
        "two are one module in two files"
    )


@pytest.mark.parametrize(
    ("adapter", "owner"),
    (
        ("behavior_program_artifacts.py", "behavior.py"),
        ("library_program_artifacts.py", "library.py"),
    ),
)
def test_private_adapter_seam_is_bilateral_and_exact(adapter: str, owner: str) -> None:
    """FS-04: actual AST use equals both explicit seam declarations."""

    adapter_path = GOLD_PACKAGE / adapter
    owner_path = GOLD_PACKAGE / owner
    adapter_module_name = f"synapse.experiments.gold.{adapter_path.stem}"
    owner_module_name = f"synapse.experiments.gold.{owner_path.stem}"
    adapter_module = importlib.import_module(adapter_module_name)
    owner_module = importlib.import_module(owner_module_name)

    declared = adapter_module.ADAPTER_PRIVATE_SEAM[owner_module_name]
    permitted = owner_module.ADAPTER_PRIVATE_EXPORTS[adapter_module_name]
    actual, dynamic = _private_owner_uses(adapter_path, owner_path.stem)
    assert not dynamic, f"{adapter} dynamically reaches owner private names {sorted(dynamic)}"
    assert actual == set(declared) == set(permitted)

    exported = set(getattr(adapter_module, "__all__", ()))
    assert not exported & actual, f"{adapter} re-exports owner private names"


def test_contracts_module_performs_no_io() -> None:
    path = GOLD_PACKAGE / "contracts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending = _imported_roots(tree) & IO_MODULE_ROOTS
    assert not offending, (
        f"contracts.py imports {sorted(offending)}; it is a vocabulary and identity "
        "boundary that performs no I/O"
    )


def test_contracts_module_holds_no_mutable_domain_state() -> None:
    """A vocabulary module may hold constant lookup tables, never a store.

    A pre-populated private mapping (``_ROLE_REASON_MATRIX``) is a frozen
    constant read through validated accessors. An *empty* module-level
    container, or any public mutable global, is a registry or cache that gets
    filled at runtime — that is domain state and belongs to its §12 owner.
    """

    path = GOLD_PACKAGE / "contracts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, (ast.List, ast.Dict, ast.Set)):
            continue
        empty = (
            not value.keys if isinstance(value, ast.Dict) else not value.elts
        )
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if empty or not target.id.startswith("_"):
                offending.append(target.id)
    assert not offending, (
        f"contracts.py declares mutable module state {sorted(offending)}; "
        "Stage 4 state belongs to its §12 owner, not the shared vocabulary"
    )


def test_swebench_gold_runner_stays_a_single_attempt_c1_adapter() -> None:
    path = REPO_ROOT / "synapse" / "experiments" / "swebench" / "gold_runner.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    stage4_domain_symbols = {
        name
        for name in defined
        if name.startswith(
            (
                "RepositoryKnowledgeSnapshot",
                "AtomicSnapshotBoundary",
                "GateDecision",
                "BehaviorLibrary",
                "RetrievalDecision",
                "PublicationTransaction",
            )
        )
    }
    assert not stage4_domain_symbols, (
        f"gold_runner.py defines Stage 4 domain symbols {sorted(stage4_domain_symbols)}; "
        "NR-05 keeps it a single-attempt C1 adapter"
    )


def test_the_ownership_map_is_versioned_governance_not_production_state() -> None:
    """One review artifact drives scripts/tests and never executes on import."""

    import synapse.experiments.gold as package

    assert _OWNERSHIP["schema_version"] == "synapse.governance.stage4-ownership/v1"
    assert _OWNERSHIP["policy_version"] == "Synapse file responsibility rule 1.1"
    source = Path(__file__).read_text(encoding="utf-8")
    for name in (
        "STAGE4_OWNERSHIP_MAP", "STAGE4_OWNER_COMPONENTS",
        "STAGE4_ADAPTER_COMPONENTS", "STAGE4_OWNER_ADAPTERS",
    ):
        assert f"{name} = {{" not in source, (
            f"{name} is defined in the acceptance layer again instead of the manifest"
        )
        assert not hasattr(package, name), f"production package executes governance-only {name}"


def test_the_ownership_dag_has_no_forbidden_edges() -> None:
    """The star topology, checked as code rather than as a claim in a document.

    Run as a subprocess on purpose. The script is the artifact a person runs by
    hand while moving modules around, and a check that re-implemented its rules
    here would be a second copy to keep true. What is asserted is what the
    script itself decides: a non-zero exit is a forbidden edge, and its output is
    the explanation.

    This is also how the check reaches CI. It runs inside a suite the Stage 4
    Gold workflow already executes, so no workflow change is needed for a
    regression to turn a pull request red.
    """

    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "stage9_ownership_dag.py")],
        capture_output=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
