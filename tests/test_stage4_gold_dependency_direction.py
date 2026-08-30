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
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLD_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "gold"
SWEBENCH_PACKAGE = REPO_ROOT / "synapse" / "experiments" / "swebench"
GOLD_MODULE_PREFIX = "synapse.experiments.gold"
OWNERSHIP_MANIFEST = REPO_ROOT / "governance" / "stage4_ownership_v1.json"
STAGE4_ADAPTER_COMPONENTS = json.loads(
    OWNERSHIP_MANIFEST.read_text(encoding="utf-8")
)["adapter_components"]

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
        # NR-03 adapter point: only the declared replay VM adapter boundary may
        # use it, including cohesion components attached to that exact adapter.
        "synapse.cvm",
    }
)

# NR-03 protected core: the gold package may never import these directly.
#
# ``synapse.cvm`` is deliberately absent from this set. NR-03 forbids wedging
# retrieval, knowledge, admission, planning, authority, orchestration,
# publication or economic logic *into* the protected core; it explicitly permits
# one narrow typed adapter point. The §9.8 ownership addendum assigns the
# protected-core integration adapter to ``gold/replay_vm_adapter.py`` under the
# replay owner. Forbidding the import outright would erase that sanctioned seam.
# The narrower rule below keeps the sole CVM edge in that adapter and keeps both
# the replay owner and capture sibling independent of the protected core.
PROTECTED_CORE_MODULES = frozenset(
    {
        "synapse.interpreter",
        "synapse.application",
        "synapse.cli",
        "synapse.golden_replay",
    }
)

# The single module allowed to hold the CognitiveVM adapter point, and the exact
# machine names it may bind. Widening either is an NR-03 review.
CVM_ADAPTER_MODULE = "replay_vm_adapter.py"
CVM_MODULE = "synapse.cvm"


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


#: The private factory that mints the frozen candidate set. Retrieval refuses to
#: enumerate without one, and that refusal is worth exactly as much as the number
#: of places able to produce one — which is one.
FROZEN_SET_MINT = "_mint_frozen_candidate_set"
FROZEN_SET_MINT_HOME = "frozen_candidates.py"
FROZEN_SET_MINT_ADAPTER = "gate_findings.py"


def test_the_frozen_candidate_set_is_minted_in_exactly_one_place() -> None:
    """A second minting site would put the §21 constraint back in a caller's hands.

    ``enumerate_retrieval_candidates`` takes its candidates from a committed
    snapshot instead of the live index, and the snapshot reaches it as a sealed
    ``FrozenCandidateSet``. The seal stops one being constructed directly; this
    keeps the private factory that *can* build one reachable from the single
    adapter that re-verifies the snapshot before minting.
    """

    reachers = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == FROZEN_SET_MINT_HOME:
            continue
        if FROZEN_SET_MINT in path.read_text(encoding="utf-8"):
            reachers.append(path.name)
    assert reachers == [FROZEN_SET_MINT_ADAPTER], (
        f"{sorted(reachers)} reach {FROZEN_SET_MINT}; the frozen candidate set must be "
        f"mintable only by {FROZEN_SET_MINT_ADAPTER}, which is the module that re-verifies "
        "the committed snapshot it is derived from"
    )


