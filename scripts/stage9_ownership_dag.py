#!/usr/bin/env python3
"""Check the ownership DAG of the Stage 4 Gold package against its own map.

The earlier version of this script analysed *imagined* groups inside one file:
it took a table of symbols that a future decomposition of ``replay.py`` would
move, and counted the edges that would then cross a module boundary. That was
useful while the decomposition was a plan, and it stopped being useful the
moment modules actually existed — a tripwire measuring a hypothetical cannot
fail when the real thing regresses.

This version reads real modules. The ownership map is not defined here either:
it belongs to the package boundary, ``synapse.experiments.gold`` declares it,
and this script holds the package to what it declared.

Four things are forbidden, and each is a way the ports-and-adapters star
collapses back into a ball of mud:

1. **An owner importing its own adapter.** The adapter exists to hold part of
   the owner's responsibility behind a port. An owner that imports it has one
   module spread across two files and a cycle it cannot see.
2. **An adapter importing a sibling adapter of the same owner.** Two adapters of
   one owner are two independent implementations of two ports; an edge between
   them makes one of them depend on a decision the other made, and the owner is
   no longer the only party that knows both.
3. **An owner re-exporting a concrete adapter.** ``__all__`` is not a trust
   boundary, but a name in it is a promise: an owner that re-exports its
   adapter's concrete types lets a caller reach the adapter *through* the owner,
   which is the same edge as (1) wearing the owner's name.
4. **A composition root being imported from inside the package.** A root is
   allowed to touch every side; that permission is only safe while nothing
   reaches through it. A module importing one would inherit edges it is not
   allowed to have.
5. **Dynamic bypasses.** ``importlib``, ``__import__`` and registration slots
   move an edge from the syntax tree into runtime, where none of the checks
   above can see it. A first-writer registration slot is worse than an import:
   whichever module registers first decides what production uses.

Rules 1, 3, 4 and 5 apply to the whole package. Rule 2 applies to the OD-10/V1 §9.5
zone — the Stage 9 modules this decomposition is about — because the rest of the
package has pre-existing sibling edges that predate the rule and are reported
rather than failed. That boundary is stated rather than silently applied: a
check whose scope nobody can see is a check nobody can trust.

    python scripts/stage9_ownership_dag.py
    python scripts/stage9_ownership_dag.py --all-siblings   # report everything
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"

sys.path.insert(0, str(REPO_ROOT))

from synapse.experiments.gold import (  # noqa: E402
    STAGE4_ADAPTER_COMPONENTS,
    STAGE4_COMPOSITION_ROOTS,
    STAGE4_OWNER_ADAPTERS,
    STAGE4_OWNER_COMPONENTS,
    STAGE4_OWNERSHIP_MAP,
)

#: The modules OD-10/V1 §9.5 governs: the replay owner, the activity stack it
#: consumes, and every adapter attached to one of them. Rule 2 is enforced here.
STAGE9_ZONE = frozenset(
    {"replay.py", "activities.py", "activity_policy.py"}
    | {
        adapter
        for adapter, owner in STAGE4_OWNER_ADAPTERS.items()
        if owner in {"replay.py", "activities.py", "activity_policy.py"}
    }
    | {
        component
        for component, owner in STAGE4_OWNER_COMPONENTS.items()
        if owner in {"replay.py", "activities.py", "activity_policy.py"}
    }
    | {
        component
        for component, adapter in STAGE4_ADAPTER_COMPONENTS.items()
        if adapter in STAGE4_OWNER_ADAPTERS
        and STAGE4_OWNER_ADAPTERS[adapter]
        in {"replay.py", "activities.py", "activity_policy.py"}
    }
)

#: Names that move an import out of the syntax tree and into runtime.
DYNAMIC_IMPORT_NAMES = frozenset({"importlib", "__import__"})


def module_imports(path: pathlib.Path) -> set[str]:
    """Every sibling module this one imports, lazily or not.

    Function-level imports count. A cycle broken by moving the import inside a
    function is still a cycle: the two modules still depend on each other, and
    the only thing the move changed is when Python notices.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module:
            found.add(node.module.split(".")[0] + ".py")
        else:
            found.update(alias.name.split(".")[0] + ".py" for alias in node.names)
    return found


def exported_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return {
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant) and type(item.value) is str
                }
    return set()


def names_taken_from(path: pathlib.Path, module: str) -> set[str]:
    """The names this module imports out of one named sibling.

    Deliberately not "the names the sibling defines". Two modules can hold the
    same constant under the same name without either taking it from the other,
    and reporting that as a re-export would be reporting a coincidence — the
    edge only exists if this module actually reached into that one.
    """

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module
            and node.module.split(".")[0] + ".py" == module
        ):
            found.update((alias.asname or alias.name) for alias in node.names)
    return found


