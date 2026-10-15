"""Memory an earlier court decided is reassessed from its record before it is used (second review).

The bank clerk's memory was decided by the court of the earlier policy (v3),
whose hypothesis check compared the stated balance and nothing else. That court
ran here in this process, through the canonical entry, with the earlier rule in
place of the current one: it confirmed the claim about account A from a ledger
answer about account B, and the transfer went through; it confirmed the claim
again from a ledger answer about A itself.

Under the current court (v6), in its own processes:

* a session is refused before anything runs — the memory was decided under the
  earlier policy and must be reassessed first;
* ``synapse memory reassess`` calls no tool: it reads the ledger answers the
  gateway recorded and decides both statuses again — the one checked about B
  is provisional (``check_about_another:subject``), the one checked about A
  stays confirmed under the current rule;
* the reassessed state is the fold of the journal, read again by a fresh owner
  from its verified snapshot and tail;
* a later session reuses the confirmed status of the claim checked about A and
  transfers; the claim read and checked about B is checked again, stays
  provisional, and the transfer is refused before any effect.

The environment is the checker's truth: no new effect from the foreign check.
"""
from __future__ import annotations

from acceptance.memory import _bank as bank
from synapse.memory_consolidation.court.projection import fold
from synapse.memory_consolidation.owner import MemoryOwner

EARLIER_POLICY = "synapse.memory.court-policy/v3"
EARLIER_RULE = "synapse.memory.hypothesis-check/v1"


def _earlier_court(world, monkeypatch, run_id, read, check):
    """One session of the earlier court: the canonical entry in this process, the earlier policy's version and
    hypothesis rule (the stated fields compared, the check's subject never read) in place of the current ones."""
    from synapse import cli
    from synapse.memory_consolidation import hypotheses, policy

    with monkeypatch.context() as earlier:
        earlier.setattr(policy, "POLICY_V6", EARLIER_POLICY)
        earlier.setattr(hypotheses, "CHECK_RULE", EARLIER_RULE)
        earlier.setattr(hypotheses, "_about_claim", lambda record, payload, configuration: None)
        assert cli.main([str(item) for item in world._run_arguments(
            bank.PROGRAM, run_id, bank.inputs(run_id, read, check))]) == 0


def test_an_earlier_courts_confirmations_are_decided_again_before_use(tmp_path, monkeypatch):
    world = bank.world(tmp_path)
    _earlier_court(world, monkeypatch, "foreign", "B", "B")
    _earlier_court(world, monkeypatch, "own", "A", "A")
    assert bank.transfers(world) == 2  # The earlier rule let the foreign check open the transfer too.
    earlier = {item["hypothesis"]: item for report in world.reports() for item in report["hypotheses"]["probed"]}
    assert sorted(item["status"] for item in earlier.values()) == ["confirmed", "confirmed"]
    assert {report["policy"]["policy"] for report in world.reports()} == {EARLIER_POLICY}

    # The current court refuses to run on memory it has not reassessed.
    calls = len(world.world()["calls"])
    code, _, _ = world.attempt(bank.PROGRAM, "early", bank.inputs("early", "A", "A"),
                               configuration=world.configuration_path)
    assert code != 0 and len(world.world()["calls"]) == calls

    code, result, stderr = world.memory("reassess")
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    assert len(world.world()["calls"]) == calls  # Decided from the record: no tool was called.
    reassessment = result["consolidation"]["reassessment"]
    assert reassessment["policy"] == {"from": EARLIER_POLICY, "to": "synapse.memory.court-policy/v6"}
    decided = {item["hypothesis"]: (item["from"]["status"], item["to"]["status"], item["to"]["reason"])
               for item in reassessment["hypotheses"]}
    foreign, own = (next(key for key, item in earlier.items() if item["run_id"] == run) for run in ("foreign", "own"))
    assert decided == {foreign: ("confirmed", "provisional", "check_about_another:subject"),
                       own: ("confirmed", "confirmed", "check_agrees")}

    # What the reassessment decided is the memory's state, read again by a fresh owner from snapshot and tail.
    owner = MemoryOwner(world.state, read_only=True)
    state = owner.state()
    assert state == fold(item["report"] for item in owner.applied() if item["report"] is not None)
    assert (state["hypotheses"][foreign]["status"], state["hypotheses"][own]["status"]) == ("provisional",
                                                                                           "confirmed")

    world.run(bank.PROGRAM, "own-again", bank.inputs("own-again", "A", "A"))
    assert bank.decided(world, "own-again") == [("hypothesis_reused", "confirmed", "court_record")]
    assert bank.transfers(world) == 3
    world.run(bank.PROGRAM, "foreign-again", bank.inputs("foreign-again", "B", "B"))
    assert bank.decided(world, "foreign-again") == [
        ("hypothesis_reused", None, "still_provisional"),
        ("hypothesis_probed", "provisional", "check_about_another:subject")]
    assert bank.admission(world, "foreign-again") != "admitted"
    assert bank.transfers(world) == 3  # The foreign check opens nothing any more.
