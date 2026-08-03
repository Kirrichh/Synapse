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


def test_the_point_of_use_adapter_depends_on_admission_and_never_the_reverse() -> None:
    """The size rule split this node out; the direction keeps it from becoming a cycle.

    ``point_of_use`` attaches to ``admission`` as an adapter, which only stays
    true while the arrow points one way. An import back would make the two a
    single owner spread over two files — the size rule satisfied on paper and
    nothing else.
    """

    admission = GOLD_PACKAGE / "admission.py"
    point_of_use = GOLD_PACKAGE / "point_of_use.py"
    assert point_of_use.exists(), "the point-of-use owner is part of Patch 8"
    assert f"{GOLD_MODULE_PREFIX}.point_of_use" not in _imported_modules(admission)
    assert "from .point_of_use" not in admission.read_text(encoding="utf-8")


#: Owners that exist before the gates in the §38 stage order. The gates consume
#: what these produce, so the dependency runs gates → owners. An owner importing
#: ``admission`` inverts it, and an inverted edge is worse than untidy: an
#: earlier contour then cannot be built, tested or changed without the later
#: one's vocabulary, and a cycle is one import away.
PRE_GATE_DOMAIN_OWNERS = ("taint.py", "compatibility.py", "lifecycle.py", "provenance.py")


@pytest.mark.parametrize("module_name", PRE_GATE_DOMAIN_OWNERS, ids=lambda name: name)
def test_a_pre_gate_owner_never_imports_the_admission_vocabulary(module_name: str) -> None:
    """The kill for the reverse edge, wherever the import is written.

    An earlier revision had ``compatibility.py`` and ``taint.py`` building
    ``CompatibilityFinding`` and ``TaintFinding`` directly, each with a
    function-local ``from .admission import ...``. Function-local is exactly why
    it went unnoticed: the module-level import scan above skips relative
    imports, so the edge existed and nothing saw it. The conversion now lives in
    ``gate_findings.py``, which is allowed to know both sides because knowing
    both sides is its entire job.
    """

    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet")
    source = path.read_text(encoding="utf-8")
    assert "from .admission import" not in source, (
        f"{module_name} imports the admission vocabulary; the projection belongs to an "
        "admission adapter, so the earlier owner does not depend on the later one"
    )
    assert f"{GOLD_MODULE_PREFIX}.admission" not in _imported_modules(path)


def test_the_findings_adapter_is_the_one_place_that_knows_both_sides() -> None:
    """The conversion must exist somewhere, and exactly one somewhere."""

    adapter = GOLD_PACKAGE / "gate_findings.py"
    assert adapter.exists(), "the findings adapter is what makes the direction fixable"
    source = adapter.read_text(encoding="utf-8")
    for owner in ("admission", "compatibility", "taint"):
        assert f"from .{owner} import" in source, (
            f"gate_findings.py must know {owner} to project between the two sides"
        )


def test_the_point_of_use_adapter_seam_matches_its_declaration() -> None:
    """A shared private helper is a decision, so it is written down and checked.

    Reimplementing the digest, timestamp, identifier and subject validators in
    the adapter is how two owners end up disagreeing about what a valid record
    is, so the seam is shared rather than duplicated. The cost is a non-public
    dependency, and the control for it is that the dependency is enumerated:
    this test fails if an import appears that ``ADAPTER_PRIVATE_SEAM`` does not
    name, or if a declared name stops being imported.
    """

    source = (GOLD_PACKAGE / "point_of_use.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_private: set[str] = set()
    declared: tuple[str, ...] = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "admission":
            for alias in node.names:
                if alias.name.startswith("_") or alias.name.isupper():
                    imported_private.add(alias.name)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ADAPTER_PRIVATE_SEAM"
            for target in node.targets
        ):
            declared = tuple(element.value for element in node.value.elts)

    assert declared, "the adapter must declare its private seam"
    assert imported_private == set(declared), (
        f"point_of_use.py takes {sorted(imported_private)} from admission but declares "
        f"{sorted(declared)}; keep ADAPTER_PRIVATE_SEAM and the imports in step"
    )


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


