"""A check of another object never commits a claim, through the canonical launch (review F1).

A clerk states that the balance of account A is 20 and relies on it for a
consequential transfer. The core banking system is the claim's source; the
ledger, an independent service, is the declared check, and the operator's
contract of the ledger binds a check to its claim: the account it is asked
about and the account it answers about name the claim's subject; it answers for
the bank only.

* The program reads account B and asks the ledger about B. The ledger answers
  about B, with balance 20 — the stated value. The claim about A stays
  provisional (``check_about_another:subject``): the fact is not admitted, and
  the transfer is refused before any effect.
* Asked about A, the same ledger answers about A: the claim is confirmed, the
  fact admitted, the transfer done once.
* A later session reuses the court's record of the confirmed claim; the claim
  checked about B was never confirmed, so nothing about it is reused — its
  check runs again.

The environment is the checker's truth: one transfer, from the confirmed claim.
"""
from __future__ import annotations

from acceptance.memory import _bank as bank


def test_a_check_of_another_account_never_commits_the_claim(tmp_path):
    world = bank.world(tmp_path)

    world.run(bank.PROGRAM, "foreign", bank.inputs("foreign", "B", "B"))
    assert bank.decided(world, "foreign") == [("hypothesis_reused", None, "no_memory_snapshot"),
                                              ("hypothesis_probed", "provisional", "check_about_another:subject")]
    assert world.calls("ledger") == [{"account": "B"}]  # The ledger was asked, and answered about B.
    assert bank.admission(world, "foreign") != "admitted"
    assert bank.transfers(world) == 0

    world.run(bank.PROGRAM, "own", bank.inputs("own", "A", "A"))
    assert bank.decided(world, "own")[-1] == ("hypothesis_probed", "confirmed", "check_agrees")
    assert bank.admission(world, "own") == "admitted"
    assert bank.transfers(world) == 1

    # The court's record serves the confirmed claim; the claim checked about B is checked again.
    world.run(bank.PROGRAM, "again", bank.inputs("again", "A", "A"))
    assert bank.decided(world, "again") == [("hypothesis_reused", "confirmed", "court_record")]
    world.run(bank.PROGRAM, "foreign-again", bank.inputs("foreign-again", "B", "B"))
    assert bank.decided(world, "foreign-again")[-1] == ("hypothesis_probed", "provisional",
                                                        "check_about_another:subject")
    assert bank.transfers(world) == 2
    probed = {item["hypothesis"]: item["status"] for report in world.reports()
              for item in report["hypotheses"]["probed"]}
    assert sorted(probed.values()) == ["confirmed", "provisional"]
