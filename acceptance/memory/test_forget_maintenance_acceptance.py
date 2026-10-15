"""An explicit consolidation applies a recorded forget without another business task (review AUD-4).

A session learns the basic plan's price from the catalog. The operator forgets
the case through ``synapse memory forget``: the act is recorded and the bodies
leave store D. No business session follows; a maintenance program only asks the
court to consolidate (``consolidate palace``).

* That consolidation applies the act: a new report carries the tombstone, the
  case is forgotten in memory state, the statement read from the forgotten
  results is withdrawn from search;
* a second maintenance run, with nothing new to apply, decides nothing again:
  no new report.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

MAINTENANCE = '''memory palace "catalog" { rooms { semantic } consolidate during dream }
consolidate palace
print("maintained")'''


def test_an_explicit_consolidation_applies_a_recorded_forget(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "monthly_price", 20, text="Basic monthly_price 20", start="2026-01-01")
    learned = catalog.learn(world, "learn", "basic")
    statement = learned["knowledge"]["declared"][0]["statement"]
    state = world.owner().state()
    source = state["knowledge"]["versions"][statement]["record"]["source"]["ref"]
    qid, = [qid for qid, quantum in state["quanta"].items() if source in quantum["evidence_refs"]]
    code, payload, stderr = world.memory("forget", "--quantum", qid, "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0 and payload["status"] == "RECORDED", (payload, stderr)
    tombstone = payload["acts"][0]["tombstone"]
    assert world.evidence().get(source) is None
    reports = len(world.reports())

    world.run(MAINTENANCE, "maintenance", {})
    assert len(world.reports()) == reports + 1
    state = world.owner().state()
    assert (state["quanta"][qid]["retention_state"], state["quanta"][qid]["tombstone"]) == ("forgotten", tombstone)
    assert state["retention"]["cursor"] == len(world.owner().retention_passes())
    assert state["knowledge"]["versions"][statement]["withdrawn"]["tombstone"] == tombstone
    asked = catalog.ask(world, "ask", "basic", "monthly_price", "basic monthly price", at="2026-03-01")
    assert asked["search"]["candidates"] == [] and asked["admission"]["decision"] != "admitted"

    reports = len(world.reports())
    world.run(MAINTENANCE, "maintenance-again", {})
    assert len(world.reports()) == reports
