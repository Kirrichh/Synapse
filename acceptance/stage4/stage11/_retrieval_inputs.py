"""Declare how this acceptance run ranks the candidates retrieval considers.

The §20 order that turns a declaration like this into a gated, durable
retrieval belongs to ``synapse.experiments.gold.run_retrieval`` and used to
live here. What is left is what only a deployment states: a scorer, and the
reference naming where that score's input came from. This suite scores every
candidate alike, because Stage 11 is about the run controller and not about
which behavior wins.
"""

from __future__ import annotations

import hashlib

from synapse.experiments.gold.canonicalization import HashBoundRef, RefKind
from synapse.experiments.gold.run_retrieval import RunRetrievalBindings


_RANKING_INPUT_SCHEMA = "acceptance.stage4.runner/ranking-input/v1"


def _ranking_input_ref(query_id, descriptor_id) -> HashBoundRef:
    """Name the exact (query, candidate) pair a score was computed over."""

    raw = f"{query_id.value}\0{descriptor_id.value}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return HashBoundRef(
        kind=RefKind.ARTIFACT,
        ref_id=digest,
        schema_id=_RANKING_INPUT_SCHEMA,
        sha256=digest,
        byte_length=len(raw),
        media_type="application/json",
    )


def acceptance_retrieval_bindings() -> RunRetrievalBindings:
    """The ranking every Stage 11 attempt in this suite retrieves under."""

    return RunRetrievalBindings(
        ranking_component_id="stage11-acceptance-score-provider",
        ranking_component_version="synapse.stage11.acceptance-score-provider/v1",
        scorer=lambda query_id, descriptor_id, score_input: 500_000,
        input_ref_resolver=_ranking_input_ref,
    )


__all__ = ["acceptance_retrieval_bindings"]