def test_retrieval_does_not_import_the_snapshot_owner() -> None:
    """The reason the frozen set is a capability rather than a parameter.

    ``retrieval.py`` precedes ``knowledge.py`` in the §12 map, so taking a
    ``UsableKnowledgeSnapshot`` as an argument would invert the dependency the
    map fixes. The constraint travels as a sealed record of plain strings
    instead, and this fails the day someone reaches for the snapshot type
    directly because the filter needed one more field.
    """

    source = (GOLD_PACKAGE / "retrieval.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "knowledge", (
                "retrieval.py must not import the §21 owner; the frozen candidate set "
                "crosses the boundary as a sealed contracts record"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.endswith("gold.knowledge"), (
                    "retrieval.py must not import the §21 owner"
                )


def test_the_frozen_candidate_adapter_declares_its_private_seam() -> None:
    """The private names it shares with its owner are written down.

    ``frozen_candidates.py`` reuses ``contracts``' digest and timestamp
    validators rather than restating them, because two modules with their own
    idea of a valid sha256 is how a record becomes valid in one place and not the
    other. The cost is a non-public dependency; the control is that it is
    enumerated, and this fails if an import appears that the seam does not name.
    """

    source = (GOLD_PACKAGE / "frozen_candidates.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_private: set[str] = set()
    declared: tuple[str, ...] = ()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "contracts":
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
        f"frozen_candidates.py takes {sorted(imported_private)} privately but declares "
        f"{sorted(declared)}; keep ADAPTER_PRIVATE_SEAM and the imports in step"
    )


#: The only production builder of the consumption gate's compatibility probe.
#: It performs a stage-3 revalidation when the gate asks; anything else handed
#: to ``compatibility_probe`` is a callable of the caller's own devising, and
#: then the gate consults *something* about compatibility with nothing requiring
#: that something to be a revalidation of this subject against this context.
BOUND_COMPATIBILITY_PROBE = "configured_revalidation_probe"
DURABLE_COMPATIBILITY_PROBE = "require_durable_revalidation_probe"
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
        if (
            BOUND_COMPATIBILITY_PROBE not in source
            and DURABLE_COMPATIBILITY_PROBE not in source
        ):
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


def test_the_snapshot_owner_confirms_an_admission_root_without_importing_the_gates() -> None:
    """A boundary now checks its admission root, and that must cost no import.

    `commit_atomic_snapshot_boundary` refuses a root the admission journal does
    not confirm. The obvious way to do that is to import the journal, and the
    obvious way is wrong: Patch 6.5 exists so §21 and §22 never form a cycle.
    The port is declared structurally in `knowledge.py`, and
    `FileAdmissionJournal` satisfies it by shape — the same mechanism that lets
    it satisfy `DecisionJournalPort` and `AdmissionHistoryPort` with neither
    module naming the other.
    """

    knowledge = GOLD_PACKAGE / "knowledge.py"
    source = knowledge.read_text(encoding="utf-8")
    assert "class AdmissionHistoryRootPort" in source, (
        "the snapshot owner must declare the port it consults, not import an implementation"
    )
    for forbidden in ("admission", "admission_journal", "admission_store"):
        assert f"{GOLD_MODULE_PREFIX}.{forbidden}" not in _imported_modules(knowledge)
        assert f"from .{forbidden} import" not in source, (
            f"knowledge.py imports {forbidden}; §21 and §22 must not depend on each other"
        )


def test_the_boundary_probe_lives_in_the_adapter_and_not_in_either_owner() -> None:
    """§21 and §22 still do not know each other, and the projection has one home.

    The consumption gate's boundary answer now comes from a committed snapshot.
    The two ways to arrange that which would be wrong are for the gate owner to
    import the snapshot owner, or the reverse; the way that is right is the
    module already allowed to know both sides.
    """

    adapter = (GOLD_PACKAGE / "gate_findings.py").read_text(encoding="utf-8")
    assert "def configured_boundary_probe(" in adapter, (
        "the projection from a committed boundary to the gate's boolean belongs to the adapter"
    )
    for owner in ("knowledge", "admission"):
        assert f"from .{owner} import" in adapter, (
            f"gate_findings.py must know {owner} to project between the two sides"
        )

    knowledge = GOLD_PACKAGE / "knowledge.py"
    assert "from .gate_findings" not in knowledge.read_text(encoding="utf-8")
    assert f"{GOLD_MODULE_PREFIX}.gate_findings" not in _imported_modules(knowledge)


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


#: Every public Library mutation and the production authority it must demand.
#: Behavior publication crosses the §22 admission gates. Program ingestion is a
#: distinct, temporary CAS write and demands the sealed Library-bound authority
#: whose runtime behavior is falsified by the ProgramArtifact acceptance suite.
LIBRARY_WRITE_BARRIERS = {
    "put_behavior": "admission",
    "ingest_program_artifact": "authority",
}

#: Verb prefixes that name a store-mutating public method. A method landing with
#: one of these without a declared authority is a second write path around the
#: production boundary.
WRITE_METHOD_PREFIXES = (
    "put_",
    "store_",
    "write_",
    "import_",
    "ingest_",
    "add_",
    "publish_",
)


def _library_class_methods() -> list[ast.FunctionDef]:
    tree = ast.parse((GOLD_PACKAGE / "library.py").read_text(encoding="utf-8"))
    owner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "BehaviorLibrary"
    )
    return [node for node in owner.body if isinstance(node, ast.FunctionDef)]


