"""An event is admitted at its moment only, however it was found (review AUD-2).

The catalog reports an outage of the basic plan in the EU at 10:00 — a property
declared as an event — and the independent billing service confirms the
content. A session learns it. Asking without a valid time, search lists the
outage as history: a candidate with no end.

* A claim about 10:00 is admitted.
* A claim about 11:00 is refused (``outside_validity``) although search listed
  the candidate: an event says nothing about any other moment, and no
  confirmation of its content makes it true then.
* Asked with the valid time 11:00, search finds nothing to admit either.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

AT_TEN, AT_ELEVEN = "2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z"


def _ask(world, run_id, at, **options):
    world.run(catalog.ASK, run_id, {"task_name": run_id, "plan_id": "basic", "subject": "basic", "prop": "outage",
                                    "query": "basic outage", "at": at, "polarity": True, "options": options})
    searched, = world.events(run_id, "knowledge_searched")
    decided, = world.events(run_id, "memory_admission")
    return searched, decided


def test_an_event_listed_as_history_is_admitted_only_at_its_moment(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "outage", "eu", text="Basic outage eu", start=AT_TEN)
    catalog.publish(world, "billing", "basic", "outage", "eu")
    catalog.learn(world, "learn-event", "basic")

    searched, decided = _ask(world, "at-ten", AT_TEN)
    listed, = searched["candidates"]
    assert (listed["freshness"], listed["valid_until"]) == ("event", None)
    assert decided["decision"] == "admitted"

    searched, decided = _ask(world, "at-eleven", AT_ELEVEN)
    assert [item["id"] for item in searched["candidates"]] == [listed["id"]]  # Listed as history.
    assert decided["decision"] != "admitted"
    checked, = [item for item in decided["checked"] if item["id"] == listed["id"]]
    assert "outside_validity" in checked["reasons"]

    searched, decided = _ask(world, "at-eleven-placed", AT_ELEVEN, valid_at=AT_ELEVEN)
    assert searched["candidates"] == [] and decided["decision"] != "admitted"
