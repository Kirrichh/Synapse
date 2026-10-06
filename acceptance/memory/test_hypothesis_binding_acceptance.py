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

from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "clerk" {
  rooms { episodic procedural }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "transfer", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "pay", "intent": "pay from account A on its confirmed balance", "element_part": "bank", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "pay from account A", "segments": [seg]})
context "pay" {
  let observed = tool("core", {"account": read_account})
  let origin = {"tool": "core", "args": {"account": read_account}}
  let h = hypothesis({"aspect": "content", "subject": "A", "statement": {"balance": 20}, "scope": "bank", "source": origin, "check": {"tool": "ledger", "args": {"account": check_account}}})
  if established(h) == false {
    let checked = probe(h)
  }
  let card = {"id": "card-A", "entity": "A", "attribute": "balance", "value": 20, "source": "core:bank", "hypothesis": h.id, "content": "balance of account A"}
  let admitted = admit([card], {"entity": "A", "attribute": "balance", "keys": ["account", "balance"], "scope": "bank"})
  try {
    let done = tool("transfer", {"from": "A", "amount": 20}, {"requires": [h]})
  } catch (ACTION_FAILED as refused) {
    print("abstained")
  }
}
print("done")
'''


def _world(root):
    accounts = [answer({"ok": True, "account": name, "balance": 20}, when={"account": name}) for name in ("A", "B")]
    tools = [
        tool("core", "core:bank", accounts, server="core"),
        tool("ledger", "ledger:bank", accounts, server="ledger", contract={"verifies": {
            "subject": {"request": "account", "answer": "account"}, "scope": {"value": "bank"}}}),
        tool("transfer", "payments:bank", [answer({"ok": True, "paid": True}, effect="paid")], server="payments",
             contract={"requires_established": True})]
    return MemoryWorld(root, tools, provenance={"core:bank": {"ancestors": []}, "ledger:bank": {"ancestors": []},
                                                "payments:bank": {"ancestors": []}})


def _decided(world, run_id):
    return [(event["type"], event["status"], event["reason"]) for event in world.history(run_id)
            if event.get("type") in {"hypothesis_probed", "hypothesis_reused"}]


def _admission(world, run_id):
    event, = world.events(run_id, "memory_admission")
    return event["decision"]


def test_a_check_of_another_account_never_commits_the_claim(tmp_path):
    world = _world(tmp_path)

    world.run(PROGRAM, "foreign", {"task_name": "foreign", "read_account": "B", "check_account": "B"})
    assert _decided(world, "foreign") == [("hypothesis_reused", None, "no_memory_snapshot"),
                                          ("hypothesis_probed", "provisional", "check_about_another:subject")]
    assert world.calls("ledger") == [{"account": "B"}]  # The ledger was asked, and answered about B.
    assert _admission(world, "foreign") != "admitted"
    assert world.world()["effects"] == []

    world.run(PROGRAM, "own", {"task_name": "own", "read_account": "A", "check_account": "A"})
    assert _decided(world, "own")[-1] == ("hypothesis_probed", "confirmed", "check_agrees")
    assert _admission(world, "own") == "admitted"
    assert [item["tool"] for item in world.world()["effects"]] == ["transfer"]

    # The court's record serves the confirmed claim; the claim checked about B is checked again.
    world.run(PROGRAM, "again", {"task_name": "again", "read_account": "A", "check_account": "A"})
    assert _decided(world, "again") == [("hypothesis_reused", "confirmed", "court_record")]
    world.run(PROGRAM, "foreign-again", {"task_name": "foreign-again", "read_account": "B", "check_account": "B"})
    assert _decided(world, "foreign-again")[-1] == ("hypothesis_probed", "provisional",
                                                    "check_about_another:subject")
    assert [item["tool"] for item in world.world()["effects"]] == ["transfer", "transfer"]
    probed = {item["hypothesis"]: item["status"] for report in world.reports()
              for item in report["hypotheses"]["probed"]}
    assert sorted(probed.values()) == ["confirmed", "provisional"]
