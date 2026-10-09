"""A graph's late answers are no source, and its result is the program's own (review DEEP-1, DEEP-2).

A plan's price is read by a ``parallel`` graph. The first read is slow and
answers 20; meanwhile the price watch reports a repricing, the price is read
again and answers 22 at once — the graph integrates 22 and commits it. The
slow first answer arrives after the commit: it is recorded as stale and used
for nothing.

* The program then states what it learned from the price feed: the statement
  is read from the answer the graph used (22), never from the stale one that
  arrived last.
* The program then changes its own copy of the result: the graph's recorded
  commit and the recorded answers keep what was observed and committed.
"""
from __future__ import annotations

from acceptance.memory import _offers as offers
from acceptance.memory._world import MemoryWorld, answer, tool

PROGRAM = '''
memory palace "offers" {
  rooms { semantic }
  consolidate during dream
}
let req = {"kind": "execute", "tool": "price_feed", "admissible_err": [], "allowed_alternatives": []}
let seg = {"segment": "price", "intent": "learn a plan's current price", "element_part": "offers", "requirement": req}
let plan = task_plan({"task_id": task_name, "goal": "learn the current price", "segments": [seg]})
context "price" {
  parallel offer limit 4 {
    node price on "repriced" = tool("price_feed", {"plan": plan_id})
    node watch = tool("price_watch", {"plan": plan_id})
    signal "repriced" when watch.payload.changed == true
    commit price
  }
  let stated = know({"subject": plan_id, "property": "price", "value": offer.value.payload.price, "polarity": true, "valid": {"from": "2026-01-01"}, "text": "price of the plan", "source": {"tool": "price_feed", "args": {"plan": plan_id}}})
  offer.value.payload.price = 99
}
print("learned")
'''


def _world(root) -> MemoryWorld:
    tools = [tool("price_feed", "prices:ops", [answer({"ok": True, "price": 22},
                                                      sequence=[{"payload": {"ok": True, "price": 20}, "delay": 4}])],
                  server="feed", contract=offers.OBSERVATION),
             tool("price_watch", "prices:watch", [answer({"ok": True, "changed": True})], server="watch",
                  contract=offers.OBSERVATION)]
    return MemoryWorld(root, tools, provenance={"prices:ops": {"ancestors": []}, "prices:watch": {"ancestors": []}},
                       knowledge={"embedder": None, "properties": {"price": {"freshness": "state"}}, "budget": 3})


def test_a_late_stale_answer_is_no_source_and_the_result_is_the_programs_own(tmp_path):
    world = _world(tmp_path)
    world.run(PROGRAM, "learn", {"task_name": "learn", "plan_id": "basic"})
    found = offers.result(world, "learn")
    reads = {item["request"]["ordinal"]: item for item in found["actions"] if item["request"]["tool"] == "price_feed"}
    statuses = {offers.step(found, "price", attempt)["status"] for attempt in (1, 2)}
    assert statuses == {"stale", "integrated"}
    integrated = next(item for item in found["steps"] if item["node"] == "price" and item["status"] == "integrated")
    used = next(item for ordinal, item in reads.items() if ordinal.endswith(f":price:{integrated['attempt']}"))
    late = next(item for ordinal, item in reads.items() if ordinal != used["request"]["ordinal"])
    assert (used["outcome"]["view"]["payload"]["price"], late["outcome"]["view"]["payload"]["price"]) == (22, 20)
    history = world.history("learn")
    assert history.index(late) > history.index(found["commit"])  # The stale answer arrived after the commit.

    declared, = world.events("learn", "knowledge_declared")
    assert declared["statement"]["value"] == 22
    assert declared["statement"]["source"]["ref"] == used["outcome"]["ref"]["evidence"]

    # The program changed its copy; the record keeps what was committed and observed.
    assert found["commit"]["value"]["payload"]["price"] == 22
    assert used["outcome"]["view"]["payload"]["price"] == 22
