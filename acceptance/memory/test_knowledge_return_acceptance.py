"""A correction back to an answer memory held before is held again (review AUD-3).

The catalog states the basic plan's price for one start three times in three
sessions: 20, then 25, then 20 again — the same answer, the same recorded
evidence as the first. Each change is a correction of the one before:

* the third session corrects 25 back to 20: memory holds 20 again, and the
  version it held before keeps the period it was held then;
* asked now, search finds 20 and admission admits it — the independent billing
  service says 20;
* asked as memory knew it in each earlier window, the answer is the one it held
  then, with the period it held it in then: 20 after the first session, 25
  after the second;
* reading the catalog again unchanged is a copy.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

TEXT = "Basic plan costs {} euro per month"


def _learn(world, run_id, value):
    catalog.publish(world, "catalog", "basic", "monthly_price", value, text=TEXT.format(value), start="2026-01-01")
    return catalog.learn(world, run_id, "basic")


def test_a_correction_returns_to_an_answer_memory_held_before(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "billing", "basic", "monthly_price", 20)
    first, second = _learn(world, "learn-20", 20), _learn(world, "learn-25", 25)
    twenty, = first["knowledge"]["declared"]
    twenty_five, = second["knowledge"]["declared"]
    assert second["knowledge"]["corrections"] == [{"statement": twenty["statement"],
                                                   "corrected_by": twenty_five["statement"], "slot": twenty["slot"]}]

    back = _learn(world, "learn-20-again", 20)
    held, = back["knowledge"]["declared"]
    assert (held["statement"], held.get("held_again")) == (twenty["statement"], True)
    assert back["knowledge"]["corrections"] == [{"statement": twenty_five["statement"],
                                                 "corrected_by": twenty["statement"], "slot": twenty["slot"]}]

    now = catalog.ask(world, "now", "basic", "monthly_price", "basic monthly price", at="2026-03-01")
    assert [item["value"] for item in now["search"]["candidates"]] == [20]
    assert now["admission"]["decision"] == "admitted"
    periods = {first["window"]["index"]: (20, [first["window"]["index"], second["window"]["index"]]),
               second["window"]["index"]: (25, [second["window"]["index"], back["window"]["index"]])}
    for window, (value, period) in periods.items():
        then = catalog.ask(world, f"then-{window}", "basic", "monthly_price", "basic monthly price",
                           at="2026-03-01", known_as_of=window)
        held, = then["search"]["candidates"]
        # The period memory held it in then, never the one it holds it in now (review R3).
        assert (held["value"], [held["known_from"], held["known_until"]]) == (value, period), window
    assert [now["admission"]["attestation"]["version"][key] for key in ("known_from", "known_until")] == [
        back["window"]["index"], None]

    again = _learn(world, "learn-20-copy", 20)
    assert again["knowledge"]["declared"] == [] and len(again["knowledge"]["copies"]) == 1
