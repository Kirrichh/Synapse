"""Pure project-structure cases; graph data conveys no admission authority."""
import base64

import pytest

from synapse.experiments.gold.project_model import build_project_model
from synapse.experiments.gold.source_verification import source_ref


def model(bindings):
    raw = b"def helper():\n    return 1\n"
    reference = source_ref(raw, "project-model-source/v1").to_dict()
    experience = {"bindings": bindings, "sources": [{"path": "src/helpers.py", "ref": reference}],
        "retained": [{"ref": reference, "content_base64url": base64.urlsafe_b64encode(raw).decode().rstrip("=")}]}
    return build_project_model(project_identity="a" * 64,
        resolution={"elements": [], "effect_paths": [], "repository_revision": "b" * 40},
        source_snapshot={"recall": {"selected": [{"experience": experience}]}})


def binding(name, kind):
    return {"path": "src/helpers.py", "qualname": name, "symbol_kind": kind, "module": "src.helpers"}


@pytest.mark.parametrize("module_present", [False, True])
def test_a_studied_top_level_symbol_has_a_real_container_with_or_without_a_module_binding(module_present):
    bindings = [binding("helper", "FUNCTION")]
    if module_present:
        bindings.append(binding("src.helpers", "MODULE"))
    value = model(bindings)
    nodes = {item["element_id"]: item for item in value["nodes"]}
    symbol, = [item for item in nodes.values() if item["name"] == "helper"]
    parent = nodes[symbol["parent_id"]]
    assert parent["kind"] == ("Element" if module_present else "Subsystem")
    if module_present:
        assert parent["symbol_kind"] == "MODULE"
    assert all(edge["source"] != edge["target"] for edge in value["edges"])
    for node in nodes.values():
        seen = set()
        while node["parent_id"] is not None:
            assert node["element_id"] not in seen
            seen.add(node["element_id"])
            node = nodes[node["parent_id"]]
        assert node["kind"] == "System"


def test_qualified_symbols_keep_their_known_owner_without_a_module_binding():
    value = model([binding("Box", "CLASS"), binding("Box.method", "FUNCTION")])
    nodes = {item["name"]: item for item in value["nodes"]}
    assert nodes["Box.method"]["parent_id"] == nodes["Box"]["element_id"]
    assert nodes["Box"]["parent_id"] != nodes["Box"]["element_id"]


def test_absolute_imports_resolve_against_the_import_names_of_a_rooted_project():
    # Binding contract v2 names ``src/shapes/__init__.py`` ``shapes`` under the root ``src``; contract v1
    # names it ``src.shapes``, and the tests' ``import shapes`` then resolves to nothing.
    sources = {"src/shapes/__init__.py": b"", "tests/test_area.py": b"import shapes\n"}
    refs = {path: source_ref(raw, "project-model-source/v1").to_dict() for path, raw in sources.items()}

    def build(package):
        experience = {
            "bindings": [
                {"path": "src/shapes/__init__.py", "qualname": package, "symbol_kind": "MODULE", "module": package},
                {"path": "tests/test_area.py", "qualname": "tests.test_area", "symbol_kind": "MODULE",
                 "module": "tests.test_area"},
            ],
            "sources": [{"path": path, "ref": refs[path]} for path in sources],
            "retained": [{"ref": refs[path], "content_base64url": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
                         for path, raw in sources.items()],
        }
        value = build_project_model(project_identity="a" * 64,
            resolution={"elements": [], "effect_paths": [], "repository_revision": "b" * 40},
            source_snapshot={"recall": {"selected": [{"experience": experience}]}})
        names = {item["element_id"]: item["name"] for item in value["nodes"]}
        imports = {(names[edge["source"]], names[edge["target"]]) for edge in value["edges"] if edge["kind"] == "IMPORTS"}
        return imports, [item["module"] for item in value["unresolved_dependencies"]]

    assert build("shapes") == ({("tests.test_area", "shapes")}, [])
    assert build("src.shapes") == (set(), ["shapes"])
