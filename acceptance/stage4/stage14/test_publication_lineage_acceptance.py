"""Lineage is part of the actual publication commit, including its proof link."""
import pytest

from acceptance.stage4.stage13._case import negative_attempt, publication_case
from synapse.experiments.gold.persistence import read_committed_snapshot_transaction
from synapse.experiments.gold.stage10.context_codec import decode_canonical
from synapse.experiments.gold.stage14.graph import LineageGraph, GraphBuilder, LineageEdgeKind


def test_publication_fragment_is_committed_and_required(tmp_path, monkeypatch):
    attempt = negative_attempt(tmp_path / "attempt")
    case = publication_case(tmp_path / "project", attempt)
    result = case.publisher.publish(case.request)
    _, members = read_committed_snapshot_transaction(case.publisher.root / "committed", transaction_id=result.transaction_id)
    assert set(members) == {"decision.json", "index.json", "result.json", "lineage.json"}
    graph = LineageGraph.from_dict(decode_canonical(members["lineage.json"]))
    assert result.payload()["lineage_ref"]["sha256"]
    roles = dict(graph.roles)
    ancestry = {n.node_id for n in graph.ancestors(roles["manifest"])}
    for role in ("verification", "verified_outcome", "input.snapshot", "input.retrieval", "plan", "worker_context", "oracle"):
        assert roles[role] in ancestry, role
    original = GraphBuilder.link_roles

    def changed(self):
        original(self)
        if self.profile == "publication/v1":
            self.edges = {e for e in self.edges if not (e.source == self.roles["verification"]
                and e.target == self.roles["publication_decision"] and e.kind is LineageEdgeKind.VERIFIED_BY)}

    # The real disk reader independently reconstructs the authority fragment.
    with monkeypatch.context() as patch:
        patch.setattr(GraphBuilder, "link_roles", changed)
        with pytest.raises(ValueError):
            result.payload()
    assert result.payload()["lineage_ref"]["sha256"]
