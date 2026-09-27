"""Search by meaning proposes; admission decides (refinement §15).

Memory knows what the catalog states about three plans. A question worded
with no word in common with the statement (another language, synonyms) is
found only by the semantic channel, and the candidate passes an independent
check of its content by the billing service before admission admits it. At
the same budget the lexical channel alone finds nothing.

The embedding model does not see negations or names: a statement that the
plan does *not* include backups and a near-identical sentence about another
plan both come back as candidates, and admission refuses both — another
polarity, another entity. Over the probe set the hybrid search finds every
useful statement the lexical one finds and more, with no false admission, and
every search publishes the semantic channel's cost.
"""
from __future__ import annotations

from acceptance.memory import _catalog as catalog

BASIC = "Basic plan costs 20 euro per month"


def _knowledge(world):
    for run_id, plan, prop, value, text, polarity in (
            ("learn-basic", "basic", "monthly_price", 20, BASIC, True),
            ("learn-plus", "basic-plus", "monthly_price", 30, "Basic Plus plan costs 30 euro per month", True),
            ("learn-backups", "starter", "backups_included", True, "The starter plan does not include backups",
             False)):
        catalog.publish(world, "catalog", plan, prop, value, text=text, start="2026-01-01", polarity=polarity)
        catalog.publish(world, "billing", plan, prop, value)
        catalog.learn(world, run_id, plan)


def test_a_paraphrase_without_common_words_is_found_by_meaning_and_admitted_only_after_its_check(tmp_path):
    world = catalog.world(tmp_path)
    _knowledge(world)
    query = "сколько стоит начальный тариф в месяц"

    lexical = catalog.ask(world, "ask-lexical", "basic", "monthly_price", query, at="2026-03-01",
                          channels=["lexical"])
    assert lexical["search"]["channels"] == {"lexical": []} and lexical["search"]["candidates"] == []
    assert lexical["admission"]["decision"] == "abstained"

    hybrid = catalog.ask(world, "ask-hybrid", "basic", "monthly_price", query, at="2026-03-01")
    search = hybrid["search"]
    assert search["channels"]["lexical"] == [] and search["budget"] == 3
    found = {(item["entity"], item["value"]): item for item in search["candidates"]}
    assert found[("basic", 20)]["found_by"] == ["semantic"] and found[("basic", 20)]["freshness"] == "current"
    decision = hybrid["admission"]
    assert decision["decision"] == "admitted" and decision["fact"] == found[("basic", 20)]["id"]
    checked = {item["id"]: item for item in decision["checked"]}
    assert checked[found[("basic", 20)]["id"]]["status_basis"] == "hypothesis"
    # The near-identical sentence about another plan is a candidate, never the same fact.
    assert "another_entity" in checked[found[("basic-plus", 30)]["id"]]["reasons"]
    # The cost of the semantic channel is published with the search.
    cost = search["cost"]
    assert cost["embedding_calls"] == 1 and cost["vectors"] == 3 and cost["dimensions"] == len(catalog.CONCEPTS)
    assert cost["index_bytes"] == 3 * len(catalog.CONCEPTS) * 8 and cost["embedding_ms"] >= 0


def test_a_negation_and_another_entity_do_not_become_the_same_fact(tmp_path):
    world = catalog.world(tmp_path)
    _knowledge(world)
    asked = catalog.ask(world, "ask-backups", "starter", "backups_included",
                        "включает ли начальный тариф резервные копии", at="2026-03-01")
    denial, = [item for item in asked["search"]["candidates"] if item["attribute"] == "backups_included"]
    assert denial["polarity"] is False and "semantic" in denial["found_by"]
    assert asked["admission"]["decision"] == "abstained"
    reasons, = [item["reasons"] for item in asked["admission"]["checked"] if item["id"] == denial["id"]]
    assert "another_polarity" in reasons

    other = catalog.ask(world, "ask-other", "basic", "monthly_price", "Basic Plus plan costs 30 euro per month",
                        at="2026-03-01")
    # The closest wording is another plan's: found by both channels, and still another entity.
    plus, = [item for item in other["search"]["candidates"] if item["entity"] == "basic-plus"]
    assert plus["found_by"] == ["lexical", "semantic"]
    assert other["admission"]["decision"] == "admitted" and other["admission"]["fact"] != plus["id"]
    reasons, = [item["reasons"] for item in other["admission"]["checked"] if item["id"] == plus["id"]]
    assert "another_entity" in reasons


def test_the_hybrid_search_finds_more_at_the_same_budget_and_admits_nothing_false(tmp_path):
    world = catalog.world(tmp_path)
    _knowledge(world)
    probes = (("basic monthly price", "basic", "monthly_price"),
              ("сколько стоит базовый тариф ежемесячно", "basic", "monthly_price"),
              ("Basic Plus plan costs", "basic-plus", "monthly_price"))
    found = {"lexical": 0, "hybrid": 0}
    false_admissions = 0
    for index, (query, plan, prop) in enumerate(probes):
        for mode, channels in (("lexical", ["lexical"]), ("hybrid", ["lexical", "semantic"])):
            asked = catalog.ask(world, f"probe-{index}-{mode}", plan, prop, query, at="2026-03-01", channels=channels)
            if any(item["entity"] == plan and item["attribute"] == prop for item in asked["search"]["candidates"]):
                found[mode] += 1
            fact = asked["admission"]["fact"]
            admitted = [item for item in asked["search"]["candidates"] if item["id"] == fact]
            if admitted and (admitted[0]["entity"] != plan or admitted[0]["attribute"] != prop):
                false_admissions += 1
    assert found == {"lexical": 2, "hybrid": 3} and false_admissions == 0
