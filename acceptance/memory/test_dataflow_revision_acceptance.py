"""A new observation revises a decision not yet committed (refinement §16).

A graph reads a plan's price and asks eligibility for it; a price watch reports
that the plan was repriced, which raises an event that makes the graph read the
price again.

* The eligibility asked for the old price is superseded the moment the new
  price arrives: its answer is recorded as stale and never used, a new check
  runs at once on the new price, the terms and the order use only the new
  price, and the event is raised once.
* When the old check had already answered and the new one also waits for a
  tax rate refreshed by the same event, the terms computed from the old check
  are not recombined with the new price: they wait until the check is settled
  again, and every computation read mutually consistent versions.
* The history explains the commit — the versions it rested on and why every
  earlier computation was not used — and retention reproduces the session from
  the recorded answers alone, stale and superseded computations included.
"""
from __future__ import annotations

from acceptance.memory import _offers as offers

RETENTION = {"n_medium_windows": 1, "n_low_windows": 1, "k_rollup": 1}


def _consistent(found: dict) -> None:
    """Every computation of the terms read an eligibility answer given for the price version it read."""
    checks = {step["version"]: step["reads"]["price"] for step in found["steps"]
              if step["node"] == "approval" and step["status"] == "integrated"}
    for step in found["steps"]:
        if step["node"] == "terms":
            assert checks[step["reads"]["approval"]] == step["reads"]["price"]


def test_a_superseded_proposal_is_stale_revised_and_never_applied(tmp_path):
    world = offers.world(tmp_path, {("price_watch", "a"): 0.5, ("eligibility", "a"): 3.0}, prices=(20, 22),
                         watch_changed=True, parameters=RETENTION)
    found = offers.offer(world, "repriced", "a", offers.REPRICED)
    first, second = offers.step(found, "approval", 1), offers.step(found, "approval", 2)
    new_price = offers.step(found, "price", 2)
    assert (first["status"], first["reads"]) == ("stale", {"price": 1})
    assert (second["status"], second["reads"]) == ("integrated", {"price": 2})
    # Superseded at once: the new check started when the new price arrived, before the old one answered.
    assert new_price["finished_ms"] <= second["started_ms"] < first["finished_ms"]
    assert [(item["event"], item["count"], item["by"]) for item in found["signals"]] == [
        ("repriced", 1, {"watch": 1})]
    terms, = [step for step in found["steps"] if step["node"] == "terms"]
    assert terms["reads"] == {"price": 2, "approval": 1}
    commit = found["commit"]
    assert commit["value"] == {"price": 22, "eligible": True}
    assert commit["reads"] == {"price": 2, "watch": 1, "approval": 1, "terms": 1}
    assert world.calls("eligibility") == [{"plan": "a", "price": 20}, {"plan": "a", "price": 22}]
    assert offers.orders(world) == [{"tool": "place_order", "effect": "order_placed",
                                     "args": {"plan": "a", "price": 22, "count": 1}}]
    assert world.reports()[-1]["replay"]["repriced"]["status"] == "replay_verified"

    # Retention reproduces the session from its recorded answers only, stale computations included.
    calls = world.world()["calls"]
    for index in range(2):
        offers.offer(world, f"later-{index}", "b", offers.OFFER)
    acts = [act for item in world.owner().retention_passes() for act in item["acts"]]
    reproduced = [act for act in acts if act["act"] == "raw_deleted" and act["qid"] in {
        entry["qid"] for report in world.reports() for entry in report["apply"]["quanta"].values()
        if entry["replay_ref"] and entry["replay_ref"]["run_id"] == "repriced"}]
    assert reproduced and all(act["replay"] == "replay_verified" for act in reproduced)
    assert world.world()["calls"][:len(calls)] == calls


def test_terms_already_computed_wait_for_the_revised_check_instead_of_mixing_versions(tmp_path):
    world = offers.world(tmp_path, {("price_watch", "a"): 3.0, ("eligibility", "a"): 0.2, ("tax_rate", "a"): 1.5},
                         prices=(20, 22), watch_changed=True)
    found = offers.offer(world, "revalued", "a", offers.REVALUED)
    # The new price arrived long before the new tax rate; the terms never combined it with the old check.
    new_price, new_rate = offers.step(found, "price", 2), offers.step(found, "rate", 2)
    assert new_price["finished_ms"] < new_rate["finished_ms"]
    terms = [step for step in found["steps"] if step["node"] == "terms"]
    assert [step["reads"] for step in terms] == [{"price": 1, "approval": 1}, {"price": 2, "approval": 2}]
    assert terms[1]["started_ms"] >= offers.step(found, "approval", 2)["finished_ms"]
    _consistent(found)
    # The first terms were ready early, but the commit waited for the watch whose event could move them.
    assert found["commit"]["value"] == {"price": 22, "eligible": True} and found["commit"]["reads"]["terms"] == 2
    assert all(step["status"] == "integrated" for step in found["steps"])  # Nothing was in flight when it moved.
    assert world.calls("eligibility") == [{"plan": "a", "price": 20, "rate": 0.2},
                                          {"plan": "a", "price": 22, "rate": 0.2}]
    assert offers.orders(world) == [{"tool": "place_order", "effect": "order_placed",
                                     "args": {"plan": "a", "price": 22, "count": 1}}]
    assert world.reports()[-1]["replay"]["revalued"]["status"] == "replay_verified"
