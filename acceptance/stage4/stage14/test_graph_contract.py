"""External, fast Stage 14 graph contract checks; no execution authority."""
from dataclasses import replace

import pytest

from synapse.experiments.gold.contracts import LineageEdgeKind as Kind
from synapse.experiments.gold.stage14.graph import (
    GraphBuilder, LineageEdge, LineageGraph, LineageNodeClass as Node,
    LineageViolation, LineageFailureCode as Failure, record_reference,
)


def input_graph():
    b = GraphBuilder("inputs/v1", "run-a", "1")
    for role, kind in (("boundary", Node.SNAPSHOT_BOUNDARY), ("snapshot", Node.KNOWLEDGE_SNAPSHOT),
            ("consumer", Node.CONSUMER_CONTEXT), ("retrieval_gate", Node.ADMISSION_DECISION),
            ("retrieval", Node.RETRIEVAL_DECISION), ("replay_request", Node.REPLAY_REQUEST),
            ("replay_consumption_gate", Node.ADMISSION_DECISION),
            ("replay_result", Node.REPLAY_RESULT)):
        b.record(role, kind, {"role": role}, "synapse.acceptance.lineage/v1")
    b.link_roles()
    return b.finish()


def test_restart_and_permutation_preserve_graph_identity():
    graph = input_graph()
    reordered = replace(graph, nodes=graph.nodes[::-1], edges=graph.edges[::-1], roles=graph.roles[::-1])
    assert graph.to_dict() == reordered.to_dict()
    assert LineageGraph.from_dict(graph.to_dict()).to_dict() == graph.to_dict()


def test_identical_artifact_is_a_distinct_execution_occurrence():
    graph = input_graph()
    node = graph.nodes[0]
    assert replace(node, attempt_id="2").reference == node.reference
    assert replace(node, attempt_id="2").node_id != node.node_id
    assert replace(node, run_id="run-b").node_id != node.node_id


@pytest.mark.parametrize("edge", input_graph().edges)
def test_every_mandatory_input_relation_is_required(edge):
    graph = input_graph()
    with pytest.raises(LineageViolation) as failure:
        replace(graph, edges=tuple(item for item in graph.edges if item != edge)).validate()
    assert failure.value.failure_code is Failure.MISSING_MANDATORY_EDGE


def test_orphan_edge_fails_before_traversal():
    graph = input_graph()
    with pytest.raises(LineageViolation) as failure:
        replace(graph, edges=graph.edges + (LineageEdge(Kind.DERIVED_FROM, "absent", graph.nodes[0].node_id),)).validate()
    assert failure.value.failure_code is Failure.ORPHAN_EDGE


def test_type_matrix_rejects_unrelated_roles():
    graph = input_graph()
    roles = dict(graph.roles)
    with pytest.raises(LineageViolation) as failure:
        replace(graph, edges=graph.edges + (LineageEdge(Kind.PUBLISHED_AS, roles["consumer"], roles["retrieval"]),)).validate()
    assert failure.value.failure_code is Failure.TYPE_CONSTRAINT


def test_cycle_with_existing_endpoints_is_rejected():
    graph = input_graph()
    b = GraphBuilder("inputs/v1", "run-a", "1")
    b.merge("source", graph)
    b.roles.update(dict(graph.roles))
    b.record("self", Node.LINEAGE, {"self": True}, "synapse.acceptance.lineage/v1")
    b.link("self", Kind.DERIVED_FROM, "self")
    with pytest.raises(LineageViolation) as failure:
        b.finish()
    assert failure.value.failure_code is Failure.CYCLE


def test_reachability_uses_explicit_edges():
    graph = input_graph()
    ancestors = graph.ancestors(dict(graph.roles)["replay_result"])
    assert {node.node_id for node in ancestors} == {node.node_id for node in graph.nodes}
    assert len(graph.ancestors(dict(graph.roles)["consumer"])) == 1


def test_schema_and_content_are_bound():
    graph = input_graph()
    for field, value in (("graph_identity", "0" * 64), ("schema_version", "future/v2")):
        with pytest.raises(LineageViolation):
            LineageGraph.from_dict({**graph.to_dict(), field: value})
