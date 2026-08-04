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


#: The private factory that mints the library write capability. A capability
#: whose factory is reachable from anywhere is not a capability, so the name is
#: private and the number of modules allowed to reach it is one.
WRITE_ADMISSION_MINT = "_mint_library_write_admission"
WRITE_ADMISSION_MINT_HOME = "contracts.py"
WRITE_ADMISSION_MINT_ADAPTER = "library_admission.py"


def test_the_write_capability_is_minted_in_exactly_one_place() -> None:
    """A second minting site must fail a test, not pass a review.

    ``library.py`` demands a ``LibraryWriteAdmission`` before it writes, and the
    demand is only worth anything while the object cannot be assembled outside
    the gates. The seal makes direct construction impossible; this makes the
    private factory that *can* build one reachable from a single adapter.
    """

    reachers = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == WRITE_ADMISSION_MINT_HOME:
            continue
        if WRITE_ADMISSION_MINT in path.read_text(encoding="utf-8"):
            reachers.append(path.name)
    assert reachers == [WRITE_ADMISSION_MINT_ADAPTER], (
        f"{sorted(reachers)} reach {WRITE_ADMISSION_MINT}; the write capability must be "
        f"mintable only by {WRITE_ADMISSION_MINT_ADAPTER}, which is the module that runs "
        "the ingestion and publication gates"
    )


#: The only production builder of the consumption gate's compatibility probe.
#: It performs a stage-3 revalidation when the gate asks; anything else handed
#: to ``compatibility_probe`` is a callable of the caller's own devising, and
#: then the gate consults *something* about compatibility with nothing requiring
#: that something to be a revalidation of this subject against this context.
BOUND_COMPATIBILITY_PROBE = "configured_revalidation_probe"
COMPATIBILITY_PROBE_HOME = "gate_findings.py"


def test_a_production_gate_controller_takes_its_compatibility_probe_from_the_binding() -> None:
    """Vacuous today, and written now so it bites the day it stops being.

    No production module configures a gate controller yet. When one does, the
    §22 consumption gate's compatibility answer has to come from a real Stage 3
    record — that is item 12 of the review — and the check for it must already
    be in place rather than remembered later.
    """

    offenders = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name in {"admission.py", COMPATIBILITY_PROBE_HOME}:
            continue
        source = path.read_text(encoding="utf-8")
        if "configure_gate_controller(" not in source:
            continue
        if BOUND_COMPATIBILITY_PROBE not in source:
            offenders.append(path.name)
    assert not offenders, (
        f"{sorted(offenders)} configure a gate controller without taking the compatibility "
        f"probe from {BOUND_COMPATIBILITY_PROBE}; the consumption gate would decide on an "
        "answer no stage-3 revalidation produced"
    )


def test_the_revalidation_binding_is_reachable_from_exactly_one_owner() -> None:
    """The projection and the binding belong together, in the adapter's file.

    ``candidate_subject_ref`` used to live in the retrieval owner while the
    write side needed the same name. Two modules deriving a gate name from a
    descriptor is one rule written twice, and the round 13 campaign showed twice
    over what that costs: neither copy can be shown to be enforced.
    """

    home = GOLD_PACKAGE / COMPATIBILITY_PROBE_HOME
    source = home.read_text(encoding="utf-8")
    for symbol in ("candidate_subject_ref", "consumer_context_ref_of", BOUND_COMPATIBILITY_PROBE):
        assert f"def {symbol}(" in source, f"{COMPATIBILITY_PROBE_HOME} must define {symbol}"

    definers = [
        path.name
        for path in _python_sources(GOLD_PACKAGE)
        if path.name != COMPATIBILITY_PROBE_HOME
        and "def candidate_subject_ref(" in path.read_text(encoding="utf-8")
    ]
    assert not definers, (
        f"{sorted(definers)} define candidate_subject_ref as well; the gate's name for a "
        "subject must come from one place or the write and read sides can drift apart"
    )


def test_the_shared_write_helper_never_mints_a_capability_directly() -> None:
    """The suites' write helper must run the gates, not shortcut them.

    A test may reach for the private factory to prove a guard bites — that is
    the acceptance layer doing its job. The *shared* helper is different: every
    library and compatibility write goes through it, so a mint there would turn
    one convenience into a gate bypass for two whole suites at once.
    """

    helper = REPO_ROOT / "tests" / "gold_write_admission.py"
    assert helper.exists(), "the suites' write helper is what keeps their writes gated"
    source = helper.read_text(encoding="utf-8")
    assert WRITE_ADMISSION_MINT not in source, (
        "tests/gold_write_admission.py mints a write capability instead of earning one; "
        "every write in the library and compatibility suites would stop crossing the gates"
    )
    assert "admit_library_write" in source, (
        "the helper must reach its admission through the adapter that runs both gates"
    )


