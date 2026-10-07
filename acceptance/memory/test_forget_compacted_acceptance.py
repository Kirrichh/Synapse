"""A forget of a compacted case leaves its own tombstone (review AUD-5).

Sessions count stock; with one medium window, retention compacts the first
case's raw trace after the second session (a ``compacted`` marker, no
tombstone). The operator then forgets that case:

* the raw trace's marker now says ``forgotten`` and names the forget's
  tombstone, as every other body the forget removed does;
* later sessions — retention running again — never lower it back to
  ``compacted``.
"""
from __future__ import annotations

from acceptance.memory import _stock as stock

GONE = "synapse.memory.evidence-gone/v1"


def test_a_forget_of_a_compacted_case_names_its_tombstone(tmp_path):
    world = stock.world(tmp_path, parameters={"n_medium_windows": 1})
    stock.count(world, "first")
    stock.count(world, "second")
    case = stock.cases(world, "first")["medium"]
    evidence = world.evidence()
    assert evidence.gone(case["raw_ref"]) == {"schema": GONE, "ref": case["raw_ref"], "reason": "compacted",
                                              "tombstone": None}

    code, payload, stderr = world.memory("forget", "--quantum", case["qid"], "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    act, = payload["acts"]
    for ref in [case["raw_ref"], *case["evidence_refs"]]:
        assert evidence.gone(ref) == {"schema": GONE, "ref": ref, "reason": "forgotten",
                                      "tombstone": act["tombstone"]}, ref

    stock.count(world, "third")
    stock.count(world, "fourth")
    assert evidence.gone(case["raw_ref"])["tombstone"] == act["tombstone"]
    assert world.owner().state()["quanta"][case["qid"]]["retention_state"] == "forgotten"
