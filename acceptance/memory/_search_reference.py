"""Freeze the repeated-scan search used before query-local grouping.

Source: synapse/memory_consolidation/knowledge/search.py, commit
fc5509e0f1c29fecece4427b6f95c6dbe053e501. Only imports are adapted: ranking,
timeline resolution and result shaping remain the unchanged product functions.
This acceptance oracle must not follow changes to the search algorithm.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from synapse.memory_consolidation.knowledge.search import (
    SEARCH_V1, candidate, fuse, lexical_ranking, semantic_ranking,
)
from synapse.memory_consolidation.knowledge.statements import freshness_of, slot
from synapse.memory_consolidation.knowledge.timeline import held_period, resolve
from synapse.palace_admission import SCORER_VERSION

_BYTES_PER_COMPONENT = 8


def search(query: str, versions: Mapping[str, Mapping[str, Any]], policy, *, valid_at: str | None,
           known_as_of: int | None, embed: Callable[[str], tuple[list[float] | None, float | None]] | None,
           channels: Sequence[str] = ("lexical", "semantic"), embedded_by: str | None = None) -> dict[str, Any]:
    """Candidates for ``query`` at ``valid_at`` as known at ``known_as_of``, with each channel's ranking and cost.

    The semantic channel compares the query's vector only with vectors of the same embedder (``embedded_by``):
    equal dimensions are no common space, so a vector of another model, or of none named, is not searched
    until a reading under this one indexes it again (review DEEP-6)."""
    budget = policy.budget
    # A version read from forgotten results left the index: it stays history, never a candidate.
    held = sorted((entry for entry in versions.values() if held_period(entry, known_as_of) is not None),
                  key=lambda entry: entry["record"]["id"])
    rankings: dict[str, list[str]] = {}
    cost = {"embedding_calls": 0, "vectors": 0, "dimensions": None, "index_bytes": 0, "embedding_ms": None}
    if "lexical" in channels:
        rankings["lexical"] = lexical_ranking(query, held, budget)
    if "semantic" in channels and embed is not None:
        vector, elapsed = embed(query)
        comparable = [entry for entry in held if embedded_by is not None and entry.get("embedded_by") == embedded_by]
        indexed = [entry["vector"] for entry in comparable if entry.get("vector") is not None]
        cost.update(embedding_calls=1, vectors=len(indexed), embedding_ms=elapsed,
                    dimensions=None if vector is None else len(vector),
                    index_bytes=sum(len(item) for item in indexed) * _BYTES_PER_COMPONENT)
        rankings["semantic"] = semantic_ranking(vector, comparable, budget)
    fused = fuse(rankings.values())[:budget]
    slots: list[str] = []
    by_id = {entry["record"]["id"]: entry for entry in held}
    for identity in fused:
        found = slot(by_id[identity]["record"])
        if found not in slots:
            slots.append(found)
    candidates = []
    for rank, found in enumerate(slots, 1):
        members = [entry for entry in versions.values() if slot(entry["record"]) == found]
        rule = freshness_of(policy, members[0]["record"]["property"])
        for resolved in resolve(members, rule, valid_at=valid_at, known_as_of=known_as_of):
            candidates.append(candidate(resolved, rank, rankings))
    return {"schema_version": SEARCH_V1, "lexical_scorer": SCORER_VERSION, "budget": budget,
            "valid_at": valid_at, "known_as_of": known_as_of, "channels": rankings, "fused": fused,
            "candidates": candidates, "cost": cost}
