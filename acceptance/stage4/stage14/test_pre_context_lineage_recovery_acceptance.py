"""Prepared inputs remain reachable when no GoldAttemptContext was published."""
import pytest

from acceptance.stage4.stage11._builders import create_composition, run_world
from synapse.experiments.gold.runner import controller_recovery
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.state_machine import load_run_state
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.library import RetentionRootKind, RetentionRootSet, LIBRARY_RETENTION_ROOTS_V1
from synapse.experiments.gold.stage14.graph import LineageGraph
from synapse.experiments.gold.stage14.sources import lineage_retention_roots


def test_prepared_context_survives_crash_before_attempt_context(tmp_path, monkeypatch):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)])
    prepare = controller_recovery.prepare_attempt_delivery

    def interrupted(**kwargs):
        prepare(**kwargs)
        raise SystemExit("lost after durable preparation, before Gold context")

    with monkeypatch.context() as patch:
        patch.setattr(controller_recovery, "prepare_attempt_delivery", interrupted)
        with pytest.raises(SystemExit):
            world.execute()
    assert not load_run_state(world.composition.record_store).attempts
    resumed = create_composition(world)
    result = resumed.execute()
    assert result.attempts == ()
    assert load_run_state(resumed.record_store).preparation_failure.detail_code == "preparation_interrupted_outcome_unknown"
    graph = LineageGraph.from_dict(resumed.record_store.get(kind=RecordKind.RUN_LINEAGE, key="final").payload)
    roles = dict(graph.roles)
    ancestors = {node.node_id for node in graph.ancestors(roles["run_result"])}
    for role in ("input.snapshot", "input.retrieval", "input.replay_result", "intent", "plan_proposal",
                 "plan_decision", "plan", "worker_audit", "worker_context", "worker_consumption_gate"):
        assert roles["prepared.1." + role] in ancestors, role
    assert "prepared.1.worker_result" not in roles
    sources = resumed.record_store.get(kind=RecordKind.LINEAGE_SOURCES, key="1").payload
    roots = lineage_retention_roots(sources, graph, root_node_id=roles["run_result"])
    assert roots.object_refs
    categories = tuple(roots if kind is RetentionRootKind.LINEAGE else
        RetentionRootSet(LIBRARY_RETENTION_ROOTS_V1, kind, ()) for kind in RetentionRootKind)
    gc = world.attempt_inputs.case.world.library.plan_garbage_collection(categories)
    assert set(roots.object_refs) <= set(gc.retained_refs)
    assert not set(roots.object_refs) & set(gc.deletion_candidates)
    assert world.attempt_inputs.calls == [1]
    assert world.worker_process.calls == world.oracle.calls == 0
    assert create_composition(world).controller.load_result() == result
    assert resumed.record_store.get(kind=RecordKind.RUN_LINEAGE, key="final").payload == graph.to_dict()

    # A later lost context is a mismatch with the retained terminal graph.
    directory = world.stage10_composition.record_store.record_root / "worker-context-audit"
    path = next(directory.glob("*.stage10"))
    saved = path.read_bytes()
    path.unlink()
    try:
        with pytest.raises(ValueError):
            create_composition(world).controller.load_result()
    finally:
        path.write_bytes(saved)
    assert create_composition(world).controller.load_result() == result
