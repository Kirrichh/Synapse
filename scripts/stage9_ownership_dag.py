#!/usr/bin/env python3
"""Report the dependency DAG that a Stage 9 decomposition of ``replay.py`` implies.

The point of this script is that the claim be *checkable*. A decomposition plan
that states "187 edges run this way and four run the other way" is an assertion
about code, and an assertion about code which nobody can re-derive is a number in
a document. So the group assignment lives here, in one table, and the edges are
counted from the module's own syntax tree rather than from anybody's reading.

Run it before moving a single line, and again after: the edges that cross a
boundary are the work, and the edges that run *backwards* — from the owner into
a group that is leaving, or between two groups that are leaving — are the ones
that must be gone before the move is legal under the architecture tripwire.

    python scripts/stage9_ownership_dag.py
    python scripts/stage9_ownership_dag.py --unassigned   # what the table misses

Nothing here decides policy. Which symbols belong in which group is a governance
question answered by the ownership map; this only says what the code currently
does about that answer.
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OWNER_MODULE = REPO_ROOT / "synapse" / "experiments" / "gold" / "replay.py"

#: Symbols that leave for the CVM adapter: the machine itself, the value
#: vocabulary it serializes under, and every read of CognitiveVM internals.
CVM_ADAPTER = {
    "CognitiveVMReplayAdapter",
    "CANONICAL_VM_SCALARS",
    "require_canonical_vm_value",
    "require_canonical_vm_state",
    "encode_recorded_result",
    "decode_recorded_result",
    "_machine_value_bytes",
    "_snapshot_bytes_of",
    "_is_back_edge",
    "_BACK_EDGE_OPCODES",
    "_MAX_VM_VALUE_DEPTH",
    "_MAX_VM_VALUE_NODES",
    "_FRAME_FIELDS",
    "_NON_VALUE_VM_FIELDS",
    "_ADAPTER_PROFILE",
}

#: Symbols that leave for the execution adapter: the concrete channel, the
#: transition driver and the raw execution facts it produces. Note what is *not*
#: here — the driver reports facts, and the owner turns them into a status.
EXECUTION_ADAPTER = {
    "RecordedActivityChannel",
    "_TransitionRun",
    "_drive_one_behavior",
    "_check_execution_contract",
}

#: Symbols that leave for the manifest adapter: taking the reference execution,
#: materialising snapshots and writing the durable records. The rules about what
#: a manifest must describe stay with the owner.
MANIFEST_ADAPTER = {
    "_machines_from_manifest",
    "replay_snapshot_ref",
}

GROUPS = {
    "cvm": CVM_ADAPTER,
    "execution": EXECUTION_ADAPTER,
    "manifest": MANIFEST_ADAPTER,
}


def group_of(name: str) -> str:
    for label, members in GROUPS.items():
        if name in members:
            return label
    return "owner"


def definitions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every name this module binds at the top level, and what binds it."""

    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found[target.id] = node
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            found[node.target.id] = node
    return found


def references(node: ast.AST, known: set[str], own_name: str) -> set[str]:
    """Which other top-level names this definition mentions."""

    used: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in known and sub.id != own_name:
            used.add(sub.id)
        elif (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id in known
        ):
            used.add(sub.value.id)
    return used


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--unassigned",
        action="store_true",
        help="list the definitions the table leaves with the owner",
    )
    parser.add_argument("--module", default=str(OWNER_MODULE))
    arguments = parser.parse_args()

    path = pathlib.Path(arguments.module)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    defined = definitions(tree)
    known = set(defined)

    functions = sum(
        1 for node in defined.values() if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    classes = sum(1 for node in defined.values() if isinstance(node, ast.ClassDef))
    assignments = len(defined) - functions - classes

    try:
        shown = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        shown = path
    print(f"module: {shown}")
    print(f"lines: {len(path.read_text(encoding='utf-8').splitlines())}")
    print(f"top-level definitions: {len(defined)}")
    print(f"  functions: {functions}")
    print(f"  classes: {classes}")
    print(f"  assignments: {assignments}")
    print()

    edges: collections.Counter[tuple[str, str]] = collections.Counter()
    backwards: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for name, node in defined.items():
        source = group_of(name)
        for target in references(node, known, name):
            sink = group_of(target)
            if source == sink:
                continue
            edges[(source, sink)] += 1
            # An edge is backwards when it would become an import the tripwire
            # forbids: the owner reaching into a group that is leaving, or one
            # leaving group reaching into another.
            if source == "owner" or (sink != "owner" and source != sink):
                backwards[(source, sink)].add(f"{name} -> {target}")

    print("edges between groups")
    for (source, sink), count in sorted(edges.items(), key=lambda item: -item[1]):
        note = ""
        if source == "owner":
            note = "   OWNER -> ADAPTER (forbidden)"
        elif sink != "owner":
            note = "   ADAPTER -> SIBLING (forbidden)"
        print(f"  {source:10s} -> {sink:10s} {count:5d}{note}")
    print()

    if backwards:
        print("edges that must be gone before the move is legal")
        for (source, sink), items in sorted(backwards.items()):
            print(f"  {source} -> {sink}:")
            for item in sorted(items):
                print(f"    {item}")
        print()

    if arguments.unassigned:
        print("definitions the table leaves with the owner")
        for name in sorted(name for name in defined if group_of(name) == "owner"):
            print(f"  {name}")

    return 1 if backwards else 0


if __name__ == "__main__":
    sys.exit(main())
