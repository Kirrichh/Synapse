"""Explicit feedback retains the predecessor's proof in the next attempt."""

import pytest

from acceptance.stage4.stage11._builders import run_world
from synapse.experiments.gold.runner.records import RecordKind
from synapse.experiments.gold.runner.vocabulary import FallbackPolicy
from synapse.experiments.gold.stage14 import execution
from synapse.experiments.gold.stage14.graph import LineageGraph, LineageViolation


def test_next_plan_reaches_its_actual_feedback_and_detects_lost_relation(tmp_path, monkeypatch):
    world = run_world(tmp_path, max_attempts=2, fallback_policy=FallbackPolicy.FORBIDDEN,
                      oracle_outcomes=[(False, False), (True, False)],
                      worker_outcomes=("PATCH", "PATCH"))
    result = world.execute()
    store = world.composition.record_store
    first_record = store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1")
    original = first_record.payload
    first = LineageGraph.from_dict(original)
    second = LineageGraph.from_dict(store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="2").payload)
    ancestors = {node.node_id for node in second.ancestors(dict(second.roles)["intent"])}
    for role in ("result", "input.snapshot", "plan", "oracle"):
        assert dict(first.roles)[role] in ancestors, role
    assert world.composition.controller.load_result() == result
    with monkeypatch.context() as patch:
        patch.setattr(execution, "_add_feedback", lambda *args, **kwargs: None)
        with pytest.raises(LineageViolation):
            world.composition.controller.load_result()
    assert world.composition.controller.load_result() == result
    assert store.get(kind=RecordKind.ATTEMPT_LINEAGE, key="1").payload == original
    assert world.worker_process.calls == 2
    assert world.oracle.calls == 2
