"""Input-dependent pure computation against its actual governed capture."""

import pytest

from synapse.experiments.gold import behavior as B
from synapse.experiments.gold import replay as R
from synapse.experiments.gold import replay_composition as RC
from synapse.experiments.gold.replay_vm_adapter import read_replayed_return_value
from tests.gold_typed_replay_support import numeric_procedure, explicit_subject
from tests.stage4_gold_replay_support import prepare_for


def test_one_admitted_program_replays_different_branches_and_results():
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2)
    original = unit.to_dict()
    traces = []
    for inputs, expected in [({"left": 11, "right": 4}, 7),
                             ({"left": -7, "right": 5}, 12),
                             ({"left": 0, "right": 0}, 0)]:
        prepared = prepare_for(unit, inputs=inputs)
        result = prepared.run()
        assert result.status is R.ReplayStatus.REPLAY_IDENTICAL
        observation, = result.observations
        assert read_replayed_return_value(observation,
            prepared.bundle.replay_store.open_snapshot(observation.terminal_snapshot_ref)) == expected
        assert result.steps_executed > 1 and not result.recorded_activity_refs
        traces.append(result.transition_hash_chain)
        assert unit.to_dict() == original
    assert len(set(traces)) == 3


def test_captured_branch_cannot_authorize_another_invocation():
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2)
    prepared = prepare_for(unit, inputs={"left": 11, "right": 4})
    manifest_ref = prepared.manifest_ref(prepared.bundle.replay_store)
    recorded = prepared.bundle.replay_store.recorded_result_refs()
    prepared.subjects = (explicit_subject(prepared, left=-7, right=5),)
    with pytest.raises(R.ReplayViolation) as error:
        RC.run_governed_replay(admission=prepared.admission, subjects=prepared.subjects,
            compiler=prepared.compiler, manifest_ref=manifest_ref,
            **prepared._governed(), **prepared._run_arguments())
    assert error.value.failure_code is R.ReplayFailureCode.SNAPSHOT_BINDING_MISMATCH
    assert prepared.bundle.replay_store.recorded_result_refs() == recorded


def test_dynamic_return_type_is_verified_before_publishing_a_capture():
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2,
                             output_type=B.ValueType.BOOLEAN)
    prepared = prepare_for(unit, inputs={"left": -7, "right": 5})
    with pytest.raises(R.ReplayViolation):
        prepared.run()
    captures = prepared.bundle.replay_store.recorded_capture_refs()
    assert captures
    capture = prepared.bundle.replay_store.require_capture(captures[-1])
    assert capture.contract_failure_reason is R.ReplayFailureReason.OUTPUT_CONTRACT_VIOLATION
    assert not capture.contract_matched
    assert not prepared.bundle.replay_store.recorded_manifest_refs()


def test_dynamic_continuation_preserves_the_exact_invocation_and_executed_prefix():
    unit = numeric_procedure(required=True, profile=B.TYPED_PURE_REPLAY_PROFILE_V2)
    inputs = {"left": -7, "right": 5}
    prepared = prepare_for(unit, inputs=inputs, gas_budget=2)
    first = prepared.run()
    assert first.failure_reason is R.ReplayFailureReason.GAS_EXHAUSTED
    prepared = prepare_for(unit, inputs=inputs, gas_budget=100)
    continued = prepared.resume(resumed_from=first)
    assert continued.status is R.ReplayStatus.REPLAY_IDENTICAL
    observation, = continued.observations
    assert read_replayed_return_value(observation,
        prepared.bundle.replay_store.open_snapshot(observation.terminal_snapshot_ref)) == 12
    prepared = prepare_for(unit, inputs={"left": 11, "right": 4}, gas_budget=100)
    with pytest.raises(R.ReplayViolation) as error:
        prepared.resume(resumed_from=continued)
    assert error.value.failure_code is R.ReplayFailureCode.RESUME_LINEAGE_MISMATCH
