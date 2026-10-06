"""Held-out cards, split by group (plan §17).

Five plan cards, each with its catalog statement, a copy republished by a web
mirror and a paraphrased question, form five groups. ``_split.py`` assigns
whole groups to training or to the exam: a card, its copy and its paraphrase
are never on two sides.

The memory learns the training cards (and their copies). In training, a
paraphrased question about a learned card is admitted on its confirmed
statement. In the exam, every question is about a held-out card: search
offers the learned cards of other plans, worded alike — and admission refuses
every one as another entity. The memory abstains; it never answers a
held-out card from a similar learned one.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog
from acceptance.memory import _split as split

PLANS = {"basic": 20, "pro": 40, "team": 60, "family": 30, "studio": 80}
SALT = "held-out-cards-v1"


def _items():
    for plan, price in PLANS.items():
        text = f"{plan.capitalize()} plan costs {price} euro per month"
        yield {"group": plan, "kind": "card", "plan": plan, "price": price, "text": text}
        yield {"group": plan, "kind": "copy", "plan": plan, "price": price, "text": text}
        yield {"group": plan, "kind": "paraphrase", "plan": plan, "query": f"monthly cost of the {plan} tariff"}


def test_held_out_cards_are_never_answered_from_similar_learned_ones(tmp_path):
    sides = split.split(_items(), lambda item: item["group"], salt=SALT, exam_share=0.4)
    groups = {name: {item["group"] for item in items} for name, items in sides.items()}
    assert groups["train"] and groups["exam"] and not groups["train"] & groups["exam"]
    for name, items in sides.items():  # Whole groups only: card, copy and paraphrase together.
        assert all({"card", "copy", "paraphrase"} == {item["kind"] for item in items if item["group"] == group}
                   for group in groups[name])

    world = catalog.world(tmp_path, budget=5)
    for plan, price in PLANS.items():  # The environment knows every card; memory learns only training ones.
        text = f"{plan.capitalize()} plan costs {price} euro per month"
        catalog.publish(world, "catalog", plan, "monthly_price", price, text=text, start="2026-01-01")
        catalog.publish(world, "mirror", plan, "monthly_price", price, text=text, start="2026-01-01")
        catalog.publish(world, "billing", plan, "monthly_price", price)
    for item in sides["train"]:
        if item["kind"] == "card":
            catalog.learn(world, f"learn-{item['plan']}", item["plan"], check=True)
        elif item["kind"] == "copy":
            catalog.learn(world, f"copy-{item['plan']}", item["plan"], source="mirror_entry")

    trained = next(item for item in sides["train"] if item["kind"] == "paraphrase")
    asked = catalog.ask(world, f"train-{trained['plan']}", trained["plan"], "monthly_price", trained["query"],
                        at="2026-03-01")
    assert asked["admission"]["decision"] == "admitted"

    offered_alike = 0
    for item in sides["exam"]:
        if item["kind"] != "paraphrase":
            continue
        asked = catalog.ask(world, f"exam-{item['plan']}", item["plan"], "monthly_price", item["query"],
                            at="2026-03-01")
        offered = {candidate["entity"] for candidate in asked["search"]["candidates"]}
        assert item["plan"] not in offered and offered <= groups["train"]
        assert asked["admission"]["decision"] == "abstained"
        assert all("another_entity" in check["reasons"] for check in asked["admission"]["checked"])
        offered_alike += len(offered)
    assert offered_alike  # Search did offer similar learned cards: the abstention is admission's, not search's.