# Owners that deliver knowledge into a replay or a worker context. Patch 8's
# exit criterion is that no path to either bypasses the consumption gate. While
# these modules do not exist the criterion holds only vacuously, so the tripwire
# below is written now and starts biting the moment one of them lands.
CONSUMPTION_BARRIER_SYMBOLS = frozenset(
    {
        "require_consumption_admitted",
        "admitted_subject_refs",
        # The capability form of the same barrier: a delivery owner that accepts
        # an AdmittedKnowledgeHandle has crossed the gate by construction,
        # because nothing else can mint one.
        "validate_admitted_handle",
        "admit_for_consumption",
        # The point-of-use form. Stronger than the handle: a handle proves an
        # admission happened once, while CurrentAdmittedKnowledge proves it was
        # re-checked against the world at the moment of delivery, and names the
        # subject set the owner is allowed to act on.
        "require_current_admitted_handle",
        "validate_current_admitted_knowledge",
        "require_admitted_subjects",
    }
)
DELIVERY_OWNERS = ("replay.py", "activities.py", "context.py", "runner.py")

#: A delivery owner reading this instead of a handle is the A-01 bypass: the
#: legacy retrieval path crosses no gate, so its record is audit evidence and
#: never a consumption authority.
AUDIT_ONLY_RETRIEVAL_RECORD = "RetrievalResult"


@pytest.mark.parametrize("module_name", DELIVERY_OWNERS)
def test_delivery_owner_cannot_bypass_the_consumption_gate(module_name: str) -> None:
    """No path to replay or a worker may skip the §22 consumption barrier.

    A module that hands admitted knowledge to a replay or to a worker context
    must reach it through the consumption gate. Importing the admission owner is
    not enough — the barrier itself has to appear, because importing a gate and
    then not asking it is exactly the bypass this criterion forbids.

    The check is conditional only on the module's existence, never on its
    content: a delivery owner that lands without the barrier fails here rather
    than silently inheriting Patch 8's vacuous pass.
    """

    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet; the criterion is vacuous until it is")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert called & CONSUMPTION_BARRIER_SYMBOLS, (
        f"{module_name} delivers knowledge to replay or a worker without calling "
        f"one of {sorted(CONSUMPTION_BARRIER_SYMBOLS)}; Patch 8's exit criterion "
        "requires every such path to cross the consumption gate"
    )


@pytest.mark.parametrize("module_name", DELIVERY_OWNERS)
def test_delivery_owner_never_consumes_the_audit_only_retrieval_record(
    module_name: str,
) -> None:
    """A consumer must take the handle, not the retrieval record.

    ``select_and_load`` now runs behind the §22 retrieval gate, so its
    ``RetrievalResult`` is no longer *ungated* — but it is still only an audit
    trace, and the retrieval gate is not the consumption gate. A delivery owner
    that reads one has reached knowledge without a consumption decision, which
    is exactly the bypass Patch 8's exit criterion forbids.
    """

    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet; the criterion is vacuous until it is")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            referenced.update(alias.asname or alias.name for alias in node.names)
    assert AUDIT_ONLY_RETRIEVAL_RECORD not in referenced, (
        f"{module_name} reads {AUDIT_ONLY_RETRIEVAL_RECORD}, which crosses no §22 gate; "
        "a consumer must accept an AdmittedKnowledgeHandle instead"
    )


def test_the_admitted_handle_is_the_only_minted_capability() -> None:
    """Only the admission owner may create the capability a consumer accepts."""

    admission = GOLD_PACKAGE / "admission.py"
    source = admission.read_text(encoding="utf-8")
    assert "class AdmittedKnowledgeHandle" in source
    assert "def admit_for_consumption(" in source
    for other in _python_sources(GOLD_PACKAGE):
        if other.name == "admission.py":
            continue
        text = other.read_text(encoding="utf-8")
        assert "class AdmittedKnowledgeHandle" not in text, (
            f"{other.name} declares a second handle type; the capability must have one owner"
        )


def test_the_consumption_barrier_exists_to_be_called() -> None:
    """The barrier the tripwire above demands must actually be exported.

    It is spread over two owners: ``admission`` holds the gate-decision half and
    ``point_of_use`` the delivery-time half. Which file a symbol lives in is a
    size-rule question; that every one of them exists and is exported is not, so
    the whole set is checked against both.
    """

    owners = [GOLD_PACKAGE / "admission.py", GOLD_PACKAGE / "point_of_use.py"]
    for owner in owners:
        assert owner.exists(), f"{owner.name} is part of the Patch 8 barrier"
    sources = {owner.name: owner.read_text(encoding="utf-8") for owner in owners}
    for symbol in sorted(CONSUMPTION_BARRIER_SYMBOLS):
        defining = [name for name, text in sources.items() if f"def {symbol}(" in text]
        assert len(defining) == 1, (
            f"{symbol} is defined in {defining or 'no barrier owner'}; the barrier must "
            "have exactly one owner"
        )
        assert f'"{symbol}"' in sources[defining[0]], (
            f"{symbol} is not exported by {defining[0]}"
        )
