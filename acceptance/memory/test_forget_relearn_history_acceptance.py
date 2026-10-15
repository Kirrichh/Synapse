"""Learning again after a forget never fills the windows the forget emptied (review R2).

A session learns the basic plan's price (20). The operator forgets the case it
was read in; the next session applies the forget and the statement leaves
search. Asked as memory knew it in that window, there is nothing to admit.
Later the catalog is read again: a new observation, held from its own window.

* Asked now, the price is found and admitted (billing says 20).
* Asked again as memory knew it in the window of the forget, there is still
  nothing: the new reading does not reach back into the period the forget ended.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog


def _ask(world, run_id, window=None):
    found = catalog.ask(world, run_id, "basic", "monthly_price", "basic monthly price", at="2026-03-01",
                        **({} if window is None else {"known_as_of": window}))
    return [item["value"] for item in found["search"]["candidates"] if item["entity"] == "basic"], \
        found["admission"]["decision"]


def _learn(world, run_id, plan, value):
    catalog.publish(world, "catalog", plan, "monthly_price", value, text=f"{plan} monthly price {value}",
                    start="2026-01-01")
    return catalog.learn(world, run_id, plan)


def test_a_reading_after_a_forget_holds_only_from_its_own_window(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "billing", "basic", "monthly_price", 20)
    statement = _learn(world, "learn", "basic", 20)["knowledge"]["declared"][0]["statement"]
    state = world.owner().state()
    source = state["knowledge"]["versions"][statement]["record"]["source"]["ref"]
    qid, = [qid for qid, quantum in state["quanta"].items() if source in quantum["evidence_refs"]]
    code, payload, stderr = world.memory("forget", "--quantum", qid, "--reason", "data subject request",
                                         "--operator", "acceptance.operator")
    assert code == 0, (payload, stderr)
    forgotten = _learn(world, "learn-other", "pro", 40)["window"]["index"]  # This consolidation applies it.
    assert world.owner().state()["knowledge"]["versions"][statement]["withdrawn"] is not None
    assert _ask(world, "before", forgotten) == ([], "abstained")

    _learn(world, "relearn", "basic", 20)
    assert _ask(world, "now") == ([20], "admitted")
    assert _ask(world, "after", forgotten) == ([], "abstained")
