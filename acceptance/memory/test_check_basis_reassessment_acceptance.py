"""A confirmation does not outlive the check basis it was decided on (review N2).

The bank clerk's claim about account A is confirmed by the ledger, asked about
A and answering about A, and the transfer goes through. The current check rule
decided it — but the rule's version says nothing about what the check rests on
besides its answer: the ledger's contract, the provenance relation of ledger and
core banking, the identity rules. The operator then adopts another
configuration:

* the ledger's contract now says it answers for the cards division only — its
  answer about the bank's account is about another scope;
* the provenance graph now shows the ledger derived from core banking — the
  check repeats the claim's own source.

A session is refused under the new configuration until the memory is judged
again under it. ``synapse memory reassess`` calls no tool: it decides the
recorded ledger answer again under the new configuration, and the claim is no
longer confirmed. A later session does not reuse a confirmation; it checks the
claim again, the check confirms nothing, and the transfer is refused before
any effect. With the configuration unchanged, the same reassessment keeps the
confirmation, and the later session reuses it and transfers.

The environment is the checker's truth: transfers happen only on a confirmation
of the configuration in force.
"""
from __future__ import annotations

import json

import pytest

from acceptance.memory import _bank as bank

CHANGES = {
    "scope": ("provisional", "check_about_another:scope"),
    "provenance": ("provisional", "checking_source_dependent"),
    "unchanged": ("confirmed", "check_agrees"),
}


def _adopted(world, change):
    configuration = json.loads(world.configuration_path.read_text())
    if change == "scope":
        ledger = next(item for item in configuration["tools"]["tools"] if item["name"] == "ledger")
        ledger["contract"]["verifies"]["scope"] = {"value": "cards"}
    elif change == "provenance":
        configuration["tools"]["provenance"]["ledger:bank"]["ancestors"] = ["core:bank"]
    path = world.root / f"memory.{change}.json"
    path.write_text(json.dumps(configuration, sort_keys=True))
    return path


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_confirmation_is_decided_again_under_the_configuration_adopted(tmp_path, change):
    world = bank.world(tmp_path)
    world.run(bank.PROGRAM, "own", bank.inputs("own", "A", "A"))
    assert bank.decided(world, "own")[-1] == ("hypothesis_probed", "confirmed", "check_agrees")
    assert bank.transfers(world) == 1

    adopted = _adopted(world, change)
    calls = len(world.world()["calls"])
    if change != "unchanged":
        code, _, _ = world.attempt(bank.PROGRAM, "early", bank.inputs("early", "A", "A"), configuration=adopted)
        assert code != 0 and len(world.world()["calls"]) == calls  # Not before the memory is judged under it.

    code, result, stderr = world.memory("reassess", configuration=adopted)
    assert code == 0 and result["status"] == "RECORDED", (code, result, stderr)
    assert len(world.world()["calls"]) == calls  # Decided from the record: no tool was called.
    decided, = result["consolidation"]["reassessment"]["hypotheses"]
    assert (decided["from"]["status"], decided["to"]["status"], decided["to"]["reason"]) == (
        "confirmed", *CHANGES[change])
    assert (decided["to"]["check_basis"] == decided["from"]["check_basis"]) is (change == "unchanged")

    code, payload, stderr = world.attempt(bank.PROGRAM, "after", bank.inputs("after", "A", "A"),
                                          configuration=adopted)
    assert code == 0 and payload["status"] == "COMPLETED", (code, payload, stderr)
    if change == "unchanged":
        assert bank.decided(world, "after") == [("hypothesis_reused", "confirmed", "court_record")]
        assert bank.admission(world, "after") == "admitted" and bank.transfers(world) == 2
    else:
        assert bank.decided(world, "after") == [("hypothesis_reused", None, "still_provisional"),
                                                ("hypothesis_probed", *CHANGES[change])]
        assert bank.admission(world, "after") != "admitted" and bank.transfers(world) == 1
