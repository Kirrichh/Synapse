"""Recall profile v2: what an operation itself names and observed decides before its sources' text.

Verified operations study one module whose docstring holds every word of the task. Profile v1 scores them
equally on that text, so the alphabet of their operation ids decides and the operation whose symbol the task
names can fall below the limit. Profile v2, chosen in the query, ranks by tiers (the operation's matched
paths, its paths and bound names, its command's output, then its sources' text) and reports each tier's terms
and how many operations only the id ordered. Malformed queries are ``test_source_recall_query_contract.py``.
"""
import json
import sys

from acceptance.stage4.stage16._source_inputs import learn, prepare, recall
from synapse.experiments.gold.bindings import BINDING_CONTRACT_VERSION_V1
from synapse.experiments.gold.source_experience import (
    SOURCE_RECALL_PROFILE_V1, SOURCE_RECALL_PROFILE_V2, SOURCE_RECALL_V1, SOURCE_RECALL_V2,
)

GEOMETRY = '''"""Shapes: square a value, halve a value, double the area of an input shape."""


def square(value):
    return value * value


def halve(value):
    return value / 2


def double_area(value):
    return value * 2
'''
STATEMENT = "square the input value"
# One operation runs a command that prints task words on both streams; its own names hold none of them.
PRINTED = {"command": [sys.executable, "-B", "-c",
                       "import sys; print('square'); print('value input observed', file=sys.stderr)"],
           "expectation": {"expected_exit_codes": [0], "expected_nonzero_exit": False,
                           "combined_output_contains": ["square", "value input observed"],
                           "combined_output_not_contains": [], "timeout_seconds": 10}}


def _symbol(path, module, qualname):
    return {"path": path, "module": module, "qualname": qualname, "symbol_kind": "FUNCTION",
            "contract_version": BINDING_CONTRACT_VERSION_V1}


def test_v1_ties_on_the_text_and_v2_ranks_by_tiers(tmp_path):
    _, state, path = prepare(tmp_path, execute=True, extra_sources={"geometry.py": GEOMETRY})
    base = json.loads(path.read_text())
    for operation_id, sources, symbol, recipe in (
            ("a-halve", ["geometry.py"], _symbol("geometry.py", "geometry", "halve"), None),
            ("b-double-area", ["geometry.py"], _symbol("geometry.py", "geometry", "double_area"), None),
            ("c-square", ["geometry.py"], _symbol("geometry.py", "geometry", "square"), None),
            ("d-run-halve", ["geometry.py"], _symbol("geometry.py", "geometry", "halve"), PRINTED),
            ("e-both-files", ["calc.py", "geometry.py"], _symbol("calc.py", "calc", "double"), None)):
        value = json.loads(json.dumps(base))
        value["claim"].update(operation_id=operation_id, sources=sources, symbols=[symbol],
                              kind="REPOSITORY_FACT_CHECK" if recipe is None else "VERIFICATION_RECIPE", recipe=recipe)
        path.write_text(json.dumps(value))
        code, result = learn(state, path)
        assert code == 0 and result["status"] == "PUBLISHED", result
    geometry = {**base["claim"], "sources": ["geometry.py"]}

    code, v1 = recall(state, tmp_path, geometry, statement=STATEMENT, limit=1)
    assert code == 0, v1
    assert (v1["schema_version"], v1["profile"]) == (SOURCE_RECALL_V1, SOURCE_RECALL_PROFILE_V1)
    # Every operation holds the same text terms; only the alphabet of the ids ordered them.
    assert [item["experience"]["operation_id"] for item in v1["selected"]] == ["a-halve"]
    assert v1["selected"][0]["match"] == {"paths": ["geometry.py"], "terms": ["input", "square", "the", "value"]}
    # v1 counts a command's output among the terms as well.
    code, observed = recall(state, tmp_path, geometry, statement="observed value", limit=1)
    assert [item["experience"]["operation_id"] for item in observed["selected"]] == ["d-run-halve"]

    code, v2 = recall(state, tmp_path, geometry, statement=STATEMENT, limit=4, profile=SOURCE_RECALL_PROFILE_V2)

    assert code == 0, v2
    assert (v2["schema_version"], v2["profile"]) == (SOURCE_RECALL_V2, SOURCE_RECALL_PROFILE_V2)
    assert v2["query"]["profile"] == SOURCE_RECALL_PROFILE_V2
    # The named symbol first, then what a command observed on either stream, then the text alone, where only
    # the ids order.
    assert [(item["experience"]["operation_id"], item["match"]["tie_group"]) for item in v2["selected"]] == [
        ("c-square", 1), ("d-run-halve", 1), ("a-halve", 3), ("b-double-area", 3)]
    assert v2["selected"][1]["match"]["terms"]["output"] == ["input", "square", "value"]
    assert v2["selected"][0]["match"] == {
        "paths": ["geometry.py"],
        "terms": {"identity": ["square"], "output": [], "source": ["input", "square", "the", "value"]},
        "tie_group": 1}
    # Matched paths come before every term tier: the operation over both files of the scope ranks first.
    code, both = recall(state, tmp_path, {**geometry, "sources": ["calc.py", "geometry.py"]}, statement=STATEMENT,
                        limit=2, profile=SOURCE_RECALL_PROFILE_V2)
    assert [(item["experience"]["operation_id"], item["match"]["paths"]) for item in both["selected"]] == [
        ("e-both-files", ["calc.py", "geometry.py"]), ("c-square", ["geometry.py"])]
