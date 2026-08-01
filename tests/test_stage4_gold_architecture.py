from __future__ import annotations

import ast
from pathlib import Path
import sys

from synapse.experiments.gold.admission_contracts import (
    ConsumptionDecision,
    IngestionGateDecision,
    PublicationGateDecision,
    RetrievalGateDecision,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "synapse" / "experiments" / "gold"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = f"{'.' * node.level}{module}"
            result.append(module)
    return tuple(result)


def _gold_edges() -> dict[str, set[str]]:
    modules = {path.stem: path for path in GOLD.glob("*.py")}
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        for imported in _imports(path):
            if imported.startswith("."):
                target = imported.lstrip(".").split(".", 1)[0]
            elif imported.startswith("synapse.experiments.gold."):
                target = imported.rsplit(".", 1)[-1]
            else:
                continue
            if target in graph:
                graph[name].add(target)
    return graph


def test_s4_p78_architecture_preserves_protected_core_and_swebench_direction() -> None:
    protected = (ROOT / "synapse" / "cvm.py", ROOT / "synapse" / "interpreter.py")
    swebench = tuple((ROOT / "synapse" / "experiments" / "swebench").glob("*.py"))

    for path in (*protected, *swebench):
        assert all(
            not imported.startswith("synapse.experiments.gold")
            for imported in _imports(path)
        ), path


def test_s4_p78_architecture_production_never_imports_acceptance_artifacts() -> None:
    for path in (ROOT / "synapse").rglob("*.py"):
        imports = _imports(path)
        assert all(
            not imported.startswith(("tests", "pytest"))
            for imported in imports
        ), path
        source = path.read_text(encoding="utf-8")
        assert "tests/fixtures" not in source
        assert "tests\\fixtures" not in source


def test_s4_p78_architecture_persistence_and_coordination_boundaries_are_narrow() -> None:
    persistence_imports = _imports(GOLD / "persistence.py")
    stdlib = set(sys.stdlib_module_names)
    assert all(
        imported.lstrip(".").split(".", 1)[0] in stdlib
        for imported in persistence_imports
    )
    assert set(_imports(GOLD / "coordination.py")) == {
        "__future__",
        "contextlib",
        "dataclasses",
        "json",
        "os",
        "pathlib",
        "re",
        "time",
        "typing",
        ".persistence",
    }


def test_s4_p78_architecture_current_gold_dependency_graph_is_acyclic() -> None:
    graph = _gold_edges()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"gold import cycle reaches {node}"
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for module in graph:
        visit(module)


def test_s4_p78_architecture_has_no_second_stage4_entrypoint_or_test_mode() -> None:
    assert not (GOLD / "__main__.py").exists()
    for path in GOLD.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "PYTEST_CURRENT_TEST" not in source
        assert "pytest" not in source


def test_s4_p78_architecture_keeps_patch6_surfaces_free_of_new_domain_imports() -> None:
    forbidden = {
        ".admission",
        ".admission_contracts",
        ".admission_store",
        ".authority_overlay",
        ".compatibility_store",
        ".coordination",
        ".knowledge",
        ".knowledge_contracts",
        ".knowledge_store",
        ".patch6_adapters",
        ".retrieval_workflow",
        ".snapshot_compatibility",
        ".snapshot_retrieval",
    }
    for name in ("compatibility.py", "retrieval.py"):
        assert not (set(_imports(GOLD / name)) & forbidden), name


def test_s4_p78_architecture_uses_four_distinct_gate_decisions() -> None:
    assert len(
        {
            IngestionGateDecision,
            PublicationGateDecision,
            RetrievalGateDecision,
            ConsumptionDecision,
        }
    ) == 4


def test_s4_p78_architecture_only_integrated_workflow_mints_admitted_handles() -> None:
    call_sites: list[str] = []
    for path in GOLD.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "create_admitted_knowledge_handle"
            ):
                call_sites.append(path.name)
    assert call_sites == ["retrieval_workflow.py"]
    assert "search_index(" not in (GOLD / "retrieval_workflow.py").read_text(
        encoding="utf-8"
    )