def test_no_unowned_write_method_has_been_added_to_the_library() -> None:
    """Every production mutation is accounted for by its real authority."""

    writers = {
        node.name
        for node in _library_class_methods()
        if not node.name.startswith("_")
        and node.name.startswith(WRITE_METHOD_PREFIXES)
    }
    assert writers == set(LIBRARY_WRITE_BARRIERS), (
        f"library.py offers write methods {sorted(writers)} while production "
        f"authorities cover only {sorted(LIBRARY_WRITE_BARRIERS)}"
    )


@pytest.mark.parametrize(
    ("method_name", "barrier_name"),
    tuple(LIBRARY_WRITE_BARRIERS.items()),
)
def test_a_library_write_demands_its_production_authority(
    method_name: str,
    barrier_name: str,
) -> None:
    """The authority is required in the signature, never caller-optional."""

    methods = {
        node.name: node
        for node in _library_class_methods()
    }
    assert method_name in methods, f"library.py no longer defines {method_name}"
    arguments = methods[method_name].args
    positional = [*arguments.posonlyargs, *arguments.args]
    positional_names = [argument.arg for argument in positional]
    keyword_names = [argument.arg for argument in arguments.kwonlyargs]
    assert barrier_name in {*positional_names, *keyword_names}, (
        f"{method_name} does not require its {barrier_name} authority"
    )
    if barrier_name in keyword_names:
        default = arguments.kw_defaults[keyword_names.index(barrier_name)]
        assert default is None, (
            f"{method_name} gives {barrier_name} a default; an optional authority "
            "does not guard a production write"
        )
    else:
        required_count = len(positional) - len(arguments.defaults)
        assert positional_names.index(barrier_name) < required_count, (
            f"{method_name} gives {barrier_name} a positional default; an optional "
            "authority does not guard a production write"
        )


def test_only_the_replay_vm_adapter_holds_the_cvm_dependency() -> None:
    """NR-03 permits one CVM edge and keeps capture behind owner-defined ports."""

    importers = {
        path.name
        for path in _python_sources(GOLD_PACKAGE)
        if CVM_MODULE in _imported_modules(path)
    }
    adapter_components = {
        component
        for component, adapter in STAGE4_ADAPTER_COMPONENTS.items()
        if adapter == CVM_ADAPTER_MODULE
    }
    expected_importers = {CVM_ADAPTER_MODULE, *adapter_components}
    assert importers == expected_importers, (
        f"{CVM_MODULE} is imported by {sorted(importers)}; the frozen dependency "
        f"direction permits only the declared {CVM_ADAPTER_MODULE} boundary "
        f"{sorted(expected_importers)}"
    )

    capture = GOLD_PACKAGE / "replay_capture.py"
    tree = ast.parse(capture.read_text(encoding="utf-8"), filename=str(capture))
    import_targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            separator = "." if node.module else ""
            import_targets.extend(
                prefix + separator + alias.name for alias in node.names
            )
    assert not any(
        "replay_vm_adapter" in target.lstrip(".").split(".")
        for target in import_targets
    ), (
        "replay_capture.py imports its sibling VM adapter instead of consuming "
        "the replay owner's machine/factory ports"
    )


def test_only_the_replay_composition_root_mints_the_machine_factory_binding() -> None:
    """The owner accepts a sealed wrapper; only the exact root may mint it."""

    binding_module = "replay_machine_binding.py"
    root = "replay_composition.py"
    seal = "_PRODUCTION_MACHINE_FACTORY_SEAL"
    constructor = "ProductionReplayMachineFactory"
    minters: set[str] = set()
    private_factory_callers: set[str] = set()
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == binding_module:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if seal in source or constructor in calls:
            minters.add(path.name)
        if "_create_production_replay_binding" in calls:
            private_factory_callers.add(path.name)
    assert minters == {root}
    assert private_factory_callers == {root}

    root_tree = ast.parse((GOLD_PACKAGE / root).read_text(encoding="utf-8"))
    public = next(
        node for node in root_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "create_production_replay_binding"
    )
    parameters = {*public.args.posonlyargs, *public.args.args, *public.args.kwonlyargs}
    assert "machine_factory" not in {parameter.arg for parameter in parameters}

