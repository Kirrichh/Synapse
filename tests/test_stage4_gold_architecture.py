"""Stage 4 package-ownership tripwire (Patch 1 artifact, added in Patch 6.5).

NR-04 forbids a new monolith: Stage 4 responsibilities are distributed across
the §12 ownership map and never collapse into ``gold_runner.py`` or any other
god-file. The blocking criterion is a file leaving its single normative
responsibility, not a line count.

This test locks three things:
  1. every module in the gold package is a declared §12 owner, so a rogue or
     merged file fails review automatically;
  2. the one sanctioned exception — an adapter holding part of a declared
     owner's responsibility, split out under the repository's file-size rule —
     really is one: it attaches to a real owner, depends on it, and is never
     depended on by it;
  3. ``contracts.py`` stays a pure vocabulary/identity boundary — it performs no
     I/O and holds no domain state, as its own module docstring asserts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"

# §12 recommended ownership map. Each entry is one normative responsibility.
# Patch 6.5 adds no new owner: it extends the existing contracts/canonicalization
# vocabulary so that knowledge.py (§21) and admission.py (§22) can be built as
# separate patches without importing each other.
STAGE4_OWNERSHIP_MAP = {
    "__init__.py": "package boundary",
    "contracts.py": "common IDs, enums, schema envelopes",
    "behavior.py": "SynapseBehaviorUnit, BehaviorBlob, BehaviorManifest",
    "bindings.py": "Python/Document/Requirement binding resolution",
    "canonicalization.py": "canonical profiles, ContentKey, migration relations",
    "library.py": "immutable CAS, index metadata, collision/corruption checks",
    "provenance.py": "BehaviorAttestation and trusted attester boundary",
    "taint.py": "SourceTaintProfile, TaintAuthorityDecision, derivation records",
    "lifecycle.py": "append-only lifecycle, supersession, revocation",
    "retrieval.py": "queries, candidates, ranking, conflicts, decisions",
    "compatibility.py": "CompatibilityContext/Evidence/Decision, revalidation",
    "knowledge.py": "RepositoryKnowledgeSnapshot, AtomicSnapshotBoundary",
    "admission.py": "ingestion/publication/retrieval/consumption gates",
    "replay.py": "CognitiveVM integration and ReplayResult",
    "activities.py": "governed external activities and recorded results",
    "intent.py": "IntentCandidate",
    "planning.py": "OperationPlanCandidate, PlanAuthorityDecision",
    "context.py": "typed worker context and rendering/envelope",
    "runner.py": "multi-attempt Gold run controller",
    "verification.py": "C1/C2 adapter coordination and FULL predicate",
    "outcome.py": "StructuredOutcome",
    "publication.py": "PublicationAuthorityDecision execution",
    "lineage.py": "DAG nodes/edges and reconstruction",
    "telemetry.py": "canonical records, completeness, reconciliation",
    "events.py": "read-only EventStream",
    "paired.py": "Baseline/Gold execution harness",
    "persistence.py": "run manifests, recovery and integrity",
}

# Adapters attached to a declared owner. An adapter is *not* a new §12 owner and
# carries no responsibility of its own: it holds part of one owner's normative
# responsibility, placed in its own file under the repository owner's standing
# decision that large modules are extended through adapters rather than grown in
# place.
#
# There is deliberately no line count here. NR-04 states that no numeric LOC
# threshold is introduced and that the blocking criterion is a file leaving its
# single normative responsibility. An earlier revision of this comment cited
# "past 2500 lines" as though it were the rule; it is not, and quoting an
# invented threshold in a tripwire's own justification is how a house convention
# gets mistaken for a normative requirement by the next reader.
#
# What the checks below actually enforce is the part NR-04 does care about: an
# adapter must attach to a real owner, depend on it, and never be depended on by
# it. A file that wanted its own responsibility would fail to be an adapter and
# would have to be argued as a §12 owner on its own merits.
STAGE4_OWNER_ADAPTERS = {
    "point_of_use.py": "admission.py",
    "gate_findings.py": "admission.py",
}

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


@pytest.mark.parametrize("path", _python_sources(GOLD_PACKAGE), ids=lambda p: p.name)
def test_every_gold_module_is_a_declared_owner_or_an_adapter(path: Path) -> None:
    assert path.name in STAGE4_OWNERSHIP_MAP or path.name in STAGE4_OWNER_ADAPTERS, (
        f"{path.name} is neither a §12 owner nor a declared adapter of one; NR-04 "
        "forbids adding responsibilities outside the declared owners"
    )


def test_ownership_map_declares_distinct_responsibilities() -> None:
    responsibilities = list(STAGE4_OWNERSHIP_MAP.values())
    assert len(responsibilities) == len(set(responsibilities))


def test_no_adapter_is_also_registered_as_an_owner() -> None:
    """The two lists mean different things, so a file may appear in only one.

    A module in both would claim a §12 responsibility *and* the exemption from
    needing one — which is precisely the loophole an adapter list could become.
    """

    assert not set(STAGE4_OWNER_ADAPTERS) & set(STAGE4_OWNERSHIP_MAP)


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

    assert f"from .{owner_stem} import" in adapter_source, (
        f"{adapter} does not depend on {owner_name}; an adapter holds part of its "
        "owner's responsibility, so it cannot stand apart from it"
    )
    assert f"from .{adapter_stem} import" not in owner_source, (
        f"{owner_name} imports {adapter}; the dependency must run one way or the "
        "two are one module in two files"
    )


def test_gold_package_does_not_collapse_into_a_single_module() -> None:
    present = {path.name for path in _python_sources(GOLD_PACKAGE)}
    present.discard("__init__.py")
    # Every implemented patch keeps its own owner; a merged file would show up
    # as a shrinking module set against the implemented contours.
    assert len(present) >= 11, (
        "implemented Stage 4 contours must each keep their own owner module"
    )


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
