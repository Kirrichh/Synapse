"""Prospective task-coverage computation through the existing CVM; no admission."""

from synapse.experiments.gold import behavior as B
from synapse.experiments.gold.source_procedures import (
    build_source_coverage_behavior, source_coverage_inputs, validate_source_coverage,
)
from synapse.experiments.gold.replay_vm_adapter import observe_typed_pure_invocation
from tests.stage4_gold_replay_support import MACHINE_CONTEXT
from acceptance.stage4.stage10._builders import hash_ref
from synapse.experiments.gold.canonicalization import RefKind


def test_source_coverage_computes_applicability_for_the_actual_task():
    bindings = (hash_ref(RefKind.BINDING, "source-target"),)
    unit = build_source_coverage_behavior(
        {"knowledge_ref": hash_ref(RefKind.SOURCE_EVIDENCE, "source").to_dict()}, bindings)
    traces = []
    for targets in (bindings, ()):
        inputs = source_coverage_inputs(unit, targets)
        trace, value = observe_typed_pure_invocation(unit, inputs=inputs,
            gas_budget=1000, step_limit=1000, execution_context=MACHINE_CONTEXT)
        assert value == ([1, len(targets), 0] if targets else [0, 0, 0])
        validate_source_coverage(value, inputs=inputs)
        traces.append(trace)
    assert traces[0] != traces[1]
    assert unit.core.replay_contract.profile_id == B.TYPED_PURE_REPLAY_PROFILE_V2