def test_the_cvm_adapter_point_stays_narrow() -> None:
    """The adapter binds machine primitives, never Stage 4 semantics.

    A widening import — the interpreter, the golden-replay driver, the host ABI
    registry — would turn the adapter point into a second integration surface.
    """

    adapter = GOLD_PACKAGE / CVM_ADAPTER_MODULE
    tree = ast.parse(adapter.read_text(encoding="utf-8"), filename=str(adapter))
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == CVM_MODULE:
            bound.update(alias.name for alias in node.names)
    allowed = {
        "CognitiveVM", "VMState", "VMStatus", "PendingHostCall",
        "GAS_COSTS", "GAS_BACK_EDGE", "HOST_ABI_VERSION",
        "compute_call_id", "compute_message_consumed_id", "encode_vm_value", "decode_vm_value",
        # A machine value type, not machine behaviour. The adapter must tell an
        # internal Synapse function apart from an ordinary Python callable
        # *before* the machine dispatches to either, and the only honest way to
        # ask that question is the type the machine itself uses to answer it.
        # Widening this set is an NR-03 review; this entry is one, and it adds a
        # type the adapter reads, never a path it drives.
        "FunctionObject",
        # The two frame types, admitted by the same review and for the same
        # reason. Before a snapshot is serialized the adapter has to establish
        # that every value reachable from the machine's state is in the closed
        # vocabulary — the encoder's fallback is ``repr(value)``, so an unchecked
        # object would run its own code inside the digest replay identity is
        # measured by. Deciding that for a frame means walking the fields the
        # frame *declares*, and those are read off the dataclass. Doing it
        # structurally instead — over whatever attributes an object happens to
        # carry — is the weaker check that let six value-bearing fields through,
        # so the types are bound here rather than approximated. Read, never
        # driven: no frame is constructed and no frame method is called.
        "CallFrame",
        "GuardFrame",
        # The snapshot decoder raises this exact format exception for malformed
        # frame entries.  The adapter catches it at the durable input boundary;
        # it does not construct or drive another core execution surface.
        "VMSnapshotFormatError",
    }
    assert bound <= allowed, (
        f"{CVM_ADAPTER_MODULE} binds machine names outside the approved adapter surface: "
        f"{sorted(bound - allowed)}"
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
        # The sole present-time entrypoint. A handle proves only that admission
        # happened and was durable at mint; this path performs fresh Stage 3 and
        # Consumption evaluation at the moment of delivery.
        "admit_for_use_now",
    }
)

#: Barriers that exist and are *not* sufficient on their own.
#:
#: Structural validation, durable mint evidence and cached audit are useful but
#: do not freshly evaluate Stage 3 and Consumption. Listing any of them as a
#: consumption barrier would let a delivery owner satisfy the tripwire using a
#: transferable old result.
WEAK_CONSUMPTION_BARRIERS = frozenset(
    {
        "require_consumption_admitted",
        "validate_admitted_handle",
        "admit_for_consumption",
        "validate_current_admitted_knowledge",
        "require_current_admitted_handle",
        # Patch 9's audit comparison. It proves a subject set matches completed
        # point-of-use evidence and says so in its own docstring: it returns no
        # refs and confers no present-tense authority. Listing it as a barrier
        # would let a delivery owner satisfy the tripwire by comparing against
        # an admission taken at any earlier time.
        "require_admitted_subjects",
    }
)
DELIVERY_OWNERS = ("replay.py", "context.py", "runner.py")

