"""Bound repeated search preparation for fleet investigation sessions."""
from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from acceptance.memory import _search_corpus as corpus
from acceptance.memory._search_reference import search as reference_search
from synapse import palace_admission
from synapse.memory_consolidation import records
from synapse.memory_consolidation.configuration import KnowledgePolicy
from synapse.memory_consolidation.knowledge.search import search
from synapse.memory_consolidation.records import canonical, make
from synapse.memory_consolidation.session import MemorySession


class TokenizationMeter:
    """Measure text processed by the real lexical tokenizer."""

    def __init__(self, expression):
        self.expression = expression
        self.texts = Counter()

    def findall(self, text):
        self.texts[text] += 1
        return self.expression.findall(text)


def _session(data, run_id):
    policy = KnowledgePolicy(None, corpus.policy(100).properties, 100)
    factory = SimpleNamespace(configuration=SimpleNamespace(knowledge=policy))
    boundary = make("snapshot_boundary", boundary={"habits": [], "knowledge": data})
    return MemorySession(factory, {"run_id": run_id}, boundary, None)


def _search_parameters(window, channels):
    return {"valid_at": corpus.MARCH, "known_as_of": window, "channels": channels,
            "embed": lambda query: ([1.0, 0.0, 0.0], 3.75), "embedded_by": corpus.MODEL}


@pytest.mark.parametrize("channels", [("lexical",), ("semantic",), ("lexical", "semantic")])
def test_repeated_fleet_investigations_preserve_the_complete_search(channels):
    from synapse.memory_consolidation.knowledge.prepared import PreparedKnowledgeIndex

    data = corpus.fleet_history(nodes=512, epochs=4)
    prepared = PreparedKnowledgeIndex(data)
    questions = [(1, "evacuation capacity historical review"),
                 (3, "rebalance capacity before recovery"),
                 (None, "rebalance evacuation capacity concurrent rack investigation")]
    for window, query in questions:
        parameters = _search_parameters(window, channels)
        expected = reference_search(query, data, corpus.policy(100), **parameters)
        actual = search(query, data, corpus.policy(100), index=prepared, **parameters)
        assert canonical(actual) == canonical(expected)
        assert len(actual["fused"]) == 100
        if window is None:
            assert len(actual["candidates"]) == 200
            assert {item["value"] for item in actual["candidates"]} == {56, 72}


def test_repeated_session_searches_do_not_retokenize_the_fleet(monkeypatch):
    data = corpus.fleet_history(nodes=512, epochs=4)
    session = _session(data, "fleet-investigation")
    questions = [f"rebalance evacuation capacity investigation{number}" for number in range(8)]
    expected = [reference_search(query, data, corpus.policy(100), valid_at=corpus.MARCH,
                                known_as_of=None, channels=("lexical",), embed=None)
                for query in questions]
    documents = Counter(entry["record"]["text"].casefold() for entry in data.values())
    meter = TokenizationMeter(palace_admission._TOKEN)
    monkeypatch.setattr(palace_admission, "_TOKEN", meter)
    for query, reference in zip(questions, expected):
        actual = session.search_knowledge(query, valid_at=corpus.MARCH, known_as_of=None,
                                         channels=("lexical",), embed=None)
        assert canonical(actual) == canonical(reference)
    processed = sum(len(text) * meter.texts[text] for text in documents)
    preparation_budget = sum(len(text) * count for text, count in documents.items())
    assert processed <= preparation_budget
    assert all(meter.texts[query.casefold()] <= 1 for query in questions)
    # Starting another owner session must not retain a process-global lexical corpus.
    following = _session(data, "independent-fleet-investigation")
    meter.texts.clear()
    assert canonical(following.search_knowledge(questions[0], valid_at=corpus.MARCH, known_as_of=None,
                                                channels=("lexical",), embed=None)) == canonical(expected[0])
    assert sum(meter.texts[text] for text in documents) > 0


def _semantic_session(data):
    policy = corpus.policy(100)
    contract = SimpleNamespace(contract_ref={"profile": "slot-preparation-acceptance"})
    tools = SimpleNamespace(contract=lambda name: contract)
    factory = SimpleNamespace(configuration=SimpleNamespace(knowledge=policy, tools=tools))
    model = records.digest({"tool": policy.embedder.tool, "version": policy.embedder.version,
                            "contract": contract.contract_ref})
    data = {identity: {**entry, "embedded_by": model} for identity, entry in data.items()}
    boundary = make("snapshot_boundary", boundary={"habits": [], "knowledge": data})
    return MemorySession(factory, {"run_id": "semantic-fleet-investigation"}, boundary, None), data


def _slot_bytes(entry):
    record = entry["record"]
    return len(canonical({"subject": record["subject"], "property": record["property"],
                          "conditions": record["conditions"]}))


def test_repeated_semantic_questions_do_not_regroup_the_complete_fleet(monkeypatch):
    session, data = _semantic_session(corpus.fleet_history(nodes=512, epochs=4))
    parameters = _search_parameters(None, ("semantic",))
    parameters["embedded_by"] = session.embedded_by()
    questions = [f"investigate quorum after regional disturbance {number}" for number in range(6)]
    expected = [reference_search(query, data, corpus.policy(100), **parameters) for query in questions]
    preparation_budget = sum(_slot_bytes(entry) for entry in data.values())
    reached_budget = sum(_slot_bytes(data[identity]) for result in expected for identity in result["fused"])
    original, processed = records.canonical, []

    def measured(value):
        raw = original(value)
        if type(value) is dict and set(value) == {"subject", "property", "conditions"}:
            processed.append(len(raw))
        return raw

    monkeypatch.setattr(records, "canonical", measured)
    for query, reference in zip(questions, expected):
        actual = session.search_knowledge(query, valid_at=corpus.MARCH, known_as_of=None,
                                         channels=("semantic",), embed=parameters["embed"])
        assert canonical(actual) == canonical(reference)
    # Grouping may serialize the complete snapshot once; each question can still
    # identify the slots of its ranked candidates. Re-reading every other subject
    # for every question exceeds that useful work even when the results agree.
    assert sum(processed) <= preparation_budget + reached_budget
