"""Two canonical processes retain separate producer and consumer proof graphs."""
import json

from acceptance.stage4.stage13._observed_reuse import observed_reuse_case
from synapse.experiments.gold.stage14.graph import LineageGraph, LineageNodeClass


def test_canonical_reuse_links_producer_without_rewriting_its_outcome(tmp_path):
    producer, consumer, original, request = observed_reuse_case(tmp_path)
    producer_graph_path = next((producer.run_root / "run-records" / "attempt-lineage").glob("*.json"))
    producer_bytes = producer_graph_path.read_bytes()
    code, pending = consumer.start()
    assert code == 3, pending
    code, result = consumer.approve(pending)
    assert code == 0, result
    graph_path = next((consumer.run_root / "run-records" / "attempt-lineage").glob("*.json"))
    graph = LineageGraph.from_dict(json.loads(graph_path.read_text()))
    roles = dict(graph.roles)
    assert "mechanism_use" in roles and "promotion" in roles
    assert "c1" not in roles and "oracle" not in roles
    ancestors = graph.ancestors(roles["result"])
    for kind in (LineageNodeClass.ORACLE_RESULT, LineageNodeClass.KNOWLEDGE_SNAPSHOT,
                 LineageNodeClass.RETRIEVAL_DECISION, LineageNodeClass.PLAN):
        assert any(n.node_class is kind and n.run_id != graph.run_id for n in ancestors), kind
    assert producer_graph_path.read_bytes() == producer_bytes
    code, resumed = consumer.cli("project", "resume", "--run-dir", consumer.run_root)
    assert code == 0, resumed
    assert result["result"] == resumed["result"]
    assert consumer.worker.calls == 2