def test_the_library_owner_never_imports_the_gate_owner() -> None:
    """The write barrier must not cost the §38 direction.

    ``library.py`` is earlier than ``admission.py``, so it may demand a §22
    admission but may not know how one is decided. The shared vocabulary record
    in ``contracts.py`` is what lets it hold the demand without the import, and
    an import here would collapse two contours into one.
    """

    library = GOLD_PACKAGE / "library.py"
    assert f"{GOLD_MODULE_PREFIX}.admission" not in _imported_modules(library)
    source = library.read_text(encoding="utf-8")
    assert "from .admission" not in source
    assert "from .library_admission" not in source


def test_the_library_admission_adapter_declares_its_private_seam() -> None:
    """The one private name it takes from another owner is written down."""

    source = (GOLD_PACKAGE / "library_admission.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_private: set[str] = set()
    declared: tuple[str, ...] = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"contracts", "admission"}:
            for alias in node.names:
                if alias.name.startswith("_"):
                    imported_private.add(alias.name)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ADAPTER_PRIVATE_SEAM"
            for target in node.targets
        ):
            declared = tuple(element.value for element in node.value.elts)

    assert declared, "the adapter must declare its private seam"
    assert imported_private == set(declared), (
        f"library_admission.py takes {sorted(imported_private)} privately but declares "
        f"{sorted(declared)}; keep ADAPTER_PRIVATE_SEAM and the imports in step"
    )


#: Library operations that put an object into the store. Each must demand the
#: §22 write capability, because a write that no gate saw is the NR-09 bypass.
#: ``put_behavior`` is currently the only one; the tripwire below discovers new
#: ones rather than trusting this tuple to be kept up to date.
GATED_LIBRARY_WRITES = ("put_behavior",)

#: Verb prefixes that name a store-mutating public method. A method landing with
#: one of these and no admission parameter is a second, ungated way in.
WRITE_METHOD_PREFIXES = ("put_", "store_", "write_", "import_", "add_", "publish_")


def _library_class_methods() -> list[ast.FunctionDef]:
    tree = ast.parse((GOLD_PACKAGE / "library.py").read_text(encoding="utf-8"))
    owner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "BehaviorLibrary"
    )
    return [node for node in owner.body if isinstance(node, ast.FunctionDef)]


def test_no_ungated_write_method_has_been_added_to_the_library() -> None:
    """A new way in must fail here rather than inherit the old exemption.

    Naming the gated methods in a tuple only protects the methods someone
    remembered to add to it. This looks at what the class actually offers, so a
    second write path is a test failure on the day it lands.
    """

    writers = {
        node.name
        for node in _library_class_methods()
        if not node.name.startswith("_")
        and node.name.startswith(WRITE_METHOD_PREFIXES)
    }
    assert writers == set(GATED_LIBRARY_WRITES), (
        f"library.py offers write methods {sorted(writers)} while only "
        f"{sorted(GATED_LIBRARY_WRITES)} are checked for the §22 capability"
    )


@pytest.mark.parametrize("method_name", GATED_LIBRARY_WRITES)
def test_a_library_write_demands_the_gate_capability(method_name: str) -> None:
    """The barrier is in the signature, so it cannot be forgotten at a call site.

    An earlier revision took only a ``PublisherIdentity``. That made the gates
    something a caller could remember to consult — which is the same defect the
    retrieval loader had when it accepted a bare tuple of admitted refs.
    """

    tree = ast.parse((GOLD_PACKAGE / "library.py").read_text(encoding="utf-8"))
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    ]
    assert found, f"library.py no longer defines {method_name}"
    for node in found:
        names = {argument.arg for argument in node.args.kwonlyargs}
        assert "admission" in names, (
            f"{method_name} does not require a §22 write admission; a write the gates "
            "never saw is exactly the bypass NR-09 forbids"
        )
        defaults = dict(zip(node.args.kwonlyargs, node.args.kw_defaults))
        assert defaults[next(a for a in node.args.kwonlyargs if a.arg == "admission")] is None, (
            f"{method_name} gives the admission a default; an optional barrier is not one"
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
        "admit_for_use_now",
        "validate_current_admitted_knowledge",
        "require_admitted_subjects",
    }
)

#: Barriers that exist and are *not* sufficient on their own.
#:
#: ``require_current_admitted_handle`` re-reads authority heads and proves the
#: stored decision durable. It does not re-decide, does not take a fence, and
#: cannot see environment drift that moves no store anchor. It was listed as an
#: acceptable consumption barrier, which meant a delivery owner could satisfy
#: the tripwire through the weaker of the two paths — the tripwire would pass
#: while the property it exists to protect did not hold.
WEAK_CONSUMPTION_BARRIERS = frozenset({"require_current_admitted_handle"})
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


def test_no_weak_barrier_counts_as_crossing_the_consumption_gate() -> None:
    """A barrier that cannot see drift must not satisfy the tripwire.

    Listing both paths made the strong one optional: an owner calling only
    ``require_current_admitted_handle`` passed a check whose whole purpose is to
    prove the gate was crossed at the moment of delivery. The weak path stays in
    the codebase — it is a real durability check — but it no longer counts as
    the barrier.
    """

    assert not (WEAK_CONSUMPTION_BARRIERS & CONSUMPTION_BARRIER_SYMBOLS)
    assert "admit_for_use_now" in CONSUMPTION_BARRIER_SYMBOLS


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
