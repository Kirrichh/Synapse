"""Acceptance of the dependency checker, including import-form bypasses."""

from __future__ import annotations

import copy
import json

import pytest

from scripts import stage9_ownership_dag as dag


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    package = tmp_path / "gold"
    package.mkdir()
    for name in ("owner.py", "root.py", "leaf.py", "adapter.py"):
        (package / name).write_text("", encoding="utf-8")
    monkeypatch.setattr(dag, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(dag, "GOLD_PACKAGE", package)
    monkeypatch.setattr(dag, "STAGE4_OWNERSHIP_MAP", {"owner.py": "domain"})
    monkeypatch.setattr(dag, "STAGE4_OWNER_COMPONENTS", {})
    monkeypatch.setattr(dag, "STAGE4_ADAPTER_COMPONENTS", {})
    monkeypatch.setattr(dag, "STAGE4_OWNER_ADAPTERS", {"adapter.py": "owner.py"})
    monkeypatch.setattr(dag, "STAGE4_COMPOSITION_ROOTS", {
        "root.py": "owner.py", "leaf.py": "owner.py",
    })
    monkeypatch.setattr(dag, "STAGE4_COMPOSITION_DEPENDENCIES", {})
    monkeypatch.setattr("sys.argv", ["stage9_ownership_dag.py"])
    return package


@pytest.mark.parametrize("source", [
    "from .root import build",
    "from . import root as assembly",
    "from synapse.experiments.gold.root import build",
    "from synapse.experiments.gold import root as assembly",
    "import synapse.experiments.gold.root as assembly",
    "def deferred():\n    from synapse.experiments.gold.root import build",
])
@pytest.mark.parametrize("importer", ["owner.py", "adapter.py"])
def test_domain_code_cannot_reach_assembly_by_changing_import_form(
    assembly, capsys, source, importer,
):
    (assembly / importer).write_text(source, encoding="utf-8")
    assert dag.main() == 1
    assert "an owner or adapter imports a composition root" in capsys.readouterr().out


def test_only_the_declared_assembly_dependency_is_accepted(assembly, monkeypatch, capsys):
    (assembly / "root.py").write_text("from .leaf import build", encoding="utf-8")
    assert dag.main() == 1
    assert "undeclared composition dependency" in capsys.readouterr().out
    monkeypatch.setattr(dag, "STAGE4_COMPOSITION_DEPENDENCIES", {"root.py": ["leaf.py"]})
    assert dag.main() == 0


def test_unused_assembly_permissions_are_refused(assembly, monkeypatch, capsys):
    monkeypatch.setattr(dag, "STAGE4_COMPOSITION_DEPENDENCIES", {"root.py": ["leaf.py"]})
    assert dag.main() == 1
    assert "declared composition dependency is absent from code" in capsys.readouterr().out


def test_a_reverse_assembly_import_is_a_cycle_even_with_mixed_import_forms(
    assembly, monkeypatch, capsys,
):
    (assembly / "root.py").write_text("from .leaf import build", encoding="utf-8")
    (assembly / "leaf.py").write_text(
        "from synapse.experiments.gold.root import build", encoding="utf-8",
    )
    monkeypatch.setattr(dag, "STAGE4_COMPOSITION_DEPENDENCIES", {"root.py": ["leaf.py"]})
    assert dag.main() == 1
    output = capsys.readouterr().out
    assert "undeclared composition dependency" in output
    assert "composition cycle" in output


def test_an_absolute_import_cannot_hide_an_owners_adapter_dependency(assembly, capsys):
    (assembly / "owner.py").write_text(
        "from synapse.experiments.gold.adapter import ConcreteAdapter", encoding="utf-8",
    )
    assert dag.main() == 1
    assert "owner imports its own adapter" in capsys.readouterr().out


@pytest.mark.parametrize("dependencies, message", [
    ({"owner.py": ["root.py"]}, "distinct roots"),
    ({"root.py": ["owner.py"]}, "distinct roots"),
    ({"root.py": []}, "distinct roots"),
    ({"root.py": ["root.py"]}, "distinct roots"),
    ({"root.py": ["leaf.py", "leaf.py"]}, "distinct roots"),
    ({"root.py": ["leaf.py"], "leaf.py": ["root.py"]}, "acyclic"),
])
def test_governance_cannot_authorize_domain_access_or_cyclic_assembly(
    tmp_path, dependencies, message,
):
    manifest = copy.deepcopy(dag._OWNERSHIP)
    manifest["owners"] = {"owner.py": "domain"}
    manifest["composition_roots"] = {"root.py": "owner.py", "leaf.py": "owner.py"}
    manifest["composition_dependencies"] = dependencies
    path = tmp_path / "ownership.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        dag._load_ownership_manifest(path)