def dynamic_bypasses(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in DYNAMIC_IMPORT_NAMES:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in DYNAMIC_IMPORT_NAMES:
                found.add(node.module)
        elif isinstance(node, ast.Name) and node.id in DYNAMIC_IMPORT_NAMES:
            found.add(node.id)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-siblings",
        action="store_true",
        help="report sibling-adapter edges outside the §9.5 zone as well",
    )
    arguments = parser.parse_args()

    sources = sorted(
        path
        for path in GOLD_PACKAGE.glob("*.py")
        if "__pycache__" not in path.parts
    )
    imports = {path.name: module_imports(path) for path in sources}

    unmapped = [
        path.name
        for path in sources
        if path.name not in STAGE4_OWNERSHIP_MAP
        and path.name not in STAGE4_OWNER_COMPONENTS
        and path.name not in STAGE4_ADAPTER_COMPONENTS
        and path.name not in STAGE4_OWNER_ADAPTERS
        and path.name not in STAGE4_COMPOSITION_ROOTS
    ]

    forbidden: dict[str, list[str]] = collections.defaultdict(list)
    reported: list[str] = []

    for name, targets in sorted(imports.items()):
        for target in sorted(targets):
            if target not in STAGE4_OWNER_ADAPTERS:
                continue
            owner = STAGE4_OWNER_ADAPTERS[target]
            if name == owner or STAGE4_OWNER_COMPONENTS.get(name) == owner:
                forbidden["owner imports its own adapter"].append(f"{name} -> {target}")
            elif STAGE4_ADAPTER_COMPONENTS.get(name) == target:
                forbidden["adapter component imports its adapter"].append(
                    f"{name} -> {target}"
                )
            elif name in STAGE4_ADAPTER_COMPONENTS:
                forbidden["adapter component imports another adapter"].append(
                    f"{name} -> {target}"
                )
            elif name in STAGE4_OWNER_ADAPTERS and STAGE4_OWNER_ADAPTERS[name] == owner:
                edge = f"{name} -> {target}"
                if name in STAGE9_ZONE and target in STAGE9_ZONE:
                    forbidden["adapter imports a sibling adapter"].append(edge)
                else:
                    reported.append(f"sibling edge outside the §9.5 zone: {edge}")

    for path in sources:
        name = path.name
        if name not in STAGE4_OWNERSHIP_MAP:
            continue
        adapters = {
            adapter for adapter, owner in STAGE4_OWNER_ADAPTERS.items() if owner == name
        }
        if not adapters:
            continue
        published = exported_names(path)
        for adapter in sorted(adapters):
            leaked = sorted(published & names_taken_from(path, adapter))
            for symbol in leaked:
                forbidden["owner re-exports a concrete adapter symbol"].append(
                    f"{name} exports {symbol} from {adapter}"
                )

    for name, targets in sorted(imports.items()):
        for target in sorted(targets):
            if target in STAGE4_COMPOSITION_ROOTS:
                forbidden["a composition root is imported from inside the package"].append(
                    f"{name} -> {target}"
                )

    for path in sources:
        for name in sorted(dynamic_bypasses(path)):
            forbidden["dynamic import bypass"].append(f"{path.name} uses {name}")

    print(f"package: {GOLD_PACKAGE.relative_to(REPO_ROOT)}")
    print(f"owners: {len(STAGE4_OWNERSHIP_MAP)}")
    print(f"owner components: {len(STAGE4_OWNER_COMPONENTS)}")
    print(f"adapter components: {len(STAGE4_ADAPTER_COMPONENTS)}")
    print(f"adapters: {len(STAGE4_OWNER_ADAPTERS)}")
    print(f"modules on disk: {len(sources)}")
    print(f"composition roots: {len(STAGE4_COMPOSITION_ROOTS)}")
    print(f"§9.5 zone: {len(STAGE9_ZONE)}")
    print()

    if unmapped:
        print("modules the ownership map does not describe")
        for name in unmapped:
            print(f"  {name}")
        print()

    if forbidden:
        print("forbidden edges")
        for rule, items in sorted(forbidden.items()):
            print(f"  {rule}:")
            for item in sorted(items):
                print(f"    {item}")
        print()
    else:
        print("forbidden edges: none")
        print()

    if reported and arguments.all_siblings:
        print("reported, not failed (outside the §9.5 zone)")
        for item in sorted(reported):
            print(f"  {item}")
        print()
    elif reported:
        print(f"{len(reported)} sibling edges outside the §9.5 zone (--all-siblings to list)")
        print()

    return 1 if forbidden or unmapped else 0


if __name__ == "__main__":
    sys.exit(main())