#: Owners that consume the *result* of a barrier crossing rather than crossing
#: it themselves, and the type each of them must require to do so.
#:
#: ``activities.py`` was listed as a delivery owner while it ran the four-gate
#: chain over activity refs. It cannot: every §22 subject needs a
#: ``CompatibilitySubjectDescriptor``, built from a published behavior unit with
#: its blob, manifest, index entry, attestation and lifecycle records, and
#: ``admit_for_use_now`` refuses a subject set its Stage 3 probe does not cover.
#: A ``RecordedActivity`` has none of those, so no production path could ever
#: obtain an admission naming one — the old chain was constructible only from a
#: hand-built controller.
#:
#: A second reason it cannot cross the barrier itself: a point-of-use attempt
#: admits exactly once. Its Stage 3 revalidation record is deterministic and the
#: append-only compatibility history refuses the duplicate, so the ledger and
#: the replay request cannot each hold an admission of their own. The replay
#: owner crosses once and the ledger is sealed against that crossing.
#:
#: So the rule here is not weaker, it is different and checkable: the sealing
#: entry point must *require* ``CurrentAdmittedKnowledge``, a type only
#: ``admit_for_use_now`` can mint — its ``__new__`` refuses — with no default.
#: A stored ``GateDecision``, a handle, or a bare ref tuple cannot satisfy it.
#:
#: It is necessary and **not sufficient**, and the difference was found by audit
#: rather than by reasoning. Requiring the minted type proves the barrier ran;
#: it says nothing about whether the barrier's answer is still true when the
#: knowledge is finally used, and an admission that has gone stale still carries
#: a perfectly valid minted object. The second half of the obligation lives in
#: ``test_an_execution_entry_point_rechecks_authority_at_the_point_of_use``:
#: whatever puts this knowledge into a machine has to ask the live authority
#: state again. Sealing is bound to an admission; executing is bound to a
#: current one.
ADMITTED_KNOWLEDGE_CONSUMERS = {
    ("activities.py", "seal_activity_ledger"): "CurrentAdmittedKnowledge",
}


@pytest.mark.parametrize(
    ("module_name", "function_name"), sorted(ADMITTED_KNOWLEDGE_CONSUMERS)
)
def test_an_admitted_knowledge_consumer_requires_the_minted_type(
    module_name: str, function_name: str
) -> None:
    """A downstream consumer must take the barrier's product, not a promise of it."""

    import importlib
    import inspect

    expected = ADMITTED_KNOWLEDGE_CONSUMERS[(module_name, function_name)]
    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet; the criterion is vacuous until it is")
    module = importlib.import_module(f"{GOLD_MODULE_PREFIX}.{module_name[:-3]}")
    signature = inspect.signature(getattr(module, function_name))
    parameters = {
        name: parameter
        for name, parameter in signature.parameters.items()
        if parameter.annotation == expected
    }
    assert parameters, (
        f"{module_name}::{function_name} does not require {expected}; it consumes the "
        "product of the consumption barrier and must take the minted type"
    )
    for name, parameter in parameters.items():
        assert parameter.default is inspect.Parameter.empty, (
            f"{module_name}::{function_name} makes {name} optional, so the barrier's "
            "product can be omitted"
        )
    # And the minted type must actually be unforgeable: only the barrier makes one.
    from synapse.experiments.gold.point_of_use import CurrentAdmittedKnowledge

    with pytest.raises(TypeError):
        CurrentAdmittedKnowledge()

#: Entry points that put admitted knowledge into execution, and the freshness
#: check each of them must reach before it can.
#:
#: This is the rule the previous revision did not have. Requiring
#: ``seal_activity_ledger`` to take ``CurrentAdmittedKnowledge`` proves the
#: barrier *ran*; it cannot prove the barrier's answer is still true, and
#: ``point_of_use.py`` says so in its own contract — the object is a completed
#: revalidation, not a portable capability. So an execution entry point has a
#: second obligation, checked here: it must consult the live authority state
#: itself, at the moment of use, and it must be unable to run without the
#: production binding that makes that possible.
#:
#: An audit found the gap this closes: a request admitted at one coordinator
#: epoch executed to ``REPLAY_IDENTICAL`` at a later one, after the system had
#: already classified the admission as stale.
USE_TIME_AUTHORITY_CHECK = "require_current_point_of_use_evidence"
POINT_OF_USE_BARRIER = "admit_for_use_now"
EXECUTION_ENTRY_POINTS = {
    "replay_composition.py": ("run_governed_replay", "resume_governed_replay"),
}
EXECUTION_OWNER = "replay.py"
EXECUTION_OWNER_DELEGATES = {
    "run_governed_replay": "_run_governed_replay",
    "resume_governed_replay": "_resume_governed_replay",
}
#: What an execution entry point must require before it can start a run. The
#: admission request is the barrier's *input*, not its output, so requiring it
#: is requiring the barrier to be crossed inside the call rather than before it.
EXECUTION_ENTRY_ARGUMENT = "admission"

