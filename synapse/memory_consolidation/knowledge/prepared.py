"""Prepare exact search projections of one pinned knowledge snapshot."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from synapse.palace_admission import content_tokens, overlap_score, tokens

from .statements import slot


def searchable(record: Mapping[str, Any]) -> dict[str, Any]:
    """The statement content read by the palace's lexical scorer."""
    return {"subject": record["subject"], "property": record["property"], "value": record["value"],
            "conditions": record["conditions"], "text": record["text"]}


class PreparedKnowledgeIndex:
    """Derived lookups of a session's fixed snapshot, each built on first use.

    The session owns the verified source entries. Only immutable identities
    occur in postings; slot groups reference the source entries without copying
    their payloads. No verdict or query result is cached here.
    A new session gets its own index even when its boundary is the same.
    """

    def __init__(self, versions: Mapping[str, Mapping[str, Any]]) -> None:
        self._versions = versions
        self._postings: dict[str, list[str]] | None = None
        self._groups: dict[str, list] | None = None

    def require_source(self, versions: Mapping[str, Mapping[str, Any]]) -> None:
        """Refuse mixing this session's projection with another source mapping.

        This checks ownership, not freshness: the session keeps its verified
        boundary fixed, while a standalone search creates a fresh projection.
        """
        if versions is not self._versions:
            raise ValueError("a prepared knowledge index belongs to its pinned source")

    def _prepare(self) -> None:
        postings: dict[str, list[str]] = {}
        for entry in self._versions.values():
            record = entry["record"]
            for word in content_tokens(searchable(record)):
                postings.setdefault(word, []).append(record["id"])
        self._postings = postings

    def rank(self, query: str, entries: Sequence[Mapping[str, Any]], budget: int) -> list[str]:
        """Rank only the versions held at this query's requested knowledge time."""
        wanted = tokens(query or "")
        if not wanted:
            return []
        if self._postings is None:
            self._prepare()
        eligible = {entry["record"]["id"] for entry in entries}
        matched: Counter[str] = Counter()
        for word in wanted:
            matched.update(identity for identity in self._postings.get(word, ()) if identity in eligible)
        scored = [(-score, identity) for identity, count in matched.items()
                  if (score := overlap_score(count, len(wanted))) > 0]
        return [identity for _, identity in sorted(scored)[:budget]]

    def members(self, slots: Sequence[str]) -> dict[str, list]:
        """Read all versions of reached slots after the query's embedding call."""
        if not slots:
            return {}
        if self._groups is None:
            groups: dict[str, list] = {}
            for entry in self._versions.values():
                groups.setdefault(slot(entry["record"]), []).append(entry)
            self._groups = groups
        return {found: self._groups.get(found, []) for found in slots}
