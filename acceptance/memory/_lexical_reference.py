"""Freeze the lexical candidate ranking that preceded prepared postings."""
from __future__ import annotations

from synapse.palace_admission import content_tokens, tokens


def searchable(record):
    """Content projection from search.py at f2bf791; metadata never enters ranking."""
    return {"subject": record["subject"], "property": record["property"], "value": record["value"],
            "conditions": record["conditions"], "text": record["text"]}


def lexical_ranking(query, entries, budget):
    """Retain the original per-record score, six-place rounding, positive-score cutoff."""
    scored = []
    for entry in entries:
        wanted = tokens(query or "")
        matched = wanted & content_tokens(searchable(entry["record"])) if wanted else ()
        score = round(len(matched) / len(wanted), 6) if wanted else 0.0
        if score > 0:
            scored.append((-score, entry["record"]["id"]))
    return [identity for _, identity in sorted(scored)[:budget]]
