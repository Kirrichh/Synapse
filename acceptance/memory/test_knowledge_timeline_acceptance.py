"""What was true then, and what memory knew when (refinement §15).

The catalog states the basic plan's price three times: 20 from January, 25
from June, and — received last — 18 from the previous October. The late
report takes its place on the timeline by its start: it answers for December
and never overrides June's price. Asked as memory knew it before June's price
arrived, the answer for July is January's price.

A promotional price is bounded to 30 days: inside them it is current, after
them its currency is unknown — admission refuses it without a time, and past
its days, but it is not a falsehood. An outage is an event: it holds at its
moment only.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

PRICE = "Basic plan costs {} euro per month"


def _price(world, run_id, value, start):
    catalog.publish(world, "catalog", "basic", "monthly_price", value, text=PRICE.format(value), start=start)
    catalog.publish(world, "billing", "basic", "monthly_price", value)
    return catalog.learn(world, run_id, "basic")


def _values(asked):
    return sorted((item["value"], item["valid_from"], item["valid_until"]) for item in asked["search"]["candidates"]
                  if item["attribute"] == "monthly_price")


def test_a_late_report_about_the_past_takes_its_place_on_the_timeline(tmp_path):
    world = catalog.world(tmp_path)
    reports = [_price(world, "price-jan", 20, "2026-01-01"), _price(world, "price-jun", 25, "2026-06-01"),
               _price(world, "price-oct", 18, "2025-10-01")]
    first = reports[0]["window"]["index"]
    # Three versions of one slot; none corrects another (each has its own start).
    assert len({report["knowledge"]["declared"][0]["slot"] for report in reports}) == 1
    assert all(report["knowledge"]["corrections"] == [] for report in reports)

    query = "basic monthly price"
    july = catalog.ask(world, "july", "basic", "monthly_price", query, at="2026-07-01")
    assert _values(july) == [(25, "2026-06-01T00:00:00Z", None)]
    march = catalog.ask(world, "march", "basic", "monthly_price", query, at="2026-03-01")
    assert _values(march) == [(20, "2026-01-01T00:00:00Z", "2026-06-01T00:00:00Z")]
    december = catalog.ask(world, "december", "basic", "monthly_price", query, at="2025-12-01")
    assert _values(december) == [(18, "2025-10-01T00:00:00Z", "2026-01-01T00:00:00Z")]
    # As memory knew it before June's price arrived: January's price still held in July.
    then = catalog.ask(world, "july-then", "basic", "monthly_price", query, at="2026-07-01", known_as_of=first)
    assert _values(then) == [(20, "2026-01-01T00:00:00Z", None)]


def test_a_bounded_price_goes_to_unknown_currency_and_an_event_holds_at_its_moment(tmp_path):
    world = catalog.world(tmp_path)
    catalog.publish(world, "catalog", "basic", "promo_price", 15, text="Basic plan promo price 15 euro",
                    start="2026-03-01")
    catalog.publish(world, "billing", "basic", "promo_price", 15)
    catalog.learn(world, "promo", "basic")
    catalog.publish(world, "catalog", "status", "outage", "eu-west", text="Outage of the hosting in eu-west",
                    start="2026-02-10T08:00:00Z")
    catalog.learn(world, "outage", "status")

    inside = catalog.ask(world, "promo-inside", "basic", "promo_price", "basic promo price", at="2026-03-15")
    promo, = inside["search"]["candidates"]
    assert promo["freshness"] == "current" and promo["valid_until"] == "2026-03-31T00:00:00Z"
    assert inside["admission"]["decision"] == "admitted"
    after = catalog.ask(world, "promo-after", "basic", "promo_price", "basic promo price", at="2026-05-01")
    promo, = after["search"]["candidates"]
    assert promo["freshness"] == "unknown" and promo["value"] == 15  # Unknown, not false.
    assert "freshness_unknown" in after["admission"]["checked"][0]["reasons"]
    timeless = catalog.ask(world, "promo-timeless", "basic", "promo_price", "basic promo price")
    assert timeless["search"]["candidates"][0]["freshness"] == "unknown"

    at_moment = catalog.ask(world, "outage-at", "status", "outage", "hosting outage", at="2026-02-10T08:00:00Z")
    assert [item["value"] for item in at_moment["search"]["candidates"]] == ["eu-west"]
    next_day = catalog.ask(world, "outage-next", "status", "outage", "hosting outage", at="2026-02-11")
    assert next_day["search"]["candidates"] == []