#: What identifies a public name as an execution entry point. A ``machines``
#: parameter used to, and no longer exists: the executor builds its machines from
#: the manifest, so the argument that turns a prepared request into a run is the
#: manifest reference itself.
EXECUTION_MACHINE_ARGUMENT = "manifest_ref"


@pytest.mark.parametrize("module_name", sorted(EXECUTION_ENTRY_POINTS))
def test_an_execution_entry_point_rechecks_authority_at_the_point_of_use(
    module_name: str,
) -> None:
    """Executing admitted knowledge requires the gate's answer to be current.

    Two things are asserted, and neither is satisfiable by a type annotation.
    The module must call the use-time check, and every entry point that starts a
    run must require the production binding without a default — a binding that
    could be omitted is a binding a caller can decline to supply, which puts the
    freshness check back under the caller's control.
    """

    import importlib
    import inspect

    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet; the criterion is vacuous until it is")
    composition_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    composition_calls = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(composition_tree)
        if isinstance(node, ast.Call)
    }
    owner_path = GOLD_PACKAGE / EXECUTION_OWNER
    owner_tree = ast.parse(owner_path.read_text(encoding="utf-8"), filename=str(owner_path))
    owner_calls = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(owner_tree)
        if isinstance(node, ast.Call)
    }
    for required in (POINT_OF_USE_BARRIER, USE_TIME_AUTHORITY_CHECK):
        assert required in owner_calls, (
            f"{EXECUTION_OWNER} puts admitted knowledge into execution without calling "
            f"{required}; §22 requires the consumption decision to be established "
            "immediately before replay, not carried from an earlier moment"
        )

    module = importlib.import_module(f"{GOLD_MODULE_PREFIX}.{module_name[:-3]}")
    for entry_point in EXECUTION_ENTRY_POINTS[module_name]:
        assert EXECUTION_OWNER_DELEGATES[entry_point] in composition_calls, (
            f"{module_name}::{entry_point} does not delegate to the single replay owner"
        )
        signature = inspect.signature(getattr(module, entry_point))
        assert EXECUTION_ENTRY_ARGUMENT in signature.parameters, (
            f"{module_name}::{entry_point} starts a run without requiring the "
            "point-of-use admission request, so the barrier is crossed elsewhere"
        )
        assert (
            signature.parameters[EXECUTION_ENTRY_ARGUMENT].default is inspect.Parameter.empty
        ), (
            f"{module_name}::{entry_point} makes the admission optional; an omitted "
            "admission is a skipped consumption gate"
        )


@pytest.mark.parametrize("module_name", sorted(EXECUTION_ENTRY_POINTS))
def test_execution_has_exactly_the_declared_public_entry_points(module_name: str) -> None:
    """No second public path from a prepared request to a running machine.

    The repaired composition admits and runs as one act. That is worth nothing
    if a neighbouring public name still offers a way from a request to a running
    machine, because a caller would simply use it — which is what the previous
    revision did, and it was the whole defect. So the public surface is
    enumerated and the set must be exactly the declared one.

    What marks an entry point moved once, and the move is the point. It used to
    be a ``machines`` parameter; the executor now builds its machines from a
    manifest, so no public name takes one. The marker is therefore the argument
    that turns a prepared request into a run: ``manifest_ref``, the resolved
    statement of what the run is expected to reach.
    """

    import importlib
    import inspect

    path = GOLD_PACKAGE / module_name
    if not path.exists():
        pytest.skip(f"{module_name} is not implemented yet; the criterion is vacuous until it is")
    module = importlib.import_module(f"{GOLD_MODULE_PREFIX}.{module_name[:-3]}")
    exported = getattr(module, "__all__", ())
    executors = set()
    for name in exported:
        candidate = getattr(module, name, None)
        if not callable(candidate) or isinstance(candidate, type):
            continue
        try:
            signature = inspect.signature(candidate)
        except (TypeError, ValueError):  # pragma: no cover - builtins have none
            continue
        if EXECUTION_MACHINE_ARGUMENT in signature.parameters:
            executors.add(name)
    assert executors == set(EXECUTION_ENTRY_POINTS[module_name]), (
        f"{module_name} exports {sorted(executors)} as execution entry points; the "
        f"declared set is {sorted(EXECUTION_ENTRY_POINTS[module_name])}, and a second "
        "public path from a request to a machine is the bypass this rule exists for"
    )
    owner = importlib.import_module(f"{GOLD_MODULE_PREFIX}.{EXECUTION_OWNER[:-3]}")
    assert not ({"run_governed_replay", "resume_governed_replay"} & set(owner.__all__)), (
        "the replay owner re-exported a second public execution route beside the "
        "canonical composition root"
    )


