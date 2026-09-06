"""The delivered context has an explicit path to its actual replay and selection."""

import pytest

from acceptance.stage4.stage13._case import negative_attempt
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.stage14.graph import GraphBuilder, LineageGraph, LineageViolation


def test_delivered_context_reaches_the_selected_and_replayed_inputs(tmp_path, monkeypatch):
    attempt = negative_attempt(tmp_path)
    world = attempt.world
    world.composition.controller.execute()
    stored = world.composition.record_store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1")
    graph = LineageGraph.from_dict(stored.payload)
    roles = dict(graph.roles)
    ancestors = {node.node_id for node in graph.ancestors(roles["worker_context"])}
    for role in ("input.snapshot", "input.retrieval", "input.replay_result", "worker_audit"):
        assert roles[role] in ancestors, role
    original = GraphBuilder.link_roles

    def incomplete(self):
        original(self)
        if "worker_audit" in self.roles:
            self.edges = {edge for edge in self.edges
                          if (edge.source, edge.target) !=
                          (self.roles["input.replay_result"], self.roles["worker_audit"])}

    assert world.composition.controller.load_result().attempts
    with monkeypatch.context() as patch:
        patch.setattr(GraphBuilder, "link_roles", incomplete)
        with pytest.raises(LineageViolation):
            world.composition.controller.load_result()
    assert world.composition.controller.load_result().attempts
    assert world.worker_process.calls == 1
