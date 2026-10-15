"""The memory state is read from a verified snapshot and the journal's tail (review §8.3).

Nine stock-count sessions, each consolidated through the canonical launch. The
court — the one writer — records a snapshot of the memory state after every
eighth report, naming the projection version, the cut it was taken at (the
decision and the report) and the digest of the state:

* the state read from the snapshot and the reports after it is exactly the
  fold of every report from the journal;
* a snapshot that is damaged (its state no longer matches its digest), of
  another projection version, or taken at a cut the chain does not hold (an
  unknown decision, or the head decision with another report) is never used —
  the state is still exactly the fold;
* sessions keep running on the memory throughout;
* a decision that names a report receipt other than its consolidation's
  recorded report is refused: the state is never folded from a report the
  decision did not name.
"""
from __future__ import annotations

import copy

import pytest

from acceptance.memory import _stock as stock
from synapse.experiments.gold.project_court import append_decision, establish_tail
from synapse.memory_consolidation import records
from synapse.memory_consolidation.court.projection import fold
from synapse.memory_consolidation.owner import MemoryOwner, MemoryOwnerViolation


def _snapshots(world):
    return [event["payload"] for event, _ in world.journal() if event["kind"] == "MEMORY_STATE"]


def _folded(world):
    return fold(item["report"] for item in world.owner().applied() if item["report"] is not None)


def _forge(world, payload, name):
    owner = MemoryOwner(world.state)
    with owner.store.session() as guard:
        owner.store.put(kind="MEMORY_STATE", guard=guard, payload=payload,
                        job_key=records.digest({"forged": name}))


def test_the_state_is_a_verified_snapshot_and_the_tail_never_a_guess(tmp_path):
    world = stock.world(tmp_path, parameters={"n_medium_windows": 100, "n_low_windows": 100})
    for index in range(9):
        stock.count(world, f"count-{index}")
    snapshot, = _snapshots(world)
    chain = world.owner().applied()
    assert snapshot["through"]["index"] == 7 and len(chain) == 9
    assert snapshot["through"]["consolidation_id"] == chain[7]["decision"]["consolidation"]["consolidation_id"]
    assert snapshot["state_sha256"] == records.digest(snapshot["state"])
    assert world.owner().state() == _folded(world)

    head = chain[-1]["decision"]["consolidation"]
    cut = {"index": 8, "consolidation_id": head["consolidation_id"], "report": head["report"]}
    damaged = copy.deepcopy(snapshot)
    damaged.update(through=cut)
    damaged["state"]["window"] += 100  # The state no longer matches the digest it names.
    _forge(world, damaged, "damaged")
    # Self-consistent snapshots whose state differs from the fold: only their version or cut gives them away.
    wrong = {**_folded(world), "window": 0}
    other_version = {**copy.deepcopy(snapshot), "through": cut, "projection": "synapse.memory.state-projection/v0",
                     "state": wrong, "state_sha256": records.digest(wrong)}
    _forge(world, other_version, "other-version")
    elsewhere = {**copy.deepcopy(snapshot), "through": {**cut, "consolidation_id": "con_" + "0" * 64},
                 "state": wrong, "state_sha256": records.digest(wrong)}
    _forge(world, elsewhere, "elsewhere")
    another_report = {**copy.deepcopy(snapshot), "through": {**cut, "report": chain[7]["decision"]["consolidation"][
        "report"]}, "state": wrong, "state_sha256": records.digest(wrong)}
    _forge(world, another_report, "another-report")
    assert len(_snapshots(world)) == 5
    assert world.owner().state() == _folded(world)

    # The memory keeps working on the journal it is the fold of.
    stock.count(world, "count-after")
    assert world.owner().state() == _folded(world) and world.owner().state()["window"] == 10

    # A decision naming the head consolidation with an earlier report's receipt: the chain holds it, the fold refuses.
    owner = MemoryOwner(world.state)
    head = owner.applied()[-1]["decision"]["consolidation"]
    with owner.store.session() as guard:
        tail = establish_tail(owner.store, guard, project_identity=owner.identity)
        append_decision(owner.store, guard, project_identity=owner.identity, tail=tail,
                        consolidation={**head, "report": chain[0]["decision"]["consolidation"]["report"]})
    with pytest.raises(MemoryOwnerViolation, match="names no consolidation report"):
        owner.state()
