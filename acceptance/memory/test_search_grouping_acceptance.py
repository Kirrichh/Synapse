"""Accept query-local grouping against the recorded search contract."""
from __future__ import annotations

import pytest

from acceptance.memory import _search_corpus as corpus
from acceptance.memory._search_reference import search as repeated_scan
from synapse.memory_consolidation.knowledge.search import search
from synapse.memory_consolidation.records import canonical
from synapse.palace_admission import SCORER_VERSION


def _compare(versions, *, query="rebalance evacuation capacity", budget=20, channels=("lexical", "semantic"),
             valid_at=corpus.MARCH, known_as_of=None, vector=(1.0, 0.25, 0.0), model=corpus.MODEL):
    before = canonical(versions)
    calls = []

    def embed(text):
        calls.append(text)
        return None if vector is None else list(vector), 12.5

    arguments = {"valid_at": valid_at, "known_as_of": known_as_of, "embed": embed,
                 "channels": channels, "embedded_by": model}
    expected = repeated_scan(query, versions, corpus.policy(budget), **arguments)
    actual = search(query, versions, corpus.policy(budget), **arguments)
    assert canonical(actual) == canonical(expected)
    assert canonical(versions) == before
    assert calls == [query] * (2 if "semantic" in channels else 0)
    assert actual["schema_version"] == "synapse.memory.knowledge-search/v1"
    assert actual["lexical_scorer"] == SCORER_VERSION
    return actual


@pytest.mark.parametrize("channels", [("lexical",), ("semantic",), ("lexical", "semantic")])
@pytest.mark.parametrize("valid_at,known_as_of", [
    (None, None), (corpus.JANUARY, 1), (corpus.MARCH, 2), (corpus.MARCH, 3),
    (corpus.MARCH, 4), (corpus.FEBRUARY, 5), (corpus.MARCH, 7),
    ("2025-12-01T00:00:00Z", None), ("2026-01-15T00:00:00Z", None),
])
def test_every_channel_preserves_full_results_across_both_times(channels, valid_at, known_as_of):
    history = corpus.engineering_history()
    actual = _compare(corpus.versions(reversed(list(history.values()))), channels=channels,
                      valid_at=valid_at, known_as_of=known_as_of)
    assert history["withdrawn"]["record"]["id"] not in actual["fused"]
    assert all(item["id"] != history["withdrawn"]["record"]["id"] for item in actual["candidates"])


@pytest.mark.parametrize("window,valid_at,values", [
    (2, corpus.MARCH, {128}), (3, corpus.MARCH, {112}), (4, corpus.MARCH, {96}),
    (5, corpus.MARCH, {96, 72}), (None, corpus.JANUARY, {112}),
])
def test_late_corrections_resolve_only_within_their_known_window(window, valid_at, values):
    history = corpus.engineering_history()
    actual = _compare(corpus.versions(history.values()), budget=100, channels=("lexical",),
                      known_as_of=window, valid_at=valid_at)
    selected = [item for item in actual["candidates"] if item["entity"] == corpus.NODE
                and item["attribute"] == "capacity" and not item["conditions"]]
    assert {item["value"] for item in selected} == values
    if window is None:
        assert selected[0]["valid_until"] == corpus.FEBRUARY


def test_unranked_conflict_keeps_its_independent_source_outside_the_candidate_budget():
    primary = corpus.entry("primary", text="evacuationneedle", value=96)
    conflicting = corpus.entry("independent", text="rack observation", vector=None, value=72,
                               source="rack-controller:independent")
    actual = _compare(corpus.versions([conflicting, primary]), query="evacuationneedle", budget=1,
                      channels=("lexical",))
    assert actual["fused"] == [primary["record"]["id"]]
    assert actual["budget"] == 1 and len(actual["candidates"]) == 2
    by_id = {item["id"]: item for item in actual["candidates"]}
    sibling = by_id[conflicting["record"]["id"]]
    assert sibling["found_by"] == [] and sibling["rank"] == 1
    assert sibling["source"]["source"] == "rack-controller:independent"
    assert sibling["source"]["ref"] == "evidence:independent"
    assert {item["value"] for item in actual["candidates"]} == {72, 96}


@pytest.mark.parametrize("window,period", [(1, (1, 2)), (2, None), (4, (4, 5)), (6, None), (7, (7, None))])
def test_returned_statement_uses_the_historical_period_requested(window, period):
    returned = corpus.engineering_history()["returned"]
    actual = _compare(corpus.versions([returned]), known_as_of=window)
    if period is None:
        assert actual["candidates"] == []
    else:
        candidate, = actual["candidates"]
        assert (candidate["known_from"], candidate["known_until"]) == period


