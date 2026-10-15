"""Corrections, repetitions and a poisoned mirror (refinement §15).

* The catalog corrects the basic plan's price for the same start (20 → 22).
  The court records a correction: the earlier version leaves the present but
  stays in history — asked as memory knew it before, the answer is still 20 —
  and the hypothesis read from the corrected answer returns to provisional
  with the reason.
* Reading the catalog again, unchanged, is a copy: memory's knowledge and
  confidence do not grow from a repetition.
* A web mirror of the catalog, inserted last, worded exactly like the catalog
  and stating 99 is no priority: the court records a conflict (both stay
  current), and admission admits the catalog's price, whose content the
  independent billing service confirms — never the mirror's, however similar,
  confident or recent.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

TEXT = "Basic plan costs {} euro per month"


def _catalog_price(world, value):
    catalog.publish(world, "catalog", "basic", "monthly_price", value, text=TEXT.format(value), start="2026-01-01")
    catalog.publish(world, "billing", "basic", "monthly_price", value)


def test_a_correction_revises_what_depended_on_it_and_keeps_history(tmp_path):
    world = catalog.world(tmp_path)
    _catalog_price(world, 20)
    learned = catalog.learn(world, "learn-20", "basic", check=True)
    old, = learned["knowledge"]["declared"]
    hypothesis, = learned["hypotheses"]["probed"]
    assert hypothesis["status"] == "confirmed"
    before = learned["window"]["index"]

    _catalog_price(world, 22)
    corrected = catalog.learn(world, "learn-22", "basic")
    new, = corrected["knowledge"]["declared"]
    assert corrected["knowledge"]["corrections"] == [{"statement": old["statement"], "corrected_by": new["statement"],
                                                      "slot": old["slot"]}]
    revision, = corrected["knowledge"]["revisions"]
    assert revision == {"statement": old["statement"], "hypotheses": [hypothesis["hypothesis"]], "habits": []}

    now = catalog.ask(world, "now", "basic", "monthly_price", "basic monthly price", at="2026-03-01")
    assert [item["value"] for item in now["search"]["candidates"]] == [22]
    assert now["admission"]["decision"] == "admitted"
    then = catalog.ask(world, "then", "basic", "monthly_price", "basic monthly price", at="2026-03-01",
                       known_as_of=before)
    kept, = then["search"]["candidates"]
    assert (kept["value"], kept["id"], kept["known_until"]) == (20, old["statement"], corrected["window"]["index"])

    # Reading the unchanged catalog again adds nothing.
    again = catalog.learn(world, "learn-22-again", "basic")
    assert again["knowledge"]["declared"] == [] and len(again["knowledge"]["copies"]) == 1


def test_a_poisoned_mirror_gets_no_priority(tmp_path):
    world = catalog.world(tmp_path)
    _catalog_price(world, 20)
    trusted = catalog.learn(world, "learn-catalog", "basic")["knowledge"]["declared"][0]["statement"]
    catalog.publish(world, "mirror", "basic", "monthly_price", 99, text=TEXT.format(20), start="2026-01-01",
                    confidence=0.99)
    mirrored = catalog.learn(world, "learn-mirror", "basic", source="mirror_entry")
    poisoned, = mirrored["knowledge"]["declared"]
    assert mirrored["knowledge"]["conflicts"] == [{"statement": poisoned["statement"], "slot": poisoned["slot"],
                                                   "with": [trusted]}]

    asked = catalog.ask(world, "ask", "basic", "monthly_price", TEXT.format(20), at="2026-03-01")
    values = {item["id"]: item["value"] for item in asked["search"]["candidates"]}
    assert values == {trusted: 20, poisoned["statement"]: 99}  # Both current; neither wins by recency.
    decision = asked["admission"]
    assert decision["decision"] == "admitted" and decision["fact"] == trusted
    reasons, = [item["reasons"] for item in decision["checked"] if item["id"] == poisoned["statement"]]
    # The confirmed hypothesis is about the catalog's statement: another value from another source version.
    assert {"basis_states_another_value", "basis_for_another_version"} <= set(reasons)
