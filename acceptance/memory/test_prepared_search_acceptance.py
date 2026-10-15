"""Accept prepared lexical retrieval against the original search semantics."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from acceptance.memory import _search_corpus as corpus
from acceptance.memory._lexical_reference import lexical_ranking
from acceptance.memory._search_reference import search as reference_search
from synapse import palace_admission
from synapse.memory_consolidation.configuration import KnowledgePolicy
from synapse.memory_consolidation.knowledge.search import search
from synapse.memory_consolidation.records import canonical, make
from synapse.memory_consolidation.session import MemorySession


def _index(data):
    from synapse.memory_consolidation.knowledge.prepared import PreparedKnowledgeIndex

    return PreparedKnowledgeIndex(data)


def _parameters(**changes):
    return {"valid_at": corpus.MARCH, "known_as_of": None,
            "channels": ("lexical", "semantic"), "embedded_by": corpus.MODEL,
            "embed": lambda query: ([1.0, 0.25, 0.0], 2.5), **changes}


def _compare(data, index, query="rebalance evacuation capacity", budget=20, **changes):
    parameters = _parameters(**changes)
    before = canonical(data)
    expected = reference_search(query, data, corpus.policy(budget), **parameters)
    actual = search(query, data, corpus.policy(budget), index=index, **parameters)
    assert canonical(actual) == canonical(expected)
    assert canonical(data) == before
    return actual


@pytest.mark.parametrize("channels", [("lexical",), ("semantic",), ("lexical", "semantic")])
def test_reused_preparation_preserves_every_historical_question(channels):
    data = corpus.versions(corpus.engineering_history().values())
    prepared = _index(data)
    moments = [(corpus.JANUARY, 1), (corpus.MARCH, 2), (corpus.MARCH, 3),
               (corpus.MARCH, 4), (corpus.FEBRUARY, 5), (corpus.MARCH, 7),
               (None, None), ("2025-12-01T00:00:00Z", None), (corpus.JANUARY, None)]
    for valid_at, known_as_of in moments:
        _compare(data, prepared, channels=channels, valid_at=valid_at, known_as_of=known_as_of)


@pytest.mark.parametrize("query", ["STRASSE реплика CAFÉ", "strasse strasse", "a x 7", "", "!!!",
                                  "sourceonlyneedle", "condition_key_only", "rackwest", "capacity",
                                  "incarnation", "999 true", "same_word"])
def test_prepared_tokens_keep_unicode_content_projection_and_empty_queries(query):
    entries = [corpus.entry("sourceonlyneedle", text="Straße РЕПЛИКА café x 7", value=999,
                            conditions={"condition_key_only": "rackwest"}),
               corpus.entry("unicode-copy", entity="secondary", text="STRASSE реплика CAFÉ", value=True),
               corpus.entry("plain", entity="third", text="same_word"),
               corpus.entry("punctuation", entity="fourth", text="No lexical overlap")]
    data = corpus.versions(entries)
    result = _compare(data, _index(data), query=query, channels=("lexical",))
    assert result["channels"]["lexical"] == lexical_ranking(query, entries, 20)
    if query in {"a x 7", "", "!!!", "sourceonlyneedle", "condition_key_only", "rackwest", "999 true"}:
        assert result["candidates"] == []


@pytest.mark.parametrize("budget", [1, 2, 100])
def test_overlap_and_identity_ties_survive_reordered_input(budget):
    entries = [corpus.entry(f"tie-{number}", entity=f"replica-{number}", text=text)
               for number, text in enumerate(["rack quorum", "rack quorum", "rack", "unrelated"])]
    data = corpus.versions(reversed(entries))
    prepared = _index(data)
    for query in ("rack quorum capacity", "rack rack quorum", "quorum rack"):
        result = _compare(data, prepared, query=query, budget=budget, channels=("lexical",))
        assert result["channels"]["lexical"] == lexical_ranking(query, entries, budget)
    expected = sorted(item["record"]["id"] for item in entries[:2])
    assert result["fused"][:min(2, budget)] == expected[:budget]


def test_a_selected_slot_keeps_an_unranked_independent_conflict():
    primary = corpus.entry("primary", text="precisionevacuation", value=96)
    sibling = corpus.entry("independent", text="rack attestation", vector=None, value=72,
                           source="rack-controller:independent")
    data = corpus.versions([sibling, primary])
    result = _compare(data, _index(data), query="precisionevacuation", budget=1, channels=("lexical",))
    assert result["fused"] == [primary["record"]["id"]]
    assert {item["value"] for item in result["candidates"]} == {72, 96}
    assert next(item for item in result["candidates"] if item["value"] == 72)["found_by"] == []


def test_preparation_does_not_cache_queries_embedding_answers_or_cost():
    entries = [corpus.entry("first", entity="first", vector=(1.0, 0.0, 0.0)),
               corpus.entry("second", entity="second", vector=(0.0, 1.0, 0.0)),
               corpus.entry("rounded-tie", entity="third", vector=(1.0, 0.0000001, 0.0)),
               corpus.entry("zero", entity="zero", vector=(0.0, 0.0, 0.0)),
               corpus.entry("foreign", entity="foreign", model="other-model"),
               corpus.entry("different-dimension", entity="short", vector=(1.0,))]
    data, calls = corpus.versions(entries), []
    prepared = _index(data)
    for vector, elapsed in [([1.0, 0.0, 0.0], 1.5), ([0.0, 1.0, 0.0], 4.5), (None, 7.5)]:
        def embed(query):
            calls.append(query)
            return vector, elapsed

        parameters = _parameters(channels=("semantic",), embed=embed)
        result = search("same query", data, corpus.policy(2), index=prepared, **parameters)
        expected = reference_search("same query", data, corpus.policy(2), **parameters)
        assert canonical(result) == canonical(expected)
        assert result["cost"]["embedding_calls"] == 1 and result["cost"]["embedding_ms"] == elapsed
        if elapsed == 1.5:
            assert result["fused"] == sorted(entries[number]["record"]["id"] for number in (0, 2))
    assert calls == ["same query"] * 6
    assert result["fused"] == []


def test_semantic_only_queries_do_not_prepare_lexical_postings(monkeypatch):
    original, processed = palace_admission._TOKEN, []

    def measured(text):
        processed.append(text)
        return original.findall(text)

    monkeypatch.setattr(palace_admission, "_TOKEN", SimpleNamespace(findall=measured))
    data = corpus.versions([corpus.entry("semantic", text="storage recovery")])
    prepared = _index(data)
    result = search("reconstruction", data, corpus.policy(), index=prepared,
                    **_parameters(channels=("semantic",)))
    assert result["candidates"]
    assert processed == []


def _session(data, *, run_id="retrieval", budget=20):
    policy = KnowledgePolicy(None, corpus.policy(budget).properties, budget)
    factory = SimpleNamespace(configuration=SimpleNamespace(knowledge=policy))
    boundary = make("snapshot_boundary", boundary={"habits": [], "knowledge": data})
    return MemorySession(factory, {"run_id": run_id}, boundary, None)


def _ask(session, query="rebalance capacity", *, known_as_of=None):
    return session.search_knowledge(query, valid_at=corpus.MARCH, known_as_of=known_as_of,
                                    channels=("lexical",), embed=None)


def test_each_session_prepares_only_its_pinned_snapshot():
    original = corpus.entry("original", value=96)
    original_data = corpus.versions([original])
    old = _session(original_data, run_id="old")
    before = _ask(old)
    updated = deepcopy(original_data)
    updated[original["record"]["id"]]["known_until"] = 2
    correction = corpus.entry("corrected", value=48, known_from=2)
    updated[correction["record"]["id"]] = correction
    new = _session(updated, run_id="new")
    assert [item["value"] for item in _ask(new)["candidates"]] == [48]
    assert canonical(_ask(old)) == canonical(before)
    assert [item["value"] for item in _ask(new, known_as_of=1)["candidates"]] == [96]


def test_empty_pinned_knowledge_keeps_the_existing_public_result():
    session = _session({})
    result = _ask(session)
    expected = reference_search("rebalance capacity", {}, corpus.policy(),
                                **_parameters(channels=("lexical",), embed=None, embedded_by=None))
    assert canonical(result) == canonical(expected)
    assert _ask(session, query="another question")["candidates"] == []


@pytest.mark.parametrize("channels", [("lexical",), ("semantic",), ()])
def test_preparation_refuses_another_source_with_the_same_identities(channels):
    data = corpus.versions([corpus.entry("original")])
    prepared = _index(data)
    _compare(data, prepared)
    foreign = deepcopy(data)
    identity = next(iter(foreign))
    foreign[identity]["known_until"] = 2
    foreign[identity]["vector"] = [0.0, 1.0, 0.0]
    assert foreign.keys() == data.keys() and foreign != data
    calls = []

    def embed(query):
        calls.append(query)
        return [1.0, 0.0, 0.0], 0.0

    with pytest.raises(ValueError, match="pinned source"):
        search("rebalance capacity", foreign, corpus.policy(), index=prepared,
               **_parameters(channels=channels, embed=embed))
    assert calls == []
    assert _compare(data, prepared)["candidates"][0]["value"] == 96
