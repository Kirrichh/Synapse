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
* A program that changes its copy of the candidate — declares the event a
  state — changes nothing: admission resolves the recorded statement again
  under the operator's currency rule, and the claim about 11:00 is refused
  (review DEEP-4).
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

AT_TEN, AT_ELEVEN = "2026-10-01T10:00:00Z", "2026-10-01T11:00:00Z"


#: The program's own copy of each candidate, the event declared a state.
FORGED = catalog.ASK.replace("  let decided = admit(found.candidates, wanted)", """  let forged = []
  for c in found.candidates {
    forged = forged + [{"id": c.id, "kind": c.kind, "entity": c.entity, "attribute": c.attribute, "value": c.value, "polarity": c.polarity, "conditions": c.conditions, "valid_from": c.valid_from, "valid_until": c.valid_until, "freshness": "state", "text": c.text, "source": c.source, "known_from": c.known_from, "known_until": c.known_until}]
  }
  let decided = admit(forged, wanted)""")


def _ask(world, run_id, at, program=catalog.ASK, **options):
    world.run(program, run_id, {"task_name": run_id, "plan_id": "basic", "subject": "basic", "prop": "outage",
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


def test_a_candidate_copy_cannot_move_an_event_in_time(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "outage", "eu", text="Basic outage eu", start=AT_TEN)
    catalog.publish(world, "billing", "basic", "outage", "eu")
    catalog.learn(world, "learn-event", "basic")
    _, decided = _ask(world, "forged-at-eleven", AT_ELEVEN, FORGED)
    checked, = decided["checked"]
    assert decided["decision"] != "admitted" and "outside_validity" in checked["reasons"]
    _, decided = _ask(world, "forged-at-ten", AT_TEN, FORGED)
    assert decided["decision"] == "admitted"  # At its moment the recorded event holds, as recorded.
    assert decided["attestation"]["version"]["valid_from"] == AT_TEN
