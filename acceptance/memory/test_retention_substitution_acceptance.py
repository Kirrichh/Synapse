"""A recorded result substituted in store D blocks compaction (refinement §9; archive check F6).

A case's recorded answer is altered in the owner's store D before its raw
trace is due. When retention re-executes the session, the altered result no
longer resolves to its address, so the case is not reproducible: its raw
trace stays in custody and the case rises to high significance with the
reason. A case of the same age whose results are intact is compacted.
"""
from __future__ import annotations

from acceptance.memory import _stock as stock


def test_a_case_whose_recorded_result_was_substituted_keeps_its_raw_trace(tmp_path):
    world = stock.world(tmp_path)
    stock.count(world, "first")
    medium = stock.cases(world, "first")["medium"]
    evidence = world.evidence()
    counted, = [ref for ref in medium["evidence_refs"]]
    path = evidence.root / f"{counted}.json"
    path.write_bytes(path.read_bytes().replace(b'"count":7', b'"count":9'))

    stock.count(world, "second")
    act, = [act for item in world.owner().retention_passes() for act in item["acts"] if act["qid"] == medium["qid"]]
    assert act["act"] == "kept" and act["reason"] == "retention_replay_diverged"
    assert evidence.get(medium["raw_ref"]) is not None and evidence.gone(medium["raw_ref"]) is None

    stock.count(world, "third")
    kept = world.owner().state()["quanta"][medium["qid"]]
    assert kept["tier"] == "high" and kept["tier_rule"] == "retention_replay_diverged"
    assert kept["retention_state"] == "full" and kept["retention"] is None
    # The intact case of the second session was compacted as planned.
    second = stock.cases(world, "second")["medium"]
    assert evidence.get(second["raw_ref"]) is None and evidence.gone(second["raw_ref"])["reason"] == "compacted"