def test_incarnations_conditions_polarity_and_currency_keep_their_meaning():
    actual = _compare(corpus.versions(corpus.engineering_history().values()), budget=100)
    candidates = actual["candidates"]
    recreated, = [item for item in candidates if isinstance(item["entity"], dict)
                   and item["entity"].get("uid") == "incarnation-b"]
    assert recreated["value"] == 256
    follower, = [item for item in candidates if item["conditions"] == {"role": "follower"}]
    assert follower["value"] == 80 and follower["entity"] == corpus.NODE
    denial, = [item for item in candidates if item["attribute"] == "certificate"]
    assert denial["polarity"] is False
    load, = [item for item in candidates if item["attribute"] == "load"]
    assert (load["freshness"], load["valid_until"]) == ("unknown", "2026-02-03T00:00:00Z")
    assert not any(item["attribute"] == "failure" for item in candidates)
    undeclared, = [item for item in candidates if item["attribute"] == "firmware"]
    assert undeclared["freshness"] == "undeclared"


@pytest.mark.parametrize("model", [corpus.MODEL, "ops-embedding/v2", None])
@pytest.mark.parametrize("vector", [(1.0, 0.0, 0.0), None])
def test_embedding_compatibility_and_reported_cost_are_unchanged(model, vector):
    entries = [corpus.entry("matching"), corpus.entry("foreign", entity="other", model="ops-embedding/v2"),
               corpus.entry("anonymous", entity="unnamed", model=None),
               corpus.entry("short", entity="short", vector=(1.0,)),
               corpus.entry("missing", entity="missing", vector=None)]
    actual = _compare(corpus.versions(entries), channels=("semantic",), model=model, vector=vector)
    expected_count, expected_bytes = {corpus.MODEL: (2, 32), "ops-embedding/v2": (1, 24), None: (0, 0)}[model]
    assert actual["cost"] == {"embedding_calls": 1, "vectors": expected_count,
                              "dimensions": None if vector is None else 3,
                              "index_bytes": expected_bytes, "embedding_ms": 12.5}
    if vector is None or model is None:
        assert actual["candidates"] == []


@pytest.mark.parametrize("budget", [1, 2, 100])
def test_equal_rankings_keep_identity_order_under_each_budget(budget):
    entries = [corpus.entry(f"independent-{index}", source=f"source-{index}") for index in range(3)]
    actual = _compare(corpus.versions(reversed(entries)), budget=budget)
    identities = sorted(item["record"]["id"] for item in entries)
    assert actual["fused"] == identities[:budget]
    assert actual["channels"] == {"lexical": identities[:budget], "semantic": identities[:budget]}
    assert [item["id"] for item in actual["candidates"]] == identities
    assert len({item["source"]["source"] for item in actual["candidates"]}) == 3


@pytest.mark.parametrize("empty", [False, True])
def test_no_result_preserves_the_empty_shape(empty):
    data = {} if empty else corpus.versions(corpus.engineering_history().values())
    actual = _compare(data, query="xyznonmatchingneedle", channels=("lexical",), budget=1)
    assert actual["channels"] == {"lexical": []}
    assert actual["fused"] == [] and actual["candidates"] == []
    assert actual["cost"] == {"embedding_calls": 0, "vectors": 0, "dimensions": None,
                              "index_bytes": 0, "embedding_ms": None}


def test_grouping_observes_the_versions_present_after_embedding():
    primary = corpus.entry("primary")
    sibling = corpus.entry("sibling", value=72, text="independent attestation", vector=None)
    outputs = []
    for implementation in (repeated_scan, search):
        data = corpus.versions([primary])

        def embed(query):
            data[sibling["record"]["id"]] = sibling
            return [1.0, 0.0, 0.0], 0.0

        outputs.append(implementation("rebalance", data, corpus.policy(1), valid_at=corpus.MARCH,
                                      known_as_of=None, embed=embed, embedded_by=corpus.MODEL))
    assert canonical(outputs[0]) == canonical(outputs[1])
    assert {item["value"] for item in outputs[1]["candidates"]} == {72, 96}


def test_a_following_query_observes_corrections_without_a_persistent_cache():
    primary = corpus.entry("primary", known_until=2)
    data = corpus.versions([primary])
    assert _compare(data, known_as_of=1)["candidates"][0]["value"] == 96
    correction = corpus.entry("correction", value=48, known_from=2)
    data[correction["record"]["id"]] = correction
    latest = _compare(data, known_as_of=2)
    assert [item["value"] for item in latest["candidates"]] == [48]
