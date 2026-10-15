"""Retention by significance: compaction only when provably reconstructible (refinement §9, И9).

Sessions count stock through the canonical launch. One consolidation after a
case was made, retention acts on it: the medium case's raw trace leaves the
owner's custody only after its session, re-executed from the replay data and
the recorded results alone, reproduces the case's exact events; the low case
keeps its tail form and is then rolled up into an aggregate that names it. The
tool server shows that no reconstruction repeated an external call. The
operator returns a compacted case to processing: the reproduced trace comes
back to the same address, and the case gains no new vote.
"""
from __future__ import annotations

from acceptance.memory import _stock as stock


def _state(world):
    return world.owner().state()


def test_cases_leave_custody_only_as_reproducible_and_return_without_a_new_vote(tmp_path):
    world = stock.world(tmp_path)
    stock.count(world, "first")
    first = stock.cases(world, "first")
    medium, low = first["medium"], first["low"]
    evidence = world.evidence()
    assert evidence.get(medium["raw_ref"]) is not None and evidence.get(low["raw_ref"]) is not None
    assert medium["retention"] == {"action": "delete_raw_trace_after_replay", "due": 2}
    assert low["retention"] == {"action": "keep_tail_form", "due": 2}

    # The next consolidation makes them due: retention reproduces the first session and acts.
    stock.count(world, "second")
    calls = world.world()["calls"]
    assert evidence.get(medium["raw_ref"]) is None and evidence.gone(medium["raw_ref"])["reason"] == "compacted"
    assert evidence.get(low["raw_ref"]) is None and evidence.gone(low["raw_ref"])["reason"] == "compacted"
    passes = world.owner().retention_passes()
    acts = {(act["qid"], act["act"]) for item in passes for act in item["acts"]}
    assert {(medium["qid"], "raw_deleted"), (low["qid"], "tail")} <= acts
    deleted, = [act for item in passes for act in item["acts"] if act["qid"] == medium["qid"]]
    assert deleted["replay"] == "replay_verified"

    # The following report applies the recorded facts; the verdict and outcome stay explained.
    stock.count(world, "third")
    applied = {(act["qid"], act["act"]) for act in world.reports()[-1]["retention"]["applied"]}
    assert {(medium["qid"], "raw_deleted"), (low["qid"], "tail")} <= applied
    state = _state(world)
    assert state["quanta"][medium["qid"]]["retention_state"] == "replay_only"
    assert state["quanta"][medium["qid"]]["canonical"]["outcome_class"] == "success"
    assert state["quanta"][low["qid"]]["retention_state"] == "tail"
    assert state["quanta"][low["qid"]]["syn_form"] is None and state["quanta"][low["qid"]]["replay_ref"] is None
    verdict, = [item for report in world.reports() for item in report["marker_verdicts"] if item["run_id"] == "first"]
    assert verdict["verdict"] == "uncertain"
    # Reconstruction consumed recorded results only: the environment saw no call from retention.
    assert world.world()["calls"][:len(calls)] == calls
    assert world.calls("inventory") == [{"item": "first"}, {"item": "second"}, {"item": "third"}]

    # The operator returns the compacted case: the same trace at the same address, no call, no vote.
    code, payload, stderr = world.memory("restore", "--quantum", medium["qid"])
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    assert evidence.get(medium["raw_ref"]) is not None and evidence.gone(medium["raw_ref"]) is None
    assert world.calls("inventory") == [{"item": "first"}, {"item": "second"}, {"item": "third"}]
    stock.count(world, "fourth")
    state = _state(world)
    restored = state["quanta"][medium["qid"]]
    assert restored["retention_state"] == "full" and restored["tier_rule"] == "restored"
    assert restored["stats"] == medium["stats"]
    # The tail case was rolled up into an aggregate that names it.
    assert any(low["qid"] in item["qids"] for item in state["retention"]["rollups"])
    assert state["quanta"][low["qid"]]["retention_state"] == "rolled_up"

    # A restore of a case whose trace is held, or of an unknown case, is refused and changes nothing.
    journal = world.journal()
    code, payload, _ = world.memory("restore", "--quantum", medium["qid"])
    assert code == 2 and payload["status"] == "REFUSED" and world.journal() == journal
