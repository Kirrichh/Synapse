"""Bound whole-memory traversal during fleet knowledge retrieval."""
from __future__ import annotations

import pytest

from acceptance.memory import _search_corpus as corpus
from acceptance.memory._search_reference import search as repeated_scan
from synapse.memory_consolidation.knowledge.search import search
from synapse.memory_consolidation.records import canonical


class CountedVersions(dict):
    """Observe entries visited through the memory mapping's values interface."""

    def __init__(self, entries):
        super().__init__(entries)
        self.visits = 0

    def values(self):
        for entry in super().values():
            self.visits += 1
            yield entry


@pytest.mark.parametrize("channels", [("lexical",), ("semantic",), ("lexical", "semantic")])
def test_large_fleet_returns_the_complete_preoptimization_result(channels):
    data = corpus.fleet_history(nodes=512, epochs=4)
    parameters = {"valid_at": corpus.MARCH, "known_as_of": 4, "channels": channels,
                  "embed": lambda query: ([1.0, 0.0, 0.0], 8.25), "embedded_by": corpus.MODEL}
    expected = repeated_scan("rebalance evacuation capacity", data, corpus.policy(100), **parameters)
    actual = search("rebalance evacuation capacity", data, corpus.policy(100), **parameters)
    assert canonical(actual) == canonical(expected)
    assert len(actual["fused"]) == 100
    assert len(actual["candidates"]) == 200
    assert sum(item["found_by"] == [] for item in actual["candidates"]) == 100


@pytest.mark.parametrize("budget", [1, 25, 100])
def test_whole_memory_traversal_stays_linear_as_more_slots_are_selected(budget):
    data = CountedVersions(corpus.fleet_history(nodes=128, epochs=3))
    parameters = {"valid_at": corpus.MARCH, "known_as_of": None, "channels": ("semantic",),
                  "embed": lambda query: ([1.0, 0.0, 0.0], 0.0), "embedded_by": corpus.MODEL}
    actual = search("capacity planning after rack failure", data, corpus.policy(budget), **parameters)
    assert len(actual["fused"]) == budget
    assert len(actual["candidates"]) == budget * 2
    # Candidate discovery plus grouping may each traverse the complete mapping.
    # Resolving more selected slots must not multiply whole-memory traversals.
    assert data.visits <= 2 * len(data)
