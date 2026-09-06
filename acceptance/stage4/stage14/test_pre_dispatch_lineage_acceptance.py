"""Retain prepared plan evidence even when delivery has no completed worker."""

import pytest

from acceptance.stage4.stage11._builders import create_composition, run_world
from acceptance.stage4.stage11._crash_prefix import begin_attempt, publish_delivery_started
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage14.graph import LineageGraph


@pytest.mark.parametrize("interrupted", (False, True))
def test_prepared_plan_remains_in_terminal_lineage(interrupted, tmp_path):
    world = run_world(tmp_path, max_attempts=1, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(True, False)],
                      delivery_unavailable_attempts=set() if interrupted else {1})
    if interrupted:
        prefix = begin_attempt(world)
        publish_delivery_started(prefix)
        create_composition(world).execute()
    else:
        world.execute()
    graph = LineageGraph.from_dict(world.composition.record_store.get(
        kind=RecordKind.ATTEMPT_LINEAGE, key="1").payload)
    roles = dict(graph.roles)
    ancestors = {node.node_id for node in graph.ancestors(roles["result"])}
    for role in ("intent", "plan_proposal", "plan_decision", "plan"):
        assert role in roles, role
        assert roles[role] in ancestors, role
    if interrupted:
        assert roles["worker_context"] in ancestors
        assert roles["worker_audit"] in ancestors
    assert "worker_result" not in roles
    assert world.worker_process.calls == 0
    assert world.oracle.calls == 0
    before = graph.to_dict()
    assert create_composition(world).controller.load_result().attempts
    assert world.composition.record_store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1").payload == before
