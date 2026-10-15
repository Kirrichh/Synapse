"""The offer desk of the event-driven execution acceptance files (refinement §16).

An offer for a plan needs a billing quote and the stock count, read
concurrently by a ``parallel`` graph; an audit trail is read too, but the offer
does not depend on it and it is slow. The order is placed once, at the commit,
with the committed price. A price watch may report that the plan was repriced:
the graph then reads the price feed again, and an eligibility check that was
still working on the old price becomes stale and runs again on the new one;
when the check also needs a tax rate refreshed by the same event, terms
computed from the old check wait for the new one instead of mixing versions.

Every read is an observation by contract (idempotent, no effect); placing the
order is the one action. How long each service takes and what it answers is
scripted per plan, so one project serves every arrangement of a test, and the
tool server's own world is the checker's truth about what was called and what
took effect.
"""
from __future__ import annotations

from acceptance.memory._world import MemoryWorld, answer, tool

OBSERVATION = {"observation": True, "idempotent": True}

OFFER = '''
memory palace "offers" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "place_order", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "offer", "intent": "offer a plan at its current price", "element_part": "offers", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "place one order at the committed price", "segments": [seg]})
context "offer" {
  parallel offer limit 4 {
    node quote = tool("billing_quote", {"plan": plan_id})
    node stock = tool("inventory", {"plan": plan_id})
    node audit = tool("audit_trail", {"plan": plan_id})
    node terms = {"price": quote.payload.price, "count": stock.payload.count}
    commit terms => tool("place_order", {"plan": plan_id, "price": terms.price, "count": terms.count})
  }
}
print("offered")
'''

REPRICED = '''
memory palace "offers" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "place_order", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "offer", "intent": "offer a plan at its current price", "element_part": "offers", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "place one order at the committed price", "segments": [seg]})
context "offer" {
  parallel offer limit 4 {
    node price on "repriced" = tool("price_feed", {"plan": plan_id})
    node watch = tool("price_watch", {"plan": plan_id})
    node approval = tool("eligibility", {"plan": plan_id, "price": price.payload.price})
    node terms = {"price": price.payload.price, "eligible": approval.payload.eligible}
    signal "repriced" when watch.payload.changed == true
    commit terms => tool("place_order", {"plan": plan_id, "price": terms.price, "count": 1})
  }
}
print("offered")
'''

REVALUED = '''
memory palace "offers" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "place_order", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "offer", "intent": "offer a plan at its current price", "element_part": "offers", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "place one order at the committed price", "segments": [seg]})
context "offer" {
  parallel offer limit 4 {
    node price on "repriced" = tool("price_feed", {"plan": plan_id})
    node rate on "repriced" = tool("tax_rate", {"plan": plan_id})
    node watch = tool("price_watch", {"plan": plan_id})
    node approval = tool("eligibility", {"plan": plan_id, "price": price.payload.price, "rate": rate.payload.rate})
    node terms = {"price": price.payload.price, "eligible": approval.payload.eligible}
    signal "repriced" when watch.payload.changed == true
    commit terms => tool("place_order", {"plan": plan_id, "price": terms.price, "count": 1})
  }
}
print("offered")
'''

UNSAFE = '''
let req = {"kind": "execute", "tool": "place_order", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "offer", "intent": "offer a plan at its current price", "element_part": "offers", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "place one order at the committed price", "segments": [seg]})
context "offer" {
  parallel offer {
    node quote = tool("billing_quote", {"plan": plan_id})
    node placed = tool("place_order", {"plan": plan_id, "price": quote.payload.price, "count": 1})
    commit placed
  }
}
print("offered")
'''


def limited(source: str, limit: int) -> str:
    """The same graph with another bound on its concurrent calls."""
    return source.replace("parallel offer limit 4 {", f"parallel offer limit {limit} {{")


def tools(delays: dict, *, prices=(20,), watch_changed: bool = False) -> list:
    """The desk's services; ``delays`` maps ``(tool, plan)`` to the seconds that service takes for that plan."""

    def rules(name, payload, *, sequence=()):
        timed = [answer(payload, when={"plan": plan}, delay=seconds) for (tool_name, plan), seconds in
                 sorted(delays.items()) if tool_name == name]
        return [*timed, answer(payload, sequence=sequence)]

    feed = [{"payload": {"ok": True, "price": value}} for value in prices[:-1]]
    return [
        tool("billing_quote", "billing:ops", rules("billing_quote", {"ok": True, "price": 20}), server="desk",
             contract=OBSERVATION),
        tool("inventory", "inventory:ops", rules("inventory", {"ok": True, "count": 5}), server="desk", contract=OBSERVATION),
        tool("audit_trail", "audit:ops", rules("audit_trail", {"ok": True, "entries": 3}), server="desk", contract=OBSERVATION),
        tool("price_feed", "prices:ops", [answer({"ok": True, "price": prices[-1]}, sequence=feed)], server="desk",
             contract=OBSERVATION),
        tool("price_watch", "prices:watch", rules("price_watch", {"ok": True, "changed": watch_changed}), server="desk",
             contract=OBSERVATION),
        tool("eligibility", "eligibility:ops", rules("eligibility", {"ok": True, "eligible": True}), server="desk",
             contract=OBSERVATION),
        tool("tax_rate", "tax:ops", rules("tax_rate", {"ok": True, "rate": 0.2}), server="desk", contract=OBSERVATION),
        tool("place_order", "orders:ops", [answer({"ok": True, "placed": True}, effect="order_placed")], server="orders"),
    ]


def provenance() -> dict:
    return {source: {"ancestors": []} for source in (
        "billing:ops", "inventory:ops", "audit:ops", "prices:ops", "prices:watch", "eligibility:ops", "tax:ops",
        "orders:ops")}


def world(root, delays: dict, *, parameters=None, **options) -> MemoryWorld:
    return MemoryWorld(root, tools(delays, **options), provenance=provenance(), parameters=parameters)


def offer(world: MemoryWorld, run_id: str, plan: str, source: str = OFFER) -> dict:
    """One session running the graph; returns its bound result and the recorded graph events."""
    world.run(source, run_id, {"task_name": run_id, "plan_id": plan})
    return result(world, run_id)


def result(world: MemoryWorld, run_id: str) -> dict:
    history = world.history(run_id)
    started, = [event for event in history if event.get("type") == "dataflow_started"]
    committed, = [event for event in history if event.get("type") == "dataflow_commit"]
    return {"started": started, "commit": committed,
            "steps": [event for event in history if event.get("type") == "dataflow_step"],
            "signals": [event for event in history if event.get("type") == "dataflow_signal"],
            "actions": [event for event in history if event.get("type") == "external_action"]}


def step(found: dict, node: str, attempt: int = 1) -> dict:
    chosen, = [item for item in found["steps"] if item["node"] == node and item["attempt"] == attempt]
    return chosen


def orders(world: MemoryWorld) -> list:
    return [item for item in world.world()["effects"] if item["effect"] == "order_placed"]
