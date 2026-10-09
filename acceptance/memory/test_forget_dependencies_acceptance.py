"""Forgetting a basis revokes what depended on it, and a fresh check restores it (review §8.2).

The service-restart scenario: a first session checks the card's restart
command against the runbook, the court records the confirmation, and the
restart runs. The operator then forgets the case that holds that check
(``synapse memory forget``):

* before any consolidation applies the forget, a session that reuses the
  recorded confirmation is refused the restart before any effect — the basis
  it read rests on forgotten results;
* that session's consolidation revokes the confirmations resting on the
  forgotten case — the entity's and the content's, whose checks it held —
  (they return to provisional, which is no refutation) and reports them apart
  from the removed data;
* a later session finds nothing to reuse, checks the claim afresh — the
  runbook is asked again — and the restart runs on the new confirmation.

The environment's own record shows exactly the restarts that were allowed.
"""
from __future__ import annotations

import json

from acceptance.memory import _runbook as runbook


def _refusals(world, run_id):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record["body"] for record in map(json.loads, journal.read_text().splitlines())
            if record["kind"] == "REJECTED" and record["body"]["run_id"] == run_id]


def _content(world):
    hypotheses = world.owner().state()["hypotheses"]
    hypothesis_id, = [key for key, entry in hypotheses.items() if entry["record"]["aspect"] == "content"]
    return hypothesis_id, hypotheses[hypothesis_id]


def test_a_forgotten_basis_is_revoked_and_checked_afresh(tmp_path):
    world = runbook.world(tmp_path)
    world.run(runbook.PROGRAM, "first", runbook.inputs("first", "pg-steady"))
    assert runbook.restarts(world) == [runbook.TRUE]
    hypothesis_id, entry = _content(world)
    assert entry["status"] == "confirmed" and entry["basis"] is not None

    code, payload, stderr = world.memory("forget", "--quantum", entry["basis"], "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    tombstone = payload["acts"][0]["tombstone"]

    # The recorded confirmation is still offered by the pinned snapshot; the effect is refused before it acts.
    world.run(runbook.PROGRAM, "again", runbook.inputs("again", "pg-steady"))
    assert runbook.statuses(world, "again")["content"] == {"status": "confirmed", "reason": "court_record",
                                                          "by": "hypothesis_reused"}
    refused, = _refusals(world, "again")
    assert refused["tool"] == "restart_service"
    # The forgotten case held both checks: the entity's and the content's confirmations rest on it.
    hypotheses = sorted(world.owner().state()["hypotheses"])
    assert hypothesis_id in hypotheses and len(hypotheses) == 2
    assert refused["reason"] == "a required basis changed since it was read: " + "; ".join(
        f"{item} rests on forgotten results ({tombstone})" for item in hypotheses)
    assert runbook.restarts(world) == [runbook.TRUE]

    # Its consolidation revokes the confirmation, apart from the data the forget removed.
    revoked, = [item for report in world.reports() for item in report["dependencies"]]
    assert revoked["tombstone"] == tombstone and revoked["authority_revoked"]["hypotheses"] == hypotheses
    assert revoked["index_withdrawn"] == [] and revoked["authority_revoked"]["habits_archived"] == []
    _, entry = _content(world)
    assert entry["status"] == "provisional" and entry["reason"] == f"basis_forgotten:{tombstone}"

    # A fresh check restores it: the runbook is asked again and the restart runs.
    asked = len(world.calls("runbook"))
    world.run(runbook.PROGRAM, "later", runbook.inputs("later", "pg-steady"))
    assert runbook.statuses(world, "later")["content"]["by"] == "hypothesis_probed"
    assert len(world.calls("runbook")) == asked + 1
    assert runbook.restarts(world) == [runbook.TRUE, runbook.TRUE] and _refusals(world, "later") == []
    assert _content(world)[1]["status"] == "confirmed"
