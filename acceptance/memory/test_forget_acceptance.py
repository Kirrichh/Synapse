"""Governed forgetting ends every chain at an explained tombstone (spec part 3 §8.7; refinement §9).

The operator forgets one case through ``synapse memory forget`` with a reason.
The act is recorded before anything is removed; the case's raw trace and its
recorded results leave store D behind markers that name the tombstone, so a
reader of the old decision finds an explicit, attributed end instead of a
dangling reference. The next consolidation applies the tombstone to memory
state; the court keeps working, and other cases are untouched.
"""
from __future__ import annotations

from acceptance.memory import _stock as stock


def test_a_forgotten_case_leaves_a_tombstone_and_nothing_dangling(tmp_path):
    world = stock.world(tmp_path, parameters={"n_medium_windows": 30, "n_low_windows": 30})
    stock.count(world, "first")
    stock.count(world, "second")
    forgotten, kept = stock.cases(world, "first")["medium"], stock.cases(world, "second")["medium"]
    evidence = world.evidence()

    code, payload, stderr = world.memory("forget", "--quantum", forgotten["qid"], "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    act, = payload["acts"]
    assert act["qid"] == forgotten["qid"] and act["authority"] == "GOVERNING_HUMAN"
    tombstone = act["tombstone"]
    for ref in [forgotten["raw_ref"], *forgotten["evidence_refs"]]:
        assert evidence.get(ref) is None
        assert evidence.gone(ref) == {"schema": "synapse.memory.evidence-gone/v1", "ref": ref,
                                      "reason": "forgotten", "tombstone": tombstone}
    # The other case keeps everything.
    assert evidence.get(kept["raw_ref"]) is not None
    assert all(evidence.get(ref) is not None for ref in kept["evidence_refs"])

    # The court keeps working and applies the tombstone.
    stock.count(world, "third")
    state = world.owner().state()
    entry = state["quanta"][forgotten["qid"]]
    assert entry["retention_state"] == "forgotten" and entry["tombstone"] == tombstone
    assert entry["syn_form"] is None and entry["replay_ref"] is None and entry["evidence_refs"] == []
    assert state["retention"]["tombstones"][forgotten["qid"]]["reason"] == "data subject request"
    assert state["quanta"][kept["qid"]]["retention_state"] == "full"

    # Forgetting it again, or an unknown case, is refused and records nothing.
    journal = world.journal()
    code, payload, _ = world.memory("forget", "--quantum", forgotten["qid"], "--reason", "again",
                                    "--operator", "acceptance.operator")
    assert code == 2 and payload["status"] == "REFUSED" and world.journal() == journal