#: A delivery owner reading this instead of entering the point-of-use path is
#: the A-01 bypass: the retrieval record is audit evidence and never a
#: consumption authority.
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
    """A consumer must take point-of-use authority, not the retrieval record.

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
        "a consumer must enter through admit_for_use_now instead"
    )


def test_the_admitted_handle_has_one_durable_record_owner() -> None:
    """Only admission may create the audit prerequisite point-of-use consumes."""

    admission = GOLD_PACKAGE / "admission.py"
    source = admission.read_text(encoding="utf-8")
    assert "class AdmittedKnowledgeHandle" in source
    assert "def admit_for_consumption(" in source
    for other in _python_sources(GOLD_PACKAGE):
        if other.name == "admission.py":
            continue
        text = other.read_text(encoding="utf-8")
        assert "class AdmittedKnowledgeHandle" not in text, (
            f"{other.name} declares a second handle type; the audit record must have one owner"
        )


def test_no_weak_barrier_counts_as_crossing_the_consumption_gate() -> None:
    """A structural or audit-only check must not satisfy the tripwire.

    Listing these paths makes fresh evaluation optional: an owner calling only
    a validator, mint function or cached audit could pass a check whose whole
    purpose is to prove the gates were evaluated at delivery. Those paths retain
    their structural and audit roles, but none counts as present-time authority.
    """

    assert not (WEAK_CONSUMPTION_BARRIERS & CONSUMPTION_BARRIER_SYMBOLS)
    assert "admit_for_use_now" in CONSUMPTION_BARRIER_SYMBOLS


def test_the_consumption_barrier_exists_to_be_called() -> None:
    """The barrier the tripwire above demands must actually be exported.

    Admission owns durable prerequisites; only ``point_of_use`` owns the
    delivery-time entrypoint. The defining owner and export are checked here so
    a delivery path cannot substitute a structural validator.
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


# ---------------------------------------------------------------------------
# Round 19 — no store mutates without advancing the shared fence
# ---------------------------------------------------------------------------

#: `persistence` owns the unfenced primitive and the fenced one that wraps it, so
#: it is the one module allowed to call the former.
#: Every primitive in ``persistence`` that changes authoritative state. Round 19
#: scanned for the first of these and described the result as discovering
#: unfenced writers; it discovered one of six. ``publish_immutable`` puts object
#: bytes in place, ``atomic_replace_metadata`` rewrites ``index.v1`` — a §21 root
#: — and the snapshot-transaction pair makes a whole boundary visible. All four
#: were outside the scan, and four of the six real mutation sites were writing
#: through them with no interval open at all.
AUTHORITY_PRIMITIVES = (
    "append_journal_payload",
    "atomic_replace_metadata",
    "commit_snapshot_transaction",
    "move_immutable",
    "publish_immutable",
    "stage_snapshot_transaction",
    "write_staged_bytes",
)
AUTHORITY_PRIMITIVE_HOME = "persistence.py"

#: The coordinator's own two writes, which cannot hold a ticket because they are
#: what makes tickets possible: one appends the epoch frames whose count *is* the
#: epoch, the other writes the identity a ticket carries. Named rather than left
#: as a silent skip in a walker — an exemption a reader cannot see is an
#: exemption nobody can review — and held below to the coordinator adapter alone.
COORDINATOR_ONLY_WRITES = ("append_coordinator_epoch_frame", "publish_coordinator_metadata")
COORDINATOR_ADAPTER = "admission_journal.py"

#: The private factory that opens an interval. One importer, for the same reason
#: every other private mint has one: a second caller would be a second authority
#: to declare a mutation in flight, and a single counter cannot tell two of them
#: apart.
INTERVAL_MINT = "_mint_store_mutation_ticket"


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if child is target:
                    return node.name
    return None


