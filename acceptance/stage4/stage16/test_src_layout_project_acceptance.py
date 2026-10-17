"""A src-layout project learns a symbol by its import name (binding contract v2).

The operator declares ``python_roots`` when connecting. ``src/shapes/area.py`` then binds as
``shapes.area`` through the canonical CLI: the project record and its status show the roots, and the
published knowledge and its retained bindings carry them, so the knowledge reopens in a new process.
The refusals of the same layout are ``test_src_layout_project_refusal_acceptance.py``.
"""
import json
from pathlib import Path

from acceptance.stage4.stage16 import _src_layout as layout
from acceptance.stage4.stage16._source_inputs import learn, write_learn_input
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V2
from synapse.experiments.gold.knowledge_environment import (
    PROJECT_RECORD_SCHEMA_V2, open_gold_project, read_gold_project_declaration,
)


def test_a_rooted_project_learns_a_symbol_by_its_import_name(tmp_path: Path) -> None:
    repo, revision = layout.repository(tmp_path, {"src/shapes/__init__.py": "", "src/shapes/area.py": layout.AREA,
                                                  "tests/test_area.py": "import shapes.area\n"})
    connected = layout.connect(tmp_path, repo, python_roots=["src"])
    assert connected.returncode == 0, connected.stderr
    state = tmp_path / "state"
    record = json.loads((state / "project.json").read_text(encoding="utf-8"))
    assert (record["schema_version"], record["python_roots"]) == (PROJECT_RECORD_SCHEMA_V2, ["src"])
    assert read_gold_project_declaration(state).python_roots == ("src",)
    assert "Python import roots: src" in layout.cli("project", "status", "--state-dir", str(state)).stdout.splitlines()

    code, result = learn(state, write_learn_input(tmp_path, layout.claim(revision, "shapes.area",
                                                                         BINDING_CONTRACT_VERSION_V2)))

    assert code == 0 and result["status"] == "PUBLISHED", result
    binding = result["knowledge"]["candidates"][0]["bindings"][0]
    assert {key: binding[key] for key in ("module", "qualname", "contract_version", "python_root", "python_roots")} == {
        "module": "shapes.area", "qualname": "circle", "contract_version": BINDING_CONTRACT_VERSION_V2,
        "python_root": "src", "python_roots": ["src"]}
    # The published knowledge reopens in a new process from its retained bindings.
    assert len(open_gold_project(state).library.search_index()) == 1
