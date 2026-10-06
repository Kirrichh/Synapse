"""Forgetting the answer a statement was read from takes it out of the index (review §8.2).

A learning session reads the basic plan's price from the catalog, states it
and confirms it by the independent billing service. The operator forgets the
case that holds those recorded answers. Three separate things follow:

* the data leaves store D (the forget itself);
* the next consolidation withdraws the version read from the forgotten answer
  from the search index — it stays in the court's timeline as history, but no
  session is offered it as a candidate — and revokes the confirmation read
  from it (provisional, which is no refutation);
* reading the catalog again is a new observation after the forget, not a copy
  of the withdrawn version: the statement is searchable again.

Another plan, learned from another case, is untouched throughout.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

TEXT = "Basic plan costs 20 euro per month"


def test_a_forgotten_answer_leaves_the_index_and_a_new_reading_returns(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "monthly_price", 20, text=TEXT, start="2026-01-01")
    catalog.publish(world, "billing", "basic", "monthly_price", 20)
    catalog.publish(world, "catalog", "pro", "monthly_price", 40, text="Pro plan costs 40 euro per month",
                    start="2026-01-01")
    learned = catalog.learn(world, "learn-basic", "basic", check=True)
    statement = learned["knowledge"]["declared"][0]["statement"]
    hypothesis = learned["hypotheses"]["probed"][0]["hypothesis"]
    other = catalog.learn(world, "learn-pro", "pro")["knowledge"]["declared"][0]["statement"]
    state = world.owner().state()
    source = state["knowledge"]["versions"][statement]["record"]["source"]["ref"]
    case, = [qid for qid, entry in state["quanta"].items() if source in entry["evidence_refs"]]

    code, payload, stderr = world.memory("forget", "--quantum", case, "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    tombstone = payload["acts"][0]["tombstone"]
    assert world.evidence().get(source) is None  # The data is gone.

    # The next consolidation withdraws the version and revokes its confirmation; the other plan stays.
    report = catalog.learn(world, "learn-pro-again", "pro")
    entry, = report["dependencies"]
    assert entry["tombstone"] == tombstone and entry["index_withdrawn"] == [statement]
    assert entry["authority_revoked"]["hypotheses"] == [hypothesis]
    state = world.owner().state()
    version = state["knowledge"]["versions"][statement]
    assert version["withdrawn"]["tombstone"] == tombstone and version["record"]["value"] == 20  # History stays.
    assert state["hypotheses"][hypothesis]["status"] == "provisional"
    assert state["knowledge"]["versions"][other].get("withdrawn") is None

    asked = catalog.ask(world, "ask-forgotten", "basic", "monthly_price", "basic monthly price", at="2026-03-01")
    assert statement not in [item["id"] for item in asked["search"]["candidates"]]
    assert asked["admission"]["decision"] == "abstained"

    # A new reading of the catalog is a new observation: searchable again, and admitted on a fresh check.
    renewed = catalog.learn(world, "learn-basic-again", "basic", check=True)
    assert [item["statement"] for item in renewed["knowledge"]["declared"]] == [statement]
    assert world.owner().state()["knowledge"]["versions"][statement].get("withdrawn") is None
    asked = catalog.ask(world, "ask-renewed", "basic", "monthly_price", "basic monthly price", at="2026-03-01")
    assert statement in [item["id"] for item in asked["search"]["candidates"]]
    assert asked["admission"]["decision"] == "admitted" and asked["admission"]["fact"] == statement
