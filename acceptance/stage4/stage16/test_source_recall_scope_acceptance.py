"""Recall reads only inside the project's declared read scope.

A learned operation is found by a query inside the read scope; the same query naming a path outside it is
refused, through the canonical CLI, before any operation is read.
"""
import json

from acceptance.stage4.stage16._source_inputs import learn, prepare, recall


def test_recall_beyond_the_declared_read_scope_is_refused(tmp_path):
    _, state, input_path = prepare(tmp_path)
    code, result = learn(state, input_path)
    assert code == 0, result
    claim = json.loads(input_path.read_text())["claim"]

    code, refused = recall(state, tmp_path, claim, scope=["vault.py"])

    assert (code, refused) == (2, {"status": "REFUSED",
                                   "reason": "source recall exceeds the project's declared read scope"})
    code, found = recall(state, tmp_path, claim)
    assert code == 0 and found["state"] == "MATCHES"
    assert [item["experience"]["operation_id"] for item in found["selected"]] == [claim["operation_id"]]
