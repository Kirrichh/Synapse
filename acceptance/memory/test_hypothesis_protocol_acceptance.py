"""Hypotheses become commitments only through a fitting check (refinement §10, §18 "Гипотеза").

Each session states an entity and a content hypothesis read from a recorded
knowledge-base card and relies on both for a consequential restart:

* the true card is confirmed by the inventory and the runbook, and the restart runs;
* a poisoned card with the right entity: the entity is confirmed, the content
  refuted, and the restart is refused before any effect;
* a check that cannot determine the answer keeps the content provisional;
* a claim whose source was never observed stays provisional, and its check is
  not even performed;
* a mirror of the knowledge base answers like the card, but a dependent
  source never confirms: the content stays provisional.

The environment is the checker's truth: only the confirmed command was ever
executed. The court records every status, the check's case as its basis.
"""
from __future__ import annotations

import json

from acceptance.memory import _runbook as runbook


def _refusals(world, run_id):
    journal = world.owner().gateway_root / "journal.jsonl"
    return [record["body"]["reason"] for record in map(json.loads, journal.read_text().splitlines())
            if record["kind"] == "REJECTED" and record["body"]["run_id"] == run_id]


def test_only_confirmed_hypotheses_open_a_consequential_action(tmp_path):
    world = runbook.world(tmp_path)

    world.run(runbook.PROGRAM, "good", runbook.inputs("good", "pg-good"))
    assert runbook.statuses(world, "good") == {
        "entity": {"status": "confirmed", "reason": "check_agrees", "by": "hypothesis_probed"},
        "content": {"status": "confirmed", "reason": "check_agrees", "by": "hypothesis_probed"}}
    assert runbook.restarts(world) == [runbook.TRUE]
    # The status travels with the information into reasoning: the fact is admitted on the live status.
    admitted = runbook.admission(world, "good")
    assert admitted["decision"] == "admitted" and admitted["checked"][0]["status_basis"] == "hypothesis"

    # The right entity with a poisoned command: nothing reaches the environment.
    world.run(runbook.PROGRAM, "bad", runbook.inputs("bad", "pg-bad"))
    found = runbook.statuses(world, "bad")
    assert found["entity"]["status"] == "confirmed"
    assert found["content"] == {"status": "refuted", "reason": "contradicted:command", "by": "hypothesis_probed"}
    assert runbook.admission(world, "bad")["checked"][0]["reasons"] == ["not_confirmed:refuted"]
    refusal, = _refusals(world, "bad")
    assert refusal.startswith("a required hypothesis is not established")
    assert runbook.restarts(world) == [runbook.TRUE]

    # "Cannot determine" is neither a confirmation nor a refutation.
    world.run(runbook.PROGRAM, "mute", runbook.inputs("mute", "pg-mute", check_args={"service": "postgresql",
                                                                                     "topic": "reload"}))
    assert runbook.statuses(world, "mute")["content"]["status"] == "provisional"
    assert runbook.statuses(world, "mute")["content"]["reason"] == "check_cannot_determine"
    assert len(_refusals(world, "mute")) == 1
    assert runbook.admission(world, "mute")["decision"] == "abstained"

    # No observed source: provisional, and the check is not performed.
    checks = len(world.calls("runbook"))
    world.run(runbook.PROGRAM, "absent", runbook.inputs("absent", "pg-none", read_card=False))
    assert runbook.statuses(world, "absent")["content"] == {"status": "provisional", "reason": "source_absent",
                                                            "by": "hypothesis_probed"}
    assert len(world.calls("runbook")) == checks and len(_refusals(world, "absent")) == 1

    # A copy of the claim's own source never confirms it.
    world.run(runbook.PROGRAM, "mirror", runbook.inputs("mirror", "pg-bad", checker="kb_mirror",
                                                        check_args={"id": "pg-bad"}))
    assert runbook.statuses(world, "mirror")["content"] == {
        "status": "provisional", "reason": "checking_source_dependent", "by": "hypothesis_probed"}
    assert runbook.restarts(world) == [runbook.TRUE]

    # The court records every decided status with the case that holds its check.
    probed = [item for report in world.reports() for item in report["hypotheses"]["probed"]]
    assert {(item["run_id"], item["status"]) for item in probed} >= {
        ("good", "confirmed"), ("bad", "refuted"), ("mute", "provisional"), ("absent", "provisional"),
        ("mirror", "provisional")}
    assert all(item["basis"] for item in probed if item["run_id"] == "good")
    # The refused sessions' segments are judged failed: the required restart never ran.
    for run_id in ("bad", "mute", "absent", "mirror"):
        verdict, = [item for report in world.reports() for item in report["marker_verdicts"]
                    if item["run_id"] == run_id]
        assert verdict["verdict"] == "failed"
