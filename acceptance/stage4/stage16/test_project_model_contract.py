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
