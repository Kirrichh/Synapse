"""Search of semantic knowledge: two candidate channels, one budget (refinement §15).

The lexical channel ranks statements by the palace's token overlap
(``palace-lexical/v2``). The semantic channel ranks them by the cosine of the
query's embedding and each statement's recorded embedding; the embedding
model is a ``reason`` tool the gateway records, so a re-execution reads the
same vectors and the model's choice changes no authority. The two rankings are
merged by reciprocal rank fusion (Cormack, Clarke and Buettcher, SIGIR 2009):
ranks, never raw scores, so neither a model's confident score nor a mirror's
near-identical wording buys priority. Both channels draw from the same
budget of statements.

Search only proposes. The slots the fused ranking reaches are resolved on the
timeline at the asked valid time as memory knew it then, and every holding
version is returned in the shape admission reads (entity, attribute, value,
polarity, conditions, validity, currency, source); nothing here admits a
fact. Near-identical wording about another entity, a flipped negation and a
high similarity are refused by admission's structured checks, which dense
retrievers are known not to make (negation: NevIR; entities: EntityQuestions).

The cost of the semantic channel is published with every search: embedding
calls, vectors and dimensions searched, the index's size in bytes, and the
wall time of the query's embedding.
"""
from __future__ import annotations

import heapq
import math
import operator
from typing import Any, Callable, Iterable, Mapping, Sequence

from synapse.palace_admission import SCORER_VERSION, candidate_score

from .statements import freshness_of, slot
from .timeline import held_period, resolve

SEARCH_V1 = "synapse.memory.knowledge-search/v1"
RRF_K = 60  # The constant Cormack, Clarke and Buettcher found robust.
_BYTES_PER_COMPONENT = 8


def searchable(record: Mapping[str, Any]) -> dict[str, Any]:
    """The content of a statement the lexical channel reads (its structure and its text)."""
    return {"subject": record["subject"], "property": record["property"], "value": record["value"],
            "conditions": record["conditions"], "text": record["text"]}


def lexical_ranking(query: str, entries: Sequence[Mapping[str, Any]], budget: int) -> list[str]:
    scored = []
    for entry in entries:
        found = candidate_score(query, searchable(entry["record"]))
        if found is not None and found["score"] > 0:
            scored.append((-found["score"], entry["record"]["id"]))
    return [identity for _, identity in sorted(scored)[:budget]]


def semantic_ranking(query_vector: Sequence[float] | None, entries: Sequence[Mapping[str, Any]],
                     budget: int) -> list[str]:
    """The exact ranking by cosine (review §8.3): every recorded vector is compared — no approximate index, so
    no useful candidate is lost to the index — the query's norm is computed once and every similarity in the
    same arithmetic order, so the scores are the very floats a pairwise computation gives; equal scores are
    ordered by identity."""
    if query_vector is None:
        return []
    query_norm = math.sqrt(sum(map(operator.mul, query_vector, query_vector)))
    scored = []
    for entry in entries:
        vector = entry.get("vector")
        if vector is None or len(vector) != len(query_vector):
            continue
        norm = query_norm * math.sqrt(sum(map(operator.mul, vector, vector)))
        similarity = 0.0 if norm == 0 else sum(map(operator.mul, query_vector, vector)) / norm
        if similarity > 0:
            scored.append((-round(similarity, 12), entry["record"]["id"]))
    return [identity for _, identity in heapq.nsmallest(budget, scored)]


def fuse(rankings: Iterable[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion of rankings; ties by identity."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, identity in enumerate(ranking, 1):
            scores[identity] = scores.get(identity, 0.0) + 1.0 / (k + position)
    return [identity for identity, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def candidate(resolved: Mapping[str, Any], rank: int, channels: Mapping[str, list[str]]) -> dict[str, Any]:
    """One holding version in the shape admission reads."""
    entry = resolved["entry"]
    record = entry["record"]
    return {"id": record["id"], "kind": "fact", "entity": record["subject"], "attribute": record["property"],
            "value": record["value"], "polarity": record["polarity"], "conditions": dict(record["conditions"]),
            "valid_from": resolved["valid_from"], "valid_until": resolved["valid_until"],
            "freshness": resolved["freshness"], "text": record["text"],
            "source": {"tool": record["source"]["tool"], "ref": record["source"]["ref"], "source": entry["source"]},
            # The period memory held it in at the asked window, never the latest one (review R3).
            "known_from": resolved["known"]["from"], "known_until": resolved["known"]["until"], "rank": rank,
            "found_by": sorted(name for name, ranking in channels.items() if record["id"] in ranking)}


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


def recorded(identity: str, versions: Mapping[str, Mapping[str, Any]], policy, *, valid_at: str | None,
             known_as_of: int | None) -> tuple[dict[str, Any] | None, str | None]:
    """A statement memory recorded, in the shape admission reads, resolved again at ``valid_at`` as memory knew
    it at ``known_as_of`` from the record and the operator's currency rule — or why it does not hold then. A
    candidate is a copy the program holds; what memory recorded decides (review DEEP-4)."""
    entry = versions.get(identity)
    if entry is None:
        return None, "statement_not_recorded"
    members = [item for item in versions.values() if slot(item["record"]) == slot(entry["record"])]
    for resolved in resolve(members, freshness_of(policy, entry["record"]["property"]), valid_at=valid_at,
                            known_as_of=known_as_of):
        if resolved["entry"]["record"]["id"] == identity:
            return candidate(resolved, None, {}), None
    return None, "outside_validity"