def test_no_authority_primitive_is_called_without_an_open_interval() -> None:
    """Every authority write names the interval it belongs to, at every call site.

    The check is on the *call*, not on the import: a required keyword argument
    already makes an omitted ticket a runtime refusal, and this is what makes it
    a review-time one as well. A caller that passed ``ticket=None`` to quiet a
    signature would be caught here rather than in production.
    """

    offenders = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == AUTHORITY_PRIMITIVE_HOME:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name not in AUTHORITY_PRIMITIVES:
                continue
            passed = {keyword.arg for keyword in node.keywords}
            if "ticket" in passed:
                continue
            offenders.append(f"{path.name}:{_enclosing_function(tree, node)}:{name}")

    assert offenders == [], (
        f"{sorted(offenders)} change authoritative state without naming an open "
        "mutation interval; open one with store_transaction and pass its ticket, so "
        "a coordinated read can detect the change"
    )


@pytest.mark.parametrize("primitive", COORDINATOR_ONLY_WRITES)
def test_the_unfenced_writes_are_reachable_only_by_the_coordinator(primitive: str) -> None:
    """Two exemptions, and exactly two holders.

    These are the writes a ticket cannot authorise, because they are the writes
    that make a ticket mean anything. The exemption is safe only while nothing
    else can reach them — a store that used the epoch appender for its own
    records would be mutating authority with the fence's own blessing.
    """

    reachers = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == AUTHORITY_PRIMITIVE_HOME:
            continue
        if primitive in path.read_text(encoding="utf-8"):
            reachers.append(path.name)
    assert reachers == [COORDINATOR_ADAPTER], (
        f"{sorted(reachers)} reach {primitive}; the unfenced writes belong to "
        f"{COORDINATOR_ADAPTER}, which is the coordinator itself"
    )


def test_a_mutation_interval_is_opened_in_exactly_one_place() -> None:
    """A second minting site would be a second coordinator wearing the first's name."""

    reachers = []
    for path in _python_sources(GOLD_PACKAGE):
        if path.name == AUTHORITY_PRIMITIVE_HOME:
            continue
        if INTERVAL_MINT in path.read_text(encoding="utf-8"):
            reachers.append(path.name)
    assert reachers == [COORDINATOR_ADAPTER], (
        f"{sorted(reachers)} reach {INTERVAL_MINT}; only {COORDINATOR_ADAPTER} may "
        "declare a mutation in flight"
    )


#: Every owner that keeps a durable authority store. Each must take a fence at
#: construction, and take it as a *required* argument — an optional fence is the
#: NR-09 bypass in the shape of a default, because the caller that omits it gets
#: a store whose mutations no reader can distinguish from a quiet system.
FENCED_STORE_OWNERS = (
    ("library.py", "BehaviorLibrary"),
    ("lifecycle.py", "open_lifecycle_store"),
    ("provenance.py", "open_behavior_attestation_store"),
    ("taint.py", "open_taint_history_store"),
)


@pytest.mark.parametrize("module_name,entry_point", FENCED_STORE_OWNERS)
def test_a_store_takes_its_mutation_fence_as_a_required_argument(
    module_name: str, entry_point: str
) -> None:
    """The barrier is in the signature, so it cannot be forgotten at a call site."""

    tree = ast.parse((GOLD_PACKAGE / module_name).read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and (node.name == entry_point or node.name == "__init__")
    ]
    if entry_point[0].isupper():
        owner = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == entry_point
        )
        functions = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"]
    assert functions, f"{module_name} no longer defines {entry_point}"

    node = functions[0]
    names = {argument.arg for argument in node.args.kwonlyargs}
    assert "mutation_fence" in names, (
        f"{module_name}:{entry_point} does not take a mutation fence, so its writes "
        "are invisible to a fenced authority read"
    )
    required = len(node.args.kwonlyargs) - len([d for d in node.args.kw_defaults if d is not None])
    defaulted = {
        argument.arg
        for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
        if default is not None
    }
    assert "mutation_fence" not in defaulted, (
        f"{module_name}:{entry_point} gives mutation_fence a default; an optional fence "
        "is a bypass, because the caller that omits it mutates invisibly"
    )
    assert required >= 1
