"""A checked status is kept, retrieved and reused only for the same source version (refinement §10).

* Written: the court records the statuses a session's checks gave.
* Retrieved in a new process: a later session reuses them without checking
  again, and the restart runs; the runbook is not asked.
* The card's source changes: the old status does not confirm the new
  content — the claim is checked again.
* With and without the journal: exam A (no accumulated experience) must check;
  exam B (the same snapshot with the journal) reuses. Both restart with the
  same command; the journal saves exactly the checks it did not repeat.
"""
from __future__ import annotations

from acceptance.memory import _runbook as runbook


def test_statuses_persist_and_serve_only_an_unchanged_source(tmp_path):
    world = runbook.world(tmp_path)
    world.run(runbook.PROGRAM, "first", runbook.inputs("first", "pg-good"))
    assert {item["status"] for item in runbook.statuses(world, "first").values()} == {"confirmed"}
    recorded = world.owner().state()["hypotheses"]
    assert {entry["status"] for entry in recorded.values()} == {"confirmed"}

    # A new process, the same card revision: the court's statuses are reused, nothing is checked again.
    checks = {name: len(world.calls(name)) for name in ("runbook", "inventory")}
    world.run(runbook.PROGRAM, "again", runbook.inputs("again", "pg-good"))
    assert runbook.statuses(world, "again") == {
        "entity": {"status": "confirmed", "reason": "court_record", "by": "hypothesis_reused"},
        "content": {"status": "confirmed", "reason": "court_record", "by": "hypothesis_reused"}}
    assert {name: len(world.calls(name)) for name in checks} == checks
    assert runbook.restarts(world) == [runbook.TRUE, runbook.TRUE]
    # Retrieved with its status: the reused confirmation admits the fact in the new process.
    assert runbook.admission(world, "again")["decision"] == "admitted"
    reused = world.reports()[-1]["hypotheses"]["reused"]
    assert {(item["run_id"], item["status"]) for item in reused} == {("again", "confirmed")}

    # The card changes revision: the source version differs, so the claim is checked again.
    world.run(runbook.PROGRAM, "changed", runbook.inputs("changed", "pg-good"))
    reuse = [event for event in world.events("changed", "hypothesis_reused")]
    assert {event["reason"] for event in reuse} == {"source_changed"}
    assert runbook.statuses(world, "changed")["content"]["by"] == "hypothesis_probed"
    assert len(world.calls("runbook")) == checks["runbook"] + 1
    snapshot = world.reports()[-1]["snapshot_boundary_after"]

    # Equal tasks on one snapshot, without and with the journal.
    before = len(world.calls("runbook"))
    world.run(runbook.PROGRAM, "exam-A", runbook.inputs("exam-A", "pg-good"), exam=("A", snapshot))
    without = len(world.calls("runbook")) - before
    before = len(world.calls("runbook"))
    world.run(runbook.PROGRAM, "exam-B", runbook.inputs("exam-B", "pg-good"), exam=("B", snapshot))
    with_journal = len(world.calls("runbook")) - before
    assert runbook.restarts(world)[-2:] == [runbook.TRUE, runbook.TRUE]  # Equal outcomes.
    assert (without, with_journal) == (1, 0)  # The journal saved the repeated check, nothing else.
    assert runbook.statuses(world, "exam-A")["content"]["by"] == "hypothesis_probed"
    assert runbook.statuses(world, "exam-B")["content"] == {"status": "confirmed", "reason": "court_record",
                                                           "by": "hypothesis_reused"}
