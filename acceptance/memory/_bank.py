"""The bank clerk scenario of the hypothesis-check acceptance files (review F1).

A clerk states that the balance of account A is 20 and relies on it for a
consequential transfer. The core banking system is the claim's source; the
ledger, an independent service, is the declared check, and the operator's
contract of the ledger binds a check to its claim: the account it is asked
about and the account it answers about name the claim's subject; it answers for
the bank only. Both services know accounts A and B, each with balance 20. The
inputs say which account the program reads and which one it asks the ledger
about.
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


def world(root) -> MemoryWorld:
    accounts = [answer({"ok": True, "account": name, "balance": 20}, when={"account": name}) for name in ("A", "B")]
    tools = [
        tool("core", "core:bank", accounts, server="core"),
        tool("ledger", "ledger:bank", accounts, server="ledger", contract={"verifies": {
            "subject": {"request": "account", "answer": "account"}, "scope": {"value": "bank"}}}),
        tool("transfer", "payments:bank", [answer({"ok": True, "paid": True}, effect="paid")], server="payments",
             contract={"requires_established": True})]
    return MemoryWorld(root, tools, provenance={"core:bank": {"ancestors": []}, "ledger:bank": {"ancestors": []},
                                                "payments:bank": {"ancestors": []}})



def inputs(task: str, read: str, check: str) -> dict:
    return {"task_name": task, "read_account": read, "check_account": check}


def decided(world: MemoryWorld, run_id: str) -> list[tuple]:
    """How the run's hypothesis was decided: each reuse and probe, with status and reason."""
    return [(event["type"], event["status"], event["reason"]) for event in world.history(run_id)
            if event.get("type") in {"hypothesis_probed", "hypothesis_reused"}]


def admission(world: MemoryWorld, run_id: str) -> str:
    event, = world.events(run_id, "memory_admission")
    return event["decision"]


def transfers(world: MemoryWorld) -> int:
    return sum(1 for item in world.world()["effects"] if item["tool"] == "transfer")
