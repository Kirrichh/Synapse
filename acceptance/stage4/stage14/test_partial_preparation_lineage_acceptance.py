"""An interrupted Stage 10 write prefix is evidence, never an executed attempt."""
import pytest

from acceptance.stage4.stage11._builders import create_composition, run_world
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage10.record_store import Stage10RecordKind
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.experiments.gold.stage14.sources import lineage_retention_roots


@pytest.mark.parametrize("cut,retained,absent", (
    (Stage10RecordKind.PLAN_PROPOSAL, ("intent", "plan_proposal"), ("plan_decision", "plan", "worker_audit")),
    (Stage10RecordKind.WORKER_CONTEXT_AUDIT, ("plan", "worker_audit", "worker_consumption_gate"), ("worker_context",)),
))
def test_recovery_retains_only_physically_written_preparation(cut, retained, absent, tmp_path, monkeypatch):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)])
    store = world.stage10_composition.record_store
    put = store.put

    def interrupted(**kwargs):
        ref = put(**kwargs)
        if kwargs["kind"] is cut:
            raise SystemExit("lost inside Stage 10 preparation")
        return ref

    with monkeypatch.context() as patch:
        patch.setattr(store, "put", interrupted)
        with pytest.raises(SystemExit):
            world.execute()
    resumed = create_composition(world)
    result = resumed.execute()
    assert result.attempts == ()
    graph = LineageGraph.from_dict(resumed.record_store.get(kind=RecordKind.RUN_LINEAGE, key="final").payload)
    roles = dict(graph.roles)
    ancestry = {node.node_id for node in graph.ancestors(roles["run_result"])}
    for role in retained:
        assert roles["prepared.1." + role] in ancestry
    for role in (*absent, "worker_result", "verification", "outcome"):
        assert "prepared.1." + role not in roles
    sources = resumed.record_store.get(kind=RecordKind.LINEAGE_SOURCES, key="1").payload
    assert lineage_retention_roots(sources, graph, root_node_id=roles["run_result"]).object_refs
    assert world.worker_process.calls == world.oracle.calls == 0
    assert create_composition(world).controller.load_result() == result
