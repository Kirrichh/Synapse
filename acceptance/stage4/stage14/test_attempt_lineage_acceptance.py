"""Real input owners, C1 completion, terminal lineage and physical restart."""
import pytest

from acceptance.stage4.stage13._case import negative_attempt
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.library import RetentionRootKind, RetentionRootSet, LIBRARY_RETENTION_ROOTS_V1
from synapse.experiments.gold.stage14.graph import LineageGraph, LineageViolation
from synapse.experiments.gold.stage14.sources import lineage_retention_roots


def test_completion_reconstructs_exact_inputs_and_retains_library(tmp_path):
    attempt = negative_attempt(tmp_path)
    world = attempt.world
    result = world.composition.controller.execute()
    store = world.composition.record_store
    graph = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1").payload)
    run = LineageGraph.from_dict(store.get(kind=RecordKind.RUN_LINEAGE, key="final").payload)
    refs = {n.node_id for n in run.ancestors(dict(run.roles)["run_result"])}
    assert {n.node_id for n in graph.nodes} <= refs
    sources = store.get(kind=RecordKind.LINEAGE_SOURCES, key="1").payload
    roots = lineage_retention_roots(sources, graph, root_node_id=dict(graph.roles)["result"])
    assert len(roots.object_refs) == 2
    categories = tuple(roots if kind is RetentionRootKind.LINEAGE else
        RetentionRootSet(LIBRARY_RETENTION_ROOTS_V1, kind, ()) for kind in RetentionRootKind)
    library = world.attempt_inputs.case.world.library
    gc = library.plan_garbage_collection(categories)
    assert set(roots.object_refs) <= set(gc.retained_refs)
    assert not set(roots.object_refs) & set(gc.deletion_candidates)
    assert world.composition.controller.execute().stored_dict() == result.stored_dict()
    assert store.get(kind=RecordKind.RUN_LINEAGE, key="final").payload == run.to_dict()
    path = next((store.record_root / RecordKind.ATTEMPT_LINEAGE).glob("*.json"))
    raw = path.read_bytes()
    path.unlink()
    try:
        with pytest.raises((LineageViolation, ValueError)):
            world.composition.controller.execute()
    finally:
        path.write_bytes(raw)
