"""Independent observations overlap; the commit waits only for what it needs (refinement §16).

An offer needs a billing quote and the stock count, read by one ``parallel``
graph through the canonical launch; an audit trail is read too, slowly, and
the offer does not depend on it.

* The quote and the stock are in flight at the same time — with a limit of
  one they are not — and the terms are computed only after both arrived.
* The commit and the order happen while the audit is still in flight; the
  audit's answer is recorded once as cancelled and never used.
* Arriving in the other order, the answers give the same committed terms and
  one order each; the history records which arrived first.
* A graph that tries to act from a call node is refused before any effect:
  its calls may only observe, the order belongs to the commit.

Every run is re-executed by the court from its record (``replay_verified``).
"""
from __future__ import annotations

from acceptance.memory import _offers as offers


def _overlap(first: dict, second: dict) -> bool:
    return first["started_ms"] < second["finished_ms"] and second["started_ms"] < first["finished_ms"]


def _verified(world, run_id):
    return world.reports()[-1]["replay"][run_id]["status"] == "replay_verified"


def test_independent_observations_overlap_and_a_slow_unrelated_one_does_not_hold_the_commit(tmp_path):
    world = offers.world(tmp_path, {("billing_quote", "a"): 1.0, ("inventory", "a"): 1.0,
                                    ("audit_trail", "a"): 6.0, ("billing_quote", "b"): 1.0,
                                    ("inventory", "b"): 1.0})
    found = offers.offer(world, "offer-a", "a")
    quote, stock, terms = (offers.step(found, name) for name in ("quote", "stock", "terms"))
    assert quote["status"] == stock["status"] == "integrated" and _overlap(quote, stock)
    assert terms["reads"] == {"quote": 1, "stock": 1}
    assert terms["started_ms"] >= max(quote["finished_ms"], stock["finished_ms"])
    commit = found["commit"]
    assert commit["value"] == {"price": 20, "count": 5} and "audit" not in commit["reads"]

    audit = offers.step(found, "audit")
    assert audit["status"] == "cancelled" and commit["at_ms"] < audit["finished_ms"]
    # The order was placed before the audit answered, once; the audit was read once and accounted once.
    kinds = [(event["type"], (event.get("request") or {}).get("tool")) for event in world.history("offer-a")]
    assert kinds.index(("external_action", "place_order")) < kinds.index(("external_action", "audit_trail"))
    assert offers.orders(world) == [{"tool": "place_order", "effect": "order_placed",
                                     "args": {"plan": "a", "price": 20, "count": 5}}]
    assert world.calls("audit_trail") == [{"plan": "a"}]
    assert sum(1 for step in found["steps"] if step["node"] == "audit") == 1
    assert _verified(world, "offer-a")

    # One call at a time: the same graph, with the observations one after another.
    serial = offers.offer(world, "offer-b", "b", offers.limited(offers.OFFER, 1))
    quote, stock = offers.step(serial, "quote"), offers.step(serial, "stock")
    assert not _overlap(quote, stock) and serial["commit"]["value"] == {"price": 20, "count": 5}
    assert _verified(world, "offer-b")


def test_answers_in_another_order_give_the_same_commit_and_one_order_each(tmp_path):
    world = offers.world(tmp_path, {("billing_quote", "x"): 0.2, ("inventory", "x"): 2.0,
                                    ("billing_quote", "y"): 2.0, ("inventory", "y"): 0.2})
    runs = {plan: offers.offer(world, f"offer-{plan}", plan) for plan in ("x", "y")}
    arrived = {plan: [event["request"]["tool"] for event in found["actions"]
                      if event["request"]["tool"] in {"billing_quote", "inventory"}] for plan, found in runs.items()}
    assert arrived == {"x": ["billing_quote", "inventory"], "y": ["inventory", "billing_quote"]}
    assert runs["x"]["commit"]["value"] == runs["y"]["commit"]["value"] == {"price": 20, "count": 5}
    assert runs["x"]["commit"]["reads"] == runs["y"]["commit"]["reads"]
    assert sorted(item["args"]["plan"] for item in offers.orders(world)) == ["x", "y"]
    assert all(step["status"] == "integrated" for found in runs.values() for step in found["steps"])
    assert _verified(world, "offer-y")


def test_a_call_node_that_would_act_is_refused_before_any_effect(tmp_path):
    world = offers.world(tmp_path, {})
    found = offers.offer(world, "unsafe", "a", offers.UNSAFE)
    placed, = [event for event in found["actions"] if event["request"]["tool"] == "place_order"]
    view = placed["outcome"]["view"]
    assert view["ok"] is False and view["transport"] == "rejected"
    assert view["reason"] == "a parallel graph calls only observations; its effect belongs to its commit"
    assert found["commit"]["value"]["reason"] == view["reason"]
    assert world.calls("place_order") == [] and offers.orders(world) == []
