"""An expired status is checked again, never read as a refutation (review R3, refinement §10).

The court keeps a status fresh for no window at all. A window passes without
the card being read, so the card's revision does not change; the later
session finds the recorded confirmation stale:

* it is no longer reused — the claim is checked again by the runbook;
* the content stands because the new check agrees, and the fact is admitted;
* nothing is ever recorded as refuted: expiry of a check's fitness is not a
  verdict on the content.
"""
from __future__ import annotations

from acceptance.memory import _runbook as runbook


def test_an_expired_status_is_checked_again_never_read_as_refuted(tmp_path):
    world = runbook.world(tmp_path, parameters={"hypothesis_fresh_windows": 0})
    world.run(runbook.PROGRAM, "first", runbook.inputs("first", "pg-good"))
    # A session that does not read the card: a window passes, the card's revision does not change.
    world.run(runbook.PROGRAM, "between", runbook.inputs("between", "pg-good", read_card=False))

    checks = len(world.calls("runbook"))
    world.run(runbook.PROGRAM, "later", runbook.inputs("later", "pg-good"))
    expired = [event for event in world.events("later", "hypothesis_reused")
               if event["reason"] == "stale"]
    assert expired and all(event["status"] is None for event in expired)
    # Checked again rather than refuted: the check is asked once more and agrees.
    assert len(world.calls("runbook")) == checks + 1
    assert runbook.statuses(world, "later")["content"] == {"status": "confirmed", "reason": "check_agrees",
                                                          "by": "hypothesis_probed"}
    admitted = runbook.admission(world, "later")
    assert admitted["decision"] == "admitted" and admitted["attestation"]["outcome"] == "confirmed"
    assert admitted["attestation"]["verification"]["checked_in"] == "later"
    assert all(entry["status"] != "refuted" for report in world.reports()
               for entry in report["apply"]["hypotheses"].values())
